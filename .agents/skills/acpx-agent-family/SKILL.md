---
name: acpx-agent-family
description: >
  Build, plan, install, and verify Orca-supervised ACPX agent-family pipelines
  (Plan → Forge → parallel skill/auth/extension/Soniox/token gates → IDR →
  Tribunal → Release). Use when routing agent stages only via ACPX with
  grok-build or opencode, enforcing deterministic quality gates, fail-closed
  secrets, concurrency caps, and Tribunal-before-Release. Trigger on agent-family
  DAG, ACPX harness, OpenCode/Grok Build routing, Soniox contract, skill
  benchmark install, or release evidence for this pipeline.
metadata:
  category: orchestration
  triggers:
    - acpx-agent-family
    - agent-family pipeline
    - ACPX harness
    - OpenCode
    - Grok Build
    - Tribunal
    - Soniox contract
    - Orca supervised DAG
---

# ACPX Agent Family

Installable skill for **Orca-supervised, ACPX-only agent-family DAGs**. It
teaches the project pipeline contract, ships a dry-run-default installer, and
a temp-space skill benchmark that scores structure, triggers, secrets, and
plan generation.

## When to use

- Planning or executing the project agent-family pipeline under Orca.
- Routing LLM stages through **ACPX** only (`grok-build` or `opencode`).
- Wiring parallel deterministic gates (skill, auth, extension-skin, Soniox,
  token-cost) before IDR and Tribunal.
- Installing this skill into an agent skills directory with hash verification.
- Pre-release checks: fail-closed secrets, concurrency ≤ 4, Tribunal before
  Release.

Do **not** use for generic browser automation, non-ACPX multi-agent fans, or
unrelated CloakBrowser profile ops (see `cloakbrowser-orca-control`).

## Core contract (irreducible)

1. **Orca supervises** the DAG; workers report heartbeats and `worker_done`.
2. **Agent stages** always use harness `acpx` and agent ∈ `{grok-build, opencode}`.
3. **Deterministic gates** are argv-only: no LLM, no external auth, no shell
   composition.
4. **Default mode is plan/dry-run**; portable `execute` is fail-closed and
   requires **absolute** permission-policy and MCP config files on disk.
5. **Fail closed** on secrets, path escapes, symlinks, concurrency > 4, cycles,
   duplicate outputs, or second browser spawn.
6. **Tribunal must pass before Release.**

Details: `references/architecture.md`, `references/quality-gates.md`.

## DAG

```text
plan → forge → [skill-benchmark, auth-benchmark, extension-skin,
                soniox-contract, token-cost] → idr → tribunal → release
```

| Stage | Kind | Notes |
| --- | --- | --- |
| plan | agent | ACPX: grok-build or opencode |
| forge | agent | Materialize executable stage work |
| skill-benchmark | deterministic | Skill structure / install scores |
| auth-benchmark | deterministic | **Only** browser-allowed gate |
| extension-skin | deterministic | Extension packaging contract |
| soniox-contract | deterministic | Speech/contract fixture checks |
| token-cost | deterministic | Metrics-only budget gate |
| idr | agent | Redacted evidence review |
| tribunal | agent | Release gate review |
| release | agent | Redacted release report only |

Private reports land under `artifacts/agent-family/private/<stage>-report.json`
(mode `0600` when written by project scripts). Unique paths per stage.

## Agent routing (ACPX only)

Allowed routes:

- `acpx:grok-build` (default)
- `acpx:opencode`

Rules:

- Never `npx` or global ACPX fallback; use repo-pinned ACPX (project pin 0.12.1).
- Prompts travel **stdin only**; do not persist raw prompts in artifacts.
- `agent-map` may assign per agent-stage only those two agents.
- Reject `claude`, `cursor`, bare `codex`, etc. at plan time.

Project CLI (repo root, not shipped in this skill package):

```bash
python3 scripts/agent_family_pipeline.py plan
python3 scripts/agent_family_pipeline.py dry-run \
  --permission-policy /absolute/path/permission-policy.json \
  --mcp-config /absolute/path/mcp.json
python3 scripts/agent_family_pipeline.py execute \
  --permission-policy /absolute/path/permission-policy.json \
  --mcp-config /absolute/path/mcp.json \
  --agent-map '{"plan":"opencode","forge":"grok-build"}'
```

Portable plan fixture (this skill, no repo imports). Modes `plan` / `dry-run`
never set `would_execute=true`. Mode `execute` raises unless absolute policy
files validate:

