# External agent control plane

Use this when Codex, Antigravity, Claude Code, OpenCode, Browser Use, a Chrome
extension, or any harness must steer CloakBrowser Manager **without UI clicks**.

The UI is for human visibility. The control plane is HTTP + CLI + this skill.

## Auth

1. Admin creates a Paperclip-compatible agent once (dashboard or API):

```bash
curl -sS -X POST "$CBM_BASE_URL/api/access/agents" \
  -H "Authorization: Bearer $CBM_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "codex-ops",
    "paperclip_agent_id": "codex-ops",
    "grants": [
      {"sandbox_id": "agents", "permission": "operate"},
      {"sandbox_id": "agents", "permission": "automate"}
    ]
  }'
```

2. Store the returned `api_key` (`cbm_agent_…`) once. Only the hash is persisted.

3. Every agent call uses:

```text
Authorization: Bearer cbm_agent_…
```

Permissions:

| Grant | Can do |
| --- | --- |
| `view` | List/get profiles in sandbox, open VNC read-only, read health/extensions/open-links |
| `interact` | VNC input + clipboard |
| `operate` | Create/update/delete/organize profiles in that sandbox; launch/stop; health rerun |
| `automate` | CDP REST + CDP WebSocket (does **not** imply launch — also grant `operate`) |

`sandbox_id` is the authorization key. `project_id` / `folder_path` are organization only.

## CLI

```bash
export CBM_BASE_URL=http://127.0.0.1:18117
export CBM_AGENT_KEY=cbm_agent_…

scripts/cbm_agent_ctl.py whoami
scripts/cbm_agent_ctl.py profiles create --name demo --sandbox agents --harness codex --geoip
scripts/cbm_agent_ctl.py profiles launch <id>
scripts/cbm_agent_ctl.py profiles open-links <id> --mode cdp --field cdp_fullscreen_url
scripts/cbm_agent_ctl.py profiles open-links <id> --mode vnc --field vnc_fullscreen_url
scripts/cbm_agent_ctl.py profiles status <id>
scripts/cbm_agent_ctl.py profiles stop <id>
```

Also: `sandboxes`, `catalog`, `profiles health`, `profiles extensions`, `open-session`.

## Core HTTP surface

| Action | Method + path | Min grant |
| --- | --- | --- |
| Identity | `GET /api/access/me` | any |
| Sandboxes | `GET /api/access/sandboxes` | view (filtered) |
| List/create profiles | `GET/POST /api/profiles` | view / operate |
| Get/update/delete | `GET/PUT/DELETE /api/profiles/{id}` | view / operate / operate |
| Launch/stop/status | `POST …/launch`, `POST …/stop`, `GET …/status` | operate / operate / view |
| Health | `GET …/health`, `POST …/health/run` | view / operate |
| Extensions | `GET …/extensions` | view |
| Open links | `GET …/open-links?prefer=local\|cloud&mode=cdp\|vnc\|shell` | view |
| One-click open | `POST /api/extension/sessions/open` | view (+ operate if launching) |
| Catalog | `GET /api/extension/catalog` | any authenticated |
| CDP | `GET/WS /api/profiles/{id}/cdp` | automate |
| VNC | `WS /api/profiles/{id}/vnc` | view (input needs interact) |

Admin-only (not agent): access users/agents/grants management, proxy inventory ingest, live diagnostics.

## VNC vs CDP open (handoff)

- **CDP / Browser-Use snappy live:** `links.cdp_fullscreen_url` or `links.live_url` → `/session/{id}/live`
- **VNC fullscreen:** `links.vnc_fullscreen_url` → `/?profile={id}&view=vnc&fullscreen=1`
- **Raw sockets:** `links.websocket_url` (VNC WS), `links.cdp_url` / `local.cdp_ws_url` (CDP)
- Prefer `mode=cdp` when the agent has `automate`; otherwise `mode=vnc`

Never print agent keys, proxy credentials, cookies, or host paths in logs/chat.

## Boundaries

- Host-scoped browser actions still require the verified `codex-computer-use` bridge.
- Preferred harness labels are metadata; they do not bypass bridge verification.
- Profile health is observation, not an undetectability guarantee.
- Manager and browsers stay on the VCVM; Mac is client/tunnel only.
