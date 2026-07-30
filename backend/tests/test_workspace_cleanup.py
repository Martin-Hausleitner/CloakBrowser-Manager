"""Clock-injected temporary-task retention and screenshot cleanup."""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend import database as db


NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_png(width: int = 2, height: int = 2) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + (b"\x00\xff\x00" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class FakeClock:
    def __init__(self, start: datetime = NOW) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, days: int = 0, seconds: int = 0) -> None:
        self._now = self._now + timedelta(days=days, seconds=seconds)


def _set_task_fields(task_id: str, **fields: Any) -> None:
    with db.get_db() as conn:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE task_sessions SET {assignments} WHERE id = ?",
            (*fields.values(), task_id),
        )
        conn.commit()


def _seed_task(
    *,
    retention_class: str,
    sandbox_id: str = "alpha",
    with_screenshot: bool = False,
    title: str = "temp",
) -> dict[str, Any]:
    profile = db.create_profile(f"{title}-browser", sandbox_id=sandbox_id)
    session = db.create_task_session(profile["id"], sandbox_id, "bootstrap", title=title)
    _set_task_fields(
        session["id"],
        retention_class=retention_class,
        activity_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
    )
    session = db.get_task_session(session["id"])
    assert session is not None
    result: dict[str, Any] = {"profile": profile, "session": session, "artifact": None}
    if with_screenshot:
        from backend.artifact_store import ArtifactStore

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
        # create_task_run_with_message bumps activity_at — freeze it again.
        _set_task_fields(session["id"], activity_at=NOW.isoformat())
        output = db.append_task_output(
            run["id"],
            idempotency_key="shot",
            kind="screenshot",
            summary="frame",
            payload={},
        )
        _set_task_fields(session["id"], activity_at=NOW.isoformat())
        result["run"] = run
        result["output"] = output
        result["_png"] = make_png()
        result["_run"] = run
    return result


@pytest.fixture()
def maintenance(tmp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.artifact_store import ArtifactStore
    from backend.workspace_maintenance import WorkspaceMaintenance

    clock = FakeClock()
    root = tmp_path / "artifacts"
    store = ArtifactStore(root=root, clock=clock, get_db=db.get_db)
    store.ensure_schema()
    audit = MagicMock()
    maint = WorkspaceMaintenance(
        clock=clock,
        artifact_store=store,
        get_db=db.get_db,
        audit=audit,
    )
    monkeypatch.setenv("CBM_ARTIFACT_ROOT", str(root))
    return maint, clock, store, root, audit


def test_temporary_task_archives_after_strictly_more_than_seven_days(maintenance):
    maint, clock, _store, _root, audit = maintenance
    seeded = _seed_task(retention_class="temporary", title="archive-me")

    clock.advance(days=7)
    maint.cleanup_retention_once()
    still = db.get_task_session(seeded["session"]["id"])
    assert still is not None
    assert still["archived_at"] is None

    clock.advance(seconds=1)
    result = maint.cleanup_retention_once()
    archived = db.get_task_session(seeded["session"]["id"])
    assert archived is not None
    assert archived["archived_at"]
    assert archived["status"] == "archived"
    assert result["archived_tasks"] >= 1
    # Audit logs IDs/counts only — never paths.
    for call in audit.call_args_list:
        rendered = " ".join(str(arg) for arg in call.args) + " ".join(
            f"{k}={v}" for k, v in call.kwargs.items()
        )
        assert "/artifacts" not in rendered
        assert "secret" not in rendered.lower()


def test_activity_resets_inactivity_archive_window(maintenance):
    maint, clock, _store, _root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", title="active")

    clock.advance(days=6)
    # Explicit user activity bumps activity_at.
    _set_task_fields(
        seeded["session"]["id"],
        activity_at=clock().isoformat(),
        updated_at=clock().isoformat(),
    )

    clock.advance(days=7)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"])["archived_at"] is None

    clock.advance(seconds=1)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"])["archived_at"]


def test_reopen_clears_archive_and_reschedules_inactivity(maintenance):
    maint, clock, store, _root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", with_screenshot=True, title="reopen")
    png = seeded["_png"]
    store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )

    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()
    archived = db.get_task_session(seeded["session"]["id"])
    assert archived["archived_at"]

    # Reopen clears archived_at, updates activity, clears screenshot expiry schedule.
    reopened_at = clock()
    _set_task_fields(
        seeded["session"]["id"],
        archived_at=None,
        status="active",
        activity_at=reopened_at.isoformat(),
        updated_at=reopened_at.isoformat(),
    )
    store.mark_task_reopened(seeded["session"]["id"])

    clock.advance(days=6)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"])["archived_at"] is None
    # Screenshot still readable (not expired from prior archive).
    loaded = store.read_for_output(seeded["output"]["id"])
    assert loaded.body == png

    clock.advance(days=1, seconds=1)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"])["archived_at"]


def test_project_and_legacy_tasks_never_auto_expire(maintenance):
    maint, clock, _store, _root, _audit = maintenance
    project = _seed_task(retention_class="project", title="keep-project")
    legacy = _seed_task(retention_class="legacy", title="keep-legacy")

    clock.advance(days=400)
    maint.cleanup_retention_once()
    assert db.get_task_session(project["session"]["id"])["archived_at"] is None
    assert db.get_task_session(legacy["session"]["id"])["archived_at"] is None
    assert db.get_task_session(project["session"]["id"]) is not None
    assert db.get_task_session(legacy["session"]["id"]) is not None


