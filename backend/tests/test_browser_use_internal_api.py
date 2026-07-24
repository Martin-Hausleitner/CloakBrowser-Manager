"""Internal Browser-Use worker API: capability, CDP, outputs, terminal."""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import database as db


WORKER_ID = "browser-use-worker-1"
WORKER_KEY = "cbm_worker_" + ("ab" * 32)


def make_png(width: int = 4, height: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes([255, 0, 0] * width) for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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


def seed_blocked_health(profile_id: str) -> None:
    db.upsert_profile_health(
        profile_id,
        state="warning",
        checked_at=datetime.now(timezone.utc).isoformat(),
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


def create_and_claim(client: TestClient) -> tuple[dict, dict]:
    profile = db.create_profile("Cap profile", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "browser-use",
            "task": "Navigate",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    claimed = client.post("/internal/task-runs/claim", headers=worker_headers())
    assert claimed.status_code == 200, claimed.text
    return claimed.json(), profile


def issue_capability(client: TestClient, run_id: str):
    return client.post(
        f"/internal/task-runs/{run_id}/capability",
        headers=worker_headers(),
    )


def test_worker_claim_returns_no_capability(client_access: TestClient):
    payload = create_and_claim(client_access)[0]
    assert "capability" not in payload
    assert "token" not in payload


def test_capability_one_time_digest_and_manager_cdp_url(client_access: TestClient):
    run, profile = create_and_claim(client_access)
    # Move to running via capability issuance (health already passed).
    first = issue_capability(client_access, run["id"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["token"].startswith("cbm_run_")
    assert body["cdp_url"].startswith("/")
    assert "9222" not in body["cdp_url"]
    assert "token=" not in body["cdp_url"]
    assert body["headers"]["Authorization"].startswith("Bearer cbm_run_")
    assert body["expires_at"]
    assert "upstream" not in body
    assert "port" not in body

    digest = hashlib.sha256(body["token"].encode("utf-8")).hexdigest()
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT capability_digest FROM task_runs WHERE id = ?",
            (run["id"],),
        ).fetchone()
        lease = conn.execute(
            "SELECT token_digest FROM automation_leases WHERE profile_id = ? AND released_at IS NULL",
            (profile["id"],),
        ).fetchone()
    assert row["capability_digest"] == digest
    assert lease["token_digest"] == digest
    assert body["token"] not in str(dict(row))

    second = issue_capability(client_access, run["id"])
    assert second.status_code in {404, 409}
    assert "cbm_run_" not in second.text


def test_capability_waiting_health_no_token(client_access: TestClient):
    profile = db.create_profile("Waiting", sandbox_id="alpha")
    db.upsert_profile_health(
        profile["id"],
        state="pending",
        checked_at=datetime.now(timezone.utc).isoformat(),
        proxy_configured=False,
        proxy_reachable=None,
        proxy_authenticity_score=None,
        fingerprint_consistency_score=None,
        browser_scan_score=None,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={},
    )
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    created = client_access.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers=bootstrap_headers(),
        json={
            "harness": "browser-use",
            "task": "Wait",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 5,
            "timeout_seconds": 60,
        },
    )
    assert created.status_code == 201
    # Pending health creates health_check status — not claimable as queued.
    empty = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert empty.status_code == 204


def test_blocked_capability_override_releases_claim_and_requeues_for_next_worker(
    client_access: TestClient,
):
    from backend import main

    run, profile = create_and_claim(client_access)
    with db.get_db() as conn:
        lease_id = str(
            conn.execute(
                "SELECT id FROM automation_leases WHERE profile_id = ? AND released_at IS NULL",
                (profile["id"],),
            ).fetchone()["id"]
        )
    handle = main.direct_cdp_socket_registry.register(
        lease_id=lease_id,
        profile_id=profile["id"],
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )

    other_profile = db.create_profile("Unrelated direct lease", sandbox_id="alpha")
    unrelated = main.automation_lease_service.acquire_direct(
        profile_id=other_profile["id"],
        owner_kind="agent",
        owner_id="agent-unrelated",
    )

    seed_blocked_health(profile["id"])
    blocked = issue_capability(client_access, run["id"])
    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "Not ready"}
    assert "cbm_run_" not in blocked.text
    assert handle.revoked.is_set()

    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, claim_eligible_at
            FROM task_runs WHERE id = ?
            """,
            (run["id"],),
        ).fetchone()
        released = conn.execute(
            "SELECT released_at FROM automation_leases WHERE id = ?",
            (lease_id,),
        ).fetchone()
        unrelated_row = conn.execute(
            "SELECT released_at FROM automation_leases WHERE id = ?",
            (unrelated.lease_id,),
        ).fetchone()
    assert row["status"] == "blocked_health"
    assert row["claimed_by"] is None
    assert row["worker_id"] is None
    assert row["claim_expires_at"] is None
    assert row["lease_id"] is None
    assert row["capability_digest"] is None
    assert row["claim_eligible_at"] is None
    assert released["released_at"] is not None
    assert unrelated_row["released_at"] is None

    stale_hb = client_access.post(
        f"/internal/task-runs/{run['id']}/heartbeat",
        headers=worker_headers(),
    )
    assert stale_hb.status_code == 404

    seed_passed_health(profile["id"])
    override = client_access.post(
        f"/api/task-runs/{run['id']}/override-health",
        headers=bootstrap_headers(),
        json={"reason": "operator accepted current health risk"},
    )
    assert override.status_code == 200, override.text
    assert override.json()["status"] == "queued"

    with db.get_db() as conn:
        queued = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, claim_eligible_at
            FROM task_runs WHERE id = ?
            """,
            (run["id"],),
        ).fetchone()
    assert queued["status"] == "queued"
    assert queued["claimed_by"] is None
    assert queued["worker_id"] is None
    assert queued["claim_expires_at"] is None
    assert queued["lease_id"] is None
    assert queued["capability_digest"] is None
    assert queued["claim_eligible_at"] is not None

    reclaimed = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["id"] == run["id"]


def test_blocked_capability_retry_releases_claim_and_requeues_for_next_worker(
    client_access: TestClient,
):
    run, profile = create_and_claim(client_access)
    seed_blocked_health(profile["id"])
    blocked = issue_capability(client_access, run["id"])
    assert blocked.status_code == 409

    seed_passed_health(profile["id"])
    retried = client_access.post(
        f"/api/task-runs/{run['id']}/retry-health",
        headers=bootstrap_headers(),
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"

    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT status, claimed_by, worker_id, claim_expires_at, lease_id,
                   capability_digest, claim_eligible_at
            FROM task_runs WHERE id = ?
            """,
            (run["id"],),
        ).fetchone()
        active_leases = conn.execute(
            """
            SELECT COUNT(*) AS n FROM automation_leases
            WHERE owner_kind = 'worker' AND owner_id = ? AND released_at IS NULL
            """,
            (WORKER_ID,),
        ).fetchone()["n"]
    assert row["status"] == "queued"
    assert row["claimed_by"] is None
    assert row["worker_id"] is None
    assert row["claim_expires_at"] is None
    assert row["lease_id"] is None
    assert row["capability_digest"] is None
    assert row["claim_eligible_at"] is not None
    assert active_leases == 0

    reclaimed = client_access.post("/internal/task-runs/claim", headers=worker_headers())
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["id"] == run["id"]


def test_cdp_http_and_ws_accept_run_capability(client_access: TestClient):
    from backend import main

    run, profile = create_and_claim(client_access)
    cap = issue_capability(client_access, run["id"]).json()
    token = cap["token"]

    main.browser_mgr.running[profile["id"]] = SimpleNamespace(
        ws_port=6100, cdp_port=19222, display=100
    )
    version_payload = {
        "Browser": "Chrome/test",
        "webSocketDebuggerUrl": "ws://127.0.0.1:19222/devtools/browser/ABC",
    }
    list_payload = [
        {
            "id": "1",
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:19222/devtools/page/P1",
        }
    ]

    try:
        with patch("httpx.AsyncClient") as client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def get(url, *args, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
                if url.endswith("/json/version"):
                    resp.json.return_value = version_payload
                else:
                    resp.json.return_value = list_payload
                return resp

            mock_client.get = get
            client_cls.return_value = mock_client

            version = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/version",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert version.status_code == 200, version.text
            body = version.json()
            assert "19222" not in version.text
            assert "Authorization" not in version.text
            assert body["webSocketDebuggerUrl"].startswith("ws://")

            query_rejected = client_access.get(
                f"/api/profiles/{profile['id']}/cdp/json/version?token={token}",
            )
            assert query_rejected.status_code in {400, 401, 404}

            cross = client_access.get(
                "/api/profiles/other-profile/cdp/json/version",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert cross.status_code == 404
    finally:
        main.browser_mgr.running.pop(profile["id"], None)


def test_capability_revoke_closes_sockets_and_blocks_reconnect(
    client_access: TestClient,
):
    from backend import main

    run, profile = create_and_claim(client_access)
    cap = issue_capability(client_access, run["id"]).json()
    token = cap["token"]

    handle = main.direct_cdp_socket_registry.register(
        lease_id="lease-test",
        profile_id=profile["id"],
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )
    # Bind registry to the actual lease id from DB.
    with db.get_db() as conn:
        lease_id = conn.execute(
            "SELECT id FROM automation_leases WHERE profile_id = ? AND released_at IS NULL",
            (profile["id"],),
        ).fetchone()["id"]
    main.direct_cdp_socket_registry.unregister(handle)
    handle = main.direct_cdp_socket_registry.register(
        lease_id=str(lease_id),
        profile_id=profile["id"],
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )

    revoked = client_access.delete(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )
    assert revoked.status_code in {200, 204}
    assert handle.revoked.is_set()

    blocked = client_access.get(
        f"/api/profiles/{profile['id']}/cdp/json/version",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 404

    # Idempotent revoke
    again = client_access.delete(
        f"/internal/task-runs/{run['id']}/capability",
        headers=worker_headers(),
    )
    assert again.status_code in {200, 204, 404}


def test_outputs_screenshot_complete_fail(client_access: TestClient):
    run, _profile = create_and_claim(client_access)
    run_id = run["id"]

    out = client_access.post(
        f"/internal/task-runs/{run_id}/outputs",
        headers=worker_headers(),
        json={
            "idempotency_key": "shot-meta",
            "kind": "screenshot",
            "summary": "Viewport",
            "payload": {},
        },
    )
    assert out.status_code == 201, out.text
    output_id = out.json()["id"]

    png = make_png()
    digest = hashlib.sha256(png).hexdigest()
    put = client_access.put(
        f"/internal/task-runs/{run_id}/screenshots/{output_id}",
        headers={
            **worker_headers(),
            "Content-Type": "image/png",
            "X-CBM-Screenshot-SHA256": digest,
        },
        content=png,
    )
    assert put.status_code in {200, 204}, put.text

    bad_type = client_access.put(
        f"/internal/task-runs/{run_id}/screenshots/{output_id}",
        headers={
            **worker_headers(),
            "Content-Type": "text/plain",
            "X-CBM-Screenshot-SHA256": digest,
        },
        content=png,
    )
    assert bad_type.status_code == 422
    assert digest not in bad_type.text

    oversized = png + (b"\x00" * (5 * 1024 * 1024 + 1 - len(png)))
    huge = client_access.put(
        f"/internal/task-runs/{run_id}/screenshots/{output_id}",
        headers={
            **worker_headers(),
            "Content-Type": "image/png",
            "X-CBM-Screenshot-SHA256": hashlib.sha256(oversized).hexdigest(),
        },
        content=oversized,
    )
    assert huge.status_code == 422

    # Capability + complete
    issue_capability(client_access, run_id)
    done = client_access.post(
        f"/internal/task-runs/{run_id}/complete",
        headers=worker_headers(),
    )
    assert done.status_code == 200
    assert done.json()["status"] == "succeeded"

    # New run for fail path
    run2, _ = create_and_claim(client_access)
    issue_capability(client_access, run2["id"])
    failed = client_access.post(
        f"/internal/task-runs/{run2['id']}/fail",
        headers=worker_headers(),
        json={"error_code": "max_steps", "message": "Step budget exhausted"},
    )
    assert failed.status_code == 200
    body = failed.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "max_steps"

    secret_fail = client_access.post(
        f"/internal/task-runs/{run2['id']}/fail",
        headers=worker_headers(),
        json={
            "error_code": "max_steps",
            "message": "Bearer cbm_run_deadbeef",
        },
    )
    # Already terminal → 404; or if accepted earlier would reject secrets
    assert secret_fail.status_code in {404, 422}


def test_screenshot_upload_rejects_chunked_oversize_without_buffering_tail(
    client_access: TestClient,
    monkeypatch,
):
    from backend import main

    run, _profile = create_and_claim(client_access)
    out = client_access.post(
        f"/internal/task-runs/{run['id']}/outputs",
        headers=worker_headers(),
        json={
            "idempotency_key": "chunked-shot",
            "kind": "screenshot",
            "summary": "Chunked viewport",
            "payload": {},
        },
    )
    assert out.status_code == 201, out.text
    output_id = out.json()["id"]

    first = b"12345678"
    second = b"ABCDEFGH"
    body = first + second
    monkeypatch.setattr(main.artifact_store_mod, "MAX_BYTES", len(first) + 1)

    async def body_forbidden(_request):
        raise AssertionError("request.body() must not be used for screenshot ingest")

    def fail_ingest(*_args, **_kwargs):
        raise AssertionError("oversized content reached artifact store")

    monkeypatch.setattr(main.starlette.requests.Request, "body", body_forbidden)
    monkeypatch.setattr(main.artifact_store, "ingest_screenshot", fail_ingest)

    response = client_access.put(
        f"/internal/task-runs/{run['id']}/screenshots/{output_id}",
        headers={
            **worker_headers(),
            "Content-Type": "image/png",
            "Transfer-Encoding": "chunked",
            "X-CBM-Screenshot-SHA256": hashlib.sha256(body).hexdigest(),
        },
        content=body,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid screenshot"}
    assert "12345678" not in response.text
    assert "ABCDEFGH" not in response.text


def test_public_cancel_revokes_lease_browser_stays_running(client_access: TestClient):
    from backend import main

    run, profile = create_and_claim(client_access)
    cap = issue_capability(client_access, run["id"]).json()
    token = cap["token"]

    with db.get_db() as conn:
        lease_id = str(
            conn.execute(
                "SELECT id FROM automation_leases WHERE profile_id = ? AND released_at IS NULL",
                (profile["id"],),
            ).fetchone()["id"]
        )
    handle = main.direct_cdp_socket_registry.register(
        lease_id=lease_id,
        profile_id=profile["id"],
        owner_kind="worker",
        owner_id=WORKER_ID,
        expires_at=datetime.now(timezone.utc),
    )

    stop_calls = []

    async def fake_stop(pid):
        stop_calls.append(pid)

    monkey_stop = AsyncMock(side_effect=fake_stop)
    main.browser_mgr.stop = monkey_stop

    cancelled = client_access.post(
        f"/api/task-runs/{run['id']}/cancel",
        headers=bootstrap_headers(),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert handle.revoked.is_set()
    assert stop_calls == []

    hb = client_access.post(
        f"/internal/task-runs/{run['id']}/heartbeat",
        headers=worker_headers(),
    )
    assert hb.status_code == 404

    blocked = client_access.get(
        f"/api/profiles/{profile['id']}/cdp/json/version",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 404

    # Idempotent cancel
    again = client_access.post(
        f"/api/task-runs/{run['id']}/cancel",
        headers=bootstrap_headers(),
    )
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


def test_wrong_worker_indistinguishable_404(client_access: TestClient):
    run, _ = create_and_claim(client_access)
    paths = [
        ("POST", f"/internal/task-runs/{run['id']}/heartbeat"),
        ("POST", f"/internal/task-runs/{run['id']}/capability"),
        ("DELETE", f"/internal/task-runs/{run['id']}/capability"),
        ("POST", f"/internal/task-runs/{run['id']}/complete"),
        ("POST", f"/internal/task-runs/{run['id']}/fail"),
    ]
    # Stale run id with valid worker auth
    for method, path in paths:
        if method == "POST" and path.endswith("/fail"):
            resp = client_access.post(
                "/internal/task-runs/does-not-exist/fail",
                headers=worker_headers(),
                json={"error_code": "max_steps", "message": "x"},
            )
        elif method == "POST":
            resp = client_access.post(path.replace(run["id"], "does-not-exist"), headers=worker_headers())
        else:
            resp = client_access.delete(
                path.replace(run["id"], "does-not-exist"),
                headers=worker_headers(),
            )
        assert resp.status_code == 404


def test_run_capability_cdp_route_matrix_rejects_non_exact_surfaces(
    client_access: TestClient,
):
    """Run bearer may bypass only exact HTTP discovery GETs and CDP WS paths."""
    from backend import main

    run, profile = create_and_claim(client_access)
    token = issue_capability(client_access, run["id"]).json()["token"]
    pid = profile["id"]
    headers = {"Authorization": f"Bearer {token}"}

    main.browser_mgr.running[pid] = SimpleNamespace(
        ws_port=6100, cdp_port=19222, display=100
    )
    upstream_calls: list[str] = []

    try:
        with patch("httpx.AsyncClient") as client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def get(url, *args, **kwargs):
                upstream_calls.append(str(url))
                resp = MagicMock()
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
                if str(url).endswith("/json/version"):
                    resp.json.return_value = {
                        "Browser": "Chrome/test",
                        "webSocketDebuggerUrl": "ws://127.0.0.1:19222/devtools/browser/ABC",
                    }
                else:
                    resp.json.return_value = []
                return resp

            mock_client.get = get
            client_cls.return_value = mock_client

            # Allowed HTTP discovery GETs.
            version = client_access.get(
                f"/api/profiles/{pid}/cdp/json/version", headers=headers
            )
            assert version.status_code == 200, version.text
            listed = client_access.get(
                f"/api/profiles/{pid}/cdp/json/list", headers=headers
            )
            assert listed.status_code == 200, listed.text
            allowed_upstream = list(upstream_calls)

            # Helper/base HTTP route must reject run bearer before handler/upstream.
            upstream_calls.clear()
            helper = client_access.get(f"/api/profiles/{pid}/cdp", headers=headers)
            assert helper.status_code in {401, 404}
            assert upstream_calls == []

            # Other HTTP paths / methods must not bypass.
            rejected_http = [
                ("GET", f"/api/profiles/{pid}/cdp/json"),
                ("GET", f"/api/profiles/{pid}/cdp/json/"),
                ("POST", f"/api/profiles/{pid}/cdp/json/version"),
                ("PUT", f"/api/profiles/{pid}/cdp/json/list"),
                ("GET", f"/api/profiles/{pid}/cdp/json/version/extra"),
                ("GET", f"/api/profiles/{pid}/cdp/devtools/page/P1"),
                ("GET", f"/api/profiles/{pid}/status"),
            ]
            for method, path in rejected_http:
                upstream_calls.clear()
                resp = client_access.request(method, path, headers=headers)
                assert resp.status_code in {401, 404, 405}, (method, path, resp.status_code)
                assert upstream_calls == []
                assert token not in resp.text

            # Query tokens remain 400/4400-class without upstream.
            upstream_calls.clear()
            query = client_access.get(
                f"/api/profiles/{pid}/cdp/json/version?token={token}",
            )
            assert query.status_code in {400, 401, 404}
            assert upstream_calls == []

            # Direct normal actor auth unchanged on helper route.
            agent = client_access.post(
                "/api/access/agents",
                headers=bootstrap_headers(),
                json={
                    "display_name": "CDP Agent",
                    "paperclip_agent_id": "paperclip-cdp-matrix",
                    "grants": [{"sandbox_id": "alpha", "permission": "automate"}],
                },
            )
            assert agent.status_code == 201, agent.text
            agent_key = agent.json()["api_key"]
            lease = client_access.post(
                f"/api/profiles/{pid}/automation-leases",
                headers={"Authorization": f"Bearer {agent_key}"},
            )
            # Profile may already hold run lease — either busy or acquired.
            if lease.status_code == 201:
                lease_headers = {
                    "Authorization": f"Bearer {agent_key}",
                    "X-CBM-Automation-Lease": lease.json()["token"],
                }
                upstream_calls.clear()
                actor_helper = client_access.get(
                    f"/api/profiles/{pid}/cdp", headers=lease_headers
                )
                assert actor_helper.status_code == 200, actor_helper.text
                assert upstream_calls == []  # helper does not call upstream CDP

            assert allowed_upstream  # version/list did reach upstream when allowed

        # WebSocket: browser + page paths are allowlisted for run capability.
        # HTTP discovery paths must not be treated as WS allow surfaces.
        try:
            with client_access.websocket_connect(
                f"/api/profiles/{pid}/cdp",
                headers={"Authorization": f"Bearer {token}"},
            ) as _ws:
                pass
        except WebSocketDisconnect as exc:
            # Upstream may be absent; auth bypass itself must not be 401-class.
            assert exc.code not in {4401}

        with pytest.raises(WebSocketDisconnect) as denied_ws:
            with client_access.websocket_connect(
                f"/api/profiles/{pid}/cdp/json/version",
                headers={"Authorization": f"Bearer {token}"},
            ):
                pass
        assert denied_ws.value.code in {1000, 1006, 1008, 4000, 4400, 4401, 4403, 4404}

        try:
            with client_access.websocket_connect(
                f"/api/profiles/{pid}/cdp/devtools/page/P1",
                headers={"Authorization": f"Bearer {token}"},
            ) as _ws:
                pass
        except WebSocketDisconnect as exc:
            assert exc.code not in {4401}
    finally:
        main.browser_mgr.running.pop(pid, None)
