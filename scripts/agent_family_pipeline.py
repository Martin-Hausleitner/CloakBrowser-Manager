#!/usr/bin/env python3
"""Project-level ACPX agent-family DAG (Plan → Forge → parallel gates → IDR → Tribunal → Release).

Reuses:
- ``scripts.acpx_runner`` for ACPX ensure/prompt/close argv construction
- ``benchmarks.auth.acpx_pipeline`` for repo-pinned ACPX resolution (0.12.1)

Security contracts (fail closed):
- default mode is plan/dry-run; execute requires explicit mode + policy files
- agent stages always route via harness=acpx and agent in {grok-build, opencode}
- deterministic gates are argv-only (no LLM, no external auth)
- concurrency hard-capped at 4
- no npx/global ACPX fallback; stdin-only prompts; private metrics reports
- DAG must be acyclic with unique outputs; at most one browser-using stage
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

# Allow `python scripts/agent_family_pipeline.py` without prior PYTHONPATH.
_REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

# Reuse auth pipeline pin resolution so both pipelines share one contract.
from benchmarks.auth.acpx_pipeline import (
    PINNED_ACPX_RELATIVE_PATH as _AUTH_PINNED_PATH,
)
from benchmarks.auth.acpx_pipeline import (
    resolve_acpx_executable as _auth_resolve_acpx,
)
from scripts.acpx_runner import (
    ACPX_VERSION,
    build_close_command,
    build_ensure_command,
    build_prompt_command,
    derive_session_name,
    validate_acpx_agent_id,
)

Mode = Literal["plan", "dry-run", "execute"]
StageKind = Literal["agent", "deterministic"]

DEFAULT_MODE: Final[Mode] = "plan"
ALLOWED_MODES: Final[frozenset[str]] = frozenset({"plan", "dry-run", "execute"})
MAX_CONCURRENCY: Final[int] = 4
DEFAULT_AGENT: Final[str] = "grok-build"
DEFAULT_HARNESS: Final[str] = "acpx"
ALLOWED_AGENTS: Final[frozenset[str]] = frozenset({"grok-build", "opencode"})
REQUIRED_ACPX_VERSION: Final[str] = ACPX_VERSION
PINNED_ACPX_RELATIVE_PATH: Final[Path] = _AUTH_PINNED_PATH

STAGE_PLAN: Final[str] = "plan"
STAGE_FORGE: Final[str] = "forge"
STAGE_SKILL_BENCHMARK: Final[str] = "skill-benchmark"
STAGE_AUTH_BENCHMARK: Final[str] = "auth-benchmark"
STAGE_EXTENSION_SKIN: Final[str] = "extension-skin"
STAGE_SONIOX_CONTRACT: Final[str] = "soniox-contract"
STAGE_TOKEN_COST: Final[str] = "token-cost"
STAGE_IDR: Final[str] = "idr"
STAGE_TRIBUNAL: Final[str] = "tribunal"
STAGE_RELEASE: Final[str] = "release"

PIPELINE_STAGES: Final[tuple[str, ...]] = (
    STAGE_PLAN,
    STAGE_FORGE,
    STAGE_SKILL_BENCHMARK,
    STAGE_AUTH_BENCHMARK,
    STAGE_EXTENSION_SKIN,
    STAGE_SONIOX_CONTRACT,
    STAGE_TOKEN_COST,
    STAGE_IDR,
    STAGE_TRIBUNAL,
    STAGE_RELEASE,
)

AGENT_STAGES: Final[frozenset[str]] = frozenset(
    {
        STAGE_PLAN,
        STAGE_FORGE,
        STAGE_IDR,
        STAGE_TRIBUNAL,
        STAGE_RELEASE,
    }
)

PARALLEL_GATE_STAGES: Final[tuple[str, ...]] = (
    STAGE_SKILL_BENCHMARK,
    STAGE_AUTH_BENCHMARK,
    STAGE_EXTENSION_SKIN,
    STAGE_SONIOX_CONTRACT,
    STAGE_TOKEN_COST,
)

DETERMINISTIC_STAGES: Final[frozenset[str]] = frozenset(PARALLEL_GATE_STAGES)

# Only auth-benchmark may touch a browser; every other gate is non-browser.
_BROWSER_STAGES: Final[frozenset[str]] = frozenset({STAGE_AUTH_BENCHMARK})

_DEFAULT_EDGES: Final[Mapping[str, tuple[str, ...]]] = {
    STAGE_PLAN: (STAGE_FORGE,),
    STAGE_FORGE: PARALLEL_GATE_STAGES,
    STAGE_SKILL_BENCHMARK: (STAGE_IDR,),
    STAGE_AUTH_BENCHMARK: (STAGE_IDR,),
    STAGE_EXTENSION_SKIN: (STAGE_IDR,),
    STAGE_SONIOX_CONTRACT: (STAGE_IDR,),
    STAGE_TOKEN_COST: (STAGE_IDR,),
    STAGE_IDR: (STAGE_TRIBUNAL,),
    STAGE_TRIBUNAL: (STAGE_RELEASE,),
    STAGE_RELEASE: (),
}

_STAGE_OUTPUTS: Final[Mapping[str, Path]] = {
    name: Path(f"artifacts/agent-family/private/{name}-report.json")
    for name in PIPELINE_STAGES
}

_AGENT_STAGE_BRIEFS: Final[Mapping[str, str]] = {
    STAGE_PLAN: (
        "Plan the project-level ACPX agent-family DAG: Plan, Forge, parallel "
        "skill/auth/extension/Soniox/token gates, then IDR, Tribunal, Release. "
        "Route agent work only via ACPX with grok-build or opencode. No secrets."
    ),
    STAGE_FORGE: (
        "Forge the agent-family plan into executable stage work. Keep deterministic "
        "gates argv-only without LLM or external auth. No secret material."
    ),
    STAGE_IDR: (
        "Run the IDR gate over redacted agent-family evidence. Reject raw prompts, "
        "credentials, and any second browser spawn. Metrics and pass/fail only."
    ),
    STAGE_TRIBUNAL: (
        "Tribunal review of agent-family results. Confirm ACPX routing, concurrency "
        "cap of 4, unique private reports, and fail-closed gate behavior."
    ),
    STAGE_RELEASE: (
        "Produce a redacted release report for the agent-family pipeline. "
        "Stage statuses and metrics only; no secrets, cookies, or raw prompts."
    ),
}

_SECRET_NEEDLES: Final[tuple[str, ...]] = (
    "private_key",
    "privatekey",
    "authorization: bearer",
    "password=",
    "cookie=",
    "api_key=",
    "apikey=",
    "secret=",
)

REPORT_SCHEMA: Final[str] = "cloakbrowser.agent-family-pipeline.v1"
DEFAULT_OUTPUT: Final[Path] = Path(
    "artifacts/agent-family/private/agent-family-pipeline-report.json"
)

# Bounded deterministic gate execution (no unbounded pytest / live network).
GATE_TIMEOUT_SECONDS: Final[int] = 180
MAX_GATE_OUTPUT_CHARS: Final[int] = 4_000
QUALITY_GATE_REL: Final[Path] = Path("scripts") / "agent_family_quality_gate.py"
BUDGET_GATE_REL: Final[Path] = Path("scripts") / "agent_family_budget_gate.py"

# Bounded execute-path ACPX ensure/prompt/close wall-clock timeout.
# Prompt also carries ACPX argv --timeout; this is the process-level hard bound
# that covers ensure/close and any hung ACPX child regardless of argv.
DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS: Final[int] = 120
MIN_AGENT_COMMAND_TIMEOUT_SECONDS: Final[int] = 1
MAX_AGENT_COMMAND_TIMEOUT_SECONDS: Final[int] = 600
AGENT_KILL_GRACE_SECONDS: Final[float] = 2.0
AGENT_TIMEOUT_RETURNCODE: Final[int] = 124
MAX_AGENT_OUTPUT_CHARS: Final[int] = 4_000

_OUTPUT_REDACT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)(?:password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;]+"
    ),
)

# quality-gate check ids used by each parallel family gate.
_GATE_QUALITY_CHECKS: Final[Mapping[str, tuple[str, ...]]] = {
    STAGE_SKILL_BENCHMARK: ("skill_benchmark",),
    STAGE_AUTH_BENCHMARK: ("auth_benchmark_contracts",),
    STAGE_EXTENSION_SKIN: ("extension_skin", "secure_recorder"),
    STAGE_SONIOX_CONTRACT: ("soniox_adapter_contract",),
    STAGE_TOKEN_COST: ("budget_dedupe",),
}


class AgentFamilyPipelineError(ValueError):
    """Raised when the agent-family pipeline would violate a safety contract."""


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One named pipeline stage with routing and command material."""

    name: str
    kind: StageKind
    harness: str | None
    agent: str | None
    route: str | None
    parallel_group: str | None
    commands: tuple[tuple[str, ...], ...]
    prompt_brief: str | None = None
    session_name: str | None = None
    uses_browser: bool = False
    output_path: Path | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentFamilyPlan:
    """Immutable planned workflow for the project agent-family DAG."""

    mode: Mode
    stages: tuple[StageSpec, ...]
    max_concurrency: int
    agent_map: Mapping[str, str]
    harness: str
    parallel_gates: tuple[str, ...]
    repo_root: Path
    would_execute: bool
    acpx_executable: str
    edges: Mapping[str, tuple[str, ...]]
    agent_command_timeout: int = DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class StageResult:
    name: str
    status: Literal["planned", "dry_run", "executed", "skipped", "failed"]
    detail: str
    commands: tuple[tuple[str, ...], ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentFamilyReport:
    mode: Mode
    passed: bool
    stages: tuple[StageResult, ...]
    failures: tuple[str, ...]
    max_concurrency: int
    notes: tuple[str, ...] = field(default_factory=tuple)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_acpx_executable(
    *,
    acpx_executable: str | Path | None = None,
    repo_root: Path | None = None,
    require_executable: bool = False,
) -> str:
    """Resolve repo-pinned ACPX 0.12.1 (or absolute override). Never npx/global."""
    try:
        return _auth_resolve_acpx(
            acpx_executable=acpx_executable,
            repo_root=repo_root or _repo_root(),
            require_executable=require_executable,
        )
    except Exception as exc:
        # Normalize auth pipeline error type into this module's fail-closed type.
        raise AgentFamilyPipelineError(str(exc)) from exc


def _validate_mode(mode: str) -> Mode:
    value = str(mode or "").strip().lower()
    if value not in ALLOWED_MODES:
        raise AgentFamilyPipelineError(
            f"unsupported pipeline mode {mode!r}; expected one of {sorted(ALLOWED_MODES)}"
        )
    return value  # type: ignore[return-value]


def _validate_concurrency(max_concurrency: int) -> int:
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool):
        raise AgentFamilyPipelineError("max_concurrency must be a positive integer")
    if max_concurrency < 1:
        raise AgentFamilyPipelineError("max_concurrency must be >= 1")
    if max_concurrency > MAX_CONCURRENCY:
        raise AgentFamilyPipelineError(
            f"max_concurrency {max_concurrency} exceeds hard cap of {MAX_CONCURRENCY}"
        )
    return max_concurrency


