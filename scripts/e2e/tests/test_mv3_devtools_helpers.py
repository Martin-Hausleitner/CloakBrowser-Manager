"""Unit tests for MV3 DevTools E2E helpers (no live Chromium required)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.e2e.mv3_devtools.artifacts import ArtifactStore
from scripts.e2e.mv3_devtools.chrome_launcher import build_chromium_args, free_loopback_port
from scripts.e2e.mv3_devtools.redaction import artifact_is_safe, redact_value
from scripts.e2e.mv3_devtools.runner import exercise_mcp_tool_surface


def test_build_chromium_args_loads_unpacked_extension(tmp_path: Path):
    extension = tmp_path / "ext"
    extension.mkdir()
    (extension / "manifest.json").write_text("{}", encoding="utf-8")
    user_data = tmp_path / "profile"
    user_data.mkdir()
    port = free_loopback_port()

    args = build_chromium_args(
        user_data_dir=user_data,
        extension_dir=extension,
        debugging_port=port,
    )

    joined = " ".join(args)
    assert f"--user-data-dir={user_data.resolve()}" in args
    assert f"--remote-debugging-port={port}" in args
    assert "--remote-allow-origins=*" in args
    assert f"--load-extension={extension.resolve()}" in args
    assert f"--disable-extensions-except={extension.resolve()}" in args
    assert "DisableLoadExtensionCommandLineSwitch" in joined
    assert args[-1] == "about:blank"


def test_build_chromium_args_rejects_missing_manifest(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="manifest"):
        build_chromium_args(
            user_data_dir=tmp_path / "profile",
            extension_dir=tmp_path / "missing",
            debugging_port=19222,
        )


def test_redaction_strips_secrets_and_home_paths():
    home = str(Path.home())
    payload = {
        "token": "super-secret-token-value",
        "nested": {"password": "raw-password", "path": f"{home}/.config/cloakbrowser/token"},
        "message": "Authorization: Bearer abcdefghijklmnop",
        "url": "http://user:pass@127.0.0.1:18766/v1",
    }

    redacted = redact_value(payload)

    assert redacted["token"] == "[redacted]"
    assert redacted["nested"]["password"] == "[redacted]"
    assert home not in json.dumps(redacted)
    assert "raw-password" not in json.dumps(redacted)
    assert "abcdefghijklmnop" not in json.dumps(redacted)
    assert "user:pass" not in json.dumps(redacted)
    assert artifact_is_safe(redacted)


def test_artifact_store_writes_redacted_steps_and_blockers(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")
    step = store.record_step(
        "demo",
        {"ok": True, "path": str(Path.home() / "secret-file"), "token": "should-not-leak"},
        ok=True,
    )
    blocker = store.record_blocker(
        "cli_start_unavailable",
        "No active tab available for recording",
        details={"error": "No active tab available for recording"},
    )
    summary = store.write_summary({"ok": False, "blockers": store.blockers})

    step_data = json.loads(step.read_text(encoding="utf-8"))
    assert step_data["payload"]["token"] == "[redacted]"
    assert str(Path.home()) not in step.read_text(encoding="utf-8")
    assert blocker.exists()
    assert summary.exists()
    assert "should-not-leak" not in summary.read_text(encoding="utf-8")


def test_mcp_tool_surface_reports_construction_or_inventory():
    report = exercise_mcp_tool_surface()
    assert "ok" in report
    # Either tools are introspectable or construction succeeded with a note.
    if report.get("error"):
        assert report["ok"] is False
        assert "mcp" in report["error"].lower() or "missing" in report["error"].lower()
    else:
        assert report["ok"] is True


def test_bridge_runner_health_and_private_token(tmp_path: Path):
    from scripts.e2e.mv3_devtools.bridge_runner import start_local_extension_bridge
    from urllib.request import urlopen

    token_path = tmp_path / "control-token"
    bridge = start_local_extension_bridge(token_path=token_path)
    try:
        with urlopen(f"{bridge.base_url}/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["ok"] is True
        assert token_path.exists()
        assert token_path.stat().st_mode & 0o077 == 0
        assert bridge.control_token not in repr(bridge)
        assert bridge.control_token not in bridge.base_url
    finally:
        bridge.close()
