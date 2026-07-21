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
- **Mobile task workspace** — browser-first live-VNC split with collapsed chat, central tools, fullscreen shortcuts, editable viewport, visual zoom and an injected Codex Computer Use host bridge
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

The new pinned r51 Selkies lane is now independently reproducible. In the fresh five-run loopback check, its HTTP shell returned **5/5** successful samples with **2.672 ms median total time** (p95 **3.515 ms**), and `/websockets` returned **5/5** valid upgrades with **2.190 ms median handshake** (p95 **3.722 ms**). This proves transport readiness only: it is not a first-frame, FPS, touch-to-pixel, authenticated product-flow, or mobile-Safari comparison. KasmVNC/noVNC remains the production recommendation because it is still the only candidate integrated and verified through the complete profile, policy, VNC-canvas and mobile-UI path. Full context and limits are in [docs/REMOTE-STREAMING-BENCHMARK.md](docs/REMOTE-STREAMING-BENCHMARK.md).

The current r55 mobile implementation is browser-first: chat starts collapsed, the live browser consumes all unused space, benchmark controls are not shown in the UI, and only Full, Tools, Chat and Send remain persistent. Tools use progressive disclosure for View, Sessions and Admin. The bookmark-like Actions row is not a list of URLs; it sends typed Capture, Copy and Paste commands through a host bridge explicitly identifying itself as `codex-computer-use`. Unknown command kinds are dropped at the bridge boundary, while a missing, generic or mislabeled harness fails closed. The fresh live r55 gate passed **276/276 checks** across five mobile viewports and produced **22 screenshots**. The earlier authenticated r51 run additionally covered the access dashboard: Codex Computer Use logged in as a scoped viewer, reached a real connected VNC canvas, confirmed exactly four persistent actions and found no horizontal overflow at `390 x 844`; an administrator then combined `operate` with independent CDP `automate` access for a Paperclip agent. The VCVM/Neko Tailnet check remains transport evidence only: Codex Computer Use completed the protected login and observed `/ws`, but WebRTC ICE failed, so no honest FPS value was recorded. Full details and limits are in [docs/MOBILE-STREAMING-AUTH-LATENCY-AUDIT-2026-07-21.md](docs/MOBILE-STREAMING-AUTH-LATENCY-AUDIT-2026-07-21.md).

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
2. Create named people with a password and per-sandbox grants.
3. Create a Paperclip agent identity, choose its inherited browser-control tier, independently enable CDP automation when needed, and copy its generated bearer key once into the agent's secret store.
4. Rotate or deactivate a person or agent immediately when access changes.

The access-control implementation and backend test matrix cover profile discovery, VNC, clipboard, launch/stop, CDP HTTP and CDP WebSockets. The r51 dashboard can persist two complementary grants on one sandbox, such as `operate + automate`, and its effective-access disclosure lists the profiles and resulting capabilities before a key is used. The authenticated r51 acceptance run proved that such an agent saw only its `beta` sandbox, reached its CDP endpoint with HTTP 200, passed lifecycle policy to the already-running response, and received HTTP 404 for `alpha`. Denied REST and WebSocket policy decisions are recorded as metadata-only audit events without credentials or browser content. The full suite currently passes **223 backend tests** and **75 frontend tests**. A profile outside a caller's scope still returns the same `404` response as a missing profile. The dashboard is a convenience layer; it is not the security boundary.

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
