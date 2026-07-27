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


def add_acpx_locks(repo: Path) -> None:
    for relative in (
        "deploy/acpx-runtime/package.json",
        "deploy/acpx-runtime/package-lock.json",
        "scripts/requirements-acpx-worker.in",
        "scripts/requirements-acpx-worker.linux-x86_64.py312.txt",
        "scripts/requirements-acpx-worker.txt",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "add acpx locks", cwd=repo)


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
        self.fail_phases = set(facts.pop("fail_phases", ()))
        self.fail_with_called_process = bool(facts.pop("fail_with_called_process", False))
        self.fail_with_os_error = bool(facts.pop("fail_with_os_error", False))
        self.fail_with_timeout = bool(facts.pop("fail_with_timeout", False))
        self.fail_stderr = str(facts.pop("fail_stderr", "synthetic ssh failure"))
        self.fail_message = str(facts.pop("fail_message", ""))
        self.rollback_verify_fails = bool(facts.pop("rollback_verify_fails", False))
        self.facts = {
            "disk_free_bytes": 9 * 1024**3,
            "helper_version": "vcvm-release-helper-v1",
            "helper_operations": sorted(remote_mod.OPERATION_SCHEMAS),
            "marker_owner": "coder",
            "commands_present": True,
            "env_mode": "600",
            "manager_container": "cloakbrowser-manager-vcvm",
            "manager_image": "cloakbrowser-manager:old",
            "manager_image_digest": "d" * 64,
            "manager_image_id": "sha256:" + ("d" * 64),
            "manager_image_revision": "1" * 40,
            "manager_revision_available": True,
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
            "acpx_bootstrap_state": "absent",
            "acpx_absent_missing": ["binary", "unit", "key", "venv", "capability"],
            "acpx_bootstrap_reason": "missing_runtime",
            "acpx_candidate_ready": True,
            "acpx_promoted_ready": True,
            "bootstrap_cleanup_ok": True,
            "rollback_capture_acpx_absent": False,
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
                "task_artifacts_v1",
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
            "orca_ok": True,
            "orca_status": "ready",
            "orca_runtime_state": "ready",
            "orca_graph_state": "ready",
            "state_previous_release": "release-previous-0001",
            "state_backup_receipt": "backup-final-old",
            "state_previous_runtime_revision": "0" * 40,
            "state_previous_runtime_revision_available": True,
            "rollback_verify_previous_image_ref": None,
            "rollback_verify_previous_image_id": None,
            "rollback_verify_previous_image_digest": None,
            "rollback_verify_previous_revision": None,
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
                "image_id": self.facts["manager_image_id"],
                "image_digest": self.facts["manager_image_digest"],
                "revision": self.facts["manager_image_revision"],
                "revision_available": self.facts["manager_revision_available"],
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
        if phase == "bootstrap.acpx_probe":
            return {
                "state": self.facts["acpx_bootstrap_state"],
                "missing": self.facts["acpx_absent_missing"],
                "reason_code": self.facts["acpx_bootstrap_reason"],
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
        if phase == "bootstrap.acpx_install":
            return {
                "node": {"version": "v22.13.0"},
                "python": {"version": "3.12.10"},
                "acpx": {"version": "0.12.1"},
                "sdk": {"version": "1.2.1"},
                "mcp": {"version": "1.28.1"},
                "playwright": {"version": "1.61.0"},
                "runtime": {
                    "node_root": {"ref": "sha256:" + ("6" * 64), "sha256": "6" * 64, "mode": "700"},
                    "venv": {"ref": "sha256:" + ("7" * 64), "sha256": "7" * 64, "mode": "700"},
                    "acpx_executable": "/home/coder/cloakbrowser-manager/releases/release-20260727-ac5840b00001/acpx-bootstrap/node-runtime/node_modules/acpx/dist/cli.js",
                },
                "lock_digests": {
                    "node": args["node_lock_sha256"],
                    "python": args["python_lock_sha256"],
                },
            }
        if phase == "bootstrap.acpx_provision_candidate":
            assert args["manager_port"] == 18116
            return {
                "worker_id": f"acpx-candidate-{args['release_id']}",
                "manager_url": "http://127.0.0.1:18116",
                "key": {"ref": "sha256:" + ("8" * 64), "sha256": "8" * 64, "mode": "600"},
                "unit": {"ref": "sha256:" + ("9" * 64), "sha256": "9" * 64, "mode": "600"},
                "capability": {"ref": "sha256:" + ("a" * 64), "sha256": "a" * 64, "mode": "700"},
                "venv": {"ref": "sha256:" + ("7" * 64), "sha256": "7" * 64, "mode": "700"},
            }
        if phase == "bootstrap.acpx_start_candidate":
            return {"active": True, "worker_id": args["worker_id"]}
        if phase == "bootstrap.acpx_verify_candidate":
            assert str(args["acpx_executable"]).endswith("/acpx-bootstrap/node-runtime/node_modules/acpx/dist/cli.js")
            return {"worker_id": args["worker_id"], "present": True, "adapters_ready": self.facts["acpx_candidate_ready"]}
        if phase == "capture.state":
            payload = {
                "source_revision": args["commit"],
                "previous_revision": self.facts["manager_image_revision"],
                "previous_revision_available": self.facts["manager_revision_available"],
                "old_image_digest": self.facts["manager_image_digest"],
                "old_image_id": self.facts["manager_image_id"],
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
        if phase == "bootstrap.acpx_promote":
            assert args["manager_port"] == 18115
            assert str(args["acpx_executable"]).endswith("/acpx-runtime/node_modules/acpx/dist/cli.js")
            return {"worker_id": args["worker_id"], "manager_url": "http://127.0.0.1:18115", "active": self.facts["acpx_promoted_ready"], "adapters_ready": self.facts["acpx_promoted_ready"]}
        if phase == "verify.manager":
            return {
                "health": self.facts["live_health"],
                "auth": self.facts["live_auth"],
                "image_id": args.get("image_id"),
                "revision": args.get("commit"),
                "revision_available": args.get("revision_available"),
            }
        if phase == "verify.browser_use":
            return {"active": True, "bound": self.facts["browser_use_bound"]}
        if phase == "verify.acpx":
            if args.get("expected_absent") is True:
                return {"absent": True}
            return {"active": True, "preflights": self.facts["acpx_preflights"]}
        if phase == "verify.proxychecker":
            return {"ok": self.facts["proxychecker"]}
        if phase == "verify.stream":
            return {"ok": self.facts["stream"]}
        if phase == "verify.orca":
            return {
                "ok": self.facts["orca_ok"],
                "status": self.facts["orca_status"],
                "runtime_state": self.facts["orca_runtime_state"],
                "graph_state": self.facts["orca_graph_state"],
            }
        if phase == "verify.tailscale":
            return {"ok": self.facts["tailscale_route"]}
        if phase == "state.commit":
            return {"current": args["current_release"], **args}
        if phase == "candidate.cleanup":
            self.candidate_removed = True
            return {"removed": "candidate-only"}
        if phase == "bootstrap.acpx_cleanup":
            return {"removed": "release-acpx-only", "ok": self.facts["bootstrap_cleanup_ok"]}
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
                "old_image_id": self.facts["manager_image_id"],
                "old_image_digest": self.facts["manager_image_digest"],
                "pointer": "release-old",
                "browser_use_unit_sha256": self.facts["browser_use_unit_sha256"],
                "browser_use_active_state": "active",
                "acpx_unit_sha256": self.facts["acpx_unit_sha256"],
                "acpx_active_state": "active",
            }
        if phase == "rollback.read_state":
            capture = {"old_image_digest": "d" * 64, "browser_use_unit_sha256": "1" * 64, "acpx_unit_sha256": "2" * 64, "live_volume": "cloakbrowser-manager-vcvm-data"}
            if self.facts["rollback_capture_acpx_absent"]:
                capture.update({"acpx_was_absent": True, "acpx_unit_sha256": "0" * 64, "acpx_active_state": "absent"})
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
                "capture": capture,
                "previous_runtime": {
                    "image_ref": "sha256:" + ("d" * 64),
                    "image_id": "sha256:" + ("d" * 64),
                    "image_digest": "d" * 64,
                    "revision": self.facts["state_previous_runtime_revision"],
                    "revision_available": self.facts["state_previous_runtime_revision_available"],
                },
            }
        if phase == "rollback.verify_backup":
            return {"compatible": self.facts["backup_compatible"]}
        if phase == "rollback.verify_previous":
            previous_runtime = args["previous_runtime"]
            return {
                "image_ref": self.facts["rollback_verify_previous_image_ref"] or previous_runtime["image_ref"],
                "image_id": self.facts["rollback_verify_previous_image_id"] or previous_runtime["image_id"],
                "image_digest": self.facts["rollback_verify_previous_image_digest"] or previous_runtime["image_digest"],
                "revision": self.facts["rollback_verify_previous_revision"] or previous_runtime["revision"],
                "revision_available": previous_runtime["revision_available"],
            }
        if phase == "rollback.start_previous":
            self.live_generation = "old"
            return {"container": "cloakbrowser-manager-vcvm"}
        raise AssertionError(f"unexpected phase {phase}")

    def _maybe_fail(self, phase: str) -> None:
        if phase == self.fail_phase or phase in self.fail_phases:
            if self.fail_with_called_process:
                raise subprocess.CalledProcessError(255, ["ssh", "vcvm", phase], stderr=self.fail_stderr)
            if self.fail_with_os_error:
                raise OSError(self.fail_stderr)
            if self.fail_with_timeout:
                raise subprocess.TimeoutExpired(["ssh", "vcvm", phase, "HELPER_SOURCE"], timeout=123, stderr=self.fail_stderr)
            raise tx.TransactionError(self.fail_message or f"synthetic failure at {phase}", phase=phase)


def test_verify_manager_remote_request_requires_structured_identity_args() -> None:
    image_id = "sha256:" + ("d" * 64)

    request = tx.remote_request(
        "verify.manager",
        {"commit": FULL_WORKER_COMMIT, "revision_available": True, "image_id": image_id},
    )

    assert request == {
        "operation": "verify.manager",
        "args": {"commit": FULL_WORKER_COMMIT, "revision_available": True, "image_id": image_id},
    }


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


def test_bootstrap_acpx_success_installs_candidate_worker_before_quiesce_and_promotes_after_switch(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor()

    receipt = tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    for phase in (
        "bootstrap.acpx_probe",
        "bootstrap.acpx_install",
        "bootstrap.acpx_provision_candidate",
        "bootstrap.acpx_start_candidate",
        "bootstrap.acpx_verify_candidate",
        "bootstrap.acpx_promote",
        "bootstrap.acpx_cleanup",
    ):
        assert phase in fake.phases
    assert fake.phases.index("bootstrap.acpx_verify_candidate") < fake.phases.index("quiesce.stop_workers")
    assert fake.phases.index("workers.rebind") < fake.phases.index("bootstrap.acpx_promote")
    bootstrap = receipt["acpx_bootstrap"]
    assert bootstrap["source_commit"] == receipt["source"]["commit"]
    assert bootstrap["candidate"]["port"] == 18116
    assert bootstrap["candidate"]["container"] == "cbm-candidate-release"
    assert bootstrap["promotion"]["port"] == 18115
    assert bootstrap["worker_id"].startswith("acpx-candidate-")
    assert bootstrap["versions"]["acpx"] == "0.12.1"
    assert bootstrap["versions"]["sdk"] == "1.2.1"
    assert bootstrap["run_started_at"]
    assert bootstrap["preflight_completed_at"]
    assert bootstrap["cleanup"]["removed"] == "release-acpx-only"
    assert "cbm_worker_" not in json.dumps(receipt)


def test_bootstrap_acpx_accepts_unlabeled_legacy_manager_without_inventing_revision(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(manager_image_revision="", manager_revision_available=False)

    receipt = tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert receipt["status"] == "success"
    state_commit = next(call for call in fake.calls if call["phase"] == "state.commit")
    payload = json.loads(str(state_commit["argv"][0]))["args"]
    assert payload["capture"]["previous_revision"] == ""
    assert payload["capture"]["previous_revision_available"] is False
    assert payload["previous_runtime"]["revision"] == ""
    assert payload["previous_runtime"]["revision_available"] is False
    assert payload["previous_runtime"]["image_id"] == "sha256:" + ("d" * 64)
    assert payload["previous_runtime"]["image_digest"] == "d" * 64


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ({"acpx_bootstrap_state": "ready", "acpx_absent_missing": []}, "not absent"),
        ({"acpx_bootstrap_state": "blocking", "acpx_absent_missing": [], "acpx_bootstrap_reason": "version_mismatch"}, "version_mismatch"),
        ({"acpx_bootstrap_state": "blocking", "acpx_absent_missing": [], "acpx_bootstrap_reason": "auth_failed"}, "auth_failed"),
        ({"acpx_bootstrap_state": "blocking", "acpx_absent_missing": ["unit"], "acpx_bootstrap_reason": "misconfigured"}, "misconfigured"),
    ],
)
def test_bootstrap_acpx_only_proceeds_for_genuine_absence(
    tmp_path: Path,
    facts: dict[str, object],
    message: str,
) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(**facts)

    with pytest.raises(tx.TransactionError, match=message):
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert fake.mutated_phases == []
    assert "release.upload_archive" not in fake.phases


def test_bootstrap_acpx_rejects_partial_absence_before_remote_mutation(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(acpx_bootstrap_state="absent", acpx_absent_missing=["unit", "key"])

    with pytest.raises(tx.TransactionError, match="partial ACPX"):
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert fake.mutated_phases == []


@pytest.mark.parametrize(
    "phase",
    [
        "bootstrap.acpx_install",
        "bootstrap.acpx_provision_candidate",
        "bootstrap.acpx_start_candidate",
        "bootstrap.acpx_verify_candidate",
    ],
)
def test_bootstrap_acpx_candidate_stage_failure_cleans_up_and_preserves_live(tmp_path: Path, phase: str) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(fail_phase=phase)

    with pytest.raises(tx.TransactionError):
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert "bootstrap.acpx_cleanup" in fake.phases
    assert fake.live_generation == "old"
    assert "quiesce.stop_live" not in fake.phases


def test_bootstrap_acpx_readiness_failure_surfaces_safe_reason_and_cleans_up_before_quiesce(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(
        fail_phase="bootstrap.acpx_verify_candidate",
        fail_message=(
            "ACPX candidate readiness failed: attempt_count=3 elapsed_seconds=180.00 "
            "deadline_seconds=180 reason=manager_preflight_auth_required"
        ),
    )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    message = str(exc_info.value)
    assert exc_info.value.phase == "bootstrap.acpx_verify_candidate"
    assert "manager_preflight_auth_required" in message
    assert "attempt_count=3" in message
    assert "bootstrap.acpx_cleanup" in fake.phases
    assert "candidate.cleanup" in fake.phases
    assert "quiesce.stop_workers" not in fake.phases
    assert "quiesce.stop_live" not in fake.phases
    assert "cbm_worker_" not in message


def test_bootstrap_called_process_failure_before_quiesce_is_wrapped_and_cleans_up(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(fail_phase="bootstrap.acpx_start_candidate", fail_with_called_process=True)

    with pytest.raises(tx.TransactionError) as exc_info:
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert exc_info.value.phase == "bootstrap.acpx_start_candidate"
    assert "bootstrap.acpx_cleanup" in fake.phases
    assert "quiesce.stop_live" not in fake.phases


def test_bootstrap_called_process_failure_after_quiesce_restores_and_cleans_up(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(fail_phase="bootstrap.acpx_promote", fail_with_called_process=True)

    with pytest.raises(tx.TransactionError, match="release failed after quiesce"):
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert "restore.runtime" in fake.phases
    assert "restore.verify" in fake.phases
    assert "bootstrap.acpx_cleanup" in fake.phases


def test_bootstrap_promotion_failure_still_restores_when_cleanup_also_fails(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(
        fail_phases={"bootstrap.acpx_promote", "bootstrap.acpx_cleanup"},
        fail_with_called_process=True,
        fail_stderr=f"cleanup failed token={SECRET_TOKEN}",
    )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert exc_info.value.phase == "bootstrap.acpx_promote"
    message = str(exc_info.value)
    assert "release failed after quiesce" in message
    assert "cleanup" in message
    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert "restore.runtime" in fake.phases
    assert "restore.verify" in fake.phases
    assert fake.live_generation == "old"


def test_bootstrap_acpx_promotion_failure_restores_old_runtime_and_cleans_release_artifacts(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(fail_phase="bootstrap.acpx_promote")

    with pytest.raises(tx.TransactionError, match="release failed after quiesce"):
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert "restore.runtime" in fake.phases
    assert "restore.verify" in fake.phases
    assert "bootstrap.acpx_cleanup" in fake.phases
    assert fake.live_generation == "old"


def test_bootstrap_cleanup_failure_fails_closed_before_state_commit_and_restores_old_runtime(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    add_acpx_locks(repo)
    fake = FakeRemoteExecutor(fail_phase="bootstrap.acpx_cleanup")

    with pytest.raises(tx.TransactionError, match="release failed after quiesce") as exc_info:
        tx.run_release(release_config(repo, bootstrap_acpx=True), fake)

    assert exc_info.value.phase == "bootstrap.acpx_cleanup"
    assert "state.commit" not in fake.phases
    assert "restore.runtime" in fake.phases
    assert "restore.verify" in fake.phases
    assert fake.live_generation == "old"


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
        ({"manager_image_revision": "", "manager_revision_available": False}, "Manager revision label"),
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


def test_bootstrap_acpx_release_config_requires_apply_before_local_work(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor()

    with pytest.raises(tx.TransactionError, match="requires --apply"):
        tx.run_release(release_config(repo, apply=False, bootstrap_acpx=True), fake)

    assert fake.calls == []
    assert fake.uploads == []


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


def test_candidate_verify_called_process_failure_reports_phase_and_redacts_stderr(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    secret_stderr = f"ssh failed token={SECRET_TOKEN} helper_source=REMOTE_HELPER_SOURCE"
    fake = FakeRemoteExecutor(
        fail_phase="candidate.verify",
        fail_with_called_process=True,
        fail_stderr=secret_stderr,
    )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx.run_release(release_config(repo), fake)

    assert exc_info.value.phase == "candidate.verify"
    message = str(exc_info.value)
    assert "remote phase failed" in message
    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert secret_stderr not in message
    assert "REMOTE_HELPER_SOURCE" not in message
    assert "candidate.cleanup" in fake.phases


def test_upload_failure_preserves_phase_and_redacts_message(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(
        fail_phase="release.upload_archive",
        fail_with_os_error=True,
        fail_stderr=f"scp failed password={SECRET_TOKEN}",
    )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx.run_release(release_config(repo), fake)

    assert exc_info.value.phase == "release.upload_archive"
    message = str(exc_info.value)
    assert "upload phase failed" in message
    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert "release.verify_archive" not in fake.phases


def test_remote_timeout_preserves_phase_and_redacts_payload() -> None:
    fake = FakeRemoteExecutor(
        fail_phase="candidate.verify",
        fail_with_timeout=True,
        fail_stderr=f'{{"helper_source":"REMOTE_HELPER_SOURCE","token":"{SECRET_TOKEN}"}}',
    )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx._remote(fake, "candidate.verify", {"commit": FULL_WORKER_COMMIT, "container": "cbm-candidate-release"})

    assert exc_info.value.phase == "candidate.verify"
    message = str(exc_info.value)
    assert "remote phase timed out" in message
    assert "timeout=123" in message
    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert "REMOTE_HELPER_SOURCE" not in message
    assert "HELPER_SOURCE" not in message


def test_upload_timeout_preserves_phase_and_redacts_payload(tmp_path: Path) -> None:
    class TimeoutUpload:
        def run_json(self, request: dict[str, object], *, phase: str, mutation: bool = False) -> dict[str, object]:
            del request, phase, mutation
            return {}

        def upload(self, local_path: Path, remote_path: str, *, phase: str) -> None:
            del local_path, remote_path, phase
            raise subprocess.TimeoutExpired(
                ["scp", "HELPER_SOURCE"],
                timeout=45,
                stderr=f'{{"helper_source":"REMOTE_HELPER_SOURCE","password":"{SECRET_TOKEN}"}}',
            )

    with pytest.raises(tx.TransactionError) as exc_info:
        tx._upload(TimeoutUpload(), tmp_path / "source.tar", "/home/coder/cloakbrowser-manager/releases/x/source.tar", phase="release.upload_archive")

    assert exc_info.value.phase == "release.upload_archive"
    message = str(exc_info.value)
    assert "upload phase timed out" in message
    assert "timeout=45" in message
    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert "REMOTE_HELPER_SOURCE" not in message
    assert "HELPER_SOURCE" not in message


def test_bounded_error_message_redacts_json_helper_source_payload() -> None:
    message = tx.bounded_error_message(
        f'failure {{"helper_source":"REMOTE_HELPER_SOURCE","token":"{SECRET_TOKEN}"}}'
    )

    assert "<redacted>" in message
    assert SECRET_TOKEN not in message
    assert "REMOTE_HELPER_SOURCE" not in message


def test_ssh_executor_uses_explicit_timeouts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("# helper\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}\n", stderr="")

    monkeypatch.setattr(tx.subprocess, "run", fake_run)
    executor = tx.SSHRemoteExecutor("vcvm", helper_path=helper, run_timeout_seconds=12, upload_timeout_seconds=34)

    assert executor.run_json({"operation": "helper.capabilities", "args": {}}, phase="helper.capabilities") == {}
    executor.upload(tmp_path / "source.tar", "/home/coder/cloakbrowser-manager/releases/x/source.tar", phase="release.upload_archive")

    assert calls[0]["kwargs"]["timeout"] == 12
    assert calls[1]["kwargs"]["timeout"] == 34


def test_candidate_verify_ssh_timeout_exceeds_remote_readiness_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text("# helper\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}\n", stderr="")

    monkeypatch.setattr(tx.subprocess, "run", fake_run)
    executor = tx.SSHRemoteExecutor("vcvm", helper_path=helper)

    assert tx.CANDIDATE_VERIFY_RUN_JSON_TIMEOUT_SECONDS > 180
    executor.run_json({"operation": "helper.capabilities", "args": {}}, phase="helper.capabilities")
    executor.run_json(
        {
            "operation": "candidate.verify",
            "args": {"commit": FULL_WORKER_COMMIT, "container": "cbm-candidate-release"},
        },
        phase="candidate.verify",
    )
    executor.run_json(
        {
            "operation": "bootstrap.acpx_verify_candidate",
            "args": {
                "release_id": "release-0000001",
                "worker_id": "acpx-candidate-release-0000001",
                "manager_port": tx.DEFAULT_CANDIDATE_PORT,
                "acpx_executable": "/home/coder/cloakbrowser-manager/releases/release-0000001/acpx-bootstrap/node-runtime/node_modules/acpx/dist/cli.js",
            },
        },
        phase="bootstrap.acpx_verify_candidate",
    )

    assert calls[0]["kwargs"]["timeout"] == tx.DEFAULT_SSH_RUN_JSON_TIMEOUT_SECONDS
    assert calls[1]["kwargs"]["timeout"] == tx.CANDIDATE_VERIFY_RUN_JSON_TIMEOUT_SECONDS
    assert calls[2]["kwargs"]["timeout"] == tx.CANDIDATE_VERIFY_RUN_JSON_TIMEOUT_SECONDS


def test_candidate_migrations_must_match_exact_required_set(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(candidate_migrations=["agent_workspace_v1"])
    with pytest.raises(tx.TransactionError, match="migration set"):
        tx.run_release(release_config(repo), fake)
    assert fake.candidate_removed is True


def test_candidate_migrations_require_task_artifacts_v1(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    old_required_migrations = [
        "agent_workspace_v1",
        "task_runs_v1",
        "worker_runtime_v1",
        "task_runs_acpx_v1",
        "worker_harness_presence_v1",
        "worker_harness_preflights_v1",
        "task_run_binding_v1",
    ]
    fake = FakeRemoteExecutor(candidate_migrations=old_required_migrations)

    with pytest.raises(tx.TransactionError, match="migration set"):
        tx.run_release(release_config(repo), fake)

    assert fake.candidate_removed is True


def test_required_migration_set_is_exact_eight_with_task_artifacts() -> None:
    assert tx.REQUIRED_MIGRATIONS == (
        "agent_workspace_v1",
        "task_runs_v1",
        "task_artifacts_v1",
        "worker_runtime_v1",
        "task_runs_acpx_v1",
        "worker_harness_presence_v1",
        "worker_harness_preflights_v1",
        "task_run_binding_v1",
    )


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
    assert payload["previous_runtime"]["revision_available"] is True
    assert payload["previous_runtime"]["revision"] != payload["image"]["revision"]


def test_secret_bearing_capture_state_refuses_before_state_commit(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(capture_extra={"browser_use_dropin_content": f"TOKEN={SECRET_TOKEN}\n"})
    with pytest.raises(tx.TransactionError, match="state payload contains secret"):
        tx.run_release(release_config(repo), fake)
    assert "state.commit" not in fake.phases


def test_verify_orca_requires_structured_ready_response(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(orca_ok=False, orca_runtime_state="starting")

    with pytest.raises(tx.TransactionError, match="Orca verification failed"):
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
    manager_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "verify.manager")
    assert verify_request["args"]["previous_runtime"]["revision"] == old_revision
    assert start_request["args"]["previous_runtime"]["revision"] == old_revision
    assert manager_request["args"] == {
        "commit": old_revision,
        "revision_available": True,
        "image_id": "sha256:" + ("d" * 64),
    }


def test_rollback_unlabeled_previous_runtime_compares_immutable_image_identity(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(state_previous_runtime_revision="", state_previous_runtime_revision_available=False)

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

    assert receipt["status"] == "rolled_back"
    verify_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "rollback.verify_previous")
    previous_runtime = verify_request["args"]["previous_runtime"]
    assert previous_runtime["revision"] == ""
    assert previous_runtime["revision_available"] is False
    assert previous_runtime["image_id"] == "sha256:" + ("d" * 64)
    assert previous_runtime["image_digest"] == "d" * 64
    manager_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "verify.manager")
    assert manager_request["args"] == {
        "commit": "",
        "revision_available": False,
        "image_id": "sha256:" + ("d" * 64),
    }


def test_rollback_unlabeled_previous_runtime_rejects_image_digest_mismatch(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(
        state_previous_runtime_revision="",
        state_previous_runtime_revision_available=False,
        rollback_verify_previous_image_digest="f" * 64,
    )

    with pytest.raises(tx.TransactionError, match="previous runtime image verification failed"):
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

    assert "quiesce.stop_workers" not in fake.phases


def test_rollback_previous_runtime_rejects_image_ref_mismatch(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(rollback_verify_previous_image_ref="sha256:" + ("f" * 64))

    with pytest.raises(tx.TransactionError, match="previous runtime image verification failed"):
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

    assert "quiesce.stop_workers" not in fake.phases


def test_rollback_runtime_verification_honors_captured_acpx_absence(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    fake = FakeRemoteExecutor(rollback_capture_acpx_absent=True)

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

    verify_acpx_request = next(json.loads(str(call["argv"][0])) for call in fake.calls if call["phase"] == "verify.acpx")
    assert verify_acpx_request["args"]["expected_absent"] is True
    assert receipt["status"] == "rolled_back"


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


def test_release_parser_preserves_global_options_before_release_subcommand(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)

    args = tx.parse_args(
        [
            "--source-root",
            str(repo),
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--expected-current-worker-commit",
            FULL_WORKER_COMMIT,
            "--apply",
            "release",
        ]
    )

    assert args.command == "release"
    assert args.source_root == repo
    assert args.expected_source_remote == AUTHORIZED_FORK
    assert args.expected_current_worker_commit == FULL_WORKER_COMMIT
    assert args.apply is True


def test_release_parser_accepts_options_after_release_subcommand(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)

    args = tx.parse_args(
        [
            "release",
            "--source-root",
            str(repo),
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--expected-current-worker-commit",
            FULL_WORKER_COMMIT,
            "--apply",
        ]
    )

    assert args.command == "release"
    assert args.source_root == repo
    assert args.expected_source_remote == AUTHORIZED_FORK
    assert args.expected_current_worker_commit == FULL_WORKER_COMMIT
    assert args.apply is True


def test_cli_bootstrap_acpx_dry_run_reports_required_exact_gates_without_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = fixture_repo(tmp_path)

    class ForbiddenSSH:
        def __init__(self, host: str) -> None:
            raise AssertionError(f"unexpected SSH construction for {host}")

    monkeypatch.setattr(tx, "SSHRemoteExecutor", ForbiddenSSH)

    rc = tx.main(
        [
            "release",
            "--source-root",
            str(repo),
            "--release-id",
            "release-20260727-ac5840b00001",
            "--source-remote",
            "fork",
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--bootstrap-acpx",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "dry_run"
    assert payload["would_mutate"] is False
    assert payload["bootstrap_acpx"] == {
        "requested": True,
        "requires_apply": True,
        "required_expected_current_worker_commit": "full 40-hex commit",
        "required_expected_source_remote": AUTHORIZED_FORK,
    }
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["--expected-source-remote", AUTHORIZED_FORK],
        ["--expected-current-worker-commit", FULL_WORKER_COMMIT],
        ["--expected-source-remote", "", "--expected-current-worker-commit", FULL_WORKER_COMMIT],
        ["--expected-source-remote", AUTHORIZED_FORK, "--expected-current-worker-commit", "50a9e43"],
    ],
)
def test_cli_bootstrap_acpx_apply_requires_explicit_exact_gates_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    repo = fixture_repo(tmp_path)

    class ForbiddenSSH:
        def __init__(self, host: str) -> None:
            raise AssertionError(f"unexpected SSH construction for {host}")

    monkeypatch.setattr(tx, "SSHRemoteExecutor", ForbiddenSSH)

    rc = tx.main(
        [
            "release",
            "--source-root",
            str(repo),
            "--release-id",
            "release-20260727-ac5840b00001",
            "--bootstrap-acpx",
            "--apply",
            *argv,
        ]
    )

    captured = capsys.readouterr()
    assert rc == 75
    assert captured.out == ""
    assert "vcvm release transaction refused:" in captured.err


def test_cli_bootstrap_acpx_apply_checks_exact_source_remote_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = fixture_repo(tmp_path)

    class ForbiddenSSH:
        def __init__(self, host: str) -> None:
            raise AssertionError(f"unexpected SSH construction for {host}")

    monkeypatch.setattr(tx, "SSHRemoteExecutor", ForbiddenSSH)

    rc = tx.main(
        [
            "release",
            "--source-root",
            str(repo),
            "--release-id",
            "release-20260727-ac5840b00001",
            "--source-remote",
            "fork",
            "--expected-source-remote",
            UPSTREAM,
            "--expected-current-worker-commit",
            FULL_WORKER_COMMIT,
            "--bootstrap-acpx",
            "--apply",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 75
    assert captured.out == ""
    assert "source remote does not match expected fork remote" in captured.err


def test_cli_bootstrap_acpx_apply_routes_bootstrap_flag_with_explicit_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = fixture_repo(tmp_path)
    captured_configs: list[tx.ReleaseConfig] = []

    class FakeSSH:
        def __init__(self, host: str) -> None:
            self.host = host

    def fake_run_release(config: tx.ReleaseConfig, executor: object) -> dict[str, object]:
        captured_configs.append(config)
        assert isinstance(executor, FakeSSH)
        return {"status": "success", "bootstrap": config.bootstrap_acpx}

    monkeypatch.setattr(tx, "SSHRemoteExecutor", FakeSSH)
    monkeypatch.setattr(tx, "run_release", fake_run_release)

    rc = tx.main(
        [
            "release",
            "--source-root",
            str(repo),
            "--release-id",
            "release-20260727-ac5840b00001",
            "--source-remote",
            "fork",
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--expected-current-worker-commit",
            FULL_WORKER_COMMIT,
            "--bootstrap-acpx",
            "--apply",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out) == {"bootstrap": True, "status": "success"}
    assert captured.err == ""
    assert len(captured_configs) == 1
    config = captured_configs[0]
    assert config.apply is True
    assert config.bootstrap_acpx is True
    assert config.expected_current_worker_commit == FULL_WORKER_COMMIT
    assert config.expected_source_remote == AUTHORIZED_FORK


def test_cli_error_output_includes_phase_and_redacted_bounded_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run_release(config: tx.ReleaseConfig, executor: object) -> dict[str, object]:
        del config, executor
        raise tx.TransactionError(
            f"remote phase failed: Command ['ssh', 'vcvm', 'python3', '-c', 'HELPER_SOURCE'] failed token={SECRET_TOKEN}",
            phase="candidate.verify",
        )

    class FakeSSH:
        def __init__(self, host: str) -> None:
            self.host = host

    monkeypatch.setattr(tx, "SSHRemoteExecutor", FakeSSH)
    monkeypatch.setattr(tx, "require_bootstrap_acpx_cli_apply_gates", lambda args: None)
    monkeypatch.setattr(tx, "run_release", fake_run_release)

    rc = tx.main(
        [
            "release",
            "--release-id",
            "release-20260727-ac5840b00001",
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--apply",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 75
    assert captured.out == ""
    assert "phase=candidate.verify" in captured.err
    assert "<redacted>" in captured.err
    assert SECRET_TOKEN not in captured.err
    assert "HELPER_SOURCE" not in captured.err
    assert "python3" not in captured.err


def test_cli_timeout_error_output_includes_phase(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run_release(config: tx.ReleaseConfig, executor: object) -> dict[str, object]:
        del config, executor
        raise tx.TransactionError("remote phase timed out: timeout=210", phase="candidate.verify")

    class FakeSSH:
        def __init__(self, host: str) -> None:
            self.host = host

    monkeypatch.setattr(tx, "SSHRemoteExecutor", FakeSSH)
    monkeypatch.setattr(tx, "require_bootstrap_acpx_cli_apply_gates", lambda args: None)
    monkeypatch.setattr(tx, "run_release", fake_run_release)

    rc = tx.main(
        [
            "release",
            "--release-id",
            "release-20260727-ac5840b00001",
            "--expected-source-remote",
            AUTHORIZED_FORK,
            "--apply",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 75
    assert captured.out == ""
    assert "phase=candidate.verify" in captured.err
    assert "remote phase timed out" in captured.err
    assert "timeout=210" in captured.err


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
