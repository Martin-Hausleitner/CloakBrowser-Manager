# Streaming Speed-Test Runner

`scripts/streaming_benchmark_runner.py` is the reproducible adapter for comparing live-streaming candidates without inventing missing numbers. It emits newline-delimited JSON progress events on stdout for logs or automation, then writes both a machine-readable JSON report and a Markdown summary.

## Candidate config

Start from `scripts/streaming_benchmark_example.json` and replace endpoints with the exact local candidates under test. Supported candidate types are:

| Type | What is measured | Typical use |
|---|---|---|
| `http` | TCP connect, optional TLS, first response byte, total response sample, status code | Health endpoints, HTTP stream endpoints, control checks |
| `websocket` | TCP connect, optional TLS, WebSocket upgrade handshake, status code | noVNC/KasmVNC proxy, Selkies WebSocket, other browser-consumable stream endpoints |
| `command` | Process startup, first output, optional `ready_regex`, exit | Local smoke commands or benchmark adapters |
| `architecture` | No timings; explicitly reported as `architecture_only` | Sunshine/Moonlight or Guacamole paths that are being evaluated conceptually |

For a measurable candidate that depends on a local binary, add `requires_executable`. If the binary is not on `PATH`, the candidate is reported as `not_installed` and receives no timing fields.

HTTP and WebSocket candidates may include a `headers` object for disposable local auth headers or cookies. Header values are used for the request but omitted from the public report. The runner also omits candidate URLs, commands, local paths, raw process output, and reproduction commands so reports can be shared without leaking local details.

## Run

```bash
python3 scripts/streaming_benchmark_runner.py \
  --config scripts/streaming_benchmark_example.json \
  --output-dir artifacts/streaming-benchmark/$(date -u +%Y%m%dT%H%M%SZ) \
  --iterations 5 \
  --latest-json "${BENCHMARK_REPORT_PATH:-/data/benchmark-report.json}" \
  --latest-markdown docs/streaming-benchmark-latest.md
```

The stdout stream is JSONL. Automation or a log tailer can consume it incrementally and react to `run_started`, `candidate_started`, `iteration_started`, `iteration_finished`, `candidate_finished`, and `run_finished` events.

## Reproducible Selkies lane

The repository includes a pinned LinuxServer Chromium/Selkies image, a loopback-only Compose service and a strict runner wrapper:

```bash
./scripts/run_selkies_benchmark.sh artifacts/selkies-benchmark/local
```

The wrapper waits for both the HTTP shell and a valid `101 Switching Protocols` response from `/websockets`, runs the normal benchmark adapter, and removes only its own Compose stack unless `SELKIES_BENCHMARK_KEEP_RUNNING=1` is set. It measures HTTP and WebSocket readiness. It does not claim first-frame latency, FPS, touch-to-pixel response, mobile Safari support or product integration.

## Report contract

Each result has:

- `status`: `measured`, `not_installed`, or `architecture_only`
- `availability`: `available`, `unavailable`, `error`, or `not_measured`
- `measurements`: raw per-iteration observations for measured candidates
- `summary`: min/median/p95/max timing rollups plus sample count and success rate for observed numeric milestones

The runner treats connection failures and non-101 WebSocket handshakes as measured outcomes, not architecture claims. A long-running command is considered available when its `ready_regex` is observed, then the probe is terminated cleanly; without a readiness regex it must exit successfully before the timeout. It treats missing binaries and intentionally conceptual candidates as not measured.

## Report storage

The manager can read the latest report from `BENCHMARK_REPORT_PATH` (default `/data/benchmark-report.json`) through the administrator-only `/api/benchmarks/latest` endpoint when an operator needs a backend health check. r50 does not show benchmark controls in the mobile UI. Run this script separately and persist its output before attaching a report to a release decision.

## Pairing with the mobile UI gate

Use this runner for comparable transport and startup milestones. Use `scripts/mobile_ui_gate.py` afterwards to prove that the chosen stack still renders the actual product UI and live canvas across the mobile viewports. The two reports answer different questions and should both be attached to a release decision.

For a fail-closed release decision, `scripts/release_acceptance_gate.py` can combine fresh quality commands, a passing Mobile UI/UX report, a machine-readable Vision verdict and an optional measured streaming report. Its public JSON and Markdown outputs redact local endpoints, credential-like values and local paths.
