"""TDD contract tests for the ACPX-backed auth benchmark pipeline."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from benchmarks.auth.acpx_pipeline import (
    AGENT_ROUTE,
    AGENT_STAGES,
    DEFAULT_AGENT,
    DEFAULT_HARNESS,
    DEFAULT_MODE,
    LANE_REPORT_PATHS,
    LANE_SCENARIOS,
    MAX_CONCURRENCY,
    PARALLEL_BROWSER_LANES,
    PINNED_ACPX_RELATIVE_PATH,
    PIPELINE_STAGES,
    REQUIRED_ACPX_VERSION,
    STAGE_FORGE,
    STAGE_IDR_SECURITY_GATE,
    STAGE_OAUTH_LANE,
    STAGE_PASSWORD_LANE,
    STAGE_PLAN,
    STAGE_RELEASE_REPORT,
    STAGE_TRIBUNAL,
    STAGE_WEBAUTHN_LANE,
    AuthAcpxPipelineError,
    agent_stage_routes,
    build_agent_stage_commands,
    build_auth_acpx_pipeline_plan,
    build_browser_lane_command,
    build_plan_or_dry_run,
    lane_report_path,
    pinned_acpx_executable,
    pipeline_stage_names,
    report_to_text,
    resolve_acpx_executable,
    run_auth_acpx_pipeline,
    validate_lane_scenario_coverage,
)
from benchmarks.auth.live_runner import DEFAULT_SCENARIOS
from scripts.acpx_runner import ACPX_VERSION, derive_session_name


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


def test_pipeline_has_named_plan_forge_lanes_idr_tribunal_and_release_report() -> None:
    plan = build_auth_acpx_pipeline_plan(mode="plan")
    names = pipeline_stage_names(plan)
    assert names == PIPELINE_STAGES
    assert names[0] == STAGE_PLAN
    assert names[1] == STAGE_FORGE
    assert STAGE_PASSWORD_LANE in names
    assert STAGE_OAUTH_LANE in names
    assert STAGE_WEBAUTHN_LANE in names
    assert names[-3] == STAGE_IDR_SECURITY_GATE
    assert names[-2] == STAGE_TRIBUNAL
    assert names[-1] == STAGE_RELEASE_REPORT


def test_default_mode_is_plan_and_does_not_execute() -> None:
    assert DEFAULT_MODE in {"plan", "dry-run"}
    plan = build_auth_acpx_pipeline_plan()
    assert plan.mode == "plan"
    assert plan.would_execute is False


def test_build_plan_or_dry_run_refuses_execute() -> None:
    with pytest.raises(AuthAcpxPipelineError, match="refuses execute"):
        build_plan_or_dry_run(mode="execute")


def test_execute_mode_is_explicit_and_requires_acpx_policy_files(tmp_path: Path) -> None:
    with pytest.raises(AuthAcpxPipelineError, match="permission_policy"):
        build_auth_acpx_pipeline_plan(mode="execute")

    plan = build_auth_acpx_pipeline_plan(
        mode="execute",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    assert plan.mode == "execute"
    assert plan.would_execute is True
    agent_stages = [s for s in plan.stages if s.kind == "agent"]
    assert agent_stages
    assert all(s.commands for s in agent_stages)


def test_every_agent_stage_routes_via_acpx_and_grok_build(tmp_path: Path) -> None:
    plan = build_auth_acpx_pipeline_plan(
        mode="dry-run",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    routes = agent_stage_routes(plan)
    assert set(routes) == AGENT_STAGES
    for stage_name, route in routes.items():
        assert route == AGENT_ROUTE
        assert DEFAULT_HARNESS in route
        assert DEFAULT_AGENT in route
        stage = next(s for s in plan.stages if s.name == stage_name)
        assert stage.harness == "acpx"
        assert stage.agent == "grok-build"
        assert stage.route == "acpx:grok-build"
        # ensure/prompt/close argv lists all pin grok-build
        for command in stage.commands:
            assert "grok-build" in command


def test_rejects_non_grok_build_agent(tmp_path: Path) -> None:
    with pytest.raises(AuthAcpxPipelineError, match="grok-build"):
        build_auth_acpx_pipeline_plan(agent="claude")


def test_parallel_password_oauth_webauthn_lanes() -> None:
    plan = build_auth_acpx_pipeline_plan(mode="plan")
    assert plan.parallel_lanes == PARALLEL_BROWSER_LANES
    lanes = [s for s in plan.stages if s.kind == "browser"]
    assert {s.name for s in lanes} == set(PARALLEL_BROWSER_LANES)
    assert all(s.parallel_group == "auth_lanes" for s in lanes)
    assert LANE_SCENARIOS[STAGE_PASSWORD_LANE]
    assert LANE_SCENARIOS[STAGE_OAUTH_LANE]
    assert LANE_SCENARIOS[STAGE_WEBAUTHN_LANE]
    assert all(
        s.startswith(("password_", "otp_")) for s in LANE_SCENARIOS[STAGE_PASSWORD_LANE]
    )
    assert all(s.startswith("oauth_") for s in LANE_SCENARIOS[STAGE_OAUTH_LANE])
    assert all(s.startswith("passkey_") for s in LANE_SCENARIOS[STAGE_WEBAUTHN_LANE])
    # OAuth+passkey 2FA and conditional mediation must live in the matrix.
    assert "oauth_passkey_2fa_success" in LANE_SCENARIOS[STAGE_OAUTH_LANE]
    assert "oauth_passkey_2fa_popup_reopen" in LANE_SCENARIOS[STAGE_OAUTH_LANE]
    assert "passkey_conditional_mediation" in LANE_SCENARIOS[STAGE_WEBAUTHN_LANE]


def test_lane_scenarios_cover_default_scenarios_exactly_once() -> None:
    """Every live_runner.DEFAULT_SCENARIOS id is assigned to exactly one lane."""
    assigned = [sid for scenarios in LANE_SCENARIOS.values() for sid in scenarios]
    assert sorted(assigned) == sorted(DEFAULT_SCENARIOS)
    assert len(assigned) == len(set(assigned))
    # Public validator used at plan build time
    validate_lane_scenario_coverage()
    # Missing or duplicate must fail closed
    with pytest.raises(AuthAcpxPipelineError, match="exactly once|missing="):
        validate_lane_scenario_coverage(
            lane_scenarios={
                STAGE_PASSWORD_LANE: DEFAULT_SCENARIOS[:2],
                STAGE_OAUTH_LANE: DEFAULT_SCENARIOS[2:4],
                STAGE_WEBAUTHN_LANE: DEFAULT_SCENARIOS[4:6],
            }
        )
    with pytest.raises(AuthAcpxPipelineError, match="exactly once"):
        validate_lane_scenario_coverage(
            lane_scenarios={
                STAGE_PASSWORD_LANE: (DEFAULT_SCENARIOS[0], DEFAULT_SCENARIOS[0]),
                STAGE_OAUTH_LANE: DEFAULT_SCENARIOS[1:2],
                STAGE_WEBAUTHN_LANE: DEFAULT_SCENARIOS[2:3],
            }
        )


def test_parallel_browser_lanes_use_distinct_private_report_paths() -> None:
    paths = [lane_report_path(lane) for lane in PARALLEL_BROWSER_LANES]
    assert len(paths) == len(set(paths))
    assert set(LANE_REPORT_PATHS) == set(PARALLEL_BROWSER_LANES)
    # Never race on the shared default report path
    shared = Path("artifacts/auth-benchmark/report.json")
    assert shared not in paths
    assert all(path.parent == Path("artifacts/auth-benchmark/private") for path in paths)

    commands = [
        build_browser_lane_command(
            lane=lane,
            repo_root=Path(__file__).resolve().parents[2],
            python_executable="/usr/bin/python3",
        )
        for lane in PARALLEL_BROWSER_LANES
    ]
    outputs: list[str] = []
    for command in commands:
        assert "--output" in command
        idx = command.index("--output")
        outputs.append(command[idx + 1])
        assert command[idx + 1] != str(shared)
        # argv-only path, not a shell redirect
        assert not any(">" in part for part in command)
    assert len(outputs) == len(set(outputs))
    assert set(outputs) == {str(path) for path in paths}

    plan = build_auth_acpx_pipeline_plan(mode="plan")
    plan_outputs: list[str] = []
    for stage in plan.stages:
        if stage.kind != "browser":
            continue
        command = list(stage.commands[0])
        idx = command.index("--output")
        plan_outputs.append(command[idx + 1])
    assert len(plan_outputs) == 3
    assert len(set(plan_outputs)) == 3


def test_max_concurrency_hard_cap_is_four() -> None:
    assert MAX_CONCURRENCY == 4
    plan = build_auth_acpx_pipeline_plan(max_concurrency=4)
    assert plan.max_concurrency == 4
    with pytest.raises(AuthAcpxPipelineError, match="hard cap"):
        build_auth_acpx_pipeline_plan(max_concurrency=5)
    with pytest.raises(AuthAcpxPipelineError, match=">= 1"):
        build_auth_acpx_pipeline_plan(max_concurrency=0)


def test_browser_commands_are_argv_lists_without_shell(tmp_path: Path) -> None:
    command = build_browser_lane_command(
        lane=STAGE_PASSWORD_LANE,
        repo_root=Path(__file__).resolve().parents[2],
        python_executable="/usr/bin/python3",
        max_parallel=2,
    )
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[0] == "/usr/bin/python3"
    assert command[1].endswith("run_auth_benchmark.py")
    assert "--parallel" in command
    assert "2" in command
    assert "--scenario" in command
    assert "password_success" in command
    # never a single shell string
    assert not any("&&" in part or "|" in part or ";" in part for part in command)


def test_browser_lane_rejects_origin_urls_and_google() -> None:
    with pytest.raises(AuthAcpxPipelineError, match="scenario ids"):
        build_browser_lane_command(
            lane=STAGE_OAUTH_LANE,
            scenarios=("https://accounts.google.com",),
        )
    with pytest.raises(AuthAcpxPipelineError, match="Google"):
        build_browser_lane_command(
            lane=STAGE_OAUTH_LANE,
            scenarios=("accounts.google.com",),
        )


def test_agent_stage_commands_reuse_acpx_runner_contracts(tmp_path: Path) -> None:
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    built = build_agent_stage_commands(
        stage=STAGE_PLAN,
        cwd=tmp_path,
        executable="/opt/acpx",
        permission_policy=policy,
        mcp_config=mcp,
        task_session_id="auth-unit-test-plan",
    )
    assert built["harness"] == "acpx"
    assert built["agent"] == "grok-build"
    assert built["route"] == "acpx:grok-build"
    assert built["prompt_transport"] == "stdin"
    assert built["persist_prompt"] is False
    assert built["session_name"] == derive_session_name("auth-unit-test-plan")
    ensure = built["commands"]["ensure"]
    prompt = built["commands"]["prompt"]
    close = built["commands"]["close"]
    assert ensure[0] == "/opt/acpx"
    assert "sessions" in ensure and "ensure" in ensure
    assert prompt[0] == "/opt/acpx"
    assert prompt[-2:] == ["--file", "-"]
    assert "grok-build" in prompt
    assert close[-2:] == ["close", built["session_name"]]
    # raw prompt must not appear in argv
    assert built["prompt_brief"] not in " ".join(prompt)


def test_default_acpx_executable_is_repo_pinned_cli_js() -> None:
    pinned = pinned_acpx_executable()
    assert PINNED_ACPX_RELATIVE_PATH.as_posix().endswith(
        "deploy/acpx-runtime/node_modules/acpx/dist/cli.js"
    )
    assert pinned == Path(__file__).resolve().parents[2] / PINNED_ACPX_RELATIVE_PATH
    assert REQUIRED_ACPX_VERSION == ACPX_VERSION == "0.12.1"
    resolved = resolve_acpx_executable()
    assert resolved == str(pinned.resolve())
    assert Path(resolved).name == "cli.js"
    assert "node_modules/acpx/dist/cli.js" in resolved.replace("\\", "/")
    # Never default to bare PATH names or npx
    assert resolved not in {"acpx", "npx"}
    assert not resolved.startswith("npx")


def test_resolve_acpx_rejects_unpinned_npx_and_global_acpx_names() -> None:
    with pytest.raises(AuthAcpxPipelineError, match="unpinned|npx|global"):
        resolve_acpx_executable(acpx_executable="npx")
    with pytest.raises(AuthAcpxPipelineError, match="unpinned|npx|global"):
        resolve_acpx_executable(acpx_executable="acpx")
    with pytest.raises(AuthAcpxPipelineError, match="unpinned|npx|global"):
        resolve_acpx_executable(acpx_executable="npx acpx")


def test_resolve_acpx_allows_explicit_override_path(tmp_path: Path) -> None:
    override = tmp_path / "custom-acpx"
    override.write_text("#!/bin/sh\necho 0.12.1\n", encoding="utf-8")
    override.chmod(0o755)
    resolved = resolve_acpx_executable(acpx_executable=override)
    assert resolved == str(override.resolve())
    required = resolve_acpx_executable(
        acpx_executable=override, require_executable=True
    )
    assert required == str(override.resolve())


def test_execute_mode_fails_closed_when_pinned_acpx_missing(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty-repo"
    empty_root.mkdir()
    with pytest.raises(AuthAcpxPipelineError, match="missing or not executable"):
        build_auth_acpx_pipeline_plan(
            mode="execute",
            repo_root=empty_root,
            cwd=tmp_path,
            permission_policy=_permission_policy(tmp_path),
            mcp_config=_mcp_config(tmp_path),
        )


def test_execute_mode_fails_closed_when_override_not_executable(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-acpx"
    with pytest.raises(AuthAcpxPipelineError, match="missing or not executable"):
        build_auth_acpx_pipeline_plan(
            mode="execute",
            acpx_executable=missing,
            cwd=tmp_path,
            permission_policy=_permission_policy(tmp_path),
            mcp_config=_mcp_config(tmp_path),
        )
    non_exec = tmp_path / "non-exec-acpx"
    non_exec.write_text("not executable", encoding="utf-8")
    non_exec.chmod(0o644)
    with pytest.raises(AuthAcpxPipelineError, match="missing or not executable"):
        build_auth_acpx_pipeline_plan(
            mode="execute",
            acpx_executable=non_exec,
            cwd=tmp_path,
            permission_policy=_permission_policy(tmp_path),
            mcp_config=_mcp_config(tmp_path),
        )


def test_plan_mode_defaults_to_pinned_acpx_for_version_compatible_argv(
    tmp_path: Path,
) -> None:
    pinned = str(pinned_acpx_executable().resolve())
    plan = build_auth_acpx_pipeline_plan(
        mode="plan",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    assert plan.acpx_executable == pinned
    agent_stages = [s for s in plan.stages if s.kind == "agent"]
    assert agent_stages
    for stage in agent_stages:
        assert stage.commands, stage.name
        ensure, prompt, close = stage.commands
        assert ensure[0] == pinned
        assert "sessions" in ensure and "ensure" in ensure
        assert "--json-strict" in ensure
        assert "grok-build" in ensure
        assert prompt[0] == pinned
        assert prompt[-2:] == ("--file", "-")
        assert "grok-build" in prompt
        assert close[0] == pinned
        assert "close" in close
        # No npx / bare global fallback in argv
        joined = " ".join((*ensure, *prompt, *close))
        assert "npx" not in joined.split()
        assert joined.split()[0] != "acpx"


def test_no_raw_prompt_persistence_on_disk(tmp_path: Path) -> None:
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    plan = build_auth_acpx_pipeline_plan(
        mode="dry-run",
        cwd=tmp_path,
        permission_policy=policy,
        mcp_config=mcp,
        task_session_id="auth-no-persist",
    )
    # Planning must not write prompt bodies into the worktree
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in {".txt", ".prompt", ".md"}:
            text = path.read_text(encoding="utf-8")
            for stage in plan.stages:
                if stage.prompt_brief:
                    assert stage.prompt_brief not in text
    # Only the policy/mcp private files we created should exist as json
    for stage in plan.stages:
        if stage.prompt_brief:
            assert "password=" not in stage.prompt_brief.lower()
            assert "bearer" not in stage.prompt_brief.lower()


@pytest.mark.asyncio
async def test_dry_run_does_not_invoke_command_runner(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(command))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_auth_acpx_pipeline(
        mode="dry-run",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    assert report.mode == "dry-run"
    assert report.passed is True
    assert calls == []
    assert all(s.status == "dry_run" for s in report.stages)
    assert len(report.stages) == len(PIPELINE_STAGES)


@pytest.mark.asyncio
async def test_plan_mode_is_default_and_non_executing() -> None:
    calls: list[list[str]] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(command))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_auth_acpx_pipeline(command_runner=runner)
    assert report.mode == "plan"
    assert calls == []
    assert all(s.status == "planned" for s in report.stages)


@pytest.mark.asyncio
async def test_execute_runs_agent_then_parallel_lanes_then_gates(tmp_path: Path) -> None:
    seen: list[str] = []
    stdin_payloads: list[str | None] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        stdin_payloads.append(kwargs.get("stdin_text"))
        joined = " ".join(command)
        if "sessions" in command and "ensure" in command:
            seen.append("ensure")
        elif command[-2:] == ["--file", "-"]:
            seen.append("prompt")
        elif "sessions" in command and "close" in command:
            seen.append("close")
        elif "run_auth_benchmark.py" in joined:
            # extract lane by scenario family
            if "--scenario" in command and any(
                s.startswith(("password_", "otp_")) for s in command
            ):
                seen.append("lane:password_lane")
            elif any(s.startswith("oauth_") for s in command):
                seen.append("lane:oauth_lane")
            elif any(s.startswith("passkey_") for s in command):
                seen.append("lane:webauthn_lane")
            else:
                seen.append("lane:other")
        else:
            seen.append("other")
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    report = await run_auth_acpx_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        max_concurrency=3,
        task_session_id="auth-exec-order",
    )
    assert report.passed is True
    assert report.mode == "execute"
    assert all(s.status == "executed" for s in report.stages)

    # Plan/Forge agent stages before lanes; lanes before idr/tribunal/report
    first_lane = min(i for i, item in enumerate(seen) if item.startswith("lane:"))
    last_prefix_prompt = max(
        i for i, item in enumerate(seen[:first_lane]) if item == "prompt"
    )
    assert last_prefix_prompt < first_lane
    lane_names = {item for item in seen if item.startswith("lane:")}
    assert lane_names == {
        "lane:password_lane",
        "lane:oauth_lane",
        "lane:webauthn_lane",
    }
    # stdin used for prompts, never None-only for agent prompts
    assert any(payload is not None and "loopback-only" in payload for payload in stdin_payloads)
    # no raw prompt files created
    for path in tmp_path.rglob("*"):
        if path.is_file() and "prompt" in path.name.lower():
            pytest.fail(f"unexpected prompt file: {path}")


@pytest.mark.asyncio
async def test_execute_failure_is_reported(tmp_path: Path) -> None:
    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        if "run_auth_benchmark.py" in " ".join(command) and any(
            s.startswith("oauth_") for s in command
        ):
            return {"returncode": 7, "stdout": "", "stderr": "boom"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_auth_acpx_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    assert report.passed is False
    assert any("oauth_lane" in f for f in report.failures)
    # After a lane failure, later agent gates should not run
    names = [s.name for s in report.stages]
    assert STAGE_IDR_SECURITY_GATE not in names


def test_allowed_origins_must_be_loopback() -> None:
    with pytest.raises(Exception, match="loopback"):
        build_auth_acpx_pipeline_plan(
            mode="plan",
            allowed_origins=["https://accounts.google.com:443"],
        )
    plan = build_auth_acpx_pipeline_plan(
        mode="plan",
        allowed_origins=["http://127.0.0.1:8765"],
    )
    assert plan.mode == "plan"


def test_report_text_is_redacted_and_safe() -> None:
    plan = build_auth_acpx_pipeline_plan(mode="plan")

    async def _run() -> None:
        report = await run_auth_acpx_pipeline(plan)
        text = report_to_text(report)
        assert "acpx:grok-build" in text
        assert "password=" not in text.lower()
        assert "authorization" not in text.lower()
        assert "cookie=" not in text.lower()
        assert "accounts.google.com" not in text

    import asyncio

    asyncio.run(_run())


def test_module_does_not_use_shell_true() -> None:
    source = Path(__file__).with_name("acpx_pipeline.py").read_text(encoding="utf-8")
    # Ignore comments/docstrings; enforce no live shell invocation kwargs.
    code_lines = [
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#") and '"""' not in line and "'''" not in line
    ]
    code = "\n".join(code_lines)
    assert "shell=True" not in code
    assert "shell = True" not in code
    assert "subprocess.call" not in code
    assert "os.system" not in code
    assert "create_subprocess_shell" not in code
    assert "create_subprocess_exec" in source


def test_permission_and_mcp_files_remain_private(tmp_path: Path) -> None:
    policy = _permission_policy(tmp_path)
    mcp = _mcp_config(tmp_path)
    assert stat.S_IMODE(policy.stat().st_mode) == 0o600
    assert stat.S_IMODE(mcp.stat().st_mode) == 0o600
    build_agent_stage_commands(
        stage=STAGE_TRIBUNAL,
        cwd=tmp_path,
        executable="acpx",
        permission_policy=policy,
        mcp_config=mcp,
    )
