from __future__ import annotations

import os
import importlib.util
from pathlib import Path

import pytest

BRIDGE = (
    Path(__file__).resolve().parents[1]
    / "extensions"
    / "cloak-profile-sync"
    / "host"
    / "local_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("cloak_profile_sync_local_bridge", BRIDGE)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
ALLOWED_EXTENSION_ORIGIN = bridge.ALLOWED_EXTENSION_ORIGIN
_allowed_cors_origin = bridge._allowed_cors_origin
_load_manager_token = bridge._load_manager_token
_validate_manager_base = bridge._validate_manager_base


def test_profile_launch_bridge_cors_is_extension_only():
    assert _allowed_cors_origin(ALLOWED_EXTENSION_ORIGIN) == ALLOWED_EXTENSION_ORIGIN
    assert _allowed_cors_origin("https://example.com") is None
    assert _allowed_cors_origin("") is None


def test_manager_base_is_operator_config_not_browser_request_data():
    assert _validate_manager_base("http://127.0.0.1:18117") == "http://127.0.0.1:18117"
    assert _validate_manager_base("https://vcvm.example.ts.net/") == "https://vcvm.example.ts.net"
    for unsafe in (
        "https://user:pass@vcvm.example.ts.net",
        "https://vcvm.example.ts.net/api",
        "https://vcvm.example.ts.net?token=raw",
        "file:///tmp/token",
    ):
        with pytest.raises(ValueError):
            _validate_manager_base(unsafe)


def test_manager_token_comes_only_from_private_local_file(tmp_path: Path):
    path = tmp_path / "manager-token"
    path.write_text("manager_test_only_token", encoding="utf-8")
    os.chmod(path, 0o600)
    assert _load_manager_token(path) == "manager_test_only_token"

    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="0600"):
        _load_manager_token(path)
