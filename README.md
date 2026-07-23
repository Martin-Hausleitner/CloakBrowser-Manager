<p align="center">
<img src="https://i.imgur.com/cqkp6fG.png" width="500" alt="CloakBrowser">
</p>

<h3 align="center">Browser Profile Manager for CloakBrowser</h3>

<p align="center">
Create, manage, and launch isolated browser profiles with unique fingerprints.<br>
Free, self-hosted alternative to Multilogin, GoLogin, and AdsPower.
</p>

<p align="center">
<a href="https://github.com/CloakHQ/CloakBrowser"><img src="https://img.shields.io/github/stars/cloakhq/cloakbrowser?label=CloakBrowser" alt="Stars"></a>
<a href="https://hub.docker.com/r/cloakhq/cloakbrowser-manager"><img src="https://img.shields.io/docker/pulls/cloakhq/cloakbrowser-manager?label=docker&logo=docker&logoColor=white" alt="Docker Pulls"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License"></a>
</p>

---

<p align="center">
<img src="https://i.imgur.com/twdX81Q.png" width="800" alt="CloakBrowser Manager — Browser View">
<br>
<img src="https://i.imgur.com/XFYn1qY.png" width="800" alt="CloakBrowser Manager — Profile Settings">
</p>

Each profile is an isolated CloakBrowser instance with its own fingerprint, proxy, cookies, and session data. Profiles persist across restarts. Everything runs in one Docker container.

```bash
docker run -p 8080:8080 -v cloakprofiles:/data cloakhq/cloakbrowser-manager
```

Or build from source:

