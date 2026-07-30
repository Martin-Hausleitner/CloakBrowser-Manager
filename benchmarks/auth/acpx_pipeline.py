"""ACPX-backed auth benchmark workflow (Plan → Forge → lanes → gates → report).

This module owns the *auth-specific* stage graph only. It reuses
``scripts.acpx_runner`` for ACPX command construction and session naming and
does not reimplement a generic orchestrator.

Security contracts (fail closed):
- default mode is plan/dry-run; execute requires an explicit mode
- agent stages always route via ACPX + Grok Build
- browser lanes stay loopback-only deterministic commands (never shell mode)
- concurrency is bounded to at most 4
- no real Google accounts, real passkeys, secrets, or raw prompt persistence
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final, Literal

from benchmarks.auth.policy import assert_artifact_is_safe, validate_benchmark_origins
from scripts.acpx_runner import (
    ACPX_VERSION,
    build_close_command,
    build_ensure_command,
    build_prompt_command,
    derive_session_name,
    validate_acpx_agent_id,
)

Mode = Literal["plan", "dry-run", "execute"]
StageKind = Literal["agent", "browser"]

DEFAULT_MODE: Final[Mode] = "plan"
ALLOWED_MODES: Final[frozenset[str]] = frozenset({"plan", "dry-run", "execute"})
MAX_CONCURRENCY: Final[int] = 4
DEFAULT_AGENT: Final[str] = "grok-build"
DEFAULT_HARNESS: Final[str] = "acpx"
AGENT_ROUTE: Final[str] = f"{DEFAULT_HARNESS}:{DEFAULT_AGENT}"

# Repo-pinned ACPX runtime (never PATH/npx). Verified pin: ACPX 0.12.1.
PINNED_ACPX_RELATIVE_PATH: Final[Path] = Path(
    "deploy/acpx-runtime/node_modules/acpx/dist/cli.js"
)
REQUIRED_ACPX_VERSION: Final[str] = ACPX_VERSION
_UNPINNED_ACPX_FALLBACKS: Final[frozenset[str]] = frozenset({"acpx", "npx"})

# Ordered high-level plan. Parallel browser lanes share one fan-out step.
STAGE_PLAN: Final[str] = "plan"
STAGE_FORGE: Final[str] = "forge"
STAGE_PASSWORD_LANE: Final[str] = "password_lane"
STAGE_OAUTH_LANE: Final[str] = "oauth_lane"
STAGE_WEBAUTHN_LANE: Final[str] = "webauthn_lane"
STAGE_IDR_SECURITY_GATE: Final[str] = "idr_security_gate"
STAGE_TRIBUNAL: Final[str] = "tribunal"
STAGE_RELEASE_REPORT: Final[str] = "release_report"

PIPELINE_STAGES: Final[tuple[str, ...]] = (
    STAGE_PLAN,
    STAGE_FORGE,
    STAGE_PASSWORD_LANE,
    STAGE_OAUTH_LANE,
    STAGE_WEBAUTHN_LANE,
    STAGE_IDR_SECURITY_GATE,
    STAGE_TRIBUNAL,
    STAGE_RELEASE_REPORT,
)

AGENT_STAGES: Final[frozenset[str]] = frozenset(
    {
        STAGE_PLAN,
        STAGE_FORGE,
        STAGE_IDR_SECURITY_GATE,
        STAGE_TRIBUNAL,
        STAGE_RELEASE_REPORT,
    }
)

PARALLEL_BROWSER_LANES: Final[tuple[str, ...]] = (
    STAGE_PASSWORD_LANE,
    STAGE_OAUTH_LANE,
    STAGE_WEBAUTHN_LANE,
)

# Synthetic loopback scenarios only — never real Google / hardware passkeys.
# Must partition live_runner.DEFAULT_SCENARIOS exactly once (validated at plan time).
LANE_SCENARIOS: Final[Mapping[str, tuple[str, ...]]] = {
    STAGE_PASSWORD_LANE: ("password_success", "password_failure", "otp_handoff", "otp_expiry"),
    STAGE_OAUTH_LANE: (
        "oauth_popup_success",
        "oauth_popup_cancel",
        "oauth_popup_close",
        "oauth_popup_reopen",
        "oauth_passkey_2fa_success",
        "oauth_passkey_2fa_popup_reopen",
    ),
    STAGE_WEBAUTHN_LANE: (
        "passkey_register_assert",
        "passkey_prompt_recovery",
        "passkey_uv_required",
        "passkey_no_authenticator",
        "passkey_revoked_credential",
        "passkey_conditional_mediation",
    ),
}

# Distinct private report paths so parallel browser lanes never race on the
# shared default artifacts/auth-benchmark/report.json path.
LANE_REPORT_PATHS: Final[Mapping[str, Path]] = {
    STAGE_PASSWORD_LANE: Path("artifacts/auth-benchmark/private/password_lane-report.json"),
    STAGE_OAUTH_LANE: Path("artifacts/auth-benchmark/private/oauth_lane-report.json"),
    STAGE_WEBAUTHN_LANE: Path("artifacts/auth-benchmark/private/webauthn_lane-report.json"),
}
_SHARED_DEFAULT_REPORT: Final[Path] = Path("artifacts/auth-benchmark/report.json")

# Stage prompts are short, non-secret briefs. Never written to disk by this module.
_AGENT_STAGE_BRIEFS: Final[Mapping[str, str]] = {
    STAGE_PLAN: (
        "Plan the loopback-only auth benchmark matrix for password, OAuth popup, "
        "and synthetic WebAuthn lanes. Do not touch real Google, real passkeys, or secrets."
    ),
    STAGE_FORGE: (
        "Forge the auth benchmark implementation plan into executable loopback-only "
        "scenario work. Route only via ACPX Grok Build. No secret material."
    ),
    STAGE_IDR_SECURITY_GATE: (
        "Run the IDR security gate over redacted auth-benchmark evidence. Reject real "
        "credentials, real Google origins, raw prompts, or passkey private material."
    ),
    STAGE_TRIBUNAL: (
        "Tribunal review of the auth benchmark results. Confirm loopback-only evidence, "
        "bounded concurrency, and ACPX/Grok Build routing for agent stages."
    ),
    STAGE_RELEASE_REPORT: (
        "Produce a redacted release report for the auth passkey benchmark. Metrics and "
        "pass/fail only; no secrets, cookies, OTP digits, or raw prompts."
    ),
}

_FORBIDDEN_ORIGIN_MARKERS: Final[tuple[str, ...]] = (
    "accounts.google.com",
    "google.com/o/oauth2",
    "googleapis.com",
)


class AuthAcpxPipelineError(ValueError):
    """Raised when the auth ACPX pipeline would violate a safety contract."""


class PipelineMode(str, Enum):
    PLAN = "plan"
    DRY_RUN = "dry-run"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One named pipeline stage with routing and command material."""

    name: str
    kind: StageKind
    harness: str | None
    agent: str | None
    route: str | None
    parallel_group: str | None
    scenarios: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    prompt_brief: str | None = None
    session_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthAcpxPipelinePlan:
    """Immutable planned workflow for the auth ACPX benchmark."""

    mode: Mode
    stages: tuple[StageSpec, ...]
    max_concurrency: int
    agent: str
    harness: str
    parallel_lanes: tuple[str, ...]
    repo_root: Path
    would_execute: bool
    acpx_executable: str


