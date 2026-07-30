"""Atomic FIFO claim eligibility and heartbeat/retry contracts."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db


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


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def worker_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_KEY}"}


def seed_provider_preflight(
    client: TestClient,
    *,
    provider: str = "grok",
    transport: str = "acp",
    ready: bool = True,
    reason_code: str = "ready",
    model_aliases: list[str] | None = None,
) -> None:
    response = client.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={
            "provider": provider,
            "transport": transport,
            "ready": ready,
            "reason_code": reason_code,
            "model_aliases": model_aliases if model_aliases is not None else [],
        },
    )
    assert response.status_code == 204, response.text


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


def create_run(
    client: TestClient,
    *,
    profile_id: str,
    sandbox_id: str = "alpha",
    task: str = "Work",
    harness: str = "browser-use",
    agent: str | None = None,
    launch_if_stopped: bool = False,
) -> dict:
    seed_passed_health(profile_id)
    session = db.create_task_session(profile_id, sandbox_id, "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": harness,
            **({"agent": agent} if agent is not None else {}),
            "task": task,
            "profile_id": profile_id,
            "launch_if_stopped": launch_if_stopped,
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def create_routed_run(
    client: TestClient,
    *,
    profile_id: str,
    provider: str = "grok",
    transport: str = "acp",
    agent: str = "grok-build",
    provider_model_alias: str | None = None,
    run_model_alias: str | None = None,
    launch_if_stopped: bool = False,
) -> dict:
    seed_passed_health(profile_id)
    session = db.create_task_session(profile_id, "alpha", "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "acpx",
            "agent": agent,
            "task": "Use routed browser tools",
            "profile_id": profile_id,
            "launch_if_stopped": launch_if_stopped,
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
            "model_alias": run_model_alias,
            "provider": {
                "id": provider,
                "transport": transport,
                "model_alias": provider_model_alias,
            },
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
            "routing_policy": {
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 2,
            },
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def claim_routed_run(client: TestClient, run_id: str) -> dict:
    claimed = client.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["id"] == run_id
    return claimed.json()


def routed_capability_row(run_id: str) -> dict:
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, error_code, error_message
            FROM task_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(row)


def test_acpx_agent_persists_and_is_returned_to_filtered_worker(
    client_access: TestClient,
):
    profile = db.create_profile("ACPX", sandbox_id="alpha")
    run = create_run(
        client_access,
        profile_id=profile["id"],
        harness="acpx",
        agent="cursor",
    )
    assert run["agent"] == "cursor"

    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["agent"] == "cursor"


def test_claim_204_when_no_work(client_access: TestClient):
    resp = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert resp.status_code == 204
    assert resp.content in {b"", b"null"}


def test_filtered_claim_reports_redacted_harness_presence(client_access: TestClient):
    from backend import main

    assert client_access.get("/api/task-harnesses/acpx/presence").status_code == 401
    missing = client_access.get(
        "/api/task-harnesses/acpx/presence",
        headers=bootstrap_headers(),
    )
    assert missing.status_code == 200
    assert missing.json() == {
        "harness": "acpx",
        "worker_seen_recently": False,
        "state": "unavailable",
        "last_seen_at": None,
        "reason": "No authenticated ACPX worker has checked in",
    }

    poll = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    )
    assert poll.status_code == 204

    ready = client_access.get(
        "/api/task-harnesses/acpx/presence",
        headers=bootstrap_headers(),
    )
    assert ready.status_code == 200
    body = ready.json()
    assert body["harness"] == "acpx"
    assert body["worker_seen_recently"] is True
    assert body["state"] == "polling"
    assert body["last_seen_at"]
    assert body["reason"] is None
    assert "worker_id" not in body

    last_seen = datetime.fromisoformat(body["last_seen_at"])
    main.worker_runtime_service._clock = lambda: last_seen + timedelta(seconds=46)
    stale = client_access.get(
        "/api/task-harnesses/acpx/presence",
        headers=bootstrap_headers(),
    )
    assert stale.status_code == 200
    assert stale.json()["worker_seen_recently"] is False
    assert stale.json()["state"] == "stale"
    assert stale.json()["reason"] == "The last authenticated ACPX worker check-in is stale"


def test_acpx_preflight_is_agent_scoped_redacted_and_expires(client_access: TestClient):
    from backend import main

    reported = client_access.post(
        "/internal/task-harnesses/acpx/preflights",
        headers=worker_headers(),
        json={"agent": "cursor", "ready": True, "reason_code": "ok"},
    )
    assert reported.status_code == 204

    auth_required = client_access.post(
        "/internal/task-harnesses/acpx/preflights",
        headers=worker_headers(),
        json={
            "agent": "codex",
            "ready": False,
            "reason_code": "auth_required",
        },
    )
    assert auth_required.status_code == 204

    response = client_access.get(
        "/api/task-harnesses/acpx/preflights",
        headers=bootstrap_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["harness"] == "acpx"
    agents = {item["agent"]: item for item in body["agents"]}
    assert agents["cursor"]["state"] == "ready"
    assert agents["cursor"]["ready"] is True
    assert agents["cursor"]["reason_code"] == "ok"
    assert agents["codex"]["state"] == "failed"
    assert agents["codex"]["ready"] is False
    assert agents["codex"]["reason_code"] == "auth_required"
    assert "worker_id" not in agents["cursor"]
    assert agents["claude"] == {
        "agent": "claude",
        "ready": False,
        "state": "unavailable",
        "reason_code": "not_checked",
        "checked_at": None,
    }

    checked_at = datetime.fromisoformat(agents["cursor"]["checked_at"])
    main.worker_runtime_service._clock = lambda: checked_at + timedelta(seconds=301)
    expired = client_access.get(
        "/api/task-harnesses/acpx/preflights",
        headers=bootstrap_headers(),
    ).json()
    expired_agents = {item["agent"]: item for item in expired["agents"]}
    assert expired_agents["cursor"]["state"] == "stale"
    assert expired_agents["cursor"]["ready"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"agent": "cursor", "ready": True, "reason_code": "auth_required"},
        {"agent": "cursor", "ready": False, "reason_code": "ok"},
        {"agent": "../cursor", "ready": True, "reason_code": "ok"},
        {
            "agent": "cursor",
            "ready": True,
            "reason_code": "ok",
            "detail": "must not be persisted",
        },
    ],
)
def test_acpx_preflight_rejects_invalid_payloads(
    client_access: TestClient,
    payload: dict[str, object],
):
    response = client_access.post(
        "/internal/task-harnesses/acpx/preflights",
        headers=worker_headers(),
        json=payload,
    )
    assert response.status_code == 422
    with db.get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_harness_preflights"
        ).fetchone()[0] == 0


def test_acpx_preflight_enforces_worker_and_public_auth(client_access: TestClient):
    payload = {"agent": "cursor", "ready": True, "reason_code": "ok"}
    assert client_access.get("/api/task-harnesses/acpx/preflights").status_code == 401
    assert client_access.post(
        "/internal/task-harnesses/acpx/preflights",
        headers=bootstrap_headers(),
        json=payload,
    ).status_code == 401
    assert client_access.post(
        "/internal/task-harnesses/acpx/preflights",
        headers={"Authorization": "Bearer cbm_worker_invalid"},
        json=payload,
    ).status_code == 401


def test_acpx_preflight_uses_newest_active_worker_result(client_access: TestClient):
    from backend import main

    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: now
    with db.get_db() as conn:
        conn.executemany(
            """
            INSERT INTO worker_identities
                (id, key_digest, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("worker-old", "digest-old", 1, now.isoformat(), now.isoformat()),
                ("worker-new", "digest-new", 1, now.isoformat(), now.isoformat()),
                ("worker-inactive", "digest-inactive", 0, now.isoformat(), now.isoformat()),
            ],
        )
        conn.executemany(
            """
            INSERT INTO worker_harness_preflights
                (worker_id, harness, agent, ready, reason_code, checked_at)
            VALUES (?, 'acpx', 'cursor', ?, ?, ?)
            """,
            [
                ("worker-old", 0, "auth_required", (now - timedelta(seconds=30)).isoformat()),
                ("worker-new", 1, "ok", (now - timedelta(seconds=10)).isoformat()),
                ("worker-inactive", 0, "protocol_error", (now - timedelta(seconds=1)).isoformat()),
            ],
        )
        conn.commit()

    cursor = {
        item["agent"]: item
        for item in main.worker_runtime_service.agent_preflights("acpx")["agents"]
    }["cursor"]
    assert cursor["ready"] is True
    assert cursor["reason_code"] == "ok"
    assert cursor["checked_at"] == (now - timedelta(seconds=10)).isoformat()