```bash
python3 .agents/skills/acpx-agent-family/scripts/benchmark_skill.py --json
# plan JSON is generated inside the benchmark temp workspace
# golden + negative fixtures: references/fixtures/
```

## Deterministic gates

Each gate builds a pure argv list (no `shell=True`, no `&&`/`|`). Project
implementation lives in `scripts/agent_family_pipeline.py gate --gate <name>`.

Hard rules:

- Concurrency hard-capped at **4**.
- Gates share parallel group `family_gates` after `forge`.
- Gate failure **skips Tribunal and Release** (fail-closed).
- Token/cost accounting is metrics-only (integer microusd); reject payloads
  containing prompts, cookies, or API keys. See project
  `scripts/agent_family_budget_gate.py`.

## Fail-closed secrets

Reject plans, prompts, reports, and installer paths containing:

- `private_key`, PEM blocks, `authorization: bearer`, `password=`, `cookie=`,
  `api_key=`, `apikey=`, `secret=`

Never put agent keys, lease tokens, or cookies in argv, URLs, logs, or skill
files. Installer refuses `.env` and similar names.

## Install this skill

Default is **dry-run**. Explicit `--dest` required. Only this skill directory
is copied. Symlinks (source, destination, ancestors, nested files/dirs) and path
escapes are rejected via `lstat` before resolve. Bytecode (`__pycache__`,
`*.pyc`) is excluded. Apply mode verifies SHA-256 after copy and is idempotent.

**macOS note:** paths under `/var/...` often include the system alias
`/var → /private/var`. Pass a **canonical** destination
(`Path(dest).resolve()`, or create under `Path(tempfile.gettempdir()).resolve()`)
so install is not rejected for that system alias. Explicit user-created symlink
parents remain fail-closed — do not install through them.

```bash
# Dry-run (default): plan + hashes, no writes
python3 .agents/skills/acpx-agent-family/scripts/install_and_verify.py \
  --dest /path/to/agent/skills --json

# Apply install + hash verify (prefer resolved absolute dest on macOS)
python3 .agents/skills/acpx-agent-family/scripts/install_and_verify.py \
  --dest /path/to/agent/skills --apply --json
```

If `--dest` does not end with `acpx-agent-family`, that segment is appended.

## Benchmark

Runs entirely under a temporary directory and scores eight dimensions
(must all pass):

1. structure (includes golden/negative fixture files; no bytecode)
2. trigger_metadata
3. no_todos (no unfinished-work markers)
4. executable_scripts
5. dry_run_install
6. plan_generation (golden match; negatives + mutations must fail; execute gated)
7. forbidden_secret_scan
8. forward_test_fixture

```bash
python3 -B .agents/skills/acpx-agent-family/scripts/benchmark_skill.py --json
python3 -B .agents/skills/acpx-agent-family/scripts/test_skill_scripts.py
```

## Orca worker habits

When dispatched under Orca:

- Heartbeat about every five minutes with `taskId` + `dispatchId`.
- Use `orca orchestration ask` for coordinator questions (never local TUI prompts).
- Send `worker_done` once with a 3-sentence body; include `files-modified` and
  optional `report-path`.
- Stay inside the owned path for the task (for skill work: this directory).

## Anti-patterns

- Routing agent stages outside ACPX or with disallowed agents.
- Claiming execute success from plan/dry-run mode.
- Skipping Tribunal or treating a failed gate as soft-warn.
- Shipping README inside the skill package.
- Following symlinks or installing without an explicit destination.
- Embedding live secrets, raw prompts, or cookies in reports.

## Workflow checklist

1. Generate or load plan (`mode=plan`, `would_execute=false`).
2. Confirm every agent stage route is `acpx:grok-build|opencode`.
3. Confirm deterministic gates are argv-only and parallel after forge.
4. Run skill benchmark + project gates as required by the ticket.
5. Run IDR on redacted evidence only.
6. Run Tribunal; only then Release with metrics/status artifacts.
7. Install skill with dry-run first, then `--apply` if deployment needs it.

## Resources

| Path | Role |
| --- | --- |
| `scripts/install_and_verify.py` | Dry-run-default installer + SHA-256 verify |
| `scripts/benchmark_skill.py` | Temp-space quality benchmark + plan fixture |
| `scripts/test_skill_scripts.py` | Unit tests (stdlib unittest) |
| `references/architecture.md` | DAG, routing, reports |
| `references/quality-gates.md` | Gate matrix and fail-closed rules |
| `references/fixtures/` | Hand-authored golden + negative plans |
| `agents/openai.yaml` | OpenAI-compatible skill UI metadata |
