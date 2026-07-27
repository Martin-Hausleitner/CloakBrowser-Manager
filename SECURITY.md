# Security Contract

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck before any release, secret-provider change, adapter addition, or remote deployment |
| Related docs | [VISION_SOVEREIGN.md](VISION_SOVEREIGN.md), [ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md) |

## Security Goal

CloakBrowser Manager must let humans and agents operate browser profiles without turning the Manager, screenshots, logs, model context, or adapter configs into credential exfiltration paths.

## Non-Negotiable Boundaries

- No raw passwords, cookies, proxy credentials, API keys, OAuth refresh tokens, TOTP seeds, passkey material, bearer tokens, or provider tokens in committed docs, code comments, test snapshots, CLI args, process titles, screenshots, or model prompts.
- Secrets are referenced and used through scoped providers or brokers. A reveal command is not part of the baseline product contract.
- Browser cookies and local storage are profile continuity, not portable configuration.
- Device-bound passkeys are not cloned. Human handoff or re-enrollment is required.
- Agent and worker capabilities are scoped, leased, revocable, and auditable.
- Raw CDP, direct vault access, unrestricted shell, and arbitrary Chromium flags are not default agent capabilities.

## Redaction Rules

All API payloads, logs, typed outputs, CLI stdout/stderr, MCP events, screenshots, release reports, and evidence artifacts must redact:

- authorization headers and bearer tokens;
- `cbm_*` worker/run/agent tokens;
- `host:port:user:pass` and URL userinfo proxy forms;
- cookie and session header values;
- password, secret, token, key, OTP, TOTP, and private-key fields;
- local tunnel endpoints, private URLs, and file paths when disclosure is not required for reproduction.

## Secret Use Flow

1. Manager grants an operation-scoped capability.
2. Secret broker resolves a reference for an allowed origin/action.
3. Secret enters only the protected execution path.
4. Capture surfaces are suppressed or redacted while the value is present.
5. Broker records a receipt without the value.
6. Capability expires or is revoked after use.

## Proxy Safety

Proxy inventory may show provider, country/region, health, masked endpoint, and redacted score. It must not return credentials to UI, API, logs, model context, screenshots, or evidence. Migration from stored proxy URLs to secret references must include a leak scan and rollback receipt.

## Adapter Safety

- ACPX runtime versions are exact-pinned and drift blocks startup until contract tests pass.
- Agent prompts go through stdin or private files, not argv.
- Private policy/MCP/config/capability files use restrictive permissions and are removed on terminal paths.
- Unknown event envelopes, oversized outputs, raw provider errors, and secret-like stderr fail closed with redacted errors.
- Cancellation must terminate child processes and release leases.

## Known Gaps

- Secret-provider bakeoff and proxy credential migration are not complete until CBM-016 and related tickets produce proof.
- Provider authentication for ACPX adapters is intentionally not copied into repo config.
- Security evidence must be refreshed for each release; old scans do not prove the current diff.

## Reporting

Use a private GitHub security advisory for this fork:
[report a vulnerability privately](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/security/advisories/new).
If that route is unavailable, contact the repository owner through an already
agreed private channel and include only the minimum reproduction details. Do not
open a public issue containing credentials, screenshots with account data,
private endpoints, or exploit details.

## Inspirations

Inspired structurally by [Block Buzz SECURITY.md](https://github.com/block/buzz/blob/main/SECURITY.md), adapted to browser profiles, passkeys, proxy credentials, MCP, ACPX, Browser Use, VCVM, and redacted release evidence.