def test_screenshot_bytes_expire_seven_days_after_archival(maintenance):
    maint, clock, store, root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", with_screenshot=True, title="shot-exp")
    png = seeded["_png"]
    artifact = store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )

    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"])["archived_at"]
    assert any(p.is_file() for p in root.rglob("*"))

    clock.advance(days=7)
    maint.cleanup_retention_once()
    assert any(p.is_file() for p in root.rglob("*"))  # exact 7d after archive: keep

    clock.advance(seconds=1)
    maint.cleanup_retention_once()
    assert not any(p.is_file() for p in root.rglob("*"))
    row = store.get_artifact(artifact.artifact_id)
    assert row["deleted_at"] is not None
    # Output row remains with artifact_expired.
    output = db.get_task_output(seeded["output"]["id"])
    assert output is not None
    assert output.get("artifact_expired") is True


def test_task_purge_thirty_days_after_archival_preserves_profile(maintenance):
    maint, clock, store, root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", with_screenshot=True, title="purge")
    png = seeded["_png"]
    store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )
    profile_id = seeded["profile"]["id"]

    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()  # archive
    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()  # expire screenshot bytes
    assert not any(p.is_file() for p in root.rglob("*"))

    clock.advance(days=22)  # 7+7+22 = 36 from start; 30d after archive needs +30 from archive
    # After archive at t+7s+1, then +7d+1 screenshot expire, need total 30d after archive.
    # Recalculate from archived_at:
    archived = db.get_task_session(seeded["session"]["id"])
    assert archived is not None
    # Jump clock to archived_at + 30 days exactly — must NOT purge.
    clock._now = datetime.fromisoformat(archived["archived_at"]) + timedelta(days=30)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"]) is not None

    clock.advance(seconds=1)
    result = maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"]) is None
    assert result["purged_tasks"] >= 1
    assert db.get_profile(profile_id) is not None
    assert not any(p.is_file() for p in root.rglob("*"))


def test_failed_screenshot_deletion_blocks_task_purge(maintenance, monkeypatch):
    maint, clock, store, root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", with_screenshot=True, title="block-purge")
    png = seeded["_png"]
    meta = store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )

    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()

    import backend.artifact_store as amod

    real_unlink = amod.os.unlink

    def boom(path, *args, **kwargs):
        raise OSError("delete blocked")

    monkeypatch.setattr(amod.os, "unlink", boom)
    clock.advance(days=7, seconds=1)
    maint.cleanup_retention_once()
    row = store.get_artifact(meta.artifact_id)
    assert row["deleted_at"] is None
    assert any(p.is_file() for p in root.rglob("*"))

    # Even past purge window, task remains because bytes could not be removed.
    clock.advance(days=30)
    maint.cleanup_retention_once()
    assert db.get_task_session(seeded["session"]["id"]) is not None
    assert store.get_artifact(meta.artifact_id)["deleted_at"] is None

    monkeypatch.setattr(amod.os, "unlink", real_unlink)
    maint.cleanup_retention_once()
    # Bytes deleted then task purged in the same pass; metadata row is removed with purge.
    assert store.get_artifact(meta.artifact_id) is None
    assert db.get_task_session(seeded["session"]["id"]) is None
    assert not any(p.is_file() for p in root.rglob("*"))


def test_cleanup_is_idempotent(maintenance):
    maint, clock, _store, _root, _audit = maintenance
    seeded = _seed_task(retention_class="temporary", title="idem")
    clock.advance(days=7, seconds=1)
    first = maint.cleanup_retention_once()
    second = maint.cleanup_retention_once()
    assert first["archived_tasks"] >= 1
    assert second["archived_tasks"] == 0
    assert db.get_task_session(seeded["session"]["id"])["archived_at"]


def test_repair_sets_missing_expiry_on_archived_task_artifacts(maintenance):
    """Archived artifacts with NULL/wrong expiry are repaired before deletion pass."""
    maint, clock, store, _root, _audit = maintenance
    seeded = _seed_task(retention_class="project", with_screenshot=True, title="repair-arch")
    png = seeded["_png"]
    meta = store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )
    archived = db.update_task_session(
        seeded["session"]["id"],
        expected_row_version=int(seeded["session"]["row_version"]),
        archived=True,
    )
    assert archived is not None
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_artifacts SET expires_at = NULL WHERE id = ?",
            (meta.artifact_id,),
        )
        conn.commit()
    assert store.get_artifact(meta.artifact_id)["expires_at"] is None

    maint.cleanup_retention_once()
    row = store.get_artifact(meta.artifact_id)
    assert row is not None
    assert row["expires_at"] is not None
    expected = datetime.fromisoformat(archived["archived_at"]) + timedelta(days=7)
    assert datetime.fromisoformat(row["expires_at"]) == expected
    # Bytes still present; not yet due.
    loaded = store.read_for_output(seeded["output"]["id"])
    assert loaded.body == png


def test_repair_clears_stale_expiry_on_reopened_task_artifacts(maintenance):
    """Unarchived undeleted artifacts with stale expiry are cleared before deletion."""
    maint, clock, store, _root, _audit = maintenance
    seeded = _seed_task(retention_class="project", with_screenshot=True, title="repair-reopen")
    png = seeded["_png"]
    meta = store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=hashlib.sha256(png).hexdigest(),
    )
    # Plant a stale expiry while the task remains active/unarchived.
    stale = (clock() - timedelta(days=1)).isoformat()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE task_artifacts SET expires_at = ? WHERE id = ?",
            (stale, meta.artifact_id),
        )
        conn.commit()

    maint.cleanup_retention_once()
    row = store.get_artifact(meta.artifact_id)
    assert row is not None
    assert row["expires_at"] is None
    assert row["deleted_at"] is None
    loaded = store.read_for_output(seeded["output"]["id"])
    assert loaded.body == png