def test_unfiltered_claim_still_picks_oldest_any_harness(client_access: TestClient):
    profile_a = db.create_profile("A", sandbox_id="alpha")
    profile_b = db.create_profile("B", sandbox_id="alpha")
    older_codex = create_run(
        client_access, profile_id=profile_a["id"], task="codex-first", harness="codex"
    )
    create_run(
        client_access,
        profile_id=profile_b["id"],
        task="browser-second",
        harness="browser-use",
    )

    claimed = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["id"] == older_codex["id"]
    assert body["harness"] == "codex"


def test_filtered_claim_picks_matching_harness_across_profiles(
    client_access: TestClient,
):
    profile_a = db.create_profile("A", sandbox_id="alpha")
    profile_b = db.create_profile("B", sandbox_id="alpha")
    create_run(
        client_access, profile_id=profile_a["id"], task="codex-older", harness="codex"
    )
    browser_run = create_run(
        client_access,
        profile_id=profile_b["id"],
        task="browser-newer",
        harness="browser-use",
    )

    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "browser-use"},
    )
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["id"] == browser_run["id"]
    assert body["harness"] == "browser-use"


def test_filtered_claim_blocks_when_same_profile_head_is_other_harness(
    client_access: TestClient,
):
    profile = db.create_profile("Mixed", sandbox_id="alpha")
    codex_head = create_run(
        client_access, profile_id=profile["id"], task="codex-head", harness="codex"
    )
    browser_later = create_run(
        client_access,
        profile_id=profile["id"],
        task="browser-later",
        harness="browser-use",
    )

    empty = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "browser-use"},
    )
    assert empty.status_code == 204

    still_codex = client_access.get(
        f"/api/task-runs/{codex_head['id']}",
        headers=bootstrap_headers(),
    ).json()
    still_browser = client_access.get(
        f"/api/task-runs/{browser_later['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert still_codex["status"] == "queued"
    assert still_codex["claimed_by"] is None
    assert still_browser["status"] == "queued"
    assert still_browser["claimed_by"] is None
    assert still_browser["claim_eligible_at"] is None


def test_invalid_harness_filter_returns_422_without_queue_leak(
    client_access: TestClient,
):
    profile = db.create_profile("Secret queue", sandbox_id="alpha")
    run = create_run(client_access, profile_id=profile["id"], task="hidden")

    resp = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "not-a-real-harness"},
    )
    assert resp.status_code == 422
    text = resp.text.lower()
    assert run["id"].lower() not in text
    assert "queued" not in text
    assert "claim_eligible" not in text
    assert profile["id"].lower() not in text


