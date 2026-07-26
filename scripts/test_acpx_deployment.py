from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "systemd" / "cloakbrowser-acpx-worker.service.template"
REQUIREMENTS = ROOT / "scripts" / "requirements-acpx-worker.txt"


def test_acpx_worker_unit_is_private_restartable_and_explicitly_configured():
    text = UNIT.read_text(encoding="utf-8")
    assert "UMask=0077" in text
    assert "Restart=on-failure" in text
    assert "-m scripts.acpx_worker" in text
    for placeholder in (
        "@WORKING_DIRECTORY@",
        "@VENV_PYTHON@",
        "@MANAGER_URL@",
        "@WORKER_ID@",
        "@TOKEN_FILE@",
        "@ACPX_EXECUTABLE@",
        "@PERMISSION_POLICY@",
        "@MCP_CONFIG@",
        "@CAPABILITY_DIR@",
    ):
        assert placeholder in text
    assert "--token " not in text
    assert "--token-file @TOKEN_FILE@" in text


def test_acpx_worker_dependencies_pin_mcp_major_and_playwright_major():
    lines = {
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "mcp>=1.27,<2" in lines
    assert "playwright>=1.52,<2" in lines