def validate_agent_command_timeout(timeout_seconds: int) -> int:
    """Require a finite agent subprocess wall-clock timeout within documented bounds."""
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise AgentFamilyPipelineError(
            "agent_command_timeout must be an integer number of seconds"
        )
    if timeout_seconds < MIN_AGENT_COMMAND_TIMEOUT_SECONDS:
        raise AgentFamilyPipelineError(
            f"agent_command_timeout must be >= {MIN_AGENT_COMMAND_TIMEOUT_SECONDS}"
        )
    if timeout_seconds > MAX_AGENT_COMMAND_TIMEOUT_SECONDS:
        raise AgentFamilyPipelineError(
            f"agent_command_timeout must be <= {MAX_AGENT_COMMAND_TIMEOUT_SECONDS}"
        )
    return timeout_seconds


def _validate_agent(agent: str) -> str:
    value = validate_acpx_agent_id(agent)
    if value not in ALLOWED_AGENTS:
        raise AgentFamilyPipelineError(
            f"agent-family agent stages must use one of {sorted(ALLOWED_AGENTS)}; got {value!r}"
        )
    return value


def _reject_forbidden_markers(text: str, *, label: str) -> None:
    lowered = text.lower()
    for needle in _SECRET_NEEDLES:
        if needle in lowered:
            raise AgentFamilyPipelineError(f"{label} contains forbidden secret material")


