"""API tests for proxy inventory ingest, check, and auto profile creation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture()
def admin_client(app_client, monkeypatch: pytest.MonkeyPatch):
    """Authenticate as bootstrap admin for access-controlled routes."""
    monkeypatch.setenv("AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    # Re-import-sensitive flags are already set at import; patch module values.
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    app_client.cookies.set("auth_token", "test-admin-token-0123456789abcdef")
    return app_client


def test_ingest_and_list_proxies_redacted(admin_client, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)

    response = admin_client.post(
        "/api/proxies/ingest",
        json={"lines": ["10.0.0.8:8080:alice:top-secret", "bad-line", "10.0.0.9:8080:bob:other-secret"]},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["created"] == 2
    assert payload["rejected"] == 1
    body = response.text
    assert "top-secret" not in body
    assert "other-secret" not in body
    assert "10.0.0.8" not in body

    listed = admin_client.get("/api/proxies")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 2
    assert all(item["host_masked"].endswith(".x.x") for item in items)
    assert "proxy_url" not in items[0]


def test_create_profile_from_proxy_auto_aligns(admin_client, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(
        main,
        "_proxychecker_check",
        AsyncMock(
            return_value={
                "reachable": True,
                "latency_ms": 20.0,
                "risk_score": 5,
                "authenticity_score": 95,
                "country_code": "AT",
                "timezone_hint": "Europe/Vienna",
                "locale_hint": "de-AT",
                "warnings": [],
                "blockers": [],
                "check_state": "passed",
            }
        ),
    )

    ingest = admin_client.post(
        "/api/proxies/ingest",
        json={"lines": ["10.1.2.3:9050:userx:passx"]},
    )
    assert ingest.status_code == 201
    proxy_id = ingest.json()["items"][0]["id"]

    created = admin_client.post(
        f"/api/proxies/{proxy_id}/profiles",
        json={"harness": "browser-use", "project_id": "proxied", "name": "Auto AT"},
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["name"] == "Auto AT"
    assert profile["project_id"] == "proxied"
    assert profile["folder_path"] == "auto"
    assert profile["harness"] == "browser-use"
    assert profile["timezone"] == "Europe/Vienna"
    assert profile["locale"] == "de-AT"
    assert profile["geoip"] is True
    # Admin may see proxy; ensure password is not echoed in JSON if redacted later.
    assert "passx" not in created.text or profile.get("proxy") is not None
