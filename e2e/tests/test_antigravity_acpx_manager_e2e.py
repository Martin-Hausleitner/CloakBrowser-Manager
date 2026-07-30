"""VCVM E2E coverage for Antigravity/ACP/ACPX managed profile routing.

Live checks are intentionally read-only. Mutating mode-routing scenarios run
against the Manager ASGI app with a temporary database so they do not leave
profiles, task sessions, runs, or proxy inventory in the deployed VCVM service.
"""

from __future__ import annotations

import json
import ssl
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_mock_cloakbrowser = types.ModuleType("cloakbrowser")
_mock_cloakbrowser.launch_persistent_context_async = AsyncMock()  # type: ignore[attr-defined]

_mock_config = types.ModuleType("cloakbrowser.config")
_mock_config.CHROMIUM_VERSION = "0.0.0-test"  # type: ignore[attr-defined]

sys.modules.setdefault("cloakbrowser", _mock_cloakbrowser)
sys.modules.setdefault("cloakbrowser.config", _mock_config)

from backend import database as db  # noqa: E402

BOOTSTRAP_TOKEN = "bootstrap-test-secret"
LIVE_BASE_URL = "https://vcvm.tail6a40cd.ts.net"
SECRET_MARKERS = ("raw-secret", "top-secret", "alice:top-secret", "password", "cookie")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "profiles.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    return tmp_path


@pytest.fixture()
def manager_client(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", BOOTSTRAP_TOKEN)
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_ID", "browser-use-worker-1")
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", "cbm_worker_" + ("ab" * 32))
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    with TestClient(main.app) as client:
        yield client


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def seed_passed_health(profile_id: str, *, proxy_configured: bool = False) -> None:
    db.upsert_profile_health(
        profile_id,
        state="passed",
        checked_at=datetime.now(timezone.utc).isoformat(),
        proxy_configured=proxy_configured,
        proxy_reachable=True if proxy_configured else None,
        outbound_ip_masked="203.0.113.x" if proxy_configured else None,
        proxy_latency_ms=42.0 if proxy_configured else None,
        proxy_risk_score=15 if proxy_configured else None,
        proxy_authenticity_score=91 if proxy_configured else 88,
        fingerprint_consistency_score=100,
        browser_scan_score=94,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={"proxy_authenticity": "measured"},
    )


def create_task_session(client: TestClient, profile_id: str, metadata: dict[str, Any]) -> str:
    response = client.post(
        "/api/task-sessions",
        headers=auth_headers(),
        json={
            "profile_id": profile_id,
            "title": "Inspect https://example.com",
            "metadata": metadata,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def create_managed_run(
    client: TestClient,
    *,
    session_id: str,
    profile_id: str,
    agent: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/task-sessions/{session_id}/runs",
        headers=auth_headers(),
        json={
            "harness": "acpx",
            "agent": agent,
            "task": "Inspect https://example.com and report the heading",
            "profile_id": profile_id,
            "allowed_origins": ["https://example.com"],
            "timeout_seconds": 360,
            "model_alias": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def assert_no_secret_markers(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True).lower()
    for marker in SECRET_MARKERS:
        assert marker not in serialized


def live_json(path: str) -> tuple[int, dict[str, Any]]:
    request = Request(f"{LIVE_BASE_URL}{path}", headers={"Accept": "application/json"})
    context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=10, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_live_auth_status_is_visible_without_secret_fields_read_only():
    status, body = live_json("/api/auth/status")

    assert status == 200
    assert body["auth_required"] is True
    assert body["access_control_enabled"] is True
    assert body["authenticated"] is False
    assert body["identity"] is None
    assert_no_secret_markers(body)


def test_live_openapi_exposes_redacted_proxy_and_health_contracts_read_only():
    status, body = live_json("/openapi.json")

    assert status == 200
    assert "/api/auth/status" in body["paths"]
    assert "/api/profiles/{profile_id}/health" in body["paths"]
    assert "/api/proxies" in body["paths"]
    proxy_schema = body["components"]["schemas"]["ProxyInventoryItem"]["properties"]
    health_schema = body["components"]["schemas"]["ProfileHealthResponse"]["properties"]
    assert {"host_masked", "username_masked", "has_credentials"} <= set(proxy_schema)
    assert {"proxy_configured", "outbound_ip_masked", "proxy_authenticity_score"} <= set(health_schema)
    assert "proxy_url" not in proxy_schema
    assert "password" not in proxy_schema


def test_acpx_mode_creates_run_bound_to_selected_acpx_profile(manager_client: TestClient):
    profile = db.create_profile("E2E ACPX profile", sandbox_id="e2e", harness="acpx")
    seed_passed_health(profile["id"])
    session_id = create_task_session(
        manager_client,
        profile["id"],
        {"source": "agent-browser-workspace", "harness": "acpx", "agent": "opencode"},
    )

    run = create_managed_run(
        manager_client,
        session_id=session_id,
        profile_id=profile["id"],
        agent="opencode",
    )

    assert run["harness"] == "acpx"
    assert run["agent"] == "opencode"
    assert run["profile_id_snapshot"] == profile["id"]
    assert run["model_alias"] is None
    assert run["health_snapshot"]["proxy_configured"] is False
    assert_no_secret_markers(run)


def test_antigravity_mode_creates_acpx_claude_run_bound_to_selected_profile(manager_client: TestClient):
    profile = db.create_profile("E2E Antigravity profile", sandbox_id="e2e", harness="antigravity")
    seed_passed_health(profile["id"])
    session_id = create_task_session(
        manager_client,
        profile["id"],
        {
            "source": "agent-browser-workspace",
            "harness": "acpx",
            "agent": "claude",
            "mode": "antigravity",
        },
    )

    run = create_managed_run(
        manager_client,
        session_id=session_id,
        profile_id=profile["id"],
        agent="claude",
    )

    assert run["harness"] == "acpx"
    assert run["agent"] == "claude"
    assert run["profile_id_snapshot"] == profile["id"]
    assert run["status"] == "queued"
    assert run["health_decision"]["allowed"] is True
    assert_no_secret_markers(run)


def test_antigravity_mode_rejects_non_claude_acpx_agent(manager_client: TestClient):
    profile = db.create_profile("E2E Antigravity strict profile", sandbox_id="e2e", harness="antigravity")
    seed_passed_health(profile["id"])
    session_id = create_task_session(
        manager_client,
        profile["id"],
        {"source": "agent-browser-workspace", "harness": "acpx", "agent": "cursor", "mode": "antigravity"},
    )

    response = manager_client.post(
        f"/api/task-sessions/{session_id}/runs",
        headers=auth_headers(),
        json={
            "harness": "acpx",
            "agent": "cursor",
            "task": "Inspect https://example.com",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "model_alias": None,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Antigravity profiles require ACPX with Claude"


def test_proxy_inventory_api_returns_status_without_proxy_credentials(manager_client: TestClient):
    response = manager_client.post(
        "/api/proxies/ingest",
        headers=auth_headers(),
        json={"lines": ["198.51.100.22:8080:alice:top-secret"]},
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]

    assert item["host_masked"].endswith(".x.x")
    assert item["username_masked"] == "a***e"
    assert item["has_credentials"] is True
    assert "proxy_url" not in item
    assert_no_secret_markers(item)
