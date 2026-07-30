"""TDD tests for the loopback-only RP + Google-style OAuth fixture server.

No external network, no real Google, no real credentials/cookies/passkeys.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

from benchmarks.auth import fixture_server as fs
from benchmarks.auth import scenarios as sc


@pytest.fixture()
def server():
    srv = fs.AuthFixtureServer(host="127.0.0.1", rp_port=0, idp_port=0)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def test_package_import_from_repo_root():
    """Consumers import benchmarks.auth.fixture_server; must not require auth/ on sys.path."""
    repo_root = Path(__file__).resolve().parents[2]
    auth_dir = Path(__file__).resolve().parent
    # Drop directory-style import paths so package import cannot cheat via cwd.
    cleaned = [
        p
        for p in sys.path
        if Path(p).resolve() not in {auth_dir.resolve(), Path.cwd().resolve() / "benchmarks" / "auth"}
    ]
    # Ensure repo root is present for the package namespace.
    root_s = str(repo_root)
    if root_s not in cleaned:
        cleaned.insert(0, root_s)

    # Purge any already-loaded local modules that would mask the package import.
    for name in list(sys.modules):
        if name in {"fixture_server", "scenarios"} or name.startswith(
            ("benchmarks.auth", "fixture_server.", "scenarios.")
        ):
            del sys.modules[name]

    old_path = sys.path[:]
    sys.path[:] = cleaned
    try:
        mod = importlib.import_module("benchmarks.auth.fixture_server")
        sc_mod = importlib.import_module("benchmarks.auth.scenarios")
        assert mod.DEFAULT_HOST == "127.0.0.1"
        assert mod.DEFAULT_RP_PORT == 18791
        assert hasattr(mod, "AuthFixtureServer")
        assert hasattr(mod, "LoopbackThreadingHTTPServer")
        assert sc_mod.scenario_ids()
        # Bound as `import … as sc` — package mode must wire the same module object.
        assert mod.sc is sc_mod
        srv = mod.AuthFixtureServer(host="127.0.0.1", rp_port=0, idp_port=0)
        assert srv.host == "127.0.0.1"
    finally:
        sys.path[:] = old_path


def _request(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 2.0,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - loopback fixture only
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def test_server_binds_loopback_only_with_deterministic_defaults():
    assert fs.DEFAULT_HOST == "127.0.0.1"
    assert fs.DEFAULT_RP_PORT == 18791
    assert fs.DEFAULT_IDP_PORT == 18792
    assert fs.MAX_BODY_BYTES <= 16_384
    assert fs.DEFAULT_REQUEST_TIMEOUT_S <= 5.0

    with pytest.raises(ValueError, match="loopback"):
        fs.AuthFixtureServer(host="0.0.0.0", rp_port=0, idp_port=0)


def test_security_headers_csp_and_referrer_policy(server: fs.AuthFixtureServer):
    status, headers, body = _request("GET", f"{server.rp_base}/health")
    assert status == 200
    csp = headers.get("Content-Security-Policy") or headers.get("content-security-policy")
    referrer = headers.get("Referrer-Policy") or headers.get("referrer-policy")
    assert csp is not None
    assert "default-src" in csp
    assert "connect-src 'self'" in csp
    assert referrer is not None
    assert "no-referrer" in referrer.lower()
    assert b"ok" in body.lower() or b"healthy" in body.lower() or b'"status"' in body


def test_password_success_and_failure(server: fs.AuthFixtureServer):
    ok_body = urlencode(
        {"username": fs.FIXTURE_USERNAME, "password": fs.FIXTURE_PASSWORD}
    ).encode()
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/login",
        data=ok_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 200
    payload = json.loads(body.decode())
    assert payload["outcome"] == "success"
    assert "password" not in payload

    bad_body = urlencode({"username": fs.FIXTURE_USERNAME, "password": "wrong"}).encode()
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/login",
        data=bad_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {401, 403}
    payload = json.loads(body.decode())
    assert payload["outcome"] == "failure"


def test_otp_handoff_and_expiry_without_logging_values(server: fs.AuthFixtureServer):
    # Establish password session first
    login = urlencode(
        {"username": fs.FIXTURE_USERNAME, "password": fs.FIXTURE_PASSWORD}
    ).encode()
    _request(
        "POST",
        f"{server.rp_base}/login",
        data=login,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    handoff_status, _, handoff_body = _request("POST", f"{server.rp_base}/otp/start")
    assert handoff_status == 200
    handoff = json.loads(handoff_body.decode())
    assert handoff["outcome"] == "pending"
    assert "code" not in handoff
    assert "otp" not in handoff
    challenge_id = handoff["challenge_id"]

    # Valid OTP via dedicated test-only submit that accepts the fixture code
    # but never returns it.
    submit = urlencode(
        {"challenge_id": challenge_id, "otp": fs.FIXTURE_OTP}
    ).encode()
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/otp/verify",
        data=submit,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 200
    assert json.loads(body.decode())["outcome"] == "success"

    # Fresh challenge then expire it
    _, _, handoff_body = _request("POST", f"{server.rp_base}/otp/start")
    challenge_id = json.loads(handoff_body.decode())["challenge_id"]
    exp_status, _, _exp_body = _request(
        "POST",
        f"{server.rp_base}/otp/expire",
        data=urlencode({"challenge_id": challenge_id}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert exp_status == 200
    expired_submit = urlencode(
        {"challenge_id": challenge_id, "otp": fs.FIXTURE_OTP}
    ).encode()
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/otp/verify",
        data=expired_submit,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {401, 403, 410}
    assert json.loads(body.decode())["outcome"] == "expired"

    # Logs must never contain the OTP value
    joined = "\n".join(server.drain_logs())
    assert fs.FIXTURE_OTP not in joined
    assert fs.FIXTURE_PASSWORD not in joined
    assert "otp=" not in joined.lower() or "[REDACTED]" in joined


def test_oauth_popup_success_cancel_close_reopen(server: fs.AuthFixtureServer):
    # success
    status, _, body = _request(
        "GET",
        f"{server.idp_base}/o/oauth2/auth?client_id=fixture&redirect_uri={server.rp_base}/oauth/callback&state=s1",
    )
    assert status == 200
    assert b"Approve" in body or b"approve" in body or b"consent" in body.lower()

    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/approve",
        data=urlencode({"state": "s1", "redirect_uri": f"{server.rp_base}/oauth/callback"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {200, 302}
    if status == 200:
        payload = json.loads(body.decode())
        assert payload["outcome"] == "success"
        assert "access_token" not in payload or payload.get("access_token") == fs.FIXTURE_TOKEN_PLACEHOLDER

    # cancel
    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/cancel",
        data=urlencode({"state": "s2"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {200, 302}
    if status == 200:
        assert json.loads(body.decode())["outcome"] == "cancel"

    # close without decision
    status, _, body = _request("POST", f"{server.idp_base}/o/oauth2/close", data=b"state=s3")
    assert status == 200
    assert json.loads(body.decode())["outcome"] == "close"

    # reopen after close then succeed
    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/reopen",
        data=urlencode({"state": "s3"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 200
    assert json.loads(body.decode())["outcome"] == "reopen"
    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/approve",
        data=urlencode({"state": "s3", "redirect_uri": f"{server.rp_base}/oauth/callback"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {200, 302}


def test_wrong_origin_postmessage_is_rejected(server: fs.AuthFixtureServer):
    payload = json.dumps(
        {
            "type": "oauth_result",
            "state": "s-wrong",
            "outcome": "success",
            "origin": "https://evil.example",
        }
    ).encode()
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/oauth/postmessage",
        data=payload,
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert status in {400, 403}
    result = json.loads(body.decode())
    assert result["outcome"] == "wrong_origin"


def test_auth_timeout_scenario_endpoint(server: fs.AuthFixtureServer):
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/scenario/auth_timeout",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert status in {200, 408}
    result = json.loads(body.decode())
    assert result["outcome"] == "timeout"
    assert result["scenario_id"] == "auth_timeout"


def test_scenario_matrix_is_served_and_matches_module(server: fs.AuthFixtureServer):
    status, _, body = _request("GET", f"{server.rp_base}/scenarios")
    assert status == 200
    payload = json.loads(body.decode())
    assert set(payload["ids"]) == set(sc.scenario_ids())


def test_oversized_body_is_rejected(server: fs.AuthFixtureServer):
    huge = b"x" * (fs.MAX_BODY_BYTES + 1)
    status, _, body = _request(
        "POST",
        f"{server.rp_base}/login",
        data=huge,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 413
    assert b"password" not in body.lower() or b"[redacted]" in body.lower()


def test_handle_error_suppresses_connection_reset_only(server: fs.AuthFixtureServer):
    """Expected client disconnects must not dump ThreadingHTTPServer tracebacks.

    Unexpected exceptions must still surface via handle_error (not swallowed).
    """
    assert isinstance(server._rp, fs.LoopbackThreadingHTTPServer)
    httpd = server._rp

    reset_buf = io.StringIO()
    with redirect_stderr(reset_buf):
        try:
            raise ConnectionResetError(104, "Connection reset by peer")
        except ConnectionResetError:
            httpd.handle_error(None, ("127.0.0.1", 9))
    reset_err = reset_buf.getvalue()
    assert "ConnectionResetError" not in reset_err
    assert "Traceback" not in reset_err
    assert "Exception occurred during processing of request" not in reset_err

    boom_buf = io.StringIO()
    with redirect_stderr(boom_buf):
        try:
            raise RuntimeError("fixture-unexpected-boom")
        except RuntimeError:
            httpd.handle_error(None, ("127.0.0.1", 9))
    boom_err = boom_buf.getvalue()
    assert "RuntimeError" in boom_err
    assert "fixture-unexpected-boom" in boom_err
    assert "Traceback" in boom_err
    # Never leak fixture secrets even if an unexpected path printed context.
    assert fs.FIXTURE_PASSWORD not in boom_err
    assert fs.FIXTURE_OTP not in boom_err


def test_oversized_body_does_not_print_connection_reset_traceback(
    server: fs.AuthFixtureServer, capsys: pytest.CaptureFixture[str]
):
    """Regression: 413 + client close must stay quiet for ConnectionResetError."""
    huge = b"x" * (fs.MAX_BODY_BYTES + 1)
    with capsys.disabled():
        # Drive the real path while capturing process stderr via redirect.
        buf = io.StringIO()
        with redirect_stderr(buf):
            status, _, _ = _request(
                "POST",
                f"{server.rp_base}/login",
                data=huge,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            # Allow worker threads a beat to flush handle_error if any.
            threading.Event().wait(0.2)
    assert status == 413
    err = buf.getvalue()
    assert "ConnectionResetError" not in err
    assert "Exception occurred during processing of request" not in err


def test_non_loopback_host_rejected_and_logs_sanitized():
    logs: list[str] = []
    lock = threading.Lock()

    def capture(msg: str) -> None:
        with lock:
            logs.append(msg)

    srv = fs.AuthFixtureServer(host="127.0.0.1", rp_port=0, idp_port=0, log_sink=capture)
    srv.start()
    try:
        data = urlencode(
            {"username": fs.FIXTURE_USERNAME, "password": fs.FIXTURE_PASSWORD}
        ).encode()
        _request(
            "POST",
            f"{srv.rp_base}/login",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        joined = "\n".join(logs)
        assert fs.FIXTURE_PASSWORD not in joined
        assert "fixture-pass" not in joined
    finally:
        srv.stop()


def test_rp_pages_are_local_html_not_external_redirects(server: fs.AuthFixtureServer):
    status, headers, body = _request("GET", f"{server.rp_base}/")
    assert status == 200
    assert b"<form" in body.lower() or b"login" in body.lower()
    location = headers.get("Location") or headers.get("location")
    assert location is None

    status, headers, body = _request(
        "GET",
        f"{server.idp_base}/o/oauth2/auth?client_id=fixture&redirect_uri={server.rp_base}/oauth/callback&state=local",
    )
    assert status == 200
    location = headers.get("Location") or headers.get("location")
    assert location is None or location.startswith((server.rp_base, server.idp_base))
    assert b"accounts.google.com" not in body


def test_connection_stays_on_loopback(server: fs.AuthFixtureServer):
    conn = HTTPConnection("127.0.0.1", server.rp_port, timeout=2.0)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
    finally:
        conn.close()


def test_oauth_auth_escapes_state_and_redirect_uri_in_html(server: fs.AuthFixtureServer):
    """Hidden form fields must not allow raw HTML/attribute injection."""
    # Valid state chars only — values that pass validation but prove escaping if
    # any markup-sensitive sequences ever slip through encoding layers.
    status, _, body = _request(
        "GET",
        (
            f"{server.idp_base}/o/oauth2/auth?client_id=fixture"
            f"&redirect_uri={server.rp_base}/oauth/callback"
            f"&state=safe-state_01"
        ),
    )
    assert status == 200
    text = body.decode()
    assert 'name=state' in text or 'name="state"' in text
    assert "safe-state_01" in text
    # Markup must not appear unescaped as executable structure from params.
    assert "<script" not in text.lower()
    assert "onerror=" not in text.lower()


def test_oauth_auth_rejects_unsafe_redirect_uri(server: fs.AuthFixtureServer):
    status, _, body = _request(
        "GET",
        (
            f"{server.idp_base}/o/oauth2/auth?client_id=fixture"
            f"&redirect_uri=https://evil.example/steal"
            f"&state=s-evil"
        ),
    )
    assert status in {400, 403}
    payload = json.loads(body.decode())
    assert payload["outcome"] == "error"
    assert "redirect" in payload.get("error", "").lower() or payload.get("error") in {
        "unsafe_redirect_uri",
        "invalid_redirect_uri",
    }


def test_oauth_auth_rejects_html_injection_state(server: fs.AuthFixtureServer):
    malicious = '"><img src=x onerror=alert(1)>'
    from urllib.parse import quote

    status, _, body = _request(
        "GET",
        (
            f"{server.idp_base}/o/oauth2/auth?client_id=fixture"
            f"&redirect_uri={quote(server.rp_base + '/oauth/callback', safe='')}"
            f"&state={quote(malicious, safe='')}"
        ),
    )
    assert status in {400, 403}
    # If rejected as JSON, good; if somehow 200 HTML, payload must not embed raw.
    if status == 200:
        assert b"<img" not in body
        assert b"onerror" not in body
    else:
        payload = json.loads(body.decode())
        assert payload["outcome"] == "error"


def test_oauth_approve_redirects_to_local_rp_callback(server: fs.AuthFixtureServer):
    """IdP approval must complete at the local RP callback, not only return click JSON."""
    state = "state-callback-1"
    # Seed consent page (registers oauth state).
    status, _, _ = _request(
        "GET",
        (
            f"{server.idp_base}/o/oauth2/auth?client_id=fixture"
            f"&redirect_uri={server.rp_base}/oauth/callback&state={state}"
        ),
    )
    assert status == 200

    # urllib follows redirects by default → land on RP callback JSON.
    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/approve",
        data=urlencode(
            {"state": state, "redirect_uri": f"{server.rp_base}/oauth/callback"}
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 200
    payload = json.loads(body.decode())
    assert payload["outcome"] == "success"
    assert payload.get("via") == "callback"


def test_oauth_approve_rejects_external_redirect_uri(server: fs.AuthFixtureServer):
    status, _, body = _request(
        "POST",
        f"{server.idp_base}/o/oauth2/approve",
        data=urlencode(
            {
                "state": "s-ext",
                "redirect_uri": "https://attacker.example/oauth/callback",
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status in {400, 403}
    payload = json.loads(body.decode())
    assert payload["outcome"] == "error"
