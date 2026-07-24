"""API and scheduler tests for stored, scoped profile health."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from backend import database as db
from backend.profile_health import ProfileHealthResult


@pytest.fixture()
def health_client(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-health-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    main.browser_mgr.running.clear()
    main._profile_health_tasks.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client
    main.browser_mgr.running.clear()
    main._profile_health_tasks.clear()


def _bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-health-secret"}


def _create_user(
    client: TestClient,
    username: str,
    sandbox_id: str,
    permission: str,
) -> str:
    password = f"{username}-password-123"
    response = client.post(
        "/api/access/users",
        headers=_bootstrap_headers(),
        json={
            "username": username,
            "password": password,
            "grants": [{"sandbox_id": sandbox_id, "permission": permission}],
        },
    )
    assert response.status_code == 201
    return password


def _login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def test_profile_health_read_defaults_to_explicit_unavailable(app_client: TestClient):
    profile = db.create_profile("No health yet")

    response = app_client.get(f"/api/profiles/{profile['id']}/health")

    assert response.status_code == 200
    assert response.json() == {
        "profile_id": profile["id"],
        "state": "unavailable",
        "checked_at": None,
        "proxy_configured": False,
        "proxy_reachable": None,
        "outbound_ip_masked": None,
        "proxy_latency_ms": None,
        "proxy_risk_score": None,
        "proxy_authenticity_score": None,
        "fingerprint_consistency_score": None,
        "browser_scan_score": None,
        "warnings": [],
        "blockers": [],
        "error_code": None,
        "sources": {},
    }


def test_profile_health_manual_run_requires_running_profile(app_client: TestClient):
    profile = db.create_profile("Stopped")

    response = app_client.post(f"/api/profiles/{profile['id']}/health/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Profile is not running"


def test_profile_health_is_scoped_and_run_requires_operate(
    health_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    alpha = db.create_profile(
        "Alpha",
        sandbox_id="alpha",
        proxy="http://secret-user:secret-password@proxy.example:8080",
    )
    beta = db.create_profile("Beta", sandbox_id="beta")
    db.upsert_profile_health(
        alpha["id"],
        state="warning",
        checked_at="2026-07-22T12:00:00+00:00",
        proxy_configured=True,
        proxy_reachable=True,
        outbound_ip_masked="203.0.113.x",
        proxy_latency_ms=31.0,
        fingerprint_consistency_score=80,
        warnings=["timezone_mismatch"],
        blockers=[],
        sources={"browser_network": "measured", "fingerprint_consistency": "measured"},
    )

    viewer_password = _create_user(health_client, "health-viewer", "alpha", "view")
    _login(health_client, "health-viewer", viewer_password)
    visible = health_client.get(f"/api/profiles/{alpha['id']}/health")
    assert visible.status_code == 200
    assert visible.json()["outbound_ip_masked"] == "203.0.113.x"
    assert health_client.get(f"/api/profiles/{beta['id']}/health").status_code == 404
    assert health_client.get("/api/profiles/missing/health").status_code == 404
    assert health_client.post(f"/api/profiles/{alpha['id']}/health/run").status_code == 404
    serialized = json.dumps(visible.json())
    assert "secret-password" not in serialized
    assert "proxy.example" not in serialized

    operator_password = _create_user(health_client, "health-operator", "alpha", "operate")
    _login(health_client, "health-operator", operator_password)
    running = SimpleNamespace(context=AsyncMock())
    main.browser_mgr.running[alpha["id"]] = running

    scheduled = MagicMock()

    def _fake_schedule(profile, active, *, force):
        scheduled(profile, active, force=force)
        db.upsert_profile_health(
            profile["id"],
            state="pending",
            proxy_configured=True,
            warnings=[],
            blockers=[],
            sources={},
        )
        return None

    monkeypatch.setattr(main, "_schedule_profile_health", _fake_schedule)
    accepted = health_client.post(f"/api/profiles/{alpha['id']}/health/run")

    assert accepted.status_code == 202
    assert accepted.json()["state"] == "pending"
    scheduled.assert_called_once_with(alpha, running, force=True)
    assert health_client.post(f"/api/profiles/{beta['id']}/health/run").status_code == 404


@pytest.mark.asyncio
async def test_profile_health_scheduler_reuses_inflight_task_and_suppresses_repeat(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    main._profile_health_tasks.clear()
    profile = db.create_profile("Scheduled", proxy="http://proxy.example:8080")
    running = SimpleNamespace(context=AsyncMock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def _run(_profile, _running):
        started.set()
        await release.wait()
        return ProfileHealthResult(
            state="passed",
            checked_at="2026-07-22T12:15:00+00:00",
            proxy_configured=True,
            proxy_reachable=True,
            outbound_ip_masked="203.0.113.x",
            proxy_latency_ms=20.0,
            proxy_risk_score=5,
            proxy_authenticity_score=95,
            fingerprint_consistency_score=100,
            browser_scan_score=99,
            warnings=(),
            blockers=(),
            error_code=None,
            sources={"browser_network": "measured"},
        )

    run_mock = AsyncMock(side_effect=_run)
    monkeypatch.setattr(main.profile_health_probe, "run", run_mock)

    first = main._schedule_profile_health(profile, running, force=False)
    assert first is not None
    await started.wait()
    second = main._schedule_profile_health(profile, running, force=True)
    assert second is first
    assert db.get_profile_health(profile["id"])["state"] in {"pending", "running"}

    release.set()
    await first

    stored = db.get_profile_health(profile["id"])
    assert stored["state"] == "passed"
    assert stored["outbound_ip_masked"] == "203.0.113.x"
    assert run_mock.await_count == 1
    assert main._schedule_profile_health(profile, running, force=False) is None


@pytest.mark.asyncio
async def test_profile_health_probe_failure_is_stored_without_raw_exception(
    tmp_db,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    main._profile_health_tasks.clear()
    profile = db.create_profile("Failing health")
    running = SimpleNamespace(context=AsyncMock())
    monkeypatch.setattr(
        main.profile_health_probe,
        "run",
        AsyncMock(side_effect=RuntimeError("secret proxy credential in exception")),
    )

    task = main._schedule_profile_health(profile, running, force=False)
    assert task is not None
    await task

    stored = db.get_profile_health(profile["id"])
    assert stored["state"] == "failed"
    assert stored["error_code"] == "profile_health_probe_failed"
    assert stored["blockers"] == ["profile_health_probe_failed"]
    assert "secret" not in json.dumps(stored)


def test_successful_launch_schedules_health_without_waiting_for_probe(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    profile = db.create_profile("Launch health")
    running = SimpleNamespace(ws_port=6100, display=100, context=AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "launch", AsyncMock(return_value=running))
    schedule = MagicMock()
    monkeypatch.setattr(main, "_schedule_profile_health", schedule)

    response = app_client.post(f"/api/profiles/{profile['id']}/launch")

    assert response.status_code == 200
    schedule.assert_called_once_with(profile, running, force=False)
