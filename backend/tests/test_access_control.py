"""Integration tests for scoped people and Paperclip-agent browser access."""

from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import access_control as access
from backend import database as db
from backend.main import _RfbClientStreamFilter, _filter_rfb_viewer_messages


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


def bootstrap_headers():
    return {"Authorization": "Bearer bootstrap-test-secret"}


def create_scoped_profiles():
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha", proxy="http://secret-proxy:8080")
    beta = db.create_profile("Beta browser", sandbox_id="beta", proxy="http://other-proxy:8080")
    return alpha, beta


def test_password_hashing_is_salted_and_verifiable():
    first = access.hash_password("very-long-test-password")
    second = access.hash_password("very-long-test-password")
    assert first != second
    assert access.verify_password("very-long-test-password", first) is True
    assert access.verify_password("wrong-password", first) is False


def test_viewer_rfb_filter_allows_only_display_negotiation():
    # SetPixelFormat (20 bytes), FramebufferUpdateRequest (10 bytes),
    # KeyEvent (8 bytes), PointerEvent (6 bytes), and ClientCutText (8 bytes
    # with an empty payload) in one noVNC-style combined frame.
    pixel_format = bytes([0]) + b"\0" * 19
    frame_request = bytes([3]) + b"\0" * 9
    key_event = bytes([4]) + b"\0" * 7
    pointer_event = bytes([5]) + b"\0" * 5
    clipboard = bytes([6]) + b"\0" * 7

    filtered = _filter_rfb_viewer_messages(
        pixel_format + frame_request + key_event + pointer_event + clipboard
    )

    assert filtered == pixel_format + frame_request


def test_viewer_rfb_filter_keeps_safe_encoding_negotiation():
    # Viewers still need the normal noVNC display-negotiation sequence.  This
    # catches a regression where a view grant would connect but remain black.
    set_encodings = struct.pack(">BBHii", 2, 0, 2, 7, -239)
    frame_request = bytes([3]) + b"\0" * 9
    key_event = bytes([4]) + b"\0" * 7

    filtered = _filter_rfb_viewer_messages(set_encodings + frame_request + key_event)

    assert filtered == set_encodings + frame_request


def test_viewer_rfb_stream_filter_drops_appended_input_in_early_frames():
    rfb_filter = _RfbClientStreamFilter(can_interact=False)
    version = b"RFB 003.008\n"
    security_none = b"\x01"
    client_init = b"\x01"
    key_event = bytes([4]) + b"\0" * 7
    pointer_event = bytes([5]) + b"\0" * 5
    clipboard = bytes([6]) + b"\0" * 7

    assert rfb_filter.filter(version + key_event) == version
    assert rfb_filter.filter(security_none + pointer_event) == security_none
    assert rfb_filter.filter(client_init + clipboard) == client_init


def test_viewer_rfb_stream_filter_allows_coalesced_handshake_segments():
    version = b"RFB 003.008\n"
    security_none = b"\x01"
    client_init = b"\x01"

    version_security_filter = _RfbClientStreamFilter(can_interact=False)
    assert version_security_filter.filter(version + security_none) == (
        version + security_none
    )
    assert version_security_filter.filter(client_init) == client_init

    security_client_init_filter = _RfbClientStreamFilter(can_interact=False)
    assert security_client_init_filter.filter(version) == version
    assert security_client_init_filter.filter(security_none + client_init) == (
        security_none + client_init
    )


def test_viewer_rfb_stream_filter_drops_input_after_full_coalesced_handshake():
    version = b"RFB 003.008\n"
    security_none = b"\x01"
    client_init = b"\x01"
    handshake = version + security_none + client_init
    key_event = bytes([4]) + b"\0" * 7
    pointer_event = bytes([5]) + b"\0" * 5
    clipboard = bytes([6]) + b"\0" * 7

    for input_event in (key_event, pointer_event, clipboard):
        rfb_filter = _RfbClientStreamFilter(can_interact=False)
        assert rfb_filter.filter(handshake + input_event) == handshake


