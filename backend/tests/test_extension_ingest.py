"""Tests for secure extension ZIP quarantine ingest."""

from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any, Mapping

import pytest

from backend import database
from backend.extension_ingest import ExtensionIngestError, ingest_extension_zip


def _zip_bytes(
    entries: Mapping[str, bytes | str], *, compression: int = zipfile.ZIP_DEFLATED
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _zip_bytes_with_directory_collision() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", _manifest())
        archive.writestr("assets/", "")
        archive.writestr("assets", "file")
    return buffer.getvalue()


def _with_encrypted_flag(payload: bytes) -> bytes:
    patched = bytearray(payload)
    local_offset = patched.find(b"PK\x03\x04")
    central_offset = patched.find(b"PK\x01\x02")
    assert local_offset >= 0
    assert central_offset >= 0
    patched[local_offset + 6 : local_offset + 8] = (
        int.from_bytes(patched[local_offset + 6 : local_offset + 8], "little") | 0x1
    ).to_bytes(2, "little")
    patched[central_offset + 8 : central_offset + 10] = (
        int.from_bytes(patched[central_offset + 8 : central_offset + 10], "little") | 0x1
    ).to_bytes(2, "little")
    return bytes(patched)


def _with_compress_type(payload: bytes, member_name: str, method: int) -> bytes:
    patched = bytearray(payload)
    offset = 0
    while True:
        offset = patched.find(b"PK\x03\x04", offset)
        if offset < 0:
            break
        name_len = int.from_bytes(patched[offset + 26 : offset + 28], "little")
        extra_len = int.from_bytes(patched[offset + 28 : offset + 30], "little")
        name = bytes(patched[offset + 30 : offset + 30 + name_len]).decode("utf-8")
        if name == member_name:
            patched[offset + 8 : offset + 10] = method.to_bytes(2, "little")
            break
        offset += 30 + name_len + extra_len

    offset = 0
    while True:
        offset = patched.find(b"PK\x01\x02", offset)
        if offset < 0:
            break
        name_len = int.from_bytes(patched[offset + 28 : offset + 30], "little")
        extra_len = int.from_bytes(patched[offset + 30 : offset + 32], "little")
        comment_len = int.from_bytes(patched[offset + 32 : offset + 34], "little")
        name = bytes(patched[offset + 46 : offset + 46 + name_len]).decode("utf-8")
        if name == member_name:
            patched[offset + 10 : offset + 12] = method.to_bytes(2, "little")
            break
        offset += 46 + name_len + extra_len + comment_len
    return bytes(patched)


def _manifest(**overrides: object) -> str:
    payload = {
        "manifest_version": 3,
        "name": "Fixture Extension",
        "version": "1.2.3",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _ingest(payload: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mapping[str, Any]:
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    return ingest_extension_zip(
        payload,
        actor={"kind": "test", "id": "agent-1"},
        source={"kind": "upload", "label": "pytest"},
    )


def test_ingests_valid_mv3_zip_into_content_addressed_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip_bytes(
        {
            "manifest.json": _manifest(permissions=["storage"]),
            "service_worker.js": "chrome.runtime.onInstalled.addListener(() => {});",
        }
    )

    record = _ingest(payload, tmp_path, monkeypatch)

    assert record["artifact_id"] == record["sha256"]
    assert record["manifest"] == {
        "manifest_version": 3,
        "name": "Fixture Extension",
        "version": "1.2.3",
    }
    assert record["permissions"] == ["storage"]
    assert record["host_permissions"] == []
    assert record["risk_reasons"] == []
    assert record["auto_approvable"] is True
    assert "contents_path" not in record
    assert str(tmp_path) not in json.dumps(record)

    quarantine_root = tmp_path / "extension-quarantine" / str(record["sha256"])
    assert (quarantine_root / "contents" / "manifest.json").exists()
    assert json.loads((quarantine_root / "metadata.json").read_text()) == record


def test_sanitizes_actor_and_source_host_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    payload = _zip_bytes({"manifest.json": _manifest()})

    record = ingest_extension_zip(
        payload,
        actor={"kind": "test", "home_path": str(tmp_path / "actor")},
        source={"kind": "upload", "file_path": str(tmp_path / "source.zip")},
    )

    serialized = json.dumps(record)
    assert str(tmp_path) not in serialized
    assert "home_path" not in record["actor"]
    assert "file_path" not in record["source"]


def test_ingest_is_idempotent_for_identical_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip_bytes({"manifest.json": _manifest(), "icons/icon.png": b"png"})

    first = _ingest(payload, tmp_path, monkeypatch)
    marker = tmp_path / "extension-quarantine" / str(first["sha256"]) / "contents" / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    second = _ingest(payload, tmp_path, monkeypatch)

    assert second == first
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("entry_name", "message"),
    [
        ("../manifest.json", "unsafe path"),
        ("/manifest.json", "unsafe path"),
        ("dir\\manifest.json", "backslash"),
    ],
)
def test_rejects_unsafe_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry_name: str, message: str
) -> None:
    payload = _zip_bytes({entry_name: _manifest()})

    with pytest.raises(ExtensionIngestError, match=message):
        _ingest(payload, tmp_path, monkeypatch)


def test_rejects_windows_drive_absolute_path_but_allows_relative_colon_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsafe_payload = _zip_bytes({"manifest.json": _manifest(), "C:/evil.js": "bad"})

    with pytest.raises(ExtensionIngestError, match="unsafe path"):
        _ingest(unsafe_payload, tmp_path, monkeypatch)

    safe_payload = _zip_bytes({"manifest.json": _manifest(), "assets/key:value.js": "ok"})
    record = _ingest(safe_payload, tmp_path, monkeypatch)

    assert record["auto_approvable"] is True


@pytest.mark.parametrize(
    "payload",
    [
        _zip_bytes({"manifest.json": _manifest(), "assets": "file", "assets/icon.js": "child"}),
        _zip_bytes_with_directory_collision(),
    ],
)
def test_rejects_file_directory_path_conflicts_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    with pytest.raises(ExtensionIngestError, match="path conflict"):
        _ingest(payload, tmp_path, monkeypatch)


def test_rejects_symlink_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", _manifest())
        info = zipfile.ZipInfo("linked")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")

    with pytest.raises(ExtensionIngestError, match="symlink"):
        _ingest(buffer.getvalue(), tmp_path, monkeypatch)


def test_rejects_nested_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _zip_bytes({"manifest.json": _manifest(), "nested.zip": b"PK\x03\x04"})

    with pytest.raises(ExtensionIngestError, match="nested archive"):
        _ingest(payload, tmp_path, monkeypatch)


def test_rejects_nested_zip_content_disguised_with_bin_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = _zip_bytes({"inner.txt": "nested"})
    payload = _zip_bytes({"manifest.json": _manifest(), "assets/payload.bin": nested})

    with pytest.raises(ExtensionIngestError, match="nested archive"):
        _ingest(payload, tmp_path, monkeypatch)


def test_rejects_nested_zip_content_with_leading_stub_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested_payload = b"arbitrary launcher stub\n" + _zip_bytes({"inner.txt": "nested"})
    assert zipfile.is_zipfile(io.BytesIO(nested_payload))
    payload = _zip_bytes({"manifest.json": _manifest(), "assets/payload.bin": nested_payload})

    with pytest.raises(ExtensionIngestError, match="nested archive"):
        _ingest(payload, tmp_path, monkeypatch)


def test_rejects_compressed_payload_over_10mib_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import extension_ingest

    over_limit_payload = _zip_bytes(
        {
            "manifest.json": _manifest(),
            "large.bin": b"0" * (extension_ingest.MAX_COMPRESSED_BYTES + 1),
        },
        compression=zipfile.ZIP_STORED,
    )

    assert len(over_limit_payload) > extension_ingest.MAX_COMPRESSED_BYTES
    with pytest.raises(ExtensionIngestError, match="compressed"):
        _ingest(over_limit_payload, tmp_path, monkeypatch)


def test_rejects_bomb_ratio_uncompressed_quota_and_file_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import extension_ingest

    ratio_payload = _zip_bytes({"manifest.json": _manifest(), "large.txt": b"0" * 2000})
    monkeypatch.setattr(extension_ingest, "MAX_COMPRESSION_RATIO", 2)
    with pytest.raises(ExtensionIngestError, match="compression ratio"):
        _ingest(ratio_payload, tmp_path, monkeypatch)

    monkeypatch.setattr(extension_ingest, "MAX_COMPRESSION_RATIO", 100)
    monkeypatch.setattr(extension_ingest, "MAX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(ExtensionIngestError, match="uncompressed"):
        _ingest(ratio_payload, tmp_path, monkeypatch)

    monkeypatch.setattr(extension_ingest, "MAX_UNCOMPRESSED_BYTES", 50_000)
    monkeypatch.setattr(extension_ingest, "MAX_FILES", 2)
    file_count_payload = _zip_bytes(
        {"manifest.json": _manifest(), "a.js": "", "b.js": ""}
    )
    with pytest.raises(ExtensionIngestError, match="file count"):
        _ingest(file_count_payload, tmp_path, monkeypatch)


def test_rejects_non_symlink_special_file_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", _manifest())
        info = zipfile.ZipInfo("fifo")
        info.create_system = 3
        info.external_attr = (stat.S_IFIFO | 0o644) << 16
        archive.writestr(info, "")

    with pytest.raises(ExtensionIngestError, match="special"):
        _ingest(buffer.getvalue(), tmp_path, monkeypatch)


def test_rejects_unsupported_zip_compression_method_as_ingest_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _with_compress_type(
        _zip_bytes({"manifest.json": _manifest(), "bad.bin": "unsupported"}),
        "bad.bin",
        99,
    )

    with pytest.raises(ExtensionIngestError, match="compression method"):
        _ingest(payload, tmp_path, monkeypatch)


def test_concurrent_identical_ingest_returns_persisted_metadata_on_replace_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from backend import extension_ingest

    payload = _zip_bytes({"manifest.json": _manifest()})
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    sha256 = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        uncompressed_size = archive.getinfo("manifest.json").file_size
    persisted: dict[str, Any] = {
        "artifact_id": sha256,
        "sha256": sha256,
        "actor": {"kind": "winner", "id": "agent-a"},
        "source": {"kind": "upload", "label": "winner"},
        "manifest": {
            "manifest_version": 3,
            "name": "Fixture Extension",
            "version": "1.2.3",
        },
        "permissions": [],
        "host_permissions": [],
        "risk_reasons": [],
        "auto_approvable": True,
        "file_count": 1,
        "compressed_size": len(payload),
        "uncompressed_size": uncompressed_size,
    }

    def lose_replace_race(_src: Any, dst: Any) -> None:
        race_root = Path(dst)
        race_root.mkdir(parents=True)
        (race_root / "contents").mkdir()
        (race_root / "metadata.json").write_text(
            json.dumps(persisted, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise OSError("replace lost race")

    monkeypatch.setattr(extension_ingest.os, "replace", lose_replace_race)

    returned = ingest_extension_zip(
        payload,
        actor={"kind": "loser", "id": "agent-b"},
        source={"kind": "upload", "label": "loser"},
    )

    assert returned == persisted


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"dir/manifest.json": _manifest()}, "root manifest"),
        ({"manifest.json": "[]"}, "manifest object"),
        ({"manifest.json": _manifest(manifest_version=2)}, "manifest_version"),
        ({"manifest.json": _manifest(name="")}, "name"),
        ({"manifest.json": _manifest(name="x" * 257)}, "name"),
        ({"manifest.json": _manifest(version="")}, "version"),
        ({"manifest.json": _manifest(version="1" * 129)}, "version"),
    ],
)
def test_rejects_invalid_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ExtensionIngestError, match=message):
        _ingest(_zip_bytes(entries), tmp_path, monkeypatch)


