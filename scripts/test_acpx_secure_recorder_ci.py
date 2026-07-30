from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), "CI workflow is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def test_acpx_secure_recorder_workflow_has_required_gates():
    text = _workflow_text()
    for job in (
        "extension-static-checks:",
        "backend-contract-security-tests:",
        "browser-use-smoke-contract:",
        "secret-scan:",
        "package-artifact:",
        "deploy-rollback-gates:",
        "recorder-watchdog:",
    ):
        assert job in text

    for command in (
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
        "bash scripts/deploy_vcvm.sh",
        "python -m pytest scripts/test_secure_recorder_watchdog.py",
    ):
        assert command in text


def test_acpx_secure_recorder_workflow_is_secret_safe_and_artifact_scoped():
    text = _workflow_text()
    assert "permissions:\n  contents: read" in text
    assert "secrets." not in text
    assert "CBM_WORKER_TOKEN" not in text
    assert "Authorization:" not in text
    assert "--token " not in text
    assert "acpx-secure-recorder-package" in text
    assert "frontend/node_modules" in text
    assert "git archive" in text
    assert "--validate-only" in text
    assert "secure_recorder_watchdog.py" in text
    assert "--output-json" in text
    assert "recorder-watchdog-dry.json\" || true" not in text
