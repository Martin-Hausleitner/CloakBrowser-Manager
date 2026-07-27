#!/usr/bin/env python3
"""Behavior tests for the CBM-022 VCVM release transaction engine."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vcvm_release_transaction.py"
AUTHORIZED_FORK = "https://github.com/example/fork.git"
UPSTREAM = "https://github.com/example/upstream.git"
SECRET_TOKEN = "cbm_agent_" + ("a1" * 24)
FULL_WORKER_COMMIT = "50a9e43000000000000000000000000000000000"

SPEC = importlib.util.spec_from_file_location("vcvm_release_transaction", SCRIPT)
assert SPEC and SPEC.loader
tx = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tx
SPEC.loader.exec_module(tx)

REMOTE_SCRIPT = ROOT / "scripts" / "vcvm_release_remote.py"
REMOTE_SPEC = importlib.util.spec_from_file_location("vcvm_release_remote_for_tx", REMOTE_SCRIPT)
assert REMOTE_SPEC and REMOTE_SPEC.loader
remote_mod = importlib.util.module_from_spec(REMOTE_SPEC)
sys.modules[REMOTE_SPEC.name] = remote_mod
REMOTE_SPEC.loader.exec_module(remote_mod)


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "backend").mkdir()
    (repo / "frontend").mkdir()
    (repo / "scripts").mkdir()
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "docker-compose.vcvm.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "backend" / "database.py").write_text('migration_version = "agent_workspace_v1"\n', encoding="utf-8")
    (repo / "frontend" / "index.html").write_text("<main></main>\n", encoding="utf-8")
    (repo / "scripts" / "deploy_vcvm.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    run("git", "init", cwd=repo)
    run("git", "checkout", "-b", "release/test", cwd=repo)
    run("git", "config", "user.email", "unit@example.invalid", cwd=repo)
    run("git", "config", "user.name", "Unit Test", cwd=repo)
    run("git", "remote", "add", "origin", UPSTREAM, cwd=repo)
    run("git", "remote", "add", "fork", AUTHORIZED_FORK, cwd=repo)
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "fixture", cwd=repo)
    return repo


def release_config(repo: Path, **overrides: object):
    payload = {
        "source_root": repo,
        "release_id": "release-20260727-ac5840b00001",
        "expected_source_remote": AUTHORIZED_FORK,
        "source_remote": "fork",
        "host": "vcvm",
        "remote_path": "/home/coder/cloakbrowser-manager",
        "apply": True,
        "expected_current_worker_commit": FULL_WORKER_COMMIT,
    }
    payload.update(overrides)
    return tx.ReleaseConfig(**payload)


class FakeRemoteExecutor:
    def __init__(self, **facts: object) -> None:
        self.calls: list[dict[str, object]] = []
        self.uploads: list[tuple[str, str]] = []
        self.live_generation = "old"
        self.candidate_removed = False
        self.fail_phase = str(facts.pop("fail_phase", ""))
        self.rollback_verify_fails = bool(facts.pop("rollback_verify_fails", False))
        self.facts = {
            "disk_free_bytes": 9 * 1024**3,
            "helper_version": "vcvm-release-helper-v1",
            "helper_operations": ["preflight.disk", "release.extract", "state.commit"],
            "marker_owner": "coder",
            "commands_present": True,
            "env_mode": "600",
            "manager_container": "cloakbrowser-manager-vcvm",
            "manager_image": "cloakbrowser-manager:old",
            "manager_image_digest": "d" * 64,
            "manager_image_revision": "1" * 40,
            "manager_volume": "cloakbrowser-manager-vcvm-data",
            "browser_use_active": True,
            "browser_use_token_mode": "600",
            "browser_use_commit": FULL_WORKER_COMMIT,
            "browser_use_unit_path": "/home/coder/.config/systemd/user/browser-use.service",
            "browser_use_unit_sha256": "1" * 64,
            "acpx_active": True,
            "acpx_token_mode": "600",
            "acpx_venv": True,
            "acpx_adapters_ready": True,
            "acpx_unit_path": "/home/coder/.config/systemd/user/acpx.service",
            "acpx_unit_sha256": "2" * 64,
            "service_receipts": True,
            "tailscale_route": True,
            "release_exists": False,
            "remote_archive_sha256": None,
            "candidate_health": True,
            "candidate_auth_required": True,
            "candidate_access_control_enabled": True,
            "candidate_migrations": [
                "agent_workspace_v1",
                "task_runs_v1",
                "worker_runtime_v1",
                "task_runs_acpx_v1",
                "worker_harness_presence_v1",
                "worker_harness_preflights_v1",
                "task_run_binding_v1",
            ],
            "candidate_revision": None,
            "live_health": True,
            "live_auth": True,
            "browser_use_bound": True,
            "acpx_preflights": True,
            "proxychecker": True,
            "stream": True,
            "orca_status": "ready",
            "state_previous_release": "release-previous-0001",
            "state_backup_receipt": "backup-final-old",
            "state_previous_runtime_revision": "0" * 40,
            "backup_compatible": True,
        }
        self.facts.update(facts)

    @property
    def phases(self) -> list[str]:
        return [str(call["phase"]) for call in self.calls]

    @property
    def mutated_phases(self) -> list[str]:
        return [str(call["phase"]) for call in self.calls if call["mutation"]]

    def upload(self, local_path: Path, remote_path: str, *, phase: str) -> None:
        self._maybe_fail(phase)
        self.uploads.append((local_path.name, remote_path))
        self.calls.append({"phase": phase, "argv": ["upload", local_path.name, remote_path], "mutation": True})

    def run_json(self, request: dict[str, object], *, phase: str, mutation: bool = False) -> dict[str, object]:
        assert request["operation"] == phase
        args = dict(request["args"])
        self.calls.append({"phase": phase, "argv": [json.dumps(request, sort_keys=True)], "mutation": mutation})
        self._maybe_fail(phase)
        if phase == "helper.capabilities":
            return {"helper_version": self.facts["helper_version"], "operations": self.facts["helper_operations"]}
        if phase == "preflight.disk":
            return {"free_bytes": self.facts["disk_free_bytes"]}
        if phase == "preflight.marker":
            return {"owner": self.facts["marker_owner"]}
        if phase == "preflight.commands":
            return {"ok": self.facts["commands_present"]}
        if phase == "preflight.env":
            return {"mode": self.facts["env_mode"]}
        if phase == "preflight.manager":
            return {
                "container": self.facts["manager_container"],
                "image": self.facts["manager_image"],
                "image_digest": self.facts["manager_image_digest"],
                "revision": self.facts["manager_image_revision"],
                "volume": self.facts["manager_volume"],
            }
        if phase == "preflight.browser_use":
            return {
                "active": self.facts["browser_use_active"],
                "token_mode": self.facts["browser_use_token_mode"],
                "commit": self.facts["browser_use_commit"],
                "unit_path": self.facts["browser_use_unit_path"],
                "unit_sha256": self.facts["browser_use_unit_sha256"],
                "venv": True,
            }
        if phase == "preflight.acpx":
            return {
                "active": self.facts["acpx_active"],
                "token_mode": self.facts["acpx_token_mode"],
                "venv": self.facts["acpx_venv"],
                "adapters_ready": self.facts["acpx_adapters_ready"],
                "unit_path": self.facts["acpx_unit_path"],
                "unit_sha256": self.facts["acpx_unit_sha256"],
            }
        if phase == "preflight.receipts":
            return {"ok": self.facts["service_receipts"]}
        if phase == "preflight.tailscale":
            return {"ok": self.facts["tailscale_route"]}
        if phase == "release.prepare":
            return {"exists": self.facts["release_exists"]}
        if phase == "release.verify_archive":
            return {"sha256": self.facts["remote_archive_sha256"] or args["archive_sha256"]}
        if phase == "release.extract":
            return {"extracted": True}
        if phase == "release.write_marker":
            return {"marker": args["commit"]}
        if phase == "build.image":
            return {"image": f"cloakbrowser-manager:{args['commit']}", "image_id": f"sha256:{'e' * 64}", "image_digest": "e" * 64, "image_ref": f"sha256:{'e' * 64}", "revision": args["commit"]}
        if phase == "backup.live":
            return {"receipt_id": "backup-live-old", "path": "/home/coder/cloakbrowser-manager/backups/backup-live-old.tar", "sha256": "b" * 64, "volume": "cloakbrowser-manager-vcvm-data", "source_revision": args["commit"], "compatible": True}
        if phase == "candidate.clone":
            return {"volume": "cloakbrowser-manager-vcvm-data-candidate-release"}
        if phase == "candidate.start":
            assert str(args["image_ref"]).startswith("sha256:")
            return {"container": "cbm-candidate-release", "port": 18116}
        if phase == "candidate.verify":
            return {
                "health": self.facts["candidate_health"],
                "auth_required": self.facts["candidate_auth_required"],
                "access_control_enabled": self.facts["candidate_access_control_enabled"],
                "migrations": self.facts["candidate_migrations"],
                "revision": self.facts["candidate_revision"] or args["commit"],
            }
        if phase == "capture.state":
            payload = {
                "source_revision": args["commit"],
                "previous_revision": self.facts["manager_image_revision"],
                "old_image_digest": self.facts["manager_image_digest"],
                "old_image_id": f"sha256:{self.facts['manager_image_digest']}",
                "container_config_receipt": "3" * 64,
                "current_pointer": "release-old",
                "state": {"current_release": "release-old"},
                "browser_use_unit_sha256": self.facts["browser_use_unit_sha256"],
                "browser_use_unit_content": "[Service]\nWorkingDirectory=old\n",
                "browser_use_unit_path": self.facts["browser_use_unit_path"],
                "browser_use_active_state": "active",
                "acpx_unit_sha256": self.facts["acpx_unit_sha256"],
                "acpx_unit_content": "[Service]\nWorkingDirectory=old\n",
                "acpx_unit_path": self.facts["acpx_unit_path"],
                "acpx_active_state": "active",
                "live_volume": "cloakbrowser-manager-vcvm-data",
            }
            payload.update(dict(self.facts.get("capture_extra", {})))
            return payload
        if phase == "quiesce.stop_workers":
            return {"stopped": True}
        if phase == "quiesce.stop_live":
            self.live_generation = "stopped"
            return {"stopped": True}
        if phase == "backup.final_stopped":
            return {"receipt_id": "backup-final-old", "path": "/home/coder/cloakbrowser-manager/backups/backup-final-old.tar", "sha256": "c" * 64, "volume": "cloakbrowser-manager-vcvm-data", "source_revision": args["commit"], "compatible": True}
        if phase == "live.start":
            assert str(args["image_ref"]).startswith("sha256:")
            self.live_generation = "new"
            return {"container": "cloakbrowser-manager-vcvm", "port": 18115}
        if phase == "workers.rebind":
            release_source = f"/home/coder/cloakbrowser-manager/releases/{args['release_id']}/source"
            return {
                "release_source": release_source,
                "commit_marker": args["commit"],
                "browser_use": {"token_mode": "600", "unit_sha256": "4" * 64, "active_state": "active", "commit": args["commit"]},
                "acpx": {"token_mode": "600", "venv": True, "unit_sha256": "5" * 64, "active_state": "active", "preflights": True},
            }
        if phase == "verify.manager":
            return {"health": self.facts["live_health"], "auth": self.facts["live_auth"], "revision": args.get("commit")}
        if phase == "verify.browser_use":
            return {"active": True, "bound": self.facts["browser_use_bound"]}
        if phase == "verify.acpx":
            return {"active": True, "preflights": self.facts["acpx_preflights"]}
        if phase == "verify.proxychecker":
            return {"ok": self.facts["proxychecker"]}
        if phase == "verify.stream":
            return {"ok": self.facts["stream"]}
        if phase == "verify.orca":
            return {"status": self.facts["orca_status"]}
        if phase == "verify.tailscale":
            return {"ok": self.facts["tailscale_route"]}
        if phase == "state.commit":
            return {"current": args["current_release"], **args}
        if phase == "candidate.cleanup":
            self.candidate_removed = True
            return {"removed": "candidate-only"}
        if phase == "restore.runtime":
            assert args["backup"]["receipt_id"] in {"backup-final-old", "backup-live-old"}
            assert "old_image_digest" in args["capture"]
            self.live_generation = "old"
            return {"restored": True}
        if phase == "restore.verify":
            restore_payload = args
            return {
                "health": not self.rollback_verify_fails,
                "auth": not self.rollback_verify_fails,
                "backup_receipt_id": restore_payload["backup"]["receipt_id"],
                "old_image_digest": self.facts["manager_image_digest"],
                "pointer": "release-old",
                "browser_use_unit_sha256": self.facts["browser_use_unit_sha256"],
                "browser_use_active_state": "active",
                "acpx_unit_sha256": self.facts["acpx_unit_sha256"],
                "acpx_active_state": "active",
            }
        if phase == "rollback.read_state":
            return {
                "current_release": "release-current-0001",
                "previous_release": self.facts["state_previous_release"],
                "final_backup": {
                    "receipt_id": self.facts["state_backup_receipt"],
                    "path": "/home/coder/cloakbrowser-manager/backups/backup-final-old.tar",
                    "sha256": "c" * 64,
                    "volume": "cloakbrowser-manager-vcvm-data",
                    "source_revision": "0" * 40,
                    "compatible": True,
                },
                "image": {"image_ref": "sha256:" + ("e" * 64), "image_id": "sha256:" + ("e" * 64), "image_digest": "e" * 64, "revision": "0" * 40},
                "capture": {"old_image_digest": "d" * 64, "browser_use_unit_sha256": "1" * 64, "acpx_unit_sha256": "2" * 64, "live_volume": "cloakbrowser-manager-vcvm-data"},
                "previous_runtime": {"image_ref": "sha256:" + ("d" * 64), "image_id": "sha256:" + ("d" * 64), "image_digest": "d" * 64, "revision": self.facts["state_previous_runtime_revision"]},
            }
        if phase == "rollback.verify_backup":
            return {"compatible": self.facts["backup_compatible"]}
        if phase == "rollback.verify_previous":
            previous_runtime = args["previous_runtime"]
            return {
                "image_ref": previous_runtime["image_ref"],
                "image_digest": previous_runtime["image_digest"],
                "revision": previous_runtime["revision"],
            }
        if phase == "rollback.start_previous":
            self.live_generation = "old"
            return {"container": "cloakbrowser-manager-vcvm"}
        raise AssertionError(f"unexpected phase {phase}")

    def _maybe_fail(self, phase: str) -> None:
        if phase == self.fail_phase:
            raise tx.TransactionError(f"synthetic failure at {phase}", phase=phase)


def test_successful_release_runs_exact_order_and_emits_secret_safe_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()
    receipt = tx.run_release(release_config(repo), fake)

    assert fake.phases == [
        "helper.capabilities",
        "preflight.disk",
        "preflight.marker",
        "preflight.commands",
        "preflight.env",
        "preflight.manager",
        "preflight.browser_use",
        "preflight.acpx",
        "preflight.receipts",
        "preflight.tailscale",
        "release.prepare",
        "release.upload_archive",
        "release.upload_manifest",
        "release.verify_archive",
        "release.extract",
        "release.write_marker",
        "build.image",
        "backup.live",
        "candidate.clone",
        "candidate.start",
        "candidate.verify",
        "capture.state",
        "quiesce.stop_workers",
        "quiesce.stop_live",
        "backup.final_stopped",
        "live.start",
        "workers.rebind",
        "verify.manager",
        "verify.browser_use",
        "verify.acpx",
        "verify.proxychecker",
        "verify.stream",
        "verify.orca",
        "verify.tailscale",
        "state.commit",
        "candidate.cleanup",
    ]
    preflight_end = fake.phases.index("preflight.tailscale")
    assert not any(call["mutation"] for call in fake.calls[: preflight_end + 1])
    assert receipt["status"] == "success"
    assert receipt["release_id"] == "release-20260727-ac5840b00001"
    assert receipt["source"]["archive_sha256"]
    assert receipt["image"]["image_digest"] == "e" * 64
    assert receipt["backup"]["pre_candidate"]["sha256"] == "b" * 64
    assert receipt["backup"]["final_stopped"]["sha256"] == "c" * 64
    assert receipt["source"]["commit_marker"] == receipt["source"]["commit"]
    assert SECRET_TOKEN not in json.dumps(receipt)
    assert not capsys.readouterr().out
    all_argv = "\n".join(" ".join(map(str, call["argv"])) for call in fake.calls)
    assert "prune" not in all_argv
    assert "token" not in all_argv.lower()


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ({"disk_free_bytes": 7 * 1024**3}, "less than 8 GiB"),
        ({"marker_owner": "root"}, "marker ownership"),
        ({"commands_present": False}, "required remote command"),
        ({"env_mode": "644"}, "mode 600"),
        ({"manager_volume": "other-volume"}, "Manager data volume"),
        ({"browser_use_token_mode": "644"}, "Browser Use token"),
        ({"acpx_active": False}, "ACPX"),
        ({"acpx_token_mode": "644"}, "ACPX token"),
        ({"acpx_venv": False}, "ACPX venv"),
        ({"service_receipts": False}, "service receipt"),
        ({"tailscale_route": False}, "Tailscale"),
        ({"helper_version": "old-helper"}, "helper capability"),
        ({"browser_use_commit": "different"}, "worker commit skew"),
    ],
)
def test_preflight_failures_stop_before_any_remote_mutation(
    tmp_path: Path,
    facts: dict[str, object],
    message: str,
) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(**facts)
    with pytest.raises(tx.TransactionError, match=message):
        tx.run_release(release_config(repo), fake)
    assert fake.mutated_phases == []
    assert "release.upload_archive" not in fake.phases


@pytest.mark.parametrize("expected_commit", ["", "50a9e43", "g" * 40, "0" * 39])
def test_expected_worker_commit_must_be_full_40_hex_before_preflight_mutation(tmp_path: Path, expected_commit: str) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()
    with pytest.raises(tx.TransactionError, match="expected current worker commit"):
        tx.run_release(release_config(repo, expected_current_worker_commit=expected_commit), fake)
    assert fake.mutated_phases == []


def test_expected_worker_commit_mismatch_is_exact_not_prefix(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(browser_use_commit=FULL_WORKER_COMMIT)
    with pytest.raises(tx.TransactionError, match="worker commit skew"):
        tx.run_release(release_config(repo, expected_current_worker_commit="50a9e43" + ("1" * 33)), fake)
    assert fake.mutated_phases == []


def test_archive_hash_mismatch_fails_before_extraction(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(remote_archive_sha256="0" * 64)
    with pytest.raises(tx.TransactionError, match="archive hash mismatch"):
        tx.run_release(release_config(repo), fake)
    assert "release.extract" not in fake.phases
    assert "build.image" not in fake.phases


@pytest.mark.parametrize("phase", ["candidate.clone", "candidate.start", "candidate.verify"])
def test_candidate_stage_failure_removes_only_candidate_and_preserves_live(tmp_path: Path, phase: str) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(fail_phase=phase)
    with pytest.raises(tx.TransactionError):
        tx.run_release(release_config(repo), fake)
    assert fake.candidate_removed is True
    assert fake.live_generation == "old"
    assert "quiesce.stop_live" not in fake.phases
    assert fake.mutated_phases[-1] == "candidate.cleanup"


def test_candidate_migrations_must_match_exact_required_set(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(candidate_migrations=["agent_workspace_v1"])
    with pytest.raises(tx.TransactionError, match="migration set"):
        tx.run_release(release_config(repo), fake)
    assert fake.candidate_removed is True


@pytest.mark.parametrize(
    "phase",
    [
        "quiesce.stop_workers",
        "quiesce.stop_live",
        "backup.final_stopped",
        "live.start",
        "workers.rebind",
        "verify.manager",
        "verify.browser_use",
        "verify.acpx",
        "verify.proxychecker",
        "verify.stream",
        "verify.orca",
        "verify.tailscale",
        "state.commit",
    ],
)
def test_every_post_quiesce_failure_restores_old_runtime(tmp_path: Path, phase: str) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(fail_phase=phase)
    with pytest.raises(tx.TransactionError, match="release failed after quiesce"):
        tx.run_release(release_config(repo), fake)
    assert "restore.runtime" in fake.phases
    assert "restore.verify" in fake.phases
    assert fake.live_generation == "old"


def test_restore_verification_failure_is_distinct_terminal_failure(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(fail_phase="live.start", rollback_verify_fails=True)
    with pytest.raises(tx.TransactionError, match="rollback verification failed"):
        tx.run_release(release_config(repo), fake)
    assert fake.live_generation == "old"
    assert fake.phases[-1] == "restore.verify"


def test_retry_existing_release_with_matching_archive_is_idempotent(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    archive_path, archive_hash = tx.create_git_archive(repo, tx.git_commit(repo))
    try:
        fake = FakeRemoteExecutor(release_exists=True, remote_archive_sha256=archive_hash)
        receipt = tx.run_release(release_config(repo), fake)
    finally:
        archive_path.unlink(missing_ok=True)
    assert receipt["status"] == "success"
    assert "release.upload_archive" not in fake.phases
    assert "release.extract" not in fake.phases
    assert "release.write_marker" not in fake.phases


def test_release_state_records_old_runtime_revision_distinct_from_new_commit(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    old_revision = "1" * 40
    fake = FakeRemoteExecutor(manager_image_revision=old_revision)

    tx.run_release(release_config(repo), fake)

    state_commit = next(call for call in fake.calls if call["phase"] == "state.commit")
    payload = json.loads(str(state_commit["argv"][0]))["args"]
    assert payload["capture"]["previous_revision"] == old_revision
    assert payload["previous_runtime"]["revision"] == old_revision
    assert payload["previous_runtime"]["revision"] != payload["image"]["revision"]


def test_secret_bearing_capture_state_refuses_before_state_commit(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(capture_extra={"browser_use_dropin_content": f"TOKEN={SECRET_TOKEN}\n"})
    with pytest.raises(tx.TransactionError, match="state payload contains secret"):
        tx.run_release(release_config(repo), fake)
    assert "state.commit" not in fake.phases


def test_existing_release_mismatch_fails_before_extract_or_overwrite(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(release_exists=True, remote_archive_sha256="0" * 64)
    with pytest.raises(tx.TransactionError, match="archive hash mismatch"):
        tx.run_release(release_config(repo), fake)
    assert "release.upload_archive" not in fake.phases
    assert "release.extract" not in fake.phases


def test_rollback_transaction_is_bounded_to_recorded_previous_release(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()
    receipt = tx.run_rollback(
        tx.RollbackConfig(
            source_root=repo,
            host="vcvm",
            remote_path="/home/coder/cloakbrowser-manager",
            target_release="release-previous-0001",
            apply=True,
        ),
        fake,
    )
    assert fake.phases == [
        "rollback.read_state",
        "rollback.verify_backup",
        "rollback.verify_previous",
        "quiesce.stop_workers",
        "quiesce.stop_live",
        "restore.runtime",
        "rollback.start_previous",
        "verify.manager",
        "verify.browser_use",
        "verify.acpx",
        "verify.proxychecker",
        "verify.stream",
        "verify.orca",
        "verify.tailscale",
        "state.commit",
    ]
    assert receipt["status"] == "rolled_back"
    assert receipt["current_release"] == "release-previous-0001"


def test_rollback_verifies_and_starts_recorded_old_revision(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    old_revision = "1" * 40
    fake = FakeRemoteExecutor(state_previous_runtime_revision=old_revision)

    tx.run_rollback(
        tx.RollbackConfig(
            source_root=repo,
            host="vcvm",
            remote_path="/home/coder/cloakbrowser-manager",
            target_release="release-previous-0001",
            apply=True,
        ),
        fake,
    )

    verify_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "rollback.verify_previous")
    start_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "rollback.start_previous")
    assert verify_request["args"]["previous_runtime"]["revision"] == old_revision
    assert start_request["args"]["previous_runtime"]["revision"] == old_revision


def test_rollback_refuses_unrecorded_target_before_mutation(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()
    with pytest.raises(tx.TransactionError, match="not the recorded previous release"):
        tx.run_rollback(
            tx.RollbackConfig(
                source_root=repo,
                host="vcvm",
                remote_path="/home/coder/cloakbrowser-manager",
                target_release="release-other-0001",
                apply=True,
            ),
            fake,
        )
    assert fake.mutated_phases == []


def test_cli_dry_run_is_default_and_apply_is_required_for_execution(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    result = run(
        sys.executable,
        str(SCRIPT),
        "--source-root",
        str(repo),
        "--release-id",
        "release-20260727-ac5840b00001",
        "--expected-source-remote",
        AUTHORIZED_FORK,
        cwd=repo,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry_run"
    assert payload["would_mutate"] is False
    assert result.stderr == ""


def test_actual_release_and_rollback_requests_dispatch_through_remote_helper_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()

    def handler(operation: str):
        def _handle(args: dict[str, object]) -> dict[str, object]:
            return fake.run_json({"operation": operation, "args": args}, phase=operation)

        return _handle

    for operation in remote_mod.OPERATION_SCHEMAS:
        monkeypatch.setitem(remote_mod.OPERATIONS, operation, handler(operation))

    class HelperExecutor:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def run_json(self, request: dict[str, object], *, phase: str, mutation: bool = False) -> dict[str, object]:
            del mutation
            assert request["operation"] == phase
            self.requests.append(request)
            return remote_mod.handle_request(request)

        def upload(self, local_path: Path, remote_path: str, *, phase: str) -> None:
            del local_path, remote_path
            self.requests.append({"operation": phase, "args": {}})

    release_executor = HelperExecutor()
    receipt = tx.run_release(release_config(repo), release_executor)
    assert receipt["status"] == "success"
    assert {request["operation"] for request in release_executor.requests if not request["operation"].startswith("release.upload_")} >= {
        "candidate.start",
        "candidate.verify",
        "live.start",
        "workers.rebind",
        "verify.manager",
        "state.commit",
    }

    rollback_executor = HelperExecutor()
    rollback = tx.run_rollback(
        tx.RollbackConfig(
            source_root=repo,
            host="vcvm",
            remote_path="/home/coder/cloakbrowser-manager",
            target_release="release-previous-0001",
            apply=True,
        ),
        rollback_executor,
    )
    assert rollback["status"] == "rolled_back"
    assert any(request["operation"] == "rollback.start_previous" for request in rollback_executor.requests)