@dataclass(frozen=True, slots=True)
class StageResult:
    name: str
    status: Literal["planned", "dry_run", "executed", "skipped", "failed"]
    detail: str
    commands: tuple[tuple[str, ...], ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthAcpxPipelineReport:
    mode: Mode
    passed: bool
    stages: tuple[StageResult, ...]
    failures: tuple[str, ...]
    max_concurrency: int
    agent_route: str = AGENT_ROUTE
    notes: tuple[str, ...] = field(default_factory=tuple)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def pinned_acpx_executable(repo_root: Path | None = None) -> Path:
    """Absolute path to the repo-pinned ACPX 0.12.1 CLI (not PATH/npx)."""
    root = (repo_root or _repo_root()).resolve()
    return (root / PINNED_ACPX_RELATIVE_PATH).resolve()


def resolve_acpx_executable(
    *,
    acpx_executable: str | Path | None = None,
    repo_root: Path | None = None,
    require_executable: bool = False,
) -> str:
    """Resolve ACPX argv[0]: default pinned runtime, optional absolute override.

    Never falls back to bare ``acpx`` on PATH or ``npx``. Execute mode callers
    must pass ``require_executable=True`` so missing/non-executable pins fail closed.
    """
    if acpx_executable is None:
        candidate = pinned_acpx_executable(repo_root)
    else:
        raw = str(acpx_executable).strip()
        if not raw:
            raise AuthAcpxPipelineError("ACPX executable path is required when overridden")
        # Reject unpinned global/npx fallbacks even as an "override".
        first_token = raw.split()[0]
        if (
            raw in _UNPINNED_ACPX_FALLBACKS
            or first_token in _UNPINNED_ACPX_FALLBACKS
            or first_token.endswith("/npx")
            or Path(first_token).name in _UNPINNED_ACPX_FALLBACKS
            and not Path(raw).is_absolute()
        ):
            raise AuthAcpxPipelineError(
                "refusing unpinned ACPX fallback (npx or global acpx); "
                "use the repo-pinned deploy/acpx-runtime CLI or an absolute override path"
            )
        candidate = Path(raw)
        if not candidate.is_absolute():
            # Relative overrides are resolved against the repo root, never PATH lookup.
            candidate = ((repo_root or _repo_root()).resolve() / candidate).resolve()
        else:
            candidate = candidate.resolve()

    if require_executable and (
        not candidate.is_file() or not os.access(candidate, os.X_OK)
    ):
        raise AuthAcpxPipelineError(
            f"ACPX executable missing or not executable: {candidate}"
        )
    return str(candidate)


def _validate_mode(mode: str) -> Mode:
    value = str(mode or "").strip().lower()
    if value not in ALLOWED_MODES:
        raise AuthAcpxPipelineError(
            f"unsupported pipeline mode {mode!r}; expected one of {sorted(ALLOWED_MODES)}"
        )
    return value  # type: ignore[return-value]


def _validate_concurrency(max_concurrency: int) -> int:
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool):
        raise AuthAcpxPipelineError("max_concurrency must be a positive integer")
    if max_concurrency < 1:
        raise AuthAcpxPipelineError("max_concurrency must be >= 1")
    if max_concurrency > MAX_CONCURRENCY:
        raise AuthAcpxPipelineError(
            f"max_concurrency {max_concurrency} exceeds hard cap of {MAX_CONCURRENCY}"
        )
    return max_concurrency