def parse_agent_map(raw: str | Mapping[str, str] | None) -> dict[str, str]:
    """Parse ``--agent-map`` from JSON object string, file path, or mapping."""
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        text = str(raw).strip()
        if not text:
            return {}
        candidate = Path(text)
        if candidate.is_file():
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AgentFamilyPipelineError(
                    f"agent-map file must be valid JSON: {candidate}"
                ) from exc
        else:
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise AgentFamilyPipelineError(
                    "agent-map must be a JSON object or path to a JSON file"
                ) from exc
        if not isinstance(loaded, dict):
            raise AgentFamilyPipelineError("agent-map must be a JSON object")
        payload = loaded

    result: dict[str, str] = {}
    for key, value in payload.items():
        stage = str(key)
        if stage not in AGENT_STAGES:
            raise AgentFamilyPipelineError(
                f"agent-map key {stage!r} is not an agent stage"
            )
        result[stage] = _validate_agent(str(value))
    return result


def stage_output_path(stage: str) -> Path:
    """Return the distinct private report path for one pipeline stage."""
    if stage not in _STAGE_OUTPUTS:
        raise AgentFamilyPipelineError(f"unknown stage: {stage}")
    return _STAGE_OUTPUTS[stage]


def required_gate_files(gate: str, repo_root: Path | None = None) -> tuple[Path, ...]:
    """Return absolute paths that must exist before a gate may be evaluated."""
    if gate not in DETERMINISTIC_STAGES:
        raise AgentFamilyPipelineError(f"unknown gate: {gate}")
    root = (repo_root or _repo_root()).resolve()
    quality = root / QUALITY_GATE_REL
    common: list[Path] = [quality]
    if gate == STAGE_SKILL_BENCHMARK:
        common.extend(
            [
                root / ".agents" / "skills" / "acpx-agent-family" / "SKILL.md",
                root / ".agents" / "skills" / "acpx-agent-family" / "agents" / "openai.yaml",
            ]
        )
    elif gate == STAGE_AUTH_BENCHMARK:
        common.extend(
            [
                root / "benchmarks" / "auth" / "test_ci_contract.py",
                root / "benchmarks" / "auth" / "test_report.py",
                root / "benchmarks" / "auth" / "acpx_pipeline.py",
                root / "benchmarks" / "auth" / "requirements.txt",
            ]
        )
    elif gate == STAGE_EXTENSION_SKIN:
        common.extend(
            [
                root / "extensions" / "cloak-profile-sync" / "manifest.json",
                root / "scripts" / "secure_recording_compiler.py",
                root / "scripts" / "secure_recorder_watchdog.py",
                root / "scripts" / "test_secure_recording_compiler.py",
                root / "scripts" / "test_secure_recorder_watchdog.py",
            ]
        )
    elif gate == STAGE_SONIOX_CONTRACT:
        common.extend(
            [
                root / "scripts" / "soniox_stt_adapter.py",
                root / "scripts" / "test_soniox_stt_adapter.py",
            ]
        )
    elif gate == STAGE_TOKEN_COST:
        common.extend(
            [
                root / BUDGET_GATE_REL,
                root / "scripts" / "test_agent_family_budget_gate.py",
            ]
        )
    return tuple(common)


def _quality_gate_command(
    *,
    python: str,
    repo_root: Path,
    check_id: str,
    output: Path,
) -> list[str]:
    return [
        python,
        str((repo_root / QUALITY_GATE_REL).resolve()),
        "--repo",
        str(repo_root.resolve()),
        "--gate",
        check_id,
        "--output",
        str(output),
    ]


def _pytest_command(
    *,
    python: str,
    repo_root: Path,
    test_paths: Sequence[Path],
) -> list[str]:
    command = [
        python,
        "-m",
        "pytest",
        *[str(p.resolve()) for p in test_paths],
        "-q",
        "--tb=line",
        "--maxfail=1",
    ]
    # Ensure repo root is importable without shell env mutation beyond PYTHONPATH
    # is not set here — pytest collects from absolute paths under the repo.
    _ = repo_root  # reserved for future cwd pinning in the runner
    return command


def prepare_token_cost_fixture(work_dir: Path) -> tuple[Path, Path]:
    """Write a minimal metrics-only events+budget fixture (no secrets/network)."""
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    events_path = work_dir / "token-cost-events.json"
    budget_path = work_dir / "token-cost-budget.json"
    events = [
        {
            "event_id": "family-fixture-evt-1",
            "run_id": "family-fixture-run",
            "stage_id": "token-cost",
            "provider": "openai",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_tokens": 0,
            "source": "provider",
            "timestamp": "2026-01-01T00:00:00Z",
            "cost_microusd": 100,
        }
    ]
    budget = {
        "max_tokens_per_run": 1_000,
        "max_cost_microusd_per_run": 10_000,
    }
    events_path.write_text(json.dumps(events), encoding="utf-8")
    budget_path.write_text(json.dumps(budget), encoding="utf-8")
    try:
        events_path.chmod(0o600)
        budget_path.chmod(0o600)
    except OSError:
        pass
    return events_path, budget_path


