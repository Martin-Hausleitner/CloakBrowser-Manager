"""TDD contract tests for the project-level ACPX agent-family pipeline."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.acpx_runner import ACPX_VERSION, derive_session_name
from scripts.agent_family_pipeline import (
    AGENT_TIMEOUT_RETURNCODE,
    ALLOWED_AGENTS,
    DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_HARNESS,
    DEFAULT_MODE,
    DETERMINISTIC_STAGES,
    GATE_TIMEOUT_SECONDS,
    MAX_AGENT_COMMAND_TIMEOUT_SECONDS,
    MAX_CONCURRENCY,
    MIN_AGENT_COMMAND_TIMEOUT_SECONDS,
    PARALLEL_GATE_STAGES,
    PINNED_ACPX_RELATIVE_PATH,
    PIPELINE_STAGES,
    REQUIRED_ACPX_VERSION,
    STAGE_AUTH_BENCHMARK,
    STAGE_EXTENSION_SKIN,
    STAGE_FORGE,
    STAGE_IDR,
    STAGE_PLAN,
    STAGE_RELEASE,
    STAGE_SKILL_BENCHMARK,
    STAGE_SONIOX_CONTRACT,
    STAGE_TOKEN_COST,
    STAGE_TRIBUNAL,
    AgentFamilyPipelineError,
    _default_command_runner,
    agent_stage_routes,
    build_agent_family_plan,
    build_agent_stage_commands,
    build_deterministic_lane_command,
    build_gate_validation_commands,
    build_plan_or_dry_run,
    parse_agent_map,
    pipeline_stage_names,
    report_to_text,
    required_gate_files,
    resolve_acpx_executable,
    run_agent_family_pipeline,
    run_gate_validations,
    stage_output_path,
    validate_agent_command_timeout,
    validate_dag,
)
from scripts.agent_family_pipeline import (
    main as cli_main,
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


def test_pipeline_stage_order_plan_forge_gates_idr_tribunal_release() -> None:
    plan = build_agent_family_plan(mode="plan")
    names = pipeline_stage_names(plan)
    assert names == PIPELINE_STAGES
    assert names[0] == STAGE_PLAN
    assert names[1] == STAGE_FORGE
    for gate in PARALLEL_GATE_STAGES:
        assert gate in names
    assert names[-3] == STAGE_IDR
    assert names[-2] == STAGE_TRIBUNAL
    assert names[-1] == STAGE_RELEASE
    assert STAGE_SKILL_BENCHMARK in PARALLEL_GATE_STAGES
    assert STAGE_AUTH_BENCHMARK in PARALLEL_GATE_STAGES
    assert STAGE_EXTENSION_SKIN in PARALLEL_GATE_STAGES
    assert STAGE_SONIOX_CONTRACT in PARALLEL_GATE_STAGES
    assert STAGE_TOKEN_COST in PARALLEL_GATE_STAGES


def test_default_mode_is_plan_and_non_executing() -> None:
    assert DEFAULT_MODE in {"plan", "dry-run"}
    plan = build_agent_family_plan()
    assert plan.mode == "plan"
    assert plan.would_execute is False


def test_build_plan_or_dry_run_refuses_execute() -> None:
    with pytest.raises(AgentFamilyPipelineError, match="refuses execute"):
        build_plan_or_dry_run(mode="execute")


def test_execute_requires_policy_files(tmp_path: Path) -> None:
    with pytest.raises(AgentFamilyPipelineError, match="permission_policy"):
        build_agent_family_plan(mode="execute")

    plan = build_agent_family_plan(
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


def test_every_agent_stage_declares_acpx_and_allowed_agent(tmp_path: Path) -> None:
    plan = build_agent_family_plan(
        mode="dry-run",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    routes = agent_stage_routes(plan)
    assert STAGE_PLAN in routes
    assert STAGE_FORGE in routes
    assert STAGE_IDR in routes
    assert STAGE_TRIBUNAL in routes
    assert STAGE_RELEASE in routes
    for stage_name, route in routes.items():
        assert route.startswith(f"{DEFAULT_HARNESS}:")
        stage = next(s for s in plan.stages if s.name == stage_name)
        assert stage.harness == "acpx"
        assert stage.agent in ALLOWED_AGENTS
        assert stage.route == f"acpx:{stage.agent}"
        for command in stage.commands:
            assert stage.agent in command


def test_agent_map_allows_opencode_and_grok_build(tmp_path: Path) -> None:
    agent_map = {
        STAGE_PLAN: "grok-build",
        STAGE_FORGE: "opencode",
        STAGE_IDR: "opencode",
        STAGE_TRIBUNAL: "grok-build",
        STAGE_RELEASE: "grok-build",
    }
    plan = build_agent_family_plan(
        mode="dry-run",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        agent_map=agent_map,
    )
    by_name = {s.name: s for s in plan.stages if s.kind == "agent"}
    assert by_name[STAGE_PLAN].agent == "grok-build"
    assert by_name[STAGE_FORGE].agent == "opencode"
    assert by_name[STAGE_IDR].agent == "opencode"


def test_rejects_disallowed_agents() -> None:
    with pytest.raises(AgentFamilyPipelineError, match="grok-build|opencode"):
        build_agent_family_plan(agent="claude")
    with pytest.raises(AgentFamilyPipelineError, match="grok-build|opencode"):
        build_agent_family_plan(agent_map={STAGE_PLAN: "cursor"})


def test_parse_agent_map_json_and_path(tmp_path: Path) -> None:
    raw = parse_agent_map(f'{{"{STAGE_PLAN}":"opencode"}}')
    assert raw[STAGE_PLAN] == "opencode"
    path = tmp_path / "map.json"
    path.write_text(json.dumps({STAGE_FORGE: "opencode"}), encoding="utf-8")
    from_file = parse_agent_map(str(path))
    assert from_file[STAGE_FORGE] == "opencode"
    with pytest.raises(AgentFamilyPipelineError):
        parse_agent_map("{not-json")


def test_parallel_gates_share_group_and_are_deterministic() -> None:
    plan = build_agent_family_plan(mode="plan")
    assert plan.parallel_gates == PARALLEL_GATE_STAGES
    gates = [s for s in plan.stages if s.name in PARALLEL_GATE_STAGES]
    assert {s.name for s in gates} == set(PARALLEL_GATE_STAGES)
    assert all(s.kind == "deterministic" for s in gates)
    assert all(s.parallel_group == "family_gates" for s in gates)
    assert all(s.harness is None and s.agent is None for s in gates)
    assert set(DETERMINISTIC_STAGES) == set(PARALLEL_GATE_STAGES)


def test_deterministic_lanes_are_argv_only_without_llm_or_auth() -> None:
    root = Path(__file__).resolve().parents[1]
    for stage in PARALLEL_GATE_STAGES:
        command = build_deterministic_lane_command(
            stage=stage,
            repo_root=root,
            python_executable="/usr/bin/python3",
        )
        assert isinstance(command, list)
        assert all(isinstance(part, str) for part in command)
        assert command[0] == "/usr/bin/python3"
        joined = " ".join(command)
        assert "npx" not in joined.split()
        assert "shell=True" not in joined
        assert not any("&&" in p or "|" in p or ";" in p for p in command)
        assert "--output" in command
        # No LLM harness tokens in deterministic argv
        assert "grok-build" not in command
        assert "opencode" not in command
        assert "acpx" not in command
        # No external auth surface markers
        assert "accounts.google.com" not in joined
        assert "authorization" not in joined.lower()
        assert "api_key" not in joined.lower()
        assert "bearer" not in joined.lower()


def test_unique_private_output_paths() -> None:
    paths = [stage_output_path(stage) for stage in PIPELINE_STAGES]
    assert len(paths) == len(set(paths))
    for stage in PARALLEL_GATE_STAGES:
        path = stage_output_path(stage)
        assert path.parent == Path("artifacts/agent-family/private")
        assert path.name.endswith("-report.json")


def test_dag_is_acyclic_with_required_edges() -> None:
    plan = build_agent_family_plan(mode="plan")
    order = validate_dag(plan)
    assert order[0] == STAGE_PLAN
    assert order[1] == STAGE_FORGE
    # All gates after forge, before idr
    forge_i = order.index(STAGE_FORGE)
    idr_i = order.index(STAGE_IDR)
    for gate in PARALLEL_GATE_STAGES:
        gi = order.index(gate)
        assert forge_i < gi < idr_i
    assert order.index(STAGE_IDR) < order.index(STAGE_TRIBUNAL)
    assert order.index(STAGE_TRIBUNAL) < order.index(STAGE_RELEASE)


def test_validate_dag_rejects_cycle() -> None:
    plan = build_agent_family_plan(mode="plan")
    # Inject a cycle via edges override
    with pytest.raises(AgentFamilyPipelineError, match="cycle|acyclic"):
        validate_dag(
            plan,
            edges={
                STAGE_PLAN: (STAGE_FORGE,),
                STAGE_FORGE: (STAGE_PLAN,),
            },
        )


def test_no_second_browser_stage() -> None:
    plan = build_agent_family_plan(mode="plan")
    browserish = [s for s in plan.stages if s.uses_browser]
    assert len(browserish) <= 1
    if browserish:
        assert browserish[0].name == STAGE_AUTH_BENCHMARK
    # All other gates must not claim a browser
    for stage in plan.stages:
        if stage.name != STAGE_AUTH_BENCHMARK:
            assert stage.uses_browser is False


def test_max_concurrency_hard_cap_is_four() -> None:
    assert MAX_CONCURRENCY == 4
    plan = build_agent_family_plan(max_concurrency=4)
    assert plan.max_concurrency == 4
    with pytest.raises(AgentFamilyPipelineError, match="hard cap"):
        build_agent_family_plan(max_concurrency=5)
    with pytest.raises(AgentFamilyPipelineError, match=">= 1"):
        build_agent_family_plan(max_concurrency=0)


def test_pinned_acpx_0_12_1_no_npx_global_fallback(tmp_path: Path) -> None:
    assert REQUIRED_ACPX_VERSION == ACPX_VERSION == "0.12.1"
    pinned = resolve_acpx_executable()
    assert PINNED_ACPX_RELATIVE_PATH.as_posix().endswith(
        "deploy/acpx-runtime/node_modules/acpx/dist/cli.js"
    )
    assert pinned.endswith("cli.js")
    assert "npx" not in pinned
    with pytest.raises(AgentFamilyPipelineError, match="unpinned|npx|global"):
        resolve_acpx_executable(acpx_executable="npx")
    with pytest.raises(AgentFamilyPipelineError, match="unpinned|npx|global"):
        resolve_acpx_executable(acpx_executable="acpx")


def test_agent_stage_commands_stdin_only_via_acpx_runner(tmp_path: Path) -> None:
    built = build_agent_stage_commands(
        stage=STAGE_PLAN,
        cwd=tmp_path,
        executable="/opt/acpx",
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        task_session_id="family-unit-plan",
        agent="opencode",
    )
    assert built["harness"] == "acpx"
    assert built["agent"] == "opencode"
    assert built["route"] == "acpx:opencode"
    assert built["prompt_transport"] == "stdin"
    assert built["persist_prompt"] is False
    assert built["session_name"] == derive_session_name("family-unit-plan")
    prompt = built["commands"]["prompt"]
    assert prompt[-2:] == ["--file", "-"]
    assert built["prompt_brief"] not in " ".join(prompt)
    assert "password=" not in (built["prompt_brief"] or "").lower()
    assert "bearer" not in (built["prompt_brief"] or "").lower()


def test_no_raw_prompt_or_secret_persistence(tmp_path: Path) -> None:
    plan = build_agent_family_plan(
        mode="dry-run",
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        task_session_id="family-no-persist",
    )
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in {".txt", ".prompt", ".md"}:
            text = path.read_text(encoding="utf-8")
            for stage in plan.stages:
                if stage.prompt_brief:
                    assert stage.prompt_brief not in text
    for stage in plan.stages:
        if stage.prompt_brief:
            lowered = stage.prompt_brief.lower()
            assert "password=" not in lowered
            assert "api_key" not in lowered
            assert "bearer" not in lowered
            assert "authorization" not in lowered


def test_execute_fails_closed_when_pinned_acpx_missing(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty-repo"
    empty_root.mkdir()
    with pytest.raises(AgentFamilyPipelineError, match="missing or not executable"):
        build_agent_family_plan(
            mode="execute",
            repo_root=empty_root,
            cwd=tmp_path,
            permission_policy=_permission_policy(tmp_path),
            mcp_config=_mcp_config(tmp_path),
        )


@pytest.mark.asyncio
async def test_plan_and_dry_run_do_not_invoke_runner() -> None:
    calls: list[list[str]] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(command))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    plan_report = await run_agent_family_pipeline(command_runner=runner)
    assert plan_report.mode == "plan"
    assert plan_report.passed is True
    assert calls == []
    assert all(s.status == "planned" for s in plan_report.stages)

    dry = await run_agent_family_pipeline(mode="dry-run", command_runner=runner)
    assert dry.mode == "dry-run"
    assert calls == []
    assert all(s.status == "dry_run" for s in dry.stages)


@pytest.mark.asyncio
async def test_fake_runner_e2e_parallel_gates_and_order(tmp_path: Path) -> None:
    """Fake-runner E2E: Plan/Forge sequential, gates concurrent, then IDR/Tribunal/Release."""
    seen: list[str] = []
    gate_start: dict[str, float] = {}
    gate_end: dict[str, float] = {}
    active_gates = 0
    max_active_gates = 0
    lock = asyncio.Lock()
    stdin_payloads: list[str | None] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        nonlocal active_gates, max_active_gates
        stdin_payloads.append(kwargs.get("stdin_text"))
        joined = " ".join(command)
        if "sessions" in command and "ensure" in command:
            seen.append("ensure")
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if command[-2:] == ["--file", "-"]:
            seen.append("prompt")
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if "sessions" in command and "close" in command:
            seen.append("close")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        # Deterministic gate markers via --gate <name>
        if "--gate" in command:
            idx = command.index("--gate")
            gate = command[idx + 1]
            async with lock:
                active_gates += 1
                max_active_gates = max(max_active_gates, active_gates)
                gate_start[gate] = time.monotonic()
                seen.append(f"gate:{gate}")
            await asyncio.sleep(0.05)
            async with lock:
                gate_end[gate] = time.monotonic()
                active_gates -= 1
            return {"returncode": 0, "stdout": "ok", "stderr": ""}

        seen.append(f"other:{joined[:40]}")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        max_concurrency=4,
        task_session_id="family-e2e-parallel",
    )
    assert report.passed is True
    assert all(s.status == "executed" for s in report.stages)

    first_gate = min(i for i, item in enumerate(seen) if item.startswith("gate:"))
    last_prefix_prompt = max(
        i for i, item in enumerate(seen[:first_gate]) if item == "prompt"
    )
    assert last_prefix_prompt < first_gate
    gate_names = {item.split(":", 1)[1] for item in seen if item.startswith("gate:")}
    assert gate_names == set(PARALLEL_GATE_STAGES)

    # Parallelism: at least two gates overlapped in wall time
    assert max_active_gates >= 2
    starts = sorted(gate_start.values())
    ends = sorted(gate_end.values())
    assert starts[0] < ends[-1]
    # Overlap check: earliest end after second start
    assert any(
        gate_start[a] < gate_end[b] and gate_start[b] < gate_end[a]
        for a in gate_start
        for b in gate_start
        if a != b
    )

    # After gates, suffix agent stages ran (ensure/prompt for idr/tribunal/release)
    last_gate = max(i for i, item in enumerate(seen) if item.startswith("gate:"))
    suffix_prompts = [i for i, item in enumerate(seen) if item == "prompt" and i > last_gate]
    assert len(suffix_prompts) >= 3  # idr, tribunal, release
    assert any(p is not None and len(p) > 0 for p in stdin_payloads)


@pytest.mark.asyncio
async def test_fake_runner_stops_before_tribunal_on_gate_failure(tmp_path: Path) -> None:
    """Gate failure must fail-close and skip Tribunal (and Release)."""
    ran_stages: list[str] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        if "--gate" in command:
            idx = command.index("--gate")
            gate = command[idx + 1]
            ran_stages.append(gate)
            if gate == STAGE_TOKEN_COST:
                return {"returncode": 9, "stdout": "", "stderr": "cost gate fail"}
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if command[-2:] == ["--file", "-"]:
            # Infer stage from session/task seed is hard; count prompts only
            ran_stages.append("agent_prompt")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        task_session_id="family-e2e-fail-gate",
    )
    assert report.passed is False
    assert any(STAGE_TOKEN_COST in f for f in report.failures)
    names = [s.name for s in report.stages]
    assert STAGE_TOKEN_COST in names or any(
        s.status == "failed" and s.name == STAGE_TOKEN_COST for s in report.stages
    )
    # Must stop before Tribunal (and Release)
    assert STAGE_TRIBUNAL not in names
    assert STAGE_RELEASE not in names
    # IDR also must not run after gate failure
    assert STAGE_IDR not in names


@pytest.mark.asyncio
async def test_agent_failure_stops_before_gates(tmp_path: Path) -> None:
    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        if command[-2:] == ["--file", "-"]:
            # Fail first agent prompt (plan)
            return {"returncode": 3, "stdout": "", "stderr": "plan fail"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
    )
    assert report.passed is False
    names = [s.name for s in report.stages]
    assert STAGE_PLAN in names
    assert not any(g in names for g in PARALLEL_GATE_STAGES)
    assert STAGE_TRIBUNAL not in names


def test_report_text_is_redacted_and_safe() -> None:
    async def _run() -> None:
        report = await run_agent_family_pipeline(mode="plan")
        text = report_to_text(report)
        assert "agent_family_pipeline" in text
        assert "password=" not in text.lower()
        assert "authorization" not in text.lower()
        assert "cookie=" not in text.lower()
        assert "api_key" not in text.lower()

    asyncio.run(_run())


def test_module_does_not_use_shell_true() -> None:
    source = Path(__file__).with_name("agent_family_pipeline.py").read_text(encoding="utf-8")
    code_lines = [
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#") and '"""' not in line and "'''" not in line
    ]
    code = "\n".join(code_lines)
    assert "shell=True" not in code
    assert "shell = True" not in code
    assert "os.system" not in code
    assert "create_subprocess_shell" not in code
    assert "create_subprocess_exec" in source


