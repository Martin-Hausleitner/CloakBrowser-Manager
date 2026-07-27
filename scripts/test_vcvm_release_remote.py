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

BROWSER_USE_UNIT = "cloakbrowser-browser-use-worker.service"
BROWSER_USE_TOKEN_PATH = "/home/coder/.config/cloakbrowser/browser-use-worker-key"
IMAGE_ID = "sha256:" + ("d" * 64)
IMAGE_DIGEST = "d" * 64
REVISION = "0" * 40


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


def test_remote_helper_capabilities_are_checked_in_and_versioned() -> None:
    payload = remote.handle_request({"operation": "helper.capabilities", "args": {}})
    assert payload["helper_version"] == remote.HELPER_VERSION
    assert "preflight.disk" in payload["operations"]
    assert "release.extract" in payload["operations"]
    assert "state.commit" in payload["operations"]
    assert "docker.system.prune" not in payload["operations"]


def test_preflight_browser_use_uses_canonical_worker_unit_token_and_working_directory_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(argv))
        if argv[:4] == ["systemctl", "--user", "show", BROWSER_USE_UNIT]:
            if "--value" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="/home/coder/.config/systemd/user/cloakbrowser-browser-use-worker.service\n", stderr="")
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "WorkingDirectory=/home/coder/vk-repos/CloakBrowser-Manager-browser-use\n"
                    "ActiveState=active\n"
                    "FragmentPath=/home/coder/.config/systemd/user/cloakbrowser-browser-use-worker.service\n"
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
    assert result["unit_path"] == "/home/coder/.config/systemd/user/cloakbrowser-browser-use-worker.service"
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


def test_bootstrap_acpx_provision_reuses_existing_canonical_worker_key_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = patch_remote_paths(monkeypatch, tmp_path)
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
    assert candidate_key.read_text(encoding="utf-8") == token + "\n"
    assert stat.S_IMODE(candidate_key.stat().st_mode) == 0o600
    serialized = json.dumps(result, sort_keys=True) + unit.read_text(encoding="utf-8")
    assert token not in serialized
    assert result["credential_reused"] is True
    assert result["credential_source"] == "browser_use_worker_key"


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
