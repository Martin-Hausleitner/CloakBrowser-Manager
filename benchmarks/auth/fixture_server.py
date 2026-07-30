"""Loopback-only local RP and Google-style OAuth popup fixture server.

Serves synthetic password, OTP, and OAuth popup flows for offline agent
benchmarks. Binds 127.0.0.1 only, enforces bounded bodies/timeouts, CSP and
referrer policy, and sanitizes logs so passwords/OTP/tokens never appear.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

try:
    # Package consumers: import benchmarks.auth.fixture_server
    from . import scenarios as sc
except ImportError:  # pragma: no cover - script / directory-on-sys.path mode
    # Focused pytest and `python fixture_server.py` keep auth/ on sys.path.
    import scenarios as sc

DEFAULT_HOST = "127.0.0.1"
DEFAULT_RP_PORT = 18791
DEFAULT_IDP_PORT = 18792
MAX_BODY_BYTES = 8_192
DEFAULT_REQUEST_TIMEOUT_S = 2.0

# Synthetic fixture-only values. Never real Google/account secrets.
FIXTURE_USERNAME = "fixture-user"
FIXTURE_PASSWORD = "fixture-pass"
FIXTURE_OTP = "123456"
FIXTURE_TOKEN_PLACEHOLDER = "fixture-token"

# OAuth state: unreserved URL-safe characters only (reject HTML/JS metacharacters).
_OAUTH_STATE_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")

CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)
REFERRER_POLICY = "no-referrer"

LogSink = Callable[[str], None]


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate_oauth_state(state: str) -> str | None:
    """Return state when safe for embedding; otherwise None."""
    if not state or not _OAUTH_STATE_RE.fullmatch(state):
        return None
    return state


def _is_safe_redirect_uri(redirect_uri: str, rp_origin: str) -> bool:
    """Allow only same-origin RP OAuth callback paths on loopback RP."""
    if not redirect_uri or not rp_origin:
        return False
    try:
        parsed = urlparse(redirect_uri)
        rp = urlparse(rp_origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.scheme != rp.scheme or parsed.netloc != rp.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    # Restrict to the local OAuth callback surface (no open redirect to other RP paths).
    if parsed.path != "/oauth/callback":
        return False
    return not parsed.fragment


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer for fixture RP/IdP with quiet expected disconnects.

    Suppresses only :class:`ConnectionResetError` (common when a client aborts
    mid-request, e.g. after a 413 oversized-body response). All other exceptions
    still flow through the default ``handle_error`` traceback path.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionResetError):
            return
        super().handle_error(request, client_address)


class _SharedState:
    def __init__(self, log_sink: LogSink | None = None) -> None:
        self._lock = threading.Lock()
        self.sessions: set[str] = set()
        self.otp_challenges: dict[str, dict[str, Any]] = {}
        self.oauth_states: dict[str, dict[str, Any]] = {}
        self.logs: list[str] = []
        # Keep sink on state (not handler class attrs) so callables are not
        # turned into bound methods via the function descriptor protocol.
        self.log_sink = log_sink

    def log(self, message: str) -> None:
        cleaned = sc.sanitize_log_message(message)
        # Hard scrub known fixture secrets even if sanitizer patterns miss.
        for secret in (FIXTURE_PASSWORD, FIXTURE_OTP):
            cleaned = cleaned.replace(secret, "[REDACTED]")
        with self._lock:
            self.logs.append(cleaned)
        sink = self.log_sink
        if sink is not None:
            sink(cleaned)

    def drain_logs(self) -> list[str]:
        with self._lock:
            out = list(self.logs)
            self.logs.clear()
            return out


class _FixtureHandler(BaseHTTPRequestHandler):
    server_version = "AuthFixture/1.0"
    protocol_version = "HTTP/1.1"

    # Populated on the class by AuthFixtureServer before serve_forever.
    role: str = "rp"
    state: _SharedState
    idp_origin: str = ""
    rp_origin: str = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            rendered = fmt % args
        except (TypeError, ValueError):
            rendered = fmt
        self.state.log(f"{self.role} {self.command} {self.path} {rendered}")

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
        csp: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Single CSP header only (multiple policies are ANDed and would tighten form-action).
        self.send_header("Content-Security-Policy", csp if csp is not None else CSP)
        self.send_header("Referrer-Policy", REFERRER_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                if key.lower() == "content-security-policy":
                    continue  # already set via csp=
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._send(status, raw)

    def _read_body(self) -> bytes | None:
        length_raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_raw)
        except ValueError:
            self._json(400, {"outcome": "error", "error": "bad_content_length"})
            return None
        if length < 0:
            self._json(400, {"outcome": "error", "error": "bad_content_length"})
            return None
        if length > MAX_BODY_BYTES:
            self.state.log(f"rejected oversized body length={length}")
            self._json(413, {"outcome": "error", "error": "body_too_large"})
            return None
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _form(self, body: bytes) -> dict[str, str]:
        parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if self.role == "rp":
            self._rp_get(path, parsed)
        else:
            self._idp_get(path, parsed)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        if body is None:
            return
        if self.role == "rp":
            self._rp_post(path, body)
        else:
            self._idp_post(path, body)

    # ── RP routes ──────────────────────────────────────────────────────────

    def _rp_get(self, path: str, parsed: Any) -> None:
        if path == "/health":
            self._json(200, {"status": "ok", "role": "rp"})
            return
        if path == "/scenarios":
            self._json(200, {"ids": list(sc.scenario_ids())})
            return
        if path == "/":
            html = (
                "<!doctype html><html><head><meta charset=utf-8>"
                f"<meta http-equiv=Content-Security-Policy content=\"{CSP}\">"
                f"<meta name=referrer content={REFERRER_POLICY}>"
                "<title>Local RP Login</title></head><body>"
                "<h1>Local RP Login</h1>"
                "<form method=post action=/login>"
                "<label>username <input name=username autocomplete=username></label>"
                "<label>password <input name=password type=password autocomplete=current-password></label>"
                "<button type=submit>Login</button></form>"
                f"<p>IdP: {self.idp_origin}</p>"
                "</body></html>"
            ).encode()
            self._send(200, html, content_type="text/html; charset=utf-8")
            return
        if path == "/oauth/callback":
            qs = parse_qs(parsed.query)
            outcome = (qs.get("outcome") or ["success"])[-1]
            self._json(200, {"outcome": outcome, "via": "callback"})
            return
        self._json(404, {"outcome": "error", "error": "not_found"})

    def _rp_post(self, path: str, body: bytes) -> None:
        if path == "/login":
            form = self._form(body)
            username = form.get("username", "")
            password = form.get("password", "")
            self.state.log(f"login attempt user={username} password=[REDACTED]")
            if username == FIXTURE_USERNAME and password == FIXTURE_PASSWORD:
                sid = secrets.token_hex(8)
                with self.state._lock:
                    self.state.sessions.add(sid)
                self._json(200, {"outcome": "success", "session_id": sid})
            else:
                self._json(401, {"outcome": "failure"})
            return

        if path == "/otp/start":
            challenge_id = secrets.token_hex(8)
            with self.state._lock:
                self.state.otp_challenges[challenge_id] = {
                    "code": FIXTURE_OTP,
                    "expired": False,
                    "created": time.time(),
                }
            self.state.log(f"otp start challenge_id={challenge_id}")
            self._json(200, {"outcome": "pending", "challenge_id": challenge_id})
            return

        if path == "/otp/expire":
            form = self._form(body)
            challenge_id = form.get("challenge_id", "")
            with self.state._lock:
                challenge = self.state.otp_challenges.get(challenge_id)
                if challenge is not None:
                    challenge["expired"] = True
            self.state.log(f"otp expire challenge_id={challenge_id}")
            self._json(200, {"outcome": "expired", "challenge_id": challenge_id})
            return

        if path == "/otp/verify":
            form = self._form(body)
            challenge_id = form.get("challenge_id", "")
            otp = form.get("otp", "")
            self.state.log(f"otp verify challenge_id={challenge_id} otp=[REDACTED]")
            with self.state._lock:
                challenge = self.state.otp_challenges.get(challenge_id)
            if challenge is None:
                self._json(401, {"outcome": "failure"})
                return
            if challenge.get("expired"):
                self._json(410, {"outcome": "expired"})
                return
            if otp == challenge.get("code"):
                with self.state._lock:
                    self.state.otp_challenges.pop(challenge_id, None)
                self._json(200, {"outcome": "success"})
            else:
                self._json(401, {"outcome": "failure"})
            return

        if path == "/oauth/postmessage":
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"outcome": "error", "error": "invalid_json"})
                return
            origin = (
                self.headers.get("Origin")
                or (payload.get("origin") if isinstance(payload, dict) else None)
                or ""
            )
            allowed = {self.idp_origin, self.rp_origin}
            if origin not in allowed:
                self.state.log(f"postmessage rejected origin={origin}")
                self._json(403, {"outcome": "wrong_origin"})
                return
            outcome = "success"
            if isinstance(payload, dict):
                outcome = str(payload.get("outcome") or "success")
            self._json(200, {"outcome": outcome})
            return

        if path.startswith("/scenario/"):
            scenario_id = path[len("/scenario/") :]
            try:
                scenario = sc.get_scenario(scenario_id)
            except KeyError:
                self._json(404, {"outcome": "error", "error": "unknown_scenario"})
                return
            if scenario.expected_outcome == "timeout":
                # Synthetic immediate timeout signal; no long sleep in unit tests.
                delay = min(scenario.timeout_ms, 50) / 1000.0
                if delay > 0:
                    time.sleep(delay)
                self._json(408, {"outcome": "timeout", "scenario_id": scenario_id})
                return
            self._json(
                200,
                {
                    "outcome": scenario.expected_outcome,
                    "scenario_id": scenario_id,
                    "family": scenario.family,
                },
            )
            return

        self._json(404, {"outcome": "error", "error": "not_found"})

    # ── IdP routes ─────────────────────────────────────────────────────────

    def _idp_get(self, path: str, parsed: Any) -> None:
        if path == "/health":
            self._json(200, {"status": "ok", "role": "idp"})
            return
        if path == "/o/oauth2/auth":
            qs = parse_qs(parsed.query)
            raw_state = (qs.get("state") or ["s"])[-1]
            state = _validate_oauth_state(raw_state)
            if state is None:
                self.state.log("oauth auth rejected invalid state")
                self._json(400, {"outcome": "error", "error": "invalid_state"})
                return
            redirect_uri = (qs.get("redirect_uri") or [self.rp_origin + "/oauth/callback"])[-1]
            if not _is_safe_redirect_uri(redirect_uri, self.rp_origin):
                self.state.log("oauth auth rejected unsafe redirect_uri")
                self._json(400, {"outcome": "error", "error": "unsafe_redirect_uri"})
                return
            with self.state._lock:
                self.state.oauth_states[state] = {
                    "status": "open",
                    "redirect_uri": redirect_uri,
                }
            # Escape before embedding into HTML attributes (defense in depth).
            safe_state = html_escape(state, quote=True)
            safe_redirect = html_escape(redirect_uri, quote=True)
            # form-action must allow the validated RP callback origin so the
            # post-approve 302 can complete the OAuth redirect (ports differ).
            idp_csp = (
                "default-src 'none'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self'; "
                "connect-src 'self'; "
                f"form-action 'self' {self.rp_origin}; "
                "frame-ancestors 'none'; "
                "base-uri 'none'"
            )
            html = (
                "<!doctype html><html><head><meta charset=utf-8>"
                f'<meta http-equiv=Content-Security-Policy content="{idp_csp}">'
                f"<meta name=referrer content={REFERRER_POLICY}>"
                "<title>Fixture Google-style Consent</title></head><body>"
                "<h1>Fixture OAuth Consent</h1>"
                "<p>Synthetic local IdP — not Google.</p>"
                f"<form method=post action=/o/oauth2/approve>"
                f'<input type=hidden name=state value="{safe_state}">'
                f'<input type=hidden name=redirect_uri value="{safe_redirect}">'
                "<button type=submit>Approve</button></form>"
                f"<form method=post action=/o/oauth2/cancel>"
                f'<input type=hidden name=state value="{safe_state}">'
                "<button type=submit>Cancel</button></form>"
                "</body></html>"
            ).encode()
            self._send(
                200,
                html,
                content_type="text/html; charset=utf-8",
                csp=idp_csp,
            )
            return
        self._json(404, {"outcome": "error", "error": "not_found"})

    def _idp_post(self, path: str, body: bytes) -> None:
        form = self._form(body)
        raw_state = form.get("state", "")
        state = _validate_oauth_state(raw_state) or ""
        if path == "/o/oauth2/approve":
            if not state:
                self._json(400, {"outcome": "error", "error": "invalid_state"})
                return
            redirect_uri = form.get("redirect_uri") or (self.rp_origin + "/oauth/callback")
            if not _is_safe_redirect_uri(redirect_uri, self.rp_origin):
                self.state.log("oauth approve rejected unsafe redirect_uri")
                self._json(400, {"outcome": "error", "error": "unsafe_redirect_uri"})
                return
            with self.state._lock:
                entry = self.state.oauth_states.setdefault(state, {})
                entry["status"] = "success"
                entry["redirect_uri"] = redirect_uri
            self.state.log(f"oauth approve state={state}")
            # Complete at the local RP callback so clients verify outcome there,
            # not merely that the Approve button was clicked.
            target = f"{redirect_uri}?{urlencode({'outcome': 'success', 'state': state})}"
            body_bytes = (
                b'{"outcome":"success","via":"redirect","state":"'
                + state.encode("ascii", errors="replace")
                + b'"}'
            )
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", REFERRER_POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body_bytes)
            return
        if path == "/o/oauth2/cancel":
            with self.state._lock:
                entry = self.state.oauth_states.setdefault(state or raw_state, {})
                entry["status"] = "cancel"
            self.state.log(f"oauth cancel state={state or raw_state}")
            self._json(200, {"outcome": "cancel", "state": state or raw_state})
            return
        if path == "/o/oauth2/close":
            with self.state._lock:
                entry = self.state.oauth_states.setdefault(state or raw_state, {})
                entry["status"] = "close"
            self.state.log(f"oauth close state={state or raw_state}")
            self._json(200, {"outcome": "close", "state": state or raw_state})
            return
        if path == "/o/oauth2/reopen":
            with self.state._lock:
                entry = self.state.oauth_states.setdefault(state or raw_state, {})
                entry["status"] = "reopen"
            self.state.log(f"oauth reopen state={state or raw_state}")
            self._json(200, {"outcome": "reopen", "state": state or raw_state})
            return
        self._json(404, {"outcome": "error", "error": "not_found"})


class AuthFixtureServer:
    """Dual loopback HTTP servers: RP (app) + IdP (Google-style popup)."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        rp_port: int = DEFAULT_RP_PORT,
        idp_port: int = DEFAULT_IDP_PORT,
        log_sink: LogSink | None = None,
    ) -> None:
        if not _is_loopback_host(host):
            raise ValueError("fixture server must bind loopback host only")
        self.host = host
        self._requested_rp_port = rp_port
        self._requested_idp_port = idp_port
        self.state = _SharedState(log_sink=log_sink)
        self._rp: ThreadingHTTPServer | None = None
        self._idp: ThreadingHTTPServer | None = None
        self._rp_thread: threading.Thread | None = None
        self._idp_thread: threading.Thread | None = None

    @property
    def rp_port(self) -> int:
        if self._rp is None:
            raise RuntimeError("server not started")
        return int(self._rp.server_address[1])

    @property
    def idp_port(self) -> int:
        if self._idp is None:
            raise RuntimeError("server not started")
        return int(self._idp.server_address[1])

    @property
    def rp_base(self) -> str:
        return f"http://{self.host}:{self.rp_port}"

    @property
    def idp_base(self) -> str:
        return f"http://{self.host}:{self.idp_port}"

    def start(self) -> None:
        if self._rp is not None:
            return

        state = self.state

        class RPHandler(_FixtureHandler):
            pass

        class IdPHandler(_FixtureHandler):
            pass

        RPHandler.role = "rp"
        RPHandler.state = state
        IdPHandler.role = "idp"
        IdPHandler.state = state

        self._rp = LoopbackThreadingHTTPServer((self.host, self._requested_rp_port), RPHandler)
        self._idp = LoopbackThreadingHTTPServer((self.host, self._requested_idp_port), IdPHandler)
        self._rp.timeout = DEFAULT_REQUEST_TIMEOUT_S
        self._idp.timeout = DEFAULT_REQUEST_TIMEOUT_S
        # Allow clean shutdown on CPython.
        self._rp.daemon_threads = True
        self._idp.daemon_threads = True

        RPHandler.rp_origin = self.rp_base
        RPHandler.idp_origin = self.idp_base
        IdPHandler.rp_origin = self.rp_base
        IdPHandler.idp_origin = self.idp_base

        self._rp_thread = threading.Thread(target=self._rp.serve_forever, name="auth-rp", daemon=True)
        self._idp_thread = threading.Thread(
            target=self._idp.serve_forever, name="auth-idp", daemon=True
        )
        self._rp_thread.start()
        self._idp_thread.start()
        self.state.log(f"started rp={self.rp_base} idp={self.idp_base}")

    def stop(self) -> None:
        for server in (self._rp, self._idp):
            if server is not None:
                server.shutdown()
                server.server_close()
        for thread in (self._rp_thread, self._idp_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        self._rp = None
        self._idp = None
        self._rp_thread = None
        self._idp_thread = None
        self.state.log("stopped")

    def drain_logs(self) -> list[str]:
        return self.state.drain_logs()


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_IDP_PORT",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "DEFAULT_RP_PORT",
    "FIXTURE_OTP",
    "FIXTURE_PASSWORD",
    "FIXTURE_TOKEN_PLACEHOLDER",
    "FIXTURE_USERNAME",
    "MAX_BODY_BYTES",
    "AuthFixtureServer",
    "LoopbackThreadingHTTPServer",
]
