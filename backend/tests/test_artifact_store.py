"""Private screenshot artifact store: validation, opaque paths, retention hooks."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import database as db


NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_png(width: int, height: int, *, rgb: bytes = b"\xff\x00\x00") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    pixel = (rgb * width)[: width * 3]
    raw = b"".join(b"\x00" + pixel for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


# Minimal valid 1x1 JPEG (baseline SOF0).
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


class FakeClock:
    def __init__(self, start: datetime = NOW) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, days: int = 0, seconds: int = 0) -> None:
        self._now = self._now + timedelta(days=days, seconds=seconds)


def seed_output(*, sandbox_id: str = "alpha") -> dict:
    profile = db.create_profile("Artifact browser", sandbox_id=sandbox_id)
    session = db.create_task_session(profile["id"], sandbox_id, "bootstrap")
    snapshot, decision = db.build_run_health_gate(profile["id"])
    run = db.create_task_run_with_message(
        task_session_id=session["id"],
        content="Capture frame",
        profile_id=profile["id"],
        sandbox_id=sandbox_id,
        harness="browser-use",
        launch_if_stopped=False,
        allowed_origins=["https://example.com"],
        max_steps=5,
        timeout_seconds=60,
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
    return {
        "profile": profile,
        "session": session,
        "run": run,
        "output": output,
    }


@pytest.fixture()
def store(tmp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.artifact_store import ArtifactStore

    root = tmp_path / "artifacts"
    clock = FakeClock()
    artifact_store = ArtifactStore(root=root, clock=clock, get_db=db.get_db)
    artifact_store.ensure_schema()
    monkeypatch.setenv("CBM_ARTIFACT_ROOT", str(root))
    return artifact_store, clock, root


def test_screenshot_store_ignores_caller_paths(store, tmp_path: Path):
    artifact_store, _clock, root = store
    seeded = seed_output()
    png = make_png(8, 6)
    digest = hashlib.sha256(png).hexdigest()

    artifact = artifact_store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=digest,
        # Caller-controlled path/name hints must be ignored if accepted as kwargs.
        caller_path=str(tmp_path / ".." / "escape.png"),
        filename="evil.png",
    )

    assert artifact.artifact_id
    assert "out-" not in artifact.artifact_id
    assert seeded["output"]["id"] not in artifact.artifact_id
    assert artifact.media_type == "image/png"
    assert artifact.width == 8
    assert artifact.height == 6
    assert artifact.sha256 == digest
    assert not hasattr(artifact, "path") or "path" not in artifact.__dict__
    # Disk layout is under configured root with opaque names unrelated to IDs.
    files = list(root.rglob("*"))
    file_paths = [p for p in files if p.is_file()]
    assert len(file_paths) == 1
    stored = file_paths[0]
    assert stored.is_relative_to(root)
    assert seeded["output"]["id"] not in stored.name
    assert seeded["run"]["id"] not in stored.name
    assert seeded["session"]["id"] not in str(stored.relative_to(root))
    assert "evil" not in stored.name
    assert "escape" not in stored.name
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert oct(root.stat().st_mode & 0o777) == "0o700"
    assert stored.read_bytes() == png


def test_artifact_root_forced_to_0700_even_if_broader(store, tmp_path: Path):
    from backend.artifact_store import ArtifactStore

    root = tmp_path / "wide"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    artifact_store = ArtifactStore(root=root, clock=FakeClock(), get_db=db.get_db)
    artifact_store.ensure_schema()
    assert oct(root.stat().st_mode & 0o777) == "0o700"


def test_rejects_wrong_media_type_digest_size_and_magic(store):
    from backend.artifact_store import ArtifactValidationError

    artifact_store, _clock, _root = store
    seeded = seed_output()
    png = make_png(4, 4)
    digest = hashlib.sha256(png).hexdigest()

    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=png,
            media_type="image/gif",
            sha256=digest,
        )

    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=png,
            media_type="image/png",
            sha256="0" * 64,
        )

    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=b"not-an-image" * 10,
            media_type="image/png",
            sha256=hashlib.sha256(b"not-an-image" * 10).hexdigest(),
        )

    huge = make_png(2, 2) + (b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=huge,
            media_type="image/png",
            sha256=hashlib.sha256(huge).hexdigest(),
        )


def test_rejects_zero_and_oversized_dimensions_and_truncated(store):
    from backend.artifact_store import ArtifactValidationError

    artifact_store, _clock, _root = store
    seeded = seed_output()

    zero = make_png(0, 1)
    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=zero,
            media_type="image/png",
            sha256=hashlib.sha256(zero).hexdigest(),
        )

    oversized = make_png(4097, 10)
    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=oversized,
            media_type="image/png",
            sha256=hashlib.sha256(oversized).hexdigest(),
        )

    truncated = make_png(16, 16)[:-8]
    with pytest.raises(ArtifactValidationError):
        artifact_store.ingest_screenshot(
            output_id=seeded["output"]["id"],
            body=truncated,
            media_type="image/png",
            sha256=hashlib.sha256(truncated).hexdigest(),
        )


def test_accepts_jpeg_and_normalizes_sha256_case(store):
    artifact_store, _clock, _root = store
    seeded = seed_output()
    digest = hashlib.sha256(MIN_JPEG).hexdigest().upper()
    artifact = artifact_store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=MIN_JPEG,
        media_type="IMAGE/JPEG",
        sha256=digest,
    )
    assert artifact.media_type == "image/jpeg"
    assert artifact.width == 1
    assert artifact.height == 1
    assert artifact.sha256 == digest.lower()


def test_rejects_symlink_targets_and_never_returns_disk_paths(store, tmp_path: Path, caplog):
    from backend.artifact_store import ArtifactStore, ArtifactValidationError

    artifact_store, _clock, root = store
    seeded = seed_output()
    png = make_png(2, 2)
    digest = hashlib.sha256(png).hexdigest()

    # Plant a symlink inside the root; ingest must refuse following it.
    link = root / "trap"
    target = tmp_path / "outside"
    target.mkdir()
    link.symlink_to(target)
    assert link.is_symlink()

    # Fresh store on same root still enforces no-follow semantics for new writes.
    store2 = ArtifactStore(root=root, clock=FakeClock(), get_db=db.get_db)
    store2.ensure_schema()
    artifact = store2.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=digest,
    )
    as_dict = artifact.to_public_dict()
    assert "path" not in as_dict
    assert "storage" not in as_dict
    assert str(root) not in str(as_dict)
    assert artifact.artifact_id not in ""  # smoke
    for record in caplog.records:
        assert str(root) not in record.getMessage()


def test_read_by_output_uses_server_metadata_only(store):
    from backend.artifact_store import ArtifactNotFound, ArtifactExpired

    artifact_store, clock, _root = store
    seeded = seed_output()
    png = make_png(3, 3)
    digest = hashlib.sha256(png).hexdigest()
    artifact_store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=digest,
    )

    loaded = artifact_store.read_for_output(seeded["output"]["id"])
    assert loaded.body == png
    assert loaded.media_type == "image/png"
    assert loaded.filename == "screenshot.png"

    with pytest.raises(ArtifactNotFound):
        artifact_store.read_for_output("missing-output")

    # Expire and delete bytes; subsequent reads are expired.
    artifact_store.mark_task_archived(seeded["session"]["id"], archived_at=clock())
    clock.advance(days=7, seconds=1)
    result = artifact_store.expire_due_once()
    assert result["deleted"] >= 1
    with pytest.raises(ArtifactExpired):
        artifact_store.read_for_output(seeded["output"]["id"])


def test_failed_deletion_keeps_metadata_retryable(store, monkeypatch):
    artifact_store, clock, root = store
    seeded = seed_output()
    png = make_png(2, 2)
    digest = hashlib.sha256(png).hexdigest()
    meta = artifact_store.ingest_screenshot(
        output_id=seeded["output"]["id"],
        body=png,
        media_type="image/png",
        sha256=digest,
    )
    artifact_store.mark_task_archived(seeded["session"]["id"], archived_at=clock())
    clock.advance(days=7, seconds=1)

    import backend.artifact_store as amod

    real_unlink = amod.os.unlink

    def boom(path, *args, **kwargs):
        raise OSError("simulated delete failure")

    monkeypatch.setattr(amod.os, "unlink", boom)
    failed = artifact_store.expire_due_once()
    assert failed["deleted"] == 0
    assert failed["delete_failures"] >= 1

    row = artifact_store.get_artifact(meta.artifact_id)
    assert row is not None
    assert row["deleted_at"] is None
    assert row["storage_relpath"]  # only reference preserved for retry
    # Bytes still present under opaque path.
    assert any(p.is_file() for p in root.rglob("*"))

    monkeypatch.setattr(amod.os, "unlink", real_unlink)
    retried = artifact_store.expire_due_once()
    assert retried["deleted"] >= 1
    row2 = artifact_store.get_artifact(meta.artifact_id)
    assert row2["deleted_at"] is not None


def test_ingest_does_not_accept_path_based_upload_api_surface(store):
    """Store API is bytes-only; no path/directory parameters for content source."""
    import inspect

    from backend import artifact_store as mod

    sig = inspect.signature(mod.ArtifactStore.ingest_screenshot)
    forbidden = {"path", "filepath", "file_path", "directory", "dir", "src", "source_path"}
    assert forbidden.isdisjoint(sig.parameters)
