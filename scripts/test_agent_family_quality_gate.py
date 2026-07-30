#!/usr/bin/env python3
"""TDD contracts for agent_family_quality_gate.py, CI job, and issue form.

Requires executed allowlisted downstream tests (not presence-only passes).
Temp fixtures prove a failing downstream test makes the gate fail.
YAML-parse + diff-check: all pre-existing CI jobs must remain.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO_ROOT / "scripts" / "agent_family_quality_gate.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ISSUE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "acpx-agent-family.yml"

PRESERVED_JOBS = frozenset(
    {
        "project-gates",
        "backend-tests",
        "migration-tests",
        "frontend-tests",
        "frontend-build",
        "feature-manifest",
        "lint",
        "secret-scan",
        "docker-smoke",
        "extension-static-checks",
        "backend-contract-security-tests",
        "browser-use-smoke-contract",
        "stale-provider-compatibility",
        "recorder-watchdog",
        "package-artifact",
        "deploy-rollback-gates",
        "auth-benchmark",
    }
)

FORBIDDEN_CI_TOKENS = (
    "--trace",
    "--video",
    "--har",
    "storage-state",
    "trace.zip",
    ".har",
    "::add-mask::",
    "continue-on-error: true",
)

REQUIRED_ISSUE_FIELDS = (
    "idea",
    "threat model",
    "acpx owner stages",
    "deterministic tests",
    "budgets",
    "rollback",
    "evidence",
)

QUALITY_JOB_ID = "agent-family-quality-gate"


class _ActionsLoader(yaml.SafeLoader):
    """Keep GitHub Actions keys like `on:` as strings, not bools."""


for first_letter, resolvers in list(_ActionsLoader.yaml_implicit_resolvers.items()):
    _ActionsLoader.yaml_implicit_resolvers[first_letter] = [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]


def _load_gate():
    assert GATE_SCRIPT.is_file(), "scripts/agent_family_quality_gate.py is missing"
    # Force reload so iterative TDD picks up gate edits.
    name = "agent_family_quality_gate"
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GATE_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _workflow() -> dict[str, Any]:
    assert WORKFLOW.is_file(), "CI workflow is missing"
    data = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=_ActionsLoader)
    assert isinstance(data, dict)
    assert isinstance(data.get("jobs"), dict)
    return data


def _run_text(job: dict[str, Any]) -> str:
    parts: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "run" in step:
            parts.append(str(step["run"]))
    return "\n".join(parts)


def _flatten_job(job: dict[str, Any]) -> str:
    return yaml.dump(job, sort_keys=True).lower()


@pytest.fixture()
def gate():
    return _load_gate()


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_module_allows_subprocess_but_not_shell_or_network(gate) -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith("import ") or line.lstrip().startswith("from ")
    ]
    joined_imports = "\n".join(import_lines)
    # Subprocess is required for executed checks; must be present.
    assert "import subprocess" in joined_imports or "from subprocess" in joined_imports
    for needle in (
        "import socket",
        "import urllib",
        "import http.client",
        "import requests",
        "import smtplib",
    ):
        assert needle not in joined_imports, f"forbidden import: {needle}"
    assert "shell=True" not in source.replace('"shell=True"', "").replace(
        "'shell=True'", ""
    ) or "shell=False" in source
    # Runtime shell usage forbidden.
    assert re.search(r"subprocess\.(run|Popen|call)\([^)]*shell\s*=\s*True", source) is None
    assert re.search(r"os\.system\s*\(", source) is None
    for needle in ("chrome.management", "load_unpacked", "webstore"):
        assert needle not in source


def test_gate_ids_cover_required_surfaces(gate) -> None:
    required = {
        "skill_benchmark",
        "acpx_runtime_lock",
        "auth_benchmark_contracts",
        "soniox_adapter_contract",
        "budget_dedupe",
        "extension_skin",
        "secure_recorder",
        "pipeline_plan",
    }
    assert required <= set(gate.CHECK_IDS)


def test_default_execute_is_true(gate) -> None:
    """Default quality command must execute downstream checks."""
    assert "execute: bool = True" in GATE_SCRIPT.read_text(encoding="utf-8") or (
        "execute=True" in GATE_SCRIPT.read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _minimal_skill(root: Path) -> None:
    skill = root / ".agents" / "skills" / "acpx-agent-family"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: acpx-agent-family
            description: Build and verify Orca ACPX agent-family pipelines.
            ---

            # ACPX Agent Family

            Use this skill to plan and verify agent-family pipelines.
            """
        ),
        encoding="utf-8",
    )
    agents = skill / "agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text(
        textwrap.dedent(
            """\
            interface:
              display_name: "ACPX Agent Family"
              short_description: "Build and verify Orca ACPX agent-family pipelines"
              default_prompt: "Use $acpx-agent-family to verify the pipeline."
            """
        ),
        encoding="utf-8",
    )
    scripts = skill / "scripts"
    scripts.mkdir()
    # Gate looks for test_skill_scripts.py or test_skill_smoke.py
    (scripts / "test_skill_scripts.py").write_text(
        "def test_skill_smoke():\n    assert True\n",
        encoding="utf-8",
    )