def test_flags_high_risk_permissions_and_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    risky_permissions = [
        "cookies",
        "webRequest",
        "webRequestBlocking",
        "nativeMessaging",
        "debugger",
        "management",
        "proxy",
    ]
    risky_hosts = [
        "<all_urls>",
        "https://*.example.com/*",
        "http://*/*",
        "https://*/*",
        "*://example.com/*",
    ]
    payload = _zip_bytes(
        {
            "manifest.json": _manifest(
                permissions=[*risky_permissions, "storage"],
                host_permissions=risky_hosts,
            )
        }
    )

    record = _ingest(payload, tmp_path, monkeypatch)

    assert record["permissions"] == [*risky_permissions, "storage"]
    assert record["host_permissions"] == risky_hosts
    for permission in risky_permissions:
        assert f"permission:{permission}" in record["risk_reasons"]
    assert "host:<all_urls>" in record["risk_reasons"]
    assert "host:broad:https://*.example.com/*" in record["risk_reasons"]
    assert "host:broad:http://*/*" in record["risk_reasons"]
    assert "host:broad:https://*/*" in record["risk_reasons"]
    assert "host:broad:*://example.com/*" in record["risk_reasons"]
    assert record["auto_approvable"] is False


def test_rejects_empty_non_zip_encrypted_and_duplicate_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ExtensionIngestError, match="empty"):
        _ingest(b"", tmp_path, monkeypatch)

    with pytest.raises(ExtensionIngestError, match="ZIP"):
        _ingest(b"not a zip", tmp_path, monkeypatch)

    duplicate = _zip_bytes({"manifest.json": _manifest(), "./manifest.json": _manifest()})
    with pytest.raises(ExtensionIngestError, match="duplicate"):
        _ingest(duplicate, tmp_path, monkeypatch)

    encrypted = _with_encrypted_flag(_zip_bytes({"manifest.json": _manifest()}))
    with pytest.raises(ExtensionIngestError, match="encrypted"):
        _ingest(encrypted, tmp_path, monkeypatch)
