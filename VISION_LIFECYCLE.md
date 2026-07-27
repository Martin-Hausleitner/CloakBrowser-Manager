# Vision Lifecycle Contract

| Field | Value |
| --- | --- |
| Status | Active reusable contract |
| Owner | Martin Hausleitner |
| Applies to | CloakBrowser Manager and future Martin-owned projects |
| Review cadence | Project start, plan replacement, material architecture decision, and every release gate |
| Current project plan | [Universal Agent Browser Control Plane Implementation Plan](docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md) |

## Purpose

A project vision is useful only when it changes how work is selected, built,
tested, released, and handed over. This contract makes the vision a living
project interface rather than a static statement.

The same lifecycle should be used in every future project. Technology choices,
team size, and hosting may change; the obligation to connect intent to current
truth does not.

## Required Vision Pack

Every project keeps these concerns discoverable. Small projects may combine
files, but they must preserve the boundaries and links.

| Concern | Required answer |
| --- | --- |
| Product vision | Who benefits, what outcome changes, and what principles survive implementation changes? |
| Sovereignty | Who owns data, identities, credentials, exports, backups, and shutdown? |
| Project scope | What does this product own, integrate, defer, and refuse to duplicate? |
| Agent contract | Which agents and harnesses may act, under which identity, lease, approval, and evidence rules? |
| Architecture | Where are the systems of record, trust boundaries, interfaces, and failure containment points? |
| Current reality | What is implemented, locally tested, live-proven, blocked, historical, or unknown today? |
| Active plan | Which plan is authoritative now, who owns it, and what condition replaces or closes it? |
| Tickets | Which acceptance item owns each material change and remaining gap? |
| Testing | Which smallest proof and which release gate validate each claim? |
| Security | Which actions, data, credentials, captures, and production changes need special controls? |
| Governance | Who decides, how conflicts are resolved, and where decisions are recorded? |
| Conduct | How humans and agents collaborate without hiding uncertainty, overriding work, or leaking data? |

## Current-Truth Block

At project start and before every release, record a short current-truth block in
the project-specific vision or active plan:

```text
vision version:
active plan:
active milestone or ticket set:
current implementation status:
freshest test and evidence receipts:
known gaps and blocked prerequisites:
deployment environment and revision:
next decision or stop condition:
```

The block may link to machine-readable receipts instead of duplicating them. It
must never promote an old transcript, screenshot, or passing local test into a
current live-deployment claim.

## Lifecycle

### 1. Frame

- Name the owner, users, agents, desired outcome, non-goals, and sovereignty
  boundary.
- Research existing systems before adopting or rebuilding a capability.
- Record external inspiration and state which mechanisms are not adopted.

### 2. Contract

- Define systems of record, trust boundaries, interfaces, failure states, and
  rollback expectations.
- Define human and agent permissions, typed outputs, cancellation, audit, and
  redaction behavior.
- Assign status labels that distinguish planned, implemented, locally tested,
  live-proven, physical-device-proven, blocked, historical, and unknown.

### 3. Plan

- Link exactly one active implementation plan from the project vision.
- Break the plan into acceptance-owned tickets with file boundaries, tests,
  evidence, dependencies, and stop conditions.
- Record decisions that change product authority, data ownership, security, or
  externally visible behavior in an ADR or equivalent durable decision record.

### 4. Build

- Agents read the vision pack and current-truth block before release-significant
  work.
- Each work item preserves unrelated work and reports its exact ownership,
  revision, tests, evidence, gaps, and unperformed actions.
- Interfaces remain harness-neutral unless the active plan deliberately adopts a
  provider-specific boundary.

### 5. Prove

- Run the smallest targeted proof first, then the broader gate required by the
  blast radius.
- Validate the real state being claimed: correct environment, revision, user
  role, viewport/device, integration, and deployment path.
- Store redacted evidence so another person or agent can verify the claim without
  relying on conversation memory.

### 6. Release and Learn

- Reconcile vision, architecture, plan, tickets, implementation, tests, evidence,
  and release notes.
- Keep unresolved gaps visible; do not rewrite them as roadmap success.
- Update the current-truth block, close or replace the active plan, and record the
  next decision or stop condition.

## Change Rules

- A durable vision principle changes only through an explicit owner decision.
- A project-scope or architecture change updates the affected vision and
  architecture files before release.
- A plan may change frequently, but its replacement must be linked and the old
  plan marked superseded or complete.
- Evidence expires when the relevant code, configuration, environment, device,
  or dependency changes materially.
- Agent output is input to governance, never authority by itself.

## CloakBrowser Manager Application

For this repository:

- `VISION_PROJECTS.md` is the project-specific application.
- The active plan is
  `docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md`.
- The CBM-001 through CBM-024 GitHub issues are the acceptance ticket set.
- GitHub issues are product acceptance; `bd` is the local execution mirror.
- Martin's fork is the only writable GitHub target. CloakHQ upstream is a
  read-only comparison source.
- Current ACPX, Browser Use, VCVM, mobile, streaming, proxy, secret, and physical
  iPhone claims retain their documented evidence labels until fresh proof changes
  them.

## Bootstrap Checklist for a New Project

- [ ] Copy and adapt `docs/templates/PROJECT-VISION-TEMPLATE.md`.
- [ ] Name the owner, repository, systems of record, and explicit non-goals.
- [ ] Link one active plan and its acceptance tickets.
- [ ] Add the current-truth block with honest evidence labels.
- [ ] Define agent/harness identity, leases, approvals, outputs, and cancellation.
- [ ] Define secret, capture, logging, backup, export, and shutdown rules.
- [ ] Define targeted, integration, UI, mobile, security, and release gates as applicable.
- [ ] Add ADR or equivalent decision-record rules.
- [ ] Link the vision pack from `AGENTS.md` and contributor documentation.
- [ ] Review the pack at the first release gate and whenever the plan changes.

## Inspirations

This lifecycle is an original project contract informed by the separation of
vision, sovereignty, projects, agents, architecture, testing, security,
governance, and contribution concerns in the official
[Block Buzz documentation](https://github.com/block/buzz). It does not adopt
Buzz-specific protocol, relay, identity, event-kind, or community mechanisms.
