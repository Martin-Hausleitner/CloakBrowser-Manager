# Project Vision Template

| Field | Value |
| --- | --- |
| Status | Draft / accepted / deprecated |
| Owner | Replace with project owner |
| Review cadence | Replace with release or milestone cadence |
| Repository | Replace with repository or workspace |
| Active plan | Replace with plan or issue link |
| Current-truth receipt | Replace with status/evidence receipt or section link |
| Vision version | Replace with date, tag, or commit |

## Purpose

State the product outcome in one paragraph. Name the user, operator, or agent who benefits. Avoid implementation claims here unless they are already proven.

## Principles

1. Name the system of record.
2. Name the actor model: humans, agents, services, workflows.
3. Name the evidence rule for completion.
4. Name the ownership and export rule.
5. Name the security boundary.

## Current Reality and Active Plan

Record the operational truth at the time of review:

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

Link exactly one active plan. Mark replaced plans as superseded or completed;
do not leave multiple plans silently competing for authority.

## Product Scope

In scope:

- Replace with product-owned capabilities.

Out of scope:

- Replace with explicit non-goals and deferred gates.

## Systems of Record

| Domain | System of record | Boundary |
| --- | --- | --- |
| Product state | Replace | Replace |
| Source and plans | Git | Runtime secrets and per-run artifacts stay out |
| Secrets | External provider or local credential store | Store references and receipts, not raw values |
| Evidence | Redacted artifact store or CI output | Public docs cite safe summaries |

## Agent and Automation Contract

Describe which agents, harnesses, workers, or workflow engines are allowed. For each, define identity, lease or scope, allowed operations, forbidden bypasses, typed outputs, cancellation, and evidence.

## Security Contract

List credential classes, redaction rules, capture suppression rules, passkey/device rules, and disclosure process. Include the exact status for provider selection and migration.

## Testing and Evidence

Define status labels and proof gates:

- planned;
- implemented;
- locally tested;
- live-proven on the named deployment environment;
- mobile-proven on the named browser and viewport;
- physical-device-proven on the named device and browser;
- blocked;
- historical.

Map release-significant claims through:

```text
vision -> architecture -> plan -> ticket -> tests -> evidence -> release
```

## Architecture Decisions and Roadmap

- Link ADRs or equivalent durable records for changes to product authority, data
  ownership, security boundaries, interfaces, or externally visible behavior.
- Organize the roadmap by outcomes and acceptance evidence, not only component
  names or dates.
- Define what closes or replaces the active plan.

## Governance and Conduct

Name the decision owner, precedence rules, writable repositories, review process,
release authority, and conflict/escalation path. Link the project's code of
conduct for both human and agent contributors.

## Project Handoff

Define the minimum handoff receipt: repository, branch, worktree, owner, active
ticket, execution mode, current revision, completed proofs, unmerged work, known
gaps, next step, forbidden actions, and stop condition.

## Known Gaps

- Replace with current unknowns, blocked prerequisites, and unproven claims.

## Inspirations

List external docs or projects used for structural inspiration. Cite official sources. State which mechanisms are not adopted.

## Bootstrap Completion

- [ ] Vision, sovereignty, project, agent, architecture, testing, security,
  governance, contribution, and conduct concerns are linked.
- [ ] One active plan and its ticket set are linked.
- [ ] The current-truth block is fresh and uses explicit evidence labels.
- [ ] Systems of record and writable repositories are named.
- [ ] Agent identities, scopes, approvals, cancellation, and typed outputs are defined.
- [ ] Release gates and plan-replacement conditions are defined.