def _validate_agent(agent: str) -> str:
    value = validate_acpx_agent_id(agent)
    if value != DEFAULT_AGENT:
        raise AuthAcpxPipelineError(
            f"auth ACPX pipeline agent stages must use {DEFAULT_AGENT!r}; got {value!r}"
        )
    return value


def _reject_forbidden_markers(text: str, *, label: str) -> None:
    lowered = text.lower()
    for marker in _FORBIDDEN_ORIGIN_MARKERS:
        if marker in lowered:
            raise AuthAcpxPipelineError(
                f"{label} must not reference real Google auth surfaces ({marker})"
            )
    for needle in (
        "private_key",
        "privatekey",
        "clientdatajson",
        "attestationobject",
        "authorization: bearer",
        "password=",
        "cookie=",
    ):
        if needle in lowered:
            raise AuthAcpxPipelineError(f"{label} contains forbidden secret material")


def _benchmark_script(repo_root: Path) -> Path:
    path = (repo_root / "benchmarks" / "auth" / "run_auth_benchmark.py").resolve()
    if not path.is_file():
        raise AuthAcpxPipelineError(f"auth benchmark entrypoint missing: {path}")
    return path


def lane_report_path(lane: str) -> Path:
    """Return the distinct private report path for one parallel browser lane."""
    if lane not in PARALLEL_BROWSER_LANES:
        raise AuthAcpxPipelineError(f"unknown browser lane: {lane}")
    path = LANE_REPORT_PATHS[lane]
    if path == _SHARED_DEFAULT_REPORT:
        raise AuthAcpxPipelineError(
            f"browser lane {lane} must not use the shared default report path"
        )
    return path