```bash
git clone https://github.com/CloakHQ/CloakBrowser-Manager.git
cd CloakBrowser-Manager
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080) in your browser. Create a profile. Click Launch. Done.

> **Early alpha** — this project is under active development. Expect bugs. If you find one, please [open an issue](https://github.com/CloakHQ/CloakBrowser-Manager/issues).

## Fork development status

Status date: **23 July 2026**. This section describes the active development branch `integrate-pr-47-27-26` in Martin Hausleitner's fork. It is intentionally stricter than the upstream feature list: a feature is not called complete merely because its component tests pass.

### Repository boundaries

| Role | Exact repository | Write policy |
| --- | --- | --- |
| Active product fork | [Martin-Hausleitner/CloakBrowser-Manager](https://github.com/Martin-Hausleitner/CloakBrowser-Manager) | Development branch and documentation are pushed here. |
| Read-only upstream | [CloakHQ/CloakBrowser-Manager](https://github.com/CloakHQ/CloakBrowser-Manager) | Used for upstream comparison and future rebases; no push from this workflow. |
| Browser engine | [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) | External runtime dependency; not modified by Manager changes. |
| Explicitly out of scope | [fintaro-ai/Fintaro-Agent](https://github.com/fintaro-ai/Fintaro-Agent) | Never receive CloakBrowser code, reports, commits, or generated artifacts. |

The repo-local continuation workflow for the next developer is [`.agents/skills/cloakbrowser-manager-development/SKILL.md`](.agents/skills/cloakbrowser-manager-development/SKILL.md). The authoritative row-by-row status is [docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md](docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md).

### What is complete

- **Selected upstream changes are integrated** — PR #47 constrains the browser window to the VNC framebuffer, PR #27 makes the documented local backend start work, and PR #26 adds a per-profile search engine.
- **Read-only extension inventory and agent CLI** — safe extension manifest parser, trust/error state calculation (`valid`, `untrusted_manifest`, `missing_manifest`, `invalid_path`), GET `/api/profiles/{profile_id}/extensions` endpoint, and operator CLI `scripts/inspect_extensions.py`.
- **Compact mobile VNC workspace** — browser-first portrait/landscape layout, fullscreen preview, session grid/switcher, Fit/Width/Height/Phone modes, live viewer zoom, persisted framebuffer viewport changes, collapsible chat, typed Capture/Copy/Paste actions, and `visualViewport` keyboard adaptation.
- **Scoped access foundation** — administrator bootstrap, people and agent identities, access groups, sandbox-scoped grants, separate view/interact/operate/automate capabilities, revocation, audit metadata, VNC input filtering, and scoped REST/CDP/VNC enforcement.
- **Profile organization and preferred harness metadata** — persistent projects, nested folders, pins, color accents, deterministic ordering, compact desktop/mobile presentation, and saved preferences for Codex, Antigravity, Claude Code, OpenCode, and Browser Use.
- **Redacted profile health foundation** — asynchronous first-launch checks, masked outbound IP, saved-versus-runtime fingerprint consistency, conservative BrowserScan authenticity, optional VCVM-local proxychecker enrichment, access-controlled reruns, and a compact desktop disclosure that does not add mobile clutter.
- **Fail-closed execution boundary** — a saved harness preference never grants execution. Browser-visible host actions still require a capability-verified `codex-computer-use` bridge; server task history remains persistence only.
- **Streaming and competitor research** — reproducible redacted benchmark tooling, a VCVM/Tailscale latency audit, a Safari/WebKit gate that reports missing prerequisites honestly, and an official-source competitor feature matrix.

### Current verified state

| Area | State | Fresh or historical evidence |
| --- | --- | --- |
| Profile schema, migration, API, organization, access, health & extension inventory | Implemented; full local suite passed | **359/359 backend tests passed** on 23 July 2026 (includes 8 live-diagnostics tests). |
| Desktop/mobile organization, form, access dashboard, harness boundary and compact health UI | Implemented; full local suite passed | **132/132 frontend tests passed** and the production build passed on 22 July 2026. |
| Release, mobile, streaming and deployment scripts | Full local script suite passed | **26/26 script tests passed**, including an explicit Python 3.11 compilation regression check. |
| Compact mobile workspace and scoped live browser control | Proven on the current automated VCVM Chromium surface | The authenticated release run passed **316 checks** across five viewports plus the access dashboard and captured **31 screenshots**. |
| Browser-path profile health | Proven on a live no-proxy VCVM profile | First-launch scheduling, manual rerun, refresh persistence, masked outbound IP, **100/100 fingerprint consistency**, **100/100 BrowserScan authenticity**, and a redacted desktop panel passed. The Manager container also reached the separately bound VCVM-local proxychecker health endpoint. |
| Admin-only live diagnostics | Implemented; full local suite passed | `GET /api/admin/live-diagnostics` returns launch/VNC counters with measured-or-unavailable metrics, strips ports/paths/URLs/proxy/secrets, rejects non-admin callers with HTTP 403, and leaves the mobile workspace unchanged. |
| Credentialed proxychecker enrichment | Implemented; live proxy sample still required | Deployment, allow-list, normalization, failure, redaction and authorization tests pass; the live E2E profile had no configured proxy, so the proxychecker source correctly reported `Skipped`. |
| Physical iPhone Safari and private Tailnet HTTPS | Not yet proven | Chromium emulation is not relabeled as Safari evidence; Safari Remote Automation and a physical-device run remain external prerequisites. |
| Direct Tailnet latency | Not achieved in the last measurement | The recorded Mac route used `DERP(nue)`; direct-path proof must be rerun rather than inferred. |

### What is being worked on next

| Priority | Feature | Required completion evidence |
| --- | --- | --- |
| P0 | Credentialed proxy health acceptance | Run the optional VCVM-local proxychecker against an authorized configured proxy without exposing credentials; prove normalized risk/authenticity, failure behavior and source state. |
| P0 | Finish the release handoff | Push only the fork branch, verify its SHA and GitHub files, then retain the green release report and screenshot paths as local evidence. |
| P1 | Direct Tailnet route and real iPhone Safari acceptance | Private HTTPS, physical keyboard behavior, touch interaction, direct-versus-DERP route evidence, and honest latency definitions. |
| P1 | Profile organization refinements | Search/filter, explicit project/folder management, safe bulk movement, and refresh-stable live E2E after the MVP metadata model is proven. |
| P2 | Operational polish | Extension templates, health history, recordings/metrics, reusable browser templates, and bulk/API/CLI flows after the security and release gates are stable. |

### Development timeline

- **23 July 2026** — admin-only live diagnostics landed: launch/VNC counters, honest unavailable metrics, redaction tests, and `GET /api/admin/live-diagnostics` without mobile UI clutter.
- **20 July 2026** — mobile VNC workspace, iOS-safe paste, initial E2E gates, scoped Paperclip access, protected mobile login, and private Tailnet fail-closed helper landed.
- **21 July 2026** — mobile controls were progressively simplified; short-iPhone, keyboard, access-dashboard, Safari/WebKit, Codex Computer Use, live viewport, VCVM, streaming, and policy gates were added or hardened.
- **22 July 2026** — compact mobile/access-group work was consolidated; profile project/folder/pin/color/harness metadata, deterministic desktop/mobile organization, and redacted access context were implemented and scoped-tested. A redacted profile-health service, persistence, access-controlled API, desktop disclosure and optional VCVM-local proxychecker boundary were implemented and deployed. PR #47 (VNC window bounds), PR #27 (local backend startup), PR #26 (search engine selection), and a read-only extension inventory with agent CLI (`scripts/inspect_extensions.py`) were integrated and verified across 351 backend tests, 132 frontend tests, 26 script tests, and production build.

Historical benchmark numbers below remain useful baselines, but they are not a substitute for the fresh release checkpoint above.

## Why Not Just Use a VPN?

A VPN only changes your IP. Incognito only clears cookies. Chrome profiles share the same hardware fingerprint underneath. Platforms use 50+ signals to link your accounts — canvas, WebGL, audio, GPU, fonts, screen size, timezone.

Each CloakBrowser profile generates a completely different device identity. To the website, each profile looks like a different computer.

| Solution | What it changes | Accounts linked? |
|----------|----------------|-----------------|
| VPN | IP address only | Yes — same fingerprint |
| Incognito | Clears cookies | Yes — same fingerprint |
| Chrome profiles | Separate bookmarks/cookies | Yes — same hardware fingerprint |
| **CloakBrowser** | **Everything — full device identity per profile** | **No** |

## Features

- **Profile management** — create, edit, delete browser profiles with unique fingerprints
- **Per-profile settings** — fingerprint seed, proxy, timezone, locale, user agent, screen size, platform
- **One-click launch/stop** — each profile runs as an isolated CloakBrowser instance
- **Session persistence** — cookies, localStorage, and cache survive browser restarts
- **In-browser viewing** — interact with launched browsers via noVNC, directly in the web GUI
- **Mobile task workspace** — aspect-fitted live VNC with collapsed chat, visually compact icon controls backed by touch-safe hit areas, Visual Viewport keyboard adaptation, one compact tool sheet, typed Quick actions, fullscreen session switching, Fit/Width/Height/Phone modes, live pane/zoom controls, restart-applied profile viewports and a capability-checked Codex Computer Use host contract
- **Playwright/Puppeteer API** — connect to any running profile programmatically via CDP, while still watching it live in the browser
- **Optional scoped access** — protect the web UI with a bootstrap token, then give people and Paperclip agents only the browser sandboxes they need
- **Powered by CloakBrowser** — 32 source-level C++ patches, passes Cloudflare Turnstile, 0.9 reCAPTCHA v3 score

## Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React + Tailwind CSS
- **Browser viewer**: noVNC (WebSocket-based VNC client)
- **Database**: SQLite
- **Browser engine**: [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (stealth Chromium binary)

## Streaming Diagnostics

Streaming stack comparisons and Tailnet checks are documented in [docs/REMOTE-STREAMING-BENCHMARK.md](docs/REMOTE-STREAMING-BENCHMARK.md). New runs should use the headless diagnostic runner instead of editing numbers by hand:

```bash
python3 scripts/streaming_benchmark_runner.py \
  --config scripts/streaming_benchmark_example.json \
  --output-dir artifacts/streaming-benchmark/$(date -u +%Y%m%dT%H%M%SZ) \
  --iterations 5 \
  --latest-json "${BENCHMARK_REPORT_PATH:-/data/benchmark-report.json}" \
  --latest-markdown docs/streaming-benchmark-latest.md
