"""Observer CDP split: view-only screencast gateway without automation lease."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import database as db


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


def _create_user(
    client: TestClient,
    *,
    username: str,
    password: str,
    sandbox_id: str = "alpha",
    permission: str = "view",
) -> None:
    created = client.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": username,
            "password": password,
            "grants": [{"sandbox_id": sandbox_id, "permission": permission}],
        },
    )
    assert created.status_code == 201, created.text
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        ).status_code
        == 200
    )


def _receive_websocket_message(session, timeout: float = 2.0):
    async def receive_with_timeout():
        with anyio.fail_after(timeout):
            return await session._send_rx.receive()

    return session.portal.call(receive_with_timeout)


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
            # After the first command response, also emit any queued events.
            while self._replies and not str(self._replies[0]).startswith('{"id"'):
                self._queue.put_nowait(self._replies.pop(0))

    def push(self, message: str | bytes):
        self._queue.put_nowait(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_observer_validators_allow_only_screencast_commands():
    from backend import cdp_gateway

    start = cdp_gateway.validate_observer_client_message(
        json.dumps(
            {
                "id": 1,
                "method": "Page.startScreencast",
                "params": {
                    "format": "jpeg",
                    "quality": 40,
                    "maxWidth": 1280,
                    "maxHeight": 720,
                    "everyNthFrame": 1,
                },
            }
        )
    )
    assert start["method"] == "Page.startScreencast"

    ack = cdp_gateway.validate_observer_client_message(
        json.dumps({"id": 2, "method": "Page.screencastFrameAck", "params": {"sessionId": 3}})
    )
    assert ack["method"] == "Page.screencastFrameAck"

    stop = cdp_gateway.validate_observer_client_message(
        json.dumps({"id": 3, "method": "Page.stopScreencast", "params": {}})
    )
    assert stop["method"] == "Page.stopScreencast"

    for payload in (
        {"id": 9, "method": "Runtime.evaluate", "params": {"expression": "1"}},
        {"id": 9, "method": "Target.createTarget", "params": {"url": "about:blank"}},
        {"id": 9, "method": "Target.attachToTarget", "params": {"targetId": "x"}},
        {"id": 9, "method": "Target.getTargets", "params": {}},
        {"id": 9, "method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed"}},
        {"id": 9, "method": "Input.dispatchKeyEvent", "params": {"type": "keyDown"}},
        {"id": 9, "method": "Browser.getVersion", "params": {}},
        {"id": 9, "method": "Network.enable", "params": {}},
        {"id": 9, "method": "DOM.getDocument", "params": {}},
        {"id": 9, "method": "Storage.getCookies", "params": {}},
        {"id": 9, "method": "Fetch.enable", "params": {}},
        {"id": 9, "method": "Page.navigate", "params": {"url": "https://example.com"}},
        {"id": 9, "method": "Page.startScreencast", "params": {"format": "jpeg", "evil": 1}},
        {"id": 9, "method": "Page.startScreencast", "params": {"format": "webp"}},
        {"id": 9, "method": "Page.startScreencast", "params": {"format": "jpeg", "quality": 999}},
        {"id": "bad", "method": "Page.stopScreencast", "params": {}},
        {"method": "Page.stopScreencast", "params": {}},
    ):
        with pytest.raises(cdp_gateway.ObserverFrameRejected):
            cdp_gateway.validate_observer_client_message(json.dumps(payload))

    with pytest.raises(cdp_gateway.ObserverFrameRejected):
        cdp_gateway.validate_observer_client_message("{" * 200_000)

    with pytest.raises(cdp_gateway.ObserverFrameRejected):
        cdp_gateway.validate_observer_client_message(b"\xff\xfe not json")


def test_sanitize_discovery_allowlists_safe_fields_only():
    from backend import cdp_gateway

    version = cdp_gateway.sanitize_cdp_version_discovery(
        {
            "Browser": "Chrome/test",
            "Protocol-Version": "1.3",
            "User-Agent": "UA",
            "V8-Version": "1",
            "WebKit-Version": "2",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x",
            "devtoolsFrontendUrl": "http://127.0.0.1:9222/devtools/inspector.html",
            "extraLeak": "ws://127.0.0.1:9222/json",
        },
        manager_ws_url="ws://manager/api/profiles/p1/cdp",
    )
    assert version == {
        "Browser": "Chrome/test",
        "Protocol-Version": "1.3",
        "User-Agent": "UA",
        "V8-Version": "1",
        "WebKit-Version": "2",
        "webSocketDebuggerUrl": "ws://manager/api/profiles/p1/cdp",
    }

    listing = cdp_gateway.sanitize_cdp_list_discovery(
        [
            {
                "id": "page1",
                "type": "page",
                "title": "T",
                "url": "https://example.com/",
                "description": "",
                "faviconUrl": "https://example.com/f.ico",
                "parentId": "browser",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ABC",
                "devtoolsFrontendUrl": "http://127.0.0.1:9222/devtools/inspector.html?ws=127.0.0.1:9222/devtools/page/ABC",
                "leak": "http://127.0.0.1:9222/json",
            }
        ],
        manager_ws_url_for_entry=lambda entry: (
            f"ws://manager/api/profiles/p1/cdp/devtools/page/{entry['id']}"
        ),
    )
    assert listing == [
        {
            "id": "page1",
            "type": "page",
            "title": "T",
            "url": "https://example.com/",
            "description": "",
            "faviconUrl": "https://example.com/f.ico",
            "parentId": "browser",
            "webSocketDebuggerUrl": "ws://manager/api/profiles/p1/cdp/devtools/page/page1",
        }
    ]
    blob = json.dumps(listing) + json.dumps(version)
    assert "9222" not in blob
    assert "devtoolsFrontendUrl" not in blob


def test_observer_upstream_filter_allows_only_acks_and_screencast_frames():
    from backend import cdp_gateway

    accepted = {1, 2}
    ok_result = cdp_gateway.filter_observer_upstream_message(
        json.dumps({"id": 1, "result": {}}),
        accepted_ids=accepted,
    )
    assert ok_result is not None

    frame = cdp_gateway.filter_observer_upstream_message(
        json.dumps(
            {
                "method": "Page.screencastFrame",
                "params": {
                    "data": "abc",
                    "sessionId": 1,
                    "metadata": {"offsetTop": 0, "pageScaleFactor": 1, "deviceWidth": 100, "deviceHeight": 100, "scrollOffsetX": 0, "scrollOffsetY": 0, "timestamp": 1},
                },
            }
        ),
        accepted_ids=accepted,
    )
    assert frame is not None

    assert (
        cdp_gateway.filter_observer_upstream_message(
            json.dumps({"method": "Runtime.consoleAPICalled", "params": {}}),
            accepted_ids=accepted,
        )
        is None
    )
    assert (
        cdp_gateway.filter_observer_upstream_message(
            json.dumps({"id": 99, "result": {}}),
            accepted_ids=accepted,
        )
        is None
    )


def test_observer_http_works_with_view_and_without_automation_lease(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Observer view", sandbox_id="alpha")
    _create_user(
        client_access,
        username="observer-viewer",
        password="observer-viewer-password-123",
        permission="view",
    )
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6200, cdp_port=5200, display=200
    )

    chrome_list = MagicMock()
    chrome_list.json.return_value = [
        {
            "id": "page1",
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5200/devtools/page/PAGE1",
        },
        {
            "id": "worker",
            "type": "service_worker",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5200/devtools/worker/W1",
        },
    ]
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=chrome_list)

    try:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client_access.get(
                f"/api/profiles/{profile['id']}/cdp-observer/json/list"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "page"
        assert data[0]["webSocketDebuggerUrl"] == (
            f"ws://testserver/api/profiles/{profile['id']}/cdp-observer/devtools/page/PAGE1"
        )
        assert "5200" not in resp.text
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_observer_discovery_strips_devtools_and_upstream_url_fields(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Observer sanitize", sandbox_id="alpha")
    _create_user(
        client_access,
        username="observer-sanitize",
        password="observer-sanitize-password-123",
        permission="view",
    )
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6202, cdp_port=5202, display=202
    )

    chrome_list = MagicMock()
    chrome_list.json.return_value = [
        {
            "id": "page1",
            "type": "page",
            "title": "Observer page",
            "url": "https://example.com/view",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5202/devtools/page/PAGE1",
            "devtoolsFrontendUrl": (
                "http://127.0.0.1:5202/devtools/inspector.html"
                "?ws=127.0.0.1:5202/devtools/page/PAGE1"
            ),
            "faviconUrl": "https://example.com/favicon.ico",
            "upstreamLeak": "ws://10.1.2.3:5202/devtools/page/PAGE1",
        },
        {
            "id": "worker",
            "type": "service_worker",
            "webSocketDebuggerUrl": "ws://127.0.0.1:5202/devtools/worker/W1",
            "devtoolsFrontendUrl": "http://127.0.0.1:5202/devtools/worker.html",
        },
    ]
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=chrome_list)

    try:
        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client_access.get(
                f"/api/profiles/{profile['id']}/cdp-observer/json/list"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Observer page"
        assert data[0]["url"] == "https://example.com/view"
        assert data[0]["faviconUrl"] == "https://example.com/favicon.ico"
        assert data[0]["webSocketDebuggerUrl"] == (
            f"ws://testserver/api/profiles/{profile['id']}/"
            "cdp-observer/devtools/page/PAGE1"
        )
        assert "devtoolsFrontendUrl" not in data[0]
        assert "upstreamLeak" not in data[0]
        assert "5202" not in resp.text
        assert "127.0.0.1" not in resp.text
        assert "devtoolsFrontendUrl" not in resp.text
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_observer_cross_sandbox_is_404(client_access: TestClient, monkeypatch):
    from backend import main

    beta = db.create_profile("Observer beta", sandbox_id="beta")
    _create_user(
        client_access,
        username="alpha-viewer",
        password="alpha-viewer-password-123",
        sandbox_id="alpha",
        permission="view",
    )
    main.browser_mgr.running[beta["id"]] = SimpleNamespace(
        ws_port=6201, cdp_port=5201, display=201
    )
    reached = {"up": False}

    async def fail_get(*_a, **_k):
        reached["up"] = True
        raise AssertionError("observer cross-sandbox reached upstream")

    try:
        with patch("httpx.AsyncClient.get", new=fail_get):
            resp = client_access.get(
                f"/api/profiles/{beta['id']}/cdp-observer/json/list"
            )
        assert resp.status_code == 404
        assert reached["up"] is False
    finally:
        main.browser_mgr.running.pop(beta["id"], None)


def test_observer_ws_rejects_forbidden_commands_before_upstream(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Observer WS", sandbox_id="alpha")
    _create_user(
        client_access,
        username="observer-ws",
        password="observer-ws-password-123",
        permission="view",
    )
    upstream = _ScriptedUpstream()
    monkeypatch.setattr("websockets.connect", lambda *_a, **_k: upstream)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6202, cdp_port=5202, display=202
    )

    try:
        with client_access.websocket_connect(
            f"/api/profiles/{profile['id']}/cdp-observer/devtools/page/PAGE1",
            headers={"origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": "1+1"},
                    }
                )
            )
            message = _receive_websocket_message(websocket)
            assert message["type"] == "websocket.close"
            assert message["code"] in {4400, 4403, 1008}
        assert upstream.sent == []
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_observer_ws_forwards_only_screencast_commands(
    client_access: TestClient, monkeypatch
):
    from backend import main

    profile = db.create_profile("Observer cast", sandbox_id="alpha")
    _create_user(
        client_access,
        username="observer-cast",
        password="observer-cast-password-123",
        permission="view",
    )
    upstream = _ScriptedUpstream(
        [
            json.dumps({"id": 1, "result": {}}),
            json.dumps(
                {
                    "method": "Page.screencastFrame",
                    "params": {
                        "data": "qq",
                        "sessionId": 7,
                        "metadata": {
                            "offsetTop": 0,
                            "pageScaleFactor": 1,
                            "deviceWidth": 10,
                            "deviceHeight": 10,
                            "scrollOffsetX": 0,
                            "scrollOffsetY": 0,
                            "timestamp": 1.0,
                        },
                    },
                }
            ),
            json.dumps({"method": "Runtime.consoleAPICalled", "params": {"type": "log"}}),
        ]
    )
    monkeypatch.setattr("websockets.connect", lambda *_a, **_k: upstream)
    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6203, cdp_port=5203, display=203
    )

    try:
        with client_access.websocket_connect(
            f"/api/profiles/{profile['id']}/cdp-observer/devtools/page/PAGE1",
            headers={"origin": "http://testserver"},
        ) as websocket:
            websocket.send_text(
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
            first = websocket.receive_json()
            assert first["id"] == 1
            frame = websocket.receive_json()
            assert frame["method"] == "Page.screencastFrame"
            # Unrelated upstream event must not arrive; closing ends the proxy.
            websocket.close()
        assert len(upstream.sent) == 1
        sent = json.loads(upstream.sent[0])
        assert sent["method"] == "Page.startScreencast"
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_session_live_html_uses_observer_endpoints_only():
    from backend import session_views

    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        cdp_ws_url="ws://127.0.0.1:18117/api/profiles/prof-1/cdp-observer/devtools/page/x",
        metrics_url="http://127.0.0.1:18117/api/profiles/prof-1/live-metrics",
        interactive=True,
        cdp_list_url="http://127.0.0.1:18117/api/profiles/prof-1/cdp-observer/json/list",
    )
    assert "cdp-observer" in html
    assert "Page.startScreencast" in html
    assert "Page.screencastFrameAck" in html
    assert "Page.stopScreencast" in html
    assert "disconnected" in html or "socket error" in html
    for forbidden in (
        "Target.createTarget",
        "Target.attachToTarget",
        "Target.getTargets",
        "Runtime.evaluate",
        "Runtime.enable",
        "Page.navigate",
        "Input.dispatchMouseEvent",
        "Input.dispatchKeyEvent",
        "Page.captureScreenshot",
        "/cdp/json/list",
        "/api/profiles/prof-1/cdp\"",
    ):
        assert forbidden not in html
