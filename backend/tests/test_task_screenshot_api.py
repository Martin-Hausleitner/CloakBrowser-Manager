"""Public screenshot retrieval: auth, headers, cross-sandbox 404, expiry."""

from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db


NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_png(width: int = 4, height: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + (b"\x11\x22\x33" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


MIN_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000"
    "ffdb004300080606070605080707070909080a0c140d0c0b0b0c191213"
    "0f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d"
    "38323c2e333432"
    "ffc0000b080001000101011100"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffda0008010100003f00bf"
    "ffd9"
)


@pytest.fixture()
def client_access(tmp_db, tmp_path, monkeypatch):
    from backend import main
    from backend.artifact_store import ArtifactStore

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    root = tmp_path / "artifacts"
    store = ArtifactStore(root=root, get_db=db.get_db)
    store.ensure_schema()
    monkeypatch.setattr(main, "artifact_store", store, raising=False)
    monkeypatch.setenv("CBM_ARTIFACT_ROOT", str(root))

    with TestClient(main.app) as client:
        yield client, store


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


def seed_screenshot(store, *, sandbox_id: str = "alpha", body: bytes | None = None):
    profile = db.create_profile("Shot browser", sandbox_id=sandbox_id)
    session = db.create_task_session(profile["id"], sandbox_id, "bootstrap")
    snapshot, decision = db.build_run_health_gate(profile["id"])
    run = db.create_task_run_with_message(
        task_session_id=session["id"],
        content="shot",
        profile_id=profile["id"],
        sandbox_id=sandbox_id,
        harness="browser-use",
        launch_if_stopped=False,
        allowed_origins=["https://example.com"],
        max_steps=3,
        timeout_seconds=30,
        model_alias=None,
        health_snapshot=snapshot,
        health_decision=decision,
        created_by_kind="test",
        created_by_id="tester",
    )
    output = db.append_task_output(
        run["id"],
        idempotency_key="shot-1",
        kind="screenshot",
        summary="frame",
        payload={},
    )
    png = body if body is not None else make_png()
    artifact = store.ingest_screenshot(
        output_id=output["id"],
        body=png,
        media_type="image/png" if png[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg",
        sha256=hashlib.sha256(png).hexdigest(),
    )
    return {
        "profile": profile,
        "session": session,
        "run": run,
        "output": output,
        "artifact": artifact,
        "body": png,
    }


def test_authorized_view_retrieves_screenshot_with_safe_headers(client_access):
    client, store = client_access
    seeded = seed_screenshot(store)
    password = create_user(client, "alpha-view", "alpha", "view")
    login(client, "alpha-view", password)

    response = client.get(f"/api/task-outputs/{seeded['output']['id']}/screenshot")
    assert response.status_code == 200
    assert response.content == seeded["body"]
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == 'inline; filename="screenshot.png"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private" in response.headers["cache-control"]
    assert "no-store" in response.headers["cache-control"]
    # Never expose artifact root or storage path.
    assert "artifacts" not in response.text
    assert "storage_relpath" not in response.text
    for value in response.headers.values():
        assert "/artifacts" not in value


def test_jpeg_uses_screenshot_jpg_disposition(client_access):
    client, store = client_access
    seeded = seed_screenshot(store, body=MIN_JPEG)
    password = create_user(client, "alpha-view-jpg", "alpha", "view")
    login(client, "alpha-view-jpg", password)

    response = client.get(f"/api/task-outputs/{seeded['output']['id']}/screenshot")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["content-disposition"] == 'inline; filename="screenshot.jpg"'


def test_cross_sandbox_missing_and_expired_are_indistinguishable_404(client_access):
    client, store = client_access
    alpha = seed_screenshot(store, sandbox_id="alpha")
    beta = seed_screenshot(store, sandbox_id="beta")

    password = create_user(client, "alpha-only", "alpha", "view")
    login(client, "alpha-only", password)

    cross = client.get(f"/api/task-outputs/{beta['output']['id']}/screenshot")
    missing = client.get("/api/task-outputs/does-not-exist/screenshot")
    assert cross.status_code == 404
    assert missing.status_code == 404
    assert cross.json() == missing.json()

    # Expire alpha screenshot bytes.
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE task_sessions
            SET archived_at = ?, status = 'archived', updated_at = ?
            WHERE id = ?
            """,
            (NOW.isoformat(), NOW.isoformat(), alpha["session"]["id"]),
        )
        conn.commit()
    store.mark_task_archived(alpha["session"]["id"], archived_at=NOW)
    from datetime import timedelta

    store._clock = lambda: NOW + timedelta(days=7, seconds=1)  # type: ignore[method-assign]
    store.expire_due_once()
    expired = client.get(f"/api/task-outputs/{alpha['output']['id']}/screenshot")
    assert expired.status_code == 404
    assert expired.json() == missing.json()


def test_output_list_exposes_artifact_expired_without_weakening_payload(client_access):
    client, store = client_access
    seeded = seed_screenshot(store)
    password = create_user(client, "alpha-auto", "alpha", "automate", "view")
    login(client, "alpha-auto", password)

    listed = client.get(f"/api/task-runs/{seeded['run']['id']}/outputs")
    assert listed.status_code == 200
    body = listed.json()
    shot = next(item for item in body if item["id"] == seeded["output"]["id"])
    assert shot["artifact_expired"] is False
    assert "path" not in shot["payload"]
    assert set(shot["payload"]) == {"artifact_id", "width", "height", "media_type", "sha256"}
    assert shot["payload"]["artifact_id"] == seeded["artifact"].artifact_id
    assert shot["payload"]["width"] == seeded["artifact"].width
    assert shot["payload"]["height"] == seeded["artifact"].height
    assert shot["payload"]["media_type"] == seeded["artifact"].media_type
    assert shot["payload"]["sha256"] == seeded["artifact"].sha256

    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE task_sessions
            SET archived_at = ?, status = 'archived', updated_at = ?
            WHERE id = ?
            """,
            (NOW.isoformat(), NOW.isoformat(), seeded["session"]["id"]),
        )
        conn.commit()
    store.mark_task_archived(seeded["session"]["id"], archived_at=NOW)
    from datetime import timedelta

    store._clock = lambda: NOW + timedelta(days=7, seconds=1)  # type: ignore[method-assign]
    store.expire_due_once()

    listed2 = client.get(f"/api/task-runs/{seeded['run']['id']}/outputs")
    shot2 = next(item for item in listed2.json() if item["id"] == seeded["output"]["id"])
    assert shot2["artifact_expired"] is True
    # Metadata remains server-derived from task_artifacts; never from forged output payload.
    assert shot2["payload"]["artifact_id"] == seeded["artifact"].artifact_id
    assert "path" not in shot2["payload"]


def test_pending_screenshot_output_has_empty_payload_and_404_bytes(client_access):
    """Phase-1 screenshot row (no ingest yet) exposes empty payload; bytes 404."""
    client, _store = client_access
    profile = db.create_profile("Pending shot", sandbox_id="alpha")
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    snapshot, decision = db.build_run_health_gate(profile["id"])
    run = db.create_task_run_with_message(
        task_session_id=session["id"],
        content="pending shot",
        profile_id=profile["id"],
        sandbox_id="alpha",
        harness="browser-use",
        launch_if_stopped=False,
        allowed_origins=["https://example.com"],
        max_steps=3,
        timeout_seconds=30,
        model_alias=None,
        health_snapshot=snapshot,
        health_decision=decision,
        created_by_kind="test",
        created_by_id="tester",
    )
    # Simulate a forged payload_json that must never surface.
    output = db.append_task_output(
        run["id"],
        idempotency_key="pending-1",
        kind="screenshot",
        summary="waiting for upload",
        payload={},
    )
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_outputs SET payload_json = ? WHERE id = ?",
            (
                '{"artifact_id":"forged-id","width":1,"height":1,'
                '"media_type":"image/png","sha256":"' + ("c" * 64) + '"}',
                output["id"],
            ),
        )
        conn.commit()

    password = create_user(client, "alpha-pending", "alpha", "automate", "view")
    login(client, "alpha-pending", password)

    listed = client.get(f"/api/task-runs/{run['id']}/outputs")
    assert listed.status_code == 200
    shot = next(item for item in listed.json() if item["id"] == output["id"])
    assert shot["payload"] == {}
    assert shot["artifact_expired"] is False
    assert "forged-id" not in listed.text

    missing = client.get(f"/api/task-outputs/{output['id']}/screenshot")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Screenshot not found"}
    assert "forged-id" not in missing.text


def test_screenshot_payload_derived_after_ingest_not_from_output_row(client_access):
    client, store = client_access
    seeded = seed_screenshot(store)
    # Corrupt stored output payload; responses must still use task_artifacts.
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_outputs SET payload_json = ? WHERE id = ?",
            ('{"artifact_id":"forged","width":9,"height":9}', seeded["output"]["id"]),
        )
        conn.commit()

    password = create_user(client, "alpha-derived", "alpha", "automate", "view")
    login(client, "alpha-derived", password)
    listed = client.get(f"/api/task-runs/{seeded['run']['id']}/outputs")
    shot = next(item for item in listed.json() if item["id"] == seeded["output"]["id"])
    assert shot["payload"]["artifact_id"] == seeded["artifact"].artifact_id
    assert shot["payload"]["artifact_id"] != "forged"
    assert shot["payload"]["width"] == seeded["artifact"].width
    assert "forged" not in listed.text
