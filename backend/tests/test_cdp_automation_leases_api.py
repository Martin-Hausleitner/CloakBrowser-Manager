"""API tests for direct automation leases and lease-gated CDP discovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import database as db


def _receive_websocket_message(session, timeout: float = 2.0):
    async def receive_with_timeout():
        with anyio.fail_after(timeout):
            return await session._send_rx.receive()

    return session.portal.call(receive_with_timeout)


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def _create_agent(
    client: TestClient,
    *,
    name: str,
    sandbox_id: str = "alpha",
    permission: str = "automate",
) -> dict:
    created = client.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": name,
            "paperclip_agent_id": f"paperclip-{name.lower().replace(' ', '-')}",
            "grants": [{"sandbox_id": sandbox_id, "permission": permission}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _agent_headers(agent: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {agent['api_key']}"}


def _lease_headers(agent: dict, token: str) -> dict[str, str]:
    headers = _agent_headers(agent)
    headers["X-CBM-Automation-Lease"] = token
    return headers


def _acquire_lease(client: TestClient, profile_id: str, agent: dict) -> dict:
    resp = client.post(
        f"/api/profiles/{profile_id}/automation-leases",
        headers=_agent_headers(agent),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lease_id"]
    assert body["token"].startswith("cbm_lease_")
    assert body["heartbeat_interval_seconds"] == 15
    assert body["expires_at"]
    return body


def test_acquire_requires_automate_not_operate_and_returns_token_once(
    client_access: TestClient,
):
    profile = db.create_profile("Lease API", sandbox_id="alpha")
    viewer = _create_agent(
        client_access, name="Viewer only", permission="view"
    )
    denied = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases",
        headers=_agent_headers(viewer),
    )
    assert denied.status_code == 404

    operator = _create_agent(
        client_access, name="Operate only", permission="operate"
    )
    operate_denied = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases",
        headers=_agent_headers(operator),
    )
    assert operate_denied.status_code == 404

    agent = _create_agent(client_access, name="Automator")
    body = _acquire_lease(client_access, profile["id"], agent)
    token = body["token"]

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT token_digest FROM automation_leases WHERE id = ?",
            (body["lease_id"],),
        ).fetchone()
        audit = conn.execute(
            "SELECT action, outcome, profile_id FROM access_audit_events "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    assert row["token_digest"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    for event in audit:
        blob = f"{event['action']} {event['outcome']} {event['profile_id'] or ''}"
        assert token not in blob
        assert "cbm_lease_" not in blob


def test_second_acquire_returns_409_automation_busy(client_access: TestClient):
    profile = db.create_profile("Busy lease", sandbox_id="alpha")
    agent_a = _create_agent(client_access, name="Busy A")
    agent_b = _create_agent(client_access, name="Busy B")
    _acquire_lease(client_access, profile["id"], agent_a)
    busy = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases",
        headers=_agent_headers(agent_b),
    )
    assert busy.status_code == 409
    assert busy.json()["detail"] == "automation_busy"
    assert "cbm_lease_" not in busy.text


def test_heartbeat_and_release_require_header_and_same_actor(client_access: TestClient):
    profile = db.create_profile("HB lease", sandbox_id="alpha")
    agent = _create_agent(client_access, name="HB agent")
    other = _create_agent(client_access, name="Other HB")
    lease = _acquire_lease(client_access, profile["id"], agent)

    missing = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}/heartbeat",
        headers=_agent_headers(agent),
    )
    assert missing.status_code in {401, 403, 404}

    query = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}/heartbeat"
        f"?token={lease['token']}",
        headers=_agent_headers(agent),
    )
    assert query.status_code == 400

    body_token = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}/heartbeat",
        headers=_agent_headers(agent),
        json={"token": lease["token"]},
    )
    assert body_token.status_code in {400, 422}

    cross = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}/heartbeat",
        headers=_lease_headers(other, lease["token"]),
    )
    assert cross.status_code in {401, 403, 404}

    ok = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}/heartbeat",
        headers=_lease_headers(agent, lease["token"]),
    )
    assert ok.status_code == 200
    assert ok.json()["expires_at"]
    assert lease["token"] not in ok.text

    released = client_access.delete(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}",
        headers=_lease_headers(agent, lease["token"]),
    )
    assert released.status_code == 204

    again = client_access.delete(
        f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}",
        headers=_lease_headers(agent, lease["token"]),
    )
    assert again.status_code == 204


def test_cdp_query_token_is_rejected(client_access: TestClient, monkeypatch):
    from backend import main

    profile = db.create_profile("Query token", sandbox_id="alpha")
    agent = _create_agent(client_access, name="Query agent")
    lease = _acquire_lease(client_access, profile["id"], agent)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6100, cdp_port=5100, display=100
    )

    reached = {"http": False}

    async def fail_get(*_args, **_kwargs):
        reached["http"] = True
        raise AssertionError("query-token request reached upstream CDP")

    try:
        with patch("httpx.AsyncClient.get", new=fail_get):
            for path in (
                f"/api/profiles/{profile['id']}/cdp/json/version?token=secret",
                f"/api/profiles/{profile['id']}/cdp/json/list?lease={lease['token']}",
                f"/api/profiles/{profile['id']}/cdp/json/version?automation_lease=x",
                f"/api/profiles/{profile['id']}/automation-leases?token=secret",
            ):
                resp = client_access.get(path, headers=_lease_headers(agent, lease["token"]))
                if path.endswith("/automation-leases?token=secret"):
                    # No public GET listing; token-like query must never succeed.
                    assert resp.status_code in {400, 404, 405}
                else:
                    assert resp.status_code == 400, path
        assert reached["http"] is False
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_cdp_discovery_requires_lease_and_rewrites_manager_urls(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Discovery lease", sandbox_id="alpha")
    agent = _create_agent(client_access, name="Discovery agent")
    other = _create_agent(client_access, name="Other discovery")
    lease = _acquire_lease(client_access, profile["id"], agent)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6100, cdp_port=5100, display=100
    )

    chrome_version = MagicMock()
    chrome_version.json.return_value = {
        "webSocketDebuggerUrl": "ws://127.0.0.1:5100/devtools/browser/abc",
        "Browser": "Chrome/test",
    }
    chrome_list = MagicMock()
    chrome_list.json.return_value = [
        {
            "id": "page1",
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5100/devtools/page/DEADBEEF",
        }
    ]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_get(url, *args, **kwargs):
        if url.endswith("/json/version"):
            return chrome_version
        return chrome_list

    mock_client.get = AsyncMock(side_effect=fake_get)

    try:
        missing = client_access.get(
            f"/api/profiles/{profile['id']}/cdp/json/version",
            headers=_agent_headers(agent),
        )
        assert missing.status_code in {401, 403, 404}

        wrong_actor = client_access.get(
            f"/api/profiles/{profile['id']}/cdp/json/version",
            headers=_lease_headers(other, lease["token"]),
        )
        assert wrong_actor.status_code in {401, 403, 404}

        with patch("httpx.AsyncClient", return_value=mock_client):
            version = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/version",
                headers=_lease_headers(agent, lease["token"]),
            )
            listing = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/list",
                headers=_lease_headers(agent, lease["token"]),
            )

        assert version.status_code == 200
        assert listing.status_code == 200
        version_url = version.json()["webSocketDebuggerUrl"]
        list_url = listing.json()[0]["webSocketDebuggerUrl"]
        assert version_url == f"ws://testserver/api/profiles/{profile['id']}/cdp"
        assert list_url == (
            f"ws://testserver/api/profiles/{profile['id']}/cdp/devtools/page/DEADBEEF"
        )
        assert "5100" not in version.text
        assert "5100" not in listing.text
        assert lease["token"] not in version.text
        assert lease["token"] not in listing.text
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_cross_sandbox_lease_and_cdp_remain_indistinguishable_404(
    client_access: TestClient, monkeypatch
):
    from backend import main

    alpha = db.create_profile("Alpha lease", sandbox_id="alpha")
    beta = db.create_profile("Beta lease", sandbox_id="beta")
    agent = _create_agent(client_access, name="Alpha only", sandbox_id="alpha")
    assert (
        client_access.post(
            f"/api/profiles/{beta['id']}/automation-leases",
            headers=_agent_headers(agent),
        ).status_code
        == 404
    )
    lease = _acquire_lease(client_access, alpha["id"], agent)
    main.browser_mgr.running[beta["id"]] = SimpleNamespace(
        ws_port=6101, cdp_port=5101, display=101
    )
    reached = {"up": False}

    async def fail_if_upstream(*_args, **_kwargs):
        reached["up"] = True
        raise AssertionError("cross-sandbox CDP reached upstream")

    try:
        with patch("httpx.AsyncClient.get", new=fail_if_upstream):
            resp = client_access.get(
                f"/api/profiles/{beta['id']}/cdp/json/version",
                headers=_lease_headers(agent, lease["token"]),
            )
        assert resp.status_code == 404
        assert reached["up"] is False
    finally:
        main.browser_mgr.running.pop(beta["id"], None)


def test_direct_ws_without_or_wrong_lease_closes_before_upstream(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("WS lease", sandbox_id="alpha")
    agent = _create_agent(client_access, name="WS agent")
    other = _create_agent(client_access, name="WS other")
    lease = _acquire_lease(client_access, profile["id"], agent)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6102, cdp_port=5102, display=102
    )
    reached = {"up": False}

    async def fail_connect(*_args, **_kwargs):
        reached["up"] = True
        raise AssertionError("unauthorized CDP websocket reached upstream")

    monkeypatch.setattr("websockets.connect", fail_connect)

    try:
        for headers in (
            _agent_headers(agent),
            _lease_headers(other, lease["token"]),
            {**_agent_headers(agent), "X-CBM-Automation-Lease": "cbm_lease_" + ("ab" * 32)},
        ):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client_access.websocket_connect(
                    f"/api/profiles/{profile['id']}/cdp",
                    headers=headers,
                ):
                    pass
            assert exc.value.code in {4400, 4403, 4404}
        assert reached["up"] is False

        with pytest.raises(WebSocketDisconnect) as exc:
            with client_access.websocket_connect(
                f"/api/profiles/{profile['id']}/cdp?token={lease['token']}",
                headers=_lease_headers(agent, lease["token"]),
            ):
                pass
        assert exc.value.code in {4400, 4403}
        assert reached["up"] is False
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


class _BlockingWebSocketUpstream:
    def __init__(self):
        self.close_code = None
        self.sent: list[bytes | str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.close_code = 1000
        return False

    async def send(self, message: bytes | str):
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        import asyncio

        await asyncio.Event().wait()
        raise StopAsyncIteration


def _receive_websocket_message(session, timeout: float = 2.0):
    import anyio

    async def receive_with_timeout():
        with anyio.fail_after(timeout):
            return await session._send_rx.receive()

    return session.portal.call(receive_with_timeout)


def test_release_closes_active_direct_cdp_socket(client_access: TestClient, monkeypatch):
    from backend import main

    profile = db.create_profile("Revoke WS", sandbox_id="alpha")
    agent = _create_agent(client_access, name="Revoke agent")
    lease = _acquire_lease(client_access, profile["id"], agent)
    upstream = _BlockingWebSocketUpstream()
    monkeypatch.setattr("websockets.connect", lambda *_a, **_k: upstream)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6103, cdp_port=5103, display=103
    )

    try:
        with client_access.websocket_connect(
            f"/api/profiles/{profile['id']}/cdp/devtools/page/TARGET",
            headers=_lease_headers(agent, lease["token"]),
        ) as websocket:
            released = client_access.delete(
                f"/api/profiles/{profile['id']}/automation-leases/{lease['lease_id']}",
                headers=_lease_headers(agent, lease["token"]),
            )
            assert released.status_code == 204
            message = _receive_websocket_message(websocket)
            assert message["type"] == "websocket.close"
            assert message["code"] in {4400, 4403}
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_expiry_closes_active_direct_cdp_socket(client_access: TestClient, monkeypatch):
    from datetime import timedelta

    from backend import automation_leases as leases
    from backend import main

    profile = db.create_profile("Expire WS", sandbox_id="alpha")
    agent = _create_agent(client_access, name="Expire agent")
    lease = _acquire_lease(client_access, profile["id"], agent)
    upstream = _BlockingWebSocketUpstream()
    monkeypatch.setattr("websockets.connect", lambda *_a, **_k: upstream)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6104, cdp_port=5104, display=104
    )

    svc = main.automation_lease_service
    base = svc._clock()

    try:
        with client_access.websocket_connect(
            f"/api/profiles/{profile['id']}/cdp/devtools/page/EXPIRE",
            headers=_lease_headers(agent, lease["token"]),
        ) as websocket:
            svc._clock = lambda: base + timedelta(seconds=leases.LEASE_TTL_SECONDS + 1)
            expired = svc.expire_stale()
            assert any(item[0] == lease["lease_id"] for item in expired)
            main.close_direct_cdp_sockets_for_leases(expired)
            message = _receive_websocket_message(websocket, timeout=3.0)
            assert message["type"] == "websocket.close"
            assert message["code"] in {4400, 4403}
    finally:
        svc._clock = lambda: base
        main.browser_mgr.running.pop(profile["id"], None)


def test_open_links_advertise_cdp_path_without_token(client_access: TestClient):
    profile = db.create_profile("Open links lease", sandbox_id="alpha")
    agent = _create_agent(
        client_access,
        name="Open links agent",
        permission="automate",
    )
    # automate alone cannot launch; create running via manager stub not required for open-links cdp path when stopped.
    links = client_access.get(
        f"/api/profiles/{profile['id']}/open-links",
        headers=_agent_headers(agent),
    )
    assert links.status_code == 200
    body = links.json()
    payload = json.dumps(body)
    assert "cbm_lease_" not in payload
    # May be null when stopped; when present must be Manager path only.
    if body.get("cdp_url"):
        assert body["cdp_url"].endswith(f"/api/profiles/{profile['id']}/cdp")


@pytest.mark.parametrize(
    "revoke_action",
    [
        pytest.param("rotate-key", id="key-rotation"),
        pytest.param("disable", id="principal-disabled"),
        pytest.param("grants-cleared", id="grant-removed"),
        pytest.param("delete", id="principal-deleted"),
    ],
)
def test_access_revocation_retires_automation_lease_and_blocks_reconnect(
    client_access: TestClient, monkeypatch, revoke_action: str
):
    from backend import main

    profile = db.create_profile("Lease revoke profile", sandbox_id="alpha")
    agent = _create_agent(client_access, name=f"Revoke {revoke_action}")
    other = _create_agent(client_access, name=f"Successor {revoke_action}")
    lease = _acquire_lease(client_access, profile["id"], agent)
    old_token = lease["token"]
    old_headers = _lease_headers(agent, old_token)

    class _Upstream:
        def __init__(self):
            self.close_code = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            self.close_code = 1000
            return False

        async def send(self, _message):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()
            raise StopAsyncIteration

    upstream = _Upstream()
    monkeypatch.setattr("websockets.connect", lambda *_a, **_k: upstream)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6110, cdp_port=5110, display=110
    )

    try:
        with client_access.websocket_connect(
            f"/api/profiles/{profile['id']}/cdp/devtools/page/REVOKEME",
            headers=old_headers,
        ) as websocket:
            if revoke_action == "rotate-key":
                rotated = client_access.post(
                    f"/api/access/agents/{agent['id']}/rotate-key",
                    headers=bootstrap_headers(),
                )
                assert rotated.status_code == 200
                new_key = rotated.json()["api_key"]
            elif revoke_action == "disable":
                assert (
                    client_access.put(
                        f"/api/access/agents/{agent['id']}",
                        headers=bootstrap_headers(),
                        json={"active": False},
                    ).status_code
                    == 200
                )
                new_key = None
            elif revoke_action == "grants-cleared":
                assert (
                    client_access.put(
                        f"/api/access/agents/{agent['id']}",
                        headers=bootstrap_headers(),
                        json={"grants": []},
                    ).status_code
                    == 200
                )
                new_key = agent["api_key"]
            else:
                assert (
                    client_access.delete(
                        f"/api/access/agents/{agent['id']}",
                        headers=bootstrap_headers(),
                    ).status_code
                    == 204
                )
                new_key = None

            message = _receive_websocket_message(websocket)
            assert message == {
                "type": "websocket.close",
                "code": 4403,
                "reason": "Access revoked",
            }

        # Old lease token must no longer authorize CDP, and must not leak.
        discovery = client_access.get(
            f"/api/profiles/{profile['id']}/cdp/json/version",
            headers=old_headers,
        )
        assert discovery.status_code in {401, 403, 404}
        assert old_token not in discovery.text

        if new_key:
            still_blocked = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/version",
                headers={
                    "Authorization": f"Bearer {new_key}",
                    "X-CBM-Automation-Lease": old_token,
                },
            )
            assert still_blocked.status_code in {401, 403, 404}
            assert old_token not in still_blocked.text
            with pytest.raises(WebSocketDisconnect):
                with client_access.websocket_connect(
                    f"/api/profiles/{profile['id']}/cdp",
                    headers={
                        "Authorization": f"Bearer {new_key}",
                        "X-CBM-Automation-Lease": old_token,
                    },
                ):
                    pass

        # Profile is no longer busy for a different automate actor.
        successor = _acquire_lease(client_access, profile["id"], other)
        assert successor["lease_id"] != lease["lease_id"]
        assert successor["token"] != old_token
        assert old_token not in json.dumps(successor)
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_cdp_discovery_strips_devtools_and_upstream_url_fields(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Sanitize discovery", sandbox_id="alpha")
    agent = _create_agent(client_access, name="Sanitize discovery agent")
    lease = _acquire_lease(client_access, profile["id"], agent)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6111, cdp_port=5111, display=111
    )

    chrome_version = MagicMock()
    chrome_version.json.return_value = {
        "Browser": "Chrome/test",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": "ws://127.0.0.1:5111/devtools/browser/abc",
        "devtoolsFrontendUrl": "http://127.0.0.1:5111/devtools/inspector.html?ws=127.0.0.1:5111/devtools/browser/abc",
        "wsUrl": "ws://127.0.0.1:5111/devtools/browser/abc",
        "suspiciousUrl": "http://127.0.0.1:5111/secret",
    }
    chrome_list = MagicMock()
    chrome_list.json.return_value = [
        {
            "id": "page1",
            "type": "page",
            "title": "Safe title",
            "url": "https://example.com/",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5111/devtools/page/DEADBEEF",
            "devtoolsFrontendUrl": (
                "https://chrome-devtools-frontend.appspot.com/serve_rev/@hash/"
                "inspector.html?ws=127.0.0.1:5111/devtools/page/DEADBEEF"
            ),
            "faviconUrl": "https://example.com/favicon.ico",
            "leakPort": "http://10.0.0.9:5111/json",
        }
    ]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_get(url, *args, **kwargs):
        if url.endswith("/json/version"):
            return chrome_version
        return chrome_list

    mock_client.get = AsyncMock(side_effect=fake_get)

    try:
        with patch("httpx.AsyncClient", return_value=mock_client):
            version = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/version",
                headers=_lease_headers(agent, lease["token"]),
            )
            listing = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/list",
                headers=_lease_headers(agent, lease["token"]),
            )
        assert version.status_code == 200
        assert listing.status_code == 200
        version_body = version.json()
        list_body = listing.json()
        assert version_body["Browser"] == "Chrome/test"
        assert version_body["Protocol-Version"] == "1.3"
        assert version_body["webSocketDebuggerUrl"] == (
            f"ws://testserver/api/profiles/{profile['id']}/cdp"
        )
        assert "devtoolsFrontendUrl" not in version_body
        assert "wsUrl" not in version_body
        assert "suspiciousUrl" not in version_body
        assert list_body[0]["title"] == "Safe title"
        assert list_body[0]["url"] == "https://example.com/"
        assert list_body[0]["faviconUrl"] == "https://example.com/favicon.ico"
        assert list_body[0]["webSocketDebuggerUrl"] == (
            f"ws://testserver/api/profiles/{profile['id']}/cdp/devtools/page/DEADBEEF"
        )
        assert "devtoolsFrontendUrl" not in list_body[0]
        assert "leakPort" not in list_body[0]
        for text in (version.text, listing.text):
            assert "5111" not in text
            assert "127.0.0.1" not in text
            assert "devtoolsFrontendUrl" not in text
            assert lease["token"] not in text
    finally:
        main.browser_mgr.running.pop(profile["id"], None)