def validate_lane_scenario_coverage(
    *,
    lane_scenarios: Mapping[str, Sequence[str]] | None = None,
    default_scenarios: Sequence[str] | None = None,
) -> None:
    """Require every DEFAULT_SCENARIOS id to appear in exactly one lane."""
    # Import lazily so unit tests can monkeypatch live_runner without circular load cost.
    if default_scenarios is None:
        from benchmarks.auth.live_runner import DEFAULT_SCENARIOS as live_defaults

        required = tuple(live_defaults)
    else:
        required = tuple(default_scenarios)
    matrix = lane_scenarios if lane_scenarios is not None else LANE_SCENARIOS
    if set(matrix) != set(PARALLEL_BROWSER_LANES):
        raise AuthAcpxPipelineError(
            "lane scenario matrix must define exactly the three parallel browser lanes"
        )
    assigned: list[str] = []
    for lane in PARALLEL_BROWSER_LANES:
        scenarios = tuple(matrix[lane])
        if not scenarios:
            raise AuthAcpxPipelineError(f"browser lane {lane} has no scenarios")
        assigned.extend(scenarios)
    if len(assigned) != len(set(assigned)):
        raise AuthAcpxPipelineError(
            "LANE_SCENARIOS must assign each scenario id exactly once (duplicate found)"
        )
    if sorted(assigned) != sorted(required):
        missing = sorted(set(required) - set(assigned))
        extra = sorted(set(assigned) - set(required))
        raise AuthAcpxPipelineError(
            "LANE_SCENARIOS must cover DEFAULT_SCENARIOS exactly once; "
            f"missing={missing!r} extra={extra!r}"
        )


def build_browser_lane_command(
    *,
    lane: str,
    repo_root: Path | None = None,
    python_executable: str | None = None,
    scenarios: Sequence[str] | None = None,
    max_parallel: int = 1,
    output: Path | None = None,
) -> list[str]:
    """Build a deterministic argv list for one loopback browser lane (no shell)."""
    if lane not in PARALLEL_BROWSER_LANES:
        raise AuthAcpxPipelineError(f"unknown browser lane: {lane}")
    root = (repo_root or _repo_root()).resolve()
    script = _benchmark_script(root)
    chosen = tuple(scenarios or LANE_SCENARIOS[lane])
    if not chosen:
        raise AuthAcpxPipelineError(f"browser lane {lane} requires at least one scenario")
    for scenario in chosen:
        if scenario.startswith(("http://", "https://")):
            raise AuthAcpxPipelineError("browser lanes accept scenario ids, not origins")
        _reject_forbidden_markers(scenario, label=f"scenario {scenario!r}")
    report_path = Path(output) if output is not None else lane_report_path(lane)
    if report_path == _SHARED_DEFAULT_REPORT:
        raise AuthAcpxPipelineError(
            f"browser lane {lane} must not write the shared default report path"
        )
    parallel = _validate_concurrency(max_parallel)
    command = [
        str(python_executable or sys.executable),
        str(script),
        "--parallel",
        str(parallel),
        "--output",
        str(report_path),
    ]
    for scenario in chosen:
        command.extend(["--scenario", scenario])
    # Guard: never produce a shell string and never embed credentials.
    if any(part is None or not isinstance(part, str) for part in command):
        raise AuthAcpxPipelineError("browser command must be a pure argv list of strings")
    joined = " ".join(command)
    _reject_forbidden_markers(joined, label="browser command")
    return command


