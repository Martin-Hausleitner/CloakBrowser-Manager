#!/usr/bin/env python3
"""Tests for the checked-in VCVM release remote helper."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REMOTE_SCRIPT = ROOT / "scripts" / "vcvm_release_remote.py"
TRANSACTION_SCRIPT = ROOT / "scripts" / "vcvm_release_transaction.py"

REMOTE_SPEC = importlib.util.spec_from_file_location("vcvm_release_remote", REMOTE_SCRIPT)
assert REMOTE_SPEC and REMOTE_SPEC.loader
remote = importlib.util.module_from_spec(REMOTE_SPEC)
sys.modules[REMOTE_SPEC.name] = remote
REMOTE_SPEC.loader.exec_module(remote)

TX_SPEC = importlib.util.spec_from_file_location("vcvm_release_transaction", TRANSACTION_SCRIPT)
assert TX_SPEC and TX_SPEC.loader
tx = importlib.util.module_from_spec(TX_SPEC)
sys.modules[TX_SPEC.name] = tx
TX_SPEC.loader.exec_module(tx)


def patch_remote_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home" / "coder"
    root = home / "cloakbrowser-manager"
    releases = root / "releases"
    state = root / ".vcvm-release-state.json"
    current = root / "current"
    for path in (root / "backups", releases, home / ".config" / "systemd" / "user"):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(remote, "REMOTE_PATH", root)
    monkeypatch.setattr(remote, "RELEASES_PATH", releases)
    monkeypatch.setattr(remote, "STATE_FILE", state)
    monkeypatch.setattr(remote, "CURRENT_LINK", current)
    monkeypatch.setattr(remote.Path, "home", lambda: home)
    return {"home": home, "root": root, "releases": releases, "state": state, "current": current}


def write_backup(paths: dict[str, Path], receipt_id: str = "backup-final-old") -> dict[str, object]:
    backup = paths["root"] / "backups" / f"{receipt_id}.tar"
    backup.write_bytes(b"backup")
    return {
        "receipt_id": receipt_id,
        "path": str(backup),
        "sha256": remote.file_sha256(backup),
        "volume": remote.MANAGER_VOLUME,
        "source_revision": "0" * 40,
        "compatible": True,
    }


def valid_capture(paths: dict[str, Path]) -> dict[str, object]:
    user_units = paths["home"] / ".config" / "systemd" / "user"
    browser_unit = user_units / "cloakbrowser-browser-use.service"
    acpx_unit = user_units / "cloakbrowser-acpx.service"
    browser_unit.write_text("[Service]\nExecStart=browser\n", encoding="utf-8")
    acpx_unit.write_text("[Service]\nExecStart=acpx\n", encoding="utf-8")
    previous = paths["releases"] / "release-old-0001" / "source"
    previous.mkdir(parents=True)
    return {
        "source_revision": "0" * 40,
        "previous_revision": "1" * 40,
        "old_image_digest": "d" * 64,
        "old_image_id": "sha256:" + ("d" * 64),
        "container_config_receipt": "3" * 64,
        "current_pointer": str(previous),
        "state": {"current_release": "release-old-0001"},
        "browser_use_unit_sha256": "1" * 64,
        "browser_use_unit_path": str(browser_unit),
        "browser_use_active_state": "active",
        "browser_use_dropin_path": str(remote.release_dropin("cloakbrowser-browser-use.service")),
        "browser_use_dropin_exists": False,
        "browser_use_dropin_content": "",
        "acpx_unit_sha256": "2" * 64,
        "acpx_unit_path": str(acpx_unit),
        "acpx_active_state": "inactive",
        "acpx_dropin_path": str(remote.release_dropin("cloakbrowser-acpx.service")),
        "acpx_dropin_exists": False,
        "acpx_dropin_content": "",
        "live_volume": remote.MANAGER_VOLUME,
    }


def test_remote_helper_dispatch_rejects_unknown_operation() -> None:
    result = subprocess.run(
        [sys.executable, str(REMOTE_SCRIPT)],
        input=json.dumps({"operation": "unknown.operation", "args": {}}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 75
    assert "unknown remote operation" in result.stderr


def test_remote_helper_capabilities_are_checked_in_and_versioned() -> None:
    payload = remote.handle_request({"operation": "helper.capabilities", "args": {}})
    assert payload["helper_version"] == remote.HELPER_VERSION
    assert "preflight.disk" in payload["operations"]
    assert "release.extract" in payload["operations"]
    assert "state.commit" in payload["operations"]
    assert "docker.system.prune" not in payload["operations"]


def test_remote_helper_uses_argv_arrays_for_local_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...] | list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert not isinstance(argv, str)
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ready\n", stderr="")

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    payload = remote.handle_request({"operation": "verify.orca", "args": {}})
    assert payload == {"status": "ready"}
    assert calls == [("orca", "status")]


def test_ssh_executor_streams_checked_in_helper_over_json_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}\n', stderr="")

    monkeypatch.setattr(tx.subprocess, "run", fake_run)
    executor = tx.SSHRemoteExecutor("vcvm", helper_path=REMOTE_SCRIPT)
    assert executor.run_json(tx.remote_request("helper.capabilities", {}), phase="helper.capabilities") == {"ok": True}
    argv = captured["argv"]
    assert argv[:4] == ("ssh", "-o", "BatchMode=yes", "--")
    assert argv[4] == "vcvm"
    request = json.loads(str(captured["input"]))
    assert "HELPER_VERSION" in request["helper_source"]
    assert request["request"] == {"operation": "helper.capabilities", "args": {}}


@pytest.mark.parametrize("host", ["vcvm;touch /tmp/pwn", "badvcvm", "user@vcvm;bad", "a/b@vcvm"])
def test_ssh_executor_rejects_host_injection(host: str) -> None:
    with pytest.raises(tx.TransactionError, match="unexpected target host"):
        tx.SSHRemoteExecutor(host, helper_path=REMOTE_SCRIPT)


def test_protocol_table_covers_every_remote_operation_with_exact_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, dict[str, object]] = {}

    for operation in remote.OPERATION_SCHEMAS:
        monkeypatch.setitem(
            remote.OPERATIONS,
            operation,
            lambda args, operation=operation: seen.setdefault(operation, dict(args)) or {"ok": True},
        )

    for phase, example_argv in tx.PHASE_REQUEST_EXAMPLES.items():
        request = tx.remote_request(phase, json.loads(example_argv[-1]))
        operation = request["operation"]
        assert operation in remote.OPERATION_SCHEMAS
        remote.handle_request(request)
        assert seen[operation] == request["args"]

    assert set(tx.PHASE_REQUEST_EXAMPLES) == set(remote.OPERATION_SCHEMAS)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "build.image", "args": {"release_id": "release-0000001"}},
        {
            "operation": "backup.live",
            "args": {
                "commit": "0" * 40,
                "unexpected": True,
            },
        },
        {
            "operation": "state.commit",
            "args": {
                "release_id": "release-0000001",
                "current_release": "release-0000001",
            },
        },
    ],
)
def test_remote_helper_rejects_missing_or_extra_args(payload: dict[str, object]) -> None:
    with pytest.raises(remote.HelperError):
        remote.handle_request(payload)


def test_state_commit_request_contains_rollback_usable_receipts() -> None:
    request = tx.remote_request("state.commit", json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1]))
    args = request["args"]
    assert args["current_release"] == "release-0000001"
    assert args["previous_release"] == "release-previous-1"
    assert args["image"]["image_ref"] == "sha256:" + ("e" * 64)
    assert args["final_backup"]["receipt_id"] == "backup-final-old"
    assert args["capture"]["browser_use_unit_sha256"] == "1" * 64
    assert args["capture"]["acpx_active_state"] == "active"


def test_restore_runtime_restarts_old_image_after_volume_restore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)
    remote.op_restore_runtime({"capture": valid_capture(paths), "backup": write_backup(paths)})

    docker_calls = [call for call in calls if call and call[0] == "docker"]
    assert ("docker", "image", "inspect", "sha256:" + ("d" * 64)) in docker_calls
    assert any(call[:4] == ("docker", "run", "-d", "--name") and call[-1] == "sha256:" + ("d" * 64) for call in docker_calls)
    restore_index = next(index for index, call in enumerate(docker_calls) if call[:3] == ("docker", "run", "--rm"))
    start_index = next(index for index, call in enumerate(docker_calls) if call[:4] == ("docker", "run", "-d", "--name"))
    assert restore_index < start_index


def test_restore_runtime_rejects_tampered_unit_path_before_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    capture = valid_capture(paths)
    capture["browser_use_unit_path"] = str(tmp_path / "evil.service")
    monkeypatch.setattr(remote, "run", fake_run)
    with pytest.raises(remote.HelperError, match="unit path"):
        remote.op_restore_runtime({"capture": capture, "backup": write_backup(paths)})
    assert calls == []


@pytest.mark.parametrize("missing_key", ["browser_use_dropin_path", "acpx_dropin_path"])
def test_restore_runtime_requires_captured_dropin_paths_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_key: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    capture = valid_capture(paths)
    del capture[missing_key]
    monkeypatch.setattr(remote, "run", fake_run)
    with pytest.raises(remote.HelperError, match="drop-in path"):
        remote.op_restore_runtime({"capture": capture, "backup": write_backup(paths)})
    assert calls == []


def test_restore_runtime_rejects_tampered_current_pointer_before_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    capture = valid_capture(paths)
    capture["current_pointer"] = str(tmp_path / "outside" / "source")
    monkeypatch.setattr(remote, "run", fake_run)
    with pytest.raises(remote.HelperError, match="current pointer"):
        remote.op_restore_runtime({"capture": capture, "backup": write_backup(paths)})
    assert calls == []


def test_state_commit_rejects_secret_and_writes_mode_0600(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])
    payload["capture"]["browser_use_dropin_content"] = "TOKEN=cbm_agent_" + ("a1" * 24)
    with pytest.raises(remote.HelperError, match="secret"):
        remote.op_state_commit(payload)
    assert not paths["state"].exists()

    payload["capture"]["browser_use_dropin_content"] = ""
    remote.op_state_commit(payload)
    assert stat.S_IMODE(paths["state"].stat().st_mode) == 0o600


def test_state_commit_rejects_symlink_temp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    target = tmp_path / "outside-state-target"
    target.write_text("do-not-touch", encoding="utf-8")
    paths["state"].with_suffix(".tmp").symlink_to(target)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])

    with pytest.raises(remote.HelperError, match="temporary"):
        remote.op_state_commit(payload)

    assert target.read_text(encoding="utf-8") == "do-not-touch"
    assert not paths["state"].exists()


def test_restore_dropin_rejects_symlink_temp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    dropin = remote.release_dropin("cloakbrowser-browser-use.service")
    dropin.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside-dropin-target"
    target.write_text("do-not-touch", encoding="utf-8")
    dropin.with_suffix(".tmp").symlink_to(target)
    capture = valid_capture(paths)
    capture["browser_use_dropin_exists"] = True
    capture["browser_use_dropin_content"] = "[Service]\nWorkingDirectory=/safe\n"

    with pytest.raises(remote.HelperError, match="temporary"):
        remote._restore_release_dropin("cloakbrowser-browser-use.service", capture, "browser_use")

    assert target.read_text(encoding="utf-8") == "do-not-touch"
