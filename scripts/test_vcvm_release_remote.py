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


def test_bootstrap_candidate_verify_uses_candidate_manager_presence_and_adapter_preflights(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = paths["releases"] / "release-0000001" / "acpx-bootstrap" / "node-runtime" / "node_modules" / ".bin" / "acpx"
    acpx_executable.parent.mkdir(parents=True)
    acpx_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def fake_manager_json(port: int, path: str) -> dict[str, object]:
        manager_calls.append((port, path))
        if path == "/api/task-harnesses/acpx/presence":
            return {"worker_seen_recently": True, "state": "polling"}
        if path == "/api/task-harnesses/acpx/preflights":
            return {
                "agents": [
                    {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                    {"agent": "claude", "ready": True, "state": "ready", "reason_code": "ok"},
                    {"agent": "cursor", "ready": True, "state": "ready", "reason_code": "ok"},
                    {"agent": "grok-build", "ready": True, "state": "ready", "reason_code": "ok"},
                    {"agent": "opencode", "ready": True, "state": "ready", "reason_code": "ok"},
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote.shutil, "which", lambda name: "/usr/local/bin/acpx" if name == "acpx" else None)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_verify_candidate(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18116,
            "acpx_executable": str(acpx_executable),
        }
    )

    assert result["present"] is True
    assert result["adapters_ready"] is True
    assert manager_calls == [
        (18116, "/api/task-harnesses/acpx/presence"),
        (18116, "/api/task-harnesses/acpx/preflights"),
    ]


def test_bootstrap_candidate_verify_rejects_missing_adapter_preflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = paths["releases"] / "release-0000001" / "acpx-bootstrap" / "node-runtime" / "node_modules" / ".bin" / "acpx"
    acpx_executable.parent.mkdir(parents=True)
    acpx_executable.write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    def fake_manager_json(port: int, path: str) -> dict[str, object]:
        assert port == 18116
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        return {"agents": [{"agent": "codex", "ready": False, "state": "failed", "reason_code": "auth_required"}]}

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote.shutil, "which", lambda name: "/usr/local/bin/acpx" if name == "acpx" else None)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_verify_candidate(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18116,
            "acpx_executable": str(acpx_executable),
        }
    )

    assert result["present"] is True
    assert result["adapters_ready"] is False


def test_bootstrap_probe_blocks_partial_acpx_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    unit = paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service"
    unit.write_text("[Service]\nExecStart=old-acpx\n", encoding="utf-8")

    result = remote.op_bootstrap_acpx_probe({"release_id": "release-0000001"})

    assert result["state"] == "blocking"
    assert result["reason_code"] == "misconfigured"
    assert "unit" not in result["missing"]


def test_bootstrap_candidate_verify_rejects_non_release_acpx_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError, match="ACPX executable"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": "/usr/local/bin/acpx",
            }
        )


def test_capture_state_records_prior_acpx_absence_without_strict_preflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_strict_acpx(args: dict[str, object]) -> dict[str, object]:
        del args
        raise remote.HelperError("ACPX unit is missing")

    monkeypatch.setattr(remote, "op_preflight_manager", lambda _args: {"revision": "1" * 40, "image_digest": "d" * 64, "image_id": "sha256:" + ("d" * 64)})
    monkeypatch.setattr(
        remote,
        "op_preflight_browser_use",
        lambda _args: {
            "unit_sha256": "1" * 64,
            "unit_path": str(remote.expected_unit_path("cloakbrowser-browser-use.service")),
            "active_state": "active",
            "dropin_path": str(remote.release_dropin("cloakbrowser-browser-use.service")),
            "dropin_exists": False,
            "dropin_content": "",
            "dropin_sha256": "",
        },
    )
    monkeypatch.setattr(remote, "op_preflight_acpx", fail_strict_acpx)
    monkeypatch.setattr(
        remote,
        "_probe_acpx_state",
        lambda: {"state": "absent", "missing": ["unit"], "reason_code": "missing_runtime"},
        raising=False,
    )
    monkeypatch.setattr(remote, "json_file", lambda _path: {})
    monkeypatch.setattr(remote, "CURRENT_LINK", tmp_path / "missing-current")

    capture = remote.op_capture_state({"commit": "0" * 40})

    assert capture["acpx_was_absent"] is True
    assert capture["acpx_active_state"] == "absent"
    assert capture["acpx_unit_sha256"] == "0" * 64


