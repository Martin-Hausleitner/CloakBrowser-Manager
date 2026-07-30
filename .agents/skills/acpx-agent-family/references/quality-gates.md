# ACPX Agent Family — Quality Gates

## Gate matrix

| Gate | Type | Must prove | Fail-closed effect |
| --- | --- | --- | --- |
| skill-benchmark | deterministic | Skill structure, triggers, install dry-run, no unfinished markers | Skip IDR chain / release path |
| auth-benchmark | deterministic | Auth pipeline contract; only browser lane | Same |
| extension-skin | deterministic | Extension packaging / skin contract | Same |
| soniox-contract | deterministic | Soniox fixture/contract checks | Same |
| token-cost | deterministic | Budget/meter integrity (microusd, no secrets) | Same |
| idr | agent (ACPX) | Redacted evidence review | Skip tribunal/release |
| tribunal | agent (ACPX) | ACPX routing, concurrency, unique reports, gate honesty | Block release |
| release | agent (ACPX) | Redacted release report only | N/A (terminal) |

Any failed gate in `family_gates` must **not** proceed to Tribunal as if green.

## Skill benchmark dimensions

`scripts/benchmark_skill.py` scores exactly these keys (1 point each):

1. **structure** — required files + fixtures present; no README; fail if any `__pycache__`/`.pyc` present (read-only; never delete evidence).
2. **trigger_metadata** — SKILL.md frontmatter name/description; openai.yaml fields.
3. **no_todos** — no unfinished-work markers in skill files.
4. **executable_scripts** — python3 shebang; scripts compile.
5. **dry_run_install** — installer default dry-run; no copies; hashes planned; no bytecode.
6. **plan_generation** — generator matches hand-authored golden; negative fixtures and mutations (wrong DAG, non-ACPX, concurrency>4, unsafe execute) must fail; casual execute rejected.
7. **forbidden_secret_scan** — no live secret material in the skill tree.
8. **forward_test_fixture** — temp JSON vs golden; re-check negatives/mutations; no leaks.

Benchmark **must** run under a temporary directory for install/fixture work.

Exit code 0 only when `total == max_total == 8`.

## Installer gates

`scripts/install_and_verify.py`:

| Rule | Behavior |
| --- | --- |
| Default mode | dry-run (no `--apply` ⇒ no writes) |
| Destination | required, explicit (`--dest`) |
| Scope | only this skill directory |
| Symlinks | `lstat` original path + every existing ancestor before resolve; reject source/dest symlink parents (including system aliases and user links); **lstat/reject every child dir symlink before SKIP_NAMES** (including `__pycache__` → outside); reject symlink files. Callers on macOS should use `Path(dest).resolve()` / temp under `Path(tempfile.gettempdir()).resolve()` — security logic does **not** broadly allow `/var` |
| Path escape | every file real path must remain under skill root |
| Hashes | SHA-256 over each installable file |
| Apply verify | re-hash destination; mismatch fails; idempotent re-apply OK |
| Junk skip | `.env`, `__pycache__`, `.git`, `*.pyc` / `*.pyo`, caches |

## Secret deny list (plan/prompt/report/argv)

Fail if any of these appear as live material:

- `private_key` / `privatekey` / PEM `BEGIN * PRIVATE KEY`
- `authorization: bearer`
- `password=`
- `cookie=`
- `api_key=` / `apikey=` / `secret=`
- Provider-looking live tokens (`sk-ant-`, `sk-proj-`, …)

Documentation may **name** forbidden patterns; it must not embed values.

## Concurrency and DAG safety

- Hard cap: **4** concurrent stage executions.
- Validate: acyclic, full stage set, unique outputs, ≤1 browser stage.
- Agent map keys limited to agent stages; values ∈ `{grok-build, opencode}`.

## Tribunal before Release

Checklist Tribunal must confirm:

1. Every agent stage route is `acpx:grok-build` or `acpx:opencode`.
2. Deterministic gates remained argv-only (no LLM harness tokens in commands).
3. Concurrency never exceeded 4.
4. Stage reports are private, unique, and free of secrets/raw prompts.
5. Failed gates did not soft-pass into Release.
6. Release artifact is redacted metrics/status only.

Release is forbidden when Tribunal status is failed/skipped.

## Project commands (repo root)

```bash
# Plan only
python3 scripts/agent_family_pipeline.py plan

# Unit contracts
python3 -m pytest scripts/test_agent_family_pipeline.py -q
python3 -m pytest scripts/test_agent_family_budget_gate.py -q

# Skill package (this skill) — use -B so tests do not write bytecode beside sources
python3 -B .agents/skills/acpx-agent-family/scripts/test_skill_scripts.py
python3 -B .agents/skills/acpx-agent-family/scripts/benchmark_skill.py --json
python3 /home/coder/.agents/skills/skill-creator/scripts/quick_validate.py \
  .agents/skills/acpx-agent-family
# Explicit post-test cleanup only (never inside product/benchmark):
# find .agents/skills/acpx-agent-family -name '__pycache__' -type d -exec rm -rf {} +
```

## Orca completion bar

Changed skill work is complete for a worker when:

1. Required skill files exist and validate.
2. Unit tests + benchmark pass.
3. `quick_validate.py` (or equivalent) accepts frontmatter.
4. Coordinator receives a single `worker_done` with accurate files-modified list.

No commit/push unless the task explicitly authorizes landing outside this skill
package rule set.
