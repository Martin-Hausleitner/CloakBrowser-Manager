# Project Vision Template

| Field | Value |
| --- | --- |
| Status | Draft / accepted / deprecated |
| Owner | Replace with project owner |
| Review cadence | Replace with release or milestone cadence |
| Repository | Replace with repository or workspace |
| Active plan | Replace with plan or issue link |

## Purpose

State the product outcome in one paragraph. Name the user, operator, or agent who benefits. Avoid implementation claims here unless they are already proven.

## Principles

1. Name the system of record.
2. Name the actor model: humans, agents, services, workflows.
3. Name the evidence rule for completion.
4. Name the ownership and export rule.
5. Name the security boundary.

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

## Known Gaps

- Replace with current unknowns, blocked prerequisites, and unproven claims.

## Inspirations

List external docs or projects used for structural inspiration. Cite official sources. State which mechanisms are not adopted.