def test_viewer_rfb_stream_filter_preserves_handshake_and_display_negotiation():
    rfb_filter = _RfbClientStreamFilter(can_interact=False)
    version = b"RFB 003.008\n"
    security_none = b"\x01"
    client_init = b"\x01"
    set_encodings = struct.pack(">BBHii", 2, 0, 2, 7, -239)
    frame_request = bytes([3]) + b"\0" * 9
    key_event = bytes([4]) + b"\0" * 7

    assert rfb_filter.filter(version[:5]) == version[:5]
    assert rfb_filter.filter(version[5:]) == version[5:]
    assert rfb_filter.filter(security_none) == security_none
    assert rfb_filter.filter(client_init) == client_init
    assert rfb_filter.filter(set_encodings + frame_request + key_event) == (
        set_encodings + frame_request
    )


def test_scoped_viewer_only_sees_granted_sandbox_and_no_sensitive_config(client_access: TestClient):
    alpha, beta = create_scoped_profiles()
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "viewer",
            "password": "viewer-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201
    assert "password_hash" not in created.json()

    client_access.cookies.clear()
    login = client_access.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password-123"},
    )
    assert login.status_code == 200
    assert login.json()["identity"]["kind"] == "user"

    listed = client_access.get("/api/profiles")
    assert listed.status_code == 200
    payload = listed.json()
    assert [profile["id"] for profile in payload] == [alpha["id"]]
    assert payload[0]["proxy"] is None
    assert payload[0]["user_data_dir"] == ""
    assert payload[0]["cdp_url"] is None

    assert client_access.get(f"/api/profiles/{alpha['id']}").status_code == 200
    denied = client_access.get(f"/api/profiles/{beta['id']}")
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Profile not found"
    assert client_access.post(f"/api/profiles/{alpha['id']}/launch").status_code == 404
    assert client_access.post(
        f"/api/profiles/{alpha['id']}/clipboard", json={"text": "should not reach xclip"}
    ).status_code == 404

    status = client_access.get(f"/api/profiles/{alpha['id']}/status")
    assert status.status_code == 200
    assert status.json()["vnc_ws_port"] is None
    assert status.json()["cdp_url"] is None

    with db.get_db() as conn:
        denied_events = {
            (row["action"], row["sandbox_id"], row["profile_id"], row["outcome"])
            for row in conn.execute(
                "SELECT action, sandbox_id, profile_id, outcome FROM access_audit_events WHERE outcome = 'denied'"
            ).fetchall()
        }
    assert ("profile.permission.view", "beta", beta["id"], "denied") in denied_events
    assert ("profile.permission.operate", "alpha", alpha["id"], "denied") in denied_events
    assert ("profile.permission.interact", "alpha", alpha["id"], "denied") in denied_events


def test_operator_can_operate_only_its_scoped_profile(
    client_access: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    monkeypatch.setattr(
        main.browser_mgr,
        "launch",
        AsyncMock(side_effect=RuntimeError("launch unavailable in policy test")),
    )
    alpha, beta = create_scoped_profiles()
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "operator",
            "password": "operator-password-123",
            "role": "operator",
            "grants": [{"sandbox_id": "alpha", "permission": "operate"}],
        },
    )
    assert created.status_code == 201

    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login", json={"username": "operator", "password": "operator-password-123"}
    ).status_code == 200
    # The controlled lifecycle error proves policy allowed the request to reach
    # the handler without depending on whether CloakBrowser is installed in the
    # test image.
    allowed = client_access.post(f"/api/profiles/{alpha['id']}/launch")
    assert allowed.status_code in {409, 500}
    denied = client_access.post(f"/api/profiles/{beta['id']}/launch")
    assert denied.status_code == 404


def test_interact_user_can_create_task_session_with_redacted_metadata(client_access: TestClient):
    alpha, _beta = create_scoped_profiles()
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "interact-task",
            "password": "interact-task-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "interact"}],
        },
    )
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login",
        json={"username": "interact-task", "password": "interact-task-password-123"},
    ).status_code == 200

    created = client_access.post(
        "/api/task-sessions",
        json={
            "profile_id": alpha["id"],
            "title": "Scoped harness task",
            "metadata": {
                "source": "vcvm-e2e",
                "authorization": "Bearer should-not-persist",
                "nested": {"password": "should-not-persist"},
            },
        },
    )

    assert created.status_code == 201
    assert created.json()["metadata"] == {
        "source": "vcvm-e2e",
        "authorization": "[redacted]",
        "nested": {"password": "[redacted]"},
    }


