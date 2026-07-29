"""Secure ZIP quarantine for uploaded Chromium extension artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from backend import database

MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
MAX_COMPRESSION_RATIO = 100
MAX_NAME_LENGTH = 256
MAX_VERSION_LENGTH = 128

_QUARANTINE_DIRNAME = "extension-quarantine"
_NESTED_ARCHIVE_SUFFIXES = {".zip", ".crx", ".xpi"}
_SUPPORTED_COMPRESSION_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_RISK_PERMISSIONS = {
    "cookies",
    "webRequest",
    "webRequestBlocking",
    "nativeMessaging",
    "debugger",
    "management",
    "proxy",
}


class ExtensionIngestError(ValueError):
    """Raised when an extension archive fails quarantine validation."""


def ingest_extension_zip(
    payload: bytes,
    *,
    actor: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    """Validate, extract, and record an extension ZIP in quarantine.

    The returned record is JSON-serializable and intentionally omits host paths.
    """
    if not payload:
        raise ExtensionIngestError("empty ZIP payload")
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise ExtensionIngestError("compressed ZIP payload exceeds limit")

    sha256 = hashlib.sha256(payload).hexdigest()
    quarantine_root = database.DATA_DIR / _QUARANTINE_DIRNAME / sha256
    metadata_path = quarantine_root / "metadata.json"
    contents_path = quarantine_root / "contents"
    if metadata_path.exists() and contents_path.is_dir():
        return _read_metadata(metadata_path)

    try:
        with tempfile.TemporaryFile() as archive_file:
            archive_file.write(payload)
            archive_file.seek(0)
            with zipfile.ZipFile(archive_file) as archive:
                members = _validated_members(archive)
                manifest = _read_manifest(archive, members)
                permissions = _string_list(manifest.get("permissions"))
                host_permissions = _string_list(manifest.get("host_permissions"))
                risk_reasons = _risk_reasons(permissions, host_permissions)
                record = {
                    "artifact_id": sha256,
                    "sha256": sha256,
                    "actor": _sanitized_metadata(actor, "actor"),
                    "source": _sanitized_metadata(source, "source"),
                    "manifest": {
                        "manifest_version": 3,
                        "name": manifest["name"],
                        "version": manifest["version"],
                    },
                    "permissions": permissions,
                    "host_permissions": host_permissions,
                    "risk_reasons": risk_reasons,
                    "auto_approvable": not risk_reasons,
                    "file_count": len([member for member in members.values() if not member.is_dir()]),
                    "compressed_size": len(payload),
                    "uncompressed_size": sum(member.file_size for member in members.values()),
                }
                persisted_record = _extract_atomically(archive, members, quarantine_root, record)
                return persisted_record or record
    except zipfile.BadZipFile as exc:
        raise ExtensionIngestError("payload is not a valid ZIP archive") from exc


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionIngestError("existing quarantine metadata is invalid") from exc
    if not isinstance(payload, dict):
        raise ExtensionIngestError("existing quarantine metadata is invalid")
    return payload


def _validated_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos:
        raise ExtensionIngestError("empty ZIP archive")

    members: dict[str, zipfile.ZipInfo] = {}
    file_count = 0
    compressed_size = 0
    uncompressed_size = 0
    for info in infos:
        normalized = _normalized_name(info.filename)
        if normalized in members:
            if members[normalized].is_dir() != info.is_dir():
                raise ExtensionIngestError(f"path conflict in ZIP member: {normalized}")
            raise ExtensionIngestError(f"duplicate normalized name: {normalized}")
        _reject_path_conflict(members, normalized, info)
        _reject_unsafe_member(info, normalized)
        if not info.is_dir():
            file_count += 1
            compressed_size += info.compress_size
            uncompressed_size += info.file_size
            if file_count > MAX_FILES:
                raise ExtensionIngestError("file count exceeds limit")
            if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
                raise ExtensionIngestError("uncompressed ZIP payload exceeds limit")
            if compressed_size and uncompressed_size / compressed_size > MAX_COMPRESSION_RATIO:
                raise ExtensionIngestError("compression ratio exceeds limit")
            _reject_nested_archive_content(archive, info)
        members[normalized] = info
    if "manifest.json" not in members:
        raise ExtensionIngestError("missing root manifest.json")
    return members


def _reject_path_conflict(
    members: dict[str, zipfile.ZipInfo], normalized: str, info: zipfile.ZipInfo
) -> None:
    for existing_name, existing_info in members.items():
        if normalized.startswith(f"{existing_name}/") and not existing_info.is_dir():
            raise ExtensionIngestError(f"path conflict in ZIP member: {normalized}")
        if existing_name.startswith(f"{normalized}/") and not info.is_dir():
            raise ExtensionIngestError(f"path conflict in ZIP member: {normalized}")


def _normalized_name(name: str) -> str:
    if "\\" in name:
        raise ExtensionIngestError("backslash paths are not allowed")
    if len(name) >= 3 and name[0].isalpha() and name[1] == ":" and name[2] == "/":
        raise ExtensionIngestError(f"unsafe path in ZIP member: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ExtensionIngestError(f"unsafe path in ZIP member: {name}")
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        raise ExtensionIngestError("empty ZIP member path")
    return normalized


def _reject_unsafe_member(info: zipfile.ZipInfo, normalized: str) -> None:
    if info.flag_bits & 0x1:
        raise ExtensionIngestError("encrypted ZIP entries are not supported")
    if info.compress_type not in _SUPPORTED_COMPRESSION_METHODS:
        raise ExtensionIngestError("unsupported ZIP compression method")
    mode = info.external_attr >> 16
    if mode:
        kind = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode):
            raise ExtensionIngestError("symlink ZIP entries are not supported")
        if kind and kind not in {stat.S_IFREG, stat.S_IFDIR}:
            raise ExtensionIngestError("special ZIP entries are not supported")
    if not info.is_dir() and PurePosixPath(normalized).suffix.lower() in _NESTED_ARCHIVE_SUFFIXES:
        raise ExtensionIngestError("nested archive entries are not supported")


def _reject_nested_archive_content(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    try:
        with archive.open(info) as handle:
            data = handle.read()
    except OSError as exc:
        raise ExtensionIngestError("ZIP entry could not be inspected") from exc
    magic = data[:4]
    if magic in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"} or magic == b"Cr24":
        raise ExtensionIngestError("nested archive entries are not supported")
    if zipfile.is_zipfile(io.BytesIO(data)):
        raise ExtensionIngestError("nested archive entries are not supported")


def _read_manifest(
    archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]
) -> dict[str, Any]:
    info = members["manifest.json"]
    if info.is_dir():
        raise ExtensionIngestError("root manifest.json must be a file")
    try:
        with archive.open(info) as handle:
            manifest = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionIngestError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ExtensionIngestError("manifest object is required")
    if manifest.get("manifest_version") != 3:
        raise ExtensionIngestError("unsupported manifest_version")
    name = manifest.get("name")
    version = manifest.get("version")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise ExtensionIngestError("manifest name is required and bounded")
    if (
        not isinstance(version, str)
        or not version.strip()
        or len(version) > MAX_VERSION_LENGTH
    ):
        raise ExtensionIngestError("manifest version is required and bounded")
    manifest["name"] = name.strip()
    manifest["version"] = version.strip()
    return manifest


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _risk_reasons(permissions: list[str], host_permissions: list[str]) -> list[str]:
    reasons: list[str] = []
    for permission in permissions:
        if permission in _RISK_PERMISSIONS:
            reasons.append(f"permission:{permission}")
    for host in host_permissions:
        if host == "<all_urls>":
            reasons.append("host:<all_urls>")
        elif _is_broad_host(host):
            reasons.append(f"host:broad:{host}")
    return list(dict.fromkeys(reasons))


def _is_broad_host(host: str) -> bool:
    lowered = host.lower()
    return (
        lowered.startswith("*://")
        or "://*." in lowered
        or lowered.startswith("http://*/")
        or lowered.startswith("https://*/")
    )


def _sanitized_metadata(value: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExtensionIngestError(f"{label} metadata must be an object")
    try:
        serializable = json.loads(json.dumps(value))
    except TypeError as exc:
        raise ExtensionIngestError(f"{label} metadata must be JSON-serializable") from exc
    if not isinstance(serializable, dict):
        raise ExtensionIngestError(f"{label} metadata must be an object")
    return _drop_path_metadata(serializable)


def _drop_path_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_path_key(key_text):
                continue
            cleaned_item = _drop_path_metadata(item)
            if cleaned_item is not _PATH_SENTINEL:
                cleaned[key_text] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [
            cleaned_item
            for item in value
            if (cleaned_item := _drop_path_metadata(item)) is not _PATH_SENTINEL
        ]
    if isinstance(value, str) and _looks_like_host_path(value):
        return _PATH_SENTINEL
    return value


def _is_path_key(key: str) -> bool:
    lowered = key.lower()
    return lowered == "path" or lowered.endswith("_path") or lowered.endswith("path")


def _looks_like_host_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("~/")
        or (len(value) >= 3 and value[1] == ":" and value[2] in {"\\", "/"})
    )


_PATH_SENTINEL = object()


def _extract_atomically(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    quarantine_root: Path,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    parent = quarantine_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{quarantine_root.name}.", dir=parent))
    try:
        temp_contents = temp_root / "contents"
        temp_contents.mkdir()
        for normalized, info in members.items():
            destination = temp_contents / normalized
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        (temp_root / "metadata.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.replace(temp_root, quarantine_root)
        except OSError:
            metadata_path = quarantine_root / "metadata.json"
            if metadata_path.exists() and (quarantine_root / "contents").is_dir():
                persisted_record = _read_metadata(metadata_path)
                shutil.rmtree(temp_root)
                return persisted_record
            else:
                raise
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return None
