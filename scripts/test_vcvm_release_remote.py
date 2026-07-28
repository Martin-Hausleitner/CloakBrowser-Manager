#!/usr/bin/env python3
"""Tests for the checked-in VCVM release remote helper."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import time as pytime
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

BROWSER_USE_UNIT = "cloakbrowser-browser-use-worker.service"
BROWSER_USE_TOKEN_PATH = "/home/coder/.config/cloakbrowser/browser-use-worker-key"
IMAGE_ID = "sha256:" + ("d" * 64)
IMAGE_DIGEST = "d" * 64
REVISION = "0" * 40
EXPECTED_ACPX_SYSTEMD_PATH = "/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin"


def patch_remote_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home" / "coder"
    root = home / "cloakbrowser-manager"
    releases = root / "releases"
    state = root / ".vcvm-release-state.json"
    current = root / "current"
    for path in (root / "backups", releases, root / "receipts", home / ".config" / "systemd" / "user"):
        path.mkdir(parents=True, exist_ok=True)
    for path in (root / "backups", releases, root / "receipts"):
        path.chmod(0o700)
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
    browser_unit = user_units / BROWSER_USE_UNIT
    acpx_unit = user_units / "cloakbrowser-acpx.service"
    browser_unit.write_text("[Service]\nExecStart=browser\n", encoding="utf-8")
    acpx_unit.write_text("[Service]\nExecStart=acpx\n", encoding="utf-8")
    previous = paths["releases"] / "release-old-0001" / "source"
    previous.mkdir(parents=True)
    return {
        "source_revision": "0" * 40,
        "previous_revision": "1" * 40,
        "previous_revision_available": True,
        "old_image_digest": "d" * 64,
        "old_image_id": "sha256:" + ("d" * 64),
        "container_config_receipt": "3" * 64,
        "current_pointer": str(previous),
        "state": {"current_release": "release-old-0001"},
        "browser_use_unit_sha256": "1" * 64,
        "browser_use_unit_path": str(browser_unit),
        "browser_use_active_state": "active",
        "browser_use_dropin_path": str(remote.release_dropin(BROWSER_USE_UNIT)),
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


def test_remote_main_bounds_and_redacts_oversized_refusal() -> None:
    secret_token = "cbm_agent_" + ("a1" * 24)
    request = {"operation": f"unknown-{secret_token}-{'x' * 2000}", "args": {}}
    result = subprocess.run(
        [sys.executable, str(REMOTE_SCRIPT)],
        input=json.dumps(request),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    prefix = "vcvm release remote refused: "
    assert result.returncode == 75
    assert result.stdout == ""
    assert result.stderr.startswith(prefix)
    assert len(result.stderr) <= len(prefix) + remote.REMOTE_REFUSAL_MESSAGE_LIMIT + 1
    assert "<redacted>" in result.stderr
    assert "Traceback" not in result.stderr
    assert "helper_source" not in result.stderr
    assert secret_token not in result.stderr


def test_remote_helper_capabilities_are_checked_in_and_versioned() -> None:
    payload = remote.handle_request({"operation": "helper.capabilities", "args": {}})
    assert payload["helper_version"] == remote.HELPER_VERSION
    assert "preflight.disk" in payload["operations"]
    assert "release.extract" in payload["operations"]
    assert "state.commit" in payload["operations"]
    assert "docker.system.prune" not in payload["operations"]


def test_preflight_browser_use_uses_canonical_worker_unit_token_and_working_directory_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    unit_path = tmp_path / "cloakbrowser-browser-use-worker.service"
    unit_path.write_text("[Service]\nWorkingDirectory=/home/coder/vk-repos/CloakBrowser-Manager-browser-use\n", encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(argv))
        if argv[:4] == ["systemctl", "--user", "show", BROWSER_USE_UNIT]:
            if "--value" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=f"{unit_path}\n", stderr="")
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "WorkingDirectory=/home/coder/vk-repos/CloakBrowser-Manager-browser-use\n"
                    "ActiveState=active\n"
                    f"FragmentPath={unit_path}\n"
                ),
                stderr="",
            )
        if argv[:4] == ["systemctl", "--user", "is-active", BROWSER_USE_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv == ["git", "-C", "/home/coder/vk-repos/CloakBrowser-Manager-browser-use", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(argv, 0, stdout="true\n", stderr="")
        if argv == ["git", "-C", "/home/coder/vk-repos/CloakBrowser-Manager-browser-use", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, stdout=REVISION + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_token_mode", lambda path: f"mode:{path}")

    result = remote.op_preflight_browser_use(
        {
            "unit": "cloakbrowser-browser-use.service",
            "token_path": "/home/coder/cloakbrowser-manager/.env.worker.vcvm",
        }
    )

    assert result["unit"] == BROWSER_USE_UNIT
    assert result["unit_path"] == str(unit_path)
    assert result["token_mode"] == f"mode:{BROWSER_USE_TOKEN_PATH}"
    assert result["commit"] == REVISION
    assert ("git", "-C", "/home/coder/cloakbrowser-manager", "rev-parse", "HEAD") not in calls
    assert ("git", "-C", "/home/coder/vk-repos/CloakBrowser-Manager-browser-use", "rev-parse", "--short=12", "HEAD") not in calls


def test_preflight_receipts_accepts_absent_first_rollout_layout_without_creating_unrelated_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    for name in ("releases", "backups", "receipts"):
        (paths["root"] / name).rmdir()
    unrelated = paths["root"] / "unrelated"

    result = remote.op_preflight_receipts({})

    assert result["ok"] is True
    assert result["bootstrap_required"] is True
    assert not unrelated.exists()


def test_release_prepare_bootstraps_exact_managed_layout_with_restrictive_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    for name in ("releases", "backups", "receipts"):
        (paths["root"] / name).rmdir()
    unrelated = paths["root"] / "unrelated"

    result = remote.op_release_prepare(
        {
            "release_id": "release-0000001",
            "commit": "0" * 40,
            "archive_sha256": "a" * 64,
        }
    )

    assert result == {"exists": False, "release_id": "release-0000001"}
    for name in ("releases", "backups", "receipts"):
        path = paths["root"] / name
        assert path.is_dir()
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        assert path.stat().st_uid == remote.os.getuid()
    assert (paths["releases"] / "release-0000001").is_dir()
    assert not unrelated.exists()


@pytest.mark.parametrize("name", ["releases", "backups", "receipts"])
def test_preflight_receipts_rejects_symlink_or_insecure_managed_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    managed = paths["root"] / name
    if name == "receipts":
        managed.rmdir()
        managed.symlink_to(target)
        expected = "symlink"
    else:
        managed.chmod(0o755)
        expected = "mode"

    with pytest.raises(remote.HelperError, match=expected):
        remote.op_preflight_receipts({})


def test_preflight_manager_reports_unlabeled_image_truthfully(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            payload = [
                {
                    "Config": {"Image": "cloakbrowser-manager:latest"},
                        "Image": IMAGE_ID,
                    "Mounts": [{"Type": "volume", "Name": remote.MANAGER_VOLUME}],
                    "Name": "/" + remote.MANAGER_CONTAINER,
                }
            ]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, stdout="\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)

    result = remote.op_preflight_manager({})

    assert result["image_id"] == IMAGE_ID
    assert result["image_digest"] == IMAGE_DIGEST
    assert result["revision"] == ""
    assert result["revision_available"] is False


def test_verify_manager_dispatch_requires_exact_labeled_image_and_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True}), stderr="")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=REVISION + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.handle_request(
        {
            "operation": "verify.manager",
            "args": {"commit": REVISION, "revision_available": True, "image_id": IMAGE_ID},
        }
    )

    assert result["health"] is True
    assert result["auth"] is True
    assert result["image_id"] == IMAGE_ID
    assert result["revision"] == REVISION
    assert result["revision_available"] is True
    assert result["revision_matches"] is True


def test_verify_manager_dispatch_allows_unlabeled_empty_commit_only_when_declared_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True}), stderr="")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            raise AssertionError("label must not be inspected when revision is unavailable")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.handle_request(
        {
            "operation": "verify.manager",
            "args": {"commit": "", "revision_available": False, "image_id": IMAGE_ID},
        }
    )

    assert result["image_id"] == IMAGE_ID
    assert result["revision"] == ""
    assert result["revision_available"] is False
    assert result["revision_matches"] is True
    assert not any("--format" in call for call in calls)


def test_verify_manager_dispatch_rejects_unlabeled_empty_commit_when_declared_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr=""))

    with pytest.raises(remote.HelperError, match="commit"):
        remote.handle_request(
            {
                "operation": "verify.manager",
                "args": {"commit": "", "revision_available": True, "image_id": IMAGE_ID},
            }
        )


def test_capture_and_restore_allow_unlabeled_previous_manager_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    capture["previous_revision"] = ""
    capture["previous_revision_available"] = False
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)

    remote.op_restore_runtime({"capture": capture, "backup": write_backup(paths)})

    manager_start = next(call for call in calls if call[:4] == ("docker", "run", "-d", "--name") and call[4] == remote.MANAGER_CONTAINER)
    assert manager_start[-1] == "sha256:" + ("d" * 64)


def test_restore_verify_dispatch_returns_exact_restored_old_image_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    backup = write_backup(paths)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True}), stderr="")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=("1" * 40) + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "op_preflight_browser_use", lambda _args: {"unit_sha256": "1" * 64, "active_state": "active"})
    monkeypatch.setattr(remote, "op_preflight_acpx", lambda _args: {"unit_sha256": "2" * 64, "active_state": "inactive"})

    result = remote.handle_request({"operation": "restore.verify", "args": {"capture": capture, "backup": backup}})

    assert result["health"] is True
    assert result["auth"] is True
    assert result["old_image_id"] == capture["old_image_id"] == IMAGE_ID
    assert result["old_image_digest"] == capture["old_image_digest"] == IMAGE_DIGEST


def test_restore_verify_polls_transient_manager_failure_before_worker_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    backup = write_backup(paths)
    clock = {"now": 0.0}
    sleeps: list[float] = []
    auth_attempts = {"count": 0}
    worker_calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") is None or 0 < float(kwargs["timeout"]) <= 90.0
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            auth_attempts["count"] += 1
            if auth_attempts["count"] == 1:
                return subprocess.CompletedProcess(argv, 56, stdout="", stderr="transient browser runtime")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True}), stderr="")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=("1" * 40) + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", fake_sleep)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "op_preflight_browser_use",
        lambda _args: worker_calls.append("browser") or {"unit_sha256": "1" * 64, "active_state": "active"},
    )
    monkeypatch.setattr(
        remote,
        "op_preflight_acpx",
        lambda _args: worker_calls.append("acpx") or {"unit_sha256": "2" * 64, "active_state": "inactive"},
    )

    result = remote.op_restore_verify({"capture": capture, "backup": backup})

    assert result["health"] is True
    assert result["auth"] is True
    assert auth_attempts["count"] == 2
    assert sleeps == [2.0]
    assert worker_calls == ["browser", "acpx"]


def test_restore_verify_timeout_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    backup = write_backup(paths)
    clock = {"now": 0.0}
    secret = "cbm_worker_" + ("1" * 64)
    worker_calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") is None or 0 < float(kwargs["timeout"]) <= 0.15
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            return subprocess.CompletedProcess(argv, 56, stdout="", stderr=f"token={secret}")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=("1" * 40) + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "RESTORE_MANAGER_VERIFY_TIMEOUT_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(remote, "RESTORE_MANAGER_VERIFY_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "op_preflight_browser_use", lambda _args: worker_calls.append("browser") or {})
    monkeypatch.setattr(remote, "op_preflight_acpx", lambda _args: worker_calls.append("acpx") or {})

    with pytest.raises(
        remote.HelperError,
        match=r"restore Manager verification failed: attempt_count=2 .*deadline_seconds=0\.15 .*reason=auth_curl_56",
    ) as exc_info:
        remote.op_restore_verify({"capture": capture, "backup": backup})

    message = str(exc_info.value)
    assert secret not in message
    assert "token" not in message
    assert "browser runtime" not in message
    assert worker_calls == []


def test_restore_verify_recomputes_remaining_timeout_between_manager_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    capture = valid_capture(paths)
    backup = write_backup(paths)
    clock = {"now": 0.0}
    worker_calls: list[str] = []
    calls: list[tuple[str, float]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert timeout is not None
        timeout_seconds = float(timeout)
        command = " ".join(str(item) for item in argv)
        calls.append((command, timeout_seconds))
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            assert timeout_seconds <= 0.07
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER] and "--format" in argv:
            raise AssertionError("revision probe must not start after global restore Manager deadline")

        delay = 0.12
        if timeout_seconds < delay:
            clock["now"] += timeout_seconds
            raise subprocess.TimeoutExpired(argv, timeout=timeout_seconds)
        clock["now"] += delay

        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/health"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["curl", "-fsS", f"http://127.0.0.1:{remote.LIVE_PORT}/api/auth/status"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True}), stderr="")
        if argv == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"Image": IMAGE_ID}]), stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "RESTORE_MANAGER_VERIFY_TIMEOUT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(remote, "RESTORE_MANAGER_VERIFY_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "op_preflight_browser_use", lambda _args: worker_calls.append("browser") or {})
    monkeypatch.setattr(remote, "op_preflight_acpx", lambda _args: worker_calls.append("acpx") or {})

    with pytest.raises(
        remote.HelperError,
        match=r"restore Manager verification failed: attempt_count=1 .*deadline_seconds=0\.3 .*reason=TimeoutExpired",
    ):
        remote.op_restore_verify({"capture": capture, "backup": backup})

    assert [timeout for _command, timeout in calls] == pytest.approx([0.3, 0.18, 0.06])
    assert len(calls) == 3
    assert clock["now"] == pytest.approx(0.3)
    assert worker_calls == []


def test_rollback_previous_skips_label_compare_only_when_previous_revision_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if argv[:3] == ["docker", "image", "inspect"] and "--format" in argv:
            raise AssertionError("revision label must not be inspected when unavailable")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)
    image_ref = IMAGE_ID

    result = remote.op_rollback_verify_previous(
        {
            "previous_runtime": {
                "image_ref": image_ref,
                "image_id": image_ref,
                "image_digest": IMAGE_DIGEST,
                "revision": "",
                "revision_available": False,
            }
        }
    )

    assert result["image_ref"] == image_ref
    assert result["image_id"] == image_ref
    assert result["image_digest"] == IMAGE_DIGEST
    assert result["revision"] == ""
    assert result["revision_available"] is False


def test_rollback_previous_keeps_strict_revision_check_for_labeled_images(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["docker", "image", "inspect"] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=("1" * 40) + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    with pytest.raises(remote.HelperError, match="revision mismatch"):
        remote.op_rollback_verify_previous(
            {
                "previous_runtime": {
                    "image_ref": IMAGE_ID,
                    "image_id": IMAGE_ID,
                    "image_digest": IMAGE_DIGEST,
                    "revision": REVISION,
                    "revision_available": True,
                }
            }
        )


def test_rollback_verify_previous_dispatch_shape_for_labeled_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["docker", "image", "inspect"] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=REVISION + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.handle_request(
        {
            "operation": "rollback.verify_previous",
            "args": {
                "previous_runtime": {
                    "image_ref": IMAGE_ID,
                    "image_id": IMAGE_ID,
                    "image_digest": IMAGE_DIGEST,
                    "revision": REVISION,
                    "revision_available": True,
                }
            },
        }
    )

    assert result["image_ref"] == IMAGE_ID
    assert result["image_id"] == IMAGE_ID
    assert result["image_digest"] == IMAGE_DIGEST
    assert result["revision"] == REVISION
    assert result["revision_available"] is True


def test_rollback_verify_previous_dispatch_shape_for_unlabeled_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["docker", "image", "inspect"] and "--format" in argv:
            raise AssertionError("revision label must not be inspected when unavailable")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.handle_request(
        {
            "operation": "rollback.verify_previous",
            "args": {
                "previous_runtime": {
                    "image_ref": IMAGE_ID,
                    "image_id": IMAGE_ID,
                    "image_digest": IMAGE_DIGEST,
                    "revision": "",
                    "revision_available": False,
                }
            },
        }
    )

    assert result["image_ref"] == IMAGE_ID
    assert result["image_id"] == IMAGE_ID
    assert result["image_digest"] == IMAGE_DIGEST
    assert result["revision"] == ""
    assert result["revision_available"] is False


def test_manager_bind_sources_are_exact_allowlisted_read_only_dirs() -> None:
    assert remote.MANAGER_BIND_MOUNTS == (
        (Path("/home/coder/orca"), Path("/home/coder/orca"), "ro"),
        (Path("/home/coder/.local"), Path("/home/coder/.local"), "ro"),
        (Path("/home/coder/.config/orca"), Path("/home/coder/.config/orca"), "ro"),
    )


def test_manager_bind_source_validation_rejects_symlink_or_missing_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.symlink_to(source)
    monkeypatch.setattr(remote, "MANAGER_BIND_MOUNTS", ((target, target, "ro"),))

    with pytest.raises(remote.HelperError, match="symlink"):
        remote._validate_manager_bind_sources()


def write_release_commit(paths: dict[str, Path], release_id: str = "release-0000001", commit: str = REVISION) -> Path:
    release = paths["releases"] / release_id
    source = release / "source"
    source.mkdir(parents=True)
    (release / "COMMIT").write_text(commit + "\n", encoding="utf-8")
    return source


def rebind_systemctl_stub(
    paths: dict[str, Path],
    source_path: Path,
    calls: list[tuple[str, ...]],
    *,
    acpx_fragment: str | None,
) -> object:
    browser_unit = paths["home"] / ".config" / "systemd" / "user" / BROWSER_USE_UNIT

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if argv == ["systemctl", "--user", "show", BROWSER_USE_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{browser_unit}\n", stderr="")
        if argv == ["systemctl", "--user", "is-active", BROWSER_USE_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv == ["systemctl", "--user", "daemon-reload"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv == ["systemctl", "--user", "restart", BROWSER_USE_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv == [
            "systemctl",
            "--user",
            "show",
            BROWSER_USE_UNIT,
            "-p",
            "WorkingDirectory",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
        ]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={source_path}\nActiveState=active\nFragmentPath={browser_unit}\n",
                stderr="",
            )
        if argv == ["systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{acpx_fragment or ''}\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    return fake_run


def bootstrap_absent_capture(paths: dict[str, Path], release_id: object = "release-0000001") -> dict[str, object]:
    capture = valid_capture(paths)
    capture.update(
        {
            "acpx_was_absent": True,
            "acpx_active_state": "absent",
            "acpx_unit_sha256": "0" * 64,
            "acpx_dropin_exists": False,
            "acpx_absence": {
                "state": "absent",
                "missing": list(remote.ACPX_ABSENCE_COMPONENTS),
                "reason_code": "missing_runtime",
            },
            "acpx_bootstrap_release_id": release_id,
        }
    )
    return capture


def corrupt_bootstrap_absence_capture(capture: dict[str, object], variant: str) -> None:
    if variant == "mismatched_bootstrap_release_id":
        capture["acpx_bootstrap_release_id"] = "release-other0001"
    elif variant == "missing_bootstrap_release_id":
        capture.pop("acpx_bootstrap_release_id")
    elif variant == "was_absent_false":
        capture["acpx_was_absent"] = False
    elif variant == "active_state":
        capture["acpx_active_state"] = "inactive"
    elif variant == "unit_hash":
        capture["acpx_unit_sha256"] = "2" * 64
    elif variant == "dropin_exists":
        capture["acpx_dropin_exists"] = True
    elif variant == "missing_absence_receipt":
        capture.pop("acpx_absence")
    elif variant == "non_object_absence_receipt":
        capture["acpx_absence"] = "absent"
    elif variant == "wrong_absence_state":
        absence = dict(capture["acpx_absence"])
        absence["state"] = "blocking"
        capture["acpx_absence"] = absence
    elif variant == "partial_absence_missing":
        absence = dict(capture["acpx_absence"])
        absence["missing"] = ["unit"]
        capture["acpx_absence"] = absence
    elif variant == "extra_absence_missing":
        absence = dict(capture["acpx_absence"])
        absence["missing"] = [*remote.ACPX_ABSENCE_COMPONENTS, "extra"]
        capture["acpx_absence"] = absence
    elif variant == "wrong_absence_reason":
        absence = dict(capture["acpx_absence"])
        absence["reason_code"] = "misconfigured"
        capture["acpx_absence"] = absence
    else:
        raise AssertionError(f"unknown bootstrap absence variant: {variant}")


def test_workers_rebind_defers_acpx_when_bootstrap_capture_proves_prior_absence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    source_path = write_release_commit(paths)
    capture = bootstrap_absent_capture(paths)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(remote, "run", rebind_systemctl_stub(paths, source_path, calls, acpx_fragment=None))
    monkeypatch.setattr(remote, "_browser_use_worktree_commit", lambda _working_directory: REVISION)
    monkeypatch.setattr(remote, "_token_mode", lambda _path: "600")
    monkeypatch.setattr(remote, "op_preflight_acpx", lambda _args: (_ for _ in ()).throw(AssertionError("ACPX preflight must be deferred")))

    result = remote.op_workers_rebind({"release_id": "release-0000001", "commit": REVISION, "capture": capture})

    assert result["release_source"] == str(source_path)
    assert result["acpx"] == {
        "deferred": True,
        "reason": "bootstrap_prior_absence",
        "release_id": "release-0000001",
        "bootstrap_release_id": "release-0000001",
        "unit": remote.ACPX_UNIT,
        "promoted_by": "bootstrap.acpx_promote",
    }
    assert BROWSER_USE_UNIT in result["dropins"]
    assert remote.ACPX_UNIT not in result["dropins"]
    assert ("systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value") not in calls
    assert ("systemctl", "--user", "restart", remote.ACPX_UNIT) not in calls


@pytest.mark.parametrize(
    "variant",
    [
        "mismatched_bootstrap_release_id",
        "missing_bootstrap_release_id",
        "was_absent_false",
        "active_state",
        "unit_hash",
        "dropin_exists",
        "missing_absence_receipt",
        "non_object_absence_receipt",
        "wrong_absence_state",
        "partial_absence_missing",
        "extra_absence_missing",
        "wrong_absence_reason",
    ],
)
def test_workers_rebind_rejects_inconsistent_bootstrap_absence_before_browser_use_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variant: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    source_path = write_release_commit(paths)
    capture = bootstrap_absent_capture(paths)
    corrupt_bootstrap_absence_capture(capture, variant)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(remote, "run", rebind_systemctl_stub(paths, source_path, calls, acpx_fragment=None))

    with pytest.raises(remote.HelperError, match="captured ACPX bootstrap absence"):
        remote.op_workers_rebind({"release_id": "release-0000001", "commit": REVISION, "capture": capture})

    assert calls == []
    assert not remote.release_dropin(BROWSER_USE_UNIT).exists()


def test_workers_rebind_normal_release_still_requires_existing_acpx_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    source_path = write_release_commit(paths)
    capture = valid_capture(paths)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(remote, "run", rebind_systemctl_stub(paths, source_path, calls, acpx_fragment=None))
    monkeypatch.setattr(remote, "_browser_use_worktree_commit", lambda _working_directory: REVISION)
    monkeypatch.setattr(remote, "_token_mode", lambda _path: "600")

    with pytest.raises(remote.HelperError, match="unit fragment path is missing: cloakbrowser-acpx.service"):
        remote.op_workers_rebind({"release_id": "release-0000001", "commit": REVISION, "capture": capture})

    assert ("systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value") in calls


def test_manager_docker_run_paths_publish_canonical_container_port_and_host_gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="0" * 40, stderr="")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)
    image_ref = "sha256:" + ("d" * 64)
    remote.op_candidate_start({"release_id": "release-0000001", "image_ref": image_ref, "volume": "candidate-volume"})
    remote.op_live_start({"image_ref": image_ref, "volume": remote.MANAGER_VOLUME, "port": 18115})
    remote.op_restore_runtime({"capture": valid_capture(paths), "backup": write_backup(paths)})
    remote.op_rollback_start_previous(
        {
            "previous_runtime": {
                "image_ref": image_ref,
                "image_id": image_ref,
                "image_digest": "d" * 64,
                "revision": "0" * 40,
                "revision_available": True,
            }
        }
    )

    published_ports = [item for call in calls for item in call if item.startswith("127.0.0.1:")]
    assert "127.0.0.1:18116:8080" in published_ports
    assert published_ports.count("127.0.0.1:18115:8080") == 3
    assert all(not item.endswith(":8000") for item in published_ports)
    manager_runs = [call for call in calls if call[:3] == ("docker", "run", "-d")]
    assert len(manager_runs) == 4
    assert all("--add-host" in call for call in manager_runs)
    assert all("host.docker.internal:host-gateway" in call for call in manager_runs)
    assert all("--restart" in call for call in manager_runs)
    assert all("unless-stopped" in call for call in manager_runs)
    assert all(not item.startswith("0.0.0.0:") for item in published_ports)
    for source, target, mode in remote.MANAGER_BIND_MOUNTS:
        assert all(f"{source}:{target}:{mode}" in call for call in manager_runs)
        assert mode == "ro"


def test_candidate_verify_waits_through_transient_curl_56_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    attempts = {"health": 0}
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health":
            attempts["health"] += 1
            if attempts["health"] == 1:
                return subprocess.CompletedProcess(argv, 56, stdout="", stderr="connection reset")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/auth/status":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True, "access_control_enabled": True}), stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/admin/migrations":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(list(remote.EXPECTED_MIGRATIONS)), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            return subprocess.CompletedProcess(argv, 0, stdout=commit + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", lambda path, timeout: list(remote.EXPECTED_MIGRATIONS))
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert result["health"] is True
    assert result["auth_required"] is True
    assert result["access_control_enabled"] is True
    assert result["revision"] == commit
    assert result["migration_set_exact"] is True
    assert result["attempt_count"] == 2
    assert result["readiness_reason"] == "ready"
    assert result["deadline_seconds"] == 1.0
    assert sleeps == [0.1]


def test_candidate_verify_requires_task_artifacts_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    old_required_migrations = [
        "agent_workspace_v1",
        "task_runs_v1",
        "worker_runtime_v1",
        "task_runs_acpx_v1",
        "worker_harness_presence_v1",
        "worker_harness_preflights_v1",
        "task_run_binding_v1",
    ]

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/auth/status":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True, "access_control_enabled": True}), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            return subprocess.CompletedProcess(argv, 0, stdout=commit + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", lambda path, timeout: old_required_migrations)

    with pytest.raises(remote.HelperError, match=r"attempt_count=1.*reason=migration_set_exact"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})


def test_candidate_verify_waits_through_malformed_startup_json_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    auth_attempts = {"count": 0}
    ticks = iter([0.0, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/auth/status":
            auth_attempts["count"] += 1
            stdout = "{" if auth_attempts["count"] == 1 else json.dumps({"auth_required": True, "access_control_enabled": True})
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/admin/migrations":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(list(remote.EXPECTED_MIGRATIONS)), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            return subprocess.CompletedProcess(argv, 0, stdout=commit + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", lambda path, timeout: list(remote.EXPECTED_MIGRATIONS))
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)

    result = remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert result["attempt_count"] == 2
    assert result["readiness_reason"] == "ready"


def test_candidate_verify_times_out_with_readiness_attempt_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    attempts = {"health": 0}
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 0.2, 0.2, 0.4, 0.4])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health":
            attempts["health"] += 1
            return subprocess.CompletedProcess(argv, 56, stdout="", stderr="connection reset")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.25, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(remote.HelperError, match=r"attempt_count=2.*deadline_seconds=0\.25.*reason=health_curl_56"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert attempts["health"] == 2
    assert sleeps
    assert all(0 < seconds <= 0.1 for seconds in sleeps)


def test_candidate_verify_fails_fast_on_valid_invariant_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/auth/status":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": False, "access_control_enabled": True}), stderr="")
        if argv[0] == "curl" and argv[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/api/admin/migrations":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(list(remote.EXPECTED_MIGRATIONS)), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            return subprocess.CompletedProcess(argv, 0, stdout=commit + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", lambda path, timeout: list(remote.EXPECTED_MIGRATIONS))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep after valid mismatch")))

    with pytest.raises(remote.HelperError, match=r"attempt_count=1.*reason=auth_required"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert sum(1 for call in calls if call[0] == "curl" and call[-1] == f"http://127.0.0.1:{remote.CANDIDATE_PORT}/health") == 1


def test_candidate_verify_binds_hung_curl_to_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    calls: list[tuple[tuple[str, ...], float | None]] = []
    ticks = iter([0.0, 0.0, 0.15, 0.15])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        calls.append((tuple(str(item) for item in argv), float(timeout) if timeout is not None else None))
        if "curl" in argv and "/health" in argv[-1]:
            raise subprocess.TimeoutExpired(argv, timeout=timeout)
        raise AssertionError(f"unexpected command after hung health curl: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)

    with pytest.raises(remote.HelperError, match=r"attempt_count=1.*deadline_seconds=0\.1.*reason=TimeoutExpired"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert len(calls) == 1
    argv, timeout = calls[0]
    assert "--connect-timeout" in argv
    assert "--max-time" in argv
    assert timeout is not None and 0 < timeout <= 0.1
    max_time = float(argv[argv.index("--max-time") + 1])
    connect_timeout = float(argv[argv.index("--connect-timeout") + 1])
    assert 0 < connect_timeout <= max_time <= 0.1


def test_candidate_verify_binds_hung_docker_inspect_to_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = REVISION
    calls: list[tuple[tuple[str, ...], float | None]] = []
    ticks = iter([0.0, 0.0, 0.02, 0.04, 0.06, 0.12])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        calls.append((tuple(str(item) for item in argv), float(timeout) if timeout is not None else None))
        if "/health" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "/api/auth/status" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"auth_required": True, "access_control_enabled": True}), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            raise subprocess.TimeoutExpired(argv, timeout=timeout)
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "time", pytime, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", lambda path, timeout=None: list(remote.EXPECTED_MIGRATIONS))
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: None)

    with pytest.raises(remote.HelperError, match=r"attempt_count=1.*deadline_seconds=0\.1.*reason=TimeoutExpired"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    docker_call = next((argv, timeout) for argv, timeout in calls if argv[:3] == ("docker", "inspect", "candidate"))
    assert docker_call[1] is not None and 0 < docker_call[1] <= 0.1


@pytest.mark.parametrize(
    ("status", "migrations", "revision", "expected_reason", "forbidden_probe"),
    [
        ({"auth_required": False, "access_control_enabled": True}, AssertionError("migrations must not run"), AssertionError("revision must not run"), "auth_required", "migrations"),
        ({"auth_required": True, "access_control_enabled": False}, AssertionError("migrations must not run"), AssertionError("revision must not run"), "access_control_enabled", "migrations"),
        ({"auth_required": True, "access_control_enabled": True}, ["agent_workspace_v1"], AssertionError("revision must not run"), "migration_set_exact", "revision"),
        ({"auth_required": True, "access_control_enabled": True}, list(remote.EXPECTED_MIGRATIONS), "1" * 40, "revision_mismatch", "none"),
    ],
)
def test_candidate_verify_fails_fast_in_probe_order_before_later_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, object],
    migrations: list[str] | AssertionError,
    revision: str | AssertionError,
    expected_reason: str,
    forbidden_probe: str,
) -> None:
    commit = REVISION
    calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "/health" in argv[-1]:
            calls.append("health")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "/api/auth/status" in argv[-1]:
            calls.append("auth")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(status), stderr="")
        if argv[:3] == ["docker", "inspect", "candidate"]:
            calls.append("revision")
            if isinstance(revision, AssertionError):
                raise revision
            return subprocess.CompletedProcess(argv, 0, stdout=revision + "\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    def fake_migrations(path: str, timeout: float | None = None) -> list[str]:
        del path, timeout
        calls.append("migrations")
        if isinstance(migrations, AssertionError):
            raise migrations
        return migrations

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(remote, "_candidate_authenticated_json", fake_migrations)

    with pytest.raises(remote.HelperError, match=rf"attempt_count=1.*reason={expected_reason}"):
        remote.op_candidate_verify({"commit": commit, "container": "candidate"})

    assert forbidden_probe not in calls


def test_verify_proxychecker_probes_docker_bridge_on_host(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    assert remote.op_verify_proxychecker({}) == {"ok": True}
    assert calls == [("curl", "-fsS", "http://172.17.0.1:18899/health")]


def test_verify_stream_uses_openapi_routes_without_profile_or_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    manager_paths: list[str] = []

    def fake_manager_json(path: str) -> dict[str, object]:
        manager_paths.append(path)
        return {
            "paths": {
                "/api/profiles/{profile_id}/live-metrics": {"get": {}},
                "/api/profiles/{profile_id}/cdp": {"get": {}},
                "/api/profiles/{profile_id}/open-links": {"get": {}},
            }
        }

    monkeypatch.setattr(remote, "_manager_json", fake_manager_json)

    result = remote.op_verify_stream({})

    assert result["ok"] is True
    assert result["routes_present"] == [
        "/api/profiles/{profile_id}/cdp",
        "/api/profiles/{profile_id}/live-metrics",
        "/api/profiles/{profile_id}/open-links",
    ]
    assert manager_paths == ["/openapi.json"]


def test_verify_stream_fails_closed_when_openapi_route_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "_manager_json", lambda _path: {"paths": {"/api/profiles/{profile_id}/open-links": {"get": {}}}})

    with pytest.raises(remote.HelperError, match="route"):
        remote.op_verify_stream({})


def test_verify_orca_requires_structured_ready_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "result": {
                        "runtime": {"reachable": True, "state": "ready"},
                        "graph": {"state": "ready"},
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(remote, "run", fake_run)

    result = remote.op_verify_orca({})

    assert result["ok"] is True
    assert result["runtime_reachable"] is True
    assert result["runtime_state"] == "ready"
    assert result["graph_state"] == "ready"
    assert calls == [("orca", "status", "--json")]


@pytest.mark.parametrize(
    "stdout",
    [
        "ready\n",
        json.dumps({"ok": False, "result": {"runtime": {"reachable": True, "state": "ready"}, "graph": {"state": "ready"}}}),
        json.dumps({"ok": True, "result": {"runtime": {"reachable": False, "state": "ready"}, "graph": {"state": "ready"}}}),
        json.dumps({"ok": True, "result": {"runtime": {"reachable": True, "state": "starting"}, "graph": {"state": "ready"}}}),
        json.dumps({"ok": True, "result": {"runtime": {"reachable": True, "state": "ready"}, "graph": {"state": "building"}}}),
    ],
)
def test_verify_orca_fails_closed_for_malformed_or_unready_status(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=""))

    with pytest.raises(remote.HelperError, match="Orca"):
        remote.op_verify_orca({})


def test_remote_helper_uses_argv_arrays_for_local_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...] | list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert not isinstance(argv, str)
        calls.append(tuple(str(item) for item in argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"ok": True, "result": {"runtime": {"reachable": True, "state": "ready"}, "graph": {"state": "ready"}}}),
            stderr="",
        )

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    payload = remote.handle_request({"operation": "verify.orca", "args": {}})
    assert payload["ok"] is True
    assert calls == [("orca", "status", "--json")]


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
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)
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


def test_state_commit_allows_empty_previous_release_only_for_consistent_first_managed_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])
    payload["previous_release"] = ""
    payload["capture"]["state"] = {}
    payload["capture"]["current_pointer"] = ""
    payload["previous_runtime"]["pointer"] = ""

    result = remote.op_state_commit(payload)

    assert result["previous_release"] == ""
    assert json.loads(paths["state"].read_text(encoding="utf-8"))["previous_release"] == ""
    assert stat.S_IMODE(paths["state"].stat().st_mode) == 0o600
    assert paths["current"].resolve() == release_source


@pytest.mark.parametrize(
    ("capture_state", "current_pointer", "previous_pointer"),
    [
        ({"current_release": "release-previous-1"}, "", ""),
        ({"previous_release": "release-previous-1"}, "", ""),
        ({}, "/home/coder/cloakbrowser-manager/releases/release-previous-1/source", ""),
        ({}, "", "release-previous-1"),
    ],
)
def test_state_commit_rejects_empty_previous_release_with_prior_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capture_state: dict[str, object],
    current_pointer: str,
    previous_pointer: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])
    payload["previous_release"] = ""
    payload["capture"]["state"] = capture_state
    payload["capture"]["current_pointer"] = current_pointer
    payload["previous_runtime"]["pointer"] = previous_pointer

    with pytest.raises(remote.HelperError, match="previous release"):
        remote.op_state_commit(payload)

    assert not paths["state"].exists()
    assert not paths["current"].exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture", []),
        ("capture.state", []),
        ("previous_runtime", []),
    ],
)
def test_state_commit_rejects_empty_previous_release_with_malformed_first_release_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])
    payload["previous_release"] = ""
    payload["capture"]["state"] = {}
    payload["capture"]["current_pointer"] = ""
    payload["previous_runtime"]["pointer"] = ""
    if field == "capture":
        payload["capture"] = value
    elif field == "capture.state":
        payload["capture"]["state"] = value
    elif field == "previous_runtime":
        payload["previous_runtime"] = value

    with pytest.raises(remote.HelperError, match="first release"):
        remote.op_state_commit(payload)

    assert not paths["state"].exists()
    assert not paths["current"].exists()


def test_state_commit_still_strictly_validates_non_empty_previous_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    payload = json.loads(tx.PHASE_REQUEST_EXAMPLES["state.commit"][-1])
    payload["previous_release"] = "not safe"

    with pytest.raises(remote.HelperError, match="unsafe release id"):
        remote.op_state_commit(payload)

    assert not paths["state"].exists()


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
    dropin = remote.release_dropin(BROWSER_USE_UNIT)
    dropin.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside-dropin-target"
    target.write_text("do-not-touch", encoding="utf-8")
    dropin.with_suffix(".tmp").symlink_to(target)
    capture = valid_capture(paths)
    capture["browser_use_dropin_exists"] = True
    capture["browser_use_dropin_content"] = "[Service]\nWorkingDirectory=/safe\n"

    with pytest.raises(remote.HelperError, match="temporary"):
        remote._restore_release_dropin(BROWSER_USE_UNIT, capture, "browser_use")

    assert target.read_text(encoding="utf-8") == "do-not-touch"


def write_release_acpx_target(paths: dict[str, Path], release_id: str = "release-0000001", *, promoted: bool = False) -> Path:
    runtime = "acpx-runtime" if promoted else "acpx-bootstrap/node-runtime"
    target = paths["releases"] / release_id / runtime / "node_modules" / "acpx" / "dist" / "cli.js"
    target.parent.mkdir(parents=True)
    target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    target.chmod(0o700)
    return target


def write_npm_bin_symlink(paths: dict[str, Path], release_id: str = "release-0000001", *, promoted: bool = False) -> Path:
    runtime = "acpx-runtime" if promoted else "acpx-bootstrap/node-runtime"
    link = paths["releases"] / release_id / runtime / "node_modules" / ".bin" / "acpx"
    link.parent.mkdir(parents=True)
    link.symlink_to("../acpx/dist/cli.js")
    return link


def install_bootstrap_root_symlink(paths: dict[str, Path], release_id: str = "release-0000001") -> tuple[Path, Path]:
    release = paths["releases"] / release_id
    release.mkdir(parents=True, exist_ok=True)
    outside = paths["root"].parent / "outside-bootstrap"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (release / "acpx-bootstrap").symlink_to(outside, target_is_directory=True)
    return outside, sentinel


def prepare_bootstrap_install_source(paths: dict[str, Path], release_id: str = "release-0000001") -> dict[str, object]:
    release = paths["releases"] / release_id
    source = release / "source"
    node_lock = source / "deploy" / "acpx-runtime" / "package-lock.json"
    python_lock = source / "scripts" / "requirements-acpx-worker.linux-x86_64.py312.txt"
    node_lock.parent.mkdir(parents=True)
    python_lock.parent.mkdir(parents=True)
    node_lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    python_lock.write_text("# lock\n", encoding="utf-8")
    (release / "COMMIT").write_text("0" * 40 + "\n", encoding="utf-8")
    return {
        "release_id": release_id,
        "commit": "0" * 40,
        "node_lock_sha256": remote.file_sha256(node_lock),
        "python_lock_sha256": remote.file_sha256(python_lock),
    }


def assert_outside_bootstrap_untouched(outside: Path, sentinel: Path) -> None:
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def tree_snapshot(path: Path) -> dict[str, str]:
    if not path.exists() and not path.is_symlink():
        return {}
    snapshot: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        relative = str(item.relative_to(path))
        if item.is_symlink():
            snapshot[relative] = f"symlink:{item.readlink()}"
        elif item.is_dir():
            snapshot[relative] = "dir"
        else:
            snapshot[relative] = item.read_text(encoding="utf-8")
    return snapshot


def systemd_environment_lines(unit_text: str) -> list[str]:
    return [line for line in unit_text.splitlines() if line.startswith("Environment=")]


def install_release_id_symlink(paths: dict[str, Path], release_id: str = "release-0000001") -> tuple[Path, dict[str, str]]:
    sibling = paths["releases"] / "release-sibling-0001"
    sibling.mkdir()
    (sibling / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    (paths["releases"] / release_id).symlink_to(sibling, target_is_directory=True)
    return sibling, tree_snapshot(sibling)


def prepare_sibling_release_source(sibling: Path) -> None:
    source = sibling / "source"
    (source / "scripts").mkdir(parents=True, exist_ok=True)
    (source / "scripts" / "cbm-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "scripts" / "cbm-mcp").chmod(0o700)


def prepare_sibling_install_source(sibling: Path) -> dict[str, object]:
    source = sibling / "source"
    node_lock = source / "deploy" / "acpx-runtime" / "package-lock.json"
    python_lock = source / "scripts" / "requirements-acpx-worker.linux-x86_64.py312.txt"
    node_lock.parent.mkdir(parents=True, exist_ok=True)
    python_lock.parent.mkdir(parents=True, exist_ok=True)
    node_lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    python_lock.write_text("# lock\n", encoding="utf-8")
    (sibling / "COMMIT").write_text("0" * 40 + "\n", encoding="utf-8")
    return {
        "release_id": "release-0000001",
        "commit": "0" * 40,
        "node_lock_sha256": remote.file_sha256(node_lock),
        "python_lock_sha256": remote.file_sha256(python_lock),
    }


def prepare_complete_bootstrap_candidate(paths: dict[str, Path], release_id: str = "release-0000001") -> dict[str, Path]:
    release = paths["releases"] / release_id
    source = release / "source"
    source.mkdir(parents=True, exist_ok=True)
    root = release / "acpx-bootstrap"
    for relative in ("venv/bin/python", "node-runtime/node_modules/acpx/dist/cli.js"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        target.chmod(0o700)
    capability = root / "capability"
    capability.mkdir(parents=True, exist_ok=True)
    policy = capability / "permission-policy.json"
    policy.write_text(json.dumps({"defaultAction": "deny"}) + "\n", encoding="utf-8")
    policy.chmod(0o600)
    mcp = capability / "mcp-config.json"
    mcp.write_text(
        json.dumps({"mcpServers": [{"name": "cloakbrowser", "command": str(source / "scripts" / "cbm-mcp"), "args": []}]}) + "\n",
        encoding="utf-8",
    )
    mcp.chmod(0o600)
    key = root / "candidate.worker.key"
    key.write_text("cbm_worker_" + ("1" * 64) + "\n", encoding="utf-8")
    key.chmod(0o600)
    unit = root / "acpx-candidate-release-0000001.service"
    unit.write_text(
        f"ExecStart={root}/venv/bin/python --manager-url http://127.0.0.1:18116 "
        f"--token-file {key} --worktree {source} "
        f"--permission-policy {policy} --mcp-config {mcp} --capability-dir {capability} "
        f"--acpx {root / 'node-runtime' / 'node_modules' / 'acpx' / 'dist' / 'cli.js'}\n",
        encoding="utf-8",
    )
    unit.chmod(0o600)
    for name in ("acpx-runtime", "acpx-venv", "acpx-capability"):
        durable = release / name
        durable.mkdir(parents=True, exist_ok=True)
        (durable / "sentinel.txt").write_text("durable\n", encoding="utf-8")
    return {
        "release": release,
        "source": source,
        "root": root,
        "unit": unit,
        "node": root / "node-runtime",
        "venv": root / "venv",
        "capability": capability,
        "policy": policy,
        "mcp": mcp,
        "key": key,
        "cli": root / "node-runtime" / "node_modules" / "acpx" / "dist" / "cli.js",
    }


def test_acpx_executable_contract_requires_direct_package_cli_not_npm_bin_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    direct = write_release_acpx_target(paths)
    npm_symlink = write_npm_bin_symlink(paths)

    assert remote._validate_acpx_executable_path("release-0000001", str(direct), kind="candidate") == direct
    with pytest.raises(remote.HelperError, match="allowlisted|non-symlink"):
        remote._validate_acpx_executable_path("release-0000001", str(npm_symlink), kind="candidate")


@pytest.mark.parametrize(
    "path_factory,kind,match",
    [
        (lambda paths: write_release_acpx_target(paths, "release-0000002"), "candidate", "allowlisted"),
        (lambda paths: paths["root"] / "acpx-bootstrap" / "node-runtime" / "node_modules" / "acpx" / "dist" / "cli.js", "candidate", "allowlisted"),
        (lambda paths: write_npm_bin_symlink(paths), "candidate", "allowlisted|non-symlink"),
        (lambda paths: write_npm_bin_symlink(paths, promoted=True), "promoted", "allowlisted|non-symlink"),
    ],
)
def test_acpx_executable_contract_rejects_wrong_release_root_or_symlink_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_factory: object,
    kind: str,
    match: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    write_release_acpx_target(paths)
    write_release_acpx_target(paths, promoted=True)
    path = path_factory(paths)
    if not path.exists() and not path.is_symlink():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        path.chmod(0o700)

    with pytest.raises(remote.HelperError, match=match):
        remote._validate_acpx_executable_path("release-0000001", str(path), kind=kind)


def test_bootstrap_acpx_install_rejects_symlink_bootstrap_root_without_outside_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    args = prepare_bootstrap_install_source(paths)
    outside, sentinel = install_bootstrap_root_symlink(paths)
    monkeypatch.setattr(
        remote,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError(f"unexpected command: {argv}")),
    )

    with pytest.raises(remote.HelperError, match="symlink"):
        remote.op_bootstrap_acpx_install(args)

    assert_outside_bootstrap_untouched(outside, sentinel)


@pytest.mark.parametrize("phase", ["provision", "start", "promote", "cleanup"])
def test_bootstrap_acpx_phases_reject_symlink_bootstrap_root_without_outside_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    outside, sentinel = install_bootstrap_root_symlink(paths)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("cbm_worker_" + ("ab" * 32) + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError, match="symlink"):
        if phase == "provision":
            remote.op_bootstrap_acpx_provision_candidate(
                {
                    "release_id": "release-0000001",
                    "commit": "0" * 40,
                    "manager_port": 18116,
                    "runtime": {},
                }
            )
        elif phase == "start":
            remote.op_bootstrap_acpx_start_candidate(
                {"release_id": "release-0000001", "worker_id": "acpx-candidate-release-0000001"}
            )
        elif phase == "promote":
            remote.op_bootstrap_acpx_promote(
                {
                    "release_id": "release-0000001",
                    "worker_id": "acpx-candidate-release-0000001",
                    "manager_port": 18115,
                    "release_source": str(release_source),
                    "acpx_executable": str(
                        paths["releases"]
                        / "release-0000001"
                        / "acpx-runtime"
                        / "node_modules"
                        / "acpx"
                        / "dist"
                        / "cli.js"
                    ),
                }
            )
        else:
            remote.op_bootstrap_acpx_cleanup({"release_id": "release-0000001"})

    assert_outside_bootstrap_untouched(outside, sentinel)


@pytest.mark.parametrize("phase", ["install", "provision", "start", "promote", "cleanup", "restore_cleanup"])
def test_bootstrap_acpx_rejects_release_id_symlink_without_sibling_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    sibling, before = install_release_id_symlink(paths)
    prepare_sibling_release_source(sibling)
    before = tree_snapshot(sibling)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("cbm_worker_" + ("ab" * 32) + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))
    if phase == "install":
        args = prepare_sibling_install_source(sibling)
        before = tree_snapshot(sibling)
    if phase in {"promote", "cleanup", "restore_cleanup"}:
        prepare_complete_bootstrap_candidate({"releases": paths["releases"]}, release_id="release-sibling-0001")
        before = tree_snapshot(sibling)

    with pytest.raises(remote.HelperError, match="release.*symlink|symlink"):
        if phase == "install":
            remote.op_bootstrap_acpx_install(args)
        elif phase == "provision":
            remote.op_bootstrap_acpx_provision_candidate(
                {
                    "release_id": "release-0000001",
                    "commit": "0" * 40,
                    "manager_port": 18116,
                    "runtime": {},
                }
            )
        elif phase == "start":
            remote.op_bootstrap_acpx_start_candidate(
                {"release_id": "release-0000001", "worker_id": "acpx-candidate-release-0000001"}
            )
        elif phase == "promote":
            remote.op_bootstrap_acpx_promote(
                {
                    "release_id": "release-0000001",
                    "worker_id": "acpx-candidate-release-0000001",
                    "manager_port": 18115,
                    "release_source": str(paths["releases"] / "release-0000001" / "source"),
                    "acpx_executable": str(
                        paths["releases"]
                        / "release-0000001"
                        / "acpx-runtime"
                        / "node_modules"
                        / "acpx"
                        / "dist"
                        / "cli.js"
                    ),
                }
            )
        elif phase == "cleanup":
            remote.op_bootstrap_acpx_cleanup({"release_id": "release-0000001"})
        else:
            remote._remove_acpx_bootstrap_artifacts(
                {"acpx_bootstrap_release_id": "release-0000001", "acpx_was_absent": True}
            )

    assert tree_snapshot(sibling) == before


@pytest.mark.parametrize("artifact", ["missing", "symlink"])
def test_bootstrap_start_requires_existing_regular_candidate_unit_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact: str,
) -> None:
    patch_remote_paths(monkeypatch, tmp_path)
    root = remote.acpx_bootstrap_dir("release-0000001")
    unit = root / "acpx-candidate-release-0000001.service"
    root.mkdir(parents=True, exist_ok=True)
    if artifact == "symlink":
        target = tmp_path / "outside-unit"
        target.write_text("[Service]\n", encoding="utf-8")
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.symlink_to(target)
    calls: list[list[str]] = []
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))

    with pytest.raises(remote.HelperError, match="candidate unit"):
        remote.op_bootstrap_acpx_start_candidate(
            {"release_id": "release-0000001", "worker_id": "acpx-candidate-release-0000001"}
        )

    assert calls == []
    assert not remote.expected_unit_path("acpx-candidate-release-0000001.service").exists()


@pytest.mark.parametrize("missing", ["source", "unit", "node", "venv", "capability", "key", "cli"])
def test_bootstrap_promote_preflights_all_candidate_artifacts_before_durable_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    if missing == "source":
        candidate["source"].rmdir()
    elif missing == "cli":
        candidate["cli"].unlink()
    else:
        target = candidate[missing]
        if target.is_dir():
            remote.shutil.rmtree(target)
        else:
            target.unlink()
    durable_before = {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    assert calls == []
    assert {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    } == durable_before


@pytest.mark.parametrize("config_name", ["policy", "mcp"])
@pytest.mark.parametrize("bad_state", ["missing", "symlink", "wrong-mode", "wrong-content"])
def test_bootstrap_promote_preflights_candidate_configs_before_durable_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_name: str,
    bad_state: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    config = candidate[config_name]
    if bad_state == "missing":
        config.unlink()
    elif bad_state == "symlink":
        config.unlink()
        outside = tmp_path / f"outside-{config_name}.json"
        outside.write_text("{}\n", encoding="utf-8")
        config.symlink_to(outside)
    elif bad_state == "wrong-mode":
        config.chmod(0o644)
    elif config_name == "policy":
        config.write_text(json.dumps({"defaultAction": "escalate"}) + "\n", encoding="utf-8")
    else:
        config.write_text(
            json.dumps({"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}) + "\n",
            encoding="utf-8",
        )
    durable_before = {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    assert calls == []
    assert {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    } == durable_before


@pytest.mark.parametrize("arg_name", ["worktree", "acpx"])
@pytest.mark.parametrize("bad_state", ["missing", "wrong"])
def test_bootstrap_promote_validates_permanent_unit_content_before_durable_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arg_name: str,
    bad_state: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    unit_text = candidate["unit"].read_text(encoding="utf-8")
    if arg_name == "worktree":
        original = f" --worktree {candidate['source']}"
        replacement = "" if bad_state == "missing" else f" --worktree {tmp_path / 'foreign-worktree'}"
    else:
        original = f" --acpx {candidate['cli']}"
        replacement = "" if bad_state == "missing" else f" --acpx {candidate['root'] / 'node-runtime' / 'node_modules' / '.bin' / 'acpx'}"
    candidate["unit"].write_text(unit_text.replace(original, replacement), encoding="utf-8")
    durable_before = {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    }
    permanent = remote.expected_unit_path(remote.ACPX_UNIT)
    calls: list[list[str]] = []
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    assert calls == []
    assert not permanent.exists()
    assert {
        name: tree_snapshot(candidate["release"] / name)
        for name in ("acpx-runtime", "acpx-venv", "acpx-capability")
    } == durable_before


def test_bootstrap_candidate_verify_uses_candidate_manager_presence_and_adapter_preflights(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        del timeout
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
    acpx_executable = write_release_acpx_target(paths)
    clock = {"now": 0.0}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        del timeout
        assert port == 18116
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        return {"agents": [{"agent": "codex", "ready": False, "state": "failed", "reason_code": "auth_required"}]}

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote.shutil, "which", lambda name: "/usr/local/bin/acpx" if name == "acpx" else None)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    with pytest.raises(remote.HelperError, match=r"reason=manager_preflight_missing_agent"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )


def test_bootstrap_candidate_verify_polls_transient_presence_then_preflight_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    sleeps: list[float] = []
    version_calls: list[list[str]] = []
    clock = {"now": 0.0}
    presence_payloads = [
        {"worker_seen_recently": False, "state": "starting"},
        {"worker_seen_recently": True, "state": "polling"},
        {"worker_seen_recently": True, "state": "polling"},
    ]
    preflight_payloads = [
        {
            "agents": [
                {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                *[
                    {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                    if agent != "codex"
                ],
            ]
        },
        {
            "agents": [
                {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
            ]
        },
    ]

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            version_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        del timeout
        assert port == 18116
        if path.endswith("/presence"):
            return presence_payloads.pop(0)
        if path.endswith("/preflights"):
            return preflight_payloads.pop(0)
        raise AssertionError(path)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", fake_sleep)
    monkeypatch.setattr(remote, "run", fake_run)
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
    assert result["attempt_count"] == 2
    assert result["deadline_seconds"] == 1.0
    assert result["readiness_reason"] == "ready"
    assert result["components"]["presence"]["ready"] is True
    assert result["components"]["manager_preflights"]["ready"] is True
    assert result["components"]["manager_preflights"]["ready_agents"] == ["codex"]
    assert result["components"]["manager_preflights"]["auth_blocked_agents"] == [
        "claude",
        "cursor",
        "grok-build",
        "opencode",
    ]
    assert version_calls == [[str(acpx_executable), "--version"]]
    assert sleeps == [0.1]


def test_bootstrap_candidate_verify_does_not_cache_failed_adapter_probe_before_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    clock = {"now": 0.0}
    version_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert timeout is not None
        if argv[-1:] == ["--version"]:
            version_calls.append(argv)
            if len(version_calls) == 1:
                raise subprocess.TimeoutExpired(argv, timeout=timeout)
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert port == 18116
        assert timeout is not None
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_verify_candidate(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18116,
            "acpx_executable": str(acpx_executable),
        }
    )

    assert result["readiness_reason"] == "ready"
    assert result["attempt_count"] == 2
    assert version_calls == [[str(acpx_executable), "--version"], [str(acpx_executable), "--version"]]


def test_acpx_manager_preflights_accept_one_ready_agent_with_auth_blocked_expected_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: list[object] = [
        {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
        *[
            {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required"}
            for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
            if agent != "codex"
        ],
    ]
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is True
    assert result["reason_code"] == "ok"
    assert result["ready_agents"] == ["codex"]
    assert result["auth_blocked_agents"] == ["claude", "cursor", "grok-build", "opencode"]
    assert result["failures"] == []


def test_acpx_manager_preflights_reject_all_auth_blocked_agents_with_stable_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: list[object] = [
        {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required"}
        for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
    ]
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is False
    assert result["reason_code"] == "no_ready_agent"
    assert result["ready_agents"] == []
    assert result["auth_blocked_agents"] == list(remote.EXPECTED_ACPX_PREFLIGHT_AGENTS)
    assert result["failures"] == []


@pytest.mark.parametrize(
    "bad_agent,reason_code",
    [
        ({"ready": False, "state": "failed", "reason_code": "protocol_error"}, "protocol_error"),
        ({"ready": False, "state": "stale", "reason_code": "stale"}, "stale"),
        ({"ready": False, "state": "not_checked", "reason_code": "not_checked"}, "not_checked"),
        ({"ready": False, "state": "failed", "reason_code": "adapter_unavailable"}, "adapter_unavailable"),
        ({"ready": True, "state": "ready", "reason_code": "version_mismatch"}, "version_mismatch"),
    ],
)
def test_acpx_manager_preflights_reject_infra_or_stale_expected_agent_states(
    monkeypatch: pytest.MonkeyPatch,
    bad_agent: dict[str, object],
    reason_code: str,
) -> None:
    agents: list[object] = [
        {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
        for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
    ]
    agents[1] = {"agent": "claude", **bad_agent}
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is False
    assert result["reason_code"] == reason_code
    assert result["ready_agents"] == ["codex", "cursor", "grok-build", "opencode"]
    assert result["auth_blocked_agents"] == []


def test_acpx_manager_preflights_accept_all_ready_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    agents: list[object] = [
        {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
        for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
    ]
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is True
    assert result["reason_code"] == "ok"
    assert result["ready_agents"] == list(remote.EXPECTED_ACPX_PREFLIGHT_AGENTS)
    assert result["auth_blocked_agents"] == []
    assert result["failures"] == []


@pytest.mark.parametrize(
    "agents,reason_code",
    [
        (
            [
                {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                *[
                    {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                    if agent != "codex"
                ][:-1],
            ],
            "missing_agent",
        ),
        (
            [
                *[
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ],
                {"agent": "unexpected", "ready": True, "state": "ready", "reason_code": "ok"},
            ],
            "unexpected_agent",
        ),
        (
            [
                {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                *[
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                    if agent != "codex"
                ],
            ],
            "duplicate_agent",
        ),
    ],
)
def test_acpx_manager_preflights_require_exact_expected_agent_set(
    monkeypatch: pytest.MonkeyPatch,
    agents: list[object],
    reason_code: str,
) -> None:
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is False
    assert result["reason_code"] == reason_code


def test_acpx_manager_preflights_reject_non_dict_agent_entry_in_otherwise_expected_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: list[object] = [
        *[
            {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
            for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
        ],
        "malformed",
    ]
    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: {"agents": agents}, raising=False)

    result = remote._acpx_manager_preflights_ready(18116)

    assert result["ready"] is False
    assert result["reason_code"] == "malformed_agent"


def test_bootstrap_candidate_verify_times_out_with_last_component_reason_without_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    clock = {"now": 0.0}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        del timeout
        assert port == 18116
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": "codex", "ready": False, "state": "failed", "reason_code": "auth_required"},
                    {"agent": "claude", "ready": True, "state": "ready", "reason_code": "ok", "token": "cbm_worker_" + ("1" * 32)},
                ]
            }
        raise AssertionError(path)

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", fake_sleep)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    with pytest.raises(
        remote.HelperError,
        match=r"attempt_count=2 .*deadline_seconds=0\.15 .*reason=manager_preflight_missing_agent",
    ) as exc_info:
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    message = str(exc_info.value)
    assert "cbm_worker_" not in message
    assert "agents" not in message
    assert "token" not in message


@pytest.mark.parametrize("component", ["adapter", "systemctl"])
def test_bootstrap_candidate_verify_bounds_hung_subprocess_probe_to_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    component: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    clock = {"now": 0.0}
    timeouts: list[float] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        if argv[-1:] == ["--version"]:
            if component == "adapter":
                assert timeout is not None
                timeouts.append(float(timeout))
                raise subprocess.TimeoutExpired(argv, timeout=timeout)
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            if component == "systemctl":
                assert timeout is not None
                timeouts.append(float(timeout))
                raise subprocess.TimeoutExpired(argv, timeout=timeout)
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: (_ for _ in ()).throw(AssertionError("manager probe should not run")),
        raising=False,
    )

    with pytest.raises(remote.HelperError, match=rf"reason={component}_TimeoutExpired"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert timeouts
    assert all(0 < timeout <= 0.15 for timeout in timeouts)


def test_bootstrap_candidate_verify_recovers_from_transient_manager_probe_failure_with_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    clock = {"now": 0.0}
    manager_timeouts: list[float] = []
    presence_attempts = {"count": 0}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert timeout is not None
        assert 0 < float(timeout) <= 0.2
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert port == 18116
        assert timeout is not None
        manager_timeouts.append(float(timeout))
        assert 0 < float(timeout) <= 0.2
        if path.endswith("/presence"):
            presence_attempts["count"] += 1
            if presence_attempts["count"] == 1:
                raise remote.URLError("transient")
            return {"worker_seen_recently": True, "state": "polling"}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(remote, "CANDIDATE_READINESS_TIMEOUT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_verify_candidate(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18116,
            "acpx_executable": str(acpx_executable),
        }
    )

    assert result["attempt_count"] == 2
    assert result["readiness_reason"] == "ready"
    assert manager_timeouts


def test_bootstrap_candidate_verify_fails_fast_on_bad_executable_without_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    manager_calls: list[tuple[int, str]] = []
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: manager_calls.append((port, path)) or {},
        raising=False,
    )

    with pytest.raises(remote.HelperError, match="ACPX executable"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": "/usr/local/bin/acpx",
            }
        )

    assert calls == []
    assert manager_calls == []


def test_bootstrap_candidate_verify_fails_fast_on_version_mismatch_without_manager_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    calls: list[list[str]] = []
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.99.0\n", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: manager_calls.append((port, path)) or {},
        raising=False,
    )

    with pytest.raises(remote.HelperError, match=r"reason=version_mismatch"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert calls == [[str(acpx_executable), "--version"]]
    assert manager_calls == []


def test_bootstrap_candidate_verify_fails_fast_on_adapter_unavailable_without_polling_or_manager_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    calls: list[list[str]] = []
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 127, stdout="", stderr="missing adapter\n")
        raise AssertionError(argv)

    monkeypatch.setattr(remote.time, "sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("must not poll adapter_unavailable")))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: manager_calls.append((port, path)) or {},
        raising=False,
    )

    with pytest.raises(remote.HelperError, match=r"attempt_count=1 .*reason=adapter_unavailable"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert calls == [[str(acpx_executable), "--version"]]
    assert manager_calls == []


def test_bootstrap_candidate_verify_rechecks_unit_after_manager_readiness_before_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    systemctl_states = ["active", "inactive"]

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") is not None
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 3, stdout=f"{systemctl_states.pop(0)}\n", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert timeout is not None
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    with pytest.raises(remote.HelperError, match=r"attempt_count=1 .*reason=unit_inactive"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert systemctl_states == []


@pytest.mark.parametrize("active_state", ["inactive", "failed"])
def test_bootstrap_candidate_verify_fails_fast_on_inactive_or_failed_unit_without_manager_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    active_state: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    acpx_executable = write_release_acpx_target(paths)
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:4] == ["systemctl", "--user", "is-active", "acpx-candidate-release-0000001.service"]:
            return subprocess.CompletedProcess(argv, 3, stdout=f"{active_state}\n", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: manager_calls.append((port, path)) or {},
        raising=False,
    )

    with pytest.raises(remote.HelperError, match=rf"attempt_count=1 .*reason=unit_{active_state}"):
        remote.op_bootstrap_acpx_verify_candidate(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18116,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert manager_calls == []


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


def test_bootstrap_acpx_provision_reuses_existing_canonical_worker_key_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    token = "cbm_worker_" + ("ab" * 32)
    source_key.write_text(token + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)

    result = remote.op_bootstrap_acpx_provision_candidate(
        {
            "release_id": "release-0000001",
            "commit": "0" * 40,
            "manager_port": 18116,
            "runtime": {},
        }
    )

    root = remote.acpx_bootstrap_dir("release-0000001")
    candidate_key = root / "candidate.worker.key"
    unit = root / "acpx-candidate-release-0000001.service"
    capability = root / "capability"
    policy = capability / "permission-policy.json"
    mcp = capability / "mcp-config.json"
    assert candidate_key.read_text(encoding="utf-8") == token + "\n"
    assert stat.S_IMODE(candidate_key.stat().st_mode) == 0o600
    unit_text = unit.read_text(encoding="utf-8")
    serialized = json.dumps(result, sort_keys=True) + unit_text + policy.read_text(encoding="utf-8") + mcp.read_text(encoding="utf-8")
    assert token not in serialized
    assert stat.S_IMODE(capability.stat().st_mode) == 0o700
    assert stat.S_IMODE(policy.stat().st_mode) == 0o600
    assert stat.S_IMODE(mcp.stat().st_mode) == 0o600
    assert json.loads(policy.read_text(encoding="utf-8")) == {"defaultAction": "deny"}
    assert json.loads(mcp.read_text(encoding="utf-8")) == {
        "mcpServers": [
            {
                "name": "cloakbrowser",
                "command": str(release_source / "scripts" / "cbm-mcp"),
                "args": [],
            }
        ]
    }
    exec_start = next(line for line in unit_text.splitlines() if line.startswith("ExecStart="))
    assert f"--worktree {release_source}" in exec_start
    assert f"--permission-policy {policy}" in exec_start
    assert f"--mcp-config {mcp}" in exec_start
    assert f"--capability-dir {capability}" in exec_start
    assert f"--acpx {root / 'node-runtime' / 'node_modules' / 'acpx' / 'dist' / 'cli.js'}" in exec_start
    assert " --preflight-interval 30" in exec_start
    assert " --preflight-interval 240" not in exec_start
    assert result["credential_reused"] is True
    assert result["credential_source"] == "browser_use_worker_key"


def test_bootstrap_acpx_provision_writes_only_safe_allowlisted_path_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("cbm_worker_" + ("ab" * 32) + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)
    monkeypatch.setenv("PATH", "/tmp/unsafe:/usr/bin")
    monkeypatch.setenv("GH_TOKEN", "ghp_" + ("a" * 40))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + ("b" * 40))

    remote.op_bootstrap_acpx_provision_candidate(
        {
            "release_id": "release-0000001",
            "commit": "0" * 40,
            "manager_port": 18116,
            "runtime": {},
        }
    )

    unit_text = (remote.acpx_bootstrap_dir("release-0000001") / "acpx-candidate-release-0000001.service").read_text(encoding="utf-8")
    assert systemd_environment_lines(unit_text) == [f"Environment=PATH={EXPECTED_ACPX_SYSTEMD_PATH}"]
    assert "/tmp/unsafe" not in unit_text
    assert "GH_TOKEN" not in unit_text
    assert "OPENAI_API_KEY" not in unit_text
    assert "ghp_" not in unit_text
    assert "sk-" not in unit_text


def test_bootstrap_acpx_provision_requires_existing_release_source_worktree_before_unit_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    (paths["releases"] / "release-0000001").mkdir()
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("cbm_worker_" + ("ab" * 32) + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)

    with pytest.raises(remote.HelperError, match="worktree"):
        remote.op_bootstrap_acpx_provision_candidate(
            {
                "release_id": "release-0000001",
                "commit": "0" * 40,
                "manager_port": 18116,
                "runtime": {},
            }
        )

    assert not (remote.acpx_bootstrap_dir("release-0000001") / "acpx-candidate-release-0000001.service").exists()


@pytest.mark.parametrize(
    ("content", "mode"),
    [
        (None, 0o600),
        ("not-a-worker-token\n", 0o600),
        ("cbm_worker_" + ("cd" * 32) + "\n", 0o644),
    ],
)
def test_bootstrap_acpx_provision_rejects_missing_invalid_or_insecure_source_key_before_unit_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str | None,
    mode: int,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    (paths["releases"] / "release-0000001" / "source").mkdir(parents=True)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    if content is not None:
        source_key.write_text(content, encoding="utf-8")
        source_key.chmod(mode)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)

    with pytest.raises(remote.HelperError, match="worker key"):
        remote.op_bootstrap_acpx_provision_candidate(
            {
                "release_id": "release-0000001",
                "commit": "0" * 40,
                "manager_port": 18116,
                "runtime": {},
            }
        )

    assert not (remote.acpx_bootstrap_dir("release-0000001") / "acpx-candidate-release-0000001.service").exists()


def test_capture_state_records_prior_acpx_absence_without_strict_preflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_strict_acpx(args: dict[str, object]) -> dict[str, object]:
        del args
        raise remote.HelperError("ACPX unit is missing")

    monkeypatch.setattr(
        remote,
        "op_preflight_manager",
        lambda _args: {
            "revision": "1" * 40,
            "revision_available": True,
            "image_digest": "d" * 64,
            "image_id": "sha256:" + ("d" * 64),
            "manager_bind_mounts": remote.manager_bind_mount_receipt(),
            "restart_policy": remote.MANAGER_RESTART_POLICY,
            "network_mode": "bridge",
        },
    )
    monkeypatch.setattr(
        remote,
        "op_preflight_browser_use",
        lambda _args: {
            "unit_sha256": "1" * 64,
            "unit_path": str(remote.expected_unit_path(BROWSER_USE_UNIT)),
            "active_state": "active",
            "dropin_path": str(remote.release_dropin(BROWSER_USE_UNIT)),
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


@pytest.mark.parametrize("fragment_path", ["", "."])
def test_capture_state_records_absent_acpx_when_unit_fragment_is_missing_or_not_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fragment_path: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    browser_unit = paths["home"] / ".config" / "systemd" / "user" / BROWSER_USE_UNIT
    browser_unit.write_text("[Service]\nWorkingDirectory=/home/coder/browser-use\n", encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["docker", "inspect", remote.MANAGER_CONTAINER]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Name": f"/{remote.MANAGER_CONTAINER}",
                            "Image": IMAGE_ID,
                            "Config": {"Image": "cloakbrowser-manager:old", "Labels": {"org.opencontainers.image.revision": REVISION}},
                            "Mounts": [{"Type": "volume", "Name": remote.MANAGER_VOLUME}],
                            "HostConfig": {"RestartPolicy": {"Name": remote.MANAGER_RESTART_POLICY}, "NetworkMode": "bridge"},
                        }
                    ]
                ),
                stderr="",
            )
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{REVISION}\n", stderr="")
        if argv == ["systemctl", "--user", "show", BROWSER_USE_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{browser_unit}\n", stderr="")
        if argv == [
            "systemctl",
            "--user",
            "show",
            BROWSER_USE_UNIT,
            "-p",
            "WorkingDirectory",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
        ]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory=/home/coder/browser-use\nActiveState=active\nFragmentPath={browser_unit}\n",
                stderr="",
            )
        if argv == ["systemctl", "--user", "is-active", BROWSER_USE_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv == ["systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{fragment_path}\n", stderr="")
        if argv == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 3, stdout="inactive\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)
    monkeypatch.setattr(remote, "_browser_use_worktree_commit", lambda _working_directory: REVISION)
    monkeypatch.setattr(remote, "_token_mode", lambda _path: "600")
    monkeypatch.setattr(
        remote,
        "_probe_acpx_state",
        lambda: {"state": "absent", "missing": ["unit"], "reason_code": "missing_runtime"},
        raising=False,
    )
    monkeypatch.setattr(remote, "CURRENT_LINK", tmp_path / "missing-current")

    capture = remote.op_capture_state({"commit": REVISION})

    assert capture["acpx_was_absent"] is True
    assert capture["acpx_active_state"] == "absent"
    assert capture["acpx_unit_path"] == str(remote.expected_unit_path(remote.ACPX_UNIT))
    assert capture["acpx_absence"]["state"] == "absent"


def test_unit_state_rejects_symlink_fragment_path_without_leaking_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.service"
    target.write_text("[Service]\nExecStart=ok\n", encoding="utf-8")
    link = tmp_path / "linked.service"
    link.symlink_to(target)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == ["systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{link}\n", stderr="")
        if argv == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote, "run", fake_run)

    with pytest.raises(remote.HelperError) as exc_info:
        remote._unit_state(remote.ACPX_UNIT)

    message = str(exc_info.value)
    assert "symlink" in message
    assert str(link) not in message
    assert str(target) not in message


def test_unit_state_rejects_fragment_path_swapped_after_lstat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = tmp_path / "acpx.service"
    unit.write_text("[Service]\nExecStart=ok\n", encoding="utf-8")
    real_open = remote.os.open

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == ["systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{unit}\n", stderr="")
        if argv == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    def swapping_open(path: str | bytes | int, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        del path, flags, mode
        assert dir_fd is None
        unit.unlink()
        unit.mkdir()
        return real_open(unit, remote.os.O_RDONLY)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote.os, "open", swapping_open)

    with pytest.raises(remote.HelperError) as exc_info:
        remote._unit_state(remote.ACPX_UNIT)

    assert "changed before read" in str(exc_info.value)


def test_unit_state_rejects_fragment_path_swapped_to_different_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = tmp_path / "acpx.service"
    replacement = tmp_path / "replacement.service"
    unit.write_text("[Service]\nExecStart=original\n", encoding="utf-8")
    replacement.write_text("[Service]\nExecStart=replacement\n", encoding="utf-8")
    real_open = remote.os.open

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == ["systemctl", "--user", "show", remote.ACPX_UNIT, "-p", "FragmentPath", "--value"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{unit}\n", stderr="")
        if argv == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    def swapping_open(path: str | bytes | int, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        assert dir_fd is None
        unit.unlink()
        replacement.rename(unit)
        return real_open(path, flags, mode)

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote.os, "open", swapping_open)

    with pytest.raises(remote.HelperError) as exc_info:
        remote._unit_state(remote.ACPX_UNIT)

    message = str(exc_info.value)
    assert message == f"unit fragment changed before read: {remote.ACPX_UNIT}"
    assert str(unit) not in message
    assert str(replacement) not in message


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
        "node-runtime/node_modules/acpx/dist/cli.js",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        target.chmod(0o700)
    capability = root / "capability"
    capability.mkdir(parents=True, mode=0o700, exist_ok=True)
    policy = capability / "permission-policy.json"
    policy.write_text('{"defaultAction":"deny"}\n', encoding="utf-8")
    policy.chmod(0o600)
    mcp = capability / "mcp-config.json"
    mcp.write_text(
        json.dumps({"mcpServers": [{"name": "cloakbrowser", "command": str(release_source / "scripts" / "cbm-mcp"), "args": []}]}),
        encoding="utf-8",
    )
    mcp.chmod(0o600)
    key = root / "candidate.worker.key"
    key.write_text("cbm_worker_" + ("1" * 64) + "\n", encoding="utf-8")
    key.chmod(0o600)
    unit = root / "acpx-candidate-release-0000001.service"
    unit.write_text(
        f"ExecStart={root}/venv/bin/python --manager-url http://127.0.0.1:18116 "
        f"--token-file {key} --worktree {release_source} "
        f"--permission-policy {policy} --mcp-config {mcp} --capability-dir {capability} "
        f"--acpx {root / 'node-runtime' / 'node_modules' / 'acpx' / 'dist' / 'cli.js'} "
        "--preflight-interval 30\n",
        encoding="utf-8",
    )
    unit.chmod(0o600)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={release_source}\nActiveState=active\nFragmentPath=/unit\n",
                stderr="",
            )
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
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: (
            {"worker_seen_recently": True, "state": "polling"}
            if path.endswith("/presence")
            else {
                "agents": [
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        ),
        raising=False,
    )

    result = remote.op_bootstrap_acpx_promote(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18115,
            "release_source": str(release_source),
            "acpx_executable": str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
        }
    )
    remote.op_bootstrap_acpx_cleanup({"release_id": "release-0000001"})

    permanent_unit = paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service"
    unit_text = permanent_unit.read_text(encoding="utf-8")
    assert result["active"] is True
    assert "acpx-bootstrap" not in unit_text
    assert "127.0.0.1:18115" in unit_text
    assert " --preflight-interval 240" in unit_text
    assert " --preflight-interval 30" not in unit_text
    assert f"--worktree {release_source}" in unit_text
    durable_capability = paths["releases"] / "release-0000001" / "acpx-capability"
    assert f"--permission-policy {durable_capability / 'permission-policy.json'}" in unit_text
    assert f"--mcp-config {durable_capability / 'mcp-config.json'}" in unit_text
    assert f"--capability-dir {durable_capability}" in unit_text
    assert json.loads((durable_capability / "permission-policy.json").read_text(encoding="utf-8")) == {"defaultAction": "deny"}
    assert json.loads((durable_capability / "mcp-config.json").read_text(encoding="utf-8"))["mcpServers"][0]["command"] == str(
        release_source / "scripts" / "cbm-mcp"
    )
    assert (paths["releases"] / "release-0000001" / "acpx-runtime").exists()
    assert version_checks == [str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js")]
    assert not root.exists()


def test_bootstrap_promote_preserves_candidate_allowlisted_path_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    source_key = paths["home"] / ".config" / "cloakbrowser" / "browser-use-worker-key"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("cbm_worker_" + ("ab" * 32) + "\n", encoding="utf-8")
    source_key.chmod(0o600)
    monkeypatch.setattr(remote, "BROWSER_USE_TOKEN_PATH", source_key)

    remote.op_bootstrap_acpx_provision_candidate(
        {
            "release_id": "release-0000001",
            "commit": "0" * 40,
            "manager_port": 18116,
            "runtime": {},
        }
    )
    root = remote.acpx_bootstrap_dir("release-0000001")
    for relative in ("venv/bin/python", "node-runtime/node_modules/acpx/dist/cli.js"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        target.chmod(0o700)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={release_source}\nActiveState=active\nFragmentPath=/unit\n",
                stderr="",
            )
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_manager_json_on_port",
        lambda port, path, timeout=None: (
            {"worker_seen_recently": True, "state": "polling"}
            if path.endswith("/presence")
            else {
                "agents": [
                    {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        ),
        raising=False,
    )
    result = remote.op_bootstrap_acpx_promote(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18115,
            "release_source": str(release_source),
            "acpx_executable": str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
        }
    )

    assert result["active"] is True
    permanent_unit = paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service"
    unit_text = permanent_unit.read_text(encoding="utf-8")
    assert systemd_environment_lines(unit_text) == [f"Environment=PATH={EXPECTED_ACPX_SYSTEMD_PATH}"]
    assert "/tmp" not in unit_text
    assert "GH_TOKEN" not in unit_text
    assert "OPENAI_API_KEY" not in unit_text


def test_bootstrap_promote_polls_live_readiness_until_bound_presence_and_preflights_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    candidate["unit"].write_text(
        candidate["unit"].read_text(encoding="utf-8").rstrip("\n") + " --preflight-interval 30\n",
        encoding="utf-8",
    )
    clock = {"now": 0.0}
    secret = "cbm_worker_" + ("1" * 64)
    sleeps: list[float] = []
    systemctl_checks = {"active": 0}
    show_states = [
        {"WorkingDirectory": "/home/coder/old-source", "ActiveState": "active"},
        {"WorkingDirectory": str(candidate["source"]), "ActiveState": "active"},
    ]
    presence_states = [
        {"worker_seen_recently": False, "state": "starting"},
        {"worker_seen_recently": True, "state": "polling"},
    ]

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") is None or float(kwargs["timeout"]) <= 90.0
        if argv[:3] == ["systemctl", "--user", "show"]:
            state = show_states.pop(0) if show_states else {"WorkingDirectory": str(candidate["source"]), "ActiveState": "active"}
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={state['WorkingDirectory']}\nActiveState={state['ActiveState']}\nFragmentPath=/unit\n",
                stderr="",
            )
        if argv[:4] == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            systemctl_checks["active"] += 1
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:3] == ["systemctl", "--user", "daemon-reload"] or argv[:4] == ["systemctl", "--user", "restart", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert port == 18115
        assert timeout is not None and 0 < timeout <= 5.0
        if path.endswith("/presence"):
            return {"harness": "acpx", "last_seen_at": "2026-07-27T12:00:00Z", "token": secret, "unknown": "raw", **presence_states.pop(0)}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": "codex", "ready": True, "state": "ready", "reason_code": "ok"},
                    *[
                        {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required"}
                        for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                        if agent != "codex"
                    ],
                ]
            }
        raise AssertionError(path)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(remote, "ACPX_PROMOTED_READINESS_TIMEOUT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", fake_sleep)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_promote(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18115,
            "release_source": str(candidate["source"]),
            "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
        }
    )

    assert result["active"] is True
    assert result["adapters_ready"] is True
    assert result["bound"] is True
    assert result["preflights"] is True
    assert result["readiness_reason"] == "ready"
    assert result["components"]["binding"]["ready"] is True
    assert result["components"]["presence"]["ready"] is True
    assert result["presence"] == {
        "harness": "acpx",
        "worker_seen_recently": True,
        "state": "polling",
        "last_seen_at": "2026-07-27T12_00_00Z",
    }
    assert result["components"]["manager_preflights"]["auth_blocked_agents"] == [
        "claude",
        "cursor",
        "grok-build",
        "opencode",
    ]
    assert result["attempt_count"] == 3
    assert sleeps == [0.1, 0.1]
    assert systemctl_checks["active"] >= 2
    receipt_json = json.dumps(result, sort_keys=True)
    assert secret not in receipt_json
    assert "token" not in receipt_json
    assert "unknown" not in receipt_json


def test_bootstrap_promote_default_readiness_allows_candidate_scale_first_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    candidate["unit"].write_text(
        candidate["unit"].read_text(encoding="utf-8").rstrip("\n") + " --preflight-interval 30\n",
        encoding="utf-8",
    )
    clock = {"now": 0.0}
    sleeps: list[float] = []
    preflight_attempts = {"count": 0}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("timeout") is None or 0 < float(kwargs["timeout"]) <= 180.0
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={candidate['source']}\nActiveState=active\nFragmentPath=/unit\n",
                stderr="",
            )
        if argv[:4] == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:3] == ["systemctl", "--user", "daemon-reload"] or argv[:4] == ["systemctl", "--user", "restart", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert port == 18115
        assert timeout is not None and 0 < timeout <= 5.0
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling"}
        if path.endswith("/preflights"):
            preflight_attempts["count"] += 1
            ready = clock["now"] >= 124.0
            return {
                "agents": [
                    {
                        "agent": agent,
                        "ready": ready,
                        "state": "ready" if ready else "starting",
                        "reason_code": "ok" if ready else "pending_first_preflight",
                    }
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        raise AssertionError(path)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", fake_sleep)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_bootstrap_acpx_promote(
        {
            "release_id": "release-0000001",
            "worker_id": "acpx-candidate-release-0000001",
            "manager_port": 18115,
            "release_source": str(candidate["source"]),
            "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
        }
    )

    assert result["preflights"] is True
    assert result["readiness_reason"] == "ready"
    assert result["deadline_seconds"] == 180.0
    assert result["elapsed_seconds"] == 124.0
    assert preflight_attempts["count"] > 45
    assert sleeps and set(sleeps) == {2.0}
    assert [agent["agent"] for agent in result["agent_preflights"]] == list(remote.EXPECTED_ACPX_PREFLIGHT_AGENTS)


def test_bootstrap_promote_times_out_with_sanitized_live_readiness_reason_without_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    candidate["unit"].write_text(
        candidate["unit"].read_text(encoding="utf-8").rstrip("\n") + " --preflight-interval 30\n",
        encoding="utf-8",
    )
    clock = {"now": 0.0}
    secret = "cbm_worker_" + ("1" * 64)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["systemctl", "--user", "show"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"WorkingDirectory={candidate['source']}\nActiveState=active\nFragmentPath=/unit\n",
                stderr="",
            )
        if argv[:4] == ["systemctl", "--user", "is-active", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        if argv[:3] == ["systemctl", "--user", "daemon-reload"] or argv[:4] == ["systemctl", "--user", "restart", remote.ACPX_UNIT]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(argv)

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        assert port == 18115
        if path.endswith("/presence"):
            return {"worker_seen_recently": True, "state": "polling", "token": secret}
        if path.endswith("/preflights"):
            return {
                "agents": [
                    {"agent": agent, "ready": False, "state": "failed", "reason_code": "auth_required", "token": secret}
                    for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(remote, "ACPX_PROMOTED_READINESS_TIMEOUT_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(remote, "CANDIDATE_READINESS_POLL_INTERVAL_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    with pytest.raises(
        remote.HelperError,
        match=r"ACPX promoted readiness failed: attempt_count=2 .*deadline_seconds=0\.15 .*reason=manager_preflight_no_ready_agent",
    ) as exc_info:
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    message = str(exc_info.value)
    assert secret not in message
    assert "token" not in message
    assert "agents" not in message


def test_bootstrap_promote_rejects_candidate_missing_bounded_preflight_interval_before_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    restart_calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "restart", "cloakbrowser-acpx.service"]:
            restart_calls.append(tuple(argv))
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    with pytest.raises(remote.HelperError, match="preflight interval"):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    assert restart_calls == []
    assert not (paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service").exists()
    assert candidate["root"].exists()


def test_bootstrap_promote_rejects_candidate_preflight_interval_token_prefix_before_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    candidate = prepare_complete_bootstrap_candidate(paths)
    candidate["unit"].write_text(
        candidate["unit"].read_text(encoding="utf-8").rstrip("\n") + " --preflight-interval 300\n",
        encoding="utf-8",
    )
    restart_calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[:4] == ["systemctl", "--user", "restart", "cloakbrowser-acpx.service"]:
            restart_calls.append(tuple(argv))
        if argv[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(remote, "run", fake_run)

    with pytest.raises(remote.HelperError, match="preflight interval"):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(candidate["source"]),
                "acpx_executable": str(candidate["release"] / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )

    assert restart_calls == []
    assert not (paths["home"] / ".config" / "systemd" / "user" / "cloakbrowser-acpx.service").exists()
    assert candidate["root"].exists()


@pytest.mark.parametrize("source_kind", ["missing", "symlink", "foreign"])
def test_bootstrap_promote_rejects_missing_symlink_or_foreign_worktree_before_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_kind: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    root = remote.acpx_bootstrap_dir("release-0000001")
    for relative in (
        "venv/bin/python",
        "node-runtime/node_modules/acpx/dist/cli.js",
        "capability/capability.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        target.chmod(0o700)
    key = root / "candidate.worker.key"
    key.write_text("cbm_worker_" + ("1" * 64) + "\n", encoding="utf-8")
    candidate_unit = root / "acpx-candidate-release-0000001.service"
    candidate_unit.write_text("ExecStart=placeholder\n", encoding="utf-8")
    expected_source = paths["releases"] / "release-0000001" / "source"
    if source_kind == "symlink":
        target = tmp_path / "outside-worktree"
        target.mkdir()
        expected_source.symlink_to(target)
        release_source = expected_source
        match = "worktree"
    elif source_kind == "foreign":
        release_source = tmp_path / "foreign"
        release_source.mkdir()
        match = "source mismatch"
    else:
        release_source = expected_source
        match = "worktree"

    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr=""))

    with pytest.raises(remote.HelperError, match=match):
        remote.op_bootstrap_acpx_promote(
            {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": 18115,
                "release_source": str(release_source),
                "acpx_executable": str(paths["releases"] / "release-0000001" / "acpx-runtime" / "node_modules" / "acpx" / "dist" / "cli.js"),
            }
        )


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
    monkeypatch.setattr(remote, "_validate_manager_bind_sources", lambda: None)
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


def test_verify_acpx_still_uses_promoted_release_executable_when_global_acpx_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    acpx_executable = write_release_acpx_target(paths, promoted=True)
    release_venv = paths["releases"] / "release-0000001" / "acpx-venv"
    release_venv.mkdir()
    version_calls: list[list[str]] = []
    manager_calls: list[tuple[int, str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv[-1:] == ["--version"]:
            version_calls.append([str(item) for item in argv])
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    def fake_manager_json(port: int, path: str, *, timeout: float | None = None) -> dict[str, object]:
        del timeout
        manager_calls.append((port, path))
        return {
            "agents": [
                {"agent": agent, "ready": True, "state": "ready", "reason_code": "ok"}
                for agent in remote.EXPECTED_ACPX_PREFLIGHT_AGENTS
            ]
        }

    monkeypatch.setattr(remote.shutil, "which", lambda name: None if name == "acpx" else None)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_unit_state", lambda unit: {"active_state": "active", "unit_sha256": "2" * 64})
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(release_source), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")
    monkeypatch.setattr(remote, "_manager_json_on_port", fake_manager_json, raising=False)

    result = remote.op_verify_acpx(
        {
            "release_source": str(release_source),
            "expected_absent": False,
            "acpx_executable": str(acpx_executable),
        }
    )

    assert result["active"] is True
    assert result["bound"] is True
    assert result["preflights"] is True
    assert result["adapters_ready"] is True
    assert result["adapter_reason_code"] == "ok"
    assert result["venv"] is True
    assert version_calls == [[str(acpx_executable), "--version"]]
    assert manager_calls == [(18115, "/api/task-harnesses/acpx/preflights")]


@pytest.mark.parametrize(
    ("venv_kind", "match"),
    [("missing", "release venv"), ("symlink", "release venv")],
)
def test_preflight_acpx_requires_promoted_release_venv_before_global_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    venv_kind: str,
    match: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    write_release_acpx_target(paths, promoted=True)
    release_venv = paths["releases"] / "release-0000001" / "acpx-venv"
    if venv_kind == "symlink":
        outside = tmp_path / "outside-venv"
        outside.mkdir()
        release_venv.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: str(tmp_path / "global-acpx") if name == "acpx" else None,
    )
    monkeypatch.setattr(
        remote,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError("global ACPX probe must not run")),
    )
    monkeypatch.setattr(remote, "_unit_state", lambda unit: {"active_state": "active", "unit_sha256": "2" * 64})
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(release_source), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    with pytest.raises(remote.HelperError, match=match):
        remote.op_preflight_acpx({})


@pytest.mark.parametrize("active_state", ["inactive", "failed"])
def test_preflight_acpx_release_working_directory_uses_release_runtime_when_unit_not_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    active_state: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    acpx_executable = write_release_acpx_target(paths, promoted=True)
    release_venv = paths["releases"] / "release-0000001" / "acpx-venv"
    release_venv.mkdir()
    version_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == [str(acpx_executable), "--version"]:
            version_calls.append([str(item) for item in argv])
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: str(tmp_path / "global-acpx") if name == "acpx" else None,
    )
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(
        remote,
        "_unit_state",
        lambda unit: {"active_state": active_state, "unit_sha256": "2" * 64},
    )
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(release_source), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    result = remote.op_preflight_acpx({})

    assert result["active"] is False
    assert result["release_id"] == "release-0000001"
    assert result["adapters_ready"] is True
    assert result["adapter_reason_code"] == "ok"
    assert result["venv"] is True
    assert result["acpx_executable"] == str(acpx_executable)
    assert version_calls == [[str(acpx_executable), "--version"]]


def test_preflight_acpx_inactive_release_working_directory_missing_venv_fails_closed_before_global_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    write_release_acpx_target(paths, promoted=True)

    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: str(tmp_path / "global-acpx") if name == "acpx" else None,
    )
    monkeypatch.setattr(
        remote,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError("global ACPX probe must not run")),
    )
    monkeypatch.setattr(
        remote,
        "_unit_state",
        lambda unit: {"active_state": "inactive", "unit_sha256": "2" * 64},
    )
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(release_source), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    with pytest.raises(remote.HelperError, match="release venv"):
        remote.op_preflight_acpx({})


@pytest.mark.parametrize(
    ("path_kind", "match"),
    [
        ("wrong_release", "allowlisted"),
        ("global", "allowlisted"),
        ("symlink", "symlink|non-symlink"),
    ],
)
def test_verify_acpx_rejects_non_release_or_symlink_executable_before_manager_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_kind: str,
    match: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    expected_acpx = write_release_acpx_target(paths, promoted=True)
    if path_kind == "wrong_release":
        acpx_executable = write_release_acpx_target(paths, "release-0000002", promoted=True)
    elif path_kind == "global":
        acpx_executable = tmp_path / "usr-local-bin-acpx"
        acpx_executable.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        acpx_executable.chmod(0o700)
    else:
        expected_acpx.unlink()
        symlink_target = tmp_path / "outside-acpx"
        symlink_target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        symlink_target.chmod(0o700)
        expected_acpx.symlink_to(symlink_target)
        acpx_executable = expected_acpx
    manager_calls: list[tuple[int, str]] = []

    monkeypatch.setattr(remote, "_manager_json_on_port", lambda port, path, timeout=None: manager_calls.append((port, path)) or {}, raising=False)
    monkeypatch.setattr(remote, "run", lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError("adapter probe must not run")))

    with pytest.raises(remote.HelperError, match=match):
        remote.op_verify_acpx(
            {
                "release_source": str(release_source),
                "expected_absent": False,
                "acpx_executable": str(acpx_executable),
            }
        )

    assert manager_calls == []


def test_preflight_acpx_uses_promoted_release_runtime_from_unit_working_directory_when_global_acpx_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release_source = paths["releases"] / "release-0000001" / "source"
    release_source.mkdir(parents=True)
    acpx_executable = write_release_acpx_target(paths, promoted=True)
    release_venv = paths["releases"] / "release-0000001" / "acpx-venv"
    release_venv.mkdir()
    version_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == [str(acpx_executable), "--version"]:
            version_calls.append([str(item) for item in argv])
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote.shutil, "which", lambda name: None if name == "acpx" else None)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_unit_state", lambda unit: {"active_state": "active", "unit_sha256": "2" * 64})
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(release_source), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    result = remote.op_preflight_acpx({})

    assert result["active"] is True
    assert result["release_id"] == "release-0000001"
    assert result["adapters_ready"] is True
    assert result["adapter_reason_code"] == "ok"
    assert result["venv"] is True
    assert result["acpx_executable"] == str(acpx_executable)
    assert version_calls == [[str(acpx_executable), "--version"]]


@pytest.mark.parametrize(
    ("path_kind", "match"),
    [
        ("malformed", "release source"),
        ("source_symlink", "non-symlink"),
        ("release_symlink", "symlink"),
    ],
)
def test_preflight_acpx_rejects_unsafe_release_working_directory_before_global_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_kind: str,
    match: str,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
    release = paths["releases"] / "release-0000001"
    if path_kind == "malformed":
        working_directory = release / "not-source"
        working_directory.mkdir(parents=True)
    elif path_kind == "source_symlink":
        release.mkdir(parents=True)
        outside = tmp_path / "outside-source"
        outside.mkdir()
        working_directory = release / "source"
        working_directory.symlink_to(outside, target_is_directory=True)
    else:
        sibling = paths["releases"] / "release-sibling-0001"
        (sibling / "source").mkdir(parents=True)
        release.symlink_to(sibling, target_is_directory=True)
        working_directory = release / "source"

    monkeypatch.setattr(
        remote.shutil,
        "which",
        lambda name: str(tmp_path / "global-acpx") if name == "acpx" else None,
    )
    monkeypatch.setattr(
        remote,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError("global ACPX probe must not run")),
    )
    monkeypatch.setattr(remote, "_unit_state", lambda unit: {"active_state": "active", "unit_sha256": "2" * 64})
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": str(working_directory), "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    with pytest.raises(remote.HelperError, match=match):
        remote.op_preflight_acpx({})


def test_preflight_acpx_legacy_capture_still_uses_global_path_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_acpx = tmp_path / "acpx"
    global_acpx.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    global_acpx.chmod(0o700)
    version_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if argv == [str(global_acpx), "--version"]:
            version_calls.append([str(item) for item in argv])
            return subprocess.CompletedProcess(argv, 0, stdout="0.12.1\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(remote.shutil, "which", lambda name: str(global_acpx) if name == "acpx" else None)
    monkeypatch.setattr(remote, "run", fake_run)
    monkeypatch.setattr(remote, "_unit_state", lambda unit: {"active_state": "active", "unit_sha256": "2" * 64})
    monkeypatch.setattr(
        remote,
        "_unit_show",
        lambda unit: {"WorkingDirectory": "/home/coder/legacy-acpx", "FragmentPath": "/unit"},
    )
    monkeypatch.setattr(remote, "_token_mode", lambda path: "600")

    result = remote.op_preflight_acpx({})

    assert result["adapters_ready"] is True
    assert result["adapter_reason_code"] == "ok"
    assert version_calls == [[str(global_acpx), "--version"]]


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