def build_agent_stage_commands(
    *,
    stage: str,
    cwd: Path,
    executable: str,
    permission_policy: Path,
    mcp_config: Path,
    task_session_id: str | None = None,
    agent: str = DEFAULT_AGENT,
    timeout_seconds: int | None = 120,
) -> dict[str, Any]:
    """Build ACPX ensure/prompt/close argv lists via ``scripts.acpx_runner`` contracts."""
    if stage not in AGENT_STAGES:
        raise AuthAcpxPipelineError(f"stage {stage!r} is not an agent stage")
    safe_agent = _validate_agent(agent)
    session_seed = task_session_id or f"auth-benchmark:{stage}"
    session_name = derive_session_name(session_seed)
    ensure = build_ensure_command(
        executable=executable,
        cwd=cwd,
        agent=safe_agent,
        session_name=session_name,
        permission_policy=permission_policy,
        mcp_config=mcp_config,
    )
    prompt = build_prompt_command(
        executable=executable,
        cwd=cwd,
        agent=safe_agent,
        session_name=session_name,
        permission_policy=permission_policy,
        mcp_config=mcp_config,
        timeout_seconds=timeout_seconds,
    )
    close = build_close_command(
        executable=executable,
        cwd=cwd,
        agent=safe_agent,
        session_name=session_name,
    )
    brief = _AGENT_STAGE_BRIEFS[stage]
    _reject_forbidden_markers(brief, label=f"stage brief {stage}")
    assert_artifact_is_safe(brief)
    return {
        "stage": stage,
        "harness": DEFAULT_HARNESS,
        "agent": safe_agent,
        "route": f"{DEFAULT_HARNESS}:{safe_agent}",
        "session_name": session_name,
        "prompt_brief": brief,
        "commands": {
            "ensure": list(ensure),
            "prompt": list(prompt),
            "close": list(close),
        },
        # Prompts are stdin-only; never persist raw prompt bodies.
        "prompt_transport": "stdin",
        "persist_prompt": False,
    }


