"""Micro-benchmarks for the local observability spool."""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

from .pipeline import TracePipeline
from .schema import SpanRecord, UsageRecord
from .spool import open_spool


def run_benchmark(
    *,
    workdir: Path,
    n_spans: int = 1000,
    n_usage: int = 200,
    backends: tuple[str, ...] = ("sqlite", "jsonl"),
) -> dict[str, Any]:
    """Benchmark append + iterate throughput for spool backends."""
    results: dict[str, Any] = {"n_spans": n_spans, "n_usage": n_usage, "backends": {}}
    for backend in backends:
        suffix = "db" if backend == "sqlite" else "jsonl"
        path = workdir / f"bench-{backend}.{suffix}"
        if path.exists():
            path.unlink()
        spool = open_spool(backend, path)
        try:
            append_samples: list[float] = []
            # Warm one write
            spool.append_span(SpanRecord(name="warmup", harness="native"))
            t0 = time.perf_counter()
            for i in range(n_spans):
                spool.append_span(
                    SpanRecord(
                        name=f"bench.span.{i}",
                        run_id=f"run-{i % 50}",
                        harness="acpx" if i % 2 == 0 else "browser-use",
                        profile_id="profile-bench",
                    )
                )
            t1 = time.perf_counter()
            append_samples.append(t1 - t0)
            t2 = time.perf_counter()
            for i in range(n_usage):
                spool.append_usage(
                    UsageRecord(
                        input_tokens=100 + i,
                        output_tokens=20 + i,
                        model="bench-model",
                        source="native",
                        run_id=f"run-{i % 50}",
                        harness="tokscale",
                    )
                )
            t3 = time.perf_counter()
            usage_append_s = t3 - t2
            t4 = time.perf_counter()
            span_count = sum(1 for _ in spool.iter_spans())
            usage_count = sum(1 for _ in spool.iter_usage())
            t5 = time.perf_counter()
            results["backends"][backend] = {
                "span_append_seconds": round(append_samples[0], 6),
                "span_append_per_sec": round(n_spans / append_samples[0], 2)
                if append_samples[0]
                else None,
                "usage_append_seconds": round(usage_append_s, 6),
                "usage_append_per_sec": round(n_usage / usage_append_s, 2)
                if usage_append_s
                else None,
                "iterate_seconds": round(t5 - t4, 6),
                "span_count": span_count,
                "usage_count": usage_count,
                "path": str(path),
            }
        finally:
            spool.close()
    # Pipeline export micro-bench on sqlite
    export_dir = workdir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    with TracePipeline(
        spool=open_spool("sqlite", workdir / "bench-export.db"),
        base_dir=workdir,
    ) as pipeline:
        for i in range(100):
            pipeline.record_span(SpanRecord(name=f"export.{i}", run_id="export-run"))
        t0 = time.perf_counter()
        pipeline.export_jsonl(export_dir / "out.jsonl")
        t1 = time.perf_counter()
        pipeline.export_otlp_json(export_dir / "out.otlp.json")
        t2 = time.perf_counter()
        results["export"] = {
            "jsonl_seconds": round(t1 - t0, 6),
            "otlp_json_seconds": round(t2 - t1, 6),
        }
    # Summary latency percentiles for single-span append (sqlite)
    path = workdir / "bench-latency.db"
    if path.exists():
        path.unlink()
    spool = open_spool("sqlite", path)
    try:
        latencies_ms: list[float] = []
        for i in range(200):
            start = time.perf_counter()
            spool.append_span(SpanRecord(name=f"lat.{i}"))
            latencies_ms.append((time.perf_counter() - start) * 1000)
        results["single_span_append_ms"] = {
            "p50": round(statistics.median(latencies_ms), 4),
            "p95": round(statistics.quantiles(latencies_ms, n=20)[18], 4)
            if len(latencies_ms) >= 20
            else round(max(latencies_ms), 4),
            "mean": round(statistics.mean(latencies_ms), 4),
        }
    finally:
        spool.close()
    return results
