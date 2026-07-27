# CloakBrowser Manager Project Vision

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Project repository | Martin-Hausleitner/CloakBrowser-Manager fork |
| Active plan | [Universal Agent Browser Control Plane Implementation Plan](docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md) |

## Product Bet

CloakBrowser Manager should become the compact control plane for owned browser work: profiles, projects, accounts, proxies, sessions, runs, approvals, live browser views, typed outputs, and evidence in one place. Humans use it directly; agents use it through bounded contracts.

The current plan's goal is to let any approved ACP/ACPX, Browser Use, Orca, Codex, Claude, Cursor, Grok, or OpenCode harness operate one explicitly leased browser profile while humans can observe and govern the run.

## What CloakBrowser Manager Owns

- Users, groups, agents, projects, folders, profiles, accounts, tasks, sessions, runs, leases, approvals, typed outputs, artifacts, health, retention, audit, and release readiness.
- The compact desktop/tablet/mobile/fullscreen UI contract, including Phone Fit, session grid, typed outputs, browser controls, and keyboard-safe mobile composer.
- The API, CLI, MCP, and skill resource envelope for controlled automation.
- Release truth: no status is promoted from historical or local-only proof to live VCVM or physical-device proof without fresh evidence.

## What It Integrates

- CloakBrowser/Chromium for isolated browser profiles.
- CDP plus noVNC/KasmVNC baseline for browser automation and viewing.
- Browser Use as a bounded browser worker.
- ACPX/ACP as a pinned adapter layer for coding agents.
- Orca as a host bridge where available.
- Optional durable workflow engines only after benchmark gates prove operational value.
- External secret providers and identity providers only after the relevant bakeoffs prove they reduce risk.

## Product Experience Contract

Desktop prioritizes a three-panel control surface: project/session context, task conversation plus typed outputs, and live browser. Mobile prioritizes browser visibility, stable controls, keyboard safety, Phone Fit, fullscreen session switching, and no hidden benchmark/admin clutter in the main path.

The UI must never imply a harness is live because a selector exists. It must distinguish:

- configured but unavailable;
- worker present but adapter unproven;
- locally contract-tested;
- live-proven on VCVM;
- physically proven on iPhone/Safari.

## Current Evidence Posture

The README and active plan describe strong implemented foundations: profile management, mobile workspace, access boundaries, proxy inventory, health checks, Browser Use Web UI E2E, ACPX local contract tests, and broad backend/frontend/script suites. The docs also record explicit gaps: real ACPX managed-browser VCVM E2E, provider auth, physical Safari/iPhone proof, direct Tailnet proof, and durable workflow selection.

This vision inherits those distinctions. It does not upgrade any pending item to complete.

## Project Traceability

For this project, traceability is:

```text
VISION_PROJECTS.md
-> ARCHITECTURE.md
-> docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md
-> CBM issue
-> targeted tests and gates
-> redacted evidence artifact
-> release report
```

## Known Gaps

- The docs contract is new and must be kept aligned with the ticket plan as CBM-001 through CBM-024 land.
- README links are not changed in this slice; discoverability depends on future allowed README edits.
- Some historical test counts in README are useful context but must not replace fresh release evidence.

## Inspirations

Inspired structurally by [Block Buzz VISION_PROJECTS.md](https://github.com/block/buzz/blob/main/VISION_PROJECTS.md), with CloakBrowser Manager replacing Buzz's relay/community model as the product authority.
