"""CloakBrowser Manager — FastAPI application.

Serves the React dashboard (static files) and provides a REST API
for browser profile management with live VNC viewing.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
import struct
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import starlette.requests
from starlette.types import ASGIApp, Receive, Scope, Send

if __package__:
    from . import access_control as access
    from . import database as db
    from . import extensions
    from . import live_diagnostics
    from .browser_manager import BrowserManager
    from .profile_health import ProfileHealthProbe
    from .models import (
        ClipboardRequest,
        AccessAgentCreate,
        AccessAgentCreatedResponse,
        AccessAgentResponse,
        AccessAgentUpdate,
        AccessGroupCreate,
        AccessGroupResponse,
        AccessGroupUpdate,
        AccessIdentityResponse,
        AccessUserCreate,
        AccessUserResponse,
        AccessUserUpdate,
        ExtensionInventoryResponse,
        ExtensionItem,
        LaunchResponse,
        LoginRequest,
        ProfileCreate,
        ProfileHealthResponse,
        ProfileResponse,
        ProfileStatusResponse,
        ProfileBulkOrganize,
        ProfileUpdate,
        ProxyAutoProfileCreate,
        ProxyInventoryIngest,
        ProxyInventoryIngestResponse,
        ProxyInventoryItem,
        StatusResponse,
        TagResponse,
        TaskCommandRequest,
        TaskEventResponse,
        TaskMessageCreate,
        TaskMessageResponse,
        TaskSessionCreate,
        TaskSessionResponse,
    )
    from . import proxy_inventory
else:  # Support `uvicorn main:app` from the backend directory.
    import access_control as access
    import database as db
    import extensions
    import live_diagnostics
    from browser_manager import BrowserManager
    from profile_health import ProfileHealthProbe
    from models import (
        ClipboardRequest,
        AccessAgentCreate,
        AccessAgentCreatedResponse,
        AccessAgentResponse,
        AccessAgentUpdate,
        AccessGroupCreate,
        AccessGroupResponse,
        AccessGroupUpdate,
        AccessIdentityResponse,
        AccessUserCreate,
        AccessUserResponse,
        AccessUserUpdate,
        ExtensionInventoryResponse,
        ExtensionItem,
        LaunchResponse,
        LoginRequest,
        ProfileCreate,
        ProfileHealthResponse,
        ProfileResponse,
        ProfileStatusResponse,
        ProfileBulkOrganize,
        ProfileUpdate,
        ProxyAutoProfileCreate,
        ProxyInventoryIngest,
        ProxyInventoryIngestResponse,
        ProxyInventoryItem,
        StatusResponse,
        TagResponse,
        TaskCommandRequest,
        TaskEventResponse,
        TaskMessageCreate,
        TaskMessageResponse,
        TaskSessionCreate,
        TaskSessionResponse,
    )
    import proxy_inventory

logger = logging.getLogger("cloakbrowser.manager")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Optional authentication via AUTH_TOKEN env var. If not set, all routes are
# open for local development. ``ACCESS_CONTROL_ENABLED=1`` adds named users and
# scoped Paperclip-agent credentials, but intentionally requires AUTH_TOKEN as
# a bootstrap signing secret to avoid an accidental locked-open deployment.
AUTH_TOKEN: str | None = os.environ.get("AUTH_TOKEN") or None
ACCESS_CONTROL_ENABLED = bool(AUTH_TOKEN) and access.access_control_enabled(
    os.environ.get("ACCESS_CONTROL_ENABLED")
)
if os.environ.get("ACCESS_CONTROL_ENABLED") and not AUTH_TOKEN:
    logger.warning("ACCESS_CONTROL_ENABLED ignored because AUTH_TOKEN is not configured")

# Paths that bypass authentication even when AUTH_TOKEN is set.  ``/health``
# deliberately contains no profile or runtime metadata so Docker can probe the
# service without turning ``/api/status`` into an information leak.
_AUTH_EXEMPT = frozenset({"/api/auth/status", "/api/auth/login", "/health"})
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_BACKOFF_SECONDS = 60.0
_LOGIN_FAILURE_TTL_SECONDS = 10 * 60.0
_LOGIN_FAILURE_MAX_KEYS = 1024
_login_failures: dict[tuple[str, str], tuple[int, float, float]] = {}
_TASK_METADATA_MAX_BYTES = 8_192
_TASK_METADATA_MAX_DEPTH = 4
_TASK_METADATA_MAX_LIST_ITEMS = 20
_TASK_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)


@dataclass(eq=False)
class _WebSocketAccessLease:
    """Process-local handle used to revoke an already-authorized WebSocket."""

    identity_kind: str
    identity_id: str
    profile_id: str
    revoked: asyncio.Event


_active_websocket_access_leases: set[_WebSocketAccessLease] = set()


def _register_websocket_access(
    identity: access.AccessIdentity, profile_id: str
) -> _WebSocketAccessLease | None:
    """Track mutable principals without adding authorization work per frame."""
    if (
        not ACCESS_CONTROL_ENABLED
        or identity.kind not in {"user", "agent"}
        or not identity.id
    ):
        return None
    lease = _WebSocketAccessLease(
        identity_kind=identity.kind,
        identity_id=identity.id,
        profile_id=profile_id,
        revoked=asyncio.Event(),
    )
    _active_websocket_access_leases.add(lease)
    return lease


def _unregister_websocket_access(lease: _WebSocketAccessLease | None) -> None:
    if lease is not None:
        _active_websocket_access_leases.discard(lease)


def _revoke_websocket_access(
    *,
    identity_kind: str | None = None,
    identity_id: str | None = None,
    profile_id: str | None = None,
) -> None:
    """Signal matching proxy tasks after a principal or sandbox policy change."""
    if identity_kind is None and identity_id is None and profile_id is None:
        return
    for lease in tuple(_active_websocket_access_leases):
        if identity_kind is not None and lease.identity_kind != identity_kind:
            continue
        if identity_id is not None and lease.identity_id != identity_id:
            continue
        if profile_id is not None and lease.profile_id != profile_id:
            continue
        lease.revoked.set()

_BENCHMARK_REPORT_ENV = "BENCHMARK_REPORT_PATH"
_DEFAULT_BENCHMARK_REPORT_PATH = Path("/data/benchmark-report.json")
_BENCHMARK_REPORT_MAX_BYTES = 1_048_576
_BENCHMARK_PUBLIC_TIMING_KEYS = frozenset(
    {
        "connect_ms",
        "tls_ms",
        "first_byte_ms",
        "handshake_ms",
        "total_ms",
        "process_start_ms",
        "first_stdout_ms",
        "first_stderr_ms",
        "ready_ms",
        "exit_ms",
        "frame_ready_ms",
        "input_response_ms",
    }
)
_BENCHMARK_PUBLIC_STATUS = frozenset({"measured", "not_installed", "architecture_only"})
_BENCHMARK_PUBLIC_AVAILABILITY = frozenset({"available", "unavailable", "error", "not_measured"})


def _check_auth(scope: Scope) -> bool:
    """Check if the request has a valid auth token (header or cookie)."""
    # Check Authorization: Bearer <token> header
    for key, val in scope.get("headers", []):
        if key == b"authorization":
            auth_value = val.decode()
            if auth_value.startswith("Bearer "):
                token = auth_value[7:]
                if token and hmac.compare_digest(token, AUTH_TOKEN):
                    return True
            break

    # Check auth_token cookie
    for key, val in scope.get("headers", []):
        if key == b"cookie":
            cookies = SimpleCookie()
            cookies.load(val.decode())
            if "auth_token" in cookies:
                cookie_val = cookies["auth_token"].value
                if cookie_val and hmac.compare_digest(cookie_val, AUTH_TOKEN):
                    return True
            break

    return False


def _access_identity(scope: Scope) -> access.AccessIdentity | None:
    return access.identity_from_scope(scope, AUTH_TOKEN, ACCESS_CONTROL_ENABLED)


def _client_host(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _login_throttle_key(username: str, request: Request) -> tuple[str, str]:
    return (username.casefold(), _client_host(request))


def _cleanup_login_failures(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        key for key, (_count, blocked_until, last_seen) in _login_failures.items()
        if (blocked_until > 0 and blocked_until <= now)
        or last_seen + _LOGIN_FAILURE_TTL_SECONDS <= now
    ]
    for key in expired:
        _login_failures.pop(key, None)

    overflow = len(_login_failures) - _LOGIN_FAILURE_MAX_KEYS
    if overflow <= 0:
        return

    oldest = sorted(
        _login_failures.items(),
        key=lambda item: (item[1][2], item[0][0], item[0][1]),
    )
    for key, _value in oldest[:overflow]:
        _login_failures.pop(key, None)


def _login_backoff_remaining(key: tuple[str, str], now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    _cleanup_login_failures(now)
    count, blocked_until, _last_seen = _login_failures.get(key, (0, 0.0, 0.0))
    if count < _LOGIN_FAILURE_LIMIT:
        return 0.0
    remaining = blocked_until - now
    if remaining <= 0:
        _login_failures.pop(key, None)
        return 0.0
    return remaining


def _record_login_failure(key: tuple[str, str]) -> None:
    now = time.monotonic()
    _cleanup_login_failures(now)
    count, blocked_until, _last_seen = _login_failures.get(key, (0, 0.0, 0.0))
    if blocked_until <= now:
        blocked_until = 0.0
    count += 1
    if count >= _LOGIN_FAILURE_LIMIT:
        blocked_until = now + _LOGIN_BACKOFF_SECONDS
    _login_failures[key] = (count, blocked_until, now)
    _cleanup_login_failures(now)


def _reset_login_failures(key: tuple[str, str]) -> None:
    _login_failures.pop(key, None)


def _benchmark_report_path() -> Path:
    configured = os.environ.get(_BENCHMARK_REPORT_ENV)
    if configured:
        return Path(configured).expanduser()
    return _DEFAULT_BENCHMARK_REPORT_PATH


def _malformed_benchmark_report() -> HTTPException:
    return HTTPException(status_code=422, detail="Benchmark report is malformed")


def _validate_latest_benchmark_report(report: object) -> dict[str, Any]:
    """Validate the persisted benchmark-runner report contract.

    GET /api/benchmarks/latest projects a deliberately redacted browser DTO
    after these checks:
    - top-level value is an object;
    - canonical runner fields ``schema_version``, ``started_at``,
      ``finished_at``, and ``results`` have the expected JSON shapes when
      present;
    - each canonical ``results`` item has a candidate object, string status,
      string availability, a measurements list, and optional summary object;
    - legacy dashboard fields ``run``, ``candidates``, and
      ``expected_technologies`` are also accepted while the frontend migrates;
    - optional URL/timestamp/note fields are strings or null.

    Producers may include extra internal fields, but the public endpoint keeps
    only a safe allowlist of labels and numeric summaries. The API never
    accepts a path from the caller and never runs benchmarks.
    """
    if not isinstance(report, dict):
        raise _malformed_benchmark_report()

    for key in ("report_url", "generated_at", "notes"):
        value = report.get(key)
        if value is not None and not isinstance(value, str):
            raise _malformed_benchmark_report()

    schema_version = report.get("schema_version")
    if schema_version is not None and not isinstance(schema_version, (str, int)):
        raise _malformed_benchmark_report()

    for key in ("started_at", "finished_at"):
        value = report.get(key)
        if value is not None and not isinstance(value, str):
            raise _malformed_benchmark_report()

    results = report.get("results")
    if results is not None:
        if not isinstance(results, list):
            raise _malformed_benchmark_report()
        for result in results:
            if not isinstance(result, dict):
                raise _malformed_benchmark_report()
            if not isinstance(result.get("candidate"), dict):
                raise _malformed_benchmark_report()
            if not isinstance(result.get("status"), str):
                raise _malformed_benchmark_report()
            if not isinstance(result.get("availability"), str):
                raise _malformed_benchmark_report()
            if not isinstance(result.get("measurements"), list):
                raise _malformed_benchmark_report()
            summary = result.get("summary")
            if summary is not None and not isinstance(summary, dict):
                raise _malformed_benchmark_report()

    run = report.get("run")
    if run is not None and not isinstance(run, dict):
        raise _malformed_benchmark_report()

    candidates = report.get("candidates")
    if candidates is not None:
        if not isinstance(candidates, list):
            raise _malformed_benchmark_report()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise _malformed_benchmark_report()
            metrics = candidate.get("metrics")
            if metrics is not None and not isinstance(metrics, dict):
                raise _malformed_benchmark_report()

    expected = report.get("expected_technologies")
    if expected is not None:
        if not isinstance(expected, list):
            raise _malformed_benchmark_report()
        for technology in expected:
            if isinstance(technology, str):
                continue
            if not isinstance(technology, dict) or not isinstance(technology.get("name"), str):
                raise _malformed_benchmark_report()

    return report


def _load_latest_benchmark_report() -> dict[str, Any]:
    report_path = _benchmark_report_path()
    try:
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail="Benchmark report not found")
        if report_path.stat().st_size > _BENCHMARK_REPORT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Benchmark report is too large")
        data = report_path.read_bytes()
    except HTTPException:
        raise
    except OSError as exc:
        logger.warning("Benchmark report unavailable: %s", exc)
        raise HTTPException(status_code=404, detail="Benchmark report not found") from exc

    if len(data) > _BENCHMARK_REPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Benchmark report is too large")

    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _malformed_benchmark_report() from exc

    return _validate_latest_benchmark_report(decoded)


def _benchmark_number(value: object) -> float | int | None:
    """Return a finite numeric value without accepting bools as timings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def _benchmark_text(value: object, *, fallback: str = "Not reported") -> str:
    if not isinstance(value, str):
        return fallback
    compact = value.strip()
    return compact[:128] if compact else fallback


