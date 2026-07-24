"""API tests for task runs: create, health gates, cancel, retry, override."""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db
from backend.run_health import NON_OVERRIDABLE_REASON_CODES


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", "worker-test-secret")
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
    *permissions: str,
) -> str:
    password = f"{username}-password-123"
    response = client.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": username,
            "password": password,
            "grants": [
                {"sandbox_id": sandbox_id, "permission": permission}
                for permission in permissions
            ],
        },
    )
    assert response.status_code == 201, response.text
    return password


def login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def seed_passed_health(profile_id: str) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    db.upsert_profile_health(
        profile_id,
        state="passed",
        checked_at=checked_at,
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


def seed_failed_health(profile_id: str, *, measurement_error: bool = False) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    db.upsert_profile_health(
        profile_id,
        state="failed",
        checked_at=checked_at,
        proxy_configured=True,
        proxy_reachable=False,
        proxy_authenticity_score=10,
        warnings=["platform_mismatch"],
        blockers=["network_timeout"] if measurement_error else ["proxy_unreachable"],
        error_code="network_timeout" if measurement_error else "proxy_unreachable",
        sources={"proxy_authenticity": "measured"},
    )


def seed_pending_health(profile_id: str) -> None:
    db.upsert_profile_health(
        profile_id,
        state="pending",
        checked_at=None,
        proxy_configured=False,
        proxy_reachable=None,
        warnings=[],
        blockers=[],
        sources={},
    )


def create_session(profile_id: str, sandbox_id: str = "alpha") -> dict:
    return db.create_task_session(profile_id, sandbox_id, "bootstrap")


def run_body(**overrides):
    body = {
        "harness": "browser-use",
        "task": "Navigate to the target and return the page title",
        "profile_id": overrides.pop("profile_id", None),
        "launch_if_stopped": False,
        "allowed_origins": ["https://example.com"],
        "max_steps": 20,
        "timeout_seconds": 300,
        "model_alias": "default",
    }
    body.update(overrides)
    return body


def create_run(client: TestClient, session_id: str, profile_id: str, **overrides) -> dict:
    response = client.post(
        f"/api/task-sessions/{session_id}/runs",
        json=run_body(profile_id=profile_id, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_run_stores_prompt_once_and_copies_immutable_health(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)

    created = create_run(client_access, session["id"], profile["id"])
    assert created["status"] == "queued"
    assert created["task_message_id"]
    assert created["profile_id"] == profile["id"]
    assert created["profile_id_snapshot"] == profile["id"]
    assert created["allowed_origins"] == ["https://example.com"]
    assert created["retry_count"] == 0
    assert created["first_action_sequence"] is None
    assert created["health_snapshot"]["state"] == "passed"
    assert created["health_decision"]["allowed"] is True
    assert "task" not in created
    assert created.get("claimed_by") is None

    messages = db.list_task_messages(session["id"])
    assert len(messages) == 1
    assert messages[0]["id"] == created["task_message_id"]
    assert messages[0]["content"] == "Navigate to the target and return the page title"

    # Mutating live profile health must not change the frozen run copy.
    seed_failed_health(profile["id"], measurement_error=True)
    fetched = client_access.get(f"/api/task-runs/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["health_snapshot"]["state"] == "passed"
    assert fetched.json()["health_decision"]["allowed"] is True
    assert db.get_task_session(session["id"])["profile_id"] == profile["id"]


def test_initial_status_follows_health_decision(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)

    seed_pending_health(profile["id"])
    waiting = create_run(
        client_access,
        session["id"],
        profile["id"],
        task="waiting health",
    )
    assert waiting["status"] == "health_check"
    assert waiting["health_decision"]["waiting"] is True

    seed_failed_health(profile["id"])
    blocked = create_run(
        client_access,
        session["id"],
        profile["id"],
        task="blocked health",
    )
    assert blocked["status"] == "blocked_health"
    assert blocked["health_decision"]["allowed"] is False
    assert blocked["status"] != "running"


def test_missing_health_row_becomes_unavailable_measurement_error(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)

    created = create_run(client_access, session["id"], profile["id"])
    assert created["status"] == "blocked_health"
    assert created["health_snapshot"]["state"] == "unavailable"
    assert created["health_snapshot"]["measurement_error"] is True
    assert "measurement_error" in created["health_decision"]["non_overridable_reasons"]


def test_run_creation_requires_automate_and_same_sandbox_profile(
    client_access: TestClient,
):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    seed_passed_health(alpha["id"])
    seed_passed_health(beta["id"])
    session = create_session(alpha["id"], "alpha")

    view_password = create_user(client_access, "alpha-view", "alpha", "view")
    login(client_access, "alpha-view", view_password)
    denied_view = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(profile_id=alpha["id"]),
    )
    assert denied_view.status_code == 404

    auto_password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", auto_password)
    cross = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(profile_id=beta["id"]),
    )
    assert cross.status_code == 404

    missing = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(profile_id="missing-profile"),
    )
    assert missing.status_code == 404

    beta_session = create_session(beta["id"], "beta")
    foreign = client_access.post(
        f"/api/task-sessions/{beta_session['id']}/runs",
        json=run_body(profile_id=beta["id"]),
    )
    assert foreign.status_code == 404


def test_launch_if_stopped_and_empty_origins_require_operate(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    auto_password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", auto_password)

    launch_denied = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(profile_id=profile["id"], launch_if_stopped=True),
    )
    assert launch_denied.status_code == 404

    empty_denied = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(profile_id=profile["id"], allowed_origins=[]),
    )
    assert empty_denied.status_code == 403

    malformed = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        json=run_body(
            profile_id=profile["id"],
            allowed_origins=["https://example.com/path"],
        ),
    )
    assert malformed.status_code == 422

    operate_password = create_user(
        client_access, "alpha-ops", "alpha", "automate", "operate"
    )
    login(client_access, "alpha-ops", operate_password)
    launched = create_run(
        client_access,
        session["id"],
        profile["id"],
        launch_if_stopped=True,
        task="launch allowed",
    )
    assert launched["launch_if_stopped"] is True

    unrestricted = create_run(
        client_access,
        session["id"],
        profile["id"],
        allowed_origins=[],
        task="unrestricted",
    )
    assert unrestricted["allowed_origins"] == []