def build_auth_acpx_pipeline_plan(
    *,
    mode: str = DEFAULT_MODE,
    max_concurrency: int = MAX_CONCURRENCY,
    repo_root: Path | None = None,
    agent: str = DEFAULT_AGENT,
    acpx_executable: str | Path | None = None,
    cwd: Path | None = None,
    permission_policy: Path | None = None,
    mcp_config: Path | None = None,
    task_session_id: str | None = None,
    python_executable: str | None = None,
    allowed_origins: Sequence[str] | None = None,
) -> AuthAcpxPipelinePlan:
    """Build the named Plan/Forge/lanes/IDR/Tribunal/report workflow plan."""
    safe_mode = _validate_mode(mode)
    concurrency = _validate_concurrency(max_concurrency)
    safe_agent = _validate_agent(agent)
    root = (repo_root or _repo_root()).resolve()
    work_cwd = (cwd or root).resolve()
    # Default is the repo-pinned ACPX CLI; execute requires it to be present+executable.
    resolved_acpx = resolve_acpx_executable(
        acpx_executable=acpx_executable,
        repo_root=root,
        require_executable=safe_mode == "execute",
    )

    if allowed_origins is not None:
        validate_benchmark_origins(list(allowed_origins))
        for origin in allowed_origins:
            _reject_forbidden_markers(origin, label="allowed origin")

    validate_lane_scenario_coverage()
    report_paths = [lane_report_path(lane) for lane in PARALLEL_BROWSER_LANES]
    if len(report_paths) != len(set(report_paths)):
        raise AuthAcpxPipelineError("parallel browser lanes must use distinct report paths")

    if safe_mode == "execute" and (permission_policy is None or mcp_config is None):
        raise AuthAcpxPipelineError(
            "execute mode requires permission_policy and mcp_config for ACPX agent stages"
        )

    stages: list[StageSpec] = []
    for name in PIPELINE_STAGES:
        if name in AGENT_STAGES:
            commands: list[tuple[str, ...]] = []
            session_name: str | None = None
            brief = _AGENT_STAGE_BRIEFS[name]
            _reject_forbidden_markers(brief, label=f"stage brief {name}")
            if permission_policy is not None and mcp_config is not None:
                built = build_agent_stage_commands(
                    stage=name,
                    cwd=work_cwd,
                    executable=resolved_acpx,
                    permission_policy=permission_policy,
                    mcp_config=mcp_config,
                    task_session_id=task_session_id or f"auth-benchmark:{name}",
                    agent=safe_agent,
                )
                session_name = str(built["session_name"])
                commands = [
                    tuple(built["commands"]["ensure"]),
                    tuple(built["commands"]["prompt"]),
                    tuple(built["commands"]["close"]),
                ]
            stages.append(
                StageSpec(
                    name=name,
                    kind="agent",
                    harness=DEFAULT_HARNESS,
                    agent=safe_agent,
                    route=f"{DEFAULT_HARNESS}:{safe_agent}",
                    parallel_group=None,
                    scenarios=(),
                    commands=tuple(commands),
                    prompt_brief=brief,
                    session_name=session_name,
                )
            )
            continue

        # Browser lane
        lane_cmd = build_browser_lane_command(
            lane=name,
            repo_root=root,
            python_executable=python_executable,
            scenarios=LANE_SCENARIOS[name],
            max_parallel=min(concurrency, len(LANE_SCENARIOS[name])),
        )
        stages.append(
            StageSpec(
                name=name,
                kind="browser",
                harness=None,
                agent=None,
                route=None,
                parallel_group="auth_lanes",
                scenarios=LANE_SCENARIOS[name],
                commands=(tuple(lane_cmd),),
                prompt_brief=None,
                session_name=None,
            )
        )

    return AuthAcpxPipelinePlan(
        mode=safe_mode,
        stages=tuple(stages),
        max_concurrency=concurrency,
        agent=safe_agent,
        harness=DEFAULT_HARNESS,
        parallel_lanes=PARALLEL_BROWSER_LANES,
        repo_root=root,
        would_execute=safe_mode == "execute",
        acpx_executable=resolved_acpx,
    )


def pipeline_stage_names(plan: AuthAcpxPipelinePlan) -> tuple[str, ...]:
    return tuple(stage.name for stage in plan.stages)


def agent_stage_routes(plan: AuthAcpxPipelinePlan) -> dict[str, str]:
    return {
        stage.name: str(stage.route)
        for stage in plan.stages
        if stage.kind == "agent" and stage.route is not None
    }


def report_to_text(report: AuthAcpxPipelineReport) -> str:
    """Render a redacted, metrics-only pipeline report suitable for artifacts."""
    # Avoid "name: value" shapes that trip secret scanners on stage ids like
    # password_lane / otp-adjacent labels. Use status= / detail= instead.
    lines = [
        f"auth_acpx_pipeline mode={report.mode} passed={str(report.passed).lower()}",
        f"agent_route={report.agent_route}",
        f"max_concurrency={report.max_concurrency}",
        "stages",
    ]
    for stage in report.stages:
        lines.append(f"  - name={stage.name} status={stage.status} detail={stage.detail}")
    if report.failures:
        lines.append("failures")
        for failure in report.failures:
            lines.append(f"  - {failure}")
    if report.notes:
        lines.append("notes")
        for note in report.notes:
            lines.append(f"  - {note}")
    text = "\n".join(lines) + "\n"
    assert_artifact_is_safe(text)
    _reject_forbidden_markers(text, label="pipeline report")
    return text


