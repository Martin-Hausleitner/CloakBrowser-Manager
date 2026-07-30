#!/usr/bin/env python3
"""Deterministic quality gate for ACPX agent-family surfaces.

Each check has two phases:
  1) static surface validation (paths, pins, allowlists)
  2) executed allowlisted deterministic command (pytest / node --test)

Pass requires BOTH phases. Default (CI/full) always executes real
downstream tests and fails when a test file contains a failure.

Never uses shell=True, never installs browser extensions, never mutates
profiles, never reads env secrets into reports, never emits traces/HAR.
Metrics-only JSON report (mode 0600). Bounded timeout and output capture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA = "cloakbrowser.agent-family-quality-gate.v1"
SCHEMA_VERSION = 2

CHECK_IDS: tuple[str, ...] = (
    "skill_benchmark",
    "acpx_runtime_lock",
    "auth_benchmark_contracts",
    "soniox_adapter_contract",
    "budget_dedupe",
    "extension_skin",
    "secure_recorder",
    "pipeline_plan",
)

REQUIRED_ACPX_VERSION = "0.12.1"
SKILL_REL = Path(".agents/skills/acpx-agent-family")
EXTENSION_ROOT_REL = Path("extensions")
KNOWN_EXTENSION = "cloak-profile-sync"

DEFAULT_TIMEOUT_S = 120.0
MAX_OUTPUT_BYTES = 24_000

FORBIDDEN_REPORT_MARKERS = (
    "password=",
    "api_key",
    "authorization",
    "bearer ",
    "cookie=",
    "secretref-",
)

# Subprocess argv must not contain these tokens (shell injection / live browser).
FORBIDDEN_ARGV_TOKENS = frozenset(
    {
        "shell=True",
        "--trace",
        "--video",
        "--har",
        "storage-state",
        "accounts.google.com",
    }
)

SECRET_ENV_NEEDLES = (
    "SECRET",
    "TOKEN",
    "API_KEY",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "AUTH",
)


class GateError(RuntimeError):
    """Deterministic quality-gate failure."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    id: str
    passed: bool
    detail: str
    static_ok: bool = True
    executed: bool = False
    exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "detail": self.detail,
            "static_ok": self.static_ok,
            "executed": self.executed,
            "exit_code": self.exit_code,
        }


@dataclass(frozen=True, slots=True)
class ExtensionSkin:
    name: str
    path: Path
    manifest_path: Path
    has_tests: bool
    has_scripts: bool
    has_package_json: bool


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool
    output_excerpt: str


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _redact(text: str) -> str:
    """Redact secret-like tokens and absolute home paths from stderr/report text."""
    out = str(text)
    out = re.sub(
        r"(?i)(api[_-]?key|token|password|secret|authorization|bearer)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        out,
    )
    out = re.sub(r"/home/[^/\s]+", "/home/[REDACTED]", out)
    out = re.sub(r"/Users/[^/\s]+", "/Users/[REDACTED]", out)
    if len(out) > MAX_OUTPUT_BYTES:
        out = out[:MAX_OUTPUT_BYTES] + "…[truncated]"
    return out


def _parse_skill_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    data: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def _scrub_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    for key in list(env):
        upper = key.upper()
        if any(needle in upper for needle in SECRET_ENV_NEEDLES):
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Do not set PYTHONNOUSERSITE: CI/user installs of pytest live in user site.
    env.pop("PYTHONNOUSERSITE", None)
    return env


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if not argv:
        raise GateError("empty command argv")
    cleaned: list[str] = []
    for part in argv:
        if not isinstance(part, str) or not part:
            raise GateError("command argv entries must be non-empty strings")
        if any(tok in part for tok in ("|", ";", "&&", "||", "`", "$(", "\n", "\r")):
            raise GateError("shell metacharacters are not allowed in argv")
        lowered = part.lower()
        for forbidden in FORBIDDEN_ARGV_TOKENS:
            if forbidden.lower() in lowered:
                raise GateError(f"forbidden argv token: {forbidden}")
        cleaned.append(part)
    return tuple(cleaned)


