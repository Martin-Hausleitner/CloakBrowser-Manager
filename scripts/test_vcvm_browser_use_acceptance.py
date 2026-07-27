import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).with_name("vcvm_browser_use_acceptance.py")
SPEC = importlib.util.spec_from_file_location("vcvm_browser_use_acceptance", SCRIPT_PATH)
assert SPEC is not None
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def valid_evidence() -> dict:
    return {
        "schema_version": 1,
        "expected": {
            "manager_commit": "8f8fd1e",
            "worker_commit": "8f8fd1e",
            "browser_use_version": "0.13.6",
            "migrations": list(gate.EXPECTED_MIGRATIONS),
            "typed_output_kinds": list(gate.EXPECTED_OUTPUT_KINDS),
            "current_url": "https://example.com/",
            "viewport": {"width": 1920, "height": 1080, "device_scale_factor": 1},
        },
        "live": {
            "manager": {
                "commit": "8f8fd1e",
                "health_ok": True,
                "migrations": list(gate.EXPECTED_MIGRATIONS),
            },
            "worker": {
                "commit": "8f8fd1e",
                "active": True,
                "worker_id": "browser-use-worker-vcvm",
                "unit_exec": (
                    "/home/coder/.local/share/cloakbrowser-worker/prod-venv/bin/python "
                    "-m scripts.browser_use_worker --manager-url http://127.0.0.1:18115 "
                    "--worker-id browser-use-worker-vcvm "
                    "--token-file /home/coder/.config/cloakbrowser/browser-use-worker-key"
                ),
                "browser_use_version": "0.13.6",
            },
        },
        "run": {
            "id": "run-1",
            "profile_id": "profile-1",
            "profile_binding": {
                "profile_id": "profile-1",
                "same_profile_visible": True,
                "user_data_dir_fingerprint": "sha256:profile",
            },
            "viewport": {"width": 1920, "height": 1080, "device_scale_factor": 1},
            "cdp_target": {
                "profile_id": "profile-1",
                "target_id": "page-1",
                "version_url": "/api/profiles/profile-1/cdp/json/version",
            },
            "first_action": {"sequence": 1, "kind": "action", "name": "navigate"},
            "outputs": [
                {"sequence": 1, "kind": "action", "payload": {"name": "navigate"}},
                {"sequence": 2, "kind": "observation", "payload": {"text": "loaded"}},
                {"sequence": 3, "kind": "observation", "payload": {"text": "read"}},
                {"sequence": 4, "kind": "screenshot", "payload": {}},
                {"sequence": 5, "kind": "summary", "payload": {"text": "ok"}},
            ],
            "screenshot": {
                "sha256": "a" * 64,
                "uploaded_sha256": "a" * 64,
                "media_type": "image/png",
                "bytes": 20445,
                "width": 1920,
                "height": 1080,
            },
            "current_url": "https://example.com/",
            "terminal_status": "succeeded",
            "health_decision": {"allowed": True, "override": {"applied": False}},
            "cancellation": {
                "cancel_status": "cancelled",
                "worker_stopped": True,
                "capability_revoked": True,
                "post_cancel_outputs": 0,
                "post_cancel_terminal_mutation": False,
            },
        },
    }


def check(result: dict, check_id: str) -> dict:
    return next(item for item in result["checks"] if item["id"] == check_id)


def test_acceptance_passes_only_with_complete_matching_evidence() -> None:
    result = gate.verify(valid_evidence())

    assert result["status"] == "passed"
    assert result["summary"] == {"passed": 15, "failed": 0, "degraded": 0}


def test_expected_migration_set_is_exact_eight_with_task_artifacts() -> None:
    assert gate.EXPECTED_MIGRATIONS == (
        "agent_workspace_v1",
        "task_runs_v1",
        "task_artifacts_v1",
        "worker_runtime_v1",
        "task_runs_acpx_v1",
        "worker_harness_presence_v1",
        "worker_harness_preflights_v1",
        "task_run_binding_v1",
    )


def test_acceptance_rejects_evidence_missing_task_artifacts_migration() -> None:
    evidence = valid_evidence()
    old_required_migrations = [
        "agent_workspace_v1",
        "task_runs_v1",
        "worker_runtime_v1",
        "task_runs_acpx_v1",
        "worker_harness_presence_v1",
        "worker_harness_preflights_v1",
        "task_run_binding_v1",
    ]
    evidence["live"]["manager"]["migrations"] = old_required_migrations

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "migration_set")["status"] == "failed"
    assert check(result, "migration_set")["evidence"]["missing"] == ["task_artifacts_v1"]


def test_acceptance_rejects_actual_unknown_migration() -> None:
    evidence = valid_evidence()
    evidence["live"]["manager"]["migrations"] = [
        *gate.EXPECTED_MIGRATIONS,
        "unknown_migration_v1",
    ]

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "migration_set")["status"] == "failed"
    assert check(result, "migration_set")["evidence"]["unexpected"] == ["unknown_migration_v1"]


