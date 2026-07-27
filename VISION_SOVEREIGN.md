# Sovereign Ownership Vision

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck before every release and before adopting external identity, secret, workflow, or hosting services |
| Related docs | [VISION.md](VISION.md), [SECURITY.md](SECURITY.md), [GOVERNANCE.md](GOVERNANCE.md) |

## Sovereignty Goal

Martin must be able to understand, run, recover, and govern the system without depending on private model memory, hosted agent state, invisible dashboards, or leaked credentials. CloakBrowser Manager can integrate external tools, but ownership remains local, inspectable, and exportable.

## Ownership Rules

- Product state belongs to CloakBrowser Manager and its documented database/artifacts, not to ACPX caches, Browser Use histories, workflow engine dashboards, IDE chats, or CI logs.
- Git owns versioned source, docs, plans, schemas, tickets-as-text references, and reviewed runbooks. Git does not become the runtime event database.
- Evidence artifacts are retained as redacted files or CI artifacts with enough metadata to reproduce the claim. They are not committed when they contain secrets, cookies, local tunnel URLs, private screenshots, or unstable machine paths.
- Worktrees are owned units. A handoff must identify worktree, branch, owner, mode, active ticket, next safe step, forbidden action, and stop condition.

## Data Portability

A sovereign project must support:

- export of projects, profiles, accounts metadata, task runs, typed outputs, approvals, audit receipts, and release evidence;
- backup and restore of Manager state before release or migration;
- rebuild of adapter caches from Manager state where practical;
- explicit loss boundaries for browser cookies/storage, device-bound passkeys, and third-party provider state.

## Local-First Control

The default posture is self-hosted and local-first:

- single-node SQLite remains acceptable for the MVP until the plan's multi-writer and high-availability gates justify Postgres;
- VCVM is a controlled execution surface, not a place to paste credentials into config;
- Tailscale, tunnel, and remote browser paths must report their real route and latency state rather than imply direct/private proof;
- Docker, systemd, and launch scripts must be reproducible from Git plus external secret references.

## Secret and Passkey Ownership

- Passwords, API keys, cookies, proxy credentials, TOTP seeds, OAuth refresh tokens, and provider tokens are not project text.
- Secrets are referenced, leased, injected, rotated, revoked, and audited through a provider or broker.
- Device-bound passkeys, TPM keys, Secure Enclave credentials, and platform authenticators are not cloned. The system must request human handoff, re-enrollment, or provider-supported delegation.
- Screenshots, DOM capture, video, HAR bodies, clipboard sync, and model context are suppressed or redacted around secret use.

## Known Gaps

- Secret-provider selection is still gated by the CBM-016 bakeoff.
- Existing proxy credential migration work is planned but not complete unless the ticket produces database and evidence proof.
- Physical iPhone Safari, private Tailnet HTTPS, and direct route evidence remain pending until fresh device artifacts exist.

## Inspirations

Inspired structurally by [Block Buzz VISION_SOVEREIGN.md](https://github.com/block/buzz/blob/main/VISION_SOVEREIGN.md), but this document uses CloakBrowser Manager's self-hosted browser-control and secret-broker model rather than Buzz relay or Nostr mechanisms.