def run_allowlisted_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a pre-validated argv list with shell=False, bounded timeout/output."""
    cmd = _validate_argv(argv)
    child_env = _scrub_env(env)
    # Prefer repo (+ scripts/) on PYTHONPATH for package imports.
    existing = child_env.get("PYTHONPATH", "")
    repo_s = str(cwd)
    scripts_s = str(cwd / "scripts")
    prefix = f"{repo_s}{os.pathsep}{scripts_s}"
    child_env["PYTHONPATH"] = (
        prefix if not existing else f"{prefix}{os.pathsep}{existing}"
    )
    try:
        completed = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        excerpt = _redact(f"TIMEOUT after {timeout_s}s\n{stdout}\n{stderr}")
        return CommandResult(cmd, 124, True, excerpt)
    except OSError as exc:
        return CommandResult(cmd, 127, False, _redact(f"exec_error: {exc}"))

    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return CommandResult(
        cmd,
        int(completed.returncode),
        False,
        _redact(combined),
    )


def _pytest_argv(repo: Path, *relative_tests: str) -> list[str]:
    paths: list[str] = []
    for rel in relative_tests:
        path = (repo / rel).resolve()
        if not path.is_file():
            raise GateError(f"allowlisted test missing: {rel}")
        # Must stay under repo root.
        path.relative_to(repo.resolve())
        paths.append(str(path))
    return [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-q",
        "--tb=line",
        "-p",
        "no:cacheprovider",
    ]


def _node_bin() -> str:
    node = shutil.which("node")
    if not node:
        raise GateError("node executable not found on PATH")
    return node


# ---------------------------------------------------------------------------
# Static surfaces
# ---------------------------------------------------------------------------


def _static_skill(repo: Path) -> CheckResult:
    skill_dir = repo / SKILL_REL
    skill_md = skill_dir / "SKILL.md"
    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not skill_dir.is_dir():
        return CheckResult(
            "skill_benchmark",
            False,
            f"installable skill directory missing: {SKILL_REL}",
            static_ok=False,
        )
    missing = []
    if not _is_file(skill_md):
        missing.append("SKILL.md")
    if not _is_file(openai_yaml):
        missing.append("agents/openai.yaml")
    if missing:
        return CheckResult(
            "skill_benchmark",
            False,
            f"skill package incomplete: missing {', '.join(missing)}",
            static_ok=False,
        )
    front = _parse_skill_frontmatter(_read_text(skill_md))
    if not front:
        return CheckResult(
            "skill_benchmark",
            False,
            "SKILL.md must start with YAML frontmatter (---)",
            static_ok=False,
        )
    if not front.get("name", "").strip() or not front.get("description", "").strip():
        return CheckResult(
            "skill_benchmark",
            False,
            "SKILL.md frontmatter requires name and description",
            static_ok=False,
        )
    agent_text = _read_text(openai_yaml)
    if "display_name" not in agent_text and "display-name" not in agent_text:
        return CheckResult(
            "skill_benchmark",
            False,
            "agents/openai.yaml must declare interface.display_name",
            static_ok=False,
        )
    return CheckResult(
        "skill_benchmark",
        True,
        f"skill static ok name={front['name'].strip()}",
        static_ok=True,
    )


def _static_acpx_runtime_lock(repo: Path) -> CheckResult:
    required = [
        repo / "scripts" / "acpx_runtime_lock.py",
        repo / "scripts" / "test_acpx_runtime_lock.py",
        repo / "deploy" / "acpx-runtime" / "package.json",
        repo / "deploy" / "acpx-runtime" / "package-lock.json",
        repo / "scripts" / "requirements-acpx-worker.in",
        repo / "scripts" / "requirements-acpx-worker.linux-x86_64.py312.txt",
    ]
    missing = [str(p.relative_to(repo)) for p in required if not _is_file(p)]
    if missing:
        return CheckResult(
            "acpx_runtime_lock",
            False,
            f"runtime lock surfaces missing: {', '.join(missing)}",
            static_ok=False,
        )
    lock_src = _read_text(repo / "scripts" / "acpx_runtime_lock.py")
    if REQUIRED_ACPX_VERSION not in lock_src:
        return CheckResult(
            "acpx_runtime_lock",
            False,
            f"acpx_runtime_lock.py must pin ACPX_VERSION={REQUIRED_ACPX_VERSION}",
            static_ok=False,
        )
    package = json.loads(_read_text(repo / "deploy" / "acpx-runtime" / "package.json"))
    if package.get("private") is not True:
        return CheckResult(
            "acpx_runtime_lock",
            False,
            "deploy/acpx-runtime/package.json must be private",
            static_ok=False,
        )
    acpx_ver = (package.get("dependencies") or {}).get("acpx")
    if acpx_ver != REQUIRED_ACPX_VERSION:
        return CheckResult(
            "acpx_runtime_lock",
            False,
            f"package.json acpx must be {REQUIRED_ACPX_VERSION}, got {acpx_ver!r}",
            static_ok=False,
        )
    lock = json.loads(_read_text(repo / "deploy" / "acpx-runtime" / "package-lock.json"))
    if lock.get("lockfileVersion") != 3:
        return CheckResult(
            "acpx_runtime_lock",
            False,
            "package-lock.json lockfileVersion must be 3",
            static_ok=False,
        )
    return CheckResult(
        "acpx_runtime_lock",
        True,
        f"runtime lock static ok version={REQUIRED_ACPX_VERSION}",
        static_ok=True,
    )


def _static_auth(repo: Path) -> CheckResult:
    auth = repo / "benchmarks" / "auth"
    required = [
        auth / "requirements.txt",
        auth / "test_ci_contract.py",
        auth / "acpx_pipeline.py",
        auth / "test_acpx_pipeline.py",
        auth / "report.py",
        auth / "test_report.py",
    ]
    missing = [str(p.relative_to(repo)) for p in required if not _is_file(p)]
    if missing:
        return CheckResult(
            "auth_benchmark_contracts",
            False,
            f"auth benchmark contracts missing: {', '.join(missing)}",
            static_ok=False,
        )
    req_lines = [
        line.strip()
        for line in _read_text(auth / "requirements.txt").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not req_lines:
        return CheckResult(
            "auth_benchmark_contracts",
            False,
            "benchmarks/auth/requirements.txt is empty",
            static_ok=False,
        )
    unpinned = [line for line in req_lines if "==" not in line]
    if unpinned:
        return CheckResult(
            "auth_benchmark_contracts",
            False,
            f"auth requirements must be pinned with ==: {unpinned[0]!r}",
            static_ok=False,
        )
    joined = "\n".join(req_lines).lower()
    for package in ("pytest==", "playwright==", "cryptography=="):
        if package not in joined:
            return CheckResult(
                "auth_benchmark_contracts",
                False,
                f"auth requirements missing pinned {package}",
                static_ok=False,
            )
    return CheckResult(
        "auth_benchmark_contracts",
        True,
        f"auth static ok pins={len(req_lines)}",
        static_ok=True,
    )


def _discover_soniox_files(repo: Path) -> tuple[list[Path], list[Path]]:
    scripts = repo / "scripts"
    adapters: list[Path] = []
    tests: list[Path] = []
    if not scripts.is_dir():
        return adapters, tests
    for path in sorted(scripts.glob("*soniox*")):
        if not path.is_file() or not path.name.endswith(".py"):
            continue
        if path.name.startswith("test_"):
            tests.append(path)
        else:
            adapters.append(path)
    return adapters, tests


def _static_soniox(repo: Path) -> CheckResult:
    adapters, tests = _discover_soniox_files(repo)
    if not adapters:
        return CheckResult(
            "soniox_adapter_contract",
            False,
            "soniox adapter missing under scripts/*soniox*.py (fail closed)",
            static_ok=False,
        )
    if not tests:
        return CheckResult(
            "soniox_adapter_contract",
            False,
            "soniox adapter tests missing under scripts/test_*soniox*.py",
            static_ok=False,
        )
    adapter = adapters[0]
    text = _read_text(adapter)
    if re.search(r"shell\s*=\s*True", text):
        return CheckResult(
            "soniox_adapter_contract",
            False,
            f"{adapter.name} must not use shell=True",
            static_ok=False,
        )
    contract_markers = (
        "SCHEMA",
        "ALLOWED_MODELS",
        "DEFAULT_MODEL",
        "AdapterReport",
        "probe(",
        "metrics only",
        "metrics_only",
    )
    if not any(marker in text for marker in contract_markers):
        return CheckResult(
            "soniox_adapter_contract",
            False,
            f"{adapter.name} missing soniox contract marker",
            static_ok=False,
        )
    if re.search(
        r"add_argument\(\s*[\"']--(api-key|apikey|soniox-api-key|token|key)[\"']",
        text,
    ):
        return CheckResult(
            "soniox_adapter_contract",
            False,
            f"{adapter.name} must not accept raw API keys on argv",
            static_ok=False,
        )
    return CheckResult(
        "soniox_adapter_contract",
        True,
        f"soniox static ok adapter={adapter.name} tests={tests[0].name}",
        static_ok=True,
    )


def _static_budget(repo: Path) -> CheckResult:
    gate = repo / "scripts" / "agent_family_budget_gate.py"
    test = repo / "scripts" / "test_agent_family_budget_gate.py"
    missing = []
    if not _is_file(gate):
        missing.append("scripts/agent_family_budget_gate.py")
    if not _is_file(test):
        missing.append("scripts/test_agent_family_budget_gate.py")
    if missing:
        return CheckResult(
            "budget_dedupe",
            False,
            f"budget/dedupe surfaces missing: {', '.join(missing)}",
            static_ok=False,
        )
    src = _read_text(gate)
    for marker in ("BudgetMeter", "SOURCE_PRECEDENCE", "KNOWN_PROVIDERS"):
        if marker not in src:
            return CheckResult(
                "budget_dedupe",
                False,
                f"agent_family_budget_gate.py missing {marker}",
                static_ok=False,
            )
    if "dedup" not in _read_text(test).lower():
        return CheckResult(
            "budget_dedupe",
            False,
            "test_agent_family_budget_gate.py must cover dedupe",
            static_ok=False,
        )
    return CheckResult("budget_dedupe", True, "budget static ok", static_ok=True)


def discover_extension_skins(repo: Path) -> list[ExtensionSkin]:
    root = repo / EXTENSION_ROOT_REL
    if not root.is_dir():
        return []
    skins: list[ExtensionSkin] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        test_dir = child / "test"
        scripts_dir = child / "scripts"
        has_tests = False
        if test_dir.is_dir():
            has_tests = any(test_dir.glob("*.test.js")) or any(
                test_dir.glob("test_*.py")
            )
        skins.append(
            ExtensionSkin(
                name=child.name,
                path=child,
                manifest_path=child / "manifest.json",
                has_tests=has_tests,
                has_scripts=scripts_dir.is_dir()
                and any(scripts_dir.iterdir())
                if scripts_dir.exists()
                else False,
                has_package_json=_is_file(child / "package.json"),
            )
        )
    return skins


def _static_extension(repo: Path) -> CheckResult:
    skins = discover_extension_skins(repo)
    if not skins:
        return CheckResult(
            "extension_skin",
            False,
            "no extension/skin surfaces under extensions/ (fail closed)",
            static_ok=False,
        )
    known = [s for s in skins if s.name == KNOWN_EXTENSION]
    if not known:
        return CheckResult(
            "extension_skin",
            False,
            f"required extension skin missing: extensions/{KNOWN_EXTENSION}",
            static_ok=False,
        )
    skin = known[0]
    failures: list[str] = []
    if not _is_file(skin.manifest_path):
        failures.append("manifest.json missing")
    else:
        try:
            manifest = json.loads(_read_text(skin.manifest_path))
        except json.JSONDecodeError as exc:
            failures.append(f"manifest.json invalid JSON: {exc}")
            manifest = {}
        if manifest.get("manifest_version") != 3:
            failures.append("manifest_version must be 3")
        if not manifest.get("name"):
            failures.append("manifest name required")
        bg = manifest.get("background") or {}
        if not (bg.get("service_worker") or bg.get("scripts")):
            failures.append("background service_worker required")
    if not skin.has_package_json:
        failures.append("package.json missing")
    else:
        package = json.loads(_read_text(skin.path / "package.json"))
        if "test" not in (package.get("scripts") or {}):
            failures.append("package.json scripts.test missing")
    if not skin.has_tests:
        failures.append("static tests missing under test/*.test.js")
    if not skin.has_scripts:
        failures.append("scripts/ directory missing or empty")
    for rel in (
        "background/service-worker.js",
        "content/recorder-content.js",
        "lib/action-recorder.js",
        "lib/local-control.js",
        "host/local_bridge.py",
        "scripts/verify_against_manager.py",
    ):
        if not _is_file(skin.path / rel):
            failures.append(f"missing {rel}")
    if failures:
        return CheckResult(
            "extension_skin",
            False,
            f"extension skin {skin.name} fail closed: {'; '.join(failures)}",
            static_ok=False,
        )
    return CheckResult(
        "extension_skin",
        True,
        f"extension static ok name={skin.name}",
        static_ok=True,
    )


def _static_secure_recorder(repo: Path) -> CheckResult:
    required = [
        repo / "scripts" / "secure_recording_compiler.py",
        repo / "scripts" / "secure_recorder_watchdog.py",
        repo / "scripts" / "test_secure_recording_compiler.py",
        repo / "scripts" / "test_secure_recorder_watchdog.py",
        repo / "scripts" / "test_acpx_secure_recorder_ci.py",
    ]
    missing = [str(p.relative_to(repo)) for p in required if not _is_file(p)]
    if missing:
        return CheckResult(
            "secure_recorder",
            False,
            f"secure recorder surfaces missing: {', '.join(missing)}",
            static_ok=False,
        )
    compiler = _read_text(repo / "scripts" / "secure_recording_compiler.py")
    if "RECORDING_SCHEMA" not in compiler and "secure-action-recording" not in compiler:
        return CheckResult(
            "secure_recorder",
            False,
            "secure_recording_compiler.py missing recording schema contract",
            static_ok=False,
        )
    has_forbidden_guard = any(
        marker in compiler
        for marker in (
            "FORBIDDEN_KEYS",
            "FORBIDDEN",
            "_reject_forbidden",
            "RecordingCompileError",
        )
    )
    if not has_forbidden_guard and "forbidden" not in compiler.lower():
        return CheckResult(
            "secure_recorder",
            False,
            "secure_recording_compiler.py must reject forbidden raw fields",
            static_ok=False,
        )
    return CheckResult("secure_recorder", True, "secure recorder static ok", static_ok=True)


def _static_pipeline(repo: Path) -> CheckResult:
    pipeline = repo / "scripts" / "agent_family_pipeline.py"
    test = repo / "scripts" / "test_agent_family_pipeline.py"
    missing = []
    if not _is_file(pipeline):
        missing.append("scripts/agent_family_pipeline.py")
    if not _is_file(test):
        missing.append("scripts/test_agent_family_pipeline.py")
    if missing:
        return CheckResult(
            "pipeline_plan",
            False,
            f"pipeline plan surfaces missing: {', '.join(missing)}",
            static_ok=False,
        )
    src = _read_text(pipeline)
    for marker in (
        "PIPELINE_STAGES",
        "PARALLEL_GATE_STAGES",
        "STAGE_SKILL_BENCHMARK",
        "STAGE_SONIOX_CONTRACT",
        "STAGE_TOKEN_COST",
        "build_agent_family_plan",
        "DEFAULT_MODE",
    ):
        if marker not in src:
            return CheckResult(
                "pipeline_plan",
                False,
                f"agent_family_pipeline.py missing {marker}",
                static_ok=False,
            )
    if re.search(r'DEFAULT_MODE\s*[:=]\s*["\']execute["\']', src):
        return CheckResult(
            "pipeline_plan",
            False,
            "DEFAULT_MODE must not be execute",
            static_ok=False,
        )
    code_lines = [
        line
        for line in src.splitlines()
        if not line.lstrip().startswith("#")
        and '"""' not in line
        and "'''" not in line
    ]
    if re.search(r"shell\s*=\s*True", "\n".join(code_lines)):
        return CheckResult(
            "pipeline_plan",
            False,
            "pipeline must not use shell=True",
            static_ok=False,
        )
    return CheckResult("pipeline_plan", True, "pipeline static ok", static_ok=True)


