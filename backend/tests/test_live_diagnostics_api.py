"""API tests for GET /api/admin/live-diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import live_diagnostics


def _patch_lifespan(main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())


@pytest.fixture()
def client_access(tmp_db, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    _patch_lifespan(main, monkeypatch)
    live_diagnostics.live_diagnostics.reset()

    with TestClient(main.app) as client:
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def test_live_diagnostics_requires_admin(client_access: TestClient):
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "viewer-diag",
            "password": "viewer-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201, created.text

    client_access.cookies.clear()
    login = client_access.post(
        "/api/auth/login",
        json={"username": "viewer-diag", "password": "viewer-password-123"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["identity"]["kind"] == "user"

    denied = client_access.get("/api/admin/live-diagnostics")
    assert denied.status_code == 403


def test_live_diagnostics_admin_snapshot_is_redacted(client_access: TestClient):
    live_diagnostics.live_diagnostics.mark_launch_started("prof-1")
    live_diagnostics.live_diagnostics.mark_launch_succeeded("prof-1")
    session = live_diagnostics.live_diagnostics.begin_vnc_session("prof-1")
    live_diagnostics.live_diagnostics.mark_vnc_websocket_open("prof-1", session)

    response = client_access.get(
        "/api/admin/live-diagnostics",
        headers=bootstrap_headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_launches"] == 1
    assert payload["total_vnc_connections"] == 1
    assert "profiles" in payload
    blob = response.text.lower()
    for forbidden in ("ws_port", "cdp_url", "password", "token=", "/tmp/"):
        assert forbidden not in blob
    profile = next(p for p in payload["profiles"] if p["profile_id"] == "prof-1")
    assert profile["metrics"]["vnc_first_framebuffer_ms"]["availability"] == "unavailable"
    assert profile["metrics"]["vnc_websocket_open_ms"]["availability"] == "measured"
    assert profile["metrics"]["launch_duration_ms"]["availability"] == "measured"
