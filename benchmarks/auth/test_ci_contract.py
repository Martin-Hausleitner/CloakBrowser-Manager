"""CI contract for the loopback auth benchmark gate.

TDD gate: the workflow must gain an auth-benchmark job without rewriting
existing jobs, install pinned deps (including cryptography for cdp_webauthn),
export CBM_AUTH_CHROMIUM_BINARY from the Playwright install for deterministic
Chromium discovery, run unit tests + all live scenarios, and upload only the
metrics report (never browser traces/video/HAR/state).
"""

from __future__ import annotations

import copy
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = ROOT / "benchmarks" / "auth" / "requirements.txt"

# Jobs present before the auth-benchmark gate was added; must remain byte-stable
# in semantic YAML form (diff-check against HEAD when available).
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
    }
)

FORBIDDEN_AUTH_JOB_TOKENS = (
    "--trace",
    "--video",
    "--har",
    "storage-state",
    "screenshot",
    "trace.zip",
    ".har",
)

REQUIRED_PINNED_PACKAGES = (
    "pytest==",
    "pytest-asyncio==",
    "playwright==",
    "cryptography==",
)


class _ActionsLoader(yaml.SafeLoader):
    """Keep GitHub Actions keys like `on:` as strings, not bools."""


for first_letter, resolvers in list(_ActionsLoader.yaml_implicit_resolvers.items()):
    _ActionsLoader.yaml_implicit_resolvers[first_letter] = [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]


def _load_workflow_text(text: str) -> dict[str, Any]:
    data = yaml.load(text, Loader=_ActionsLoader)
    assert isinstance(data, dict)
    assert isinstance(data.get("jobs"), dict)
    return data


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), "CI workflow is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    return _load_workflow_text(_workflow_text())


def _head_workflow_text() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", "HEAD:.github/workflows/ci.yml"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _flatten_job(job: dict[str, Any]) -> str:
    return yaml.dump(job, sort_keys=True).lower()


def _run_text(job: dict[str, Any]) -> str:
    parts: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and "run" in step:
            parts.append(str(step["run"]))
    return "\n".join(parts)


def _requirement_lines() -> list[str]:
    assert REQUIREMENTS.is_file(), "benchmarks/auth/requirements.txt must exist"
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_auth_requirements_pin_test_and_browser_dependencies() -> None:
    lines = _requirement_lines()
    assert lines, "requirements must not be empty"
    for line in lines:
        assert "==" in line, f"requirement must be pinned with ==: {line!r}"
    joined = "\n".join(lines).lower()
    for package in REQUIRED_PINNED_PACKAGES:
        assert package in joined, f"missing pinned dependency {package!r}"
    assert "pyyaml==" in joined or "yaml==" in joined
    # cdp_webauthn imports cryptography; pin must be explicit (not transitive-only).
    assert any(line.lower().startswith("cryptography==") for line in lines)


def test_auth_requirements_include_pinned_cryptography_for_cdp_webauthn() -> None:
    lines = _requirement_lines()
    crypto_lines = [line for line in lines if line.lower().startswith("cryptography==")]
    assert len(crypto_lines) == 1, "exactly one cryptography pin is required"
    assert re.fullmatch(r"cryptography==\d+\.\d+\.\d+", crypto_lines[0].lower()), (
        f"cryptography pin must be exact major.minor.patch: {crypto_lines[0]!r}"
    )


def test_existing_ci_jobs_are_preserved_by_yaml_diff() -> None:
    current_text = _workflow_text()
    current = _load_workflow_text(current_text)
    current_jobs = current["jobs"]

    missing = PRESERVED_JOBS - set(current_jobs)
    assert not missing, f"existing CI jobs were removed: {sorted(missing)}"

    head_text = _head_workflow_text()
    if head_text is None:
        return

    baseline = _load_workflow_text(head_text)
    baseline_jobs = baseline["jobs"]
    assert set(baseline_jobs) == PRESERVED_JOBS

    added = set(current_jobs) - set(baseline_jobs)
    assert added == {"auth-benchmark"}, f"unexpected job delta: {sorted(added)}"

    for job_id in PRESERVED_JOBS:
        assert current_jobs[job_id] == baseline_jobs[job_id], (
            f"existing job {job_id!r} was modified; preserve existing workflow"
        )


def test_auth_benchmark_exports_playwright_chromium_binary_for_gha() -> None:
    """Chromium path must come from Playwright install, not a guessed system path."""
    job = _workflow()["jobs"]["auth-benchmark"]
    runs = _run_text(job)

    assert "CBM_AUTH_CHROMIUM_BINARY" in runs
    assert "GITHUB_ENV" in runs
    # Resolve executable from the installed Playwright browser package.
    assert "executable_path" in runs or "chromium.executable_path" in runs
    assert "sync_playwright" in runs or "playwright.sync_api" in runs
    # Must not hardcode host-local cache paths as the discovery strategy.
    assert "/home/coder/.cache/ms-playwright" not in runs
    assert "/usr/bin/google-chrome" not in runs
    assert "/snap/bin/chromium" not in runs


def test_ci_runs_local_auth_benchmark_without_sensitive_browser_artifacts() -> None:
    workflow = _workflow_text()
    assert "auth-benchmark:" in workflow
    assert "benchmarks/auth/requirements.txt" in workflow
    assert "python -m pytest benchmarks/auth -q" in workflow
    assert "benchmarks/auth/run_auth_benchmark.py" in workflow
    assert "--parallel 4" in workflow
    assert "CBM_AUTH_CHROMIUM_BINARY" in workflow
    assert "cryptography==" in REQUIREMENTS.read_text(encoding="utf-8").lower()

    data = _workflow()
    assert "auth-benchmark" in data["jobs"]
    job = data["jobs"]["auth-benchmark"]
    job_text = _flatten_job(job)
    runs = _run_text(job)

    assert "benchmarks/auth/requirements.txt" in runs
    assert "python -m pytest benchmarks/auth -q" in runs
    assert "benchmarks/auth/run_auth_benchmark.py" in runs
    assert "--parallel 4" in runs
    assert "playwright install" in runs.lower()
    assert "report.json" in runs
    assert "CBM_AUTH_CHROMIUM_BINARY" in runs
    assert "GITHUB_ENV" in runs

    for forbidden in FORBIDDEN_AUTH_JOB_TOKENS:
        assert forbidden not in job_text, f"auth-benchmark must not use {forbidden!r}"
        assert forbidden not in runs.lower()

    # String-slice guard used by the original gate (case-insensitive job body).
    job_slice = workflow.split("  auth-benchmark:", 1)[1].split("\n  ", 1)[0].lower()
    for forbidden in ("--trace", "--video", "--har", "storage-state"):
        assert forbidden not in job_slice

    uploads = [
        step
        for step in job.get("steps") or []
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert len(uploads) == 1, "auth-benchmark must upload exactly one artifact"
    upload_path = str(uploads[0].get("with", {}).get("path", "")).strip()
    assert upload_path == "artifacts/auth-benchmark/report.json", (
        "upload only the metrics report.json, not a directory of browser artifacts"
    )

    # Deep-copy sanity: job structure remains a plain mapping after parse.
    assert copy.deepcopy(job)["runs-on"] in {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"}
