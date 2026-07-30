"""Tests for optional authentication middleware and endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def client_no_auth(tmp_db, monkeypatch):
    """TestClient with AUTH_TOKEN = None (auth disabled)."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", None)
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def client_auth(tmp_db, monkeypatch):
    """TestClient with AUTH_TOKEN = 'test-secret' (auth enabled)."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def client_dev_auto_admin(tmp_db, monkeypatch):
    """Development-only bypass authenticates every public request as admin."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "DEV_AUTO_ADMIN", True)
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


# ── Group A: AUTH_TOKEN not set ──────────────────────────────────────────────


def test_no_auth_profiles_accessible(client_no_auth: TestClient):
    resp = client_no_auth.get("/api/profiles")
    assert resp.status_code == 200


def test_no_auth_status_shows_not_required(client_no_auth: TestClient):
    resp = client_no_auth.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "private, no-store"
    data = resp.json()
    assert data["auth_required"] is False
    assert data["authenticated"] is False


def test_no_auth_login_noop(client_no_auth: TestClient):
    resp = client_no_auth.post("/api/auth/login", json={"token": "anything"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ── Group A2: explicit development auto-admin ────────────────────────────────


def test_dev_auto_admin_environment_is_fail_closed_for_production():
    from backend import main

    assert main._resolve_dev_auto_admin("1", "development") is True
    assert main._resolve_dev_auto_admin("true", "dev") is True
    assert main._resolve_dev_auto_admin("0", "production") is False
    with pytest.raises(RuntimeError, match="development-only"):
        main._resolve_dev_auto_admin("1", "production")


def test_dev_auto_admin_opens_public_api_as_bootstrap_admin(
    client_dev_auto_admin: TestClient,
):
    profiles = client_dev_auto_admin.get("/api/profiles")
    status = client_dev_auto_admin.get("/api/auth/status")

    assert profiles.status_code == 200
    assert status.status_code == 200
    assert status.headers["cache-control"] == "private, no-store"
    body = status.json()
    assert body["auth_required"] is False
    assert body["authenticated"] is True
    assert body["access_control_enabled"] is True
    assert body["identity"]["kind"] == "bootstrap"
    assert body["identity"]["role"] == "admin"


def test_dev_auto_admin_logout_cannot_reenable_login(
    client_dev_auto_admin: TestClient,
):
    assert client_dev_auto_admin.post("/api/auth/logout").status_code == 200
    status = client_dev_auto_admin.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["auth_required"] is False


def test_dev_auto_admin_does_not_open_internal_worker_api(
    client_dev_auto_admin: TestClient,
):
    response = client_dev_auto_admin.post("/internal/task-runs/claim")
    assert response.status_code == 401


# ── Group B: AUTH_TOKEN set ──────────────────────────────────────────────────


def test_auth_no_token_401(client_auth: TestClient):
    resp = client_auth.get("/api/profiles")
    assert resp.status_code == 401


def test_auth_wrong_bearer_401(client_auth: TestClient):
    resp = client_auth.get(
        "/api/profiles", headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_auth_correct_bearer_200(client_auth: TestClient):
    resp = client_auth.get(
        "/api/profiles", headers={"Authorization": "Bearer test-secret"}
    )
    assert resp.status_code == 200


def test_auth_correct_cookie_200(client_auth: TestClient):
    client_auth.cookies.set("auth_token", "test-secret")
    resp = client_auth.get("/api/profiles")
    assert resp.status_code == 200


def test_auth_status_unauthenticated(client_auth: TestClient):
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is True
    assert data["authenticated"] is False


def test_auth_status_authenticated(client_auth: TestClient):
    client_auth.cookies.set("auth_token", "test-secret")
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is True
    assert data["authenticated"] is True


def test_login_correct_sets_cookie(client_auth: TestClient):
    resp = client_auth.post("/api/auth/login", json={"token": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "auth_token" in resp.cookies


def test_login_wrong_token_401(client_auth: TestClient):
    resp = client_auth.post("/api/auth/login", json={"token": "wrong"})
    assert resp.status_code == 401


def test_logout_clears_cookie(client_auth: TestClient):
    # Login first
    client_auth.post("/api/auth/login", json={"token": "test-secret"})
    # Logout
    resp = client_auth.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_healthcheck_always_accessible(client_auth: TestClient):
    """GET /health remains available without profile/runtime metadata."""
    resp = client_auth.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Status includes browser counts and is intentionally authenticated.
    assert client_auth.get("/api/status").status_code == 401


def test_auth_status_always_accessible(client_auth: TestClient):
    """GET /api/auth/status must work without auth (frontend bootstrap)."""
    resp = client_auth.get("/api/auth/status")
    assert resp.status_code == 200
