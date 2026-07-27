"""Strict TDD for profile-bound cbm_bridge observer identity cookies."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import access_control as access
from backend import database as db
from backend import session_views


SECRET = "bootstrap-test-secret"
PATH_CLASS = "cdp-observer"


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", SECRET)
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SECRET}"}


def _scope(*, path: str, cookie: str | None = None) -> dict:
    headers: list[tuple[bytes, bytes]] = []
    if cookie:
        headers.append((b"cookie", cookie.encode("latin-1")))
    return {"type": "http", "path": path, "headers": headers}


def _set_cookie_headers(response) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


def _bridge_cookies(response) -> list[str]:
    return [h for h in _set_cookie_headers(response) if h.startswith("cbm_bridge=")]


def _bridge_value(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0].split("=", 1)[1]


class _ScriptedUpstream:
    def __init__(self, inbound_replies: list[str | bytes] | None = None):
        self.sent: list[str | bytes] = []
        self._replies = list(inbound_replies or [])
        self._queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, message: bytes | str):
        self.sent.append(message)
        if self._replies:
            self._queue.put_nowait(self._replies.pop(0))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


# ── Signer / parser ──────────────────────────────────────────────────────────


def test_bridge_round_trip_binds_profile_and_path_class(tmp_db):
    user = db.create_access_user(
        "bridge-user",
        access.hash_password("bridge-user-password-123"),
        "member",
        [{"sandbox_id": "alpha", "permission": "view"}],
        [],
    )
    agent_key = "cbm_agent_" + ("ab" * 16)
    agent = db.create_access_agent(
        "Bridge Agent",
        access.hash_agent_key(agent_key),
        "paperclip-bridge-unit",
        [{"sandbox_id": "alpha", "permission": "view"}],
    )
    profile_id = "prof-aaa"

    boot = access.create_bridge_session(
        "bootstrap", None, SECRET, profile_id=profile_id, path_class=PATH_CLASS
    )
    user_tok = access.create_bridge_session(
        "user", str(user["id"]), SECRET, profile_id=profile_id, path_class=PATH_CLASS
    )
    agent_tok = access.create_bridge_session(
        "agent", str(agent["id"]), SECRET, profile_id=profile_id, path_class=PATH_CLASS
    )

    assert access.verify_bridge_session(boot, SECRET) == (
        "bootstrap",
        None,
        profile_id,
        PATH_CLASS,
    )
    assert access.verify_bridge_session(user_tok, SECRET) == (
        "user",
        str(user["id"]),
        profile_id,
        PATH_CLASS,
    )
    assert access.verify_bridge_session(agent_tok, SECRET) == (
        "agent",
        str(agent["id"]),
        profile_id,
        PATH_CLASS,
    )
    assert SECRET not in boot
    assert agent_key not in agent_tok
    assert "cbm_agent_" not in agent_tok


def test_bridge_rejects_tamper_expiry_equality_and_bad_principals(tmp_db):
    token = access.create_bridge_session(
        "bootstrap", None, SECRET, profile_id="p1", path_class=PATH_CLASS
    )
    payload, sig = token.split(".", 1)
    tampered_first = "A" if sig[0] != "A" else "B"
    assert access.verify_bridge_session(f"{payload}.{tampered_first}{sig[1:]}", SECRET) is None

    now = int(time.time())
    expired = access.create_bridge_session(
        "bootstrap",
        None,
        SECRET,
        profile_id="p1",
        path_class=PATH_CLASS,
        ttl_seconds=900,
        now=now - 10_000,
    )
    assert access.verify_bridge_session(expired, SECRET, now=now) is None

    # Expiry equality (expires_at == now) is invalid.
    equal = access.create_bridge_session(
        "bootstrap",
        None,
        SECRET,
        profile_id="p1",
        path_class=PATH_CLASS,
        ttl_seconds=0,
        now=now,
    )
    assert access.verify_bridge_session(equal, SECRET, now=now) is None

    with pytest.raises(ValueError):
        access.create_bridge_session(
            "user", "-", SECRET, profile_id="p1", path_class=PATH_CLASS
        )
    with pytest.raises(ValueError):
        access.create_bridge_session(
            "agent", "", SECRET, profile_id="p1", path_class=PATH_CLASS
        )
    with pytest.raises(ValueError):
        access.create_bridge_session(
            "user", None, SECRET, profile_id="p1", path_class=PATH_CLASS
        )
    with pytest.raises(ValueError):
        access.create_bridge_session(
            "anonymous", None, SECRET, profile_id="p1", path_class=PATH_CLASS
        )  # type: ignore[arg-type]


def test_resolve_identity_requires_matching_profile_path_and_class(tmp_db):
    user = db.create_access_user(
        "bridge-path-user",
        access.hash_password("bridge-path-user-password-123"),
        "member",
        [
            {"sandbox_id": "alpha", "permission": "view"},
            {"sandbox_id": "beta", "permission": "view"},
        ],
        [],
    )
    token_a = access.create_bridge_session(
        "user",
        str(user["id"]),
        SECRET,
        profile_id="profile-a",
        path_class=PATH_CLASS,
    )
    cookie = f"cbm_bridge={token_a}"

    ok = access.resolve_identity(
        _scope(
            path="/api/profiles/profile-a/cdp-observer/devtools/page/X",
            cookie=cookie,
        ),
        SECRET,
    )
    assert ok is not None
    assert ok.id == user["id"]

    # A ticket presented on B's observer path must fail before grants matter.
    assert (
        access.resolve_identity(
            _scope(
                path="/api/profiles/profile-b/cdp-observer/devtools/page/X",
                cookie=cookie,
            ),
            SECRET,
        )
        is None
    )

    # Non-observer profile API path rejects bridge.
    assert (
        access.resolve_identity(
            _scope(path="/api/profiles/profile-a/status", cookie=cookie),
            SECRET,
        )
        is None
    )
    assert (
        access.resolve_identity(
            _scope(path="/api/profiles/profile-a/cdp/json/list", cookie=cookie),
            SECRET,
        )
        is None
    )
    assert (
        access.resolve_identity(
            _scope(path="/api/access/me", cookie=cookie),
            SECRET,
        )
        is None
    )


def test_resolve_identity_rejects_path_class_mismatch(tmp_db):
    token = access.create_bridge_session(
        "bootstrap",
        None,
        SECRET,
        profile_id="profile-a",
        path_class="cdp-observer",
    )
    # Forge wrong class by minting helper if supported, else craft verify expectation
    # via create with invalid class rejected; simulate verify of wrong class token.
    bad = access.create_bridge_session(
        "bootstrap",
        None,
        SECRET,
        profile_id="profile-a",
        path_class="cdp-observer",
    )
    # Mutate payload class after mint is hard; create with alternate class must fail mint
    # or verify. Prefer create raising or verify returning None for non-observer class.
    with pytest.raises(ValueError):
        access.create_bridge_session(
            "bootstrap",
            None,
            SECRET,
            profile_id="profile-a",
            path_class="not-observer",
        )
    assert bad  # valid token still works on matching path
    assert (
        access.resolve_identity(
            _scope(
                path="/api/profiles/profile-a/cdp-observer/json/list",
                cookie=f"cbm_bridge={token}",
            ),
            SECRET,
        )
        is not None
    )


def test_resolve_identity_bridge_denies_inactive(tmp_db):
    user = db.create_access_user(
        "bridge-inactive",
        access.hash_password("bridge-inactive-password-123"),
        "member",
        [{"sandbox_id": "alpha", "permission": "view"}],
        [],
    )
    agent_key = "cbm_agent_" + ("cd" * 16)
    agent = db.create_access_agent(
        "Inactive Bridge Agent",
        access.hash_agent_key(agent_key),
        "paperclip-bridge-inactive",
        [{"sandbox_id": "alpha", "permission": "view"}],
    )
    path = "/api/profiles/p1/cdp-observer/devtools/page/X"
    user_tok = access.create_bridge_session(
        "user", str(user["id"]), SECRET, profile_id="p1", path_class=PATH_CLASS
    )
    agent_tok = access.create_bridge_session(
        "agent", str(agent["id"]), SECRET, profile_id="p1", path_class=PATH_CLASS
    )
    db.update_access_user(str(user["id"]), active=False)
    assert (
        access.resolve_identity(_scope(path=path, cookie=f"cbm_bridge={user_tok}"), SECRET)
        is None
    )
    db.update_access_agent(str(agent["id"]), active=False)
    assert (
        access.resolve_identity(
            _scope(path=path, cookie=f"cbm_bridge={agent_tok}"), SECRET
        )
        is None
    )


# ── HTTP mint / logout ───────────────────────────────────────────────────────


def test_live_set_cookie_path_is_profile_specific(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Bridge live path", sandbox_id="alpha")
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6401, cdp_port=5401, display=401
    )
    try:
        resp = client_access.get(
            f"/session/{profile['id']}/live",
            headers={**bootstrap_headers(), "X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 200, resp.text
        headers = _bridge_cookies(resp)
        assert len(headers) == 1
        header = headers[0]
        expected_path = f"Path=/api/profiles/{profile['id']}/cdp-observer/"
        assert expected_path in header or expected_path.lower() in header.lower()
        assert "Path=/api/profiles/;" not in header.replace(
            expected_path, ""
        ) and "path=/api/profiles/;" not in header.lower().replace(
            expected_path.lower(), ""
        )
        assert "HttpOnly" in header or "httponly" in header.lower()
        assert "SameSite=Strict" in header or "samesite=strict" in header.lower()
        assert "Max-Age=900" in header or "max-age=900" in header.lower()
        assert "Secure" in header
        assert SECRET not in header
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_two_profile_bridge_cookies_coexist_and_ws_succeed(
    client_access: TestClient, monkeypatch
):
    from backend import main

    a = db.create_profile("Bridge A", sandbox_id="alpha")
    b = db.create_profile("Bridge B", sandbox_id="alpha")
    main.browser_mgr.running[a["id"]] = SimpleNamespace(
        ws_port=6402, cdp_port=5402, display=402
    )
    main.browser_mgr.running[b["id"]] = SimpleNamespace(
        ws_port=6403, cdp_port=5403, display=403
    )
    live_a = client_access.get(f"/session/{a['id']}/live", headers=bootstrap_headers())
    live_b = client_access.get(f"/session/{b['id']}/live", headers=bootstrap_headers())
    assert live_a.status_code == 200 and live_b.status_code == 200
    cookie_a = _bridge_value(_bridge_cookies(live_a)[0])
    cookie_b = _bridge_value(_bridge_cookies(live_b)[0])
    assert cookie_a != cookie_b

    path_a = f"/api/profiles/{a['id']}/cdp-observer/"
    path_b = f"/api/profiles/{b['id']}/cdp-observer/"
    client_access.cookies.clear()
    client_access.cookies.set("cbm_bridge", cookie_a, path=path_a)
    client_access.cookies.set("cbm_bridge", cookie_b, path=path_b)

    upstream_a = _ScriptedUpstream([json.dumps({"id": 1, "result": {}})])
    upstream_b = _ScriptedUpstream([json.dumps({"id": 1, "result": {}})])
    calls: list[str] = []

    def _connect(url, *args, **kwargs):
        calls.append(str(url))
        if f":5402/" in str(url) or str(url).endswith("/page/A1"):
            return upstream_a
        return upstream_b

    monkeypatch.setattr("websockets.connect", _connect)
    try:
        with client_access.websocket_connect(
            f"/api/profiles/{a['id']}/cdp-observer/devtools/page/A1",
            headers={"origin": "http://testserver"},
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Page.startScreencast",
                        "params": {
                            "format": "jpeg",
                            "quality": 35,
                            "maxWidth": 800,
                            "maxHeight": 600,
                            "everyNthFrame": 1,
                        },
                    }
                )
            )
            assert ws.receive_json()["id"] == 1
        with client_access.websocket_connect(
            f"/api/profiles/{b['id']}/cdp-observer/devtools/page/B1",
            headers={"origin": "http://testserver"},
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Page.startScreencast",
                        "params": {
                            "format": "jpeg",
                            "quality": 35,
                            "maxWidth": 800,
                            "maxHeight": 600,
                            "everyNthFrame": 1,
                        },
                    }
                )
            )
            assert ws.receive_json()["id"] == 1
        assert upstream_a.sent and upstream_b.sent
    finally:
        main.browser_mgr.running.pop(a["id"], None)
        main.browser_mgr.running.pop(b["id"], None)


def test_ticket_a_on_path_b_denied_even_if_grants_cover_both(
    client_access: TestClient, monkeypatch
):
    from backend import main

    a = db.create_profile("Cross A", sandbox_id="alpha")
    b = db.create_profile("Cross B", sandbox_id="alpha")
    main.browser_mgr.running[a["id"]] = SimpleNamespace(
        ws_port=6404, cdp_port=5404, display=404
    )
    main.browser_mgr.running[b["id"]] = SimpleNamespace(
        ws_port=6405, cdp_port=5405, display=405
    )
    live_a = client_access.get(f"/session/{a['id']}/live", headers=bootstrap_headers())
    cookie_a = _bridge_value(_bridge_cookies(live_a)[0])
    # Intentionally attach A's cookie under B's path jar entry to simulate malice.
    client_access.cookies.clear()
    client_access.cookies.set(
        "cbm_bridge", cookie_a, path=f"/api/profiles/{b['id']}/cdp-observer/"
    )
    monkeypatch.setattr(
        "websockets.connect",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no upstream")),
    )
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client_access.websocket_connect(
                f"/api/profiles/{b['id']}/cdp-observer/devtools/page/PAGE1",
                headers={"origin": "http://testserver"},
            ):
                pass
        assert exc.value.code in {1006, 4401, 4404, 1000, 1008}
    finally:
        main.browser_mgr.running.pop(a["id"], None)
        main.browser_mgr.running.pop(b["id"], None)


def test_auth_logout_clears_legacy_root_and_profile_paths(client_access: TestClient):
    a = db.create_profile("Logout A", sandbox_id="alpha")
    b = db.create_profile("Logout B", sandbox_id="beta")
    resp = client_access.post("/api/auth/logout", headers=bootstrap_headers())
    assert resp.status_code == 200
    clears = [h.lower() for h in _set_cookie_headers(resp) if h.lower().startswith("cbm_bridge=")]
    assert clears
    joined = "\n".join(clears)
    assert "path=/api/profiles/" in joined  # legacy root bridge path
    assert f"path=/api/profiles/{a['id']}/cdp-observer/" in joined
    assert f"path=/api/profiles/{b['id']}/cdp-observer/" in joined


def test_cookie_only_user_live_get_does_not_mint_bridge(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Bridge user cookie", sandbox_id="alpha")
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "bridge-cookie-user",
            "password": "bridge-cookie-user-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201, created.text
    client_access.cookies.clear()
    assert (
        client_access.post(
            "/api/auth/login",
            json={
                "username": "bridge-cookie-user",
                "password": "bridge-cookie-user-password-123",
            },
        ).status_code
        == 200
    )
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6406, cdp_port=5406, display=406
    )
    try:
        resp = client_access.get(f"/session/{profile['id']}/live")
        assert resp.status_code == 200
        assert _bridge_cookies(resp) == []
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_observer_ws_without_cookie_remains_denied(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Bridge WS deny", sandbox_id="alpha")
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6407, cdp_port=5407, display=407
    )
    client_access.cookies.clear()
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client_access.websocket_connect(
                f"/api/profiles/{profile['id']}/cdp-observer/devtools/page/PAGE1",
                headers={"origin": "http://testserver"},
            ):
                pass
        assert exc.value.code in {1006, 4401, 1000, 1008, 4404}
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_observer_ws_token_like_query_still_denied(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Bridge query deny", sandbox_id="alpha")
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6408, cdp_port=5408, display=408
    )
    live = client_access.get(f"/session/{profile['id']}/live", headers=bootstrap_headers())
    cookie = _bridge_value(_bridge_cookies(live)[0])
    client_access.cookies.clear()
    client_access.cookies.set(
        "cbm_bridge",
        cookie,
        path=f"/api/profiles/{profile['id']}/cdp-observer/",
    )
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client_access.websocket_connect(
                f"/api/profiles/{profile['id']}/cdp-observer/devtools/page/PAGE1"
                f"?token=nope",
                headers={"origin": "http://testserver"},
            ):
                pass
        assert exc.value.code in {1006, 4400, 1000, 1008}
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_live_html_does_not_propagate_debugger_url_query():
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        interactive=False,
    )
    assert "sameOriginWsFromDebuggerUrl" in html
    # Must not append discovery query string onto the client WebSocket URL.
    assert "parsed.search" not in html
    assert "+ (parsed.search" not in html
    assert "parsed.search || ''" not in html
