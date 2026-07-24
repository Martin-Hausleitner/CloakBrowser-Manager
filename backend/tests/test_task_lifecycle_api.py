"""API tests for temporary/done task lifecycle and optimistic concurrency."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

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


def create_user(
    client: TestClient,
    username: str,
    sandbox_id: str,
    permission: str,
) -> str:
    password = f"{username}-password-123"
    response = client.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": username,
            "password": password,
            "grants": [{"sandbox_id": sandbox_id, "permission": permission}],
        },
    )
    assert response.status_code == 201
    return password


def login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def create_task(client: TestClient, profile_id: str) -> dict:
    created = client.post(
        "/api/task-sessions",
        json={"profile_id": profile_id, "title": "Lifecycle task"},
    )
    assert created.status_code == 201
    return created.json()


def patch_task(client: TestClient, task: dict, body: dict) -> dict:
    payload = {"row_version": task["row_version"], **body}
    response = client.patch(f"/api/task-sessions/{task['id']}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_task_can_be_done_archived_unarchived_and_reopened(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    task = create_task(client_access, profile["id"])
    assert task["workflow_state"] == "open"
    assert task["done_at"] is None
    assert task["archived_at"] is None
    assert task["retention_class"] == "project"
    assert task["row_version"] == 1
    assert task["activity_at"]

    done = patch_task(client_access, task, {"workflow_state": "done"})
    assert done["workflow_state"] == "done"
    assert done["done_at"]
    assert done["row_version"] == 2
    assert done["activity_at"] >= task["activity_at"]

    archived = patch_task(client_access, done, {"archived": True})
    assert archived["archived_at"]
    assert archived["status"] == "archived"
    assert archived["row_version"] == 3

    reopened = patch_task(
        client_access,
        archived,
        {"archived": False, "workflow_state": "open"},
    )
    assert reopened["workflow_state"] == "open"
    assert reopened["done_at"] is None
    assert reopened["archived_at"] is None
    assert reopened["status"] == "active"
    assert reopened["row_version"] == 4


def test_task_retention_and_activity_update(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    task = create_task(client_access, profile["id"])
    updated = patch_task(
        client_access,
        task,
        {"retention_class": "temporary", "title": "Temp chat"},
    )
    assert updated["retention_class"] == "temporary"
    assert updated["title"] == "Temp chat"
    assert updated["expires_at"] is None
    assert updated["activity_at"] >= task["activity_at"]
    assert updated["row_version"] == 2


def test_task_row_version_conflict(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    task = create_task(client_access, profile["id"])
    stale = client_access.patch(
        f"/api/task-sessions/{task['id']}",
        json={"row_version": 1, "workflow_state": "done"},
    )
    assert stale.status_code == 200

    conflict = client_access.patch(
        f"/api/task-sessions/{task['id']}",
        json={"row_version": 1, "workflow_state": "open"},
    )
    assert conflict.status_code == 409
    current = db.get_task_session(task["id"])
    assert current["workflow_state"] == "done"
    assert current["row_version"] == 2


def test_task_auth_uses_immutable_sandbox_after_profile_delete(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    task = create_task(client_access, profile["id"])
    assert db.delete_profile(profile["id"]) is True
    assert db.get_task_session(task["id"])["profile_id"] is None

    fetched = client_access.get(f"/api/task-sessions/{task['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["profile_id"] is None
    assert fetched.json()["sandbox_id"] == "alpha"

    updated = patch_task(client_access, task, {"workflow_state": "done"})
    assert updated["workflow_state"] == "done"
    assert updated["sandbox_id"] == "alpha"


def test_cross_sandbox_task_lifecycle_is_indistinguishable_404(
    client_access: TestClient,
):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    beta_task = db.create_task_session(beta["id"], "beta", "bootstrap")

    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    denied = client_access.patch(
        f"/api/task-sessions/{beta_task['id']}",
        json={"row_version": 1, "workflow_state": "done"},
    )
    assert denied.status_code == 404
    assert db.get_task_session(beta_task["id"])["workflow_state"] == "open"

    # Control: same-sandbox task remains writable.
    alpha_task = create_task(client_access, alpha["id"])
    assert patch_task(client_access, alpha_task, {"workflow_state": "done"})[
        "workflow_state"
    ] == "done"


def test_legacy_retention_is_preserved_and_not_overwritten_by_reopen(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-interact", "alpha", "interact")
    login(client_access, "alpha-interact", password)

    task = db.create_task_session(profile["id"], "alpha", "bootstrap")
    with db.get_db() as conn:
        conn.execute(
            """UPDATE task_sessions
               SET retention_class = 'legacy', expires_at = NULL
               WHERE id = ?""",
            (task["id"],),
        )
        conn.commit()
    task = db.get_task_session(task["id"])
    assert task["retention_class"] == "legacy"

    client_access.cookies.clear()
    login(client_access, "alpha-interact", password)
    updated = patch_task(
        client_access,
        task,
        {"workflow_state": "done"},
    )
    assert updated["retention_class"] == "legacy"
    assert updated["expires_at"] is None
