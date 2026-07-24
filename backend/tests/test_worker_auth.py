"""Worker identity auth: hashed keys, middleware gating, rotation."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import access_control as access
from backend import database as db


WORKER_ID = "browser-use-worker-1"
# 32 random bytes as hex, prefixed — strong cbm_worker_ format.
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


@pytest.fixture()
def client_legacy_open(tmp_db, monkeypatch):
    """ACCESS_CONTROL off and no AUTH_TOKEN — /internal still requires worker."""
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", None)
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", False)
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


def worker_headers(key: str = WORKER_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


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


def create_queued_run(client: TestClient) -> dict:
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "browser-use",
            "task": "Do the thing",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_generate_worker_key_format_and_hash():
    key = access.generate_worker_key()
    assert key.startswith("cbm_worker_")
    raw = key.removeprefix("cbm_worker_")
    assert len(bytes.fromhex(raw)) == 32
    digest = access.hash_worker_key(key)
    assert digest == hashlib.sha256(key.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert not hmac.compare_digest(digest, key)


def test_internal_routes_reject_admin_and_agent_tokens(client_access: TestClient):
    agent = client_access.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": "Agent",
            "paperclip_agent_id": "paperclip-agent",
            "grants": [{"sandbox_id": "alpha", "permission": "automate"}],
        },
    ).json()

    admin = client_access.post(
        "/internal/task-runs/claim",
        headers=bootstrap_headers(),
    )
    assert admin.status_code == 401
    assert "bootstrap" not in admin.text.lower()
    assert WORKER_KEY not in admin.text

    agent_resp = client_access.post(
        "/internal/task-runs/claim",
        headers={"Authorization": f"Bearer {agent['api_key']}"},
    )
    assert agent_resp.status_code == 401
    assert admin.json() == agent_resp.json()


def test_internal_rejects_legacy_x_cbm_worker_token(client_access: TestClient):
    resp = client_access.post(
        "/internal/task-runs/claim",
        headers={"X-CBM-Worker-Token": WORKER_KEY},
    )
    assert resp.status_code == 401
    assert WORKER_KEY not in resp.text


def test_worker_bearer_accepted_on_internal_rejected_on_public(
    client_access: TestClient,
):
    claim = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claim.status_code in {200, 204}

    public = client_access.get(
        "/api/profiles",
        headers=worker_headers(),
    )
    assert public.status_code == 401


def test_internal_protected_when_access_control_disabled(client_legacy_open: TestClient):
    open_public = client_legacy_open.get("/api/profiles")
    assert open_public.status_code == 200

    missing = client_legacy_open.post("/internal/task-runs/claim")
    assert missing.status_code == 401

    ok = client_legacy_open.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert ok.status_code in {200, 204}


def test_worker_key_digest_only_persisted(client_access: TestClient):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id, key_digest, active FROM worker_identities WHERE id = ?",
            (WORKER_ID,),
        ).fetchone()
    assert row is not None
    assert row["id"] == WORKER_ID
    assert bool(row["active"])
    assert row["key_digest"] == access.hash_worker_key(WORKER_KEY)
    assert WORKER_KEY not in str(dict(row))
    assert "cbm_worker_" not in str(row["key_digest"])


def test_credential_rotation_revokes_active_claims(client_access: TestClient, monkeypatch):
    from backend import main

    create_queued_run(client_access)
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text
    run_id = claimed.json()["id"]

    new_key = "cbm_worker_" + ("cd" * 32)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", new_key)
    revoked = main.worker_runtime_service.sync_configured_worker()
    assert revoked.get("worker_id") == WORKER_ID
    assert revoked.get("rotated") is True

    stale = client_access.post(
        f"/internal/task-runs/{run_id}/heartbeat",
        headers=worker_headers(WORKER_KEY),
    )
    assert stale.status_code == 401

    ok = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(new_key),
    )
    assert ok.status_code in {200, 204}

    fetched = client_access.get(
        f"/api/task-runs/{run_id}",
        headers=bootstrap_headers(),
    )
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] in {"queued", "revoked", "failed"}
    assert body.get("claimed_by") is None or body["status"] != "health_check"


def test_weak_or_legacy_worker_token_rejected_at_bootstrap(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_ID", WORKER_ID)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", "worker-test-secret")
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        result = main.worker_runtime_service.sync_configured_worker()
        assert result.get("configured") is False
        denied = client.post(
            "/internal/task-runs/claim",
            headers={"Authorization": "Bearer worker-test-secret"},
        )
        assert denied.status_code == 401


def test_sync_rotates_worker_id_and_revokes_stale_identities(
    client_access: TestClient, monkeypatch
):
    """Old worker ID → new worker ID: sole active identity + revoked claims/leases."""
    from backend import main

    create_queued_run(client_access)
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text
    run_body = claimed.json()
    run_id = run_body["id"]
    profile_id = run_body["profile_id"]
    cap = client_access.post(
        f"/internal/task-runs/{run_id}/capability",
        headers=worker_headers(),
    )
    assert cap.status_code == 200, cap.text
    run_token = cap.json()["token"]

    with db.get_db() as conn:
        lease_row = conn.execute(
            "SELECT id FROM automation_leases WHERE released_at IS NULL AND owner_id = ?",
            (WORKER_ID,),
        ).fetchone()
    assert lease_row is not None
    claimed_lease_id = str(lease_row["id"])
    handle = main.direct_cdp_socket_registry.register(
        lease_id=claimed_lease_id,
        profile_id=profile_id,
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )

    other_profile = db.create_profile("Unrelated", sandbox_id="alpha")
    unrelated = main.automation_lease_service.acquire_direct(
        profile_id=other_profile["id"],
        owner_kind="agent",
        owner_id="agent-unrelated",
    )
    unrelated_lease_id = unrelated.lease_id

    stale_a = "stale-worker-a"
    stale_b = "stale-worker-b"
    db.upsert_worker_identity(
        stale_a, access.hash_worker_key("cbm_worker_" + ("11" * 32)), active=True
    )
    db.upsert_worker_identity(
        stale_b, access.hash_worker_key("cbm_worker_" + ("22" * 32)), active=True
    )

    new_id = "browser-use-worker-2"
    new_key = "cbm_worker_" + ("cd" * 32)
    rotated = main.worker_runtime_service.sync_configured_worker(
        worker_id=new_id, worker_token=new_key
    )
    assert rotated.get("configured") is True
    assert rotated.get("worker_id") == new_id
    assert claimed_lease_id in (rotated.get("lease_ids") or [])
    assert unrelated_lease_id not in (rotated.get("lease_ids") or [])

    # Startup closes sockets for returned leases after commit (never inside txn).
    main.close_direct_cdp_sockets_for_leases(list(rotated.get("lease_ids") or []))
    assert handle.revoked.is_set()

    with db.get_db() as conn:
        rows = {
            str(r["id"]): bool(r["active"])
            for r in conn.execute("SELECT id, active FROM worker_identities").fetchall()
        }
        digests = {
            str(r["id"]): str(r["key_digest"])
            for r in conn.execute(
                "SELECT id, key_digest FROM worker_identities"
            ).fetchall()
        }
        run_row = conn.execute(
            "SELECT status, claimed_by, capability_digest, lease_id FROM task_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        unrelated_row = conn.execute(
            "SELECT released_at FROM automation_leases WHERE id = ?",
            (unrelated_lease_id,),
        ).fetchone()
        history = conn.execute(
            "SELECT COUNT(*) AS n FROM worker_identities WHERE id = ?",
            (WORKER_ID,),
        ).fetchone()["n"]
    assert rows[new_id] is True
    assert rows.get(WORKER_ID) is False
    assert rows.get(stale_a) is False
    assert rows.get(stale_b) is False
    assert history == 1  # history row preserved (inactive), no raw keys
    assert digests[new_id] == access.hash_worker_key(new_key)
    assert WORKER_KEY not in str(digests)
    assert new_key not in str(digests)
    assert run_row["capability_digest"] is None
    assert run_row["lease_id"] is None
    assert run_row["status"] == "revoked"
    assert unrelated_row["released_at"] is None

    stale_hb = client_access.post(
        f"/internal/task-runs/{run_id}/heartbeat",
        headers=worker_headers(WORKER_KEY),
    )
    assert stale_hb.status_code == 401
    stale_claim = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(WORKER_KEY),
    )
    assert stale_claim.status_code == 401
    stale_cdp = client_access.get(
        f"/api/profiles/{profile_id}/cdp/json/version",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert stale_cdp.status_code == 404

    ok = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(new_key),
    )
    assert ok.status_code in {200, 204}


def test_sync_rotates_worker_id_with_same_token_reassigns_digest_and_revokes_old_worker(
    client_access: TestClient, monkeypatch
):
    """Old worker ID -> new worker ID with same token must not leave old ID active."""
    from backend import main

    create_queued_run(client_access)
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200, claimed.text
    run_body = claimed.json()
    run_id = run_body["id"]
    profile_id = run_body["profile_id"]
    cap = client_access.post(
        f"/internal/task-runs/{run_id}/capability",
        headers=worker_headers(),
    )
    assert cap.status_code == 200, cap.text
    run_token = cap.json()["token"]

    with db.get_db() as conn:
        lease_row = conn.execute(
            "SELECT id FROM automation_leases WHERE released_at IS NULL AND owner_id = ?",
            (WORKER_ID,),
        ).fetchone()
    assert lease_row is not None
    claimed_lease_id = str(lease_row["id"])
    handle = main.direct_cdp_socket_registry.register(
        lease_id=claimed_lease_id,
        profile_id=profile_id,
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )

    other_profile = db.create_profile("Unrelated same-token", sandbox_id="alpha")
    unrelated = main.automation_lease_service.acquire_direct(
        profile_id=other_profile["id"],
        owner_kind="agent",
        owner_id="agent-unrelated",
    )
    unrelated_lease_id = unrelated.lease_id

    new_id = "browser-use-worker-2"
    rotated = main.worker_runtime_service.sync_configured_worker(
        worker_id=new_id, worker_token=WORKER_KEY
    )
    assert rotated.get("configured") is True
    assert rotated.get("rotated") is True
    assert rotated.get("worker_id") == new_id
    assert claimed_lease_id in (rotated.get("lease_ids") or [])
    assert unrelated_lease_id not in (rotated.get("lease_ids") or [])

    main.close_direct_cdp_sockets_for_leases(list(rotated.get("lease_ids") or []))
    assert handle.revoked.is_set()

    with db.get_db() as conn:
        rows = {
            str(r["id"]): bool(r["active"])
            for r in conn.execute("SELECT id, active FROM worker_identities").fetchall()
        }
        digest = access.hash_worker_key(WORKER_KEY)
        matching_digest_rows = [
            str(r["id"])
            for r in conn.execute(
                "SELECT id FROM worker_identities WHERE key_digest = ?",
                (digest,),
            ).fetchall()
        ]
        resolved = db.get_worker_identity_by_key_hash(digest)
        run_row = conn.execute(
            "SELECT status, claimed_by, capability_digest, lease_id FROM task_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        old_lease = conn.execute(
            "SELECT released_at FROM automation_leases WHERE id = ?",
            (claimed_lease_id,),
        ).fetchone()
        unrelated_row = conn.execute(
            "SELECT released_at FROM automation_leases WHERE id = ?",
            (unrelated_lease_id,),
        ).fetchone()
        old_history = conn.execute(
            "SELECT key_digest FROM worker_identities WHERE id = ?",
            (WORKER_ID,),
        ).fetchone()
    assert rows[new_id] is True
    assert rows.get(WORKER_ID) is False
    assert matching_digest_rows == [new_id]
    assert resolved is not None
    assert resolved["id"] == new_id
    assert bool(resolved["active"])
    assert old_history is not None
    assert old_history["key_digest"] != access.hash_worker_key(WORKER_KEY)
    assert WORKER_KEY not in str(old_history["key_digest"])
    assert run_row["status"] == "revoked"
    assert run_row["claimed_by"] is None
    assert run_row["capability_digest"] is None
    assert run_row["lease_id"] is None
    assert old_lease["released_at"] is not None
    assert unrelated_row["released_at"] is None

    stale_hb = client_access.post(
        f"/internal/task-runs/{run_id}/heartbeat",
        headers=worker_headers(WORKER_KEY),
    )
    assert stale_hb.status_code == 404
    stale_cdp = client_access.get(
        f"/api/profiles/{profile_id}/cdp/json/version",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert stale_cdp.status_code == 404
    current = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(WORKER_KEY),
    )
    assert current.status_code in {200, 204}


def test_sync_same_id_digest_change_revokes_and_is_atomic(
    client_access: TestClient, monkeypatch
):
    from backend import main

    create_queued_run(client_access)
    claimed = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(),
    )
    assert claimed.status_code == 200
    run_id = claimed.json()["id"]

    new_key = "cbm_worker_" + ("ef" * 32)
    rotated = main.worker_runtime_service.sync_configured_worker(
        worker_id=WORKER_ID, worker_token=new_key
    )
    assert rotated.get("rotated") is True

    stale = client_access.post(
        f"/internal/task-runs/{run_id}/heartbeat",
        headers=worker_headers(WORKER_KEY),
    )
    assert stale.status_code == 401
    ok = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(new_key),
    )
    assert ok.status_code in {200, 204}

    # Atomic rollback: failure mid-sync must not persist the new digest.
    prior_digest = access.hash_worker_key(new_key)
    failing_key = "cbm_worker_" + ("99" * 32)

    def boom(*_args, **_kwargs):
        raise RuntimeError("injected sync failure")

    monkeypatch.setattr(
        main.worker_runtime_service._leases, "release_on_conn", boom
    )
    # Re-claim so rotation has an active lease/claim to revoke.
    create_queued_run(client_access)
    claimed2 = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(new_key),
    )
    assert claimed2.status_code == 200

    with pytest.raises(RuntimeError, match="injected"):
        main.worker_runtime_service.sync_configured_worker(
            worker_id=WORKER_ID, worker_token=failing_key
        )

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT key_digest, active FROM worker_identities WHERE id = ?",
            (WORKER_ID,),
        ).fetchone()
    assert bool(row["active"])
    assert row["key_digest"] == prior_digest
    still_ok = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(new_key),
    )
    assert still_ok.status_code in {200, 204}
    denied = client_access.post(
        "/internal/task-runs/claim",
        headers=worker_headers(failing_key),
    )
    assert denied.status_code == 401
