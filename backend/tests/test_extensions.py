"""Tests for backend/extensions.py manifest inspection and parsing."""

import json
from pathlib import Path
import pytest

from backend.extensions import (
    extract_load_extension_paths,
    inspect_profile_extensions,
    parse_extension_manifest,
)


def test_extract_load_extension_paths():
    args = [
        "--no-sandbox",
        "--load-extension=/tmp/ext1,/tmp/ext2",
        "--disable-gpu",
        "--load-extension=/tmp/ext3",
    ]
    paths = extract_load_extension_paths(args)
    assert paths == ["/tmp/ext1", "/tmp/ext2", "/tmp/ext3"]


def test_parse_extension_manifest_invalid_path(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist"
    info = parse_extension_manifest(str(non_existent))
    assert info.trust_state == "invalid_path"
    assert info.error is not None


def test_parse_extension_manifest_missing_manifest(tmp_path: Path):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    info = parse_extension_manifest(str(ext_dir))
    assert info.trust_state == "missing_manifest"
    assert info.error is not None


def test_parse_extension_manifest_invalid_json(tmp_path: Path):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    manifest = ext_dir / "manifest.json"
    manifest.write_text("{ invalid json ", encoding="utf-8")

    info = parse_extension_manifest(str(ext_dir))
    assert info.trust_state == "untrusted_manifest"
    assert "Invalid manifest JSON" in (info.error or "")


def test_parse_extension_manifest_valid(tmp_path: Path):
    ext_dir = tmp_path / "my_extension"
    ext_dir.mkdir()
    manifest_data = {
        "name": "Test Extension",
        "version": "1.2.3",
        "manifest_version": 3,
        "description": "A sample extension for testing.",
        "permissions": ["storage", "activeTab"],
    }
    manifest = ext_dir / "manifest.json"
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    info = parse_extension_manifest(str(ext_dir))
    assert info.trust_state == "valid"
    assert info.name == "Test Extension"
    assert info.version == "1.2.3"
    assert info.manifest_version == 3
    assert info.description == "A sample extension for testing."
    assert info.permissions == ["storage", "activeTab"]
    assert info.error is None


def test_inspect_profile_extensions(tmp_path: Path):
    ext_dir = tmp_path / "my_extension"
    ext_dir.mkdir()
    manifest_data = {
        "name": "Test Extension",
        "version": "1.0.0",
        "manifest_version": 2,
        "permissions": ["cookies"],
    }
    (ext_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    profile = {
        "id": "p-123",
        "launch_args": [f"--load-extension={ext_dir}"],
    }

    results = inspect_profile_extensions(profile)
    assert len(results) == 1
    assert results[0]["name"] == "Test Extension"
    assert results[0]["trust_state"] == "valid"
