# Observability spool benchmarks

Run via CLI (preferred):

```bash
python scripts/cbm_trace_ctl.py benchmark --spans 1000 --usage 200
```

Or programmatically:

```python
from pathlib import Path
from backend.observability.benchmark import run_benchmark
print(run_benchmark(workdir=Path("/tmp/cbm-obs-bench")))
```

Metrics reported:

- Span/usage append throughput for `sqlite` and `jsonl`
- Iterate latency
- JSONL + OTLP JSON export time
- Single-span append p50/p95 (sqlite)