def test_acceptance_rejects_expected_override_with_unknown_migration() -> None:
    evidence = valid_evidence()
    widened = [
        *gate.EXPECTED_MIGRATIONS,
        "unknown_migration_v1",
    ]
    evidence["expected"]["migrations"] = widened
    evidence["live"]["manager"]["migrations"] = widened

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "migration_set")["status"] == "failed"
    assert check(result, "migration_set")["evidence"]["unexpected"] == ["unknown_migration_v1"]


def test_acceptance_rejects_duplicate_migration_entries() -> None:
    evidence = valid_evidence()
    evidence["live"]["manager"]["migrations"] = [
        *gate.EXPECTED_MIGRATIONS,
        "task_artifacts_v1",
    ]

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "migration_set")["status"] == "failed"
    assert check(result, "migration_set")["evidence"]["duplicates"] == ["task_artifacts_v1"]


def test_live_version_skew_is_a_hard_failure() -> None:
    evidence = valid_evidence()
    evidence["live"]["manager"]["commit"] = ""
    evidence["live"]["worker"]["commit"] = "50a9e43"

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "manager_commit")["status"] == "failed"
    assert check(result, "worker_commit")["status"] == "failed"


def test_missing_run_proofs_fail_closed() -> None:
    evidence = valid_evidence()
    evidence["run"] = {}

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    for check_id in (
        "profile_binding",
        "viewport",
        "first_action",
        "typed_output_ordering",
        "screenshot_hash",
        "current_url",
        "terminal_status",
        "cancellation_fencing",
    ):
        assert check(result, check_id)["status"] == "failed"


def test_health_override_is_degraded_not_success() -> None:
    evidence = valid_evidence()
    evidence["run"]["health_decision"] = {
        "allowed": True,
        "override": {"applied": True, "reason": "operator exception"},
    }

    result = gate.verify(evidence)

    assert result["status"] == "degraded"
    assert check(result, "health_gate")["status"] == "degraded"


def test_cli_returns_nonzero_for_degraded_evidence(tmp_path) -> None:
    evidence = valid_evidence()
    evidence["run"]["health_decision"]["override"]["applied"] = True
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    completed = subprocess.run(
        ["python3", "scripts/vcvm_browser_use_acceptance.py", "--evidence", str(evidence_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "degraded"


def test_redaction_covers_common_secret_shapes() -> None:
    dirty = {
        "headers": "Authorization: Bearer abc123 and bearer def456",
        "argv": "--token super-secret --auth-token=other-secret --token-file /tmp/key",
        "urls": [
            "https://user:pass@example.com/path?token=query-secret&ok=1",
            "http://example.com/callback?password=pw&api_key=key",
        ],
        "assignment": "password=hunter2 token: direct secret=inline api_key=abc",
        "cbm": "cbm_worker_abc123",
    }

    cleaned = json.dumps(gate.redact(dirty))

    for leaked in (
        "abc123",
        "def456",
        "super-secret",
        "other-secret",
        "/tmp/key",
        "user:pass",
        "query-secret",
        "hunter2",
        "cbm_worker_abc123",
    ):
        assert leaked not in cleaned
    assert "<redacted>" in cleaned


def test_malformed_numeric_fields_fail_closed_without_exceptions() -> None:
    evidence = valid_evidence()
    evidence["run"]["outputs"][1]["sequence"] = "two"
    evidence["run"]["screenshot"]["bytes"] = "many"
    evidence["run"]["screenshot"]["width"] = None
    evidence["run"]["cancellation"]["post_cancel_outputs"] = "none"

    result = gate.verify(evidence)

    assert result["status"] == "failed"
    assert check(result, "typed_output_ordering")["status"] == "failed"
    assert check(result, "screenshot_hash")["status"] == "failed"
    assert check(result, "cancellation_fencing")["status"] == "failed"


def test_live_inventory_keeps_manager_runtime_commit_separate(monkeypatch) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": "",
        "live": {
            "checkout": {"commit": "50a9e43", "branch": "feature/browser-use-agent-workspace"},
            "manager": {"commit": "", "health_ok": True, "migrations": []},
            "worker": {
                "commit": "50a9e43",
                "branch": "feature/browser-use-agent-workspace",
                "working_directory": "/home/coder/vk-repos/CloakBrowser-Manager-browser-use",
                "active": True,
                "worker_id": "browser-use-worker-vcvm",
                "unit_exec": "python -m scripts.browser_use_worker --token-file /path/key",
                "browser_use_version": "",
            },
        },
        "run": {},
    }

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    inventory = gate.live_inventory("vcvm")

    assert inventory["live"]["checkout"]["commit"] == "50a9e43"
    assert inventory["live"]["manager"]["commit"] == ""
    assert inventory["live"]["worker"]["commit"] == "50a9e43"
