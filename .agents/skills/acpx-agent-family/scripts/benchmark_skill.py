#!/usr/bin/env python3
"""Benchmark acpx-agent-family skill quality in an isolated temp workspace.

Scores (each 0 or 1; report includes total):
  structure, trigger_metadata, no_todos, executable_scripts,
  dry_run_install, plan_generation, forbidden_secret_scan, forward_test_fixture

Plan generation is cross-checked against independently authored golden and
negative fixtures under references/fixtures/ (not tautological self-checks).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Prevent accidental .pyc writes during import/benchmark. Never delete source
# tree evidence — structure scoring fails closed if bytecode is already present.
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from install_and_verify import (
    SKILL_NAME,
    InstallError,
    install_skill,
    skill_root,
)

SCORE_KEYS: tuple[str, ...] = (
    "structure",
    "trigger_metadata",
    "no_todos",
    "executable_scripts",
    "dry_run_install",
    "plan_generation",
    "forbidden_secret_scan",
    "forward_test_fixture",
)

REQUIRED_PATHS: tuple[str, ...] = (
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/install_and_verify.py",
    "scripts/benchmark_skill.py",
    "references/architecture.md",
    "references/quality-gates.md",
    "references/fixtures/golden_plan.json",
    "references/fixtures/negative_wrong_dag_order.json",
    "references/fixtures/negative_non_acpx_route.json",
    "references/fixtures/negative_excessive_concurrency.json",
    "references/fixtures/negative_unsafe_execute.json",
)

NEGATIVE_FIXTURE_NAMES: tuple[str, ...] = (
    "negative_wrong_dag_order.json",
    "negative_non_acpx_route.json",
    "negative_excessive_concurrency.json",
    "negative_unsafe_execute.json",
)

PIPELINE_STAGES: tuple[str, ...] = (
    "plan",
    "forge",
    "skill-benchmark",
    "auth-benchmark",
    "extension-skin",
    "soniox-contract",
    "token-cost",
    "idr",
    "tribunal",
    "release",
)

AGENT_STAGES: frozenset[str] = frozenset(
    {"plan", "forge", "idr", "tribunal", "release"}
)
PARALLEL_GATES: tuple[str, ...] = (
    "skill-benchmark",
    "auth-benchmark",
    "extension-skin",
    "soniox-contract",
    "token-cost",
)
ALLOWED_AGENTS: frozenset[str] = frozenset({"grok-build", "opencode"})
DEFAULT_HARNESS = "acpx"
DEFAULT_AGENT = "grok-build"
MAX_CONCURRENCY = 4

FORBIDDEN_SECRET_NEEDLES: tuple[str, ...] = (
    "private_key",
    "privatekey",
    "authorization: bearer",
    "password=",
    "cookie=",
    "api_key=",
    "apikey=",
    "secret=",
    "-----begin " + "private key-----",
    "-----begin rsa " + "private key-----",
    "sk-ant-",
    "sk-proj-",
)

_UNFINISHED_TOKENS = ("TO" + "DO", "FIX" + "ME", "X" * 3)
UNFINISHED_MARKER_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _UNFINISHED_TOKENS) + r")\b",
    re.IGNORECASE,
)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

BYTECODE_PARTS = frozenset({"__pycache__"})
BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})


@dataclass
class ScoreResult:
    name: str
    passed: bool
    detail: str
    weight: int = 1


@dataclass
class BenchmarkReport:
    skill: str
    scores: list[ScoreResult] = field(default_factory=list)
    total: int = 0
    max_total: int = 0
    passed: bool = False
    workspace: str = ""
    notes: list[str] = field(default_factory=list)

    def add(self, result: ScoreResult) -> None:
        self.scores.append(result)
        self.max_total += result.weight
        if result.passed:
            self.total += result.weight


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fixtures_dir(skill_path: Path | None = None) -> Path:
    root = skill_path or skill_root()
    return root / "references" / "fixtures"


def resolved_os_temp_root() -> Path:
    """Canonical OS temp root (follows system path aliases like macOS /var)."""
    return Path(tempfile.gettempdir()).resolve()


def resolved_temporary_directory(
    prefix: str = "acpx-agent-family-",
) -> tempfile.TemporaryDirectory[str]:
    """tempfile under the *resolved* OS temp root.

    On macOS, tempfile often returns paths under /var/folders while /var is a
    symlink to /private/var. Creating under resolve(gettempdir()) avoids a
    false fail-closed install rejection without relaxing installer security.
    """
    return tempfile.TemporaryDirectory(
        prefix=prefix, dir=str(resolved_os_temp_root())
    )


def find_bytecode_paths(root: Path) -> list[str]:
    """Return relative paths of __pycache__ dirs and bytecode files (read-only)."""
    hits: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.name == "__pycache__" or any(
            part in BYTECODE_PARTS for part in path.parts
        ):
            hits.append(rel)
            continue
        if path.is_file() and path.suffix in BYTECODE_SUFFIXES:
            hits.append(rel)
    # Stable unique order
    return sorted(set(hits))


def load_json_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"fixture must be a JSON object: {path}")
    return data


def _validate_policy_file(path: Path | str, *, label: str) -> str:
    """Require absolute, non-symlink, existing JSON policy/config file."""
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError(f"{label} must be an absolute path, got {path!r}")
    try:
        if stat.S_ISLNK(os.lstat(raw).st_mode):
            raise ValueError(f"{label} must not be a symlink: {raw}")
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {raw}") from exc
    if not raw.is_file():
        raise ValueError(f"{label} must be a regular file: {raw}")
    try:
        payload = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid JSON: {raw}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON root must be an object: {raw}")
    return str(raw)


def generate_agent_family_plan(
    *,
    mode: str = "plan",
    agent: str = DEFAULT_AGENT,
    agent_map: dict[str, str] | None = None,
    max_concurrency: int = MAX_CONCURRENCY,
    permission_policy: Path | str | None = None,
    mcp_config: Path | str | None = None,
) -> dict[str, Any]:
    """Generate a portable agent-family plan fixture (no network, no secrets).

    mode=execute is fail-closed: requires absolute permission_policy and
    mcp_config files validated on disk before would_execute can be true.
    """
    if mode not in {"plan", "dry-run", "execute"}:
        raise ValueError(f"unsupported mode: {mode}")
    if max_concurrency < 1 or max_concurrency > MAX_CONCURRENCY:
        raise ValueError(f"max_concurrency must be 1..{MAX_CONCURRENCY}")
    if agent not in ALLOWED_AGENTS:
        raise ValueError(f"agent must be one of {sorted(ALLOWED_AGENTS)}")

    policy_path: str | None = None
    mcp_path: str | None = None
    would_execute = False

    if mode == "execute":
        if permission_policy is None or mcp_config is None:
            raise ValueError(
                "execute mode requires absolute permission_policy and mcp_config"
            )
        policy_path = _validate_policy_file(
            permission_policy, label="permission_policy"
        )
        mcp_path = _validate_policy_file(mcp_config, label="mcp_config")
        would_execute = True
    else:
        # Portable plan/dry-run never claims execution.
        would_execute = False
        if permission_policy is not None or mcp_config is not None:
            # Allow attaching paths for documentation, but still not execute.
            if permission_policy is not None:
                policy_path = _validate_policy_file(
                    permission_policy, label="permission_policy"
                )
            if mcp_config is not None:
                mcp_path = _validate_policy_file(mcp_config, label="mcp_config")

    mapping = dict(agent_map or {})
    for stage, value in mapping.items():
        if stage not in AGENT_STAGES:
            raise ValueError(f"agent-map key not an agent stage: {stage}")
        if value not in ALLOWED_AGENTS:
            raise ValueError(f"disallowed agent for {stage}: {value}")

    stages: list[dict[str, Any]] = []
    for name in PIPELINE_STAGES:
        if name in AGENT_STAGES:
            chosen = mapping.get(name, agent)
            stages.append(
                {
                    "name": name,
                    "kind": "agent",
                    "harness": DEFAULT_HARNESS,
                    "agent": chosen,
                    "route": f"{DEFAULT_HARNESS}:{chosen}",
                    "parallel_group": None,
                    "output": f"artifacts/agent-family/private/{name}-report.json",
                    "uses_browser": False,
                }
            )
        else:
            stages.append(
                {
                    "name": name,
                    "kind": "deterministic",
                    "harness": None,
                    "agent": None,
                    "route": None,
                    "parallel_group": "family_gates",
                    "output": f"artifacts/agent-family/private/{name}-report.json",
                    "uses_browser": name == "auth-benchmark",
                }
            )

    edges = {
        "plan": ["forge"],
        "forge": list(PARALLEL_GATES),
        "skill-benchmark": ["idr"],
        "auth-benchmark": ["idr"],
        "extension-skin": ["idr"],
        "soniox-contract": ["idr"],
        "token-cost": ["idr"],
        "idr": ["tribunal"],
        "tribunal": ["release"],
        "release": [],
    }
    return {
        "schema": "cloakbrowser.agent-family-pipeline.v1",
        "mode": mode,
        "would_execute": would_execute,
        "max_concurrency": max_concurrency,
        "harness": DEFAULT_HARNESS,
        "permission_policy": policy_path,
        "mcp_config": mcp_path,
        "allowed_agents": sorted(ALLOWED_AGENTS),
        "parallel_gates": list(PARALLEL_GATES),
        "stages": stages,
        "edges": edges,
        "notes": [
            "Agent stages route only via ACPX (grok-build|opencode).",
            "Deterministic gates are argv-only (no LLM, no external auth).",
            "Tribunal must pass before Release.",
            "Fail-closed: execute requires absolute policy + MCP files.",
            "Fail-closed: no secrets in plans, prompts, or reports.",
        ],
    }


def validate_plan_fixture(plan: dict[str, Any]) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors: list[str] = []
    stages = plan.get("stages") or []
    names = [s.get("name") for s in stages]
    if tuple(names) != PIPELINE_STAGES:
        errors.append(f"stage order mismatch: {names}")
    if plan.get("max_concurrency", 99) > MAX_CONCURRENCY:
        errors.append("max_concurrency exceeds hard cap")
    if plan.get("harness") != DEFAULT_HARNESS:
        errors.append("harness must be acpx")
    browser_stages = [s["name"] for s in stages if s.get("uses_browser")]
    if browser_stages != ["auth-benchmark"]:
        errors.append(
            f"browser stages must be only auth-benchmark, got {browser_stages}"
        )
    outputs = [s.get("output") for s in stages]
    if len(outputs) != len(set(outputs)):
        errors.append("stage outputs must be unique")
    for s in stages:
        if s.get("kind") == "agent":
            if s.get("harness") != "acpx":
                errors.append(f"{s.get('name')}: agent harness must be acpx")
            if s.get("agent") not in ALLOWED_AGENTS:
                errors.append(f"{s.get('name')}: agent not allowed")
            if not str(s.get("route", "")).startswith("acpx:"):
                errors.append(f"{s.get('name')}: route must start with acpx:")
        if s.get("kind") == "deterministic":
            if s.get("name") not in PARALLEL_GATES:
                errors.append(f"unexpected deterministic stage {s.get('name')}")
            if s.get("harness") is not None or s.get("agent") is not None:
                errors.append(
                    f"{s.get('name')}: deterministic must not set agent/harness"
                )
    edges = plan.get("edges") or {}
    if "release" not in (edges.get("tribunal") or []):
        errors.append("tribunal must precede release")
    if edges.get("release"):
        errors.append("release must be terminal")

    # Unsafe execute: would_execute without absolute policy paths.
    if plan.get("would_execute") or plan.get("mode") == "execute":
        for key in ("permission_policy", "mcp_config"):
            value = plan.get(key)
            if not value or not isinstance(value, str) or not value.startswith("/"):
                errors.append(
                    f"unsafe execute: {key} must be an absolute path when "
                    "mode=execute or would_execute=true"
                )
    return errors


def structural_keys_match_golden(
    generated: dict[str, Any], golden: dict[str, Any]
) -> list[str]:
    """Compare generator output to hand-authored golden (independent contract)."""
    errors: list[str] = []
    for key in (
        "schema",
        "mode",
        "would_execute",
        "max_concurrency",
        "harness",
        "parallel_gates",
    ):
        if generated.get(key) != golden.get(key):
            errors.append(
                f"golden mismatch on {key}: {generated.get(key)!r} != {golden.get(key)!r}"
            )
    gen_names = [s.get("name") for s in generated.get("stages") or []]
    gold_names = [s.get("name") for s in golden.get("stages") or []]
    if gen_names != gold_names:
        errors.append(f"golden stage order mismatch: {gen_names} != {gold_names}")
    if generated.get("edges") != golden.get("edges"):
        errors.append("golden edges mismatch")
    for gs, gg in zip(
        generated.get("stages") or [], golden.get("stages") or [], strict=False
    ):
        for field_name in (
            "kind",
            "harness",
            "agent",
            "route",
            "parallel_group",
            "uses_browser",
            "output",
        ):
            if gs.get(field_name) != gg.get(field_name):
                errors.append(
                    f"golden stage {gs.get('name')} field {field_name} mismatch"
                )
    return errors


def mutate_plan_wrong_order(plan: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(plan)
    stages = list(mutated["stages"])
    # Swap tribunal and release positions in the stage list.
    idx_t = next(i for i, s in enumerate(stages) if s["name"] == "tribunal")
    idx_r = next(i for i, s in enumerate(stages) if s["name"] == "release")
    stages[idx_t], stages[idx_r] = stages[idx_r], stages[idx_t]
    mutated["stages"] = stages
    mutated["edges"] = {
        **mutated["edges"],
        "idr": ["release"],
        "release": ["tribunal"],
        "tribunal": [],
    }
    return mutated


def mutate_plan_non_acpx(plan: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(plan)
    for stage in mutated["stages"]:
        if stage["name"] == "plan":
            stage["harness"] = "cursor"
            stage["agent"] = "claude"
            stage["route"] = "cursor:claude"
    return mutated


def mutate_plan_excessive_concurrency(plan: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(plan)
    mutated["max_concurrency"] = 99
    return mutated


def mutate_plan_unsafe_execute(plan: dict[str, Any]) -> dict[str, Any]:
    mutated = copy.deepcopy(plan)
    mutated["mode"] = "execute"
    mutated["would_execute"] = True
    mutated["permission_policy"] = None
    mutated["mcp_config"] = None
    return mutated


def score_structure(root: Path) -> ScoreResult:
    missing = [p for p in REQUIRED_PATHS if not (root / p).is_file()]
    has_readme = (root / "README.md").exists() or (root / "readme.md").exists()
    # Fail closed if any __pycache__ or .pyc is present. Never delete them here.
    bytecode_hits = find_bytecode_paths(root)
    if missing:
        return ScoreResult("structure", False, f"missing: {missing}")
    if has_readme:
        return ScoreResult("structure", False, "README.md must not exist inside skill")
    if bytecode_hits:
        return ScoreResult(
            "structure", False, f"bytecode present: {bytecode_hits[:8]}"
        )
    return ScoreResult(
        "structure", True, f"all {len(REQUIRED_PATHS)} required paths present"
    )


def score_trigger_metadata(root: Path) -> ScoreResult:
    skill_md = root / "SKILL.md"
    text = _read_text(skill_md)
    match = FRONTMATTER_RE.match(text)
    if not match:
        return ScoreResult("trigger_metadata", False, "missing YAML frontmatter")
    fm = match.group(1)
    if "name:" not in fm or "description:" not in fm:
        return ScoreResult(
            "trigger_metadata", False, "frontmatter needs name and description"
        )
    if "acpx-agent-family" not in fm:
        return ScoreResult(
            "trigger_metadata", False, "name must be acpx-agent-family"
        )
    desc_ok = any(
        token in fm.lower()
        for token in ("acpx", "orca", "agent-family", "tribunal", "opencode", "grok")
    )
    if not desc_ok:
        return ScoreResult(
            "trigger_metadata", False, "description lacks trigger keywords"
        )
    openai = root / "agents" / "openai.yaml"
    oy = _read_text(openai)
    if "display_name" not in oy or "default_prompt" not in oy:
        return ScoreResult("trigger_metadata", False, "openai.yaml incomplete")
    if UNFINISHED_MARKER_PATTERN.search(fm) or UNFINISHED_MARKER_PATTERN.search(
        text[:500]
    ):
        return ScoreResult(
            "trigger_metadata",
            False,
            "frontmatter still has unfinished-work markers",
        )
    return ScoreResult("trigger_metadata", True, "frontmatter + openai.yaml OK")


def score_no_todos(root: Path) -> ScoreResult:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".pyo"}:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if UNFINISHED_MARKER_PATTERN.search(content):
            hits.append(path.relative_to(root).as_posix())
    if hits:
        return ScoreResult(
            "no_todos", False, f"unfinished markers in: {hits[:8]}"
        )
    return ScoreResult("no_todos", True, "no unfinished-work markers")


def score_executable_scripts(root: Path) -> ScoreResult:
    scripts = [
        root / "scripts" / "install_and_verify.py",
        root / "scripts" / "benchmark_skill.py",
    ]
    details: list[str] = []
    for path in scripts:
        if not path.is_file():
            return ScoreResult("executable_scripts", False, f"missing {path.name}")
        text = _read_text(path)
        if not text.startswith("#!/usr/bin/env python3"):
            return ScoreResult(
                "executable_scripts",
                False,
                f"{path.name} missing python3 shebang",
            )
        mode = path.stat().st_mode
        if not (mode & stat.S_IXUSR):
            details.append(f"{path.name} not +x (shebang OK)")
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            return ScoreResult(
                "executable_scripts",
                False,
                f"{path.name} syntax error: {exc}",
            )
    msg = "scripts compile" + (
        f"; notes: {details}" if details else " and are +x-ready"
    )
    return ScoreResult("executable_scripts", True, msg)


def score_dry_run_install(root: Path, workspace: Path) -> ScoreResult:
    # Workspace must already be under a canonical temp root (see run_benchmark).
    dest = Path(workspace).resolve() / "skills-dest"
    try:
        report = install_skill(destination=dest, source=root, apply=False)
    except InstallError as exc:
        return ScoreResult("dry_run_install", False, f"InstallError: {exc}")
    if report.mode != "dry-run":
        return ScoreResult(
            "dry_run_install", False, f"expected dry-run, got {report.mode}"
        )
    if report.copied:
        return ScoreResult(
            "dry_run_install", False, "dry-run must not copy files"
        )
    if not report.would_copy:
        return ScoreResult("dry_run_install", False, "would_copy empty")
    if not report.passed:
        return ScoreResult(
            "dry_run_install", False, f"errors: {report.errors}"
        )
    # Manifest must exclude bytecode.
    for rel in report.would_copy:
        if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
            return ScoreResult(
                "dry_run_install",
                False,
                f"bytecode in would_copy: {rel}",
            )
    return ScoreResult(
        "dry_run_install",
        True,
        f"dry-run OK ({len(report.would_copy)} files, hashes planned)",
    )


def score_plan_generation(root: Path) -> ScoreResult:
    try:
        golden_path = fixtures_dir(root) / "golden_plan.json"
        golden = load_json_fixture(golden_path)
        golden_errors = validate_plan_fixture(golden)
        if golden_errors:
            return ScoreResult(
                "plan_generation",
                False,
                f"hand-authored golden failed validation: {golden_errors}",
            )

        plan = generate_agent_family_plan(mode="plan", agent="grok-build")
        errors = validate_plan_fixture(plan)
        if errors:
            return ScoreResult(
                "plan_generation", False, f"plan invalid: {errors}"
            )
        mismatch = structural_keys_match_golden(plan, golden)
        if mismatch:
            return ScoreResult(
                "plan_generation",
                False,
                f"generator diverges from golden: {mismatch[:6]}",
            )

        plan2 = generate_agent_family_plan(
            mode="dry-run",
            agent_map={"plan": "opencode", "forge": "grok-build"},
        )
        errors2 = validate_plan_fixture(plan2)
        if errors2:
            return ScoreResult(
                "plan_generation",
                False,
                f"mapped plan invalid: {errors2}",
            )

        # Reject casual execute (no policies).
        try:
            generate_agent_family_plan(mode="execute")
            return ScoreResult(
                "plan_generation",
                False,
                "execute without policies should raise",
            )
        except ValueError:
            pass

        # Reject bad agents and concurrency.
        for kwargs, label in (
            ({"agent": "claude"}, "claude agent"),
            ({"max_concurrency": 9}, "concurrency 9"),
        ):
            try:
                generate_agent_family_plan(**kwargs)  # type: ignore[arg-type]
                return ScoreResult(
                    "plan_generation",
                    False,
                    f"should reject {label}",
                )
            except ValueError:
                pass

        # Negative fixtures must fail validation.
        for name in NEGATIVE_FIXTURE_NAMES:
            path = fixtures_dir(root) / name
            negative = load_json_fixture(path)
            neg_errors = validate_plan_fixture(negative)
            if not neg_errors:
                return ScoreResult(
                    "plan_generation",
                    False,
                    f"negative fixture {name} unexpectedly passed",
                )

        # Mutations of a good plan must also fail.
        for mutator, label in (
            (mutate_plan_wrong_order, "wrong_order"),
            (mutate_plan_non_acpx, "non_acpx"),
            (mutate_plan_excessive_concurrency, "excessive_concurrency"),
            (mutate_plan_unsafe_execute, "unsafe_execute"),
        ):
            bad = mutator(plan)
            mut_errors = validate_plan_fixture(bad)
            if not mut_errors:
                return ScoreResult(
                    "plan_generation",
                    False,
                    f"mutation {label} unexpectedly passed",
                )

    except Exception as exc:  # noqa: BLE001
        return ScoreResult("plan_generation", False, f"exception: {exc}")
    return ScoreResult(
        "plan_generation",
        True,
        "golden match + negatives/mutations fail + execute gated",
    )


def score_forbidden_secret_scan(root: Path) -> ScoreResult:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix in {".pyc", ".png", ".pyo"}:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        for needle in FORBIDDEN_SECRET_NEEDLES:
            if needle in text:
                rel = path.relative_to(root).as_posix()
                if rel.endswith((".md", ".py", ".yaml", ".yml", ".json")):
                    if needle.startswith("-----begin") or needle.startswith("sk-") or re.search(
                        rf"{re.escape(needle)}\s*['\"]?[A-Za-z0-9_\-/+]{{8,}}",
                        text,
                    ):
                        hits.append(f"{rel}:{needle}")
                break
    if hits:
        return ScoreResult("forbidden_secret_scan", False, f"hits: {hits[:10]}")
    return ScoreResult("forbidden_secret_scan", True, "no live secret material")


def score_forward_test_fixture(root: Path, workspace: Path) -> ScoreResult:
    """Cross-check generator vs golden fixture and re-validate negatives in temp."""
    golden = load_json_fixture(fixtures_dir(root) / "golden_plan.json")
    plan = generate_agent_family_plan(mode="plan")
    fixture_path = workspace / "forward-agent-family-plan.json"
    fixture_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    loaded = json.loads(fixture_path.read_text(encoding="utf-8"))

    errors = validate_plan_fixture(loaded)
    if errors:
        return ScoreResult(
            "forward_test_fixture", False, f"fixture errors: {errors}"
        )
    mismatch = structural_keys_match_golden(loaded, golden)
    if mismatch:
        return ScoreResult(
            "forward_test_fixture",
            False,
            f"forward diverges from golden: {mismatch[:6]}",
        )

    # Re-assert independent negatives still fail when loaded from disk.
    for name in NEGATIVE_FIXTURE_NAMES:
        neg = load_json_fixture(fixtures_dir(root) / name)
        if not validate_plan_fixture(neg):
            return ScoreResult(
                "forward_test_fixture",
                False,
                f"negative {name} passed validation",
            )

    # Mutation battery in temp space.
    for mutator, label in (
        (mutate_plan_wrong_order, "wrong_order"),
        (mutate_plan_non_acpx, "non_acpx"),
        (mutate_plan_excessive_concurrency, "excessive_concurrency"),
        (mutate_plan_unsafe_execute, "unsafe_execute"),
    ):
        if not validate_plan_fixture(mutator(loaded)):
            return ScoreResult(
                "forward_test_fixture",
                False,
                f"mutation {label} passed",
            )

    blob = json.dumps(loaded).lower()
    for needle in ("password=", "api_key=", "authorization: bearer", "cookie="):
        if needle in blob:
            return ScoreResult(
                "forward_test_fixture",
                False,
                f"fixture leaked {needle}",
            )

    return ScoreResult(
        "forward_test_fixture",
        True,
        f"golden+negatives+mutations OK ({fixture_path.name})",
    )


def run_benchmark(skill_path: Path | None = None) -> BenchmarkReport:
    """Score skill quality. Read-only on skill_path — never deletes or mutates source."""
    root = (skill_path or skill_root()).resolve()
    report = BenchmarkReport(skill=SKILL_NAME, workspace="")

    with resolved_temporary_directory(prefix="acpx-agent-family-bench-") as tmp:
        # Always canonicalize so install dry-run does not see /var symlink ancestors.
        workspace = Path(tmp).resolve()
        report.workspace = str(workspace)

        report.add(score_structure(root))
        report.add(score_trigger_metadata(root))
        report.add(score_no_todos(root))
        report.add(score_executable_scripts(root))
        report.add(score_dry_run_install(root, workspace))
        report.add(score_plan_generation(root))
        report.add(score_forbidden_secret_scan(root))
        report.add(score_forward_test_fixture(root, workspace))

    report.passed = (
        report.total == report.max_total
        and report.max_total == len(SCORE_KEYS)
    )
    if set(s.name for s in report.scores) != set(SCORE_KEYS):
        report.notes.append("score key set mismatch")
        report.passed = False
    return report


def report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
    return {
        "skill": report.skill,
        "passed": report.passed,
        "total": report.total,
        "max_total": report.max_total,
        "score_ratio": (report.total / report.max_total) if report.max_total else 0.0,
        "workspace": report.workspace,
        "notes": report.notes,
        "scores": [asdict(s) for s in report.scores],
        "required_score_keys": list(SCORE_KEYS),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark acpx-agent-family skill")
    parser.add_argument(
        "--skill-path",
        default=None,
        help="Skill root to score (default: this skill directory)",
    )
    parser.add_argument("--json", action="store_true", help="JSON report on stdout")
    args = parser.parse_args(argv)

    skill_path = Path(args.skill_path).resolve() if args.skill_path else None
    report = run_benchmark(skill_path)
    payload = report_to_dict(report)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"benchmark skill={report.skill} "
            f"score={report.total}/{report.max_total} passed={report.passed}"
        )
        for s in report.scores:
            mark = "PASS" if s.passed else "FAIL"
            print(f"  [{mark}] {s.name}: {s.detail}")
        for note in report.notes:
            print(f"  note: {note}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
