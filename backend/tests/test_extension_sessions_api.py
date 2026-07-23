"""API tests for Chrome-extension catalog and one-click session open links."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture()
def admin_client(app_client, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    app_client.cookies.set("auth_token", "test-admin-token-0123456789abcdef")
    return app_client


def test_extension_catalog_lists_profiles_and_endpoints(admin_client, sample_profile, monkeypatch):
    monkeypatch.setenv("CLOUD_BASE_URL", "https://cloud.example")
    response = admin_client.get(
        "/api/extension/catalog",
        headers={"Host": "127.0.0.1:18117"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["bases"]["local"].startswith("http://127.0.0.1:18117")
    assert payload["bases"]["cloud"] == "https://cloud.example"
    assert payload["endpoints"]["open_session"] == "/api/extension/sessions/open"
    assert payload["endpoints"]["defaults"] == "/api/extension/defaults"
    assert payload["endpoints"]["templates"] == "/api/extension/templates"
    assert any(item["id"] == sample_profile["id"] for item in payload["profiles"])
    assert payload["capabilities"]["can_list_proxies"] is True
    body = response.text
    assert "password" not in body.lower() or "has_credentials" in body

    posted = admin_client.post("/api/extension/catalog", headers={"Host": "127.0.0.1:18117"})
    assert posted.status_code == 200
    assert posted.json()["endpoints"]["catalog"] == "/api/extension/catalog"


def test_extension_defaults_and_templates_discoverable(admin_client):
    defaults = admin_client.get("/api/extension/defaults")
    assert defaults.status_code == 200, defaults.text
    payload = defaults.json()
    assert "extensions" in payload
    assert "items" in payload
    assert isinstance(payload["extensions"], list)
    if payload["extensions"]:
        assert "icon_url" in payload["extensions"][0]
        assert "id" in payload["extensions"][0]
        assert "name" in payload["extensions"][0]

    templates = admin_client.get("/api/extension/templates")
    assert templates.status_code == 200, templates.text
    tpayload = templates.json()
    assert "templates" in tpayload
    assert tpayload["create_profile_path"] == "/api/profiles"


def test_extension_open_session_launches_and_returns_links(admin_client, sample_profile, monkeypatch):
    from backend import main

    running = MagicMock()
    running.ws_port = 6101
    running.display = 101
    monkeypatch.setattr(main.browser_mgr, "launch", AsyncMock(return_value=running))
    monkeypatch.setattr(main.browser_mgr, "running", {})
    monkeypatch.setattr(main, "_schedule_profile_health", MagicMock())
    monkeypatch.setenv("CLOUD_BASE_URL", "https://cloud.example")

    response = admin_client.post(
        "/api/extension/sessions/open",
        headers={"Host": "127.0.0.1:18117"},
        json={
            "profile_id": sample_profile["id"],
            "launch": True,
            "prefer": "local",
            "mode": "cdp",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["launched"] is True
    assert payload["status"] == "running"
    assert payload["mode"] == "cdp"
    assert payload["open_url"] == f"http://127.0.0.1:18117/session/{sample_profile['id']}/live"
    assert payload["links"]["local"]["vnc_ws_url"].endswith(f"/api/profiles/{sample_profile['id']}/vnc")
    assert payload["links"]["local"]["vnc_fullscreen_url"].endswith("&view=vnc&fullscreen=1")
    assert payload["links"]["cloud"]["session_viewer_url"].startswith("https://cloud.example/?profile=")
    assert payload["cdp_url"] == f"/api/profiles/{sample_profile['id']}/cdp"
    assert "secret" not in response.text


def test_launch_response_includes_open_links(admin_client, sample_profile, monkeypatch):
    from backend import main

    running = MagicMock()
    running.ws_port = 6102
    running.display = 102
    monkeypatch.setattr(main.browser_mgr, "launch", AsyncMock(return_value=running))
    monkeypatch.setattr(main, "_schedule_profile_health", MagicMock())

    response = admin_client.post(
        f"/api/profiles/{sample_profile['id']}/launch",
        headers={"Host": "127.0.0.1:18117"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "/session/" in payload["links"]["open_url"] or "?profile=" in payload["links"]["open_url"]
    assert payload["links"]["local"]["status_path"] == f"/api/profiles/{sample_profile['id']}/status"
    assert payload["links"]["local"]["live_metrics_path"] == (
        f"/api/profiles/{sample_profile['id']}/live-metrics"
    )


def test_status_includes_open_links_without_launch(admin_client, sample_profile):
    response = admin_client.get(
        f"/api/profiles/{sample_profile['id']}/status",
        headers={"Host": "127.0.0.1:18117"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["links"]["local"]["session_viewer_url"].endswith(f"?profile={sample_profile['id']}")


def test_profile_open_links_flat_compatibility(admin_client, sample_profile, monkeypatch):
    monkeypatch.setenv("CLOUD_BASE_URL", "https://cloud.example")
    response = admin_client.get(
        f"/api/profiles/{sample_profile['id']}/open-links?prefer=cloud&mode=cdp",
        headers={"Host": "127.0.0.1:18117"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["cloud_url"] == f"https://cloud.example/?profile={sample_profile['id']}"
    assert payload["local_url"] == f"http://127.0.0.1:18117/?profile={sample_profile['id']}"
    assert payload["live_url"] == f"https://cloud.example/session/{sample_profile['id']}/live"
    assert payload["vnc_fullscreen_url"].endswith("&view=vnc&fullscreen=1")
    assert payload["open_url"] == payload["live_url"]
    assert "secret" not in response.text


def test_live_metrics_roundtrip(admin_client, sample_profile):
    posted = admin_client.post(
        f"/api/profiles/{sample_profile['id']}/live-metrics",
        json={
            "transport": "cdp",
            "connection_state": "connected",
            "fps": 28.5,
            "rtt_ms": 42.0,
            "reconnect_count": 1,
            "dropped_frames": 3,
        },
    )
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["transport"] == "cdp"
    assert body["fps"] == 28.5
    assert body["rtt_ms"] == 42.0

    got = admin_client.get(f"/api/profiles/{sample_profile['id']}/live-metrics")
    assert got.status_code == 200
    assert got.json()["connection_state"] == "connected"