def _public_benchmark_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Keep labels needed by the dashboard, never configuration internals."""
    public = {
        "name": _benchmark_text(candidate.get("name"), fallback=_benchmark_text(candidate.get("id"))),
        "type": _benchmark_text(candidate.get("type"), fallback="unknown"),
    }
    candidate_id = candidate.get("id")
    if isinstance(candidate_id, str) and candidate_id.strip():
        public["id"] = candidate_id.strip()[:128]

    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        allowed_metadata: dict[str, str | int | float | bool | None] = {}
        for key in ("technology", "version", "comparison_role"):
            value = metadata.get(key)
            if value is None or isinstance(value, (str, int, float, bool)):
                if key in metadata:
                    allowed_metadata[key] = value
        if allowed_metadata:
            public["metadata"] = allowed_metadata
    return public


def _public_benchmark_summary(summary: object) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}

    public: dict[str, Any] = {}
    runs = summary.get("runs")
    if isinstance(runs, int) and not isinstance(runs, bool) and runs >= 0:
        public["runs"] = runs

    success_rate = _benchmark_number(summary.get("success_rate_pct"))
    if success_rate is not None:
        public["success_rate_pct"] = max(0, min(100, success_rate))

    timings = summary.get("timings_ms")
    if isinstance(timings, dict):
        public_timings: dict[str, dict[str, float | int]] = {}
        for name in _BENCHMARK_PUBLIC_TIMING_KEYS:
            rollup = timings.get(name)
            if not isinstance(rollup, dict):
                continue
            safe_rollup = {
                key: numeric
                for key in ("min", "median", "p95", "max")
                if (numeric := _benchmark_number(rollup.get(key))) is not None
            }
            if safe_rollup:
                public_timings[name] = safe_rollup
        if public_timings:
            public["timings_ms"] = public_timings
    return public


def _benchmark_reason(status: str, availability: str) -> str | None:
    if status == "not_installed":
        return "The required local dependency was not installed for this run."
    if status == "architecture_only":
        return "Architecture-only candidate; no comparable live browser measurement was run."
    if availability == "error":
        return "The candidate could not be measured successfully."
    if availability == "unavailable":
        return "The candidate was measured but was unavailable during this run."
    return None


def _public_latest_benchmark_report(report: dict[str, Any]) -> dict[str, Any]:
    """Create the bounded browser DTO from a persisted runner artifact.

    Reports are generated by local tooling and may still contain deployment
    paths, command lines, loopback URLs, profile IDs, raw process output, or
    credentials in an old artifact. The API intentionally projects only the
    labels and numeric summary needed by the dashboard.
    """
    public: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at"),
    }

    results = report.get("results")
    if isinstance(results, list):
        public_results: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            raw_status = result.get("status")
            status = raw_status if raw_status in _BENCHMARK_PUBLIC_STATUS else "not_installed"
            raw_availability = result.get("availability")
            availability = (
                raw_availability if raw_availability in _BENCHMARK_PUBLIC_AVAILABILITY else "not_measured"
            )
            row: dict[str, Any] = {
                "candidate": _public_benchmark_candidate(result.get("candidate", {})),
                "status": status,
                "availability": availability,
                "summary": _public_benchmark_summary(result.get("summary")),
            }
            reason = _benchmark_reason(status, availability)
            if reason:
                row["reason"] = reason
            public_results.append(row)
        public["results"] = public_results
        return public

    # Older reports can still be rendered safely while deployments migrate to
    # the runner schema. This is intentionally a one-way projection too.
    candidates = report.get("candidates")
    if isinstance(candidates, list):
        public_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            state = candidate.get("state")
            measured = state in {"measured", "complete", "completed", "pass", "passed"}
            raw_metrics = candidate.get("metrics")
            metrics: dict[str, float | int] = {}
            if measured and isinstance(raw_metrics, dict):
                for key in (
                    "p50_latency_ms",
                    "p95_latency_ms",
                    "median_latency_ms",
                    "avg_latency_ms",
                    "availability_pct",
                    "success_rate_pct",
                    "samples",
                ):
                    numeric = _benchmark_number(raw_metrics.get(key))
                    if numeric is not None:
                        metrics[key] = numeric
            public_candidate = {
                "name": _benchmark_text(candidate.get("name"), fallback=_benchmark_text(candidate.get("technology"))),
                "technology": _benchmark_text(candidate.get("technology"), fallback="unknown"),
                "state": "measured" if measured else "not_measured",
                "measured": measured,
            }
            if metrics:
                public_candidate["metrics"] = metrics
            if not measured:
                public_candidate["not_measured_reason"] = "No comparable live measurement was reported."
            public_candidates.append(public_candidate)
        public["candidates"] = public_candidates
    return public


def _is_https(request: Request) -> bool:
    """Check if the original client connection was HTTPS (via reverse proxy header)."""
    proto = request.headers.get("x-forwarded-proto", "")
    return "https" in proto


async def _check_websocket_origin(websocket: WebSocket) -> bool:
    """Reject cross-origin WebSocket connections (CSWSH protection).

    Browsers always send an Origin header on WebSocket upgrades.
    Non-browser clients (Playwright, curl) typically don't — those are allowed.
    If Origin is present, its host must match the request Host header.
    """
    origin = None
    host = None
    for key, val in websocket.scope.get("headers", []):
        if key == b"origin":
            origin = val.decode("latin-1")
        elif key == b"host":
            host = val.decode("latin-1")

    # No Origin header → non-browser client (Playwright, Puppeteer) → allow
    if not origin:
        return True

    # Parse origin to extract host:port
    try:
        parsed = urlparse(origin)
        origin_host = parsed.hostname or ""
        origin_port = parsed.port
    except ValueError:
        logger.warning("WebSocket origin malformed: %s", origin)
        await websocket.close(code=4403, reason="Origin not allowed")
        return False
    # Build origin netloc (host:port or just host if default port)
    if origin_port and origin_port not in (80, 443):
        origin_netloc = f"{origin_host}:{origin_port}"
    else:
        origin_netloc = origin_host

    if not host:
        return True  # no Host header to compare against

    # Strip default port from Host too (some proxies send "example.com:443")
    host_normalized = host
    if host.endswith(":80") or host.endswith(":443"):
        host_normalized = host.rsplit(":", 1)[0]

    if origin_netloc == host_normalized:
        return True

    logger.warning("WebSocket origin mismatch: origin=%s host=%s", origin, host)
    await websocket.close(code=4403, reason="Origin not allowed")
    return False


class AuthMiddleware:
    """Raw ASGI middleware for optional token auth.

    Uses raw ASGI instead of BaseHTTPMiddleware because the latter
    breaks WebSocket routes (wraps request body, preventing WS upgrade).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Pass through non-HTTP/WS scope (e.g. lifespan).
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # Scoped policy mode recognizes bootstrap, signed human sessions, and
        # individual agent bearer keys. The resolved identity is placed on the
        # raw ASGI scope so REST and WebSocket handlers use one decision.
        if ACCESS_CONTROL_ENABLED:
            identity = access.resolve_identity(scope, AUTH_TOKEN)
            if identity:
                scope.setdefault("state", {})["access_identity"] = identity

            # Auth bootstrap/static routes intentionally remain reachable.
            if path in _AUTH_EXEMPT or not path.startswith("/api/"):
                await self.app(scope, receive, send)
                return

            if identity:
                await self.app(scope, receive, send)
                return

            await _reject_unauthenticated(scope, receive, send)
            return

        # Legacy mode: optional single owner token, unchanged for existing
        # deployments that have not opted into access control.
        if not AUTH_TOKEN or path in _AUTH_EXEMPT or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if _check_auth(scope):
            await self.app(scope, receive, send)
            return

        await _reject_unauthenticated(scope, receive, send)


async def _reject_unauthenticated(scope: Scope, receive: Receive, send: Send) -> None:
    """Reject both HTTP and WebSocket callers without bypassing ASGI rules."""
    if scope["type"] == "websocket":
        # ASGI requires receiving websocket.connect before sending close.
        await receive()
        await send({"type": "websocket.close", "code": 4401, "reason": "Unauthorized"})
    else:
        response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)


# Singleton browser manager
browser_mgr = BrowserManager()
_profile_health_allowed_hosts = {
    host.strip()
    for host in os.environ.get("PROXYCHECKER_ALLOWED_HOSTS", "").split(",")
    if host.strip()
}
try:
    profile_health_probe = ProfileHealthProbe(
        proxychecker_url=os.environ.get("PROXYCHECKER_URL", ""),
        allowed_proxychecker_hosts=_profile_health_allowed_hosts,
    )
except ValueError:
    logger.error("Ignoring invalid PROXYCHECKER_URL; profile health enrichment is disabled")
    profile_health_probe = ProfileHealthProbe()
_profile_health_tasks: dict[str, asyncio.Task[None]] = {}

# Frontend build directory (React production build)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# RFB server message translator — KasmVNC BinaryClipboard → standard RFB
# ---------------------------------------------------------------------------


def _parse_kasmvnc_clipboard(data: bytes) -> str | None:
    """Extract text/plain from KasmVNC BinaryClipboard (type 180).

    Format: type(1) + action(1) + flags(4) + entries...
    Each entry: mime_len(u8) + mime(N) + data_len(u32 BE) + data(M)
    """
    if len(data) < 7:
        return None
    offset = 6  # skip type(1) + action(1) + flags(4)
    while offset < len(data):
        if offset + 1 > len(data):
            break
        mime_len = data[offset]
        offset += 1
        if offset + mime_len > len(data):
            break
        mime_type = data[offset:offset + mime_len]
        offset += mime_len
        if offset + 4 > len(data):
            break
        data_len = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if mime_type == b"text/plain":
            end = min(offset + data_len, len(data))
            return data[offset:end].decode("utf-8", errors="replace")
        offset += data_len
    return None


def _build_server_cut_text(text: str) -> bytes:
    """Build standard RFB ServerCutText (type 3) message.

    RFB spec mandates Latin-1 encoding for ServerCutText.
    Characters outside Latin-1 (CJK, emoji, etc.) are replaced with '?'.
    """
    text_bytes = text.encode("latin-1", errors="replace")
    return struct.pack(">BxxxI", 3, len(text_bytes)) + text_bytes


def _filter_vnc_server_message(
    data: bytes, *, can_interact: bool
) -> bytes | None:
    """Translate a frame-aligned Kasm clipboard message for operators."""
    if not data:
        return data

    if data[0] == 180:
        if not can_interact:
            return None
        text = _parse_kasmvnc_clipboard(data)
        return _build_server_cut_text(text) if text is not None else None

    return data


_RFB_SERVER_MAX_BUFFER = 64 * 1024 * 1024


class _RfbServerProtocolError(ValueError):
    """Raised when a viewer stream cannot be parsed without leaking data."""


