from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class _ActionsLoader(yaml.SafeLoader):
    pass


for first_letter, resolvers in list(_ActionsLoader.yaml_implicit_resolvers.items()):
    _ActionsLoader.yaml_implicit_resolvers[first_letter] = [
        (tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"
    ]


def _workflow() -> dict[str, Any]:
    assert WORKFLOW.is_file(), "CI workflow is missing"
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=_ActionsLoader)


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), "CI workflow is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def _job(job_id: str) -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert job_id in jobs
    return jobs[job_id]


def _run_blocks(job_id: str) -> list[str]:
    return [str(step.get("run", "")) for step in _job(job_id)["steps"] if "run" in step]


def _combined_run(job_id: str) -> str:
    return "\n".join(_run_blocks(job_id))


def test_acpx_secure_recorder_workflow_has_required_gates():
    jobs = _workflow()["jobs"]
    for job in (
        "project-gates",
        "extension-static-checks",
        "backend-contract-security-tests",
        "browser-use-smoke-contract",
        "stale-provider-compatibility",
        "secret-scan",
        "package-artifact",
        "deploy-rollback-gates",
        "recorder-watchdog",
    ):
        assert job in jobs

    command_sources = "\n".join(
        _combined_run(job)
        for job in (
            "project-gates",
            "extension-static-checks",
            "backend-contract-security-tests",
            "browser-use-smoke-contract",
            "stale-provider-compatibility",
            "deploy-rollback-gates",
            "recorder-watchdog",
        )
    )
    for command in (
        "python -m pytest scripts/test_acpx_secure_recorder_ci.py",
        "node --check extensions/cloak-profile-sync/background/service-worker.js",
        "node --check extensions/cloak-profile-sync/content/recorder-content.js",
        "node --check extensions/cloak-profile-sync/lib/local-control.js",
        "npm --prefix extensions/cloak-profile-sync test",
        "python -m py_compile extensions/cloak-profile-sync/host/local_bridge.py",
        "python -m py_compile scripts/cbm_extension_bridge.py",
        "python -m py_compile scripts/cbm_extension_ctl.py",
        "python -m py_compile scripts/cbm_extension_mcp.py",
        "scripts/test_cbm_extension_bridge.py",
        "scripts/test_cbm_extension_ctl.py",
        "scripts/test_cbm_extension_mcp.py",
        "scripts/test_extension_local_bridge_security.py",
        "backend/tests/test_secure_action_recorder.py",
        "backend/tests/test_worker_auth.py",
        "scripts/test_browser_use_worker.py",
        "scripts/test_secure_recording_compiler.py",
        "scripts/test_provider_readiness.py",
        "bash scripts/deploy_vcvm.sh",
        "bash scripts/rollback_vcvm_release.sh",
        "python -m pytest scripts/test_secure_recorder_watchdog.py",
    ):
        assert command in command_sources


def test_package_artifact_depends_on_project_gates_and_security_gates():
    needs = _job("package-artifact")["needs"]
    assert needs == [
        "project-gates",
        "extension-static-checks",
        "backend-contract-security-tests",
        "browser-use-smoke-contract",
        "secret-scan",
        "recorder-watchdog",
    ]
    assert "stale-provider-compatibility" not in needs

    package_run = _combined_run("package-artifact")
    assert "Enforce package archive denylist" in str(_job("package-artifact")["steps"])
    assert "frontend/node_modules/" in package_run
    assert "secret|token" in package_run
    assert "git archive" in package_run


def test_script_importing_jobs_set_repo_pythonpath():
    browser_smoke = _combined_run("browser-use-smoke-contract")
    provider_compatibility = _combined_run("stale-provider-compatibility")
    assert "PYTHONPATH=. python -m pytest" in browser_smoke
    assert "PYTHONPATH=. python -m pytest" in provider_compatibility


def test_secret_scan_reports_only_redacted_findings():
    secret_scan = _combined_run("secret-scan")
    assert "secret-candidates-redacted.txt" in secret_scan
    assert "Matched values are intentionally redacted" in secret_scan
    assert "rule_name" in secret_scan
    assert "git grep" not in secret_scan
    assert "secret-candidates.txt" not in secret_scan
    assert "cat " not in secret_scan
    assert "::add-mask::" not in secret_scan

    package_run = _combined_run("package-artifact")
    assert "package-denylist-redacted.txt" in package_run
    assert "high-confidence secret candidates with values redacted" in package_run
    assert "findings.append((path, line_number, rule_name))" in package_run


def test_deploy_rollback_gate_is_honest_dry_run_only():
    job = _job("deploy-rollback-gates")
    assert job["name"] == "ACPX deploy and rollback dry-run gates"
    run = _combined_run("deploy-rollback-gates")
    assert "DRY RUN ONLY: deploy command planning; no live VCVM deploy is attempted." in run
    assert "DRY RUN ONLY: rollback command planning; no live VCVM rollback is attempted." in run
    assert "--apply" not in run


def test_stale_provider_compatibility_is_separate_from_release_gates():
    smoke_run = _combined_run("browser-use-smoke-contract")
    compatibility_run = _combined_run("stale-provider-compatibility")
    assert "scripts/test_provider_readiness.py" not in smoke_run
    assert "scripts/test_provider_readiness.py" in compatibility_run
    assert "Antigravity and Claude compatibility drift tests" in str(
        _job("stale-provider-compatibility")["steps"]
    )


def test_workflow_is_secret_safe_and_artifact_scoped():
    text = _workflow_text()
    assert _workflow()["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    assert "CBM_WORKER_TOKEN" not in text
    assert "Authorization:" not in text
    assert "--token " not in text
    assert "acpx-secure-recorder-package" in text
    assert "--validate-only" in text
    assert "secure_recorder_watchdog.py" in text
    assert "--output-json" in text
    assert 'recorder-watchdog-dry.json" || true' not in text
