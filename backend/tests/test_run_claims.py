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
    with TestClient(main.app) as client:
        main.worker_runtime_service.sync_configured_worker()
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def worker_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_KEY}"}


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
        sources={"proxy_authenticity": "measured"},
    )


def create_run(
    client: TestClient,
    *,
    profile_id: str,
    sandbox_id: str = "alpha",
    task: str = "Work",
    harness: str = "browser-use",
) -> dict:
    seed_passed_health(profile_id)
    session = db.create_task_session(profile_id, sandbox_id, "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": harness,
            "task": task,
            "profile_id": profile_id,
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_claim_204_when_no_work(client_access: TestClient):
    resp = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert resp.status_code == 204
    assert resp.content in {b"", b"null"}


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
