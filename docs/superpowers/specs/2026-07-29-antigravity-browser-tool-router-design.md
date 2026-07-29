# Antigravity Browser Tool Router Design

**Status:** Approved for implementation by the user on 2026-07-29.

## Goal

Let Antigravity or Grok control one Manager-owned CloakBrowser profile while Browser Harness, Unbrowse, and Stagehand remain independently detectable, reorderable, testable, and replaceable behind one run-scoped router.

## Non-negotiable invariants

1. CloakBrowser Manager owns the browser process, profile, proxy, authentication state, automation lease, and audit trail.
2. A tool may never launch or silently switch to a second browser for a managed run.
3. Every tool receives only the same short-lived run capability and approved origin set.
4. Exactly one tool may mutate a profile at a time. Tool fallback occurs inside the active profile lease.
5. Policy, authentication, origin, capability, and identity failures fail closed. Only route misses, unsupported actions, or explicitly classified transient failures may fall through.
6. Antigravity and Grok are providers. Browser Harness, Unbrowse, and Stagehand are browser tools. These concepts are not represented by one mutually exclusive field.
7. Google Antigravity is not aliased to xAI Grok Build. Existing UI behavior that labels `grok-build` as Antigravity is deprecated and must be removed.
8. Secrets remain server-side. The UI may show readiness and reason codes but never raw provider keys, cookies, proxy credentials, capability tokens, or CDP URLs.

## Existing VCVM assets

- `agy` and `grok` CLIs are installed.
- ACPX/Grok Build is installed and currently passes its agent preflight.
- `grok-proxy.service` runs the existing `ai-cli-proxy-api` repository and exposes an OpenAI-compatible `/v1/models` endpoint on loopback port 8317.
- Unbrowse and Stagehand already have Manager worker implementations.
- `cbm_mcp.py` already exposes run-scoped bounded Manager browser tools.
- `automation_leases.py` already enforces one active automation lease per profile.

These assets are reused. No second CLI proxy, browser process, lease system, or public control API is introduced.

## Architecture

```text
Operator prompt
      |
      v
Provider adapter
  - Antigravity CLI
  - Grok ACP via ACPX
  - Grok OpenAI-compatible loopback proxy
      |
      v
cbm-router MCP (one run-scoped server)
      |
      +-- Unbrowse adapter       API-first / route reuse
      +-- Stagehand adapter      semantic UI / structured extraction
      +-- Browser Harness adapter direct CDP / universal fallback
      |
      v
Manager CDP capability gateway
      |
      v
One CloakBrowser profile + one automation lease
```

The provider sees one stable MCP surface. The router, not the provider prompt, owns tool ordering, fallback classification, and per-step telemetry.

## Public run contract

```json
{
  "provider": {
    "id": "grok",
    "transport": "acp",
    "model_alias": null
  },
  "browser_tools": [
    {"id": "unbrowse", "enabled": true},
    {"id": "stagehand", "enabled": true},
    {"id": "browser-harness", "enabled": true}
  ],
  "routing_policy": {
    "mode": "ordered-fallback",
    "allow_second_browser": false,
    "max_tool_attempts": 3
  }
}
```

Provider IDs in the normalized schema are `antigravity` and `grok`. Provider transports are `cli`, `acp`, and `openai-compatible`. Browser tool IDs are exactly `unbrowse`, `stagehand`, and `browser-harness`.

The first executable vertical slice accepts only `provider={id:"grok", transport:"acp"}` and runs through the existing ACPX/Grok Build adapter. `antigravity/cli` and `grok/openai-compatible` are reported through readiness first and become create-run-capable only when their dedicated provider adapters and tool loops pass their own tests. Until that point, create-run rejects those combinations with an explicit `provider_transport_not_ready` validation error rather than silently switching transports.

Legacy `harness` and `agent` request fields remain accepted during migration. Legacy requests are not rewritten: their new response fields are `provider=null`, `browser_tools=[]`, and `routing_policy=null`. New responses include both the normalized contract and legacy compatibility fields until the UI and CLI have migrated.

