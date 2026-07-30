# Local-first observability trace pipeline (MVP)

Status: **MVP implemented** on branch workstreams for CloakBrowser Manager.  
Scope: vendor-neutral spans + usage aggregates, local spool, TokScale aggregate import, harness correlation, CLI export. **No required SaaS.**

## Goals

| Goal | Behavior |
|------|----------|
| Local-first | SQLite or JSONL spool on disk; works offline |
| Vendor-neutral | OpenTelemetry-style span/usage schema without hard SDK/SaaS deps |
| TokScale import | Read **safe aggregate JSON** (pinned docs: TokScale **4.7.0**) |
| Correlation | Stamp `run_id`, harness (`acp`/`acpx`/…), `profile_id`, session ids |
| Privacy default | `content_capture=false`; no prompts, responses, cookies, secrets |
| Export | CLI JSONL + OTLP HTTP JSON file; optional OTLP HTTP POST |
| Optional remote | `CBM_OTLP_HTTP_ENDPOINT` only if you choose a collector |

## Non-goals (MVP)

- Embedding Helicone or Langfuse as required dependencies
- Storing chat transcripts, tool payloads, cookies, or credentials
- Replacing Manager task history or VNC/CDP live metrics
- Claiming full OpenTelemetry SDK conformance (this is a deliberate subset)

## Package layout

```
backend/observability/
  schema.py           # SpanRecord, UsageRecord
  privacy.py          # content_capture + forbidden keys
  spool.py            # SqliteSpool, JsonlSpool
  tokscale_import.py  # safe aggregate importer
  correlation.py      # ACP/ACPX/harness run keys
  otlp.py             # OTLP JSON map + optional HTTP POST
  config.py           # env config
  pipeline.py         # TracePipeline facade
  benchmark.py        # micro-benchmarks
scripts/cbm_trace_ctl.py
docs/OBSERVABILITY-TRACE-PIPELINE.md
```

## Privacy contract

- Default: **`content_capture=false`** (`CBM_CONTENT_CAPTURE` unset or false).
- Forbidden keys include: `prompt`, `response`, `messages`, `content`, `cookie(s)`, `authorization`, `password`, `secret`, `api_key`, `token` (auth sense), etc.
- Safe attributes include correlation (`cbm.run_id`, `cbm.harness`, …) and GenAI **usage counters** (`gen_ai.usage.input_tokens`, …).
- Secret-like **values** (Bearer headers, `password=…`, proxy URLs with credentials) are rejected.
- TokScale import calls `assert_no_content_payload` before mapping rows.

## Schema (canonical)

### SpanRecord

- `trace_id` (32 hex), `span_id` (16 hex), optional `parent_span_id`
- `name`, `kind` (`INTERNAL|SERVER|CLIENT|PRODUCER|CONSUMER`)
- `start_time_unix_nano`, `end_time_unix_nano`
- `status_code` (`UNSET|OK|ERROR`), optional short `status_message`
- Correlation: `run_id`, `profile_id`, `session_id`, `harness`
- `attributes` / `resource` (sanitized)
- `content_capture` (default false)

### UsageRecord

- Token counters: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `total_tokens`
- Optional `cost_usd`, `model`, `provider`
- Correlation fields + `source` (`native|tokscale|import`)
- **Never** includes prompts or completions

## TokScale 4.7.0 importer

Only **aggregate** documents are accepted. Example fixture:

`backend/tests/fixtures/observability/tokscale_aggregate_4_7_0.json`

```json
{
  "version": "4.7.0",
  "source": "tokscale",
  "models": [
    {"model": "claude-opus", "input_tokens": 12000, "output_tokens": 3400, "cost_usd": 4.56}
  ],
  "totals": {"input_tokens": 12000, "output_tokens": 3400, "cost_usd": 4.56}
}
```

- Use `--require-version` on the CLI to enforce `4.7.0`.
- Documents with `prompt`, `messages`, `cookie`, etc. are **rejected**.
- TokScale CLI remains an **optional external tool**; CBM does not vendor or require it.

## Harness correlation

`RunCorrelation` stamps:

