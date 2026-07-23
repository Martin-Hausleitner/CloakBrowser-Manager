# Agent / CLI session control plane

Bearer auth (bootstrap token or `cbm_agent_…` API key):

```bash
export BASE=http://127.0.0.1:18117
export TOKEN="$(cat ~/.config/cloakbrowser/vcvm-auth-token)"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/json")
```

## Catalog and defaults

```bash
curl -fsS "${AUTH[@]}" "$BASE/api/extension/catalog"
curl -fsS -X POST "${AUTH[@]}" "$BASE/api/extension/catalog"
curl -fsS "${AUTH[@]}" "$BASE/api/extension/defaults"   # Comet selectable defaults (+ optional icon_url)
curl -fsS "${AUTH[@]}" "$BASE/api/extension/templates"
curl -fsS "${AUTH[@]}" "$BASE/api/profile-templates"
```

## Open session (VNC + CDP links)

```bash
# Prefer snappy CDP live URL (default mode=cdp)
curl -fsS -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"profile_id":"PROFILE_ID","launch":true,"prefer":"local","mode":"cdp"}' \
  "$BASE/api/extension/sessions/open"

# Or only resolve links
curl -fsS "${AUTH[@]}" "$BASE/api/profiles/PROFILE_ID/open-links?prefer=local&mode=cdp"
curl -fsS "${AUTH[@]}" "$BASE/api/profiles/PROFILE_ID/open-links?prefer=local&mode=vnc"
```

Returned link fields (local and optional cloud via `CLOUD_BASE_URL`):

| Field | Purpose |
| --- | --- |
| `live_url` / `cdp_fullscreen_url` | Ultra-low-latency CDP screencast (`/session/{id}/live`) |
| `vnc_fullscreen_url` | Manager VNC fullscreen deep-link |
| `session_viewer_url` | Standard Manager shell |
| `cdp_http_url` / `cdp_ws_url` | Playwright/agent-browser CDP attach |
| `vnc_ws_url` | Authenticated VNC websocket |
| `live_metrics_url` | Poll/post FPS/RTT/connection state |

## Live metrics

```bash
curl -fsS -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"transport":"cdp","connection_state":"connected","fps":30,"rtt_ms":18}' \
  "$BASE/api/profiles/PROFILE_ID/live-metrics"
curl -fsS "${AUTH[@]}" "$BASE/api/profiles/PROFILE_ID/live-metrics"
```

Do not put proxy passwords, agent keys, or auth tokens in docs, commits, or chat.
