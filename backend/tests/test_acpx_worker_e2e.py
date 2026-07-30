from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db
from scripts.acpx_worker import AcpxManagerClient, AcpxWorker, AcpxWorkerConfig


WORKER_ID = "browser-use-worker-1"
WORKER_KEY = "cbm_worker_" + ("ab" * 32)


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_ID", WORKER_ID)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", WORKER_KEY)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(
        main.worker_runtime_service,
        "_clock",
        lambda: datetime.now(timezone.utc),
    )
    with TestClient(main.app) as client:
        main.worker_runtime_service.sync_configured_worker()
        yield client


class ClientTransport:
    def __init__(self, client: TestClient):
        self.client = client

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        return self.client.request(
            method,
            path,
            headers=kwargs.get("headers"),
            json=kwargs.get("json"),
            content=kwargs.get("content"),
        )


class ContractRuntime:
    async def validate_version(self):
        return None

    async def discover_agents(self):
        await self.validate_version()
        return ("cursor",)

    async def ensure_session(self, *, cwd, agent, session_name, environment):
        assert agent == "cursor"
        assert session_name.startswith("cbm-")
        assert "CBM_RUN_CAPABILITY_FILE" in environment

    async def run_prompt(self, *, emit, environment, **kwargs):
        capability = Path(environment["CBM_RUN_CAPABILITY_FILE"])
        assert capability.stat().st_mode & 0o077 == 0
        assert capability.read_text(encoding="utf-8").startswith("cbm_run_")
        assert json.loads(environment["CBM_ALLOWED_ORIGINS"]) == ["https://example.com"]
        await emit(
            {
                "idempotency_key": "acpx-e2e-1",
                "kind": "summary",
                "summary": "ACPX Manager E2E completed",
                "payload": {"text": "ACPX Manager E2E completed"},
            }
        )
        return "ACPX Manager E2E completed"

    async def cancel(self, **kwargs):
        return None


def bootstrap_headers():
    return {"Authorization": "Bearer bootstrap-test-secret"}


def seed_passed_health(profile_id: str) -> None:
    db.upsert_profile_health(
        profile_id,
        state="passed",
        checked_at=datetime.now(timezone.utc).isoformat(),
        proxy_configured=False,
        proxy_reachable=True,
        proxy_authenticity_score=88,
        fingerprint_consistency_score=100,
        browser_scan_score=90,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={
            "fingerprint_consistency": "measured",
            "browser_scan": "measured",
        },
    )


def make_config(tmp_path: Path) -> AcpxWorkerConfig:
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)
    capability_dir = tmp_path / "capabilities"
    capability_dir.mkdir(mode=0o700)
    return AcpxWorkerConfig(
        manager_url="http://manager.test",
        worker_id=WORKER_ID,
        worktree=tmp_path,
        permission_policy=policy,
        mcp_config=mcp,
        capability_dir=capability_dir,
        token=WORKER_KEY,
        heartbeat_interval_seconds=0.01,
    )


def test_acpx_worker_manager_lifecycle_e2e(client_access: TestClient, tmp_path: Path):
    profile = db.create_profile("ACPX E2E", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    created = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "acpx",
            "agent": "cursor",
            "task": "Inspect the browser",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "timeout_seconds": 30,
        },
    )
    assert created.status_code == 201, created.text

    manager = AcpxManagerClient(
        "http://manager.test",
        token=WORKER_KEY,
        http=ClientTransport(client_access),
    )
    claimed = manager.claim()
    assert claimed and claimed["agent"] == "cursor"
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=ContractRuntime())

    assert asyncio.run(worker.execute_claim(claimed)) == {"status": "succeeded"}
    run = client_access.get(
        f"/api/task-runs/{created.json()['id']}", headers=bootstrap_headers()
    )
    assert run.status_code == 200
    assert run.json()["status"] == "succeeded"
    assert run.json()["agent"] == "cursor"
    outputs = client_access.get(
        f"/api/task-runs/{created.json()['id']}/outputs", headers=bootstrap_headers()
    )
    assert outputs.status_code == 200
    assert outputs.json()[0]["kind"] == "summary"
    assert outputs.json()[0]["summary"] == "ACPX Manager E2E completed"
    assert list((tmp_path / "capabilities").iterdir()) == []