def test_filtered_concurrent_claims_single_winner(client_access: TestClient):
    profile = db.create_profile("Race filter", sandbox_id="alpha")
    create_run(client_access, profile_id=profile["id"], harness="browser-use")

    def claim_once():
        return client_access.post(
            "/internal/task-runs/claim",
            headers=worker_headers(),
            params={"harness": "browser-use"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim_once(), range(8)))

    wins = [r for r in results if r.status_code == 200]
    empties = [r for r in results if r.status_code == 204]
    assert len(wins) == 1
    assert len(empties) == 7
    assert len({w.json()["id"] for w in wins}) == 1
    assert wins[0].json()["harness"] == "browser-use"
    assert wins[0].json()["worker_id"] == WORKER_ID


def test_claim_fifo_per_profile_and_global_oldest(client_access: TestClient):
    profile_a = db.create_profile("A", sandbox_id="alpha")
    profile_b = db.create_profile("B", sandbox_id="alpha")
    run_a1 = create_run(client_access, profile_id=profile_a["id"], task="A1")
    run_b1 = create_run(client_access, profile_id=profile_b["id"], task="B1")
    run_a2 = create_run(client_access, profile_id=profile_a["id"], task="A2")

    first = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert first.status_code == 200
    assert first.json()["id"] == run_a1["id"]
    assert first.json()["status"] == "health_check"
    assert first.json()["claim_expires_at"]
    assert first.json()["worker_id"] == WORKER_ID

    # A2 remains queued behind A1; B1 is next globally eligible.
    second = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert second.status_code == 200
    assert second.json()["id"] == run_b1["id"]

    empty = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert empty.status_code == 204

    fetched_a2 = client_access.get(
        f"/api/task-runs/{run_a2['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert fetched_a2["status"] == "queued"
    assert fetched_a2["claim_eligible_at"] is None


def test_claim_response_excludes_secrets(client_access: TestClient):
    profile = db.create_profile("Secret check", sandbox_id="alpha")
    create_run(client_access, profile_id=profile["id"])
    resp = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert resp.status_code == 200
    body = resp.json()
    text = resp.text
    for forbidden in (
        "capability",
        "token",
        "digest",
        "cbm_run_",
        "cbm_worker_",
        "cbm_lease_",
        WORKER_KEY,
        "password",
        "credential",
    ):
        assert forbidden not in text.lower() or forbidden in {
            # task text may contain benign words; check keys only for capability/token/digest
        }
    assert "capability" not in body
    assert "token" not in body
    assert "capability_digest" not in body
    assert body["task"]
    assert body["profile_id"]
    assert body["allowed_origins"] == ["https://example.com"]
    assert body["viewport_revision"]


def test_launch_if_stopped_launches_records_evidence_before_capability(
    client_access: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    profile = db.create_profile("Launch me", sandbox_id="alpha")
    run = create_run(
        client_access,
        profile_id=profile["id"],
        launch_if_stopped=True,
    )
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text

    async def fake_launch(launched_profile: dict):
        main.browser_mgr.running[launched_profile["id"]] = object()
        return object()

    async def fake_wait(ready_profile: dict, *, timeout_seconds: float):
        assert ready_profile["id"] == profile["id"]
        assert timeout_seconds == 5.0
        return {
            "profile_id": profile["id"],
            "user_data_dir": "/private/profile/path-must-not-persist",
            "user_data_dir_digest": "a" * 64,
            "display": ":100",
            "vnc_ws_port": 6100,
            "cdp_port": 5100,
            "cdp_ready": True,
        }

    monkeypatch.setattr(main.browser_mgr, "launch", fake_launch)
    monkeypatch.setattr(main.browser_mgr, "wait_for_cdp_ready", fake_wait)

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 200, capability.text
    body = capability.json()
    assert body["profile_id"] == profile["id"]
    assert body["harness"] == "browser-use"
    assert body["allowed_origins"] == ["https://example.com"]
    assert body["viewport_revision"] == claimed.json()["viewport_revision"]
    assert body["launch_evidence"]["source"] == "manager"
    assert body["launch_evidence"]["launched"] is True
    assert body["launch_evidence"]["cdp_ready"] is True
    assert body["launch_evidence"]["user_data_dir_digest"] == "a" * 64
    assert "user_data_dir" not in body["launch_evidence"]

    fetched = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert fetched["launch_evidence"]["source"] == "manager"
    assert fetched["launch_evidence"]["cdp_ready"] is True


def test_routed_capability_revalidates_exact_worker_provider_freshness(
    client_access: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from backend import main

    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: now
    profile = db.create_profile("Routed stale provider", sandbox_id="alpha", harness="acpx")
    seed_provider_preflight(client_access, provider="grok", transport="acp")
    run = create_routed_run(
        client_access,
        profile_id=profile["id"],
        transport="acp",
        launch_if_stopped=True,
    )
    claim_routed_run(client_access, run["id"])
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE worker_provider_preflights
            SET checked_at = ?
            WHERE worker_id = ? AND provider = 'grok' AND transport = 'acp'
            """,
            ((now - timedelta(seconds=301)).isoformat(), WORKER_ID),
        )
        conn.commit()
    launched = False

    async def fail_if_launched(_profile: dict):
        nonlocal launched
        launched = True
        raise AssertionError("browser launch must not run for stale provider")

    monkeypatch.setattr(main.browser_mgr, "launch", fail_if_launched)

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 404
    assert "cbm_run_" not in capability.text
    assert launched is False
    row = routed_capability_row(run["id"])
    assert row["status"] == "health_check"
    assert row["worker_id"] == WORKER_ID
    assert row["lease_id"]
    assert row["capability_digest"] is None
    assert row["error_code"] is None


def test_routed_capability_requires_provider_readiness_from_claimed_worker(
    client_access: TestClient,
):
    now = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
    profile = db.create_profile("Routed wrong worker", sandbox_id="alpha", harness="acpx")
    seed_provider_preflight(client_access, provider="grok", transport="acp")
    run = create_routed_run(client_access, profile_id=profile["id"], transport="acp")
    claim_routed_run(client_access, run["id"])
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE worker_provider_preflights
            SET ready = 0, reason_code = 'auth_required', checked_at = ?
            WHERE worker_id = ? AND provider = 'grok' AND transport = 'acp'
            """,
            (now.isoformat(), WORKER_ID),
        )
        conn.execute(
            """
            INSERT INTO worker_identities (id, key_digest, active, created_at, updated_at)
            VALUES ('other-worker', 'other-digest', 1, ?, ?)
            """,
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO worker_provider_preflights
                (worker_id, provider, transport, ready, reason_code, model_aliases_json, checked_at)
            VALUES ('other-worker', 'grok', 'acp', 1, 'ready', '[]', ?)
            """,
            (now.isoformat(),),
        )
        conn.commit()

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 404
    assert "cbm_run_" not in capability.text
    assert routed_capability_row(run["id"])["capability_digest"] is None


def test_routed_capability_rejects_openai_compatible_model_alias_drift(
    client_access: TestClient,
):
    profile = db.create_profile("Routed wrong model", sandbox_id="alpha", harness="acpx")
    seed_provider_preflight(
        client_access,
        provider="grok",
        transport="openai-compatible",
        model_aliases=["grok-build-0.1"],
    )
    run = create_routed_run(
        client_access,
        profile_id=profile["id"],
        transport="openai-compatible",
        provider_model_alias="grok-build-0.1",
    )
    claim_routed_run(client_access, run["id"])
    seed_provider_preflight(
        client_access,
        provider="grok",
        transport="openai-compatible",
        model_aliases=["other-model"],
    )

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 404
    assert "cbm_run_" not in capability.text
    assert routed_capability_row(run["id"])["capability_digest"] is None


def test_routed_capability_succeeds_with_fresh_exact_provider_readiness(
    client_access: TestClient,
):
    profile = db.create_profile("Routed fresh provider", sandbox_id="alpha", harness="acpx")
    seed_provider_preflight(
        client_access,
        provider="grok",
        transport="openai-compatible",
        model_aliases=["grok-build-0.1"],
    )
    run = create_routed_run(
        client_access,
        profile_id=profile["id"],
        transport="openai-compatible",
        provider_model_alias="grok-build-0.1",
    )
    claim_routed_run(client_access, run["id"])

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 200, capability.text
    body = capability.json()
    assert body["provider"] == run["provider"]
    assert body["browser_tools"] == run["browser_tools"]
    assert body["token"].startswith("cbm_run_")
    row = routed_capability_row(run["id"])
    assert row["status"] == "running"
    assert row["capability_digest"]


def test_legacy_capability_without_provider_readiness_still_succeeds(
    client_access: TestClient,
):
    profile = db.create_profile("Legacy capability", sandbox_id="alpha")
    run = create_run(client_access, profile_id=profile["id"])
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["id"] == run["id"]

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 200, capability.text
    assert capability.json()["provider"] is None


def test_capability_rejects_viewport_revision_drift_without_token(
    client_access: TestClient,
):
    profile = db.create_profile("Resize before run", sandbox_id="alpha")
    run = create_run(client_access, profile_id=profile["id"])
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text
    db.update_profile(profile["id"], screen_width=1366)

    capability = client_access.post(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 404
    assert "cbm_run_" not in capability.text


def test_claim_fails_closed_on_corrupted_persisted_routing_contract_before_lease(
    client_access: TestClient,
):
    profile = db.create_profile("Corrupt routing", sandbox_id="alpha", harness="antigravity")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    seed_provider_preflight(client_access, transport="acp")
    created = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "acpx",
            "agent": "grok-build",
            "task": "Work",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_runs SET browser_tools_json = ? WHERE id = ?",
            ('{"not":"a-list"}', run_id),
        )
        conn.commit()

    claim = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    )

    assert claim.status_code == 204
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, error_code, error_message
            FROM task_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        active_leases = conn.execute(
            "SELECT COUNT(*) FROM automation_leases WHERE profile_id = ? AND released_at IS NULL",
            (profile["id"],),
        ).fetchone()[0]
    assert dict(row) == {
        "status": "failed",
        "claimed_by": None,
        "worker_id": None,
        "claim_expires_at": None,
        "lease_id": None,
        "capability_digest": None,
        "error_code": "invalid_routing_contract",
        "error_message": "Invalid persisted routing contract",
    }
    assert active_leases == 0


def test_capability_rejects_corrupted_persisted_routing_contract_without_token(
    client_access: TestClient,
):
    profile = db.create_profile("Corrupt capability routing", sandbox_id="alpha", harness="antigravity")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    seed_provider_preflight(client_access, transport="acp")
    created = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "acpx",
            "agent": "grok-build",
            "task": "Work",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    )
    assert claimed.status_code == 200, claimed.text
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_runs SET routing_policy_json = ? WHERE id = ?",
            ('{"allow_second_browser":true}', run_id),
        )
        conn.commit()

    capability = client_access.post(
        f"/internal/task-runs/{run_id}/capability",
        headers=worker_headers(),
    )

    assert capability.status_code == 404
    assert "cbm_run_" not in capability.text
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, error_code, error_message
            FROM task_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    assert dict(row) == {
        "status": "failed",
        "claimed_by": None,
        "worker_id": None,
        "claim_expires_at": None,
        "lease_id": None,
        "capability_digest": None,
        "error_code": "invalid_routing_contract",
        "error_message": "Invalid persisted routing contract",
    }


def test_direct_lease_blocks_claim_eligibility(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Busy", sandbox_id="alpha")
    run = create_run(client_access, profile_id=profile["id"])
    agent = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Direct",
            "paperclip_agent_id": "paperclip-direct",
            "grants": [{"sandbox_id": "alpha", "permission": "automate"}],
        },
    ).json()
    lease = client_access.post(
        f"/api/profiles/{profile['id']}/automation-leases",
        headers={"Authorization": f"Bearer {agent['api_key']}"},
    )
    assert lease.status_code == 200

    fetched = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert fetched["claim_eligible_at"] is None

    empty = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert empty.status_code == 204

    # Time behind lease must not count toward eligibility timeout.
    clock = {"now": datetime.now(timezone.utc)}
    main.worker_runtime_service._clock = lambda: clock["now"]
    clock["now"] = clock["now"] + timedelta(seconds=120)
    main.worker_runtime_service.maintain_once()
    still = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert still["status"] == "queued"
    assert still.get("error_code") is None

    client_access.delete(
        f"/api/profiles/{profile['id']}/automation-leases/{lease.json()['lease_id']}",
        headers={
            "Authorization": f"Bearer {agent['api_key']}",
            "X-CBM-Automation-Lease": lease.json()["token"],
        },
    )
    main.worker_runtime_service.refresh_claim_eligibility()
    eligible = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert eligible["claim_eligible_at"] is not None


def test_eligibility_timeout_fails_worker_unavailable(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Timeout", sandbox_id="alpha")
    run = create_run(client_access, profile_id=profile["id"])
    fetched = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert fetched["claim_eligible_at"]

    start = datetime.fromisoformat(fetched["claim_eligible_at"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: start + timedelta(seconds=61)
    main.worker_runtime_service.maintain_once()

    failed = client_access.get(
        f"/api/task-runs/{run['id']}",
        headers=bootstrap_headers(),
    ).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_unavailable"


def test_concurrent_claims_single_winner(client_access: TestClient):
    profile = db.create_profile("Race", sandbox_id="alpha")
    create_run(client_access, profile_id=profile["id"])

    def claim_once():
        return client_access.post(
            "/internal/task-runs/claim",
            headers=worker_headers(),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim_once(), range(8)))

    wins = [r for r in results if r.status_code == 200]
    empties = [r for r in results if r.status_code == 204]
    assert len(wins) == 1
    assert len(empties) == 7
    assert len({w.json()["id"] for w in wins}) == 1


def test_heartbeat_renews_and_wrong_worker_404(client_access: TestClient):
    profile = db.create_profile("HB", sandbox_id="alpha")
    create_run(client_access, profile_id=profile["id"])
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    ).json()
    run_id = claimed["id"]

    hb = client_access.post(
        f"/internal/task-runs/{run_id}/heartbeat",
        headers=worker_headers(),
    )
    assert hb.status_code == 200
    body = hb.json()
    assert body["claim_expires_at"]
    assert body["lease_expires_at"]
    assert "cancel_requested" in body

    missing = client_access.post(
        "/internal/task-runs/unknown/heartbeat",
        headers=worker_headers(),
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not found"}


def test_run_heartbeat_keeps_harness_presence_fresh_while_worker_is_busy(
    client_access: TestClient,
):
    from backend import main

    profile = db.create_profile("Busy ACPX", sandbox_id="alpha")
    create_run(
        client_access,
        profile_id=profile["id"],
        harness="acpx",
        agent="cursor",
    )
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
        params={"harness": "acpx"},
    ).json()

    initial_presence = client_access.get(
        "/api/task-harnesses/acpx/presence",
        headers=bootstrap_headers(),
    ).json()
    first_seen = datetime.fromisoformat(initial_presence["last_seen_at"])

    for elapsed_seconds in (20, 40, 60):
        heartbeat_at = first_seen + timedelta(seconds=elapsed_seconds)
        main.worker_runtime_service._clock = lambda now=heartbeat_at: now
        heartbeat = client_access.post(
            f"/internal/task-runs/{claimed['id']}/heartbeat",
            headers=worker_headers(),
        )
        assert heartbeat.status_code == 200, heartbeat.text

    busy_presence = client_access.get(
        "/api/task-harnesses/acpx/presence",
        headers=bootstrap_headers(),
    )
    assert busy_presence.status_code == 200
    assert busy_presence.json()["worker_seen_recently"] is True
    assert busy_presence.json()["state"] == "polling"
    assert datetime.fromisoformat(busy_presence.json()["last_seen_at"]) == heartbeat_at


def test_one_pre_action_retry_then_worker_lost(client_access: TestClient):
    from backend import main

    profile = db.create_profile("Retry", sandbox_id="alpha")
    create_run(client_access, profile_id=profile["id"])
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    ).json()
    run_id = claimed["id"]

    # Advance past claim expiry before first action.
    expires = datetime.fromisoformat(claimed["claim_expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: expires + timedelta(seconds=1)
    main.worker_runtime_service.maintain_once()

    requeued = client_access.get(
        f"/api/task-runs/{run_id}",
        headers=bootstrap_headers(),
    ).json()
    assert requeued["status"] == "queued"
    assert requeued["retry_count"] == 1
    assert requeued["claimed_by"] is None

    claimed2 = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    ).json()
    assert claimed2["id"] == run_id

    # Emit first action, then lose heartbeat → failed worker_lost, no retry.
    out = client_access.post(
        f"/internal/task-runs/{run_id}/outputs",
        headers=worker_headers(),
        json={
            "idempotency_key": "a1",
            "kind": "action",
            "summary": "Click",
            "payload": {"name": "click"},
        },
    )
    assert out.status_code == 201, out.text

    expires2 = datetime.fromisoformat(claimed2["claim_expires_at"])
    if expires2.tzinfo is None:
        expires2 = expires2.replace(tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: expires2 + timedelta(seconds=1)
    main.worker_runtime_service.maintain_once()

    failed = client_access.get(
        f"/api/task-runs/{run_id}",
        headers=bootstrap_headers(),
    ).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "worker_lost"
    assert failed["retry_count"] == 1