```

For the pinned, loopback-only Selkies/Chromium comparison lane:

```bash
./scripts/run_selkies_benchmark.sh artifacts/selkies-benchmark/local
```

The runner writes JSON plus Markdown reports and separates `measured` candidates from `not_installed` or `architecture_only` entries. It deliberately omits local paths, endpoints, commands, raw process output and request headers from the public report. Benchmarks are offline diagnostics and are not part of the compact mobile UI. Pair them with `scripts/mobile_ui_gate.py` when a candidate also needs proof that the real mobile UI and live canvas still work. On macOS, `scripts/mobile_webkit_gate.py` adds a Safari/WebKit shell check after Remote Automation has been deliberately enabled in Safari Settings; it reports that prerequisite as blocked instead of weakening the result.

The [redacted 20-run report](docs/streaming-benchmark-latest.md) remains the warm r49 product-path baseline: the Manager health endpoint reached a median first byte at **1.438 ms** (p95 **4.005 ms**), and the real KasmVNC/noVNC WebSocket upgrade reached a median **3.457 ms** handshake (p95 **8.931 ms**). That historical report listed Selkies as `not_installed` and Sunshine/Moonlight plus Guacamole as `architecture_only`.

The fresh r55 comparison provisioned Selkies and Apache Guacamole beside the current product path. Twenty-run shell checks found a **0.756 ms** median Selkies WebSocket upgrade and **1.749 ms** median Guacamole HTTP total time. A separate five-run mobile browser observation measured the first non-black frame at **180 ms median** for the integrated KasmVNC/noVNC product path, **351 ms** for Guacamole and **5,272 ms** for Selkies after four reloads stalled near 5.3 seconds (its first run was 296 ms). These are directional local observations, not interchangeable transport metrics: only KasmVNC/noVNC includes the complete profile, policy, VNC-canvas and mobile-UI chain, and no FPS or WAN touch-to-pixel value is claimed. Full context and limits are in [docs/REMOTE-STREAMING-BENCHMARK.md](docs/REMOTE-STREAMING-BENCHMARK.md).

### Current VCVM-only acceptance run

The current deployment runs FastAPI, React, SQLite, CloakBrowser, Xvnc/KasmVNC and the profile data volume on the VCVM. The Mac is only a browser/test client through an SSH tunnel; the Manager itself stays bound to VCVM loopback. Thirty warm VCVM-local requests measured **0.514 ms median / 1.179 ms p95** for `/health` and **2.148 ms / 4.147 ms** for the authenticated profile API. Twenty real VNC proxy connections measured **6.689 ms / 9.989 ms** to WebSocket open and **19.558 ms / 26.837 ms** to the first RFB frame.

The current Mac-to-VCVM route is degraded by Tailscale relay: `tailscale ping` used `DERP(nue)` at **49–64 ms** and did not establish a direct path. Through the authenticated SSH/Tailscale path, 30 health requests measured **61.8 ms median / 125.6 ms p95**; 15 VNC connections measured **195.2 ms / 266.8 ms** to WebSocket open and **204.6 ms / 328.6 ms** to the first RFB frame. A five-second synthetic moving-page observation produced **6.96 visible canvas changes/s** on VCVM loopback and **4.18/s** through the Mac relay path. This is a visual-update proxy, not an encoded-video FPS or touch-to-pixel claim. The practical latency fix is a direct Tailnet path (shared IPv4 reachability or IPv6 on the Mac side), not more mobile controls.

The current mobile implementation is browser-first and compact: chat starts collapsed, the normal live pane fits the actual stream aspect instead of reserving empty letterbox space, benchmark controls are absent, and only Full, Tools, Chat and Send remain persistent. Icons and labels are visually small while their interaction areas remain touch-safe; text inputs stay at 16 px so iOS does not focus-zoom them. The root follows `visualViewport.height` while the software keyboard is open, removes nonessential chrome and keeps the VNC pane above the composer instead of letting either slide behind the keyboard. Opening a detail editor temporarily compresses the live pane so width, height and Apply stay reachable on short portrait screens. Fullscreen exposes a compact session switcher, distinct Fit, Width and Height modes, Phone fit, visual zoom and an editable viewport panel. Viewport Apply persists the new size and restarts a running VCVM browser so the changed framebuffer is actually used; pane and noVNC zoom remain immediate viewer-only controls.

Tools uses progressive disclosure for Quick actions, View, Sessions, Admin and account controls. Quick actions are not URL bookmarks; Capture, Copy and Paste are typed, host-scoped commands enabled only when a verified `codex-computer-use` bridge reports the matching capability. Unknown command kinds are dropped at the boundary, while a missing, generic or mislabeled harness fails closed. The browser gate injects a deterministic test bridge for this contract; it does not claim that the Codex CLI/IDE on the VCVM supplies a production Computer Use browser runtime. Server-backed task history is persistence only and does not execute an agent by itself.

The last published pre-organization baseline passed **261 backend tests**, **111 frontend tests** and **22 release/mobile gate tests**, plus the frontend production build and VCVM deployment-surface gate. Its VCVM-backed Chromium run passed **318/318 checks** with **31 screenshots** across iPhone 14, iPhone SE, iPhone Pro Max, iPhone landscape, touch tablet and the access dashboard. It explicitly emulated the open software keyboard, checked VNC/composer non-overlap, exercised the fullscreen session grid, applied Phone fit and restored the live profile to its original **665×1114** framebuffer. A separate group-only operator login saw exactly one assigned live profile, exposed no administration and received HTTP 403 for the group-management API. The Safari/WebKit gate records an explicit `blocked` result while Safari Remote Automation remains disabled; Chromium evidence is not relabeled as Safari evidence. The complete UI/UX rationale and ten next input improvements are in [docs/MOBILE-UI-UX-HARNESS-AUDIT-2026-07-21.md](docs/MOBILE-UI-UX-HARNESS-AUDIT-2026-07-21.md); the current official-source competitor matrix is in [docs/COMPETITOR-UI-FEATURE-MATRIX-2026-07-22.md](docs/COMPETITOR-UI-FEATURE-MATRIX-2026-07-22.md), and streaming, auth and Tailnet limits remain documented in [docs/MOBILE-STREAMING-AUTH-LATENCY-AUDIT-2026-07-21.md](docs/MOBILE-STREAMING-AUTH-LATENCY-AUDIT-2026-07-21.md).

## Development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

By default, Docker stores profile data in `/data`. For local development, if `/data` is not writable, the backend falls back to `backend/.data`. You can override this with `CLOAKBROWSER_MANAGER_DATA_DIR=/path/to/data`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker compose up --build
```