def build_gate_validation_commands(
    *,
    gate: str,
    repo_root: Path | None = None,
    python_executable: str | None = None,
    work_dir: Path | None = None,
) -> list[list[str]]:
    """Build argv-only validation command lists for one deterministic gate.

    Never uses shell, never embeds secrets, never mutates browser profiles.
    """
    if gate not in DETERMINISTIC_STAGES:
        raise AgentFamilyPipelineError(f"stage {gate!r} is not a deterministic gate")
    root = (repo_root or _repo_root()).resolve()
    python = str(python_executable or sys.executable)
    work = (work_dir or (root / "artifacts" / "agent-family" / "private" / f"{gate}-work")).resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    commands: list[list[str]] = []

    for check_id in _GATE_QUALITY_CHECKS[gate]:
        commands.append(
            _quality_gate_command(
                python=python,
                repo_root=root,
                check_id=check_id,
                output=work / f"quality-{check_id}.json",
            )
        )

    if gate == STAGE_AUTH_BENCHMARK:
        commands.append(
            _pytest_command(
                python=python,
                repo_root=root,
                test_paths=(
                    root / "benchmarks" / "auth" / "test_ci_contract.py",
                    root / "benchmarks" / "auth" / "test_report.py",
                ),
            )
        )
    elif gate == STAGE_EXTENSION_SKIN:
        commands.append(
            _pytest_command(
                python=python,
                repo_root=root,
                test_paths=(
                    root / "scripts" / "test_secure_recording_compiler.py",
                    root / "scripts" / "test_secure_recorder_watchdog.py",
                ),
            )
        )
    elif gate == STAGE_SONIOX_CONTRACT:
        commands.append(
            _pytest_command(
                python=python,
                repo_root=root,
                test_paths=(root / "scripts" / "test_soniox_stt_adapter.py",),
            )
        )
    elif gate == STAGE_TOKEN_COST:
        events_path, budget_path = prepare_token_cost_fixture(work)
        commands.append(
            [
                python,
                str((root / BUDGET_GATE_REL).resolve()),
                "--events",
                str(events_path),
                "--budget",
                str(budget_path),
                "--output",
                str(work / "token-cost-budget-report.json"),
            ]
        )
        commands.append(
            _pytest_command(
                python=python,
                repo_root=root,
                test_paths=(root / "scripts" / "test_agent_family_budget_gate.py",),
            )
        )

    for command in commands:
        if any(part is None or not isinstance(part, str) for part in command):
            raise AgentFamilyPipelineError("gate validation command must be pure argv")
        if any("&&" in p or "|" in p or ";" in p for p in command):
            raise AgentFamilyPipelineError("gate validation command must not use shell metacharacters")
        _reject_forbidden_markers(" ".join(command), label=f"gate validation {gate}")
    return commands


def build_deterministic_lane_command(
    *,
    stage: str,
    repo_root: Path | None = None,
    python_executable: str | None = None,
    output: Path | None = None,
) -> list[str]:
    """Build argv-only command for one deterministic gate (no LLM, no auth)."""
    if stage not in DETERMINISTIC_STAGES:
        raise AgentFamilyPipelineError(f"stage {stage!r} is not a deterministic gate")
    root = (repo_root or _repo_root()).resolve()
    report_path = Path(output) if output is not None else stage_output_path(stage)
    # Outer entrypoint: runs real per-gate validations via the gate subcommand.
    command = [
        str(python_executable or sys.executable),
        str((root / "scripts" / "agent_family_pipeline.py").resolve()),
        "gate",
        "--gate",
        stage,
        "--output",
        str(report_path),
        "--repo-root",
        str(root),
    ]
    if any(part is None or not isinstance(part, str) for part in command):
        raise AgentFamilyPipelineError("deterministic command must be a pure argv list")
    joined = " ".join(command)
    _reject_forbidden_markers(joined, label=f"deterministic command {stage}")
    return command


def _truncate_output(text: str | None) -> str:
    value = str(text or "")
    if len(value) <= MAX_GATE_OUTPUT_CHARS:
        return value
    return value[:MAX_GATE_OUTPUT_CHARS] + "…[truncated]"


def _default_gate_subprocess_runner(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run one argv command with shell=False, bounded timeout and output."""
    if not command or any(not isinstance(part, str) for part in command):
        raise AgentFamilyPipelineError("gate subprocess runner requires a pure argv list")
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": _truncate_output(exc.stdout if isinstance(exc.stdout, str) else ""),
            "stderr": _truncate_output(
                f"timeout after {timeout_seconds}s: {exc.stderr if isinstance(exc.stderr, str) else ''}"
            ),
        }
    except FileNotFoundError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": _truncate_output(f"executable missing: {exc}"),
        }
    except OSError as exc:
        return {
            "returncode": 126,
            "stdout": "",
            "stderr": _truncate_output(f"os error: {exc}"),
        }
    return {
        "returncode": int(completed.returncode if completed.returncode is not None else 1),
        "stdout": _truncate_output(completed.stdout),
        "stderr": _truncate_output(completed.stderr),
    }


GateSubprocessRunner = Callable[..., dict[str, Any]]


def run_gate_validations(
    *,
    gate: str,
    repo_root: Path | None = None,
    output: Path,
    python_executable: str | None = None,
    timeout_seconds: int = GATE_TIMEOUT_SECONDS,
    command_runner: GateSubprocessRunner | None = None,
) -> dict[str, Any]:
    """Execute real bounded validations for one gate; derive pass/fail from rc.

    Fail-closed: missing required files => evaluated=false, passed=false.
    """
    if gate not in DETERMINISTIC_STAGES:
        raise AgentFamilyPipelineError(f"unknown gate: {gate}")
    root = (repo_root or _repo_root()).resolve()
    target = Path(output)
    work = (target.parent / f"{gate}-work").resolve()
    python = str(python_executable or sys.executable)
    runner = command_runner or (
        lambda cmd, **kwargs: _default_gate_subprocess_runner(
            cmd,
            timeout_seconds=int(kwargs.get("timeout_seconds", timeout_seconds)),
            cwd=root,
        )
    )

    required = required_gate_files(gate, root)
    missing = [str(path.relative_to(root) if path.is_relative_to(root) else path)
               for path in required if not path.is_file()]
    base: dict[str, Any] = {
        "schema": f"{REPORT_SCHEMA}.gate",
        "gate": gate,
        "kind": "deterministic",
        "uses_browser": gate in _BROWSER_STAGES,
        "evaluated": False,
        "passed": False,
        "command_count": 0,
        "failed_commands": [],
        "missing_files": missing,
        "timeout_seconds": timeout_seconds,
    }
    if missing:
        base["detail"] = f"required files missing (fail closed): {', '.join(missing)}"
        return base

    try:
        commands = build_gate_validation_commands(
            gate=gate,
            repo_root=root,
            python_executable=python,
            work_dir=work,
        )
    except AgentFamilyPipelineError as exc:
        base["detail"] = f"command construction failed: {exc}"
        return base

    if not commands:
        base["detail"] = "no validation commands constructed (unevaluated)"
        return base

    base["command_count"] = len(commands)
    failed: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        outcome = runner(list(command), timeout_seconds=timeout_seconds)
        rc = outcome.get("returncode")
        if rc not in (0, None):
            failed.append(
                {
                    "index": index,
                    "returncode": rc,
                    # argv0 + marker only — never full process streams in report
                    "argv0": command[0] if command else "",
                    "marker": next(
                        (
                            part
                            for part in command
                            if part.endswith(".py")
                            or part.startswith("test_")
                            or part
                            in {
                                "skill_benchmark",
                                "auth_benchmark_contracts",
                                "extension_skin",
                                "secure_recorder",
                                "soniox_adapter_contract",
                                "budget_dedupe",
                                "pytest",
                            }
                        ),
                        command[-1] if command else "",
                    ),
                }
            )

    base["evaluated"] = True
    base["passed"] = not failed
    base["failed_commands"] = failed
    base["detail"] = (
        f"gate ok commands={len(commands)}"
        if not failed
        else f"gate failed commands={len(failed)}/{len(commands)}"
    )
    return base


def write_gate_report(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a private gate report (0600) with metrics only."""
    data = dict(payload)
    # Never persist raw stdout/stderr/prompts.
    for forbidden in ("stdout", "stderr", "stdin_text", "prompt_brief", "commands"):
        data.pop(forbidden, None)
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    _reject_forbidden_markers(text, label="gate report")
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        target.chmod(0o600)
    except OSError:
        pass


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
    """Build ACPX ensure/prompt/close argv lists via ``scripts.acpx_runner``."""
    if stage not in AGENT_STAGES:
        raise AgentFamilyPipelineError(f"stage {stage!r} is not an agent stage")
    safe_agent = _validate_agent(agent)
    session_seed = task_session_id or f"agent-family:{stage}"
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
        "prompt_transport": "stdin",
        "persist_prompt": False,
    }