def test_bootstrap_promote_rehomes_runtime_before_cleanup_keeps_permanent_unit_valid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    root = remote.acpx_bootstrap_dir("release-0000001")
    for relative in (
        "venv/bin/python",
        "node-runtime/node_modules/.bin/acpx",
        "capability/capability.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    key = root / "candidate.worker.key"
    key.write_text("cbm_worker_" + ("1" * 64) + "\n", encoding="utf-8")
    key.chmod(0o600)
    unit = root / "acpx-candidate-release-0000001.service"
    unit.write_text(f"ExecStart={root}/venv/bin/python --manager-url http://127.0.0.1:18116 --token-file {key}\n", encoding="utf-8")
    unit.chmod(0o600)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    version_checks: list[str] = []

    def fake_run_with_version_capture(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[-1:] == ["--version"]:
            version_checks.append(argv[0])
        return fake_run(argv, **kwargs)

    monkeypatch.setattr(remote, "run", fake_run_with_version_capture)
    monkeypatch.setattr(remote.shutil, "which", lambda name: str(release_source / "acpx") if name == "acpx" else None)

    result = remote.op_bootstrap_acpx_promote(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18115,
            "release_source": str(release_source),
            "acpx_executable": str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / ".bin" / "acpx"),
        }
    )
    remote.op_bootstrap_acpx_cleanup({"release_id": "release-0000001"})

    permanent_unit = paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service"
    unit_text = permanent_unit.read_text(encoding="utf-8")
    assert result["active"] is True
    assert "acpx-bootstrap" not in unit_text
    assert "127.0.0.1:18115" in unit_text
    assert (paths["releases"] / "release-0000001" / "acpx-runtime").exists()
    assert version_checks == [str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / ".bin" / "acpx")]
    assert not root.exists()


def test_restore_runtime_removes_promoted_acpx_artifacts_when_old_state_was_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    capture.update(
        {
            "acpx_was_absent": True,
            "acpx_active_state": "absent",
            "acpx_unit_sha256": "0" * 64,
            "acpx_dropin_exists": False,
            "acpx_bootstrap_release_id": "release-0000001",
        }
    )
    release_acpx = paths["releases"] / "release-0000001" / "acpx-runtime"
    release_acpx.mkdir(parents=True)
    acpx_unit = paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service"
    acpx_unit.write_text("[Service]\nExecStart=new-acpx\n", encoding="utf-8")
    unrelated = paths["home"] / ".config" / "systemd" / "user" / "unrelated.service"
    unrelated.write_text("keep\n", encoding="utf-8")

    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))
    remote.op_restore_runtime({"capture": capture, "backup": write_backup(paths)})

    assert not acpx_unit.exists()
    assert not release_acpx.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep\n"


def test_verify_acpx_expected_absent_uses_absence_probe_before_strict_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_strict_acpx(args: dict[str, object]) -> dict[str, object]:
        del args
        raise AssertionError("strict ACPX preflight must not run for expected absence")

    monkeypatch.setattr(remote, "op_preflight_acpx", fail_strict_acpx)
    monkeypatch.setattr(
        remote,
        "_probe_acpx_state",
        lambda: {"state": "absent", "missing": list(remote.ACPX_ABSENCE_COMPONENTS), "reason_code": "missing_runtime"},
    )

    result = remote.op_verify_acpx({"release_source": "/home/coder/cloakbrowser-manager/releases/release-0000001/source", "expected_absent": True})

    assert result["absent"] is True
    assert sorted(result["missing"]) == sorted(remote.ACPX_ABSENCE_COMPONENTS)


def test_verify_acpx_expected_absent_rejects_partial_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "op_preflight_acpx", lambda _args: (_ for _ in ()).throw(AssertionError("strict preflight must not run")))
    monkeypatch.setattr(remote, "_probe_acpx_state", lambda: {"state": "blocking", "missing": ["unit"], "reason_code": "misconfigured"})

    with pytest.raises(remote.HelperError, match="absence"):
        remote.op_verify_acpx({"release_source": "/home/coder/cloakbrowser-manager/releases/release-0000001/source", "expected_absent": True})


def test_bootstrap_cleanup_does_not_report_success_when_rmtree_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patch_remote_paths(monkeypatch, tmp_path)
    root = remote.acpx_bootstrap_dir("release-0000001")
    root.mkdir(parents=True)
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))
    monkeypatch.setattr(remote.shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("refuse removal")))

    with pytest.raises(remote.HelperError, match="cleanup failed"):
        remote.op_bootstrap_acpx_cleanup({"release_id": "release-0000001"})

    assert root.exists()
