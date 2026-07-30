#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from benchmarks.auth.live_runner import (
    DEFAULT_SCENARIOS,
    default_chromium_binary,
    run_live_benchmark,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run loopback-only synthetic password/OAuth/passkey benchmarks."
    )
    parser.add_argument("--chromium-binary")
    parser.add_argument("--scenario", action="append", choices=DEFAULT_SCENARIOS)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/auth-benchmark/report.json")
    )
    args = parser.parse_args()
    binary = args.chromium_binary or default_chromium_binary()
    if binary is None:
        parser.error("no compatible Chromium binary found")
    report = asyncio.run(
        run_live_benchmark(
            chromium_binary=binary,
            scenario_ids=tuple(args.scenario or DEFAULT_SCENARIOS),
            max_parallel=args.parallel,
        )
    )
    write_report(args.output, report)
    print(
        f"auth_benchmark passed={str(report.passed).lower()} "
        f"scenarios={len(report.results)} p95_ms={report.p95_duration_ms}"
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