class _RfbServerStreamFilter:
    """Stateful server→client RFB filter for view-only connections.

    RFB is a byte stream, so WebSocket messages may split or coalesce RFB
    messages. The filter buffers until it can identify complete top-level
    messages and framebuffer rectangles. Clipboard messages are removed at
    RFB boundaries; unknown message types or encodings fail closed instead of
    forwarding bytes that might contain clipboard content.
    """

    def __init__(self, *, can_interact: bool, handshake_complete: bool = False):
        self.can_interact = can_interact
        self._phase = "normal" if handshake_complete else "protocol_version"
        self._protocol_minor = 8
        self._pixel_size = 4
        self._buffer = bytearray()
        self.last_dropped_clipboard = False
        self.last_saw_framebuffer = False

    @staticmethod
    def _bounded_length(length: int, label: str) -> int:
        if length < 0 or length > _RFB_SERVER_MAX_BUFFER:
            raise _RfbServerProtocolError(f"Invalid {label} length")
        return length

    def _handshake_message_length(self) -> int | None:
        data = self._buffer
        if self._phase == "protocol_version":
            if len(data) < 12:
                return None
            version = bytes(data[:12])
            if not version.startswith(b"RFB 003.") or version[11:12] != b"\n":
                raise _RfbServerProtocolError("Invalid RFB protocol version")
            try:
                self._protocol_minor = int(version[8:11])
            except ValueError as exc:
                raise _RfbServerProtocolError("Invalid RFB protocol version") from exc
            self._phase = "security_types"
            return 12

        if self._phase == "security_types":
            if self._protocol_minor <= 3:
                if len(data) < 4:
                    return None
                security_type = struct.unpack_from(">I", data, 0)[0]
                if security_type != _RFB_SECURITY_TYPE_NONE:
                    raise _RfbServerProtocolError("Unsupported RFB security type")
                self._phase = "server_init"
                return 4

            if not data:
                return None
            count = data[0]
            if count == 0:
                if len(data) < 5:
                    return None
                reason_length = self._bounded_length(
                    struct.unpack_from(">I", data, 1)[0], "RFB failure reason"
                )
                total = 5 + reason_length
                if len(data) < total:
                    return None
                self._phase = "failed"
                return total
            total = 1 + count
            if len(data) < total:
                return None
            if _RFB_SECURITY_TYPE_NONE not in data[1:total]:
                raise _RfbServerProtocolError("Unsupported RFB security types")
            self._phase = "security_result"
            return total

        if self._phase == "security_result":
            if len(data) < 4:
                return None
            result = struct.unpack_from(">I", data, 0)[0]
            self._phase = "server_init" if result == 0 else "failed"
            return 4

        if self._phase == "server_init":
            if len(data) < 24:
                return None
            name_length = self._bounded_length(
                struct.unpack_from(">I", data, 20)[0], "RFB desktop name"
            )
            total = 24 + name_length
            if len(data) < total:
                return None
            bits_per_pixel = data[4]
            self._pixel_size = 1 if bits_per_pixel == 8 else 4
            self._phase = "normal"
            return total

        if self._phase == "failed":
            raise _RfbServerProtocolError("RFB negotiation failed")
        return None

    def _rectangle_payload_length(
        self,
        data: bytearray,
        offset: int,
        width: int,
        height: int,
        encoding: int,
    ) -> int | None:
        if encoding == 0:  # Raw
            return self._bounded_length(
                width * height * self._pixel_size, "Raw rectangle"
            )
        if encoding == 1:  # CopyRect
            return 4
        if encoding == 16:  # ZRLE
            if len(data) < offset + 4:
                return None
            compressed = self._bounded_length(
                struct.unpack_from(">I", data, offset)[0], "compressed rectangle"
            )
            return 4 + compressed
        if encoding == -239:  # Cursor
            return self._bounded_length(
                width * height * self._pixel_size + ((width + 7) // 8) * height,
                "cursor rectangle",
            )
        if encoding == -224:  # LastRect
            return 0
        raise _RfbServerProtocolError(f"Unsupported RFB encoding {encoding}")

    def _framebuffer_update_length(self) -> int | None:
        data = self._buffer
        if len(data) < 4:
            return None
        rectangles = struct.unpack_from(">H", data, 2)[0]
        cursor = 4
        for _ in range(rectangles):
            if len(data) < cursor + 12:
                return None
            width, height = struct.unpack_from(">HH", data, cursor + 4)
            encoding = struct.unpack_from(">i", data, cursor + 8)[0]
            cursor += 12
            payload_length = self._rectangle_payload_length(
                data, cursor, width, height, encoding
            )
            if payload_length is None or len(data) < cursor + payload_length:
                return None
            cursor += payload_length
            if encoding == -224:  # LastRect terminates the update early.
                break
        return cursor

    def _normal_message_length(self) -> int | None:
        data = self._buffer
        if not data:
            return None
        message_type = data[0]
        if message_type == 0:
            return self._framebuffer_update_length()
        if message_type == 1:  # SetColourMapEntries
            if len(data) < 6:
                return None
            colors = struct.unpack_from(">H", data, 4)[0]
            return 6 + colors * 6
        if message_type in {2, 150}:  # Bell / EndOfContinuousUpdates
            return 1
        if message_type == 3:  # ServerCutText, including extended clipboard
            if len(data) < 8:
                return None
            signed_length = struct.unpack_from(">i", data, 4)[0]
            return 8 + self._bounded_length(abs(signed_length), "ServerCutText")
        if message_type == 248:  # ServerFence
            if len(data) < 9:
                return None
            return 9 + data[8]
        if message_type == 250:  # XVP
            return 4
        raise _RfbServerProtocolError(
            f"Unsupported RFB server message {message_type}"
        )

    def filter(self, data: bytes) -> bytes:
        self.last_dropped_clipboard = False
        self.last_saw_framebuffer = False
        if self.can_interact:
            filtered = _filter_vnc_server_message(data, can_interact=True)
            return filtered or b""

        if len(self._buffer) + len(data) > _RFB_SERVER_MAX_BUFFER:
            raise _RfbServerProtocolError("RFB server buffer limit exceeded")
        self._buffer.extend(data)
        result = bytearray()

        while self._buffer:
            if self._phase != "normal":
                message_length = self._handshake_message_length()
                if message_length is None:
                    break
                result.extend(self._buffer[:message_length])
                del self._buffer[:message_length]
                continue

            # KasmVNC BinaryClipboard is a WebSocket-framed extension without
            # a stream-level total length. Drop the complete buffered extension
            # and any coalesced tail rather than risk forwarding clipboard data.
            if self._buffer[0] == 180:
                self.last_dropped_clipboard = True
                self._buffer.clear()
                break

            message_length = self._normal_message_length()
            if message_length is None or len(self._buffer) < message_length:
                break
            if self._buffer[0] == 3:
                self.last_dropped_clipboard = True
            else:
                if self._buffer[0] == 0:
                    self.last_saw_framebuffer = True
                result.extend(self._buffer[:message_length])
            del self._buffer[:message_length]

        return bytes(result)


# ---------------------------------------------------------------------------
# RFB client message filter — strip extension types KasmVNC doesn't support
# ---------------------------------------------------------------------------
# noVNC v1.4 batches multiple RFB messages into one WebSocket frame.
# KasmVNC 1.3.3 crashes on unsupported types (150, 248, etc.).
# We parse message boundaries using known sizes and keep only standard types.

# Client→server message sizes (fixed, except 2 and 6 which encode length)
_RFB_MSG_SIZE: dict[int, int | None] = {
    0: 20,    # SetPixelFormat
    2: None,  # SetEncodings — 4 + numEncodings*4 (rewritten to strip bad pseudo-encodings)
    3: 10,    # FramebufferUpdateRequest
    4: 8,     # KeyEvent
    5: 6,     # PointerEvent
    6: None,  # ClientCutText — 8 + length
}

# Extension types that noVNC sends — known sizes so we can skip past them
# instead of breaking and dropping all trailing data in the frame.
_RFB_EXTENSION_SIZE: dict[int, int] = {
    150: 10,  # EnableContinuousUpdates (1+1+2+2+2+2)
    248: 10,  # QEMU-like key event (observed from noVNC 1.4.0)
    252: 4,   # xvp (1+1+1+1)
    255: 4,   # QEMU audio control (1+1+2) — noVNC QEMUExtendedKeyEvent is actually 12
}

# Whitelist of encodings safe to send to KasmVNC.
# Instead of trying to blocklist problematic pseudo-encodings (error-prone —
# we had wrong numbers), we ONLY keep known-good encodings.
# Anything not on this list is stripped from SetEncodings.
_ALLOWED_ENCODINGS: set[int] = {
    # Framebuffer encodings (standard RFB)
    0,    # Raw
    1,    # CopyRect
    2,    # RRE
    5,    # Hextile
    7,    # Tight
    16,   # ZRLE
    # Safe pseudo-encodings
    -239,  # Cursor (0xFFFFFF11) — cursor shape
    -224,  # LastRect (0xFFFFFF20) — performance optimization
    # Tight quality/compress levels (these are just hints)
    *range(-32, -22),   # quality levels 0-9
    *range(-256, -246),  # compress levels 0-9
}

# View-only egress filtering needs deterministic server-message boundaries.
# Keep one compressed framebuffer encoding (ZRLE), simple fallbacks, and the
# two pseudo-encodings whose rectangle payloads are unambiguous. Interactive
# connections retain the broader KasmVNC-compatible allow-list above.
_VIEWER_ALLOWED_ENCODINGS: set[int] = {
    0,     # Raw
    1,     # CopyRect
    16,    # ZRLE
    -239,  # Cursor
    -224,  # LastRect
}


def _rfb_msg_length(data: bytes, offset: int) -> int | None:
    """Return total length of the RFB message at offset, or None if unrecognized."""
    if offset >= len(data):
        return None
    msg_type = data[offset]
    fixed = _RFB_MSG_SIZE.get(msg_type)
    if fixed is not None:
        return fixed
    remaining = len(data) - offset
    if msg_type == 2 and remaining >= 4:  # SetEncodings
        num_enc = struct.unpack_from(">H", data, offset + 2)[0]
        return 4 + num_enc * 4
    if msg_type == 6 and remaining >= 8:  # ClientCutText
        length = struct.unpack_from(">I", data, offset + 4)[0]
        return 8 + length
    # Known extension types — skip past them instead of giving up
    ext_size = _RFB_EXTENSION_SIZE.get(msg_type)
    if ext_size is not None:
        return ext_size
    return None  # truly unknown type


def _rewrite_set_encodings(
    data: bytes,
    offset: int,
    msg_len: int,
    *,
    allowed_encodings: set[int] | None = None,
) -> bytes:
    """Keep only whitelisted encodings in a SetEncodings message."""
    _log = logging.getLogger("cloakbrowser.manager")
    allowed = _ALLOWED_ENCODINGS if allowed_encodings is None else allowed_encodings
    num_enc = struct.unpack_from(">H", data, offset + 2)[0]
    kept = []
    stripped = []
    for i in range(num_enc):
        enc = struct.unpack_from(">i", data, offset + 4 + i * 4)[0]  # signed
        if enc in allowed:
            kept.append(enc)
        else:
            stripped.append(enc)
    if not stripped:
        return data[offset:offset + msg_len]
    _log.info("RFB filter: SetEncodings keeping %d: %s, stripped %d: %s", len(kept), kept, len(stripped), stripped)
    result = struct.pack(">BxH", 2, len(kept))
    for enc in kept:
        result += struct.pack(">i", enc)
    return result


def _rewrite_pointer_event(data: bytes, offset: int) -> bytes:
    """Convert standard 6-byte PointerEvent to KasmVNC's 11-byte format.

    Standard RFB:  [5:u8][mask:u8][x:u16][y:u16]          = 6 bytes
    KasmVNC:       [5:u8][mask:u16][x:u16][y:u16][sx:s16][sy:s16] = 11 bytes
    """
    mask = data[offset + 1]
    x = struct.unpack_from(">H", data, offset + 2)[0]
    y = struct.unpack_from(">H", data, offset + 4)[0]
    # Expand mask from u8 to u16.  Scroll deltas (sx, sy) are zero because
    # noVNC encodes scroll as button-mask bits (3=up, 4=down, 5=left, 6=right)
    # which pass through in the mask.  KasmVNC accepts mask-bit scroll on its
    # extended 11-byte format, so explicit deltas are unnecessary.
    return struct.pack(">BHHHhh", 5, mask, x, y, 0, 0)


def _filter_rfb_client_messages(data: bytes) -> bytes:
    """Parse concatenated RFB messages, keep only standard types (0-6).

    Rewrites PointerEvents from 6-byte standard to 11-byte KasmVNC format
    and strips unsupported pseudo-encodings from SetEncodings.
    """
    _log = logging.getLogger("cloakbrowser.manager")
    result = bytearray()
    offset = 0
    msg_idx = 0
    while offset < len(data):
        msg_type = data[offset]
        msg_len = _rfb_msg_length(data, offset)
        if msg_len is None:
            _log.info("RFB filter: DROPPING unknown type=%d at offset=%d/%d, skipping %d trailing bytes, hex=%s",
                       msg_type, offset, len(data), len(data) - offset, data[offset:offset+20].hex())
            break
        if offset + msg_len > len(data):
            # Incomplete message — DO NOT forward partial data, it desynchronizes
            # the RFB stream (KasmVNC buffers partial reads across frames).
            _log.warning("RFB filter: DROPPING incomplete type=%d need=%d have=%d — would desync stream",
                         msg_type, msg_len, len(data) - offset)
            break
        msg_idx += 1
        if msg_type in _RFB_MSG_SIZE:
            # Standard RFB type — keep (with rewrites for KasmVNC compatibility)
            _log.debug("RFB filter: KEEP type=%d len=%d at offset=%d (msg #%d in frame)", msg_type, msg_len, offset, msg_idx)
            if msg_type == 2:  # SetEncodings — whitelist safe encodings
                result.extend(_rewrite_set_encodings(data, offset, msg_len))
            elif msg_type == 5:  # PointerEvent — expand to KasmVNC's 11-byte format
                result.extend(_rewrite_pointer_event(data, offset))
            else:
                result.extend(data[offset:offset + msg_len])
        else:
            # Extension type (150, 248, etc.) — skip but continue parsing
            _log.debug("RFB filter: SKIP extension type=%d len=%d at offset=%d (msg #%d in frame)", msg_type, msg_len, offset, msg_idx)
        offset += msg_len
    if len(result) != len(data):
        _log.info("RFB filter: input=%d output=%d (delta %+d bytes)", len(data), len(result), len(result) - len(data))
    return bytes(result)


def _filter_rfb_viewer_messages(data: bytes) -> bytes:
    """Keep only display-negotiation RFB messages for a view-only session.

    A noVNC client must still send SetPixelFormat, SetEncodings and
    FramebufferUpdateRequest after the initial RFB handshake; otherwise it
    never receives a framebuffer.  Key events, pointer events and clipboard
    transfers are intentionally excluded.  This makes a ``view`` grant a real
    live viewer rather than a connection that appears successful but stays
    black.
    """
    result = bytearray()
    offset = 0
    while offset < len(data):
        msg_type = data[offset]
        msg_len = _rfb_msg_length(data, offset)
        if msg_len is None or offset + msg_len > len(data):
            break

        if msg_type == 0:  # SetPixelFormat
            result.extend(data[offset:offset + msg_len])
        elif msg_type == 2:  # SetEncodings, with the same safe allow-list
            result.extend(
                _rewrite_set_encodings(
                    data,
                    offset,
                    msg_len,
                    allowed_encodings=_VIEWER_ALLOWED_ENCODINGS,
                )
            )
        elif msg_type == 3:  # FramebufferUpdateRequest
            result.extend(data[offset:offset + msg_len])
        offset += msg_len
    return bytes(result)


_RFB_CLIENT_PROTOCOL_VERSION = b"RFB 003.008\n"
_RFB_SECURITY_TYPE_NONE = 1
_RFB_CLIENT_INIT_SHARED_FLAGS = {0, 1}


class _RfbClientStreamFilter:
    """Filter client->server bytes after consuming exact RFB handshake bytes.

    KasmVNC is launched with SecurityTypes=None, so the noVNC client sends
    exactly three handshake messages: ProtocolVersion, SecurityType, ClientInit.
    Multiple handshake messages may be coalesced in one WebSocket frame.  Each
    byte must still match the next expected handshake state before it is
    forwarded; the first non-handshake byte is handled only after the handshake
    has completed and normal RFB filtering is active.
    """

    def __init__(self, *, can_interact: bool):
        self.can_interact = can_interact
        self._handshake_state = "protocol_version"
        self._protocol_version = bytearray()
        self.last_forwarded_handshake = False

    def _consume_handshake_byte(self, value: int) -> bool:
        if self._handshake_state == "protocol_version":
            expected = _RFB_CLIENT_PROTOCOL_VERSION[len(self._protocol_version)]
            if value != expected:
                return False
            self._protocol_version.append(value)
            if len(self._protocol_version) == len(_RFB_CLIENT_PROTOCOL_VERSION):
                self._handshake_state = "security_type"
            return True

        if self._handshake_state == "security_type":
            if value != _RFB_SECURITY_TYPE_NONE:
                return False
            self._handshake_state = "client_init"
            return True

        if self._handshake_state == "client_init":
            if value not in _RFB_CLIENT_INIT_SHARED_FLAGS:
                return False
            self._handshake_state = "done"
            return True

        return False

    def filter(self, data: bytes) -> bytes:
        result = bytearray()
        offset = 0
        self.last_forwarded_handshake = False

        while offset < len(data) and self._handshake_state != "done":
            if not self._consume_handshake_byte(data[offset]):
                return bytes(result)
            result.append(data[offset])
            self.last_forwarded_handshake = True
            offset += 1

        if offset < len(data):
            remaining = data[offset:]
            if self.can_interact:
                result.extend(_filter_rfb_client_messages(remaining))
            else:
                result.extend(_filter_rfb_viewer_messages(remaining))

        return bytes(result)


async def _run_profile_health_probe(profile: dict[str, object], running: Any) -> None:
    """Run and persist one normalized probe without exposing provider errors."""
    profile_id = str(profile["id"])
    if db.get_profile(profile_id) is None:
        return
    db.upsert_profile_health(
        profile_id,
        state="running",
        proxy_configured=bool(profile.get("proxy")),
        warnings=[],
        blockers=[],
        sources={},
    )
    try:
        result = await profile_health_probe.run(profile, running)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Profile health probe failed for %s (%s)", profile_id, type(exc).__name__)
        if db.get_profile(profile_id) is not None:
            db.upsert_profile_health(
                profile_id,
                state="failed",
                proxy_configured=bool(profile.get("proxy")),
                warnings=[],
                blockers=["profile_health_probe_failed"],
                error_code="profile_health_probe_failed",
                sources={},
            )
        return

    if db.get_profile(profile_id) is not None:
        db.upsert_profile_health(profile_id, **result.as_record())


def _schedule_profile_health(
    profile: dict[str, object],
    running: Any,
    *,
    force: bool,
) -> asyncio.Task[None] | None:
    """Schedule at most one probe per profile and persist the pending state."""
    profile_id = str(profile["id"])
    existing_task = _profile_health_tasks.get(profile_id)
    if existing_task is not None and not existing_task.done():
        return existing_task
    if existing_task is not None:
        _profile_health_tasks.pop(profile_id, None)
    if not force and db.get_profile_health(profile_id) is not None:
        return None

    db.upsert_profile_health(
        profile_id,
        state="pending",
        proxy_configured=bool(profile.get("proxy")),
        warnings=[],
        blockers=[],
        sources={},
    )
    task = asyncio.create_task(_run_profile_health_probe(profile, running))
    _profile_health_tasks[profile_id] = task

    def _forget(completed: asyncio.Task[None]) -> None:
        if _profile_health_tasks.get(profile_id) is completed:
            _profile_health_tasks.pop(profile_id, None)

    task.add_done_callback(_forget)
    return task


async def _cancel_profile_health_task(profile_id: str) -> None:
    task = _profile_health_tasks.pop(profile_id, None)
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _cancel_all_profile_health_tasks() -> None:
    tasks = list(_profile_health_tasks.values())
    _profile_health_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await browser_mgr.cleanup_stale()
    browser_mgr._auto_launch_task = asyncio.create_task(browser_mgr.auto_launch_all())
    logger.info("CloakBrowser Manager started")
    yield
    logger.info("Shutting down — stopping all browsers...")
    if browser_mgr._auto_launch_task and not browser_mgr._auto_launch_task.done():
        browser_mgr._auto_launch_task.cancel()
        await asyncio.gather(browser_mgr._auto_launch_task, return_exceptions=True)
    await _cancel_all_profile_health_tasks()
    await browser_mgr.cleanup_all()


app = FastAPI(title="CloakBrowser Manager", lifespan=lifespan)
app.add_middleware(AuthMiddleware)


def _require_identity(scope: Scope) -> access.AccessIdentity:
    identity = _access_identity(scope)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity


def _require_admin(scope: Scope) -> access.AccessIdentity:
    identity = _require_identity(scope)
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return identity


def _require_profile_permission(
    scope: Scope, profile_id: str, permission: access.Permission
) -> tuple[dict[str, object], access.AccessIdentity]:
    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    identity = _require_identity(scope)
    if not access.can_access_profile(identity, profile, permission):
        db.record_access_audit_event(
            identity.kind,
            identity.id,
            f"profile.permission.{permission}",
            "denied",
            str(profile.get("sandbox_id") or "default"),
            profile_id,
        )
        # Keep a profile outside the caller's scope indistinguishable from a
        # missing one. This applies equally to direct REST and WebSocket URLs.
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile, identity


def _can_read_task_sessions(identity: access.AccessIdentity, profile: dict[str, object]) -> bool:
    sandbox_id = str(profile.get("sandbox_id") or "default")
    return identity.is_admin or access.has_permission(identity, sandbox_id, "view")


def _can_write_task_sessions(identity: access.AccessIdentity, profile: dict[str, object]) -> bool:
    sandbox_id = str(profile.get("sandbox_id") or "default")
    return (
        identity.is_admin
        or access.has_permission(identity, sandbox_id, "interact")
        or access.has_permission(identity, sandbox_id, "automate")
    )


def _require_task_profile(
    scope: Scope, profile_id: str, permission: access.Permission = "interact"
) -> tuple[dict[str, object], access.AccessIdentity]:
    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    identity = _require_identity(scope)
    allowed = (
        _can_write_task_sessions(identity, profile)
        if permission == "interact"
        else _can_read_task_sessions(identity, profile)
    )
    if not allowed:
        db.record_access_audit_event(
            identity.kind,
            identity.id,
            f"task_session.permission.{permission}",
            "denied",
            str(profile.get("sandbox_id") or "default"),
            profile_id,
        )
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile, identity


def _require_task_session(
    scope: Scope, session_id: str, permission: access.Permission = "view"
) -> tuple[dict[str, object], dict[str, object], access.AccessIdentity]:
    session = db.get_task_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Task session not found")
    profile = db.get_profile(str(session["profile_id"]))
    identity = _require_identity(scope)
    allowed = False
    if profile:
        allowed = (
            _can_write_task_sessions(identity, profile)
            if permission == "interact"
            else _can_read_task_sessions(identity, profile)
        )
    if not profile or not allowed:
        if profile:
            db.record_access_audit_event(
                identity.kind,
                identity.id,
                f"task_session.permission.{permission}",
                "denied",
                str(profile.get("sandbox_id") or "default"),
                str(profile.get("id") or session["profile_id"]),
            )
        raise HTTPException(status_code=404, detail="Task session not found")
    return session, profile, identity


def _sanitize_task_value(value: object, depth: int = 0) -> object:
    if depth >= _TASK_METADATA_MAX_DEPTH:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [
            _sanitize_task_value(item, depth + 1)
            for item in value[:_TASK_METADATA_MAX_LIST_ITEMS]
        ]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)[:120]
            key_lower = key.lower()
            if any(part in key_lower for part in _TASK_SENSITIVE_KEY_PARTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_task_value(raw_value, depth + 1)
        return sanitized
    return str(value)[:500]


def _sanitize_task_metadata(metadata: dict[str, object] | None) -> dict[str, object]:
    sanitized = _sanitize_task_value(metadata or {})
    if not isinstance(sanitized, dict):
        return {}
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= _TASK_METADATA_MAX_BYTES:
        return sanitized
    return {"truncated": True}


async def _require_websocket_profile_permission(
    websocket: WebSocket, profile_id: str, permission: access.Permission
) -> tuple[dict[str, object], access.AccessIdentity] | None:
    profile = db.get_profile(profile_id)
    identity = _access_identity(websocket.scope)
    if not profile or not identity or not access.can_access_profile(identity, profile, permission):
        if profile and identity:
            db.record_access_audit_event(
                identity.kind,
                identity.id,
                f"profile.permission.{permission}",
                "denied",
                str(profile.get("sandbox_id") or "default"),
                profile_id,
            )
        await websocket.close(code=4404, reason="Profile not found")
        return None
    return profile, identity


def _profile_response(profile: dict[str, object], identity: access.AccessIdentity) -> ProfileResponse:
    """Build a minimally disclosed profile response for scoped callers."""
    response = dict(profile)
    status = browser_mgr.get_status(str(response["id"]))
    response["status"] = status["status"]
    response["vnc_ws_port"] = status["vnc_ws_port"]
    response["cdp_url"] = status["cdp_url"]
    response["tags"] = [TagResponse(**tag) for tag in response.get("tags", [])]

    if ACCESS_CONTROL_ENABLED and not identity.is_admin:
        # Viewer/operator cards do not reveal proxy credentials, fingerprint
        # implementation details, local data paths, notes, or CDP endpoints.
        response["proxy"] = None
        response["user_agent"] = None
        response["gpu_vendor"] = None
        response["gpu_renderer"] = None
        response["hardware_concurrency"] = None
        response["fingerprint_seed"] = 0
        response["launch_args"] = []
        response["notes"] = None
        response["user_data_dir"] = ""
        response["vnc_ws_port"] = None
        if not access.can_access_profile(identity, response, "automate"):
            response["cdp_url"] = None

    return ProfileResponse(**response)


def _access_user_response(user: dict[str, object]) -> AccessUserResponse:
    return AccessUserResponse(
        id=str(user["id"]),
        username=str(user["username"]),
        role=str(user["role"]),
        active=bool(user["active"]),
        created_at=str(user["created_at"]),
        group_ids=[str(group_id) for group_id in user.get("group_ids", [])],
        grants=user.get("grants", []),
        effective_grants=user.get("effective_grants", user.get("grants", [])),
    )


def _access_group_response(group: dict[str, object]) -> AccessGroupResponse:
    return AccessGroupResponse(
        id=str(group["id"]),
        name=str(group["name"]),
        description=group.get("description") or None,
        active=bool(group["active"]),
        created_at=str(group["created_at"]),
        member_user_ids=[str(user_id) for user_id in group.get("member_user_ids", [])],
        grants=group.get("grants", []),
    )


def _access_agent_response(agent: dict[str, object]) -> AccessAgentResponse:
    return AccessAgentResponse(
        id=str(agent["id"]),
        display_name=str(agent["display_name"]),
        paperclip_agent_id=agent.get("paperclip_agent_id") or None,
        active=bool(agent["active"]),
        created_at=str(agent["created_at"]),
        grants=agent.get("grants", []),
    )


def _normalize_access_grants(grants: list[object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for grant in grants:
        value = grant.model_dump() if hasattr(grant, "model_dump") else grant
        if isinstance(value, dict):
            key = (value.get("sandbox_id"), value.get("permission"))
            if key in seen:
                continue
            seen.add(key)
            normalized.append(value)
    return normalized


def _validate_access_group_member_ids(member_user_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    valid_ids: list[str] = []
    for user_id in member_user_ids:
        if user_id in seen:
            continue
        if not db.get_access_user(user_id):
            raise HTTPException(status_code=404, detail=f"User not found: {user_id}")
        seen.add(user_id)
        valid_ids.append(user_id)
    return valid_ids


def _validate_access_group_ids(group_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    valid_ids: list[str] = []
    for group_id in group_ids:
        if group_id in seen:
            continue
        if not db.get_access_group(group_id):
            raise HTTPException(status_code=404, detail=f"Group not found: {group_id}")
        seen.add(group_id)
        valid_ids.append(group_id)
    return valid_ids


def _revoke_user_websocket_access(user_ids: list[str]) -> None:
    for user_id in set(user_ids):
        _revoke_websocket_access(identity_kind="user", identity_id=user_id)


# ── Authentication ────────────────────────────────────────────────────────────


@app.get("/api/auth/status")
async def auth_status(request: starlette.requests.Request):
    """Check if auth is enabled and if the current request is authenticated.

    Exempt from auth middleware so the frontend can always call it.
    """
    if ACCESS_CONTROL_ENABLED:
        identity = _access_identity(request.scope)
    elif AUTH_TOKEN and _check_auth(request.scope):
        identity = access.bootstrap_identity()
    elif not AUTH_TOKEN:
        identity = _access_identity(request.scope)
    else:
        identity = None
    authenticated = (
        bool(identity)
        if ACCESS_CONTROL_ENABLED
        else _check_auth(request.scope)
        if AUTH_TOKEN
        else False
    )
    return {
        "auth_required": AUTH_TOKEN is not None,
        "access_control_enabled": ACCESS_CONTROL_ENABLED,
        "authenticated": authenticated,
        "identity": identity.public() if identity else None,
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest, request: Request, response: Response):
    if not AUTH_TOKEN:
        return {"ok": True}

    is_https = _is_https(request)
    if body.token:
        if not hmac.compare_digest(body.token, AUTH_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        response.delete_cookie(
            key="cbm_session", path="/", secure=is_https, samesite="strict",
        )
        response.set_cookie(
            key="auth_token",
            value=AUTH_TOKEN,
            httponly=True,
            samesite="strict",
            secure=is_https,
            path="/",
        )
        return {"ok": True, "identity": access.bootstrap_identity().public()}

    if not ACCESS_CONTROL_ENABLED or not body.username or not body.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    throttle_key = _login_throttle_key(body.username, request)
    retry_after = _login_backoff_remaining(throttle_key)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )
    user = db.get_access_user_by_username(body.username)
    valid_user = (
        user
        and bool(user.get("active"))
        and access.verify_password(body.password, str(user["password_hash"]))
    )
    if not valid_user:
        _record_login_failure(throttle_key)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _reset_login_failures(throttle_key)
    session = access.create_session(str(user["id"]), AUTH_TOKEN)
    response.set_cookie(
        key="cbm_session",
        value=session,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=is_https,
        path="/",
    )
    response.delete_cookie(
        key="auth_token", path="/", secure=is_https, samesite="strict",
    )
    # The new cookie is attached to the response, so build the identity from
    # the verified database record rather than asking the old request cookie.
    return {
        "ok": True,
        "identity": access.AccessIdentity(
            kind="user",
            id=str(user["id"]),
            display_name=str(user["username"]),
            role=str(user["role"]),
            grants=tuple(user.get("effective_grants", user.get("grants", []))),
            group_ids=tuple(user.get("group_ids", [])),
        ).public(),
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    is_https = _is_https(request)
    response.delete_cookie(
        key="auth_token", path="/", secure=is_https, samesite="strict",
    )
    response.delete_cookie(
        key="cbm_session", path="/", secure=is_https, samesite="strict",
    )
    return {"ok": True}


# ── Scoped identity administration ───────────────────────────────────────────


@app.get("/api/access/me", response_model=AccessIdentityResponse)
async def access_me(request: Request):
    return AccessIdentityResponse(**_require_identity(request.scope).public())


@app.get("/api/access/users", response_model=list[AccessUserResponse])
async def list_access_users(request: Request):
    _require_admin(request.scope)
    return [_access_user_response(user) for user in db.list_access_users()]


@app.post("/api/access/users", response_model=AccessUserResponse, status_code=201)
async def create_access_user(body: AccessUserCreate, request: Request):
    actor = _require_admin(request.scope)
    group_ids = _validate_access_group_ids(body.group_ids)
    try:
        user = db.create_access_user(
            body.username,
            access.hash_password(body.password),
            body.role,
            [grant.model_dump() for grant in body.grants],
            group_ids,
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Username already exists") from exc
        raise
    db.record_access_audit_event(actor.kind, actor.id, "access_user.create", "allowed")
    if group_ids:
        db.record_access_audit_event(
            actor.kind, actor.id, "access_user.groups.update", "allowed"
        )
    return _access_user_response(user)


@app.put("/api/access/users/{user_id}", response_model=AccessUserResponse)
async def update_access_user(user_id: str, body: AccessUserUpdate, request: Request):
    actor = _require_admin(request.scope)
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        data["password_hash"] = access.hash_password(data.pop("password"))
    if "grants" in data and data["grants"] is not None:
        # Pydantic's model_dump() has already converted nested AccessGrant
        # models to dictionaries.  Keep the endpoint compatible with both
        # shapes so editing a person's sandbox permissions never turns into a
        # 500 response in the access dashboard.
        data["grants"] = [
            grant.model_dump() if hasattr(grant, "model_dump") else grant
            for grant in data["grants"]
        ]
    if "group_ids" in data and data["group_ids"] is not None:
        data["group_ids"] = _validate_access_group_ids(data["group_ids"])
    user = db.update_access_user(user_id, **data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if {"password_hash", "role", "active", "grants", "group_ids"}.intersection(data):
        _revoke_websocket_access(identity_kind="user", identity_id=user_id)
    if "group_ids" in data:
        db.record_access_audit_event(
            actor.kind, actor.id, "access_user.groups.update", "allowed"
        )
    db.record_access_audit_event(actor.kind, actor.id, "access_user.update", "allowed")
    return _access_user_response(user)


@app.get("/api/access/groups", response_model=list[AccessGroupResponse])
async def list_access_groups(request: Request):
    _require_admin(request.scope)
    return [_access_group_response(group) for group in db.list_access_groups()]


@app.post("/api/access/groups", response_model=AccessGroupResponse, status_code=201)
async def create_access_group(body: AccessGroupCreate, request: Request):
    actor = _require_admin(request.scope)
    member_user_ids = _validate_access_group_member_ids(body.member_user_ids)
    try:
        group = db.create_access_group(
            body.name,
            body.description,
            body.active,
            member_user_ids,
            _normalize_access_grants(body.grants),
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Group name already exists") from exc
        raise
    _revoke_user_websocket_access(member_user_ids)
    db.record_access_audit_event(actor.kind, actor.id, "access_group.create", "allowed")
    return _access_group_response(group)


@app.put("/api/access/groups/{group_id}", response_model=AccessGroupResponse)
async def update_access_group(group_id: str, body: AccessGroupUpdate, request: Request):
    actor = _require_admin(request.scope)
    existing = db.get_access_group(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Group not found")
    data = body.model_dump(exclude_unset=True)
    prior_member_ids = [str(user_id) for user_id in existing.get("member_user_ids", [])]
    if "member_user_ids" in data and data["member_user_ids"] is not None:
        data["member_user_ids"] = _validate_access_group_member_ids(data["member_user_ids"])
    if "grants" in data and data["grants"] is not None:
        data["grants"] = _normalize_access_grants(data["grants"])
    try:
        group = db.update_access_group(group_id, **data)
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Group name already exists") from exc
        raise
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    current_member_ids = [str(user_id) for user_id in group.get("member_user_ids", [])]
    if {"active", "member_user_ids", "grants"}.intersection(data):
        _revoke_user_websocket_access(prior_member_ids + current_member_ids)
    if "member_user_ids" in data:
        db.record_access_audit_event(
            actor.kind, actor.id, "access_group.members.update", "allowed"
        )
    if "grants" in data:
        db.record_access_audit_event(
            actor.kind, actor.id, "access_group.grants.update", "allowed"
        )
    db.record_access_audit_event(actor.kind, actor.id, "access_group.update", "allowed")
    return _access_group_response(group)


@app.get("/api/access/agents", response_model=list[AccessAgentResponse])
async def list_access_agents(request: Request):
    _require_admin(request.scope)
    return [_access_agent_response(agent) for agent in db.list_access_agents()]


@app.post("/api/access/agents", response_model=AccessAgentCreatedResponse, status_code=201)
async def create_access_agent(body: AccessAgentCreate, request: Request):
    actor = _require_admin(request.scope)
    key = access.generate_agent_key()
    agent = db.create_access_agent(
        body.display_name,
        access.hash_agent_key(key),
        body.paperclip_agent_id,
        [grant.model_dump() for grant in body.grants],
    )
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.create", "allowed")
    return AccessAgentCreatedResponse(**_access_agent_response(agent).model_dump(), api_key=key)


@app.put("/api/access/agents/{agent_id}", response_model=AccessAgentResponse)
async def update_access_agent(agent_id: str, body: AccessAgentUpdate, request: Request):
    actor = _require_admin(request.scope)
    data = body.model_dump(exclude_unset=True)
    if "grants" in data and data["grants"] is not None:
        data["grants"] = [
            grant.model_dump() if hasattr(grant, "model_dump") else grant
            for grant in data["grants"]
        ]
    agent = db.update_access_agent(agent_id, **data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if {"active", "grants"}.intersection(data):
        _revoke_websocket_access(identity_kind="agent", identity_id=agent_id)
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.update", "allowed")
    return _access_agent_response(agent)


@app.post("/api/access/agents/{agent_id}/rotate-key", response_model=AccessAgentCreatedResponse)
async def rotate_access_agent_key(agent_id: str, request: Request):
    actor = _require_admin(request.scope)
    key = access.generate_agent_key()
    agent = db.update_access_agent(agent_id, key_hash=access.hash_agent_key(key))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _revoke_websocket_access(identity_kind="agent", identity_id=agent_id)
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.rotate_key", "allowed")
    return AccessAgentCreatedResponse(**_access_agent_response(agent).model_dump(), api_key=key)


@app.get("/api/access/sandboxes")
async def list_access_sandboxes(request: Request):
    _require_admin(request.scope)
    summaries: dict[str, dict[str, Any]] = {}
    for profile in db.list_profiles():
        sandbox_id = str(profile.get("sandbox_id") or "default")
        summary = summaries.setdefault(
            sandbox_id,
            {
                "profile_count": 0,
                "project_ids": set(),
                "folder_paths": set(),
                "profile_names": set(),
            },
        )
        summary["profile_count"] += 1
        summary["project_ids"].add(str(profile.get("project_id") or "default"))
        folder_path = str(profile.get("folder_path") or "")
        if folder_path:
            summary["folder_paths"].add(folder_path)
        summary["profile_names"].add(str(profile.get("name") or "Unnamed profile"))

    return [
        {
            "sandbox_id": sandbox_id,
            "profile_count": summaries[sandbox_id]["profile_count"],
            "project_ids": sorted(summaries[sandbox_id]["project_ids"]),
            "folder_paths": sorted(summaries[sandbox_id]["folder_paths"]),
            "profile_names": sorted(summaries[sandbox_id]["profile_names"]),
        }
        for sandbox_id in sorted(summaries)
    ]


# ── Task sessions ───────────────────────────────────────────────────────────


@app.post("/api/task-sessions", response_model=TaskSessionResponse, status_code=201)
async def create_task_session(body: TaskSessionCreate, request: Request):
    profile, identity = _require_task_profile(request.scope, body.profile_id, "interact")
    session = db.create_task_session(
        str(profile["id"]),
        str(profile.get("sandbox_id") or "default"),
        identity.kind,
        identity.id,
        body.title,
        _sanitize_task_metadata(body.metadata),
    )
    db.record_task_event(
        str(session["id"]),
        "task_session.created",
        identity.kind,
        identity.id,
        {"profile_id": str(profile["id"]), "sandbox_id": str(profile.get("sandbox_id") or "default")},
    )
    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "task_session.create",
        "allowed",
        str(profile.get("sandbox_id") or "default"),
        str(profile["id"]),
    )
    return TaskSessionResponse(**session)


@app.get("/api/task-sessions", response_model=list[TaskSessionResponse])
async def list_task_sessions(
    request: Request,
    profile_id: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(100, ge=1, le=200),
):
    profile, _identity = _require_task_profile(request.scope, profile_id, "view")
    return [
        TaskSessionResponse(**session)
        for session in db.list_task_sessions(str(profile["id"]), limit=limit)
    ]


@app.get("/api/task-sessions/{session_id}", response_model=TaskSessionResponse)
async def get_task_session(session_id: str, request: Request):
    session, _profile, _identity = _require_task_session(request.scope, session_id, "view")
    return TaskSessionResponse(**session)


def _append_task_user_message(
    scope: Scope,
    session_id: str,
    text: str,
    profile_id: str | None,
    commands: list[object],
    metadata: dict[str, object],
) -> TaskMessageResponse:
    session, profile, identity = _require_task_session(scope, session_id, "interact")
    if profile_id and profile_id != str(session["profile_id"]):
        requested_profile = db.get_profile(profile_id)
        if requested_profile:
            db.record_access_audit_event(
                identity.kind,
                identity.id,
                "task_session.profile_mismatch",
                "denied",
                str(requested_profile.get("sandbox_id") or "default"),
                profile_id,
            )
        raise HTTPException(status_code=404, detail="Task session not found")

    command_payload = [
        command.model_dump() if hasattr(command, "model_dump") else command
        for command in commands
    ]
    stored_metadata_input = dict(metadata)
    if command_payload:
        stored_metadata_input["commands"] = command_payload
    stored_metadata = _sanitize_task_metadata(stored_metadata_input)
    message = db.append_task_message(
        str(session["id"]),
        "user",
        text,
        identity.kind,
        identity.id,
        stored_metadata,
    )
    host_command_count = sum(
        1 for command in command_payload
        if isinstance(command, dict) and command.get("scope") == "host"
    )
    db.record_task_event(
        str(session["id"]),
        "task_message.appended",
        identity.kind,
        identity.id,
        {
            "message_id": str(message["id"]),
            "role": "user",
            "command_count": len(command_payload),
            "host_command_count": host_command_count,
            "server_executed": False,
        },
    )
    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "task_message.append",
        "allowed",
        str(profile.get("sandbox_id") or "default"),
        str(profile["id"]),
    )
    return TaskMessageResponse(**message)


@app.post(
    "/api/task-sessions/{session_id}/messages",
    response_model=TaskMessageResponse,
    status_code=201,
)
async def append_task_message(session_id: str, body: TaskMessageCreate, request: Request):
    return _append_task_user_message(
        request.scope,
        session_id,
        body.text,
        body.profile_id,
        body.commands,
        body.metadata,
    )


@app.post(
    "/api/task-sessions/{session_id}/commands",
    response_model=TaskMessageResponse,
    status_code=201,
)
async def append_task_command(session_id: str, body: TaskCommandRequest, request: Request):
    return _append_task_user_message(
        request.scope,
        session_id,
        body.content,
        body.profile_id,
        body.commands,
        body.metadata,
    )


@app.get("/api/task-sessions/{session_id}/messages", response_model=list[TaskMessageResponse])
async def list_task_messages(
    session_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
):
    session, _profile, _identity = _require_task_session(request.scope, session_id, "view")
    return [
        TaskMessageResponse(**message)
        for message in db.list_task_messages(str(session["id"]), limit=limit)
    ]


@app.get("/api/task-sessions/{session_id}/events", response_model=list[TaskEventResponse])
async def list_task_events(
    session_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
):
    session, _profile, _identity = _require_task_session(request.scope, session_id, "view")
    return [
        TaskEventResponse(**event)
        for event in db.list_task_events(str(session["id"]), limit=limit)
    ]


# ── Profile CRUD ──────────────────────────────────────────────────────────────


def _proxy_inventory_item(row: dict[str, object]) -> ProxyInventoryItem:
    return ProxyInventoryItem(
        id=str(row["id"]),
        label=str(row.get("label") or "proxy"),
        host_masked=str(row.get("host_masked") or "unknown"),
        port=row.get("port") if isinstance(row.get("port"), int) else None,
        username_masked=row.get("username_masked") if isinstance(row.get("username_masked"), str) else None,
        has_credentials=bool(row.get("has_credentials")),
        active=bool(row.get("active", True)),
        check_state=str(row.get("check_state") or "missing"),  # type: ignore[arg-type]
        reachable=row.get("reachable") if isinstance(row.get("reachable"), bool) else None,
        latency_ms=row.get("latency_ms") if isinstance(row.get("latency_ms"), (int, float)) else None,
        risk_score=row.get("risk_score") if isinstance(row.get("risk_score"), int) else None,
        authenticity_score=(
            row.get("authenticity_score") if isinstance(row.get("authenticity_score"), int) else None
        ),
        country_code=row.get("country_code") if isinstance(row.get("country_code"), str) else None,
        timezone_hint=row.get("timezone_hint") if isinstance(row.get("timezone_hint"), str) else None,
        locale_hint=row.get("locale_hint") if isinstance(row.get("locale_hint"), str) else None,
        warnings=[str(item) for item in (row.get("warnings") or [])],
        blockers=[str(item) for item in (row.get("blockers") or [])],
        last_checked_at=row.get("last_checked_at") if isinstance(row.get("last_checked_at"), str) else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


async def _proxychecker_check(proxy_url: str) -> dict[str, object]:
    """Call VCVM-local proxychecker and reduce to redacted summary fields."""
    base = getattr(profile_health_probe, "proxychecker_url", "") or ""
    if not base:
        return {
            "reachable": None,
            "latency_ms": None,
            "risk_score": None,
            "authenticity_score": None,
            "country_code": None,
            "timezone_hint": None,
            "locale_hint": None,
            "warnings": [],
            "blockers": ["proxychecker_unavailable"],
            "check_state": "unavailable",
        }
    try:
        async with httpx.AsyncClient(timeout=getattr(profile_health_probe, "component_timeout_s", 20.0)) as client:
            response = await client.post(
                f"{base.rstrip('/')}/check",
                json={
                    "target": getattr(profile_health_probe, "ip_echo_url", "https://api.ipify.org"),
                    "proxies": [proxy_url],
                    "limit": 1,
                    "scoring_profile": "default",
                },
            )
            response.raise_for_status()
            return proxy_inventory.summarize_check_payload(response.json())
    except Exception:
        return {
            "reachable": None,
            "latency_ms": None,
            "risk_score": None,
            "authenticity_score": None,
            "country_code": None,
            "timezone_hint": None,
            "locale_hint": None,
            "warnings": [],
            "blockers": ["proxychecker_unavailable"],
            "check_state": "unavailable",
        }


@app.get("/api/proxies", response_model=list[ProxyInventoryItem])
async def list_proxies(request: Request):
    identity = _require_admin(request.scope)
    del identity  # admin gate only
    return [_proxy_inventory_item(row) for row in db.list_proxy_inventory()]


@app.post("/api/proxies/ingest", response_model=ProxyInventoryIngestResponse, status_code=201)
async def ingest_proxies(req: ProxyInventoryIngest, request: Request):
    identity = _require_admin(request.scope)
    created = 0
    updated = 0
    rejected = 0
    items: list[ProxyInventoryItem] = []

    for raw in req.lines:
        try:
            proxy_url = proxy_inventory.parse_proxy_line(raw)
        except proxy_inventory.ProxyParseError:
            rejected += 1
            continue
        fingerprint = proxy_inventory.proxy_fingerprint(proxy_url)
        with db.get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM proxy_inventory WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        redacted = proxy_inventory.redact_proxy_url(proxy_url)
        row = db.upsert_proxy_inventory_entry(proxy_url, redacted=redacted)
        if existing:
            updated += 1
        else:
            created += 1
        items.append(_proxy_inventory_item(row))

    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "proxy.ingest",
        "allowed",
        None,
        None,
    )
    return ProxyInventoryIngestResponse(
        created=created,
        updated=updated,
        rejected=rejected,
        items=items,
    )


@app.post("/api/proxies/{proxy_id}/check", response_model=ProxyInventoryItem)
async def check_proxy(proxy_id: str, request: Request):
    identity = _require_admin(request.scope)
    secret_row = db.get_proxy_inventory_entry(proxy_id, include_secret=True)
    if secret_row is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    proxy_url = str(secret_row.get("proxy_url") or "")
    summary = await _proxychecker_check(proxy_url)
    updated = db.update_proxy_inventory_check(proxy_id, summary)
    if updated is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "proxy.check",
        "allowed",
        None,
        None,
    )
    return _proxy_inventory_item(updated)


@app.post("/api/proxies/{proxy_id}/profiles", response_model=ProfileResponse, status_code=201)
async def create_profile_from_proxy(
    proxy_id: str,
    req: ProxyAutoProfileCreate,
    request: Request,
):
    """Create a proxy-aligned anti-stealth profile without manual fingerprint tuning."""
    identity = _require_admin(request.scope)
    secret_row = db.get_proxy_inventory_entry(proxy_id, include_secret=True)
    if secret_row is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    proxy_url = str(secret_row.get("proxy_url") or "")
    country = secret_row.get("country_code") if isinstance(secret_row.get("country_code"), str) else None
    if country is None:
        # Soft enrichment before profile create; degrade if checker is down.
        summary = await _proxychecker_check(proxy_url)
        db.update_proxy_inventory_check(proxy_id, summary)
        country = summary.get("country_code") if isinstance(summary.get("country_code"), str) else None

    defaults = proxy_inventory.build_auto_profile_defaults(
        proxy_url=proxy_url,
        country_code=country,
        name=req.name,
        project_id=req.project_id,
        harness=req.harness,
        sandbox_id=req.sandbox_id,
    )
    tags = defaults.pop("tags", [])
    profile = db.create_profile(**defaults, tags=tags)
    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "profile.create_from_proxy",
        "allowed",
        str(profile.get("sandbox_id") or "default"),
        str(profile.get("id") or ""),
    )
    if req.launch:
        try:
            running = await browser_mgr.launch(profile)
            _schedule_profile_health(profile, running, force=False)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to launch profile") from exc
    return _profile_response(profile, identity)


@app.get("/api/profiles", response_model=list[ProfileResponse])
async def list_profiles(request: Request):
    identity = _require_identity(request.scope)
    return [
        _profile_response(profile, identity)
        for profile in db.list_profiles()
        if access.can_access_profile(identity, profile, "view")
    ]


@app.post("/api/profiles", response_model=ProfileResponse, status_code=201)
async def create_profile(req: ProfileCreate, request: Request):
    identity = _require_admin(request.scope)
    data = req.model_dump()
    tags = data.pop("tags", None)
    if tags:
        data["tags"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in tags]
    else:
        data["tags"] = []
    profile = db.create_profile(**data)
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.create", "allowed", str(profile.get("sandbox_id") or "default"), profile["id"]
    )
    return _profile_response(profile, identity)


@app.get("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "view")
    return _profile_response(profile, identity)



@app.post("/api/profiles/bulk-organize", response_model=list[ProfileResponse])
async def bulk_organize_profiles(req: ProfileBulkOrganize, request: Request):
    """Move or pin multiple profiles. Matches profile update: administrator only."""
    identity = _require_admin(request.scope)
    if req.project_id is None and req.folder_path is None and req.pinned is None:
        raise HTTPException(status_code=400, detail="No organization fields provided")

    try:
        updated = db.bulk_organize_profiles(
            req.profile_ids,
            project_id=req.project_id,
            folder_path=req.folder_path,
            pinned=req.pinned,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found")

    for row in updated:
        db.record_access_audit_event(
            identity.kind,
            identity.id,
            "profile.bulk_organize",
            "allowed",
            str(row.get("sandbox_id") or "default"),
            str(row.get("id") or ""),
        )
    return [_profile_response(row, identity) for row in updated]


@app.put("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile(profile_id: str, req: ProfileUpdate, request: Request):
    identity = _require_admin(request.scope)
    # Only pass fields that were explicitly set
    data = req.model_dump(exclude_unset=True)
    tags = data.pop("tags", None)
    if tags is not None:
        data["tags"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in tags]
    profile = db.update_profile(profile_id, **data)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if "sandbox_id" in data:
        _revoke_websocket_access(profile_id=profile_id)
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.update", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return _profile_response(profile, identity)


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: str, request: Request):
    identity = _require_admin(request.scope)
    # Stop browser if running
    if profile_id in browser_mgr.running:
        await browser_mgr.stop(profile_id)
    await _cancel_profile_health_task(profile_id)

    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    user_data_dir = Path(profile["user_data_dir"])

    # DB first — if this fails, filesystem is untouched
    db.delete_profile(profile_id)

    # Then clean up disk
    if user_data_dir.exists():
        shutil.rmtree(user_data_dir, ignore_errors=True)

    db.record_access_audit_event(
        identity.kind, identity.id, "profile.delete", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return {"ok": True}


# ── Launch / Stop ─────────────────────────────────────────────────────────────


@app.post("/api/profiles/{profile_id}/launch", response_model=LaunchResponse)
async def launch_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "operate")
    if profile_id in browser_mgr.running:
        raise HTTPException(status_code=409, detail="Profile is already running")

    try:
        running = await browser_mgr.launch(profile)
    except ValueError:
        # Validation errors can originate in URL-parsing or browser libraries
        # and may contain embedded proxy credentials. Keep the API boundary
        # intentionally generic while preserving the useful 400 classification.
        raise HTTPException(status_code=400, detail="Invalid browser profile configuration")
    except Exception as exc:
        # Third-party launch errors may echo the configured proxy URL. Log the
        # exception class for diagnosis without persisting embedded credentials.
        logger.error("Failed to launch profile %s (%s)", profile_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to launch browser")

    db.record_access_audit_event(
        identity.kind, identity.id, "profile.launch", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    try:
        _schedule_profile_health(profile, running, force=False)
    except Exception as exc:
        logger.error("Could not schedule profile health for %s (%s)", profile_id, type(exc).__name__)
    return LaunchResponse(
        profile_id=profile_id,
        status="running",
        vnc_ws_port=running.ws_port,
        display=f":{running.display}",
        cdp_url=(
            f"/api/profiles/{profile_id}/cdp"
            if access.can_access_profile(identity, profile, "automate")
            else None
        ),
    )


@app.get("/api/profiles/{profile_id}/health", response_model=ProfileHealthResponse)
async def get_profile_health(profile_id: str, request: Request):
    profile, _identity = _require_profile_permission(request.scope, profile_id, "view")
    stored = db.get_profile_health(profile_id)
    if stored is None:
        return ProfileHealthResponse(
            profile_id=profile_id,
            proxy_configured=bool(profile.get("proxy")),
        )
    return ProfileHealthResponse(**stored)


@app.get("/api/profiles/{profile_id}/extensions", response_model=ExtensionInventoryResponse)
async def get_profile_extensions(profile_id: str, request: Request):
    profile, _identity = _require_profile_permission(request.scope, profile_id, "view")
    ext_list = extensions.inspect_profile_extensions(profile)
    return ExtensionInventoryResponse(profile_id=profile_id, extensions=ext_list)



@app.post(
    "/api/profiles/{profile_id}/health/run",
    response_model=ProfileHealthResponse,
    status_code=202,
)
async def run_profile_health(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "operate")
    running = browser_mgr.running.get(profile_id)
    if running is None:
        raise HTTPException(status_code=409, detail="Profile is not running")

    _schedule_profile_health(profile, running, force=True)
    db.record_access_audit_event(
        identity.kind,
        identity.id,
        "profile.health.run",
        "allowed",
        str(profile.get("sandbox_id") or "default"),
        profile_id,
    )
    stored = db.get_profile_health(profile_id)
    return ProfileHealthResponse(
        **(
            stored
            or {
                "profile_id": profile_id,
                "state": "pending",
                "proxy_configured": bool(profile.get("proxy")),
            }
        )
    )


@app.post("/api/profiles/{profile_id}/stop")
async def stop_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "operate")
    if profile_id not in browser_mgr.running:
        raise HTTPException(status_code=404, detail="Profile is not running")
    await browser_mgr.stop(profile_id)
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.stop", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return {"ok": True}


@app.get("/api/profiles/{profile_id}/status", response_model=ProfileStatusResponse)
async def get_profile_status(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "view")
    status = browser_mgr.get_status(profile_id)
    if ACCESS_CONTROL_ENABLED and not identity.is_admin:
        # Scoped callers connect through stable, authenticated proxy routes;
        # exposing ephemeral local listener ports or an automation endpoint is
        # unnecessary and leaks more than a view grant needs.
        status["vnc_ws_port"] = None
        if not access.can_access_profile(identity, profile, "automate"):
            status["cdp_url"] = None
    return ProfileStatusResponse(**status)


# ── System Status ─────────────────────────────────────────────────────────────


@app.get("/health")
async def get_health():
    """Minimal unauthenticated liveness endpoint for container health checks."""
    return {"ok": True}


@app.get("/api/status", response_model=StatusResponse)
async def get_system_status(request: Request):
    _require_identity(request.scope)
    from cloakbrowser.config import CHROMIUM_VERSION

    profiles = db.list_profiles()
    return StatusResponse(
        running_count=len(browser_mgr.running),
        binary_version=CHROMIUM_VERSION,
        profiles_total=len(profiles),
    )


@app.get("/api/admin/live-diagnostics")
async def get_live_diagnostics(request: Request) -> dict[str, Any]:
    """Return administrator-only live launch and VNC counters.

    Unmeasured timings are explicit ``unavailable`` values. The payload never
    includes ports, display numbers, filesystem paths, URLs, proxy data, or
    browser content. This endpoint does not start benchmarks or mutate runtime.
    """
    _require_admin(request.scope)
    return live_diagnostics.live_diagnostics.snapshot(
        running_profile_ids=set(browser_mgr.running.keys())
    )


@app.get("/api/benchmarks/latest")
async def get_latest_benchmark_report(request: Request) -> dict[str, Any]:
    """Serve a redacted, administrator-only benchmark summary.

    The source path is server-side only: ``BENCHMARK_REPORT_PATH`` when set,
    otherwise ``/data/benchmark-report.json`` for persistent container storage.
    This endpoint reads a bounded JSON file, never starts or shells out to a
    benchmark runner, and strips local endpoints, commands, paths, raw process
    output, and per-iteration observations before returning data to the UI.
    """
    _require_admin(request.scope)
    return _public_latest_benchmark_report(_load_latest_benchmark_report())


# ── Clipboard Relay ──────────────────────────────────────────────────────────

_CLIPBOARD_MAX_READ = 1_048_576  # 1MB cap on GET response

# Track xclip processes per display so we can kill the old one before spawning new
_xclip_procs: dict[int, asyncio.subprocess.Process] = {}


@app.post("/api/profiles/{profile_id}/clipboard")
async def set_clipboard(profile_id: str, body: ClipboardRequest, request: Request):
    """Push text into the VNC session's X clipboard via xclip."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "interact")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    import os

    # Kill previous xclip for this display (it stays alive to serve paste)
    old = _xclip_procs.pop(running.display, None)
    if old and old.returncode is None:
        old.kill()
        await old.wait()

    env = {**os.environ, "DISPLAY": f":{running.display}"}
    proc = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard",
        stdin=asyncio.subprocess.PIPE,
        env=env,
    )
    # xclip reads stdin then stays alive to serve paste requests.
    proc.stdin.write(body.text.encode())  # type: ignore[union-attr]
    await proc.stdin.drain()  # type: ignore[union-attr]
    proc.stdin.close()  # type: ignore[union-attr]

    _xclip_procs[running.display] = proc

    return {"ok": True}


@app.get("/api/profiles/{profile_id}/clipboard")
async def get_clipboard(profile_id: str, request: Request):
    """Read the VNC session's clipboard.

    Chrome doesn't write to X11 clipboard under KasmVNC, so xclip can't read it.
    Instead, read via Playwright's CDP connection to Chrome (navigator.clipboard.readText).
    Falls back to xclip for non-Chrome clipboard owners.
    """
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "interact")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    # Read Chrome's current text selection via Playwright.
    # Chrome's native copy (via VNC Ctrl+C) doesn't write to X11 clipboard
    # and doesn't fire DOM events, so we read the visible selection instead.
    # The init script also captures copy events when they do fire.
    # Check all pages — user may have copied in any tab
    try:
        for page in running.context.pages:
            try:
                text = await page.evaluate("window.__clipboardText || ''")
                if text:
                    return {"text": text[:_CLIPBOARD_MAX_READ]}
            except Exception as exc:
                logger.debug("Clipboard read failed on page: %s", exc)
                continue
    except Exception as exc:
        logger.debug("Playwright clipboard read failed: %s", exc)

    # Fallback: xclip for non-Chrome clipboard owners
    import os

    env = {**os.environ, "DISPLAY": f":{running.display}"}
    proc = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard", "-o",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"text": ""}

    if proc.returncode != 0:
        return {"text": ""}

    text = stdout[:_CLIPBOARD_MAX_READ].decode("utf-8", errors="replace")
    return {"text": text}


# ── VNC WebSocket Proxy ──────────────────────────────────────────────────────


@app.websocket("/api/profiles/{profile_id}/vnc")
async def vnc_proxy(websocket: WebSocket, profile_id: str):
    """Proxy WebSocket frames between the frontend and a profile's KasmVNC."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "view")
    if not access_result:
        return
    _profile, identity = access_result
    can_interact = access.can_access_profile(identity, _profile, "interact")

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    # Accept with client's requested subprotocol (if any) — RFC 6455 requires
    # the server must not respond with a subprotocol the client didn't request.
    requested = websocket.scope.get("subprotocols", [])
    subprotocol = "binary" if "binary" in requested else None
    import websockets

    access_lease = _register_websocket_access(identity, profile_id)
    diagnostics_session_id: int | None = None
    try:
        await websocket.accept(subprotocol=subprotocol)
    except BaseException:
        _unregister_websocket_access(access_lease)
        raise

    diagnostics_session_id = live_diagnostics.live_diagnostics.begin_vnc_session(profile_id)
    framebuffer_detector = live_diagnostics.FirstFramebufferDetector()
    vnc_url = f"ws://127.0.0.1:{running.ws_port}/websockify"

    try:
        async with websockets.connect(
            vnc_url,
            subprotocols=["binary"],
            origin=f"http://127.0.0.1:{running.ws_port}",
            max_size=None,  # VNC frames can be large (1920x1080 framebuffer)
            ping_interval=None,  # KasmVNC doesn't respond to WS pings
            ping_timeout=None,
            compression=None,  # KasmVNC can't handle permessage-deflate
        ) as vnc_ws:
            live_diagnostics.live_diagnostics.mark_vnc_websocket_open(
                profile_id, diagnostics_session_id
            )
            logger.info(
                "VNC proxy: connected to KasmVNC for %s (subprotocol=%s)",
                profile_id, vnc_ws.subprotocol,
            )

            # noVNC v1.4 sends extension message types (150=ContinuousUpdates,
            # 248=QEMUKey, etc.) that KasmVNC 1.3.3 doesn't support, causing
            # "unknown message type" → disconnect.
            #
            # noVNC batches multiple RFB messages into a single WebSocket frame,
            # so we must parse the RFB stream to find message boundaries and strip
            # unsupported types before forwarding. Standard client→server types
            # have known fixed sizes (except SetEncodings and ClientCutText which
            # encode their length).

            async def client_to_vnc():
                count = 0
                dropped = 0
                rfb_filter = _RfbClientStreamFilter(can_interact=can_interact)
                try:
                    while True:
                        msg = await websocket.receive()
                        msg_type = msg.get("type", "")
                        if msg_type == "websocket.disconnect":
                            logger.info("VNC proxy [c->v]: client disconnect (code=%s) after %d msgs (%d dropped)", msg.get("code"), count, dropped)
                            break
                        if "bytes" in msg and msg["bytes"]:
                            count += 1
                            data = msg["bytes"]
                            filtered = rfb_filter.filter(data)
                            if filtered:
                                # Safety: verify first byte is a valid RFB client type
                                if (
                                    not rfb_filter.last_forwarded_handshake
                                    and filtered[0] not in _RFB_MSG_SIZE
                                ):
                                    logger.error("RFB SAFETY: refusing to send data with invalid first byte=%d hex=%s",
                                                 filtered[0], filtered[:20].hex())
                                    dropped += 1
                                    continue
                                logger.debug("VNC send: %d bytes first_type=%d hex=%s", len(filtered), filtered[0], filtered[:100].hex())
                                await vnc_ws.send(filtered)
                            else:
                                dropped += 1

                        elif "text" in msg and msg["text"]:
                            if not can_interact:
                                dropped += 1
                                continue
                            # noVNC only sends binary frames — text frames are unexpected
                            # and would bypass the RFB filter, so drop them.
                            count += 1
                            logger.warning("VNC proxy [c->v]: DROPPING text frame len=%d (noVNC should only send binary)", len(msg["text"]))
                            dropped += 1
                        else:
                            logger.warning("VNC proxy [c->v]: unhandled msg keys=%s type=%s", list(msg.keys()), msg_type)
                except WebSocketDisconnect as exc:
                    logger.info("VNC proxy [c->v]: WebSocketDisconnect code=%s after %d msgs (%d dropped)", exc.code, count, dropped)
                except Exception as exc:
                    logger.warning("VNC proxy [c->v]: %s: %s (after %d msgs)", type(exc).__name__, exc, count)

            async def vnc_to_client():
                count = 0
                dropped = 0
                rfb_filter = _RfbServerStreamFilter(can_interact=can_interact)
                try:
                    async for msg in vnc_ws:
                        count += 1
                        if isinstance(msg, bytes):
                            filtered = rfb_filter.filter(msg)
                            saw_framebuffer = False
                            if diagnostics_session_id is not None and not framebuffer_detector.seen:
                                saw_framebuffer = framebuffer_detector.observe(msg)
                                if rfb_filter.last_saw_framebuffer:
                                    saw_framebuffer = True
                                    framebuffer_detector.seen = True
                                if saw_framebuffer:
                                    live_diagnostics.live_diagnostics.mark_vnc_first_framebuffer(
                                        profile_id, diagnostics_session_id
                                    )
                            if rfb_filter.last_dropped_clipboard:
                                dropped += 1
                                logger.info(
                                    "VNC proxy [v->c]: dropped clipboard message for %s",
                                    identity.kind,
                                )
                            if not filtered:
                                continue
                            await websocket.send_bytes(filtered)
                        else:
                            if not can_interact:
                                dropped += 1
                                continue
                            await websocket.send_text(msg)
                    logger.info("VNC proxy [v->c]: KasmVNC stream ended after %d msgs (%d dropped, close_code=%s)", count, dropped, vnc_ws.close_code)
                except WebSocketDisconnect as exc:
                    logger.info("VNC proxy [v->c]: client disconnect code=%s after %d msgs", exc.code, count)
                except Exception as exc:
                    logger.warning("VNC proxy [v->c]: %s: %s (after %d msgs)", type(exc).__name__, exc, count)

            c2v = asyncio.create_task(client_to_vnc(), name="c2v")
            v2c = asyncio.create_task(vnc_to_client(), name="v2c")
            proxy_tasks = [c2v, v2c]
            revocation_task = None
            if access_lease is not None:
                revocation_task = asyncio.create_task(
                    access_lease.revoked.wait(), name="access-revocation"
                )
                proxy_tasks.append(revocation_task)

            done, pending = await asyncio.wait(
                proxy_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            access_revoked = revocation_task is not None and revocation_task in done
            finished = [t.get_name() for t in done]
            still_running = [t.get_name() for t in pending]

            # Check if Xvnc is still alive
            vnc_instance = browser_mgr.vnc._allocated.get(running.display)
            xvnc_alive = vnc_instance and vnc_instance.process and vnc_instance.process.poll() is None
            logger.info(
                "VNC proxy: finished=%s pending=%s xvnc_alive=%s display=:%d for %s",
                finished, still_running, xvnc_alive, running.display, profile_id,
            )

            # Dump Xvnc log on disconnect
            import os
            xvnc_log = f"/tmp/xvnc-{running.display}.log"
            if os.path.exists(xvnc_log):
                with open(xvnc_log) as f:
                    log_content = f.read()
                if log_content.strip():
                    for line in log_content.strip().split("\n")[-20:]:
                        logger.info("Xvnc[:%d] %s", running.display, line)

            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if access_revoked:
                logger.info("VNC proxy: access revoked for %s", profile_id)
                await websocket.close(code=4403, reason="Access revoked")

    except Exception as exc:
        logger.error("VNC proxy connect error for %s: %s: %s", profile_id, type(exc).__name__, exc)
    finally:
        if diagnostics_session_id is not None:
            live_diagnostics.live_diagnostics.end_vnc_session(
                profile_id, diagnostics_session_id
            )
        _unregister_websocket_access(access_lease)
        try:
            await websocket.close()
        except Exception as exc:
            logger.debug("VNC proxy: websocket.close() failed: %s", exc)


# ── CDP WebSocket Proxy ──────────────────────────────────────────────────────
# Simple bidirectional passthrough — CDP is standard JSON over WebSocket,
# no protocol translation needed (unlike VNC which requires RFB filtering).


@app.get("/api/profiles/{profile_id}/cdp")
async def cdp_info(profile_id: str, request: Request):
    """Return CDP connection info. Prevents SPA catch-all from serving index.html."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")
    return {
        "cdp_url": f"/api/profiles/{profile_id}/cdp",
        "usage": "playwright.chromium.connect_over_cdp('http://<host>/api/profiles/"
        + profile_id + "/cdp')",
    }


@app.get("/api/profiles/{profile_id}/cdp/json/version/")
@app.get("/api/profiles/{profile_id}/cdp/json/version")
async def cdp_json_version(profile_id: str, request: Request):
    """Proxy Chrome's /json/version, rewriting WS URLs to go through our proxy."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://127.0.0.1:{running.cdp_port}/json/version", timeout=5
            )
            data = resp.json()
    except Exception as exc:
        logger.error("CDP proxy: failed to reach Chrome CDP for %s: %s", profile_id, exc)
        raise HTTPException(status_code=502, detail="CDP endpoint unreachable")

    # Rewrite webSocketDebuggerUrl to point through our proxy
    host = request.headers.get("host", "localhost:8080")
    ws_scheme = "wss" if _is_https(request) else "ws"
    data["webSocketDebuggerUrl"] = f"{ws_scheme}://{host}/api/profiles/{profile_id}/cdp"
    return data


@app.get("/api/profiles/{profile_id}/cdp/json/list/")
@app.get("/api/profiles/{profile_id}/cdp/json/list")
@app.get("/api/profiles/{profile_id}/cdp/json/")
@app.get("/api/profiles/{profile_id}/cdp/json")
async def cdp_json_list(profile_id: str, request: Request):
    """Proxy Chrome's /json/list, rewriting WS URLs."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://127.0.0.1:{running.cdp_port}/json/list", timeout=5
            )
            data = resp.json()
    except Exception as exc:
        logger.error("CDP proxy: failed to reach Chrome CDP for %s: %s", profile_id, exc)
        raise HTTPException(status_code=502, detail="CDP endpoint unreachable")

    host = request.headers.get("host", "localhost:8080")
    ws_scheme = "wss" if _is_https(request) else "ws"
    for entry in data:
        if "webSocketDebuggerUrl" in entry:
            ws_path = entry["webSocketDebuggerUrl"].split("/devtools/")[-1]
            entry["webSocketDebuggerUrl"] = (
                f"{ws_scheme}://{host}/api/profiles/{profile_id}/cdp/devtools/{ws_path}"
            )
    return data


async def _proxy_cdp_websocket(
    websocket: WebSocket,
    target_url: str,
    label: str,
    access_lease: _WebSocketAccessLease | None = None,
) -> None:
    """Bidirectional WebSocket proxy between a FastAPI client and a CDP target.

    Used by both browser-level and page-level CDP proxy endpoints.
    """
    import websockets

    try:
        async with websockets.connect(
            target_url, max_size=None, ping_interval=None, ping_timeout=None
        ) as cdp_ws:
            logger.info("%s: connected to %s", label, target_url)

            async def client_to_cdp():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"]:
                            await cdp_ws.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await cdp_ws.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception as exc:
                    logger.warning("%s [c->cdp]: %s: %s", label, type(exc).__name__, exc)

            async def cdp_to_client():
                try:
                    async for msg in cdp_ws:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except WebSocketDisconnect:
                    pass
                except Exception as exc:
                    logger.warning("%s [cdp->c]: %s: %s", label, type(exc).__name__, exc)

            c2d = asyncio.create_task(client_to_cdp(), name="c2d")
            d2c = asyncio.create_task(cdp_to_client(), name="d2c")
            proxy_tasks = [c2d, d2c]
            revocation_task = None
            if access_lease is not None:
                revocation_task = asyncio.create_task(
                    access_lease.revoked.wait(), name="access-revocation"
                )
                proxy_tasks.append(revocation_task)
            done, pending = await asyncio.wait(
                proxy_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            access_revoked = revocation_task is not None and revocation_task in done
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if access_revoked:
                logger.info("%s: access revoked", label)
                await websocket.close(code=4403, reason="Access revoked")
            logger.info("%s: disconnected", label)

    except Exception as exc:
        logger.error("%s error: %s", label, exc)
    finally:
        try:
            await websocket.close()
        except Exception as exc:
            logger.debug("%s: websocket.close() failed: %s", label, exc)


@app.websocket("/api/profiles/{profile_id}/cdp")
async def cdp_proxy(websocket: WebSocket, profile_id: str):
    """Proxy WebSocket frames between external tools and Chrome's CDP."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "automate")
    if not access_result:
        return
    _profile, identity = access_result

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    access_lease = _register_websocket_access(identity, profile_id)
    try:
        await websocket.accept()

        # Get browser-level CDP WebSocket URL from Chrome
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{running.cdp_port}/json/version", timeout=5
                )
                ws_url = resp.json()["webSocketDebuggerUrl"]
        except Exception as exc:
            logger.error("CDP proxy: failed to get WS URL for %s: %s", profile_id, exc)
            await websocket.close(code=4005, reason="CDP not available")
            return

        await _proxy_cdp_websocket(
            websocket, ws_url, f"CDP proxy [{profile_id}]", access_lease
        )
    finally:
        _unregister_websocket_access(access_lease)


@app.websocket("/api/profiles/{profile_id}/cdp/devtools/{path:path}")
async def cdp_page_proxy(websocket: WebSocket, profile_id: str, path: str):
    """Proxy page-specific CDP WebSocket connections (e.g. /devtools/page/GUID)."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "automate")
    if not access_result:
        return
    _profile, identity = access_result

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    access_lease = _register_websocket_access(identity, profile_id)
    try:
        await websocket.accept()
        target_url = f"ws://127.0.0.1:{running.cdp_port}/devtools/{path}"
        await _proxy_cdp_websocket(
            websocket, target_url, f"CDP page proxy [{profile_id}]", access_lease
        )
    finally:
        _unregister_websocket_access(access_lease)


# ── Static Frontend ───────────────────────────────────────────────────────────

# Serve React build. Must be AFTER API routes so /api/* isn't caught by the SPA.
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA — all non-API routes return index.html."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        file_path = FRONTEND_DIR / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