async def _default_command_runner(
    command: Sequence[str],
    *,
    stdin_text: str | None = None,
) -> dict[str, Any]:
    """Run one argv command without shell=True. Does not persist stdin_text."""
    if not command or any(not isinstance(part, str) for part in command):
        raise AuthAcpxPipelineError("command runner requires a non-empty argv list")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        payload = stdin_text.encode("utf-8") if stdin_text is not None else None
        stdout, stderr = await process.communicate(input=payload)
    except asyncio.CancelledError:
        process.kill()
        raise
    return {
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace")[:4_000],
        "stderr": stderr.decode("utf-8", errors="replace")[:4_000],
    }


CommandRunner = Callable[..., Awaitable[dict[str, Any]]]


async def run_auth_acpx_pipeline(
    plan: AuthAcpxPipelinePlan | None = None,
    *,
    mode: str | None = None,
    command_runner: CommandRunner | None = None,
    **plan_kwargs: Any,
) -> AuthAcpxPipelineReport:
    """Run or dry-run the auth ACPX pipeline. Default is non-executing plan mode."""
    if plan is None:
        effective_mode = _validate_mode(mode or DEFAULT_MODE)
        plan = build_auth_acpx_pipeline_plan(mode=effective_mode, **plan_kwargs)
    elif mode is not None and _validate_mode(mode) != plan.mode:
        raise AuthAcpxPipelineError("mode argument conflicts with provided plan.mode")

    runner = command_runner or _default_command_runner
    results: list[StageResult] = []
    failures: list[str] = []
    notes: list[str] = []

    if plan.mode in {"plan", "dry-run"}:
        status: Literal["planned", "dry_run"] = "planned" if plan.mode == "plan" else "dry_run"
        for stage in plan.stages:
            detail = (
                f"{stage.kind} stage routed via {stage.route}"
                if stage.kind == "agent"
                else f"browser lane scenario_count={len(stage.scenarios)}"
            )
            results.append(
                StageResult(
                    name=stage.name,
                    status=status,
                    detail=detail,
                    commands=stage.commands,
                )
            )
        notes.append("default non-executing mode; pass mode='execute' to run")
        report = AuthAcpxPipelineReport(
            mode=plan.mode,
            passed=True,
            stages=tuple(results),
            failures=(),
            max_concurrency=plan.max_concurrency,
            agent_route=AGENT_ROUTE,
            notes=tuple(notes),
        )
        report_to_text(report)
        return report

    # execute mode
    semaphore = asyncio.Semaphore(plan.max_concurrency)

    async def _run_agent(stage: StageSpec) -> StageResult:
        if not stage.commands or len(stage.commands) < 2:
            raise AuthAcpxPipelineError(
                f"agent stage {stage.name} missing ACPX commands; supply permission_policy/mcp_config"
            )
        ensure_cmd, prompt_cmd, *rest = stage.commands
        close_cmd = rest[0] if rest else ()
        async with semaphore:
            ensure_result = await runner(list(ensure_cmd))
            if ensure_result.get("returncode") not in (0, None):
                return StageResult(
                    name=stage.name,
                    status="failed",
                    detail=f"ensure failed rc={ensure_result.get('returncode')}",
                    commands=stage.commands,
                )
            # stdin-only prompt; never write the brief to disk
            prompt_result = await runner(
                list(prompt_cmd),
                stdin_text=stage.prompt_brief or "",
            )
            if close_cmd:
                await runner(list(close_cmd))
            if prompt_result.get("returncode") not in (0, None):
                return StageResult(
                    name=stage.name,
                    status="failed",
                    detail=f"prompt failed rc={prompt_result.get('returncode')}",
                    commands=stage.commands,
                )
            return StageResult(
                name=stage.name,
                status="executed",
                detail=f"acpx:{stage.agent} ok",
                commands=stage.commands,
            )

    async def _run_browser(stage: StageSpec) -> StageResult:
        if not stage.commands:
            raise AuthAcpxPipelineError(f"browser stage {stage.name} has no commands")
        command = list(stage.commands[0])
        async with semaphore:
            outcome = await runner(command)
        if outcome.get("returncode") not in (0, None):
            return StageResult(
                name=stage.name,
                status="failed",
                detail=f"browser lane failed rc={outcome.get('returncode')}",
                commands=stage.commands,
            )
        return StageResult(
            name=stage.name,
            status="executed",
            detail=f"browser lane ok scenario_count={len(stage.scenarios)}",
            commands=stage.commands,
        )

    # Sequential agent plan/forge, then parallel browser lanes, then gates/report.
    ordered_agent_prefix = [s for s in plan.stages if s.name in {STAGE_PLAN, STAGE_FORGE}]
    lane_stages = [s for s in plan.stages if s.name in PARALLEL_BROWSER_LANES]
    ordered_agent_suffix = [
        s for s in plan.stages if s.name in {STAGE_IDR_SECURITY_GATE, STAGE_TRIBUNAL, STAGE_RELEASE_REPORT}
    ]

    for stage in ordered_agent_prefix:
        result = await _run_agent(stage)
        results.append(result)
        if result.status == "failed":
            failures.append(f"{stage.name}: {result.detail}")
            break
    else:
        lane_results = await asyncio.gather(*[_run_browser(stage) for stage in lane_stages])
        results.extend(lane_results)
        for result in lane_results:
            if result.status == "failed":
                failures.append(f"{result.name}: {result.detail}")
        if not failures:
            for stage in ordered_agent_suffix:
                result = await _run_agent(stage)
                results.append(result)
                if result.status == "failed":
                    failures.append(f"{stage.name}: {result.detail}")
                    break

    report = AuthAcpxPipelineReport(
        mode=plan.mode,
        passed=not failures,
        stages=tuple(results),
        failures=tuple(failures),
        max_concurrency=plan.max_concurrency,
        agent_route=AGENT_ROUTE,
        notes=tuple(notes),
    )
    report_to_text(report)
    return report


