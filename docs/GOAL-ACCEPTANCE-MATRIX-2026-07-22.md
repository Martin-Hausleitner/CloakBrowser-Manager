# Goal acceptance matrix

Stand: 23. Juli 2026

This document keeps the full mobile browser-manager objective intact and separates proven behavior from partial or missing work. A green narrow test is not used as evidence for a broader requirement. Future developers must start with the repo-local [continuation skill](../.agents/skills/cloakbrowser-manager-development/SKILL.md) and keep this matrix synchronized with fresh evidence.

## Mobile design checkpoint

- Platform: iPhone and iPad Safari, with Chromium device emulation as the automated regression surface.
- Framework: React 19 web UI served by FastAPI; the remote browser runs on the VCVM.
- Audience: private enterprise administrators, scoped operators and automation agents.
- Offline behavior: a live VNC session cannot work offline. The UI must instead keep explicit connection, reconnect and recovery states.
- Interaction rules: visually compact controls may use 24-32 px icons, but every actionable hit area remains at least 44 px; only one detail panel is open at a time; the live browser stays the dominant surface.

The inherent network and streaming dependency makes this a high-risk mobile surface. Release evidence therefore requires real VCVM streaming, keyboard-resize geometry, access enforcement and measured remote-path latency rather than component tests alone.

## Requirement status

| Requirement | Status | Current evidence | Evidence still required |
|---|---|---|---|
| Fullscreen keeps View, Viewport, Phone fit and Sessions reachable | Proven | `MobileSplitScreen.tsx`, component tests and the 318-check VCVM mobile report | Physical iPhone Safari remains separate release evidence |
| Phone fit persists the real VCVM framebuffer and restores it after the gate | Proven | `App.applyProfileViewport`, live gate apply/restore checks | Runtime mobile identity consistency is still required |
| Compact browser-first mobile UI and keyboard-safe composer | Proven on automated VCVM surface | Five viewport runs, 31 screenshots, independent visual PASS | Physical iPhone keyboard and private HTTPS proof |
| Existing profiles, colored tags and scoped access groups | Proven | Profile/tag persistence, groups CRUD/effective grants, authenticated E2E | None for the current MVP |
| Persistent profile pinning, projects and collapsible folders | Proven on automated VCVM surface; bulk move implemented locally | Additive SQLite migration, deterministic desktop/mobile organization, search/filter, and admin bulk-organize API/UI with tests | Refresh-stable live VCVM E2E after redeploy |
| Harness choice for Codex, Antigravity, Claude Code, OpenCode and Browser Use clients | Live-proven on VCVM desktop shell | Preference persists, Browser-Use home exposes project + harness selectors, execution remains fail-closed behind the verified Codex Computer Use bridge, and full local suites pass | Fresh live VCVM round-trip for each non-Codex label remains metadata-only |
| Proxy reachability, outbound IP, fingerprint consistency and BrowserScan score | Live-proven on VCVM | No-proxy path earlier; credentialed disposable profile after redeploy produced `proxychecker: measured`, risk/authenticity scores, masked IP, redacted API payload | Optional higher-quality residential proxy sample remains nice-to-have |
| Proxy inventory, overview UI, checker and auto geo-aligned profiles | Live-proven on VCVM | Admin ingest of 11 entries, redacted overview, Proxy-Checker check scores, auto profile under `proxied/auto` with geoip/timezone/locale defaults; credentials never returned | Batch-check remaining inventory entries; optional country enrichment when providers return codes |
| Read-only extension inventory managed from CLI | Implemented; full local suites passed | Safe manifest parser, trust/error state calculation, GET `/api/profiles/{profile_id}/extensions` endpoint, CLI tool `scripts/inspect_extensions.py`, and unit test suites (9 new tests passed; 351 total backend tests) | None for MVP extension inventory |
| External agent control plane (API/CLI/skill; not UI-only) | Live-proven on VCVM | Operate-scoped create/update/delete/launch/stop; `scripts/cbm_agent_ctl.py`; skill `references/agent-control.md`; agent-key E2E create→launch→open-links(CDP/VNC)→health→stop→delete without UI; finance sandbox create denied 403 | Commit/push of harness SHA still pending if requested |
| Live latency and developer diagnostics | Implemented; full local suites passed | Admin-only `GET /api/admin/live-diagnostics` with launch/VNC counters, measured-or-unavailable timings, redaction tests and non-admin 403 | Fresh VCVM live connection counters after deploy; still no encoded FPS or touch-to-pixel claim |
| Competitor research | Proven | `COMPETITOR-UI-FEATURE-MATRIX-2026-07-22.md` | Keep facts and design inferences separated |
| End-to-end mobile release | Proven on automated VCVM Chromium surface | Current authenticated gate passed 316 checks across five viewports plus access dashboard, produced 31 screenshots, and the redacted release acceptance gate passed | Safari Remote Automation, physical iPhone and private Tailnet HTTPS remain external/device proof |

## Fresh verification snapshot

- Complete backend suite: **371 passed**.
- Complete frontend suite: **133 passed**.
- Complete script suite: **26 passed**, including Python 3.11 release-gate compilation.
- Frontend production build: **passed**.
- Redacted release acceptance gate: **passed** (prior mobile pack); Browser-Use/Proxies VCVM UI re-verified with headless Chromium screenshots on 23 July evening.
- VCVM deployment: **healthy and protected**, with the optional local proxychecker reachable from the Manager container.
- Proxy inventory: **11** entries ingested on VCVM; credentials masked in API/UI; one live Proxy-Checker sample recorded as redacted scores.
- Authenticated VCVM Chromium gate: **316 checks passed**, **31 screenshots**, five mobile/tablet viewports plus access dashboard.
- Live profile health: first-launch persistence, manual rerun, masked outbound IP, 100/100 fingerprint consistency and 100/100 BrowserScan passed.
- Safari/WebKit gate: **blocked externally** because Safari Remote Automation is disabled; no WebKit or physical-iPhone claim is made.

## Delivery order and stop conditions

1. **Proven on automated VCVM surface:** additive profile organization, compact grouped UI and current mobile release ladder.
2. **Implemented; one live input still required:** redacted profile health and BrowserScan are live; credentialed proxychecker scoring still needs an authorized configured proxy.
3. **Proven locally:** read-only extension inventory plus admin-only live diagnostics.
4. **Live-proven:** credentialed proxychecker enrichment via Manager health path.
5. **Next:** release handoff evidence pack.
6. **External/device checkpoint:** Safari Remote Automation, physical iPhone and private Tailnet HTTPS/direct-route evidence.

The goal is accepted only when every row above is proven or an external-only proof is explicitly completed. A blocked Safari or physical-device row cannot be relabeled as a pass.