def test_interact_user_cannot_create_task_session_outside_granted_sandbox(
    client_access: TestClient,
):
    _alpha, beta = create_scoped_profiles()
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "interact-task-denied",
            "password": "interact-task-denied-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "interact"}],
        },
    )
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login",
        json={
            "username": "interact-task-denied",
            "password": "interact-task-denied-password-123",
        },
    ).status_code == 200

    denied = client_access.post(
        "/api/task-sessions",
        json={"profile_id": beta["id"], "title": "Out of scope"},
    )

    assert denied.status_code == 404
    assert denied.json()["detail"] == "Profile not found"


def test_interact_user_cannot_operate_or_automate_profile(client_access: TestClient):
    alpha, _beta = create_scoped_profiles()
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "interact-no-lifecycle",
            "password": "interact-no-lifecycle-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "interact"}],
        },
    )
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login",
        json={
            "username": "interact-no-lifecycle",
            "password": "interact-no-lifecycle-password-123",
        },
    ).status_code == 200

    launch_denied = client_access.post(f"/api/profiles/{alpha['id']}/launch")
    cdp_denied = client_access.get(f"/api/profiles/{alpha['id']}/cdp")

    assert launch_denied.status_code == 404
    assert cdp_denied.status_code == 404


def test_paperclip_agent_command_metadata_is_redacted(client_access: TestClient):
    alpha, _beta = create_scoped_profiles()
    created = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Paperclip task agent",
            "paperclip_agent_id": "paperclip-agent-task",
            "grants": [
                {"sandbox_id": "alpha", "permission": "interact"},
                {"sandbox_id": "alpha", "permission": "automate"},
            ],
        },
    )
    agent_headers = {"Authorization": f"Bearer {created.json()['api_key']}"}
    identity = client_access.get("/api/access/me", headers=agent_headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()["kind"] == "agent"
    visible = client_access.get("/api/profiles", headers=agent_headers)
    assert visible.status_code == 200, visible.text
    assert [profile["id"] for profile in visible.json()] == [alpha["id"]]
    session = client_access.post(
        "/api/task-sessions",
        headers=agent_headers,
        json={"profile_id": alpha["id"], "title": "Agent task"},
    )
    assert session.status_code == 201, session.text

    command = client_access.post(
        f"/api/task-sessions/{session.json()['id']}/commands",
        headers=agent_headers,
        json={
            "content": "type into browser",
            "metadata": {
                "harness": "paperclip",
                "cookie": "should-not-persist",
                "api_key": "should-not-persist",
            },
        },
    )

    assert command.status_code == 201
    assert command.json()["metadata"]["harness"] == "paperclip"
    assert command.json()["metadata"]["cookie"] == "[redacted]"
    assert command.json()["metadata"]["api_key"] == "[redacted]"


def test_admin_can_update_a_user_grants_from_the_access_dashboard_payload(client_access: TestClient):
    """Pydantic serializes nested grants before main.py receives the update.

    Keep the browser-facing access dashboard able to move a person between
    sandboxes instead of raising an AttributeError while normalizing its JSON
    payload.
    """
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "grant-editor",
            "password": "grant-editor-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201

    updated = client_access.put(
        f"/api/access/users/{created.json()['id']}",
        headers=bootstrap_headers(),
        json={
            "grants": [{"sandbox_id": "beta", "permission": "operate"}],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["grants"] == [{"sandbox_id": "beta", "permission": "operate"}]


def test_admin_can_update_an_agent_grants_from_the_access_dashboard_payload(
    client_access: TestClient,
):
    created = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Grant editable Paperclip agent",
            "paperclip_agent_id": "paperclip-agent-grant-editor",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201

    updated = client_access.put(
        f"/api/access/agents/{created.json()['id']}",
        headers=bootstrap_headers(),
        json={
            "grants": [{"sandbox_id": "beta", "permission": "automate"}],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["grants"] == [{"sandbox_id": "beta", "permission": "automate"}]


def test_paperclip_agent_key_is_scoped_and_rotation_revokes_old_key(client_access: TestClient):
    alpha, beta = create_scoped_profiles()
    created = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Paperclip research agent",
            "paperclip_agent_id": "paperclip-agent-research",
            "grants": [{"sandbox_id": "beta", "permission": "automate"}],
        },
    )
    assert created.status_code == 201
    agent = created.json()
    assert agent["api_key"].startswith("cbm_agent_")
    assert "key_hash" not in agent

    agent_headers = {"Authorization": f"Bearer {agent['api_key']}"}
    visible = client_access.get("/api/profiles", headers=agent_headers)
    assert visible.status_code == 200
    assert [profile["id"] for profile in visible.json()] == [beta["id"]]

    denied = client_access.get(f"/api/profiles/{alpha['id']}/cdp", headers=agent_headers)
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Profile not found"
    allowed = client_access.get(f"/api/profiles/{beta['id']}/cdp", headers=agent_headers)
    assert allowed.status_code == 404
    assert allowed.json()["detail"] == "Profile not running"
    assert client_access.post(f"/api/profiles/{beta['id']}/launch", headers=agent_headers).status_code == 404

    rotated = client_access.post(
        f"/api/access/agents/{agent['id']}/rotate-key", headers=bootstrap_headers()
    )
    assert rotated.status_code == 200
    assert rotated.json()["api_key"] != agent["api_key"]
    assert client_access.get("/api/profiles", headers=agent_headers).status_code == 401


def test_scoped_user_cannot_reach_out_of_scope_vnc_upstream(client_access: TestClient, monkeypatch):
    from backend import main

    alpha, beta = create_scoped_profiles()
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "viewer-ws",
            "password": "viewer-ws-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login", json={"username": "viewer-ws", "password": "viewer-ws-password-123"}
    ).status_code == 200

    main.browser_mgr.running[beta["id"]] = SimpleNamespace(ws_port=6100, cdp_port=5100, display=100)

    async def fail_if_vnc_upstream_is_used(*_args, **_kwargs):
        raise AssertionError("out-of-scope VNC request reached upstream")

    monkeypatch.setattr("websockets.connect", fail_if_vnc_upstream_is_used)
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client_access.websocket_connect(f"/api/profiles/{beta['id']}/vnc"):
                pass
        assert exc.value.code == 4404
    finally:
        main.browser_mgr.running.pop(beta["id"], None)

    with db.get_db() as conn:
        denied_vnc = conn.execute(
            """SELECT action, sandbox_id, profile_id, outcome
            FROM access_audit_events
            WHERE action = 'profile.permission.view' AND profile_id = ?""",
            (beta["id"],),
        ).fetchone()
    assert denied_vnc is not None
    assert denied_vnc["sandbox_id"] == "beta"
    assert denied_vnc["outcome"] == "denied"


def test_scoped_agent_cannot_reach_out_of_scope_cdp_upstreams(client_access: TestClient, monkeypatch):
    from backend import main

    alpha, beta = create_scoped_profiles()
    created = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Paperclip CDP agent",
            "paperclip_agent_id": "paperclip-agent-cdp",
            "grants": [{"sandbox_id": "alpha", "permission": "automate"}],
        },
    )
    agent_headers = {"Authorization": f"Bearer {created.json()['api_key']}"}
    main.browser_mgr.running[beta["id"]] = SimpleNamespace(ws_port=6101, cdp_port=5101, display=101)

    class ForbiddenAsyncClient:
        async def __aenter__(self):
            raise AssertionError("out-of-scope CDP request reached HTTP upstream")

        async def __aexit__(self, *_args):
            return False

    async def fail_if_cdp_upstream_is_used(*_args, **_kwargs):
        raise AssertionError("out-of-scope CDP request reached WebSocket upstream")

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *_args, **_kwargs: ForbiddenAsyncClient())
    monkeypatch.setattr("websockets.connect", fail_if_cdp_upstream_is_used)
    try:
        for path in (
            f"/api/profiles/{beta['id']}/cdp",
            f"/api/profiles/{beta['id']}/cdp/devtools/page/OUT_OF_SCOPE",
        ):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client_access.websocket_connect(path, headers=agent_headers):
                    pass
            assert exc.value.code == 4404
    finally:
        main.browser_mgr.running.pop(beta["id"], None)