## Requirements

- Docker (20.10+)
- ~2 GB disk (image + binary)
- ~512 MB RAM per running profile

## Updating

Pull the latest image and restart:

```bash
docker pull cloakhq/cloakbrowser-manager
docker stop <container-id>
docker run -p 8080:8080 -v cloakprofiles:/data cloakhq/cloakbrowser-manager
```

Your profiles and session data are stored in the `cloakprofiles` volume and persist across updates.

## Automation API

Every running profile exposes a CDP (Chrome DevTools Protocol) endpoint. Connect Playwright or Puppeteer to automate a profile while watching it live in the browser.

```python
from playwright.async_api import async_playwright

async with async_playwright() as pw:
    browser = await pw.chromium.connect_over_cdp(
        "http://localhost:8080/api/profiles/<profile-id>/cdp"
    )
    page = browser.contexts[0].pages[0]
    await page.goto("https://example.com")
```

```javascript
const { chromium } = require("playwright");

const browser = await chromium.connectOverCDP(
  "http://localhost:8080/api/profiles/<profile-id>/cdp"
);
const page = browser.contexts()[0].pages()[0];
await page.goto("https://example.com");
```

The CDP URL is available in the toolbar (code icon) when a profile is running. The same browser session is accessible both visually through VNC and programmatically through the API.

