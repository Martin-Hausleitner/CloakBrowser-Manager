## Context

CloakBrowser Manager launches isolated CloakBrowser profiles and exposes CDP at `/api/profiles/{profile_id}/cdp` (with optional Bearer auth). Skyvern automates browsers via Playwright + Vision LLMs and already supports `browser_address` / `connect_to_browser_over_cdp`. Combining them means Skyvern never launches vanilla Chromium; it attaches to the cloaked session (fingerprint + per-profile proxy).

Skyvern is licensed AGPL-3.0. This Manager GUI is MIT. We therefore treat Skyvern as an **optional runtime dependency** behind a thin MIT adapter — we do not vendor AGPL sources into the tree.

## Goals / Non-Goals

**Goals:**
- First-class harness adapter: resolve CDP URL + auth headers for a profile, connect Skyvern, run tasks through Cloak.
- API surface for capabilities / run / status without breaking existing Manager routes.
- Honest degradation when Skyvern or LLM is unavailable (`status=unavailable|blocked`).
- Tests for CDP URL construction, auth header wiring, and unavailable-path behavior.
- Proof runner that launches a real Cloak profile and drives it through the Skyvern harness path.

**Non-Goals:**
- Vendoring the full Skyvern monorepo into this repository.
- Replacing noVNC / Codex computer-use host harness.
- Shipping Skyvern UI or workflow builder inside the Manager React app in this change.
- Guaranteeing Cloudflare bypass claims beyond what CloakBrowser already provides.

## Decisions

1. **CDP docking over process embedding**  
   Skyvern connects with `browser_address=http://<manager>/api/profiles/<id>/cdp` (and Bearer headers when auth is on). Alternative considered: launching CloakBrowser binary from Skyvern directly — rejected because it bypasses Manager profile/proxy/lifecycle.

2. **Optional import, not hard dependency**  
   `import skyvern` is attempted at harness construction. Manager boots without Skyvern installed. Alternative: pin `skyvern` in `requirements.txt` — rejected to keep core image lean and make AGPL opt-in.

3. **No AGPL source copy**  
   Adapter calls the published Skyvern Python API (`Skyvern.connect_to_browser_over_cdp`, `run_task(..., browser_address=...)`). Capability parity is via that API, not a forked copy.

4. **Harness API under `/api/harnesses/skyvern`**  
   Keeps separation from profile CRUD and from the frontend task-harness bridge.

## Risks / Trade-offs

- [AGPL network-copyleft] → Document clearly; operators who enable Skyvern must comply with AGPL for the combined service.
- [Heavy Skyvern deps / Docker] → Optional install; proof runner reports BLOCKED with evidence if install/LLM fails.
- [LLM required for agent loop] → CDP connect + deterministic navigate/screenshot still proves cloak+Skyvern path; full `run_task` needs OpenAI-compatible credentials.
- [Auth on CDP WebSocket] → Pass Manager Bearer token via CDP connect headers Skyvern already supports.

## Migration Plan

1. Deploy Manager with new harness modules (no behavior change until Skyvern installed).
2. `pip install skyvern` (or extra) in environments that want the harness.
3. Configure LLM env vars for agent runs.
4. Rollback: remove harness routes/modules; profiles/CDP unchanged.

## Open Questions

- Whether to add a Docker Compose sidecar for full Skyvern server mode later (out of scope for this change).
