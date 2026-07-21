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
- **Mobile task workspace** — compact live-VNC split with editable viewport, ratio and visual zoom; fullscreen, grid, task history, attachments and a touch-sized composer
- **Playwright/Puppeteer API** — connect to any running profile programmatically via CDP, while still watching it live in the browser
- **Optional scoped access** — protect the web UI with a bootstrap token, then give people and Paperclip agents only the browser sandboxes they need
- **Powered by CloakBrowser** — 32 source-level C++ patches, passes Cloudflare Turnstile, 0.9 reCAPTCHA v3 score

## Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React + Tailwind CSS
- **Browser viewer**: noVNC (WebSocket-based VNC client)
- **Database**: SQLite
- **Browser engine**: [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (stealth Chromium binary)

## Streaming Benchmarks

Streaming stack comparisons are documented in [docs/REMOTE-STREAMING-BENCHMARK.md](docs/REMOTE-STREAMING-BENCHMARK.md). New local runs should use the reproducible runner instead of editing benchmark numbers by hand:

```bash
python3 scripts/streaming_benchmark_runner.py \
  --config scripts/streaming_benchmark_example.json \
  --output-dir artifacts/streaming-benchmark/$(date -u +%Y%m%dT%H%M%SZ) \
  --iterations 5 \
  --latest-json "${BENCHMARK_REPORT_PATH:-/data/benchmark-report.json}" \
  --latest-markdown docs/streaming-benchmark-latest.md
```

The administrator-only **Streaming benchmarks** view reads the configured report through `/api/benchmarks/latest`; set `BENCHMARK_REPORT_PATH` on the manager if its persistent report file is not `/data/benchmark-report.json`. The runner emits JSONL progress events for a browser UI, writes JSON plus Markdown reports, and separates `measured` candidates from `not_installed` or `architecture_only` entries. Its browser-facing output deliberately omits local paths, endpoints, commands, raw process output, and request headers. Pair it with `scripts/mobile_ui_gate.py` when a candidate also needs proof that the real mobile UI and live canvas still work. On macOS, `scripts/mobile_webkit_gate.py` adds a Safari/WebKit shell check after Remote Automation has been deliberately enabled in Safari Settings; it reports that prerequisite as blocked instead of weakening the result.

The current [redacted five-run report](docs/streaming-benchmark-latest.md) is a fresh warm loopback recheck against an isolated live-VNC preview: the Manager health endpoint reached a median first byte at **2.233 ms**, and the real KasmVNC/noVNC WebSocket upgrade reached a median **4.541 ms** handshake. Selkies was explicitly `not_installed`; Sunshine/Moonlight and Guacamole were explicitly `architecture_only`. These are loopback regression probes for the existing path, not a cross-technology winner claim; the full context and limits are in [docs/REMOTE-STREAMING-BENCHMARK.md](docs/REMOTE-STREAMING-BENCHMARK.md).

The current compact mobile split has separately passed the live VNC gate across iPhone 14 portrait, iPhone SE portrait, iPhone Pro Max portrait, iPhone 14 landscape, and a touch tablet: **195/195 checks** passed with one connected canvas, no horizontal overflow, touch/keyboard input, fullscreen, grid, ratio/zoom adjustment, a deterministic manual iOS-paste fallback, and a visible task chat/composer. The vision states include the empty workspace and the editable Pro Max viewport; short portrait devices start at a compact 44% live pane while retaining direct ratio control. The evidence and remaining physical-iPhone/Safari limits are in [docs/MOBILE-E2E-VALIDATION.md](docs/MOBILE-E2E-VALIDATION.md).

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
3. Create a Paperclip agent identity and copy its generated bearer key once into the agent's secret store.
4. Rotate or deactivate a person or agent immediately when access changes.

The server enforces every grant for profile discovery, VNC, clipboard, launch/stop, CDP HTTP and CDP WebSockets. A profile outside a caller's scope returns the same `404` response as a missing profile. The dashboard is a convenience layer; it is not the security boundary.

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
