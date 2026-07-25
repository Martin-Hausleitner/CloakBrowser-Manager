"""Unit tests for VCVM Orca host-bridge preflight."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("vcvm_orca_preflight.py")
SYNTH_KEY = "cbm_agent_synth_test_key_01"


def _load():
    spec = importlib.util.spec_from_file_location("vcvm_orca_preflight", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_key(path: Path, content: str = SYNTH_KEY, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_check_paths_reports_missing_mounts_and_binary(tmp_path: Path):
    preflight = _load()
    fake_root = tmp_path / "missing"
    preflight.REQUIRED_MOUNTS = (
        fake_root / "orca",
        fake_root / ".local",
        fake_root / ".config" / "orca",
    )
    errors = preflight.check_paths(orca_bin=fake_root / "bin" / "orca-ide")
    assert any("missing required Orca host path" in item for item in errors)
    assert any("missing required Orca binary path" in item for item in errors)


def test_check_runtime_requires_reachable_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    preflight = _load()
    binary = tmp_path / "orca-ide"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "result": {"runtime": {"reachable": False, "state": "starting"}},
            }
        )
        stderr = ""

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: Result())
    errors = preflight.check_runtime(binary)
    assert "Orca runtime is not reachable" in errors
    assert "Orca runtime is not ready" in errors


def test_check_worktree_requires_registered_vk_repos_selector(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    preflight = _load()
    binary = tmp_path / "orca-ide"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    assert "Orca worktree must be path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use" in preflight.check_worktree(
        binary,
        "path:/home/coder/cloakbrowser-manager",
    )

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "result": {
                    "worktree": {
                        "path": "/home/coder/vk-repos/CloakBrowser-Manager-browser-use",
                    }
                },
            }
        )
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Result()

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    assert preflight.check_worktree(
        binary,
        "path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use",
    ) == []
    assert calls[0][1:4] == ["worktree", "show", "--worktree"]
    assert calls[0][4] == "path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use"


def test_check_agent_key_file_rejects_missing_and_bad_mode(tmp_path: Path):
    preflight = _load()
    missing = tmp_path / "missing-key"
    assert "agent key file is missing" in preflight.check_agent_key_file(missing)

    bad_mode = _write_key(tmp_path / "bad-mode", mode=0o644)
    errors = preflight.check_agent_key_file(bad_mode)
    assert any("0600" in item for item in errors)
    assert SYNTH_KEY not in " ".join(errors)


def test_check_agent_key_file_rejects_bad_syntax_without_echo(tmp_path: Path):
    preflight = _load()
    leaked = "cbm_agent_LEAKME_SHOULD_NOT_APPEAR_IN_ERRORS"
    bad = _write_key(tmp_path / "bad-syntax", content="not-a-valid-key")
    errors = preflight.check_agent_key_file(bad)
    assert any("syntactic" in item for item in errors)
    assert "not-a-valid-key" not in " ".join(errors)

    # Valid shape must pass and still never appear in returned notes.
    good = _write_key(tmp_path / "good", content=SYNTH_KEY)
    assert preflight.check_agent_key_file(good) == []
    assert leaked not in SYNTH_KEY  # sanity: keep synthetic key distinct


def test_check_agent_wrapper_requires_host_script(tmp_path: Path):
    preflight = _load()
    missing = tmp_path / "scripts" / "orca_agent_cli.sh"
    assert "missing required Orca agent wrapper on the host" in preflight.check_agent_wrapper(missing)
    bad_name = tmp_path / "evil.sh"
    bad_name.write_text("#!/bin/sh\n", encoding="utf-8")
    bad_name.chmod(0o755)
    assert "must be scripts/orca_agent_cli.sh" in preflight.check_agent_wrapper(bad_name)[0]
    good = tmp_path / "scripts" / "orca_agent_cli.sh"
    good.parent.mkdir(parents=True)
    good.write_text("#!/bin/sh\n", encoding="utf-8")
    good.chmod(0o755)
    assert preflight.check_agent_wrapper(good) == []


def test_main_fails_closed_when_agent_key_missing_even_with_skip_runtime(tmp_path: Path):
    preflight = _load()
    orca_dir = tmp_path / "orca"
    local_dir = tmp_path / ".local"
    config_dir = tmp_path / ".config" / "orca"
    orca_dir.mkdir()
    local_dir.mkdir()
    config_dir.mkdir(parents=True)
    binary = local_dir / "bin" / "orca-ide"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    wrapper = tmp_path / "scripts" / "orca_agent_cli.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)

    preflight.REQUIRED_MOUNTS = (orca_dir, local_dir, config_dir)
    missing_key = tmp_path / "no-such-key"
    assert (
        preflight.main(
            [
                "--orca-bin",
                str(binary),
                "--skip-runtime",
                "--agent-wrapper",
                str(wrapper),
                "--agent-key-file",
                str(missing_key),
            ]
        )
        == 78
    )


def test_main_passes_when_paths_and_key_ok_and_runtime_skipped(tmp_path: Path):
    preflight = _load()
    orca_dir = tmp_path / "orca"
    local_dir = tmp_path / ".local"
    config_dir = tmp_path / ".config" / "orca"
    orca_dir.mkdir()
    local_dir.mkdir()
    config_dir.mkdir(parents=True)
    binary = local_dir / "bin" / "orca-ide"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    key = _write_key(tmp_path / "orca-agent-key")
    wrapper = tmp_path / "scripts" / "orca_agent_cli.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)

    preflight.REQUIRED_MOUNTS = (orca_dir, local_dir, config_dir)
    assert (
        preflight.main(
            [
                "--orca-bin",
                str(binary),
                "--skip-runtime",
                "--agent-wrapper",
                str(wrapper),
                "--agent-key-file",
                str(key),
            ]
        )
        == 0
    )