def _minimal_extension(root: Path) -> None:
    ext = root / "extensions" / "cloak-profile-sync"
    for sub in ("background", "content", "lib", "scripts", "test", "host", "popup"):
        (ext / sub).mkdir(parents=True, exist_ok=True)
    (ext / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "CloakBrowser Profile Sync",
                "version": "0.1.0",
                "background": {"service_worker": "background/service-worker.js"},
            }
        ),
        encoding="utf-8",
    )
    (ext / "package.json").write_text(
        json.dumps(
            {
                "name": "cloak-profile-sync",
                "private": True,
                "type": "module",
                "scripts": {"test": "node --test test/*.test.js"},
            }
        ),
        encoding="utf-8",
    )
    (ext / "background" / "service-worker.js").write_text("// bg\n", encoding="utf-8")
    (ext / "content" / "recorder-content.js").write_text("// content\n", encoding="utf-8")
    (ext / "lib" / "action-recorder.js").write_text("// recorder\n", encoding="utf-8")
    (ext / "lib" / "local-control.js").write_text("// local\n", encoding="utf-8")
    (ext / "scripts" / "verify_against_manager.py").write_text(
        "print('ok')\n", encoding="utf-8"
    )
    (ext / "host" / "local_bridge.py").write_text("print('bridge')\n", encoding="utf-8")
    (ext / "test" / "action-recorder.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('ok', () => { assert.equal(1, 1); });\n",
        encoding="utf-8",
    )
    (ext / "test" / "local-control.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('ok', () => { assert.equal(2, 2); });\n",
        encoding="utf-8",
    )


def _minimal_acpx_runtime(root: Path) -> None:
    runtime = root / "deploy" / "acpx-runtime"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text(
        json.dumps(
            {
                "private": True,
                "dependencies": {"acpx": "0.12.1"},
                "engines": {"node": ">=22.13.0"},
                "overrides": {"@agentclientprotocol/sdk": "1.2.1"},
            }
        ),
        encoding="utf-8",
    )
    (runtime / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {}}),
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "acpx_runtime_lock.py").write_text(
        'ACPX_VERSION = "0.12.1"\n', encoding="utf-8"
    )
    (scripts / "test_acpx_runtime_lock.py").write_text(
        "def test_pin():\n    assert True\n", encoding="utf-8"
    )
    (scripts / "requirements-acpx-worker.in").write_text(
        "aiohttp>=3.12,<4\nmcp==1.28.1\nplaywright==1.61.0\n",
        encoding="utf-8",
    )
    (scripts / "requirements-acpx-worker.linux-x86_64.py312.txt").write_text(
        "aiohttp==3.14.3\nmcp==1.28.1\nplaywright==1.61.0\n",
        encoding="utf-8",
    )


