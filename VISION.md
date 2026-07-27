# Universal Project Vision

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck on every release gate and whenever governance precedence changes |
| Applies to | CloakBrowser Manager now; future Martin-owned control-plane projects by template |

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

## Documentation Map

- [VISION_SOVEREIGN.md](VISION_SOVEREIGN.md) defines ownership, export, secrets, and local-first expectations.
- [VISION_PROJECTS.md](VISION_PROJECTS.md) applies this vision to CloakBrowser Manager.
- [VISION_AGENT.md](VISION_AGENT.md) defines the harness-neutral agent contract.
- [ARCHITECTURE.md](ARCHITECTURE.md) defines boundaries and systems of record.
- [TESTING.md](TESTING.md) defines proof gates and evidence rules.
- [SECURITY.md](SECURITY.md) defines secret, passkey, proxy, browser, and logging safety.
- [GOVERNANCE.md](GOVERNANCE.md) defines precedence and decision flow.
- [CONTRIBUTING.md](CONTRIBUTING.md) defines the contribution workflow.
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
