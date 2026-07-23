"""Agent/CLI control plane: operate-scoped profile CRUD without UI or admin token."""

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


def _create_agent(client: TestClient, *, grants: list[dict[str, str]], name: str = "Control agent") -> dict:
    created = client.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": name,
            "paperclip_agent_id": f"paperclip-{name.lower().replace(' ', '-')}",
            "grants": grants,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_operate_agent_can_create_update_launch_status_and_open_links(
    client_access: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from backend import main

    agent = _create_agent(
        client_access,
        grants=[
            {"sandbox_id": "agents", "permission": "operate"},
            {"sandbox_id": "agents", "permission": "automate"},
        ],
    )
    headers = {"Authorization": f"Bearer {agent['api_key']}"}

    created = client_access.post(
        "/api/profiles",
        headers=headers,
        json={
            "name": "Agent-built profile",
            "sandbox_id": "agents",
            "harness": "codex",
            "project_id": "automation",
            "folder_path": "external/agents",
            "timezone": "Europe/Vienna",
            "locale": "de-AT",
            "geoip": True,
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["name"] == "Agent-built profile"
    assert profile["sandbox_id"] == "agents"
    assert profile["harness"] == "codex"
    # Secrets stay redacted for non-admin callers even after agent create.
    assert profile["proxy"] is None
    assert profile["user_data_dir"] == ""

    updated = client_access.put(
        f"/api/profiles/{profile['id']}",
        headers=headers,
        json={"harness": "antigravity", "pinned": True, "notes": "set-by-agent"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["harness"] == "antigravity"
    assert updated.json()["pinned"] is True
    assert updated.json()["notes"] is None  # redacted on read

    forbidden_sandbox = client_access.post(
        "/api/profiles",
        headers=headers,
        json={"name": "Outside", "sandbox_id": "finance"},
    )
    assert forbidden_sandbox.status_code == 403

    async def fake_launch(profile_row):
        from types import SimpleNamespace

        running = SimpleNamespace(ws_port=6100, cdp_port=9222, display=99)
        main.browser_mgr.running[str(profile_row["id"])] = running
        return running

    monkeypatch.setattr(main.browser_mgr, "launch", fake_launch)
    monkeypatch.setattr(main, "_schedule_profile_health", lambda *args, **kwargs: None)

    launched = client_access.post(f"/api/profiles/{profile['id']}/launch", headers=headers)
    assert launched.status_code == 200, launched.text
    body = launched.json()
    assert body["status"] == "running"
    assert body["cdp_url"] == f"/api/profiles/{profile['id']}/cdp"
    assert body["links"]["local"]["vnc_ws_url"].endswith(f"/api/profiles/{profile['id']}/vnc")
    assert body["links"]["local"]["cdp_http_url"].endswith(f"/api/profiles/{profile['id']}/cdp")

    status = client_access.get(f"/api/profiles/{profile['id']}/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    assert status.json()["links"]["local"]["vnc_ws_url"].endswith(
        f"/api/profiles/{profile['id']}/vnc"
    )

    open_links = client_access.get(
        f"/api/profiles/{profile['id']}/open-links?prefer=local",
        headers=headers,
    )
    assert open_links.status_code == 200
    links = open_links.json()
    assert links["cdp_url"].endswith(f"/api/profiles/{profile['id']}/cdp")
    assert links["websocket_url"].endswith(f"/api/profiles/{profile['id']}/vnc")
    assert links["vnc_fullscreen_url"].endswith("fullscreen=1")
    assert "/session/" in links["cdp_fullscreen_url"]

    sandboxes = client_access.get("/api/access/sandboxes", headers=headers)
    assert sandboxes.status_code == 200
    assert any(row["sandbox_id"] == "agents" for row in sandboxes.json())

    stopped = client_access.post(f"/api/profiles/{profile['id']}/stop", headers=headers)
    assert stopped.status_code == 200
    deleted = client_access.delete(f"/api/profiles/{profile['id']}", headers=headers)
    assert deleted.status_code == 200


def test_view_agent_cannot_create_or_launch_profiles(client_access: TestClient):
    db.create_profile("Existing", sandbox_id="alpha")
    agent = _create_agent(
        client_access,
        name="Viewer agent",
        grants=[{"sandbox_id": "alpha", "permission": "view"}],
    )
    headers = {"Authorization": f"Bearer {agent['api_key']}"}

    assert (
        client_access.post(
            "/api/profiles",
            headers=headers,
            json={"name": "Nope", "sandbox_id": "alpha"},
        ).status_code
        == 403
    )
    listed = client_access.get("/api/profiles", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    profile_id = listed.json()[0]["id"]
    assert client_access.post(f"/api/profiles/{profile_id}/launch", headers=headers).status_code == 404


def test_operate_agent_bulk_organize_is_sandbox_scoped(client_access: TestClient):
    alpha = db.create_profile("Alpha", sandbox_id="alpha", project_id="old")
    beta = db.create_profile("Beta", sandbox_id="beta", project_id="old")
    agent = _create_agent(
        client_access,
        name="Organize agent",
        grants=[{"sandbox_id": "alpha", "permission": "operate"}],
    )
    headers = {"Authorization": f"Bearer {agent['api_key']}"}

    denied = client_access.post(
        "/api/profiles/bulk-organize",
        headers=headers,
        json={"profile_ids": [alpha["id"], beta["id"]], "project_id": "new"},
    )
    assert denied.status_code == 404

    moved = client_access.post(
        "/api/profiles/bulk-organize",
        headers=headers,
        json={"profile_ids": [alpha["id"]], "project_id": "commerce", "folder_path": "ops"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()[0]["project_id"] == "commerce"
    assert moved.json()[0]["folder_path"] == "ops"
