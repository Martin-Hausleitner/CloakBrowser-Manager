"""Integration tests for scoped people and Paperclip-agent browser access."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import access_control as access
from backend import database as db
from backend.main import _filter_rfb_viewer_messages


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
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


def test_operator_can_operate_only_its_scoped_profile(client_access: TestClient):
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
    # No running browser is expected in this test. The error proves the policy
    # allowed the request to reach the lifecycle handler.
    allowed = client_access.post(f"/api/profiles/{alpha['id']}/launch")
    assert allowed.status_code in {409, 500}
    denied = client_access.post(f"/api/profiles/{beta['id']}/launch")
    assert denied.status_code == 404


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
