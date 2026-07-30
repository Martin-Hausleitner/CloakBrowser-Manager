# ACPX Agent Family — Architecture

## Purpose

Define a single project-level **agent-family DAG** that Orca can supervise:

- LLM work runs only through **ACPX** sessions.
- Quality work runs as **deterministic** argv lanes (no model, no interactive auth).
- Evidence is **private, redacted, and unique per stage**.
- **Tribunal** is the mandatory pre-release gate.

This skill documents the contract. Runtime orchestration for the CloakBrowser
Manager fork lives in repo `scripts/agent_family_pipeline.py` (and related
budget/auth helpers). The skill’s own `scripts/` package is portable for
install + benchmark without importing the full app.

## Topology

```text
                    ┌──────────────────────────────┐
                    │  Orca coordinator (supervise) │
                    └──────────────┬───────────────┘
                                   │ dispatch / heartbeat / worker_done
                                   v
 plan ──► forge ──► parallel family_gates ──► idr ──► tribunal ──► release
                      │
                      ├─ skill-benchmark
                      ├─ auth-benchmark  (only browser lane)
                      ├─ extension-skin
                      ├─ soniox-contract
                      └─ token-cost
```

Edges (default):

| From | To |
| --- | --- |
| plan | forge |
| forge | skill-benchmark, auth-benchmark, extension-skin, soniox-contract, token-cost |
| each gate | idr |
| idr | tribunal |
| tribunal | release |
| release | ∅ |

Validation requirements:

- Acyclic graph with all pipeline stage nodes present.
- Unique `artifacts/agent-family/private/<stage>-report.json` outputs.
- At most one browser-using stage (`auth-benchmark`).
- `max_concurrency` ∈ [1, 4].

## Stage kinds

### Agent stages

`plan`, `forge`, `idr`, `tribunal`, `release`

| Field | Value |
| --- | --- |
| harness | `acpx` only |
| agent | `grok-build` (default) or `opencode` |
| route | `acpx:<agent>` |
| transport | stdin prompt; do not persist raw prompt |
| session | derived name from seed `agent-family:<stage>` |

ACPX command surface (project runner):

1. ensure session (permission policy + MCP config)
2. prompt (stdin)
3. close session

No `npx`, no global binary fallback — resolve **repo-pinned** ACPX.

### Deterministic stages

`skill-benchmark`, `auth-benchmark`, `extension-skin`, `soniox-contract`,
`token-cost`

| Field | Value |
| --- | --- |
| kind | `deterministic` |
| harness / agent | null |
| parallel_group | `family_gates` |
| command | pure argv list via project `gate` subcommand |

Deterministic lanes must not embed LLM agent ids or external auth URLs in argv.

## Modes

| Mode | Executes side effects? | Requirements |
| --- | --- | --- |
| plan (default) | no (`would_execute=false`) | none |
| dry-run | no (`would_execute=false`) | optional absolute policy paths for command material |
| execute | yes (`would_execute=true`) | absolute, non-symlink `permission_policy` + `mcp_config` JSON files |

Portable skill generator (`scripts/benchmark_skill.generate_agent_family_plan`):

- Never sets `would_execute=true` for `plan` / `dry-run`.
- Raises on `mode=execute` without absolute validated policy files.
- Relative policy paths are rejected.

Project `build_plan_or_dry_run` style helpers must still **refuse** `execute`.

Hand-authored fixtures under `references/fixtures/`:

- `golden_plan.json` — independent contract for stage order, edges, ACPX routes.
- `negative_*.json` — wrong DAG order, non-ACPX route, excessive concurrency,
  unsafe execute; each must fail `validate_plan_fixture`.

## Reports and privacy

- Schema id (project): `cloakbrowser.agent-family-pipeline.v1`
- Default aggregate report:
  `artifacts/agent-family/private/agent-family-pipeline-report.json`
- Contents: stage status, metrics, pass/fail notes.
- Forbidden: raw prompts, cookies, API keys, PEM private keys, bearer tokens.

## Budget / token cost

Token-cost gate is **metrics-only**:

- Integer microusd only (no float money).
- Exact-repeat dedupe; conflicting event ids fail closed.
- Mirror double-count prevention via canonical event id + source precedence.
- Reject metric events that include prompt/message/secret-like fields.

## OpenCode and Grok Build

Both are first-class ACPX agent ids for this family:

- Prefer **grok-build** as the default for plan/tribunal/release consistency.
- Use **opencode** where the ticket or agent-map assigns forge/IDR coding work.
- Mixing is allowed only through validated `agent-map` keys on agent stages.

## Skill package layout

```text
acpx-agent-family/
  SKILL.md
  agents/openai.yaml
  scripts/install_and_verify.py
  scripts/benchmark_skill.py
  scripts/test_skill_scripts.py
  references/architecture.md
  references/quality-gates.md
  references/fixtures/golden_plan.json
  references/fixtures/negative_*.json
```

No README inside the skill. No `__pycache__` / `*.pyc` in the tree or install
manifest. Installer uses `lstat` on source/destination ancestors before resolve,
rejects symlink directories before walk pruning, copies only this tree to an
explicit destination, and verifies SHA-256 digests.