def test_cli_defaults_to_plan_writes_private_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "artifacts" / "agent-family" / "private" / "report.json"
    code = cli_main(["--output", str(output)])
    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith(f"mode={DEFAULT_MODE} passed=true")
    assert "prompt" not in printed.lower()
    assert output.is_file()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "plan"
    assert payload["passed"] is True
    assert payload["stage_count"] == len(PIPELINE_STAGES)
    assert "prompt_brief" not in output.read_text(encoding="utf-8")
    assert "stdin_text" not in output.read_text(encoding="utf-8")


def test_cli_rejects_parallel_above_cap(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--parallel", "5"])
    assert exc.value.code == 2


def test_cli_execute_fails_closed_without_policy(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = cli_main(["--mode", "execute", "--output", str(tmp_path / "r.json")])
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "permission" in err or "mcp" in err or "execute" in err


def test_cli_accepts_agent_map(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "out.json"
    code = cli_main(
        [
            "--mode",
            "plan",
            "--agent-map",
            json.dumps({STAGE_PLAN: "opencode", STAGE_FORGE: "grok-build"}),
            "--output",
            str(output),
        ]
    )
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is True


# ---------------------------------------------------------------------------
# P1: real gate validation (not stub passed=true)
# ---------------------------------------------------------------------------


def test_gate_validation_commands_are_real_and_gate_specific() -> None:
    root = Path(__file__).resolve().parents[1]
    work = root / "artifacts" / "agent-family" / "private" / "test-work"
    skill_cmds = build_gate_validation_commands(
        gate=STAGE_SKILL_BENCHMARK,
        repo_root=root,
        python_executable="/usr/bin/python3",
        work_dir=work / "skill",
    )
    assert skill_cmds
    joined = " ".join(skill_cmds[0])
    assert "agent_family_quality_gate.py" in joined
    assert "skill_benchmark" in skill_cmds[0]
    assert "shell=True" not in joined
    assert "npx" not in joined.split()

    auth_cmds = build_gate_validation_commands(
        gate=STAGE_AUTH_BENCHMARK,
        repo_root=root,
        python_executable="/usr/bin/python3",
        work_dir=work / "auth",
    )
    assert any("agent_family_quality_gate.py" in " ".join(c) for c in auth_cmds)
    assert any("auth_benchmark_contracts" in c for c in auth_cmds)
    assert any(
        "pytest" in c and any("test_ci_contract.py" in p for p in c) for c in auth_cmds
    )

    ext_cmds = build_gate_validation_commands(
        gate=STAGE_EXTENSION_SKIN,
        repo_root=root,
        python_executable="/usr/bin/python3",
        work_dir=work / "ext",
    )
    assert any("extension_skin" in c for c in ext_cmds)
    assert any("secure_recorder" in c for c in ext_cmds)
    assert any(
        "pytest" in c and any("test_secure_recording_compiler.py" in p for p in c)
        for c in ext_cmds
    )

    soniox_cmds = build_gate_validation_commands(
        gate=STAGE_SONIOX_CONTRACT,
        repo_root=root,
        python_executable="/usr/bin/python3",
        work_dir=work / "soniox",
    )
    assert any("soniox_adapter_contract" in c for c in soniox_cmds)
    assert any(
        "pytest" in c and any("test_soniox_stt_adapter.py" in p for p in c)
        for c in soniox_cmds
    )

    token_cmds = build_gate_validation_commands(
        gate=STAGE_TOKEN_COST,
        repo_root=root,
        python_executable="/usr/bin/python3",
        work_dir=work / "token",
    )
    assert any("budget_dedupe" in c for c in token_cmds)
    assert any("agent_family_budget_gate.py" in " ".join(c) for c in token_cmds)
    assert any(
        "pytest" in c and any("test_agent_family_budget_gate.py" in p for p in c)
        for c in token_cmds
    )
    # Fixture paths must be argv-only and private-ish under work dir
    fixture_cmd = next(c for c in token_cmds if "agent_family_budget_gate.py" in " ".join(c))
    assert "--events" in fixture_cmd
    assert "--budget" in fixture_cmd
    assert "--output" in fixture_cmd


def test_required_gate_files_fail_closed_when_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    missing = required_gate_files(STAGE_SKILL_BENCHMARK, empty)
    assert missing
    assert not any(p.is_file() for p in missing)

    output = tmp_path / "private" / "skill-report.json"
    result = run_gate_validations(
        gate=STAGE_SKILL_BENCHMARK,
        repo_root=empty,
        output=output,
    )
    assert result["evaluated"] is False
    assert result["passed"] is False
    assert result["missing_files"]
    write_path = tmp_path / "written.json"
    # Gate CLI path: write report with unevaluated=false
    from scripts.agent_family_pipeline import write_gate_report

    write_gate_report(write_path, result)
    payload = json.loads(write_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["evaluated"] is False


def test_run_gate_validations_failing_subprocess(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    # Provide a repo root that has required files so we reach command execution.
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(command))
        # Fail the first validation command
        if len(calls) == 1:
            return {"returncode": 7, "stdout": "", "stderr": "boom"}
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    output = tmp_path / "private" / f"{STAGE_SKILL_BENCHMARK}-report.json"
    result = run_gate_validations(
        gate=STAGE_SKILL_BENCHMARK,
        repo_root=root,
        output=output,
        command_runner=runner,
    )
    assert result["evaluated"] is True
    assert result["passed"] is False
    assert result["failed_commands"]
    assert result["failed_commands"][0]["returncode"] == 7
    assert calls, "expected validation commands to run"


def test_run_gate_validations_success_with_fake_runner(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]

    def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    output = tmp_path / "private" / f"{STAGE_AUTH_BENCHMARK}-report.json"
    result = run_gate_validations(
        gate=STAGE_AUTH_BENCHMARK,
        repo_root=root,
        output=output,
        command_runner=runner,
    )
    assert result["evaluated"] is True
    assert result["passed"] is True
    assert result["command_count"] >= 2
    assert result["failed_commands"] == []


def test_gate_cli_real_smoke_skill_benchmark(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Real smoke: skill-benchmark runs quality-gate skill check against the repo."""
    output = tmp_path / "private" / "skill-benchmark-report.json"
    code = cli_main(
        [
            "gate",
            "--gate",
            STAGE_SKILL_BENCHMARK,
            "--output",
            str(output),
            "--repo-root",
            str(Path(__file__).resolve().parents[1]),
            "--timeout",
            str(GATE_TIMEOUT_SECONDS),
        ]
    )
    printed = capsys.readouterr().out
    assert "gate=skill-benchmark" in printed
    assert "evaluated=true" in printed
    assert output.is_file()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["gate"] == STAGE_SKILL_BENCHMARK
    assert payload["evaluated"] is True
    # Must not be the old stub that always passed without evaluating.
    assert "command_count" in payload
    assert payload["command_count"] >= 1
    assert code == 0
    assert payload["passed"] is True
    # Metrics only
    text = output.read_text(encoding="utf-8")
    assert "stdout" not in text
    assert "stderr" not in text
    assert "password=" not in text.lower()


def test_gate_cli_missing_repo_is_unevaluated_nonzero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    output = tmp_path / "out.json"
    code = cli_main(
        [
            "gate",
            "--gate",
            STAGE_SONIOX_CONTRACT,
            "--output",
            str(output),
            "--repo-root",
            str(empty),
        ]
    )
    assert code != 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["evaluated"] is False
    assert payload["passed"] is False
    assert payload["missing_files"]


@pytest.mark.asyncio
async def test_unevaluated_gate_stops_before_idr_tribunal_release(tmp_path: Path) -> None:
    """Pipeline execute must not run IDR/Tribunal/Release when a gate is unevaluated/fails."""

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        # Agent stages succeed
        if command[-2:] == ["--file", "-"] or (
            "sessions" in command and ("ensure" in command or "close" in command)
        ):
            return {"returncode": 0, "stdout": "", "stderr": ""}
        # Outer gate command: simulate unevaluated/fail closed
        if "--gate" in command:
            return {"returncode": 1, "stdout": "", "stderr": "unevaluated"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        task_session_id="family-unevaluated-stop",
    )
    assert report.passed is False
    names = [s.name for s in report.stages]
    assert STAGE_IDR not in names
    assert STAGE_TRIBUNAL not in names
    assert STAGE_RELEASE not in names


def test_gate_validation_commands_unique_outputs_and_no_shell() -> None:
    root = Path(__file__).resolve().parents[1]
    source = Path(__file__).with_name("agent_family_pipeline.py").read_text(encoding="utf-8")
    # Live code must pin shell=False on subprocess.run; never shell=True.
    assert "shell=False" in source
    code_lines = [
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
        and '"""' not in line
        and "'''" not in line
        and "shell=" in line
    ]
    assert code_lines
    for line in code_lines:
        assert "shell=True" not in line
        assert "shell=False" in line or "shell = False" in line

    seen_outputs: list[str] = []
    for gate in PARALLEL_GATE_STAGES:
        cmds = build_gate_validation_commands(
            gate=gate,
            repo_root=root,
            python_executable="/usr/bin/python3",
            work_dir=root / "artifacts" / "agent-family" / "private" / f"{gate}-uniq",
        )
        for cmd in cmds:
            if "--output" in cmd:
                idx = cmd.index("--output")
                seen_outputs.append(cmd[idx + 1])
            assert not any(ch in part for part in cmd for ch in ("&&", "|", ";"))
    assert len(seen_outputs) == len(set(seen_outputs))


# ---------------------------------------------------------------------------
# P2: execute-path agent command wall-clock timeout
# ---------------------------------------------------------------------------


def test_agent_command_timeout_bounds_documented() -> None:
    assert DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS == 120
    assert MIN_AGENT_COMMAND_TIMEOUT_SECONDS == 1
    assert MAX_AGENT_COMMAND_TIMEOUT_SECONDS == 600
    assert validate_agent_command_timeout(120) == 120
    with pytest.raises(AgentFamilyPipelineError, match=">="):
        validate_agent_command_timeout(0)
    with pytest.raises(AgentFamilyPipelineError, match="<="):
        validate_agent_command_timeout(MAX_AGENT_COMMAND_TIMEOUT_SECONDS + 1)
    plan = build_agent_family_plan(mode="plan")
    assert plan.agent_command_timeout == DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_default_command_runner_times_out_slow_child_and_terminates() -> None:
    """Hung child must not hang forever: rc=124, process gone, no orphan."""
    before = time.monotonic()
    outcome = await _default_command_runner(
        ["sleep", "30"],
        timeout_seconds=1,
    )
    elapsed = time.monotonic() - before
    assert elapsed < 10  # well under the sleep duration
    assert outcome["timed_out"] is True
    assert outcome["returncode"] == AGENT_TIMEOUT_RETURNCODE
    # stderr is redacted/bounded metrics text
    assert "timed out" in outcome["stderr"].lower() or outcome["stderr"]
    assert "password=" not in outcome["stderr"].lower()
    assert "authorization" not in outcome["stderr"].lower()


@pytest.mark.asyncio
async def test_default_command_runner_prompt_stdin_path_succeeds() -> None:
    """stdin-only prompt path still works under the timeout wrapper."""
    outcome = await _default_command_runner(
        ["cat"],
        stdin_text="hello-agent-family-stdin",
        timeout_seconds=5,
    )
    assert outcome["timed_out"] is False
    assert outcome["returncode"] == 0
    assert "hello-agent-family-stdin" in outcome["stdout"]


@pytest.mark.asyncio
async def test_default_command_runner_redacts_secret_like_output() -> None:
    outcome = await _default_command_runner(
        ["bash", "-c", "printf 'token=supersecret123\\n'"],
        timeout_seconds=5,
    )
    assert outcome["returncode"] == 0
    assert "supersecret123" not in outcome["stdout"]
    assert "[REDACTED]" in outcome["stdout"]


@pytest.mark.asyncio
async def test_close_timeout_fails_agent_stage_and_stops_downstream(
    tmp_path: Path,
) -> None:
    """Close command timeout fails the stage; IDR/Tribunal/Release do not run."""
    calls: list[str] = []

    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        if "sessions" in command and "ensure" in command:
            calls.append("ensure")
            return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
        if command[-2:] == ["--file", "-"]:
            calls.append("prompt")
            assert kwargs.get("stdin_text") is not None
            return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
        if "sessions" in command and "close" in command:
            calls.append("close")
            return {
                "returncode": AGENT_TIMEOUT_RETURNCODE,
                "stdout": "",
                "stderr": "timed out",
                "timed_out": True,
            }
        if "--gate" in command:
            calls.append("gate")
            return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
        calls.append("other")
        return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        agent_command_timeout=5,
        task_session_id="family-close-timeout",
    )
    assert report.passed is False
    assert any("timed out" in f for f in report.failures)
    names = [s.name for s in report.stages]
    # Plan fails on close timeout before forge/gates/IDR
    assert STAGE_PLAN in names
    assert STAGE_IDR not in names
    assert STAGE_TRIBUNAL not in names
    assert STAGE_RELEASE not in names
    assert "gate" not in calls


@pytest.mark.asyncio
async def test_ensure_timeout_propagates_and_skips_gates(tmp_path: Path) -> None:
    async def runner(command: list[str], **kwargs: Any) -> dict[str, Any]:
        if "sessions" in command and "ensure" in command:
            return {
                "returncode": AGENT_TIMEOUT_RETURNCODE,
                "stdout": "",
                "stderr": "ensure timed out",
                "timed_out": True,
            }
        return {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}

    report = await run_agent_family_pipeline(
        mode="execute",
        command_runner=runner,
        cwd=tmp_path,
        permission_policy=_permission_policy(tmp_path),
        mcp_config=_mcp_config(tmp_path),
        agent_command_timeout=3,
    )
    assert report.passed is False
    assert any("ensure" in f and "timed out" in f for f in report.failures)
    names = [s.name for s in report.stages]
    assert not any(g in names for g in PARALLEL_GATE_STAGES)
    assert STAGE_TRIBUNAL not in names


def test_cli_agent_timeout_bound_rejection(capsys: pytest.CaptureFixture[str]) -> None:
    code_low = cli_main(["--mode", "plan", "--agent-timeout", "0"])
    assert code_low != 0
    err_low = capsys.readouterr().err.lower()
    assert "timeout" in err_low or "agent" in err_low

    code_high = cli_main(
        [
            "--mode",
            "plan",
            "--agent-timeout",
            str(MAX_AGENT_COMMAND_TIMEOUT_SECONDS + 1),
        ]
    )
    assert code_high != 0
    err_high = capsys.readouterr().err.lower()
    assert "timeout" in err_high or str(MAX_AGENT_COMMAND_TIMEOUT_SECONDS) in err_high


def test_cli_accepts_agent_timeout_in_range(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "private" / "plan.json"
    code = cli_main(
        [
            "--mode",
            "plan",
            "--agent-timeout",
            "30",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    assert output.is_file()


def test_default_command_runner_uses_wait_for_in_source() -> None:
    source = Path(__file__).with_name("agent_family_pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "asyncio.wait_for" in source
    assert "AGENT_TIMEOUT_RETURNCODE" in source
    assert "killpg" in source or "SIGKILL" in source
    # execute path must not call bare communicate without wait_for nearby
    assert "process.communicate" in source