def test_allowed_origins_are_normalized_and_deduped(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)

    created = create_run(
        client_access,
        session["id"],
        profile["id"],
        allowed_origins=[
            "https://Example.com",
            "https://example.com:443",
            "https://EXAMPLE.com",
        ],
    )
    assert created["allowed_origins"] == ["https://example.com"]


def test_cancel_is_idempotent_and_preserves_terminals(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"])

    first = client_access.post(f"/api/task-runs/{run['id']}/cancel")
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"

    second = client_access.post(f"/api/task-runs/{run['id']}/cancel")
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert second.json()["cancelled_at"] == first.json()["cancelled_at"]


def test_retry_health_refreshes_snapshot_without_duplicating_prompt(
    client_access: TestClient,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_failed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"])
    assert run["status"] == "blocked_health"
    message_id = run["task_message_id"]

    seed_passed_health(profile["id"])
    retried = client_access.post(f"/api/task-runs/{run['id']}/retry-health")
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "queued"
    assert body["retry_count"] == 1
    assert body["task_message_id"] == message_id
    assert body["health_snapshot"]["state"] == "passed"
    assert len(db.list_task_messages(session["id"])) == 1


def test_override_health_blocks_non_overridable_reasons(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_failed_health(profile["id"], measurement_error=True)
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"])
    assert run["status"] == "blocked_health"
    assert set(run["health_decision"]["non_overridable_reasons"]) & NON_OVERRIDABLE_REASON_CODES

    blocked = client_access.post(
        f"/api/task-runs/{run['id']}/override-health",
        json={"reason": "temporary exception"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked_health"
    assert blocked.json().get("health_override") is None or blocked.json()[
        "health_override"
    ].get("applied") is not True


def test_override_health_allows_overridable_reasons(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    # Fresh warning with overridable authenticity failure only.
    checked_at = datetime.now(timezone.utc).isoformat()
    db.upsert_profile_health(
        profile["id"],
        state="warning",
        checked_at=checked_at,
        proxy_configured=False,
        proxy_reachable=True,
        proxy_authenticity_score=10,
        fingerprint_consistency_score=100,
        browser_scan_score=90,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={"proxy_authenticity": "measured"},
    )
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"])
    assert run["status"] == "blocked_health"
    assert run["health_decision"]["non_overridable_reasons"] == []

    empty = client_access.post(
        f"/api/task-runs/{run['id']}/override-health",
        json={"reason": "   "},
    )
    assert empty.status_code == 422

    overridden = client_access.post(
        f"/api/task-runs/{run['id']}/override-health",
        json={"reason": "approved temporary authenticity exception"},
    )
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["status"] == "queued"
    assert body["health_override"]["reason"] == "approved temporary authenticity exception"
    assert body["health_override"]["actor_kind"] == "user"
    assert body["health_override"]["applied"] is True


def test_profile_deletion_preserves_run_history_snapshot(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"])
    profile_id = profile["id"]

    assert db.delete_profile(profile_id) is True
    fetched = client_access.get(f"/api/task-runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["profile_id"] is None
    assert fetched.json()["profile_id_snapshot"] == profile_id


def test_get_run_requires_view_and_hides_cross_sandbox(client_access: TestClient):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    seed_passed_health(alpha["id"])
    seed_passed_health(beta["id"])
    alpha_session = create_session(alpha["id"], "alpha")
    beta_session = create_session(beta["id"], "beta")

    beta_password = create_user(client_access, "beta-auto", "beta", "automate")
    login(client_access, "beta-auto", beta_password)
    beta_run = create_run(client_access, beta_session["id"], beta["id"])

    alpha_password = create_user(client_access, "alpha-view", "alpha", "view")
    login(client_access, "alpha-view", alpha_password)
    denied = client_access.get(f"/api/task-runs/{beta_run['id']}")
    assert denied.status_code == 404

    # Alpha automate creates a run; alpha view can read it.
    auto_password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", auto_password)
    alpha_run = create_run(client_access, alpha_session["id"], alpha["id"])
    login(client_access, "alpha-view", alpha_password)
    allowed = client_access.get(f"/api/task-runs/{alpha_run['id']}")
    assert allowed.status_code == 200
    assert allowed.json()["id"] == alpha_run["id"]


def test_retry_and_override_cross_sandbox_are_404(client_access: TestClient):
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    seed_failed_health(beta["id"])
    session = create_session(beta["id"], "beta")
    beta_password = create_user(client_access, "beta-auto", "beta", "automate")
    login(client_access, "beta-auto", beta_password)
    run = create_run(client_access, session["id"], beta["id"])

    alpha_password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", alpha_password)
    assert client_access.post(f"/api/task-runs/{run['id']}/retry-health").status_code == 404
    assert (
        client_access.post(
            f"/api/task-runs/{run['id']}/override-health",
            json={"reason": "nope"},
        ).status_code
        == 404
    )
    assert client_access.post(f"/api/task-runs/{run['id']}/cancel").status_code == 404


def seed_overridable_blocked_health(profile_id: str) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    db.upsert_profile_health(
        profile_id,
        state="warning",
        checked_at=checked_at,
        proxy_configured=False,
        proxy_reachable=True,
        proxy_authenticity_score=10,
        fingerprint_consistency_score=100,
        browser_scan_score=90,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={"proxy_authenticity": "measured"},
    )


def _force_run_status(run_id: str, status: str, *, cancelled_at: str | None = None) -> None:
    with db.get_db() as conn:
        conn.execute(
            """UPDATE task_runs
            SET status = ?, cancelled_at = ?, updated_at = ?
            WHERE id = ?""",
            (status, cancelled_at, datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()


def test_override_health_does_not_resurrect_terminal_runs(client_access: TestClient):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_overridable_blocked_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"], task="override terminal")
    assert run["status"] == "blocked_health"
    assert run["health_decision"]["non_overridable_reasons"] == []

    cancelled = client_access.post(f"/api/task-runs/{run['id']}/cancel")
    assert cancelled.status_code == 200
    before = cancelled.json()
    assert before["status"] == "cancelled"
    assert before["cancelled_at"]
    assert before.get("health_override") is None

    overridden = client_access.post(
        f"/api/task-runs/{run['id']}/override-health",
        json={"reason": "should not resurrect cancelled run"},
    )
    assert overridden.status_code == 200
    after = overridden.json()
    assert after["status"] == before["status"]
    assert after["cancelled_at"] == before["cancelled_at"]
    assert after.get("health_override") == before.get("health_override")
    assert after["health_decision"] == before["health_decision"]
    assert after["updated_at"] == before["updated_at"]

    for terminal in ("succeeded", "failed", "revoked"):
        blocked = create_run(
            client_access,
            session["id"],
            profile["id"],
            task=f"terminal-{terminal}",
        )
        assert blocked["status"] == "blocked_health"
        _force_run_status(blocked["id"], terminal)
        frozen = client_access.get(f"/api/task-runs/{blocked['id']}").json()
        response = client_access.post(
            f"/api/task-runs/{blocked['id']}/override-health",
            json={"reason": f"should not resurrect {terminal}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == frozen["status"] == terminal
        assert body.get("health_override") == frozen.get("health_override")
        assert body["health_decision"] == frozen["health_decision"]
        assert body["cancelled_at"] == frozen["cancelled_at"]
        assert body["updated_at"] == frozen["updated_at"]


def test_retry_health_loses_to_concurrent_cancel(client_access: TestClient, monkeypatch):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_failed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)
    run = create_run(client_access, session["id"], profile["id"], task="retry race")
    assert run["status"] == "blocked_health"
    run_id = run["id"]
    before_snapshot = run["health_snapshot"]
    before_decision = run["health_decision"]

    real_build = db.build_run_health_gate
    cancelled_at_holder: dict[str, str] = {}

    def cancel_during_health_build(profile_id: str):
        cancelled = db.cancel_task_run(run_id)
        assert cancelled is not None
        assert cancelled["status"] == "cancelled"
        cancelled_at_holder["cancelled_at"] = cancelled["cancelled_at"]
        seed_passed_health(profile_id)
        return real_build(profile_id)

    monkeypatch.setattr(db, "build_run_health_gate", cancel_during_health_build)

    retried = db.retry_task_run_health(run_id)
    assert retried is not None
    assert retried["status"] == "cancelled"
    assert retried["cancelled_at"] == cancelled_at_holder["cancelled_at"]
    assert retried["retry_count"] == 0
    assert retried["health_snapshot"] == before_snapshot
    assert retried["health_decision"] == before_decision
    assert retried["health_decision"]["allowed"] is False


def test_create_run_is_atomic_when_run_insert_fails(
    client_access: TestClient,
    monkeypatch,
):
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = create_session(profile["id"])
    password = create_user(client_access, "alpha-auto", "alpha", "automate")
    login(client_access, "alpha-auto", password)

    def fail_run_insert(*_args, **_kwargs):
        raise RuntimeError("injected run insert failure")

    if hasattr(db, "_insert_task_run_on_conn"):
        monkeypatch.setattr(db, "_insert_task_run_on_conn", fail_run_insert)
    else:
        monkeypatch.setattr(db, "create_task_run", fail_run_insert)

    with pytest.raises(RuntimeError, match="injected run insert failure"):
        client_access.post(
            f"/api/task-sessions/{session['id']}/runs",
            json=run_body(profile_id=profile["id"], task="atomic prompt"),
        )

    assert db.list_task_messages(session["id"]) == []
    with db.get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_session_id = ?",
            (session["id"],),
        ).fetchone()[0] == 0
