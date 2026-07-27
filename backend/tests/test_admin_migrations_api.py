"""API tests for GET /api/admin/migrations."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db


@pytest.fixture()
def client_access(tmp_db, monkeypatch: pytest.MonkeyPatch):
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


def test_admin_migrations_requires_authentication(client_access: TestClient):
    response = client_access.get("/api/admin/migrations")

    assert response.status_code == 401


def test_admin_migrations_requires_admin(client_access: TestClient):
    created = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "migration-viewer",
            "password": "migration-viewer-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert created.status_code == 201, created.text

    client_access.cookies.clear()
    login = client_access.post(
        "/api/auth/login",
        json={
            "username": "migration-viewer",
            "password": "migration-viewer-password-123",
        },
    )
    assert login.status_code == 200, login.text

    response = client_access.get("/api/admin/migrations")

    assert response.status_code == 403


def test_admin_migrations_returns_sorted_release_required_ids(
    client_access: TestClient,
):
    response = client_access.get(
        "/api/admin/migrations",
        headers=bootstrap_headers(),
    )

    assert response.status_code == 200, response.text
    migrations = response.json()
    assert migrations == sorted(set(migrations))
    assert all(isinstance(migration, str) and migration for migration in migrations)
    assert {
        "agent_workspace_v1",
        "task_run_binding_v1",
        "task_runs_acpx_v1",
        "task_runs_v1",
        "worker_harness_preflights_v1",
        "worker_harness_presence_v1",
        "worker_runtime_v1",
    }.issubset(migrations)
    for forbidden in ("profiles.db", "/data", "/tmp", "applied_at", "token", "secret"):
        assert forbidden not in response.text


def test_admin_migrations_returns_sanitized_unavailable_when_status_read_fails(
    client_access: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def broken_get_db():
        raise sqlite3.DatabaseError("database disk image is malformed: /tmp/profiles.db")

    monkeypatch.setattr(db, "get_db", broken_get_db)

    response = client_access.get(
        "/api/admin/migrations",
        headers=bootstrap_headers(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Schema migration status unavailable"}
    assert "malformed" not in response.text
    assert "/tmp/profiles.db" not in response.text