def test_human_login_failures_are_throttled_per_username_and_client(
    client_access: TestClient, monkeypatch
):
    from backend import main

    client_ip = {"value": "198.51.100.10"}
    monkeypatch.setattr(main, "_client_host", lambda _request: client_ip["value"])
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "limited-user",
            "password": "limited-user-password-123",
            "grants": [],
        },
    )

    client_access.cookies.clear()
    for _ in range(5):
        failed = client_access.post(
            "/api/auth/login",
            json={"username": "limited-user", "password": "wrong-password-123"},
        )
        assert failed.status_code == 401

    throttled = client_access.post(
        "/api/auth/login",
        json={"username": "limited-user", "password": "wrong-password-123"},
    )
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"]

    client_ip["value"] = "198.51.100.11"
    assert client_access.post(
        "/api/auth/login",
        json={"username": "limited-user", "password": "limited-user-password-123"},
    ).status_code == 200
    client_access.cookies.clear()

    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "other-limited-user",
            "password": "other-limited-user-password-123",
            "grants": [],
        },
    )
    assert client_access.post(
        "/api/auth/login",
        json={"username": "other-limited-user", "password": "other-limited-user-password-123"},
    ).status_code == 200


def test_login_failure_state_expires_old_entries(monkeypatch):
    from backend import main

    clock = {"now": 1000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])
    main._login_failures.clear()

    main._record_login_failure(("stale-user", "198.51.100.20"))
    assert ("stale-user", "198.51.100.20") in main._login_failures

    clock["now"] += main._LOGIN_FAILURE_TTL_SECONDS + 0.1
    main._cleanup_login_failures()
    assert ("stale-user", "198.51.100.20") not in main._login_failures

    blocked_key = ("blocked-user", "198.51.100.21")
    for _ in range(main._LOGIN_FAILURE_LIMIT):
        main._record_login_failure(blocked_key)
    assert main._login_backoff_remaining(blocked_key) > 0

    clock["now"] += main._LOGIN_BACKOFF_SECONDS + 0.1
    main._cleanup_login_failures()
    assert blocked_key not in main._login_failures


