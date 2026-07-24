"""API tests for POST /api/profiles/bulk-organize."""

from __future__ import annotations

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


def test_bulk_organize_moves_profiles_and_keeps_sandbox_authz(client_access: TestClient):
    alpha = db.create_profile("Alpha", sandbox_id="alpha", project_id="old", folder_path="a")
    beta = db.create_profile("Beta", sandbox_id="alpha", project_id="old", folder_path="b")
    other = db.create_profile("Other", sandbox_id="beta", project_id="keep")

    # Viewer cannot organize
    viewer = client_access.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": "bulk-viewer",
            "password": "viewer-password-123",
            "grants": [{"sandbox_id": "alpha", "permission": "view"}],
        },
    )
    assert viewer.status_code == 201
    client_access.cookies.clear()
    assert client_access.post(
        "/api/auth/login",
        json={"username": "bulk-viewer", "password": "viewer-password-123"},
    ).status_code == 200
    denied = client_access.post(
        "/api/profiles/bulk-organize",
        json={"profile_ids": [alpha["id"], beta["id"]], "project_id": "commerce", "folder_path": "buyers/us"},
    )
    assert denied.status_code in {403, 404}

    # Admin can organize within scope
    moved = client_access.post(
        "/api/profiles/bulk-organize",
        headers=bootstrap_headers(),
        json={
            "profile_ids": [alpha["id"], beta["id"], other["id"]],
            "project_id": "commerce",
            "folder_path": "buyers/us",
            "pinned": True,
        },
    )
    assert moved.status_code == 200, moved.text
    payload = moved.json()
    assert {row["id"] for row in payload} == {alpha["id"], beta["id"], other["id"]}
    assert all(row["project_id"] == "commerce" for row in payload)
    assert all(row["folder_path"] == "buyers/us" for row in payload)
    assert all(row["pinned"] is True for row in payload)


def test_bulk_organize_requires_at_least_one_field(client_access: TestClient):
    profile = db.create_profile("Solo")
    response = client_access.post(
        "/api/profiles/bulk-organize",
        headers=bootstrap_headers(),
        json={"profile_ids": [profile["id"]]},
    )
    assert response.status_code == 400