def _reverse_depends(
    edges: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    reverse: dict[str, list[str]] = {name: [] for name in PIPELINE_STAGES}
    for src, dests in edges.items():
        for dest in dests:
            reverse.setdefault(dest, []).append(src)
    return {k: tuple(v) for k, v in reverse.items()}


def validate_dag(
    plan: AgentFamilyPlan,
    *,
    edges: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Validate acyclic DAG, unique outputs, single browser; return topo order."""
    graph = {
        k: tuple(v)
        for k, v in (edges if edges is not None else plan.edges).items()
    }
    # Ensure all pipeline stages are present as nodes.
    for name in PIPELINE_STAGES:
        graph.setdefault(name, ())

    # Unique outputs
    outputs = [stage_output_path(s.name) for s in plan.stages]
    if len(outputs) != len(set(outputs)):
        raise AgentFamilyPipelineError("pipeline stages must use unique output paths")

    # At most one browser stage
    browser_stages = [s.name for s in plan.stages if s.uses_browser]
    if len(browser_stages) > 1:
        raise AgentFamilyPipelineError(
            f"no second browser allowed; browser stages={browser_stages!r}"
        )

    # No raw prompts / secrets in briefs
    for stage in plan.stages:
        if stage.prompt_brief:
            _reject_forbidden_markers(stage.prompt_brief, label=f"brief {stage.name}")

    # Kahn topological sort for cycle detection
    indegree: dict[str, int] = {n: 0 for n in graph}
    for src, dests in graph.items():
        if src not in indegree:
            indegree[src] = 0
        for dest in dests:
            indegree[dest] = indegree.get(dest, 0) + 1
    queue: deque[str] = deque(sorted(n for n, d in indegree.items() if d == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for dest in graph.get(node, ()):
            indegree[dest] -= 1
            if indegree[dest] == 0:
                queue.append(dest)
    if len(order) != len(indegree):
        raise AgentFamilyPipelineError(
            "agent-family DAG must be acyclic (cycle detected)"
        )
    # Prefer pipeline order when it is a valid topo order of the default graph.
    pipeline_set = set(PIPELINE_STAGES)
    if set(order) >= pipeline_set and edges is None:
        # Stable order matching PIPELINE_STAGES when default edges.
        return list(PIPELINE_STAGES)
    return order


def build_agent_family_plan(
    *,
    mode: str = DEFAULT_MODE,
    max_concurrency: int = MAX_CONCURRENCY,
    repo_root: Path | None = None,
    agent: str = DEFAULT_AGENT,
    agent_map: str | Mapping[str, str] | None = None,
    acpx_executable: str | Path | None = None,
    cwd: Path | None = None,
    permission_policy: Path | None = None,
    mcp_config: Path | None = None,
    task_session_id: str | None = None,
    python_executable: str | None = None,
    agent_command_timeout: int = DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS,
) -> AgentFamilyPlan:
    """Build the Plan/Forge/gates/IDR/Tribunal/Release DAG plan."""
    safe_mode = _validate_mode(mode)
    concurrency = _validate_concurrency(max_concurrency)
    agent_timeout = validate_agent_command_timeout(agent_command_timeout)
    default_agent = _validate_agent(agent)
    mapped = parse_agent_map(agent_map)
    root = (repo_root or _repo_root()).resolve()
    work_cwd = (cwd or root).resolve()
    resolved_acpx = resolve_acpx_executable(
        acpx_executable=acpx_executable,
        repo_root=root,
        require_executable=safe_mode == "execute",
    )

    outputs = [stage_output_path(name) for name in PIPELINE_STAGES]
    if len(outputs) != len(set(outputs)):
        raise AgentFamilyPipelineError("pipeline stages must use unique output paths")

    if safe_mode == "execute" and (permission_policy is None or mcp_config is None):
        raise AgentFamilyPipelineError(
            "execute mode requires permission_policy and mcp_config for ACPX agent stages"
        )

    edges = {k: tuple(v) for k, v in _DEFAULT_EDGES.items()}
    depends = _reverse_depends(edges)

    stages: list[StageSpec] = []
    for name in PIPELINE_STAGES:
        if name in AGENT_STAGES:
            stage_agent = mapped.get(name, default_agent)
            stage_agent = _validate_agent(stage_agent)
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
                    task_session_id=task_session_id or f"agent-family:{name}",
                    agent=stage_agent,
                    timeout_seconds=agent_timeout,
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
                    agent=stage_agent,
                    route=f"{DEFAULT_HARNESS}:{stage_agent}",
                    parallel_group=None,
                    commands=tuple(commands),
                    prompt_brief=brief,
                    session_name=session_name,
                    uses_browser=False,
                    output_path=stage_output_path(name),
                    depends_on=depends.get(name, ()),
                )
            )
            continue

        # Deterministic gate
        lane_cmd = build_deterministic_lane_command(
            stage=name,
            repo_root=root,
            python_executable=python_executable,
            output=stage_output_path(name),
        )
        stages.append(
            StageSpec(
                name=name,
                kind="deterministic",
                harness=None,
                agent=None,
                route=None,
                parallel_group="family_gates",
                commands=(tuple(lane_cmd),),
                prompt_brief=None,
                session_name=None,
                uses_browser=name in _BROWSER_STAGES,
                output_path=stage_output_path(name),
                depends_on=depends.get(name, ()),
            )
        )

    full_map = {name: mapped.get(name, default_agent) for name in AGENT_STAGES}
    plan = AgentFamilyPlan(
        mode=safe_mode,
        stages=tuple(stages),
        max_concurrency=concurrency,
        agent_map=full_map,
        harness=DEFAULT_HARNESS,
        parallel_gates=PARALLEL_GATE_STAGES,
        repo_root=root,
        would_execute=safe_mode == "execute",
        acpx_executable=resolved_acpx,
        edges=edges,
        agent_command_timeout=agent_timeout,
    )
    validate_dag(plan)
    return plan


def pipeline_stage_names(plan: AgentFamilyPlan) -> tuple[str, ...]:
    return tuple(stage.name for stage in plan.stages)


def agent_stage_routes(plan: AgentFamilyPlan) -> dict[str, str]:
    return {
        stage.name: str(stage.route)
        for stage in plan.stages
        if stage.kind == "agent" and stage.route is not None
    }


def report_to_text(report: AgentFamilyReport) -> str:
    """Render a redacted, metrics-only pipeline report."""
    lines = [
        f"agent_family_pipeline mode={report.mode} passed={str(report.passed).lower()}",
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
    _reject_forbidden_markers(text, label="pipeline report")
    return text


def _bound_and_redact_output(raw: bytes | str | None) -> str:
    """Bound process output and scrub secret-like tokens for metrics-only use."""
    if raw is None:
        text = ""
    elif isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    for pattern in _OUTPUT_REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > MAX_AGENT_OUTPUT_CHARS:
        text = text[:MAX_AGENT_OUTPUT_CHARS] + "…[truncated]"
    return text


async def _force_kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate then kill a start_new_session process group; never hang forever."""
    if process.returncode is not None:
        return
    pid = process.pid
    # Prefer process-group kill (start_new_session=True makes the child session leader).
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if process.returncode is not None:
            return
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=AGENT_KILL_GRACE_SECONDS)
            return
        except asyncio.TimeoutError:
            continue


async def _default_command_runner(
    command: Sequence[str],
    *,
    stdin_text: str | None = None,
    timeout_seconds: int = DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one argv command without shell=True under a finite wall-clock timeout.

    On timeout: SIGTERM the process group, bounded wait, then SIGKILL; drain
    stdout/stderr safely; return structured nonzero failure (rc=124).
    Prompts stay stdin-only and are never persisted.
    """
    if not command or any(not isinstance(part, str) for part in command):
        raise AgentFamilyPipelineError("command runner requires a non-empty argv list")
    timeout = validate_agent_command_timeout(int(timeout_seconds))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    payload = stdin_text.encode("utf-8") if stdin_text is not None else None
    timed_out = False
    stdout = b""
    stderr = b""
    try:
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=payload),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError:
            timed_out = True
            await _force_kill_process_group(process)
            try:
                # Drain remaining pipes after kill (first communicate was cancelled).
                drained = await asyncio.wait_for(
                    process.communicate(),
                    timeout=AGENT_KILL_GRACE_SECONDS,
                )
                stdout, stderr = drained
            except (asyncio.TimeoutError, OSError, ValueError, BrokenPipeError):
                stdout, stderr = b"", b"agent command timed out"
    except asyncio.CancelledError:
        await _force_kill_process_group(process)
        raise

    if timed_out:
        return {
            "returncode": AGENT_TIMEOUT_RETURNCODE,
            "stdout": _bound_and_redact_output(stdout),
            "stderr": _bound_and_redact_output(
                stderr if stderr else b"agent command timed out"
            ),
            "timed_out": True,
        }
    return {
        "returncode": process.returncode,
        "stdout": _bound_and_redact_output(stdout),
        "stderr": _bound_and_redact_output(stderr),
        "timed_out": False,
    }


CommandRunner = Callable[..., Awaitable[dict[str, Any]]]


def _stage_failure_detail(action: str, outcome: Mapping[str, Any]) -> str:
    if outcome.get("timed_out"):
        return f"{action} timed out rc={outcome.get('returncode')}"
    return f"{action} failed rc={outcome.get('returncode')}"


async def run_agent_family_pipeline(
    plan: AgentFamilyPlan | None = None,
    *,
    mode: str | None = None,
    command_runner: CommandRunner | None = None,
    **plan_kwargs: Any,
) -> AgentFamilyReport:
    """Run or dry-run the agent-family DAG. Default is non-executing plan mode."""
    if plan is None:
        effective_mode = _validate_mode(mode or DEFAULT_MODE)
        plan = build_agent_family_plan(mode=effective_mode, **plan_kwargs)
    elif mode is not None and _validate_mode(mode) != plan.mode:
        raise AgentFamilyPipelineError("mode argument conflicts with provided plan.mode")

    agent_timeout = validate_agent_command_timeout(plan.agent_command_timeout)

    async def _bounded_default_runner(
        command: Sequence[str],
        *,
        stdin_text: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return await _default_command_runner(
            command,
            stdin_text=stdin_text,
            timeout_seconds=agent_timeout,
        )

    runner = command_runner or _bounded_default_runner
    results: list[StageResult] = []
    failures: list[str] = []
    notes: list[str] = []

    if plan.mode in {"plan", "dry-run"}:
        status: Literal["planned", "dry_run"] = (
            "planned" if plan.mode == "plan" else "dry_run"
        )
        for stage in plan.stages:
            detail = (
                f"{stage.kind} stage routed via {stage.route}"
                if stage.kind == "agent"
                else f"deterministic gate uses_browser={str(stage.uses_browser).lower()}"
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
        report = AgentFamilyReport(
            mode=plan.mode,
            passed=True,
            stages=tuple(results),
            failures=(),
            max_concurrency=plan.max_concurrency,
            notes=tuple(notes),
        )
        report_to_text(report)
        return report

    # execute mode
    semaphore = asyncio.Semaphore(plan.max_concurrency)
    by_name = {s.name: s for s in plan.stages}

    async def _run_agent(stage: StageSpec) -> StageResult:
        if not stage.commands or len(stage.commands) < 2:
            raise AgentFamilyPipelineError(
                f"agent stage {stage.name} missing ACPX commands; "
                "supply permission_policy/mcp_config"
            )
        ensure_cmd, prompt_cmd, *rest = stage.commands
        close_cmd = rest[0] if rest else ()
        async with semaphore:
            ensure_result = await runner(list(ensure_cmd))
            if ensure_result.get("returncode") not in (0, None):
                return StageResult(
                    name=stage.name,
                    status="failed",
                    detail=_stage_failure_detail("ensure", ensure_result),
                    commands=stage.commands,
                )
            # stdin-only prompt; wall-clock timeout applies via runner
            prompt_result = await runner(
                list(prompt_cmd),
                stdin_text=stage.prompt_brief or "",
            )
            close_result: dict[str, Any] | None = None
            if close_cmd:
                close_result = await runner(list(close_cmd))
            if prompt_result.get("returncode") not in (0, None):
                return StageResult(
                    name=stage.name,
                    status="failed",
                    detail=_stage_failure_detail("prompt", prompt_result),
                    commands=stage.commands,
                )
            if close_result is not None and close_result.get("returncode") not in (
                0,
                None,
            ):
                return StageResult(
                    name=stage.name,
                    status="failed",
                    detail=_stage_failure_detail("close", close_result),
                    commands=stage.commands,
                )
            return StageResult(
                name=stage.name,
                status="executed",
                detail=f"acpx:{stage.agent} ok",
                commands=stage.commands,
            )

    async def _run_deterministic(stage: StageSpec) -> StageResult:
        if not stage.commands:
            raise AgentFamilyPipelineError(
                f"deterministic stage {stage.name} has no commands"
            )
        command = list(stage.commands[0])
        async with semaphore:
            outcome = await runner(command)
        if outcome.get("returncode") not in (0, None):
            return StageResult(
                name=stage.name,
                status="failed",
                detail=f"gate failed rc={outcome.get('returncode')}",
                commands=stage.commands,
            )
        return StageResult(
            name=stage.name,
            status="executed",
            detail=f"deterministic gate ok uses_browser={str(stage.uses_browser).lower()}",
            commands=stage.commands,
            artifacts=(str(stage.output_path),) if stage.output_path else (),
        )

    # Sequential Plan → Forge
    for name in (STAGE_PLAN, STAGE_FORGE):
        stage = by_name[name]
        result = await _run_agent(stage)
        results.append(result)
        if result.status == "failed":
            failures.append(f"{stage.name}: {result.detail}")
            break
    else:
        # Parallel deterministic gates
        gate_stages = [by_name[name] for name in PARALLEL_GATE_STAGES]
        gate_results = await asyncio.gather(
            *[_run_deterministic(stage) for stage in gate_stages]
        )
        results.extend(gate_results)
        for result in gate_results:
            if result.status == "failed":
                failures.append(f"{result.name}: {result.detail}")
        if not failures:
            # Sequential IDR → Tribunal → Release
            for name in (STAGE_IDR, STAGE_TRIBUNAL, STAGE_RELEASE):
                stage = by_name[name]
                result = await _run_agent(stage)
                results.append(result)
                if result.status == "failed":
                    failures.append(f"{stage.name}: {result.detail}")
                    break

    report = AgentFamilyReport(
        mode=plan.mode,
        passed=not failures,
        stages=tuple(results),
        failures=tuple(failures),
        max_concurrency=plan.max_concurrency,
        notes=tuple(notes),
    )
    report_to_text(report)
    return report


def build_plan_or_dry_run(**kwargs: Any) -> AgentFamilyPlan:
    """Safe default non-execute plan builder."""
    mode = kwargs.pop("mode", DEFAULT_MODE)
    safe = _validate_mode(mode)
    if safe == "execute":
        raise AgentFamilyPipelineError(
            "build_plan_or_dry_run refuses execute; "
            "call build_agent_family_plan(mode='execute')"
        )
    return build_agent_family_plan(mode=safe, **kwargs)


def _run_gate_cli(argv: Sequence[str]) -> int:
    """Deterministic gate subcommand: real argv-only checks, metrics-only report."""
    parser = argparse.ArgumentParser(prog="agent_family_pipeline gate")
    parser.add_argument("--gate", required=True, choices=list(PARALLEL_GATE_STAGES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument(
        "--timeout",
        type=int,
        default=GATE_TIMEOUT_SECONDS,
        help=f"Per-command timeout seconds (default {GATE_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args(list(argv))
    if args.gate not in DETERMINISTIC_STAGES:
        print(f"unknown gate: {args.gate}", file=sys.stderr)
        return 2
    if not isinstance(args.timeout, int) or args.timeout < 1:
        print("timeout must be a positive integer", file=sys.stderr)
        return 2

    try:
        payload = run_gate_validations(
            gate=args.gate,
            repo_root=args.repo_root,
            output=args.output,
            timeout_seconds=args.timeout,
        )
        write_gate_report(args.output, payload)
    except AgentFamilyPipelineError as exc:
        print(f"agent_family_pipeline_error: {exc}", file=sys.stderr)
        return 2

    evaluated = bool(payload.get("evaluated"))
    passed = bool(payload.get("passed")) and evaluated
    detail = str(payload.get("detail") or "")
    print(
        f"gate={args.gate} evaluated={str(evaluated).lower()} "
        f"passed={str(passed).lower()} detail={detail}",
        file=sys.stdout,
    )
    # Unevaluated or failed both non-zero so execute path stops before IDR.
    return 0 if passed else 1


def build_metrics_payload(report: AgentFamilyReport, *, acpx_executable: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "mode": report.mode,
        "passed": report.passed,
        "stage_count": len(report.stages),
        "max_concurrency": report.max_concurrency,
        "acpx_executable": acpx_executable,
        "failures": list(report.failures),
        "stages": [
            {
                "name": stage.name,
                "status": stage.status,
                "kind": (
                    "agent"
                    if stage.name in AGENT_STAGES
                    else "deterministic"
                ),
            }
            for stage in report.stages
        ],
    }
    encoded = json.dumps(payload, sort_keys=True)
    for forbidden in ("prompt_brief", "stdin_text", "stdout", "stderr", "commands"):
        if forbidden in encoded:
            raise AgentFamilyPipelineError(f"metrics payload must not contain {forbidden}")
    _reject_forbidden_markers(encoded, label="metrics payload")
    return payload


def write_private_report(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _reject_forbidden_markers(text, label="private report")
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        # Parent may be a shared directory (e.g. /tmp); file mode still fail-closes.
        pass
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or dry-run (default) the project ACPX agent-family DAG; "
            "execute only with explicit --mode execute and absolute policy files."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("plan", "dry-run", "execute"),
        default=DEFAULT_MODE,
        help="Pipeline mode (default: plan)",
    )
    parser.add_argument(
        "--agent-map",
        default=None,
        help=(
            "JSON object or path mapping agent stages to grok-build|opencode "
            f"(default: all {DEFAULT_AGENT})"
        ),
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=MAX_CONCURRENCY,
        help=f"Gate concurrency 1..{MAX_CONCURRENCY} (default: {MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--permission-policy",
        type=Path,
        help="Absolute path to ACPX permission policy (required for execute)",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        help="Absolute path to ACPX MCP config (required for execute)",
    )
    parser.add_argument(
        "--acpx",
        type=Path,
        help="Optional absolute override for ACPX executable (default: repo-pinned)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Metrics-only report path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS,
        help=(
            "Wall-clock seconds for each ACPX ensure/prompt/close subprocess "
            f"({MIN_AGENT_COMMAND_TIMEOUT_SECONDS}..{MAX_AGENT_COMMAND_TIMEOUT_SECONDS}; "
            f"default {DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS})"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    # Subcommand: gate (deterministic lane entry)
    if raw and raw[0] == "gate":
        return _run_gate_cli(raw[1:])

    parser = build_parser()
    args = parser.parse_args(raw)

    if (
        not isinstance(args.parallel, int)
        or args.parallel < 1
        or args.parallel > MAX_CONCURRENCY
    ):
        parser.error(f"--parallel must be an integer between 1 and {MAX_CONCURRENCY}")

    try:
        # Fail closed on out-of-range agent timeout (strict min/max bounds).
        agent_timeout = validate_agent_command_timeout(args.agent_timeout)

        permission_policy = None
        mcp_config = None
        if args.permission_policy is not None:
            if not args.permission_policy.is_absolute():
                raise AgentFamilyPipelineError("--permission-policy must be an absolute path")
            permission_policy = args.permission_policy
        if args.mcp_config is not None:
            if not args.mcp_config.is_absolute():
                raise AgentFamilyPipelineError("--mcp-config must be an absolute path")
            mcp_config = args.mcp_config

        if args.mode == "execute" and (
            permission_policy is None or mcp_config is None
        ):
            raise AgentFamilyPipelineError(
                "execute mode requires absolute --permission-policy and --mcp-config"
            )

        acpx_override = None
        if args.acpx is not None:
            if not args.acpx.is_absolute():
                raise AgentFamilyPipelineError("--acpx must be an absolute path")
            acpx_override = args.acpx

        agent_map = parse_agent_map(args.agent_map) if args.agent_map else {}
        resolved_acpx = resolve_acpx_executable(
            acpx_executable=acpx_override,
            require_executable=args.mode == "execute",
        )

        report = asyncio.run(
            run_agent_family_pipeline(
                mode=args.mode,
                max_concurrency=args.parallel,
                acpx_executable=resolved_acpx,
                permission_policy=permission_policy,
                mcp_config=mcp_config,
                agent_map=agent_map,
                agent_command_timeout=agent_timeout,
            )
        )
        report_to_text(report)
        payload = build_metrics_payload(report, acpx_executable=resolved_acpx)
        write_private_report(args.output, payload)

        routes = sorted(
            {
                f"acpx:{agent}"
                for agent in (agent_map.values() or [DEFAULT_AGENT])
            }
        )
        route_summary = ",".join(routes) if routes else f"acpx:{DEFAULT_AGENT}"
        print(
            f"mode={report.mode} passed={str(report.passed).lower()} "
            f"stage_count={len(report.stages)} route={route_summary}"
        )
        return 0 if report.passed else 1
    except AgentFamilyPipelineError as exc:
        print(f"agent_family_pipeline_error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"agent_family_pipeline_error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGENT_STAGES",
    "AGENT_TIMEOUT_RETURNCODE",
    "ALLOWED_AGENTS",
    "ALLOWED_MODES",
    "DEFAULT_AGENT",
    "DEFAULT_AGENT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_HARNESS",
    "DEFAULT_MODE",
    "DETERMINISTIC_STAGES",
    "GATE_TIMEOUT_SECONDS",
    "MAX_AGENT_COMMAND_TIMEOUT_SECONDS",
    "MAX_CONCURRENCY",
    "MIN_AGENT_COMMAND_TIMEOUT_SECONDS",
    "PARALLEL_GATE_STAGES",
    "PINNED_ACPX_RELATIVE_PATH",
    "PIPELINE_STAGES",
    "REQUIRED_ACPX_VERSION",
    "STAGE_AUTH_BENCHMARK",
    "STAGE_EXTENSION_SKIN",
    "STAGE_FORGE",
    "STAGE_IDR",
    "STAGE_PLAN",
    "STAGE_RELEASE",
    "STAGE_SKILL_BENCHMARK",
    "STAGE_SONIOX_CONTRACT",
    "STAGE_TOKEN_COST",
    "STAGE_TRIBUNAL",
    "AgentFamilyPipelineError",
    "AgentFamilyPlan",
    "AgentFamilyReport",
    "StageResult",
    "StageSpec",
    "agent_stage_routes",
    "build_agent_family_plan",
    "build_agent_stage_commands",
    "build_deterministic_lane_command",
    "build_gate_validation_commands",
    "build_plan_or_dry_run",
    "main",
    "parse_agent_map",
    "pipeline_stage_names",
    "prepare_token_cost_fixture",
    "report_to_text",
    "required_gate_files",
    "resolve_acpx_executable",
    "run_agent_family_pipeline",
    "run_gate_validations",
    "stage_output_path",
    "validate_agent_command_timeout",
    "validate_dag",
    "write_gate_report",
]