## Remote Access

The container binds to localhost only. To access from a remote server:

```bash
ssh -L 8080:localhost:8080 your-server
```

Then open `http://localhost:8080`.

## Authentication

By default, there is no authentication (ideal for local use). To protect the web UI and API when hosting on a network, set the `AUTH_TOKEN` environment variable:

```bash
docker run -p 8080:8080 -v cloakprofiles:/data -e AUTH_TOKEN=your-secret-token cloakhq/cloakbrowser-manager
```

Or in `docker-compose.yml`:

```yaml
environment:
  - AUTH_TOKEN=your-secret-token
```

When `AUTH_TOKEN` is set:

- The web UI shows a login page. Enter the token to unlock.
- API consumers pass the token via `Authorization: Bearer <token>` header.
- VNC WebSocket connections are authenticated via the login cookie.
- Docker uses the minimal unauthenticated `/health` liveness endpoint. `/api/status` contains runtime counts and remains authenticated.

> **Note**: The auth token is transmitted in cleartext over HTTP. If you expose the Manager to the internet, put it behind a reverse proxy with HTTPS (Caddy, nginx, Traefik).

### Scoped people and Paperclip-agent access

For a shared private deployment, opt into server-enforced sandbox policies in addition to the bootstrap token:

```yaml
environment:
  - AUTH_TOKEN=use-a-long-random-bootstrap-secret
  - ACCESS_CONTROL_ENABLED=1
```

`AUTH_TOKEN` remains the emergency administrator credential and signing secret. Do not give it to normal users or Paperclip agents. With `ACCESS_CONTROL_ENABLED=1`, sign in with that token and open **Browser access controls** in the dashboard to:

1. Assign every profile an `Access sandbox` (for example `research` or `finance`).
2. Create named people with a password and either direct per-sandbox grants or membership in reusable access groups.
3. Create a Paperclip agent identity, choose its inherited browser-control tier, independently enable CDP automation when needed, and copy its generated bearer key once into the agent's secret store.
4. Create groups of people, assign sandbox grants once, and review each person's effective union of direct and group access.
5. Rotate or deactivate a person or agent immediately when access changes.