_STATIC_FUNCS: Mapping[str, Callable[[Path], CheckResult]] = {
    "skill_benchmark": _static_skill,
    "acpx_runtime_lock": _static_acpx_runtime_lock,
    "auth_benchmark_contracts": _static_auth,
    "soniox_adapter_contract": _static_soniox,
    "budget_dedupe": _static_budget,
    "extension_skin": _static_extension,
    "secure_recorder": _static_secure_recorder,
    "pipeline_plan": _static_pipeline,
}


# ---------------------------------------------------------------------------
# Executed allowlisted commands
# ---------------------------------------------------------------------------


def _commands_for_check(check_id: str, repo: Path) -> list[list[str]]:
    """Return allowlisted argv lists for a check (no shell)."""
    if check_id == "skill_benchmark":
        skill_test = SKILL_REL / "scripts" / "test_skill_scripts.py"
        if _is_file(repo / skill_test):
            return [_pytest_argv(repo, str(skill_test))]
        # Fixture / minimal skill: allow a single local skill smoke test path.
        alt = SKILL_REL / "scripts" / "test_skill_smoke.py"
        if _is_file(repo / alt):
            return [_pytest_argv(repo, str(alt))]
        raise GateError("skill tests missing (scripts/test_skill_scripts.py)")

    if check_id == "acpx_runtime_lock":
        return [_pytest_argv(repo, "scripts/test_acpx_runtime_lock.py")]

    if check_id == "auth_benchmark_contracts":
        # Unit/contract only — never live browser scenarios.
        tests = [
            "benchmarks/auth/test_ci_contract.py",
            "benchmarks/auth/test_report.py",
            "benchmarks/auth/test_policy.py",
            "benchmarks/auth/test_acpx_pipeline.py",
        ]
        present = [t for t in tests if _is_file(repo / t)]
        if not present:
            raise GateError("no auth unit/contract tests present")
        return [_pytest_argv(repo, *present)]

    if check_id == "soniox_adapter_contract":
        _adapters, tests = _discover_soniox_files(repo)
        if not tests:
            raise GateError("soniox tests missing")
        rel = str(tests[0].relative_to(repo))
        return [_pytest_argv(repo, rel)]

    if check_id == "budget_dedupe":
        return [_pytest_argv(repo, "scripts/test_agent_family_budget_gate.py")]

    if check_id == "extension_skin":
        skin = repo / "extensions" / KNOWN_EXTENSION
        test_dir = skin / "test"
        tests = sorted(test_dir.glob("*.test.js")) if test_dir.is_dir() else []
        if not tests:
            raise GateError("extension test/*.test.js missing")
        node = _node_bin()
        # node --test with explicit files (no shell glob).
        return [[node, "--test", *[str(p) for p in tests]]]

    if check_id == "secure_recorder":
        return [
            _pytest_argv(
                repo,
                "scripts/test_secure_recording_compiler.py",
                "scripts/test_secure_recorder_watchdog.py",
            )
        ]

    if check_id == "pipeline_plan":
        return [_pytest_argv(repo, "scripts/test_agent_family_pipeline.py")]

    raise GateError(f"no allowlisted commands for {check_id}")


