# Secure Action Recorder Research Packet

**Date:** 2026-07-30  
**Scope:** Chrome MV3 recorder, Browser Use/Browser Harness, ACPX delivery, and reference-only vault integration  
**Evidence rule:** official documentation and upstream repositories only

## Decision

Use a small Manifest V3 extension that records only after an explicit operator action, requests the narrowest possible host access, normalizes events locally, and keeps bounded recorder state in `chrome.storage.session`. Persist only redacted canonical flows through CloakBrowser Manager. Vaultwarden remains the human vault, Infisical owns machine identities and short-lived CI/runtime authentication, and gopass is an optional local sovereign mode. ACPX transports structured agent sessions and CI/CD workflows but does not own browser, vault, or product state.

## Sources and Implications

### Chrome Extensions

- [Manifest V3 overview](https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3): remotely hosted code is not allowed; all recorder code must ship inside the extension package.
- [Protect user privacy](https://developer.chrome.com/docs/extensions/develop/security-privacy/user-privacy): request the minimum permissions; `activeTab` and optional permissions are preferred to broad future-proof access. Extension storage is not encrypted, so sensitive data must not be stored client-side.
- [chrome.storage](https://developer.chrome.com/docs/extensions/reference/api/storage): `storage.session` is in-memory and can be restricted to trusted extension contexts; it is appropriate for bounded transient recorder state, not raw secrets.
- [Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts): content scripts run in isolated worlds but can read page DOM; messages must be validated because the page boundary remains hostile.

**Implementation consequence:** explicit record/stop, active-tab or per-origin injection, in-memory bounded state, no raw form-value persistence, strict message schema, no eval, and no remote code.

### Browser Use and Browser Harness

- [Browser Use](https://github.com/browser-use/browser-use): supports domain restrictions and browser automation behind an agent layer.
- [Browser Harness](https://github.com/browser-use/browser-harness): provides a thin direct CDP harness and editable helper layer.
- [Browser Harness connection guide](https://github.com/browser-use/browser-harness/blob/main/install.md): dedicated non-default browser profiles and explicit CDP URLs are suitable for unattended automation; direct attachment to a user's normal browser carries broader risk.
- [Browser Harness skill](https://github.com/browser-use/browser-harness/blob/main/SKILL.md): new tabs are preferred over clobbering the active user tab.

**Implementation consequence:** live proof must use a Manager-owned disposable profile and lease. Direct raw CDP access is not promoted into the product contract, and replay stays origin-bound and cancellable.

### ACPX

- [ACPX repository](https://github.com/openclaw/acpx): ACPX is an alpha headless ACP client with stateful sessions, queues, cancellation, structured output, and experimental TypeScript flows.
- [ACPX CLI reference](https://github.com/openclaw/acpx/blob/main/docs/CLI.md): flows can run scripted multi-step work and persist run state; exit codes and structured formats are available for CI.
- [ACPX site](https://acpx.sh/): ACPX exposes structured ACP messages instead of scraping terminal output.

**Implementation consequence:** pin the exact ACPX version, treat protocol or version drift as failure, use named per-run sessions, always cancel/close/reap, and preserve Manager run/lease authority. Because flow APIs are alpha, CI must include a schema/preflight gate and a non-ACPX deterministic fallback for static tests.

### Vaultwarden

- [Vaultwarden repository](https://github.com/dani-garcia/vaultwarden): Bitwarden-compatible self-hosted server with personal vaults, organizations, collections, sharing, member roles, groups, event logs, policies, and WebAuthn/2FA support. HTTPS is required for the web vault, and regular backups are recommended.

**Implementation consequence:** reuse Vaultwarden for human-facing credential administration instead of rebuilding it. Do not use it as the primary workload/machine-identity authority. CloakBrowser stores only provider/reference metadata and use receipts.

### Infisical

- [Machine identities](https://infisical.com/docs/documentation/platform/identities/machine-identities): identities are role-scoped at organization or project level and authenticate through Universal, Kubernetes, cloud-native, OIDC, JWT, or SPIFFE methods to receive short-lived access tokens.
- [Identity overview](https://infisical.com/docs/documentation/platform/identities/overview): identities should be separated by permission boundary, not blindly per machine.
- [CLI quickstart](https://infisical.com/docs/cli/usage): CI may authenticate a machine identity for a task-scoped short-lived token.

**Implementation consequence:** one least-privilege identity per distinct staging/release/security boundary; never print the token; prevent shell tracing and environment dumps; revoke on completion; return a receipt rather than a secret value.

### gopass

- [gopass age backend](https://github.com/gopasspw/gopass/blob/master/docs/backends/age.md): supports Git-backed stores and age recipients, but the age backend is marked experimental and its on-disk format may change.
- [gopassbridge](https://github.com/gopasspw/gopassbridge): browser integration uses a native-messaging bridge and `gopass-jsonapi`.

**Implementation consequence:** gopass/age is optional for local sovereign mode, not the default multi-user service. Pin versions and back up/migrate explicitly. Do not copy its native-messaging secret-reveal semantics into the agent-facing recorder API.

## Pareto Risks

1. A recorder with broad host permissions or persistent storage becomes a browsing-history/credential exfiltration surface.
2. Secret detection by field name alone misses custom widgets; classification must combine input type, autocomplete, labels, attributes, origin policy, and conservative fallback.
3. CDP attachment to the wrong profile exposes unrelated tabs and sessions.
4. A prompt compiler can reintroduce secrets even when capture redacted them; scan every boundary.
5. ACPX alpha drift can make a green local flow fail in CI or leave orphan processes.
6. Vault provider CLIs can print access tokens or secrets to stdout; no raw provider command output may enter logs.
7. Watchdog restart loops can amplify corruption or lock contention; cap retries and fail blocked.
8. Extension service-worker suspension can lose or reorder transient events; sequence and TTL must be explicit and loss must be reported honestly.
9. Browser Use replay can silently select a cached/default profile; the Manager lease and process identity must be verified first.
10. Screenshots and DOM snapshots can contain credentials even when event JSON is clean; sensitive checkpoints require redacted or suppressed screenshots.

## NotebookLM Status

The NotebookLM wrapper reports an authenticated but stale browser state and an empty notebook library. The normal Brave session is currently at Google sign-in. No credentials were requested or reused, and no external notebook was created. This packet is ready to be added as public-source material once the NotebookLM session is re-authenticated; until then, NotebookLM analysis remains **blocked**, not simulated.

