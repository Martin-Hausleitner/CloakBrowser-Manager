"""API tests for sandbox-scoped browser task sessions."""

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


def test_task_session_messages_persist_commands_and_never_fake_assistant(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-operator", "alpha", "interact")
    login(client_access, "alpha-operator", password)

    created = client_access.post(
        "/api/task-sessions",
        json={
            "profile_id": profile["id"],
            "metadata": {"source": "mobile", "api_token": "secret-token"},
        },
    )
    assert created.status_code == 201
    session = created.json()
    assert session["profile_id"] == profile["id"]
    assert session["sandbox_id"] == "alpha"
    assert session["metadata"]["api_token"] == "[redacted]"

    posted = client_access.post(
        f"/api/task-sessions/{session['id']}/messages",
        json={
            "text": "Copy the visible result",
            "profile_id": profile["id"],
            "commands": [
                {
                    "id": "copy-visible",
                    "label": "Copy visible result",
                    "kind": "copy",
                    "scope": "host",
                    "args": {"format": "text"},
                }
            ],
            "metadata": {"password": "do-not-store", "client": "mobile"},
        },
    )
    assert posted.status_code == 201
    message = posted.json()
    assert message["role"] == "user"
    assert message["content"] == "Copy the visible result"
    assert message["metadata"]["password"] == "[redacted]"
    assert message["metadata"]["commands"][0]["kind"] == "copy"
    assert message["metadata"]["commands"][0]["scope"] == "host"

    history = client_access.get(f"/api/task-sessions/{session['id']}/messages")
    assert history.status_code == 200
    assert history.json() == [message]
    assert all(item["role"] != "assistant" for item in history.json())

    events = client_access.get(f"/api/task-sessions/{session['id']}/events")
    assert events.status_code == 200
    appended = [event for event in events.json() if event["type"] == "task_message.appended"][0]
    assert appended["payload"]["command_count"] == 1
    assert appended["payload"]["host_command_count"] == 1
    assert appended["payload"]["server_executed"] is False


def test_view_grant_can_read_history_but_cannot_create_or_send(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    db.append_task_message(session["id"], "user", "stored command", "bootstrap")
    password = create_user(client_access, "alpha-viewer", "alpha", "view")
    login(client_access, "alpha-viewer", password)

    listed = client_access.get(f"/api/task-sessions?profile_id={profile['id']}")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [session["id"]]

    messages = client_access.get(f"/api/task-sessions/{session['id']}/messages")
    assert messages.status_code == 200
    assert messages.json()[0]["content"] == "stored command"

    denied_create = client_access.post(
        "/api/task-sessions",
        json={"profile_id": profile["id"]},
    )
    assert denied_create.status_code == 404
    assert denied_create.json()["detail"] == "Profile not found"

    denied_send = client_access.post(
        f"/api/task-sessions/{session['id']}/messages",
        json={"text": "should not append"},
    )
    assert denied_send.status_code == 404
    assert denied_send.json()["detail"] == "Task session not found"


def test_cross_sandbox_task_sessions_are_indistinguishable_404(
    client_access: TestClient,
):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    beta_session = db.create_task_session(beta["id"], "beta", "bootstrap")
    password = create_user(client_access, "alpha-user", "alpha", "interact")
    login(client_access, "alpha-user", password)

    assert client_access.get(f"/api/task-sessions?profile_id={alpha['id']}").status_code == 200
    denied_profile = client_access.get(f"/api/task-sessions?profile_id={beta['id']}")
    assert denied_profile.status_code == 404
    assert denied_profile.json()["detail"] == "Profile not found"

    denied_session = client_access.get(f"/api/task-sessions/{beta_session['id']}/messages")
    assert denied_session.status_code == 404
    assert denied_session.json()["detail"] == "Task session not found"

    with db.get_db() as conn:
        denied_events = {
            (row["action"], row["sandbox_id"], row["profile_id"], row["outcome"])
            for row in conn.execute(
                """SELECT action, sandbox_id, profile_id, outcome
                FROM access_audit_events WHERE outcome = 'denied'"""
            ).fetchall()
        }
    assert ("task_session.permission.view", "beta", beta["id"], "denied") in denied_events


def test_task_message_rejects_missing_profile_mismatch_and_unknown_commands(
    client_access: TestClient,
):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    password = create_user(client_access, "alpha-operator", "alpha", "interact")
    login(client_access, "alpha-operator", password)

    missing = client_access.post("/api/task-sessions", json={"profile_id": "missing"})
    assert missing.status_code == 404

    session = client_access.post(
        "/api/task-sessions",
        json={"profile_id": alpha["id"]},
    ).json()

    mismatch = client_access.post(
        f"/api/task-sessions/{session['id']}/messages",
        json={"text": "wrong browser", "profile_id": beta["id"]},
    )
    assert mismatch.status_code == 404
    assert mismatch.json()["detail"] == "Task session not found"

    invalid = client_access.post(
        f"/api/task-sessions/{session['id']}/messages",
        json={
            "text": "bad command",
            "commands": [
                {
                    "id": "bad",
                    "label": "Bad",
                    "kind": "shell",
                    "scope": "host",
                }
            ],
        },
    )
    assert invalid.status_code == 422


def test_legacy_commands_route_rejects_more_than_twenty_commands(client_access: TestClient):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    password = create_user(client_access, "alpha-operator", "alpha", "interact")
    login(client_access, "alpha-operator", password)
    session = client_access.post(
        "/api/task-sessions",
        json={"profile_id": alpha["id"]},
    ).json()

    too_many = client_access.post(
        f"/api/task-sessions/{session['id']}/commands",
        json={
            "content": "run pinned actions",
            "commands": [
                {
                    "id": f"cmd-{index}",
                    "label": f"Command {index}",
                    "kind": "screenshot",
                    "scope": "ui",
                }
                for index in range(21)
            ],
        },
    )

    assert too_many.status_code == 422


def test_task_session_history_follows_profile_sandbox_move(client_access: TestClient):
    profile = db.create_profile("Movable browser", sandbox_id="alpha")
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    db.append_task_message(session["id"], "user", "move-safe history", "bootstrap")

    updated = client_access.put(
        f"/api/profiles/{profile['id']}",
        headers=bootstrap_headers(),
        json={"sandbox_id": "beta"},
    )
    assert updated.status_code == 200
    assert updated.json()["sandbox_id"] == "beta"
    assert db.get_task_session(session["id"])["sandbox_id"] == "beta"

    old_password = create_user(client_access, "old-alpha-viewer", "alpha", "view")
    login(client_access, "old-alpha-viewer", old_password)
    old_view = client_access.get(f"/api/task-sessions/{session['id']}/messages")
    assert old_view.status_code == 404
    assert old_view.json()["detail"] == "Task session not found"

    new_password = create_user(client_access, "new-beta-viewer", "beta", "view")
    login(client_access, "new-beta-viewer", new_password)
    new_view = client_access.get(f"/api/task-sessions/{session['id']}/messages")
    assert new_view.status_code == 200
    assert new_view.json()[0]["content"] == "move-safe history"
