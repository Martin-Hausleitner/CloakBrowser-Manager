#!/usr/bin/env python3
"""CLI for the local-first CloakBrowser observability trace pipeline.

Examples:
  python scripts/cbm_trace_ctl.py config
  python scripts/cbm_trace_ctl.py init --backend sqlite --path .cbm/observability/traces.db
  python scripts/cbm_trace_ctl.py record-span --name acpx.run --run-id r1 --harness acpx
  python scripts/cbm_trace_ctl.py import-tokscale --input usage.json --run-id r1
  python scripts/cbm_trace_ctl.py export --format jsonl --out /tmp/traces.jsonl
  python scripts/cbm_trace_ctl.py export --format otlp-json --out /tmp/traces.otlp.json
  python scripts/cbm_trace_ctl.py benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.observability.benchmark import run_benchmark  # noqa: E402
from backend.observability.config import ObservabilityConfig, load_config  # noqa: E402
from backend.observability.correlation import RunCorrelation  # noqa: E402
from backend.observability.pipeline import TracePipeline  # noqa: E402
from backend.observability.schema import SpanKind, SpanRecord, SpanStatus  # noqa: E402


def _pipeline_from_args(args: argparse.Namespace) -> TracePipeline:
    cfg = load_config()
    if getattr(args, "backend", None):
        cfg.spool_backend = args.backend  # type: ignore[assignment]
    if getattr(args, "path", None):
        cfg.spool_path = args.path
    if getattr(args, "otlp_endpoint", None) is not None:
        cfg.otlp_http_endpoint = args.otlp_endpoint or None
    if getattr(args, "content_capture", None) is not None:
        cfg.content_capture = bool(args.content_capture)
    return TracePipeline(cfg, base_dir=ROOT)


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.backend:
        cfg.spool_backend = args.backend  # type: ignore[assignment]
    if args.path:
        cfg.spool_path = args.path
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    with _pipeline_from_args(args) as pipeline:
        stats = pipeline.stats()
        print(json.dumps({"initialized": True, **stats}, indent=2))
    return 0


def cmd_record_span(args: argparse.Namespace) -> int:
    correlation = None
    if args.run_id:
        correlation = RunCorrelation(
            run_id=args.run_id,
            harness=args.harness,
            profile_id=args.profile_id,
            session_id=args.session_id,
            acp_session_id=args.acp_session_id,
            acpx_run_id=args.acpx_run_id,
        )
    attributes = {}
    if args.attributes_json:
        attributes = json.loads(args.attributes_json)
    with _pipeline_from_args(args) as pipeline:
        span = pipeline.start_span(
            args.name,
            correlation=correlation,
            kind=SpanKind(args.kind),
            status_code=SpanStatus(args.status),
            attributes=attributes,
            harness=args.harness,
            run_id=args.run_id,
            profile_id=args.profile_id,
            session_id=args.session_id,
        )
        if args.finish:
            span.finish(status_code=SpanStatus(args.status))
            pipeline.record_span(span)
        print(json.dumps(span.to_dict(), indent=2))
    return 0


def cmd_import_tokscale(args: argparse.Namespace) -> int:
    correlation = None
    if args.run_id:
        correlation = RunCorrelation(
            run_id=args.run_id,
            harness=args.harness or "tokscale",
            profile_id=args.profile_id,
            session_id=args.session_id,
        )
    with _pipeline_from_args(args) as pipeline:
        records = pipeline.import_tokscale(
            Path(args.input),
            correlation=correlation,
            require_version=args.require_version,
        )
        print(
            json.dumps(
                {
                    "imported": len(records),
                    "usage": [r.to_dict() for r in records],
                    "counts": pipeline.spool.count(),
                },
                indent=2,
            )
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with _pipeline_from_args(args) as pipeline:
        if args.format == "jsonl":
            result = pipeline.export_jsonl(args.out)
        elif args.format == "otlp-json":
            result = pipeline.export_otlp_json(args.out)
        else:
            print(f"unknown format: {args.format}", file=sys.stderr)
            return 2
        if args.push_otlp:
            pushed = pipeline.maybe_export_otlp_http()
            result = {**result, "otlp_http": pushed}
        print(json.dumps({"exported": True, **result}, indent=2))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with _pipeline_from_args(args) as pipeline:
        print(json.dumps(pipeline.stats(), indent=2))
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    if args.workdir:
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        result = run_benchmark(
            workdir=workdir,
            n_spans=args.spans,
            n_usage=args.usage,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="cbm-obs-bench-") as tmp:
            result = run_benchmark(
                workdir=Path(tmp),
                n_spans=args.spans,
                n_usage=args.usage,
            )
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cbm_trace_ctl",
        description="Local-first vendor-neutral CloakBrowser trace pipeline CLI",
    )
    parser.add_argument(
        "--backend",
        choices=("sqlite", "jsonl"),
        default=None,
        help="Spool backend (default: env or sqlite)",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Spool path (default: env or .cbm/observability/traces.db)",
    )
    parser.add_argument(
        "--otlp-endpoint",
        default=None,
        help="Optional OTLP HTTP endpoint (empty disables)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="Show effective configuration")
    p_config.set_defaults(func=cmd_config)

    p_init = sub.add_parser("init", help="Initialize local spool")
    p_init.set_defaults(func=cmd_init)

    p_span = sub.add_parser("record-span", help="Append one span")
    p_span.add_argument("--name", required=True)
    p_span.add_argument("--run-id")
    p_span.add_argument("--profile-id")
    p_span.add_argument("--session-id")
    p_span.add_argument("--harness")
    p_span.add_argument("--acp-session-id")
    p_span.add_argument("--acpx-run-id")
    p_span.add_argument("--kind", default="INTERNAL", choices=[k.value for k in SpanKind])
    p_span.add_argument("--status", default="OK", choices=[s.value for s in SpanStatus])
    p_span.add_argument("--attributes-json", default=None)
    p_span.add_argument("--finish", action="store_true")
    p_span.add_argument(
        "--content-capture",
        action="store_true",
        default=False,
        help="Do not use; default is false and content keys are forbidden",
    )
    p_span.set_defaults(func=cmd_record_span)

    p_imp = sub.add_parser("import-tokscale", help="Import TokScale 4.7.0 safe aggregate JSON")
    p_imp.add_argument("--input", required=True, help="Path to aggregate JSON")
    p_imp.add_argument("--run-id")
    p_imp.add_argument("--profile-id")
    p_imp.add_argument("--session-id")
    p_imp.add_argument("--harness", default="tokscale")
    p_imp.add_argument(
        "--require-version",
        action="store_true",
        help="Require document version == 4.7.0",
    )
    p_imp.set_defaults(func=cmd_import_tokscale)

    p_exp = sub.add_parser("export", help="Export spool to JSONL or OTLP JSON")
    p_exp.add_argument("--format", choices=("jsonl", "otlp-json"), required=True)
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument(
        "--push-otlp",
        action="store_true",
        help="Also POST to configured OTLP HTTP endpoint if set",
    )
    p_exp.set_defaults(func=cmd_export)

    p_stats = sub.add_parser("stats", help="Show spool counts")
    p_stats.set_defaults(func=cmd_stats)

    p_bench = sub.add_parser("benchmark", help="Run local spool micro-benchmarks")
    p_bench.add_argument("--spans", type=int, default=1000)
    p_bench.add_argument("--usage", type=int, default=200)
    p_bench.add_argument("--workdir", default=None)
    p_bench.set_defaults(func=cmd_benchmark)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