def build_plan_or_dry_run(**kwargs: Any) -> AuthAcpxPipelinePlan:
    """Convenience entry used by callers that want the safe default non-execute plan."""
    mode = kwargs.pop("mode", DEFAULT_MODE)
    safe = _validate_mode(mode)
    if safe == "execute":
        raise AuthAcpxPipelineError(
            "build_plan_or_dry_run refuses execute; call build_auth_acpx_pipeline_plan(mode='execute')"
        )
    return build_auth_acpx_pipeline_plan(mode=safe, **kwargs)


__all__ = [
    "AGENT_ROUTE",
    "AGENT_STAGES",
    "ALLOWED_MODES",
    "DEFAULT_AGENT",
    "DEFAULT_HARNESS",
    "DEFAULT_MODE",
    "LANE_REPORT_PATHS",
    "LANE_SCENARIOS",
    "MAX_CONCURRENCY",
    "PARALLEL_BROWSER_LANES",
    "PINNED_ACPX_RELATIVE_PATH",
    "PIPELINE_STAGES",
    "REQUIRED_ACPX_VERSION",
    "STAGE_FORGE",
    "STAGE_IDR_SECURITY_GATE",
    "STAGE_OAUTH_LANE",
    "STAGE_PASSWORD_LANE",
    "STAGE_PLAN",
    "STAGE_RELEASE_REPORT",
    "STAGE_TRIBUNAL",
    "STAGE_WEBAUTHN_LANE",
    "AuthAcpxPipelineError",
    "AuthAcpxPipelinePlan",
    "AuthAcpxPipelineReport",
    "PipelineMode",
    "StageResult",
    "StageSpec",
    "agent_stage_routes",
    "build_agent_stage_commands",
    "build_auth_acpx_pipeline_plan",
    "build_browser_lane_command",
    "build_plan_or_dry_run",
    "lane_report_path",
    "pinned_acpx_executable",
    "pipeline_stage_names",
    "report_to_text",
    "resolve_acpx_executable",
    "run_auth_acpx_pipeline",
    "validate_lane_scenario_coverage",
]
