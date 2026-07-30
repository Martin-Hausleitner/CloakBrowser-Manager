# Universal Project Vision

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck on every release gate and whenever governance precedence changes |
| Applies to | CloakBrowser Manager now; future Martin-owned control-plane projects by template |
| Lifecycle contract | [VISION_LIFECYCLE.md](VISION_LIFECYCLE.md) |

## Purpose

This repository treats a project as an owned control plane, not as a pile of agent transcripts. Humans, agents, harnesses, workflows, browsers, tasks, tests, and release evidence must all resolve to one understandable product state.

The reusable rule is simple: every capability needs an owner, a boundary, an acceptance path, and evidence. A feature is not complete because code exists, a component test passed, or an agent said it worked. A feature is complete only when the documented contract, shipped UI/API/CLI behavior, tests, and redacted evidence agree.

## Universal Principles

1. **One product authority.** Runtime tools may execute work, but the project chooses one source of truth for product state, ownership, approvals, audit, and release readiness.
2. **Agents are scoped operators.** Agent access is explicit, leased, revocable, and traceable. No harness receives hidden global power through config, screenshots, cookies, raw CDP, or leaked secrets.
3. **Evidence beats memory.** Release claims cite tests, screenshots, typed artifacts, logs, or receipts that can be inspected after the conversation is gone.
4. **Interfaces outlive tools.** ACP/ACPX, Browser Use, Orca, Codex, Claude, Cursor, Grok, OpenCode, and future adapters must speak bounded project contracts rather than become product authorities.
5. **Sovereignty is a feature.** The owner can run, inspect, export, back up, and shut down the system without losing the project record to a hosted tool or opaque agent cache.
6. **No false completion.** Unknown, blocked, unavailable, unproven, historical, and live-proven states are different states and must be labeled differently.

## Traceability Contract

Every material change should be traceable through this chain:

```text
vision -> architecture -> implementation plan -> ticket -> tests -> evidence -> release
```

For this repository, the current implementation plan is [docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md](docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md). The ticket layer is the CBM-001 through CBM-024 issue set linked from that plan.

## Reuse in Every Future Project

Every future project should begin with the same small vision pack, adapted to its
own product rather than copied blindly:

1. `VISION.md` explains the durable product outcome and principles.
2. `VISION_SOVEREIGN.md` defines ownership, portability, shutdown, and data exit.
3. `VISION_PROJECTS.md` applies the vision to the current repository and names
   what the product owns, integrates, and deliberately does not own.
4. `VISION_AGENT.md` defines how agents, harnesses, tools, and humans share the
   system without bypassing identity, leases, approvals, or evidence.
5. `VISION_LIFECYCLE.md` keeps the vision connected to current reality, the
   active plan, tickets, decisions, tests, evidence, and release status.
6. `ARCHITECTURE.md`, `TESTING.md`, `SECURITY.md`, `GOVERNANCE.md`,
   `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` turn the vision into boundaries
   and working rules.

The reusable starting point is
[docs/templates/PROJECT-VISION-TEMPLATE.md](docs/templates/PROJECT-VISION-TEMPLATE.md).
A project may use different technologies or repositories, but it may not omit
the current-plan and current-evidence links while still claiming that the vision
is operational.

## Documentation Map

- [VISION_SOVEREIGN.md](VISION_SOVEREIGN.md) defines ownership, export, secrets, and local-first expectations.
- [VISION_PROJECTS.md](VISION_PROJECTS.md) applies this vision to CloakBrowser Manager.
- [VISION_AGENT.md](VISION_AGENT.md) defines the harness-neutral agent contract.
- [VISION_LIFECYCLE.md](VISION_LIFECYCLE.md) defines how the vision stays tied to current work and evidence.
- [ARCHITECTURE.md](ARCHITECTURE.md) defines boundaries and systems of record.
- [TESTING.md](TESTING.md) defines proof gates and evidence rules.
- [SECURITY.md](SECURITY.md) defines secret, passkey, proxy, browser, and logging safety.
- [GOVERNANCE.md](GOVERNANCE.md) defines precedence and decision flow.
- [CONTRIBUTING.md](CONTRIBUTING.md) defines the contribution workflow.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) defines human-agent collaboration conduct.
- [docs/templates/PROJECT-VISION-TEMPLATE.md](docs/templates/PROJECT-VISION-TEMPLATE.md) is the reusable starting point for future projects.

## Known Gaps

- This document set is a contract; it does not by itself implement CBM tickets.
- ACPX live VCVM proof, adapter-specific authentication, physical iPhone Safari evidence, direct Tailnet proof, and durable workflow selection remain evidence-gated until fresh artifacts exist.
- Future projects must replace CloakBrowser-specific assumptions through the project template before claiming this vision applies.

## Inspirations

This structure is inspired by the official Block Buzz documentation set, especially its separation of vision, sovereign ownership, project, agent, architecture, testing, security, governance, and contribution concerns:

- [Block Buzz VISION.md](https://github.com/block/buzz/blob/main/VISION.md)
- [Block Buzz VISION_SOVEREIGN.md](https://github.com/block/buzz/blob/main/VISION_SOVEREIGN.md)
- [Block Buzz VISION_PROJECTS.md](https://github.com/block/buzz/blob/main/VISION_PROJECTS.md)
- [Block Buzz VISION_AGENT.md](https://github.com/block/buzz/blob/main/VISION_AGENT.md)
- [Block Buzz ARCHITECTURE.md](https://github.com/block/buzz/blob/main/ARCHITECTURE.md)
- [Block Buzz TESTING.md](https://github.com/block/buzz/blob/main/TESTING.md)
- [Block Buzz SECURITY.md](https://github.com/block/buzz/blob/main/SECURITY.md)
- [Block Buzz GOVERNANCE.md](https://github.com/block/buzz/blob/main/GOVERNANCE.md)
- [Block Buzz CONTRIBUTING.md](https://github.com/block/buzz/blob/main/CONTRIBUTING.md)

The mechanics here are CloakBrowser-specific. Buzz/Nostr-specific event-log, relay, key, and community mechanisms are not adopted by this contract.
