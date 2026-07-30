# Governance Contract

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck when plans, tickets, release gates, or source-of-truth decisions change |
| Related docs | [VISION.md](VISION.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md) |

## Governance Goal

Governance keeps agents, humans, worktrees, tickets, tests, and releases aligned around one product truth. It prevents duplicated authorities, invisible state, and false completion claims.

## Precedence

When instructions conflict, use this order:

1. User or owner instruction for the current task.
2. Repository safety rules and AGENTS.md.
3. This governance contract and the root vision/lifecycle/security/testing docs.
4. Active implementation plan: [docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md](docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md).
5. Current CBM ticket or issue acceptance criteria.
6. Existing code patterns and tests.
7. Historical reports and transcripts.

Historical evidence informs decisions but does not override fresh tests, live artifacts, or explicit owner direction.

## Decision Classes

| Decision | Authority | Required evidence |
| --- | --- | --- |
| Product source of truth | Owner plus architecture docs | Updated architecture and release note |
| Adapter admission | Active plan plus security/testing gates | Version pin, descriptor, tests, E2E proof |
| Secret provider | Security gate and owner decision | Bakeoff, migration plan, leak scan |
| Workflow engine | Benchmark gate | Same scenario comparison, replay/cancel/retry proof |
| Release readiness | Release owner | Tests, build, screenshots/evidence, known gaps |
| Documentation contract | Owner or delegated docs owner | Link/placeholder/secret scan and diff check |

CBM GitHub issues are the product-acceptance record. `bd` is the local execution
mirror required by [AGENTS.md](AGENTS.md); it must reference the matching CBM
issue and may not silently redefine its acceptance criteria.

## Worktree and Agent Conduct

- Do not overwrite another worktree or revert unrelated edits.
- Each implementation slice needs file ownership, active ticket, stop condition, and verification path.
- Claims must cite commands or artifacts.
- Deploy, merge, credential changes, and external production actions require explicit authority.
- Once a scoped change is authorized and verified, the repository landing policy requires pushing it only to Martin's fork; the CloakHQ upstream remains read-only.
- If a verifier checks the wrong UI state, the result is invalid even if the screenshot looks good.

## Traceability Requirement

Every release-significant decision should leave a path from:

```text
vision -> architecture -> plan -> ticket -> implementation -> tests -> evidence -> release
```

If any link is missing, label the status honestly and keep the gap visible.

Every project must also keep one active-plan pointer and a fresh current-truth
block as defined in [VISION_LIFECYCLE.md](VISION_LIFECYCLE.md). Replacing a plan
requires marking the previous plan complete or superseded and updating the
project-specific vision, ticket set, and next stop condition.

## Known Gaps

- GitHub issues are the product ticket layer; the `bd` execution mirror still needs automated drift checking.
- A durable project-state receipt is planned in CBM-001.
- Feature-presence governance is planned in CBM-003.

## Inspirations

The official [Block Buzz GOVERNANCE.md](https://github.com/block/buzz/blob/main/GOVERNANCE.md)
links to Block's broader open-source governance rather than defining Buzz-specific
runtime policy. This repository keeps its own owner-led fork, CBM ticket, and
agent/browser release contract while using the Buzz documentation family only as
structural inspiration.