def _combine_static_and_exec(
    static: CheckResult,
    *,
    exec_results: Sequence[CommandResult],
) -> CheckResult:
    if not static.static_ok or not static.passed:
        return CheckResult(
            static.id,
            False,
            f"static_failed: {static.detail}",
            static_ok=False,
            executed=False,
            exit_code=None,
        )
    if not exec_results:
        return CheckResult(
            static.id,
            False,
            "executed_failed: no allowlisted commands ran",
            static_ok=True,
            executed=False,
            exit_code=None,
        )
    for result in exec_results:
        if result.timed_out or result.exit_code != 0:
            tail = result.output_excerpt.strip().splitlines()
            tail_s = tail[-1] if tail else "no output"
            return CheckResult(
                static.id,
                False,
                f"executed_failed exit={result.exit_code} timed_out={result.timed_out} "
                f"cmd={' '.join(result.argv[:4])}… last={tail_s}",
                static_ok=True,
                executed=True,
                exit_code=result.exit_code,
            )
    return CheckResult(
        static.id,
        True,
        f"static+executed ok ({len(exec_results)} cmd) {static.detail}",
        static_ok=True,
        executed=True,
        exit_code=0,
    )


def run_one_check(
    check_id: str,
    repo_root: Path,
    *,
    execute: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CheckResult:
    if check_id not in _STATIC_FUNCS:
        return CheckResult(check_id, False, f"unknown check id: {check_id}", static_ok=False)
    root = Path(repo_root).resolve()
    static = _STATIC_FUNCS[check_id](root)
    if not execute:
        # Explicit static-only mode (not default): never claim executed.
        return CheckResult(
            static.id,
            static.passed,
            f"static_only: {static.detail}",
            static_ok=static.static_ok and static.passed,
            executed=False,
            exit_code=None,
        )
    if not static.passed:
        return CheckResult(
            static.id,
            False,
            f"static_failed: {static.detail}",
            static_ok=False,
            executed=False,
            exit_code=None,
        )
    try:
        commands = _commands_for_check(check_id, root)
    except GateError as exc:
        return CheckResult(
            check_id,
            False,
            f"executed_failed: {_redact(str(exc))}",
            static_ok=True,
            executed=False,
            exit_code=None,
        )
    results = [
        run_allowlisted_command(cmd, cwd=root, timeout_s=timeout_s) for cmd in commands
    ]
    return _combine_static_and_exec(static, exec_results=results)


# Back-compat aliases used by older tests.
def check_skill_benchmark(repo: Path) -> CheckResult:
    return run_one_check("skill_benchmark", repo, execute=True)


def check_extension_skin(repo: Path) -> CheckResult:
    return run_one_check("extension_skin", repo, execute=True)


def run_quality_gate(
    *,
    repo_root: Path | None = None,
    gates: Sequence[str] | None = None,
    execute: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    root = Path(repo_root or _repo_root_default()).resolve()
    selected = list(gates) if gates else list(CHECK_IDS)
    results: list[CheckResult] = []
    for check_id in selected:
        if check_id not in _STATIC_FUNCS:
            results.append(
                CheckResult(
                    check_id,
                    False,
                    f"unknown check id: {check_id}",
                    static_ok=False,
                )
            )
            continue
        results.append(
            run_one_check(
                check_id,
                root,
                execute=execute,
                timeout_s=timeout_s,
            )
        )
    failures = [f"{r.id}: {r.detail}" for r in results if not r.passed]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "passed": not failures,
        "metrics_only": True,
        "execute": bool(execute),
        "repo": str(root),
        "check_count": len(results),
        "passed_count": sum(1 for r in results if r.passed),
        "failed_count": len(failures),
        "executed_count": sum(1 for r in results if r.executed),
        "checks": [r.as_dict() for r in results],
        "failures": failures,
    }
    _assert_metrics_only(report)
    return report


def _assert_metrics_only(report: Mapping[str, Any]) -> None:
    blob = json.dumps(report, sort_keys=True).lower()
    for marker in FORBIDDEN_REPORT_MARKERS:
        if marker in blob:
            raise GateError(f"report contains forbidden marker {marker!r}")


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    _assert_metrics_only(report)
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(dict(report), indent=2, sort_keys=True) + "\n"
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
                fd = -1
        finally:
            if fd >= 0:
                os.close(fd)
        os.chmod(target, 0o600)
    except OSError as exc:
        raise GateError(f"report write failed: {_redact(str(exc))}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic ACPX agent-family quality gate "
            "(static + executed allowlisted tests; metrics-only)."
        )
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for 0600 metrics-only JSON report.",
    )
    parser.add_argument(
        "--gate",
        action="append",
        dest="gates",
        choices=list(CHECK_IDS),
        help="Run only the named check (repeatable). Default: all checks.",
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="Run static surface checks only (not default; CI must not use this).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-command timeout seconds (default {DEFAULT_TIMEOUT_S}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    execute = not bool(args.static_only)
    try:
        report = run_quality_gate(
            repo_root=args.repo,
            gates=args.gates,
            execute=execute,
            timeout_s=float(args.timeout),
        )
    except (GateError, OSError, json.JSONDecodeError, ValueError) as exc:
        fail = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "metrics_only": True,
            "execute": execute,
            "check_count": 0,
            "passed_count": 0,
            "failed_count": 1,
            "executed_count": 0,
            "checks": [],
            "failures": [f"gate_error: {type(exc).__name__}: {_redact(str(exc))}"],
        }
        if args.output is not None:
            try:
                write_report(args.output, fail)
            except GateError as write_exc:
                print(
                    f"agent_family_quality_gate: failed writing error report: "
                    f"{_redact(str(write_exc))}",
                    file=sys.stderr,
                )
                print(
                    f"agent_family_quality_gate: {type(exc).__name__}: {_redact(str(exc))}",
                    file=sys.stderr,
                )
                return 2
        print(
            f"agent_family_quality_gate: {type(exc).__name__}: {_redact(str(exc))}",
            file=sys.stderr,
        )
        return 2

    if args.output is not None:
        try:
            write_report(args.output, report)
        except GateError as write_exc:
            print(
                f"agent_family_quality_gate: report write failed: {_redact(str(write_exc))}",
                file=sys.stderr,
            )
            # Gate result still printed; write failure is a hard error.
            print(
                f"passed={str(report['passed']).lower()} "
                f"checks={report['check_count']} "
                f"failed={report['failed_count']} "
                f"executed={report['executed_count']}",
                file=sys.stdout,
            )
            return 2

    print(
        f"passed={str(report['passed']).lower()} "
        f"checks={report['check_count']} "
        f"failed={report['failed_count']} "
        f"executed={report['executed_count']}",
        file=sys.stdout,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
