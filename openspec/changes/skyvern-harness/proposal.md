## Why

CloakBrowser Manager already exposes per-profile cloaking (fingerprint, proxy, CDP). Operators still need LLM+Vision browser automation. Skyvern (OSS) provides that agent stack, but runs its own browser by default — losing CloakBrowser anti-detect. We need Skyvern as a first-class harness that drives CloakBrowser sessions through the Manager CDP layer.

## What Changes

- Add an optional **Skyvern harness** that connects Skyvern's automation core to a running CloakBrowser profile via Manager CDP (`/api/profiles/{id}/cdp`) with auth headers.
- Expose Manager API endpoints to resolve harness capabilities, bind a profile, run a Skyvern task through the cloak layer, and capture run metadata/screenshots.
- Document AGPL-3.0 license obligations for Skyvern as an optional dependency (not vendored into the MIT GUI tree).
- Add OpenSpec change `skyvern-harness`, backend unit tests, and a proof runner for R040 evidence.

## Capabilities

### New Capabilities
- `skyvern-harness`: Combine Skyvern LLM+Vision automation with CloakBrowser Manager cloaking/anti-detect/per-session proxy via CDP adapter.

### Modified Capabilities

## Impact

- Backend: new `backend/harnesses/skyvern_*` modules and FastAPI routes under `/api/harnesses/skyvern`.
- Optional dependency: `skyvern` (AGPL-3.0) — not required for Manager core boot; harness reports `unavailable` when missing.
- Runtime: requires a launched profile (CloakBrowser binary) and, for full agent tasks, an OpenAI-compatible LLM endpoint.
- Docs/report: license notice, combination architecture, proof screenshot path.
