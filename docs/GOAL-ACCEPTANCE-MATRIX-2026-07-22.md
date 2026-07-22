# Goal acceptance matrix

Stand: 22. Juli 2026

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
| Persistent profile pinning, projects and collapsible folders | Implemented; full local suites passed | Additive SQLite migration, model/API persistence, deterministic desktop/mobile organization, form/access summaries; 286 backend and 126 frontend tests plus production build pass | Fresh authenticated VCVM refresh-stable E2E and screenshots |
| Harness choice for Codex, Antigravity, Claude Code, OpenCode and Browser Use clients | Implemented; full local suites passed | Preference persists, capability state and task metadata are visible, execution remains fail-closed behind the verified Codex Computer Use bridge, and full local suites pass | Fresh live VCVM round-trip for each label/state; non-Codex preferences remain metadata, not execution providers |
| Proxy reachability, outbound IP and fingerprint consistency score | Missing | Proxy syntax validation and fingerprint launch arguments only | Runtime probe, stored redacted result, automatic first-launch scheduling and failure cases |
| Read-only extension inventory managed from CLI | Missing | Raw `--load-extension` launch arguments only | Manifest parsing, trust/error state, icon/source endpoint, agent CLI and no mobile install control |
| Live latency and developer diagnostics | Partial | Offline benchmark reports and VNC logs | Admin-only live launch/VNC counters, honest unavailable metrics and redaction tests |
| Competitor research | Proven | `COMPETITOR-UI-FEATURE-MATRIX-2026-07-22.md` | Keep facts and design inferences separated |
| End-to-end mobile release | Partial | Prior authenticated Chromium/VCVM gate passed 318 checks with 31 screenshots | Rerun after profile-organization changes; Safari Remote Automation and physical Tailnet HTTPS are not yet proven |

## Fresh verification snapshot

- Complete backend suite: **286 passed**.
- Complete frontend suite: **126 passed**.
- Complete script suite: **26 passed**, including Python 3.11 release-gate compilation.
- Frontend production build: **passed**.
- Release-gate, redeploy and browser evidence: **not rerun after the latest slice yet**.
- Prior automated VCVM Chromium evidence remains historical until that rerun is complete.

## Delivery order and stop conditions

1. **Implemented; live rerun pending:** additive profile organization and harness metadata, with schema migration and grouped compact UI.
2. **Next:** read-only extension inventory plus agent-facing CLI attachment flow.
3. **Next:** stored profile health probe for proxy/IP/runtime fingerprint consistency, scheduled after first successful launch.
4. **Next:** admin-only live diagnostics that do not execute benchmarks or expose secrets.
5. **Release checkpoint:** full unit, integration, build, release-gate, VCVM deploy and authenticated mobile E2E verification.

The goal is accepted only when every row above is proven or an external-only proof is explicitly completed. A blocked Safari or physical-device row cannot be relabeled as a pass.
