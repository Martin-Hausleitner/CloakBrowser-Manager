"""API tests for first-class sandbox-scoped projects."""

from __future__ import annotations

import concurrent.futures
import threading
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


def test_empty_project_survives_reload(client_access: TestClient):
    password = create_user(client_access, "alpha-operator", "alpha", "operate")
    login(client_access, "alpha-operator", password)

    created = client_access.post(
        "/api/projects",
        json={
            "id": "research",
            "name": "Research",
            "sandbox_id": "alpha",
            "default_retention": "project",
            "description": "Empty project without profiles",
            "accent_color": "#336699",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "research"
    assert body["sandbox_id"] == "alpha"
    assert body["name"] == "Research"
    assert body["default_retention"] == "project"
    assert body["description"] == "Empty project without profiles"
    assert body["accent_color"] == "#336699"
    assert body["archived_at"] is None
    assert body["created_by_kind"] == "user"
    assert body["created_by_id"]
    assert body["created_at"]
    assert body["updated_at"]

    listed = client_access.get("/api/projects?sandbox_id=alpha")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == "research"

    # Reload after re-init of the same DB file (simulates process reopen).
    db.init_db()
    stored = db.get_project("alpha", "research")
    assert stored is not None
    assert stored["name"] == "Research"
    assert db.list_projects("alpha")[0]["id"] == "research"


def test_project_can_be_archived_and_updated(client_access: TestClient):
    password = create_user(client_access, "alpha-operator", "alpha", "operate")
    login(client_access, "alpha-operator", password)

    created = client_access.post(
        "/api/projects",
        json={"id": "ops", "name": "Ops", "sandbox_id": "alpha"},
    )
    assert created.status_code == 201

    updated = client_access.patch(
        "/api/projects/ops?sandbox_id=alpha",
        json={
            "name": "Operations",
            "description": "Day-to-day",
            "default_retention": "temporary",
            "archived": True,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "Operations"
    assert body["description"] == "Day-to-day"
    assert body["default_retention"] == "temporary"
    assert body["archived_at"]

    restored = client_access.patch(
        "/api/projects/ops?sandbox_id=alpha",
        json={"archived": False},
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


def test_duplicate_project_is_conflict(client_access: TestClient):
    password = create_user(client_access, "alpha-operator", "alpha", "operate")
    login(client_access, "alpha-operator", password)

    first = client_access.post(
        "/api/projects",
        json={"id": "research", "name": "Research", "sandbox_id": "alpha"},
    )
    assert first.status_code == 201
    second = client_access.post(
        "/api/projects",
        json={"id": "research", "name": "Research again", "sandbox_id": "alpha"},
    )
    assert second.status_code == 409


def test_concurrent_create_project_maps_primary_key_race_to_conflict(
    client_access: TestClient,
):
    """Exactly one concurrent create wins; the loser is ProjectConflictError, not IntegrityError."""
    del client_access  # Ensures isolated tmp_db is initialized.
    start = threading.Barrier(2)
    results: list[object] = []

    def attempt() -> None:
        start.wait(timeout=5)
        try:
            results.append(
                db.create_project(
                    sandbox_id="alpha",
                    project_id="race",
                    name="Race",
                    created_by_kind="bootstrap",
                )
            )
        except Exception as exc:  # noqa: BLE001 - capture exact loser type
            results.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    successes = [item for item in results if isinstance(item, dict)]
    failures = [item for item in results if not isinstance(item, dict)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], db.ProjectConflictError)
    assert not isinstance(failures[0], db.sqlite3.IntegrityError)
    assert db.get_project("alpha", "race") is not None
    assert db.get_project("alpha", "race")["id"] == "race"


def test_cross_sandbox_projects_are_indistinguishable_404(client_access: TestClient):
    password = create_user(client_access, "alpha-operator", "alpha", "operate")
    login(client_access, "alpha-operator", password)

    created = client_access.post(
        "/api/projects",
        json={"id": "secret", "name": "Secret", "sandbox_id": "beta"},
    )
    assert created.status_code == 404

    db.create_project(
        sandbox_id="beta",
        project_id="secret",
        name="Secret",
        created_by_kind="bootstrap",
    )

    listed = client_access.get("/api/projects?sandbox_id=beta")
    assert listed.status_code == 404

    fetched = client_access.get("/api/projects/secret?sandbox_id=beta")
    assert fetched.status_code == 404

    patched = client_access.patch(
        "/api/projects/secret?sandbox_id=beta",
        json={"name": "Nope"},
    )
    assert patched.status_code == 404


def test_profile_create_ensures_project_row(client_access: TestClient):
    password = create_user(client_access, "alpha-operator", "alpha", "operate")
    login(client_access, "alpha-operator", password)

    created = client_access.post(
        "/api/profiles",
        json={
            "name": "Browser",
            "sandbox_id": "alpha",
            "project_id": "commerce",
        },
    )
    assert created.status_code == 201
    project = db.get_project("alpha", "commerce")
    assert project is not None
    assert project["name"] == "commerce"
    assert project["default_retention"] == "project"
