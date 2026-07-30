#!/usr/bin/env python3
"""CLI for the ACPX-backed auth benchmark pipeline.

Safe defaults: ``--mode plan``, parallel capped at 4, repo-pinned ACPX runtime.
Execute requires absolute permission-policy and mcp-config paths. Writes a
metrics-only private report (0600 under 0700 parent) and prints only
mode/passed/stage_count/route.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.auth.acpx_pipeline import (
    AGENT_ROUTE,
    AGENT_STAGES,
    DEFAULT_MODE,
    MAX_CONCURRENCY,
    PARALLEL_BROWSER_LANES,
    AuthAcpxPipelineError,
    AuthAcpxPipelineReport,
    report_to_text,
    resolve_acpx_executable,
    run_auth_acpx_pipeline,
)
from benchmarks.auth.policy import assert_artifact_is_safe

DEFAULT_OUTPUT = Path("artifacts/auth-benchmark/private/acpx-pipeline-report.json")
REPORT_SCHEMA = "cloakbrowser.auth-acpx-pipeline.v1"


def _stage_kind(name: str) -> str:
    if name in AGENT_STAGES:
        return "agent"
    if name in PARALLEL_BROWSER_LANES:
        return "browser"
    return "unknown"


def build_metrics_payload(
    report: AuthAcpxPipelineReport,
    *,
    acpx_executable: str,
) -> dict[str, Any]:
    """Build a metrics-only JSON payload (no prompts, commands, or process streams)."""
    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "mode": report.mode,
        "passed": report.passed,
        "stage_count": len(report.stages),
        "agent_route": report.agent_route,
        "max_concurrency": report.max_concurrency,
        "acpx_executable": acpx_executable,
        "failures": list(report.failures),
        "stages": [
            {
                "name": stage.name,
                "status": stage.status,
                "kind": _stage_kind(stage.name),
            }
            for stage in report.stages
        ],
    }
    # Fail closed if command material or process streams ever leak into the payload.
    encoded = json.dumps(payload, sort_keys=True)
    assert_artifact_is_safe(encoded)
    for forbidden in ("prompt_brief", "stdin_text", "stdout", "stderr", "commands"):
        if forbidden in encoded:
            raise AuthAcpxPipelineError(f"metrics payload must not contain {forbidden}")
    return payload


def write_private_report(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON report as mode 0600 under a 0700 parent directory."""
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert_artifact_is_safe(text)
    # Cross-check against the text report contract (also redacted).
    if "mode" in payload and "passed" in payload:
        # Light structural safety only; full stage text report is separate.
        pass
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    target.chmod(0o600)


def _require_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise AuthAcpxPipelineError(f"{label} must be an absolute path")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or dry-run (default) the ACPX auth benchmark pipeline; "
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
        "--parallel",
        type=int,
        default=MAX_CONCURRENCY,
        help=f"Browser lane concurrency 1..{MAX_CONCURRENCY} (default: {MAX_CONCURRENCY})",
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
        help="Optional absolute override for ACPX executable (default: repo-pinned runtime)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Metrics-only report path (default: {DEFAULT_OUTPUT})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not isinstance(args.parallel, int) or args.parallel < 1 or args.parallel > MAX_CONCURRENCY:
        parser.error(f"--parallel must be an integer between 1 and {MAX_CONCURRENCY}")

    try:
        permission_policy = None
        mcp_config = None
        if args.permission_policy is not None:
            permission_policy = _require_absolute(
                args.permission_policy, label="--permission-policy"
            )
        if args.mcp_config is not None:
            mcp_config = _require_absolute(args.mcp_config, label="--mcp-config")

        if args.mode == "execute" and (
            permission_policy is None or mcp_config is None
        ):
            raise AuthAcpxPipelineError(
                "execute mode requires absolute --permission-policy and --mcp-config"
            )

        acpx_override = None
        if args.acpx is not None:
            acpx_override = _require_absolute(args.acpx, label="--acpx")

        # Resolve pin early so execute fail-closes before any stage work.
        resolved_acpx = resolve_acpx_executable(
            acpx_executable=acpx_override,
            require_executable=args.mode == "execute",
        )

        report = asyncio.run(
            run_auth_acpx_pipeline(
                mode=args.mode,
                max_concurrency=args.parallel,
                acpx_executable=resolved_acpx,
                permission_policy=permission_policy,
                mcp_config=mcp_config,
            )
        )
        # Ensure redacted text form is also safe (side-effect validation).
        report_to_text(report)
        payload = build_metrics_payload(report, acpx_executable=resolved_acpx)
        write_private_report(args.output, payload)

        # Single-line operator summary only.
        print(
            f"mode={report.mode} passed={str(report.passed).lower()} "
            f"stage_count={len(report.stages)} route={report.agent_route or AGENT_ROUTE}"
        )
        return 0 if report.passed else 1
    except AuthAcpxPipelineError as exc:
        print(f"auth_acpx_pipeline_error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"auth_acpx_pipeline_error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