| Field | Attribute |
|-------|-----------|
| `run_id` | `cbm.run_id` |
| `harness` | `cbm.harness` (`acp`, `acpx`, `browser-use`, …) |
| `profile_id` | `cbm.profile_id` |
| `session_id` | `cbm.session_id` |
| `acp_session_id` | `cbm.acp_session_id` |
| `acpx_run_id` | `cbm.acpx_run_id` |
| `worker_id` / `lease_id` / `task_id` | matching `cbm.*` keys |

## CLI

```bash
# Effective config (content_capture defaults false; no SaaS required)
python scripts/cbm_trace_ctl.py config

# Init local SQLite spool
python scripts/cbm_trace_ctl.py --backend sqlite --path .cbm/observability/traces.db init

# Record a correlated span
python scripts/cbm_trace_ctl.py --path .cbm/observability/traces.db record-span \
  --name acpx.run --run-id run-1 --harness acpx --profile-id prof-1 --finish

# Import TokScale aggregate (safe JSON only)
python scripts/cbm_trace_ctl.py --path .cbm/observability/traces.db import-tokscale \
  --input backend/tests/fixtures/observability/tokscale_aggregate_4_7_0.json \
  --run-id run-1 --require-version

# Export
python scripts/cbm_trace_ctl.py --path .cbm/observability/traces.db export \
  --format jsonl --out /tmp/cbm-traces.jsonl
python scripts/cbm_trace_ctl.py --path .cbm/observability/traces.db export \
  --format otlp-json --out /tmp/cbm-traces.otlp.json

# Optional push if endpoint configured
CBM_OTLP_HTTP_ENDPOINT=http://127.0.0.1:4318/v1/traces \
  python scripts/cbm_trace_ctl.py export --format otlp-json --out /tmp/x.json --push-otlp

# Benchmark
python scripts/cbm_trace_ctl.py benchmark --spans 1000 --usage 200
```

## Environment

| Variable | Meaning |
|----------|---------|
| `CBM_TRACE_SPOOL_BACKEND` | `sqlite` (default) or `jsonl` |
| `CBM_TRACE_SPOOL_PATH` | Spool file path |
| `CBM_CONTENT_CAPTURE` | Default `false`; keep false in production |
| `CBM_OTLP_HTTP_ENDPOINT` | Optional OTLP/HTTP traces URL |
| `CBM_OTLP_HEADERS` | Optional `k=v,k2=v2` (redacted in config dump) |
| `CBM_OTEL_SERVICE_NAME` | Resource `service.name` |
| `CBM_TOKSCALE_VERSION` | Soft/hard pin (default `4.7.0`) |

Also recognizes stock `OTEL_EXPORTER_OTLP_*` / `OTEL_SERVICE_NAME` names when set.

## License and dependency boundaries

| Component | Role in CBM | License note for operators |
|-----------|-------------|------------------------------|
| This package (`backend/observability`) | First-party code in CloakBrowser Manager | Same as repository license (MIT for Manager sources unless noted) |
| OpenTelemetry **specification** concepts | Inspired span/attribute shapes only | Spec is open; **no OTel SDK is required** for this MVP |
| Python stdlib (`sqlite3`, `json`, `urllib`) | Spool + optional HTTP | PSF license (Python) |
| TokScale (external CLI, e.g. community trackers) | Optional producer of aggregate JSON | **Not a hard dependency.** Operators who install TokScale must comply with **that project’s license** separately. CBM only consumes operator-supplied aggregate JSON. |
| Helicone | Not used | **Not a dependency** |
| Langfuse | Not used | **Not a dependency** |
| Commercial OTLP backends | Optional via endpoint config | Operator-chosen; not required to run the pipeline |

### Explicit boundary rules

1. Do **not** add Helicone, Langfuse, or other SaaS SDKs as required package dependencies for this MVP.
2. Do **not** vendor TokScale binaries into this repository for the importer to work.
3. Do **not** store prompts, completions, cookies, or secrets in the spool “for debugging.”
4. OTLP export is **opt-in** via config; local spool + CLI export is sufficient for MVP acceptance.

## Tests

```bash
python -m pytest backend/tests/test_observability_*.py -q
python scripts/cbm_trace_ctl.py benchmark --spans 200 --usage 50
```

## Integration notes (future, out of MVP file ownership)

Other lanes may later call `TracePipeline` from ACPX workers or Manager run lifecycle. This MVP is intentionally self-contained so parallel worktrees are not blocked. Do not revert unrelated harness/UI changes when wiring integration later.
