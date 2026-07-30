"""TDD contract tests for the auth ACPX pipeline CLI entrypoint."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from benchmarks.auth import run_acpx_pipeline as cli
from benchmarks.auth.acpx_pipeline import (
    AGENT_ROUTE,
    DEFAULT_MODE,
    MAX_CONCURRENCY,
    PIPELINE_STAGES,
    pinned_acpx_executable,
)


def _write_private_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def _permission_policy(tmp_path: Path) -> Path:
    return _write_private_json(
        tmp_path / "permission-policy.json",
        {
            "autoApprove": [],
            "autoDeny": ["*"],
            "escalate": [],
            "defaultAction": "deny",
        },
    )


def _mcp_config(tmp_path: Path) -> Path:
    return _write_private_json(
        tmp_path / "mcp.json",
        {
            "mcpServers": [
                {
                    "name": "cloakbrowser",
                    "command": "cbm-mcp",
                    "args": ["--stdio"],
                }
            ]
        },
    )


def test_cli_defaults_to_plan_mode_and_safe_parallel(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    output = tmp_path / "out" / "pipeline-report.json"
    code = cli.main(["--output", str(output)])
    assert code == 0
    printed = capsys.readouterr().out.strip()
    # Only mode passed stage count route — nothing else
    assert printed == (
        f"mode={DEFAULT_MODE} passed=true "
        f"stage_count={len(PIPELINE_STAGES)} route={AGENT_ROUTE}"
    )
    assert "prompt" not in printed.lower()
    assert "stdout" not in printed.lower()
    assert "stderr" not in printed.lower()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "plan"
    assert payload["passed"] is True
    assert payload["stage_count"] == len(PIPELINE_STAGES)
    assert payload["agent_route"] == AGENT_ROUTE
    assert payload["max_concurrency"] == MAX_CONCURRENCY
    assert Path(payload["acpx_executable"]) == pinned_acpx_executable().resolve()


def test_cli_rejects_parallel_above_cap(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--parallel", "5"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "parallel" in err.lower() or "1" in err


def test_cli_execute_fails_closed_without_policy_and_mcp(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "report.json"
    code = cli.main(["--mode", "execute", "--output", str(output)])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "permission" in err or "mcp" in err or "execute" in err
    assert not output.exists()


def test_cli_execute_requires_absolute_policy_paths(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    # Relative paths must be rejected even if files exist when cwd is tmp_path
    code = cli.main(
        [
            "--mode",
            "execute",
            "--permission-policy",
            policy.name,
            "--mcp-config",
            mcp.name,
            "--output",
            str(tmp_path / "r.json"),
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "absolute" in err


def test_cli_dry_run_writes_private_metrics_only_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    parent = tmp_path / "artifacts" / "auth-acpx-pipeline"
    output = parent / "report.json"
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    code = cli.main(
        [
            "--mode",
            "dry-run",
            "--parallel",
            "2",
            "--permission-policy",
            str(policy),
            "--mcp-config",
            str(mcp),
            "--output",
            str(output),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("mode=dry-run passed=true stage_count=")
    assert f"route={AGENT_ROUTE}" in printed
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    # metrics-only: stages status, no prompt bodies / command streams
    assert "prompt_brief" not in text
    assert "stdin_text" not in text
    assert "stdout" not in text
    assert "stderr" not in text
    assert "password=" not in text.lower()
    assert "authorization" not in text.lower()
    assert "cookie=" not in text.lower()
    assert "bearer " not in text.lower()
    assert payload["mode"] == "dry-run"
    assert payload["max_concurrency"] == 2
    assert isinstance(payload["stages"], list)
    assert len(payload["stages"]) == payload["stage_count"]
    for stage in payload["stages"]:
        assert set(stage) <= {"name", "status", "kind"}
        assert "commands" not in stage
        assert "prompt" not in stage
        assert "detail" not in stage  # keep report metrics-only, not free text dumps


def test_cli_report_contains_no_secret_material(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "private" / "metrics.json"
    code = cli.main(["--mode", "plan", "--output", str(output)])
    assert code == 0
    text = output.read_text(encoding="utf-8")
    for needle in (
        "password=",
        "authorization",
        "bearer ",
        "cookie=",
        "clientDataJSON",
        "privateKey",
        "accounts.google.com",
        "PROMPT_BODY",
        "-----BEGIN",
    ):
        assert needle.lower() not in text.lower()
    # stdout must remain the one-line summary only
    out_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(out_lines) == 1


def test_cli_uses_pinned_acpx_by_default(tmp_path: Path) -> None:
    output = tmp_path / "r.json"
    code = cli.main(["--output", str(output)])
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["acpx_executable"] == str(pinned_acpx_executable().resolve())
    assert payload["acpx_executable"].endswith(
        "deploy/acpx-runtime/node_modules/acpx/dist/cli.js"
    )
    assert "npx" not in payload["acpx_executable"]


def test_cli_execute_fail_closed_when_acpx_override_missing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    missing = tmp_path / "missing-acpx"
    code = cli.main(
        [
            "--mode",
            "execute",
            "--permission-policy",
            str(policy.resolve()),
            "--mcp-config",
            str(mcp.resolve()),
            "--acpx",
            str(missing),
            "--output",
            str(tmp_path / "r.json"),
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "missing" in err or "executable" in err or "acpx" in err


def test_build_metrics_payload_omits_commands_and_streams() -> None:
    from benchmarks.auth.acpx_pipeline import AuthAcpxPipelineReport, StageResult

    report = AuthAcpxPipelineReport(
        mode="plan",
        passed=True,
        stages=(
            StageResult(
                name="plan",
                status="planned",
                detail="agent stage routed via acpx:grok-build",
                commands=(("acpx", "would-leak"),),
            ),
        ),
        failures=(),
        max_concurrency=2,
        notes=("default non-executing mode; pass mode='execute' to run",),
    )
    payload = cli.build_metrics_payload(
        report,
        acpx_executable=str(pinned_acpx_executable()),
    )
    encoded = json.dumps(payload)
    assert "would-leak" not in encoded
    assert "commands" not in encoded
    assert "stdout" not in encoded
    assert "stderr" not in encoded
    assert "prompt" not in encoded
    assert payload["stage_count"] == 1
    assert payload["stages"] == [{"name": "plan", "status": "planned", "kind": "agent"}]


def test_write_private_report_modes(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "report.json"
    cli.write_private_report(path, {"mode": "plan", "passed": True, "stage_count": 1})
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # ensure no world/group bits
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert os.path.expanduser("~") not in path.read_text(encoding="utf-8")