def test_login_failure_state_is_bounded_with_oldest_eviction(monkeypatch):
    from backend import main

    clock = {"now": 2000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(main, "_LOGIN_FAILURE_MAX_KEYS", 3)
    main._login_failures.clear()

    for index in range(5):
        main._record_login_failure((f"user-{index}", "198.51.100.30"))
        clock["now"] += 1.0

    assert set(main._login_failures) == {
        ("user-2", "198.51.100.30"),
        ("user-3", "198.51.100.30"),
        ("user-4", "198.51.100.30"),
    }


def test_successful_human_login_resets_failed_login_backoff(client_access: TestClient):
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "reset-user",
            "password": "reset-user-password-123",
            "grants": [],
        },
    )

    client_access.cookies.clear()
    for _ in range(4):
        assert client_access.post(
            "/api/auth/login",
            json={"username": "reset-user", "password": "wrong-password-123"},
        ).status_code == 401

    assert client_access.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "reset-user-password-123"},
    ).status_code == 200
    client_access.cookies.clear()

    for _ in range(4):
        assert client_access.post(
            "/api/auth/login",
            json={"username": "reset-user", "password": "wrong-password-123"},
        ).status_code == 401
    assert client_access.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "reset-user-password-123"},
    ).status_code == 200


def test_access_management_is_admin_only(client_access: TestClient):
    alpha, _beta = create_scoped_profiles()
    client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "viewer2",
            "password": "viewer2-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login", json={"username": "viewer2", "password": "viewer2-password-123"}
    ).status_code == 200
    assert client_access.get("/api/access/users").status_code == 403
    assert client_access.post(
        "/api/access/agents",
        json={"display_name": "not allowed"},
    ).status_code == 403