Existing Antigravity-profile compatibility is preserved temporarily: a profile stored with `harness="antigravity"` may still create the legacy `harness="acpx", agent="grok-build"` preset required by existing clients and tests. Such a legacy run is not relabelled as Google Antigravity and returns the null/empty normalized fields above. A new normalized run on that profile must explicitly request `provider={id:"grok",transport:"acp"}`. The UI migration removes the misleading Antigravity label while leaving stored profile data intact.

## Provider adapters

### Grok ACP

The existing ACPX worker remains the first production runner. It receives the normalized tool stack and injects it into the run-scoped `cbm-router` environment. This is the first E2E path because it already has structured sessions, preflight, cancellation, outputs, and capability handling.

### Antigravity CLI

Antigravity runs through the installed `agy` CLI with a workspace-local MCP configuration that contains only `cbm-router`. It is a real provider adapter, not a label for Grok Build. The adapter must support start, send, cancel, bounded output, and readiness. If the installed CLI cannot provide a stable non-interactive protocol, readiness reports `protocol_unavailable` rather than silently using Grok.

### Grok OpenAI-compatible proxy

The existing loopback `grok-proxy.service` is an optional fast provider transport. Manager probes `/v1/models` without exposing the proxy credential. It is readiness-only in the first vertical slice. A later task in this same implementation must add and test a bounded OpenAI tool-call loop before `grok/openai-compatible` becomes valid for create-run; until then the API reports it as detected but non-executable.

## Browser tool routing

The default order is:

1. Unbrowse for API discovery/reuse, structured reads, and compatible actions.
2. Stagehand for semantic DOM/UI actions and extraction when its model dependency is ready.
3. Browser Harness for direct CDP control and unsupported operations.

Stagehand remains visible when its browser runtime is installed but its semantic model is unavailable. In that state it reports `model_required` and is skipped only for eligible fallback classes. It never borrows or invents a provider key.

## Failure taxonomy

Eligible fallback:

- `route_miss`
- `unsupported_action`
- `tool_unavailable`
- bounded `transient_timeout`

Stop immediately:

- `auth_required`
- `origin_denied`
- `capability_invalid`
- `profile_lease_lost`
- `policy_denied`
- `secret_boundary_violation`
- `second_browser_attempted`

Every attempt is persisted through the existing task-output stream using `kind="metric"` for completed attempts, `kind="status"` for routing transitions, and `kind="error"` for terminal failures. The payload schema is exactly `{tool_id, action_class, duration_ms, result_class, fallback_reason}`; `fallback_reason` is null when no fallback occurred. Raw prompts, model messages, cookies, headers, capability tokens, proxy values, CDP URLs, and unrestricted tool payloads are never stored in this telemetry. Adapter results continue through their existing separately bounded/redacted output contracts.

`WorkerClaimResponse` and `WorkerCapabilityResponse` carry the exact normalized `provider`, ordered `browser_tools`, and `routing_policy` values from the run. Capability issuance does not mint a tool-specific browser identity: every adapter invocation uses the same run/profile capability and the same lease ID. Tests must compare the normalized fields across create response, claim, and capability and prove the token remains run-scoped.

## UI

The compact workspace shows only:

```text
Provider  Antigravity | Grok
Tools     Unbrowse -> Stagehand -> Browser Harness
Status    3/3 ready
```

An expandable `Harness / KI-Provider` panel exposes transport, detected models, per-tool readiness, reorder/enable controls, and one-button smoke tests. The same controls are available in normal and full view. Changing provider or tools does not remount the live browser viewer.

## Testing and proof

1. Contract tests for normalization, validation, persistence, claims, and backwards compatibility.
2. Router tests for ordered fallback, fail-closed errors, same-capability propagation, and no second-browser behavior.
3. Provider preflight tests for ACPX, Antigravity CLI, and the loopback OpenAI-compatible proxy.
4. UI tests proving provider and tool selections are independent.
5. Real VCVM E2E: Grok ACP -> router -> Unbrowse route miss -> Browser Harness fallback -> same profile evidence.
6. Real VCVM E2E: Stagehand readiness and explicit `model_required` or successful semantic action.
7. Agent Browser verification of the deployed UI with screenshots and console/network error capture.

## Rollback

All new database fields are nullable JSON/text columns. Legacy requests continue to use the current single-harness path. Deployment can disable the router feature flag and restart workers without deleting profiles, sessions, run outputs, or browser data.