The access-control implementation and backend test matrix cover profile discovery, VNC, clipboard, launch/stop, profile-health reads/reruns, CDP HTTP and CDP WebSockets. Direct grants and active group grants are combined server-side into one effective policy; agents remain direct identities and are not group members. The dashboard can persist two complementary grants on one sandbox, such as `operate + automate`, and its effective-access disclosure lists the profiles and resulting capabilities before a key is used. The authenticated acceptance runs proved both a scoped agent and a group-only person saw only their assigned sandbox; the person received HTTP 403 for group administration, while the agent reached scoped CDP and received HTTP 404 for a profile outside its scope. Active VNC/CDP leases are revoked as soon as a user, group membership, group grant, agent, key or direct grant changes. Viewer-only RFB filtering is stateful, restricts negotiated encodings and discards pointer, keyboard and clipboard input before KasmVNC. Denied REST and WebSocket policy decisions are recorded as metadata-only audit events without credentials or browser content. The current local full suites pass **343 backend tests** and **132 frontend tests**, plus the production build; live VCVM evidence is tracked separately above. A profile outside a caller's scope still returns the same `404` response as a missing profile. The dashboard is a convenience layer; it is not the security boundary.

| Grant | What it allows |
| --- | --- |
| `view` | Discover the assigned profile and see its VNC stream in read-only mode. |
| `interact` | `view` plus VNC keyboard/mouse and clipboard input. |
| `operate` | `interact` plus launch and stop. |
| `automate` | `view` plus scoped CDP automation. It does not imply manual VNC input or lifecycle control. |

An administrator is unrestricted. A Paperclip agent uses its own opaque key with `Authorization: Bearer <agent-key>`; clients that connect directly to a CDP WebSocket must attach the same header to the WebSocket upgrade. Agent keys are stored only as hashes and are shown in the dashboard once at creation or rotation.

The design, enforcement matrix and the latest isolated browser E2E evidence are in [docs/PAPERCLIP-BROWSER-ACCESS-CONTROL-PROPOSAL.md](docs/PAPERCLIP-BROWSER-ACCESS-CONTROL-PROPOSAL.md). The maintainer-facing discussion draft remains deliberately credential-free in [docs/drafts/PAPERCLIP-BROWSER-ACCESS-GITHUB-DISCUSSION.md](docs/drafts/PAPERCLIP-BROWSER-ACCESS-GITHUB-DISCUSSION.md).

Existing installations stay on the previous single-token behavior until `ACCESS_CONTROL_ENABLED=1` is explicitly set. The local SQLite migration runs automatically, and existing profiles start in the `default` sandbox.

### Private Tailscale HTTPS for iPhone access

Do not bind an unauthenticated Manager to a network interface. Once the Manager is already running on loopback with `AUTH_TOKEN` and `ACCESS_CONTROL_ENABLED=1`, use the guarded helper to publish it **only within the tailnet**:

```bash
# Checks the target without changing Tailscale configuration.
./scripts/serve_private_tailnet.sh --check http://127.0.0.1:8080

# Configures private Tailscale Serve HTTPS, never Funnel.
./scripts/serve_private_tailnet.sh --apply http://127.0.0.1:8080
```

The helper refuses non-loopback, open, and legacy single-token targets. Tailscale Serve also needs HTTPS/Serve enabled by the tailnet administrator; if that policy is disabled, the command safely fails before publishing any URL. After a successful run, use `tailscale serve status` to obtain the private `https://<machine>.<tailnet>.ts.net` URL. Disable the proxy when it is no longer needed with `tailscale serve off`.

## License

- **This application** (GUI source code) — MIT. See [LICENSE](LICENSE).
- **CloakBrowser binary** (compiled Chromium) — free to use, no redistribution. See [BINARY-LICENSE.md](BINARY-LICENSE.md).

The GUI application requires the CloakBrowser Chromium binary to function. The binary is automatically downloaded on first launch and is governed by its own license terms. If you fork or redistribute this application, your users must comply with the [CloakBrowser Binary License](BINARY-LICENSE.md).

## Contributing

Contributions are welcome. Please [open an issue](https://github.com/CloakHQ/CloakBrowser-Manager/issues) first to discuss what you'd like to change.

## Links

- **CloakBrowser** — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser)
- **Website** — [cloakbrowser.dev](https://cloakbrowser.dev)
- **Bug reports** — [GitHub Issues](https://github.com/CloakHQ/CloakBrowser-Manager/issues)
- **Contact** — cloakhq@pm.me