def _minimal_auth(root: Path) -> None:
    auth = root / "benchmarks" / "auth"
    auth.mkdir(parents=True)
    (auth / "requirements.txt").write_text(
        "pytest==8.3.5\npytest-asyncio==0.25.3\nplaywright==1.61.0\n"
        "cryptography==49.0.0\nPyYAML==6.0.3\n",
        encoding="utf-8",
    )
    for name in (
        "test_ci_contract.py",
        "test_acpx_pipeline.py",
        "test_report.py",
        "test_policy.py",
    ):
        (auth / name).write_text(
            f"def test_{name.replace('.py', '')}():\n    assert True\n",
            encoding="utf-8",
        )
    (auth / "acpx_pipeline.py").write_text(
        "PIPELINE_STAGES = ('plan',)\n", encoding="utf-8"
    )
    (auth / "report.py").write_text("def build_report(*a, **k):\n    pass\n", encoding="utf-8")


def _minimal_soniox(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "soniox_adapter.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"Soniox STT adapter contract surface.\"\"\"
            SCHEMA = "cloakbrowser.soniox-adapter.v1"
            ALLOWED_MODELS = frozenset({"stt-rt-v5"})
            DEFAULT_MODEL = "stt-rt-v5"

            def probe():
                return {"ok": True, "metrics_only": True}
            """
        ),
        encoding="utf-8",
    )
    (scripts / "test_soniox_adapter.py").write_text(
        "from soniox_adapter import SCHEMA\n\ndef test_schema():\n    assert 'soniox' in SCHEMA\n",
        encoding="utf-8",
    )


def _minimal_budget(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "agent_family_budget_gate.py").write_text(
        "class BudgetMeter:\n    pass\nKNOWN_PROVIDERS = frozenset()\n"
        "SOURCE_PRECEDENCE = {}\n",
        encoding="utf-8",
    )
    (scripts / "test_agent_family_budget_gate.py").write_text(
        "def test_dedupe():\n    assert True\n", encoding="utf-8"
    )


def _minimal_secure_recorder(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "secure_recording_compiler.py").write_text(
        "RECORDING_SCHEMA = 'cloakbrowser.secure-action-recording.v1'\n"
        "FORBIDDEN_KEYS = {'password', 'token'}\n"
        "def _reject_forbidden(value):\n    pass\n",
        encoding="utf-8",
    )
    (scripts / "secure_recorder_watchdog.py").write_text(
        "def probe_http_health(*a, **k):\n    pass\n", encoding="utf-8"
    )
    (scripts / "test_secure_recording_compiler.py").write_text(
        "def test_compile():\n    assert True\n", encoding="utf-8"
    )
    (scripts / "test_secure_recorder_watchdog.py").write_text(
        "def test_watchdog():\n    assert True\n", encoding="utf-8"
    )
    (scripts / "test_acpx_secure_recorder_ci.py").write_text(
        "def test_ci():\n    assert True\n", encoding="utf-8"
    )


def _minimal_pipeline(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "agent_family_pipeline.py").write_text(
        textwrap.dedent(
            """\
            STAGE_PLAN = "plan"
            STAGE_FORGE = "forge"
            STAGE_SKILL_BENCHMARK = "skill-benchmark"
            STAGE_AUTH_BENCHMARK = "auth-benchmark"
            STAGE_EXTENSION_SKIN = "extension-skin"
            STAGE_SONIOX_CONTRACT = "soniox-contract"
            STAGE_TOKEN_COST = "token-cost"
            STAGE_IDR = "idr"
            STAGE_TRIBUNAL = "tribunal"
            STAGE_RELEASE = "release"
            PIPELINE_STAGES = (
                STAGE_PLAN, STAGE_FORGE, STAGE_SKILL_BENCHMARK, STAGE_AUTH_BENCHMARK,
                STAGE_EXTENSION_SKIN, STAGE_SONIOX_CONTRACT, STAGE_TOKEN_COST,
                STAGE_IDR, STAGE_TRIBUNAL, STAGE_RELEASE,
            )
            PARALLEL_GATE_STAGES = (
                STAGE_SKILL_BENCHMARK, STAGE_AUTH_BENCHMARK, STAGE_EXTENSION_SKIN,
                STAGE_SONIOX_CONTRACT, STAGE_TOKEN_COST,
            )
            DEFAULT_MODE = "plan"
            def build_agent_family_plan(mode="plan", **kwargs):
                class Plan:
                    pass
                p = Plan()
                p.mode = mode
                p.would_execute = mode == "execute"
                p.stages = []
                return p
            """
        ),
        encoding="utf-8",
    )
    (scripts / "test_agent_family_pipeline.py").write_text(
        "def test_order():\n    assert True\n", encoding="utf-8"
    )


def _complete_fixture_repo(root: Path) -> Path:
    _minimal_skill(root)
    _minimal_extension(root)
    _minimal_acpx_runtime(root)
    _minimal_auth(root)
    _minimal_soniox(root)
    _minimal_budget(root)
    _minimal_secure_recorder(root)
    _minimal_pipeline(root)
    return root


# ---------------------------------------------------------------------------
# Fixture-repo pass/fail with execution
# ---------------------------------------------------------------------------


def test_complete_fixture_repo_passes_with_execution(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "good")
    report = gate.run_quality_gate(repo_root=root, execute=True)
    assert report["passed"] is True, report["failures"]
    assert report["metrics_only"] is True
    assert report["execute"] is True
    assert report["executed_count"] == len(gate.CHECK_IDS)
    assert set(c["id"] for c in report["checks"]) == set(gate.CHECK_IDS)
    assert all(c["passed"] and c["executed"] and c["static_ok"] for c in report["checks"])
    assert report["failures"] == []


def test_presence_only_does_not_claim_pass_when_tests_fail(gate, tmp_path: Path) -> None:
    """A test file that fails must fail the gate even if static surfaces exist."""
    root = _complete_fixture_repo(tmp_path / "failing-budget")
    # Static surface still valid (BudgetMeter present, file names ok).
    (root / "scripts" / "test_agent_family_budget_gate.py").write_text(
        "def test_dedupe():\n    assert False, 'intentional downstream failure'\n",
        encoding="utf-8",
    )
    report = gate.run_quality_gate(
        repo_root=root, gates=["budget_dedupe"], execute=True
    )
    assert report["passed"] is False
    check = report["checks"][0]
    assert check["static_ok"] is True
    assert check["executed"] is True
    assert check["exit_code"] not in (0, None)
    assert any("budget_dedupe" in f for f in report["failures"])


def test_failing_secure_recorder_test_fails_gate(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "failing-recorder")
    (root / "scripts" / "test_secure_recording_compiler.py").write_text(
        "def test_compile():\n    assert 0, 'compiler regression'\n",
        encoding="utf-8",
    )
    report = gate.run_quality_gate(
        repo_root=root, gates=["secure_recorder"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["executed"] is True
    assert report["checks"][0]["exit_code"] != 0


def test_failing_pipeline_test_fails_gate(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "failing-pipeline")
    (root / "scripts" / "test_agent_family_pipeline.py").write_text(
        "def test_order():\n    raise AssertionError('pipeline broken')\n",
        encoding="utf-8",
    )
    report = gate.run_quality_gate(
        repo_root=root, gates=["pipeline_plan"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["executed"] is True


def test_failing_soniox_test_fails_gate(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "failing-soniox")
    (root / "scripts" / "test_soniox_adapter.py").write_text(
        "def test_schema():\n    assert False\n",
        encoding="utf-8",
    )
    report = gate.run_quality_gate(
        repo_root=root, gates=["soniox_adapter_contract"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["executed"] is True


def test_failing_extension_test_fails_gate(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "failing-ext")
    (root / "extensions" / "cloak-profile-sync" / "test" / "action-recorder.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('fail', () => { assert.equal(1, 2); });\n",
        encoding="utf-8",
    )
    report = gate.run_quality_gate(
        repo_root=root, gates=["extension_skin"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["executed"] is True
    assert report["checks"][0]["exit_code"] != 0


def test_failing_skill_test_fails_gate(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "failing-skill")
    skill_test = (
        root
        / ".agents"
        / "skills"
        / "acpx-agent-family"
        / "scripts"
        / "test_skill_scripts.py"
    )
    skill_test.write_text("def test_skill_smoke():\n    assert False\n", encoding="utf-8")
    report = gate.run_quality_gate(
        repo_root=root, gates=["skill_benchmark"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["executed"] is True


def test_missing_extension_manifest_fails_closed(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "no-manifest")
    (root / "extensions" / "cloak-profile-sync" / "manifest.json").unlink()
    report = gate.run_quality_gate(
        repo_root=root, gates=["extension_skin"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["static_ok"] is False
    assert report["checks"][0]["executed"] is False


def test_missing_soniox_adapter_fails_closed(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "no-soniox")
    (root / "scripts" / "soniox_adapter.py").unlink()
    (root / "scripts" / "test_soniox_adapter.py").unlink()
    report = gate.run_quality_gate(
        repo_root=root, gates=["soniox_adapter_contract"], execute=True
    )
    assert report["passed"] is False


def test_missing_skill_frontmatter_fails_closed(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "bad-skill")
    skill_md = root / ".agents" / "skills" / "acpx-agent-family" / "SKILL.md"
    skill_md.write_text("# no frontmatter\n", encoding="utf-8")
    report = gate.run_quality_gate(
        repo_root=root, gates=["skill_benchmark"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["static_ok"] is False


def test_auth_requirements_must_be_pinned(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "float-auth")
    req = root / "benchmarks" / "auth" / "requirements.txt"
    req.write_text("pytest\nplaywright>=1.0\n", encoding="utf-8")
    report = gate.run_quality_gate(
        repo_root=root, gates=["auth_benchmark_contracts"], execute=True
    )
    assert report["passed"] is False
    assert report["checks"][0]["static_ok"] is False


def test_does_not_install_extension_or_mutate_profiles(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "immutable")
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
    }
    report = gate.run_quality_gate(repo_root=root, execute=True)
    assert report["passed"] is True, report["failures"]
    after_files = {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
    }
    assert after_files == set(before)
    for rel, data in before.items():
        assert (root / rel).read_bytes() == data


def test_report_is_metrics_only_and_redacted(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "metrics")
    report = gate.run_quality_gate(repo_root=root, execute=True)
    blob = json.dumps(report).lower()
    for needle in (
        "password=",
        "api_key",
        "authorization",
        "bearer ",
        "cookie=",
        "secretref-",
        "sk-",
    ):
        assert needle not in blob
    assert report.get("metrics_only") is True


def test_write_report_sets_0600(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "perms")
    report = gate.run_quality_gate(repo_root=root, execute=True)
    out = tmp_path / "out" / "quality-report.json"
    gate.write_report(out, report)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_write_report_failure_is_not_swallowed(gate, tmp_path: Path, monkeypatch) -> None:
    root = _complete_fixture_repo(tmp_path / "write-fail")
    report = gate.run_quality_gate(repo_root=root, gates=["budget_dedupe"], execute=True)
    assert report["passed"] is True

    def boom(*_a, **_k):
        raise OSError("permission denied for /tmp/secret-path")

    monkeypatch.setattr(gate.os, "open", boom)
    with pytest.raises(gate.GateError) as exc:
        gate.write_report(tmp_path / "blocked.json", report)
    msg = str(exc.value).lower()
    assert "report write failed" in msg or "permission" in msg


def test_cli_write_failure_returns_error_and_stderr(
    gate, tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _complete_fixture_repo(tmp_path / "cli-write-fail")
    out = tmp_path / "out.json"

    def boom(*_a, **_k):
        raise OSError("disk full at /home/coder/secret")

    monkeypatch.setattr(gate.os, "open", boom)
    code = gate.main(["--repo", str(root), "--gate", "budget_dedupe", "--output", str(out)])
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "report write failed" in err or "write" in err
    assert "secret" not in err or "[redacted]" in err


def test_cli_writes_report_and_exit_codes(gate, tmp_path: Path) -> None:
    good = _complete_fixture_repo(tmp_path / "cli-good")
    out = tmp_path / "good-report.json"
    code = gate.main(["--repo", str(good), "--output", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["executed_count"] >= 1
    assert stat.S_IMODE(out.stat().st_mode) == 0o600

    bad = _complete_fixture_repo(tmp_path / "cli-bad")
    (bad / "scripts" / "test_agent_family_budget_gate.py").write_text(
        "def test_dedupe():\n    assert False\n", encoding="utf-8"
    )
    out_bad = tmp_path / "bad-report.json"
    code_bad = gate.main(
        ["--repo", str(bad), "--gate", "budget_dedupe", "--output", str(out_bad)]
    )
    assert code_bad == 1
    assert json.loads(out_bad.read_text(encoding="utf-8"))["passed"] is False


def test_single_gate_filter_executes(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "filter")
    report = gate.run_quality_gate(
        repo_root=root, gates=["extension_skin"], execute=True
    )
    assert report["passed"] is True
    assert [c["id"] for c in report["checks"]] == ["extension_skin"]
    assert report["checks"][0]["executed"] is True


def test_extension_skin_discovery_from_cloak_profile_sync(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "discover")
    surfaces = gate.discover_extension_skins(root)
    assert any(s.name == "cloak-profile-sync" for s in surfaces)


def test_does_not_read_env_secrets(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "env")
    os.environ["OPENAI_API_KEY"] = "sk-should-never-appear"
    os.environ["SONIOX_API_KEY"] = "soniox-secret-value"
    try:
        report = gate.run_quality_gate(
            repo_root=root, gates=["budget_dedupe"], execute=True
        )
        blob = json.dumps(report)
        assert "sk-should-never-appear" not in blob
        assert "soniox-secret-value" not in blob
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("SONIOX_API_KEY", None)


def test_no_shell_true_in_allowlisted_runner(gate, tmp_path: Path) -> None:
    root = _complete_fixture_repo(tmp_path / "shell")
    # Spy via running a real command and ensuring module uses shell=False.
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "shell=False" in source
    result = gate.run_allowlisted_command(
        [sys.executable, "-c", "print('ok')"],
        cwd=root,
        timeout_s=10,
    )
    assert result.exit_code == 0
    assert result.timed_out is False


# ---------------------------------------------------------------------------
# Real repository executed checks
# ---------------------------------------------------------------------------


def test_real_repo_extension_skin_executes(gate) -> None:
    result = gate.run_one_check("extension_skin", REPO_ROOT, execute=True)
    assert result.static_ok is True
    assert result.executed is True
    assert result.passed is True, result.detail


def test_real_repo_executed_core_gates(gate) -> None:
    for check_id in (
        "acpx_runtime_lock",
        "auth_benchmark_contracts",
        "budget_dedupe",
        "secure_recorder",
        "pipeline_plan",
        "soniox_adapter_contract",
    ):
        result = gate.run_one_check(check_id, REPO_ROOT, execute=True)
        assert result.executed is True, check_id
        assert result.static_ok is True, f"{check_id}: {result.detail}"
        assert result.passed is True, f"{check_id}: {result.detail}"


def test_real_repo_skill_is_executed_not_presence_only(gate) -> None:
    result = gate.run_one_check("skill_benchmark", REPO_ROOT, execute=True)
    assert result.static_ok is True
    # Must attempt execution (not presence-only pass).
    assert result.executed is True or "executed_failed" in result.detail
    # If skill tests are currently red, gate correctly fails; never silent pass.
    if result.passed:
        assert result.executed is True
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# CI workflow contracts
# ---------------------------------------------------------------------------


def test_existing_ci_jobs_are_preserved() -> None:
    jobs = _workflow()["jobs"]
    missing = PRESERVED_JOBS - set(jobs)
    assert not missing, f"existing CI jobs were removed: {sorted(missing)}"
    assert QUALITY_JOB_ID in jobs


def test_quality_gate_ci_job_is_deterministic_and_safe() -> None:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert f"{QUALITY_JOB_ID}:" in workflow_text
    job = _workflow()["jobs"][QUALITY_JOB_ID]
    runs = _run_text(job)
    flat = _flatten_job(job)

    assert "agent_family_quality_gate" in runs
    assert "test_agent_family_quality_gate.py" in runs
    assert "PYTHONPATH=." in runs

    assert "secrets." not in flat
    job_slice = workflow_text.split(f"  {QUALITY_JOB_ID}:", 1)[1].split("\n  ", 1)[0]
    assert "${{ secrets" not in job_slice.lower()

    for forbidden in FORBIDDEN_CI_TOKENS:
        assert forbidden not in flat, f"quality gate CI must not use {forbidden!r}"
        assert forbidden not in runs.lower()

    assert "continue-on-error" not in flat
    assert "|| true" not in runs
    assert "--static-only" not in runs

    uploads = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(uploads) >= 1
    for upload in uploads:
        path = str(upload.get("with", {}).get("path", ""))
        assert "report" in path or "metrics" in path or "quality" in path
        assert "trace" not in path.lower()
        assert "video" not in path.lower()
        assert "har" not in path.lower()
        assert "storage" not in path.lower()

    assert copy.deepcopy(job)["runs-on"] in {
        "ubuntu-latest",
        "ubuntu-24.04",
        "ubuntu-22.04",
    }


def test_quality_gate_job_uses_only_fully_pinned_deps() -> None:
    job = _workflow()["jobs"][QUALITY_JOB_ID]
    runs = _run_text(job)
    flat = _flatten_job(job)
    # Exact-pinned auth requirements only — no floating backend requirements.
    assert "benchmarks/auth/requirements.txt" in runs
    assert "backend/requirements-dev.txt" not in runs
    assert "backend/requirements-dev.txt" not in flat
    # cache-dependency-path must also be pinned auth requirements when present
    job_yaml = yaml.dump(job)
    if "cache-dependency-path" in job_yaml:
        assert "benchmarks/auth/requirements.txt" in job_yaml
        assert "backend/requirements-dev.txt" not in job_yaml
    assert "pip install" in runs.lower() or "python -m pip install" in runs
    # No unpinned pip installs like `pip install pytest` without ==
    for line in runs.splitlines():
        if "pip install" in line and "-r " not in line and "requirements" not in line:
            assert "==" in line, f"unpinned pip install line: {line}"


# ---------------------------------------------------------------------------
# Issue template contracts
# ---------------------------------------------------------------------------


def test_issue_template_exists_and_parses() -> None:
    assert ISSUE_TEMPLATE.is_file(), "issue template missing"
    data = yaml.safe_load(ISSUE_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("name")
    assert data.get("description")
    body = data.get("body")
    assert isinstance(body, list) and body


def test_issue_template_captures_required_fields() -> None:
    text = ISSUE_TEMPLATE.read_text(encoding="utf-8").lower()
    for field in REQUIRED_ISSUE_FIELDS:
        assert field in text, f"issue template missing field: {field}"
    data = yaml.safe_load(ISSUE_TEMPLATE.read_text(encoding="utf-8"))
    labels = " ".join(str(x) for x in (data.get("labels") or [])).lower()
    assert (
        "acpx" in data.get("name", "").lower()
        or "acpx" in labels
        or "agent" in data.get("name", "").lower()
    )
    rendered = yaml.dump(data).lower()
    for field in REQUIRED_ISSUE_FIELDS:
        assert field in rendered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
