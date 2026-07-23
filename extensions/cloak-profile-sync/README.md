# CloakBrowser Profile Sync (Chrome Extension)

Compact MV3 extension that lists CloakBrowser Manager profiles and proxies, then opens the same profile in **Cloud/VCVM** or **Local Mac CloakBrowser**.

## Load unpacked

1. Ensure the Manager tunnel is up (`http://127.0.0.1:18117/`).
2. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked**.
3. Select this directory:

   `extensions/cloak-profile-sync/`

4. Open the extension popup → paste the Manager bearer token
   (from `~/.config/cloakbrowser/vcvm-auth-token`) or username/password.
5. For **Open Local**, start the bridge in a terminal:

```bash
python3 extensions/cloak-profile-sync/host/local_bridge.py
```

Requires `cloakbrowser` installed (`pip install cloakbrowser && cloakbrowser install`).

## Open Cloud vs Open Local

| Button | What it does | Proxy-on-start |
|--------|----------------|----------------|
| **Open Cloud** | Calls `POST /api/extension/sessions/open` (`prefer: cloud`, `launch: true`) when available; otherwise `POST /api/profiles/{id}/launch` + opens `/?profile={id}` in Manager. | Manager launch always applies the profile proxy. |
| **Open Local** | POSTs to the localhost bridge (`http://127.0.0.1:18765/launch`). Bridge fetches the profile from Manager and launches CloakBrowser with fingerprint + **proxy when configured**. | Enforced in bridge (`proxy_applied` in response). |

Cloud = VCVM/Manager VNC workspace (via tunnel or `CLOUD_BASE_URL`).  
Local = CloakBrowser binary on this Mac under `~/.cloakbrowser/profile-sync/profiles/<id>/`.

## Data sources (aligned to Manager)

Preferred (when deployed on VCVM):

- `GET /api/extension/catalog` — redacted profiles + proxy inventory + endpoint map
- `POST /api/extension/sessions/open` — launch + steel-style link set (`session_viewer_url`, `debug_url`, …)

Fallback (current VCVM as of this writing):

- `GET /api/profiles`, `GET /api/proxies`
- `POST /api/profiles/{id}/launch`
- Deep-link `/?profile={id}` (Manager frontend)

Also used:

- `GET /api/profiles/{id}/extensions` — installed extensions (Details)
- `GET /api/profiles/{id}/health` — authenticity / scan / fingerprint scores when measured
- `GET /api/extension/defaults` — **extension point** for selectable Comet default extensions (ignored until Manager ships it)

## UI notes

- Dark compact Browser-Use aesthetic (`#0a0a0a` / indigo accent).
- Proxy passwords are never rendered; inventory fields are already masked; legacy `profile.proxy` is masked client-side.
- Profile **Details** loads extension chips (name · version · trust) and health scores when the API returns them.
- Default-extension selection is shown only if Manager exposes `/api/extension/defaults`.

## Auth storage

- Bearer token → `chrome.storage.local` (practical for tunnel workflows).
- Password → `chrome.storage.session` only (cleared when Chrome quits).
- Prefer token; password cookie auth is unreliable cross-origin from extensions.

## Verify without Chrome

```bash
python3 extensions/cloak-profile-sync/scripts/verify_against_manager.py
```

## Manager API gaps / extension points

| Gap | Extension behavior today |
|-----|---------------------------|
| `/api/extension/catalog` + `/sessions/open` not yet on VCVM | Falls back to classic profile/proxy/launch APIs |
| `/api/extension/defaults` (Comet selectable defaults) | Optional banner when present |
| Extension `icon_url` on inventory items | Rendered when Manager adds the field |
| Explicit “Mac local launch” URL in open-links | Remains the localhost bridge (`18765`) |
| Profile create/templates from Manager create-flow | Extension lists whatever catalog/profiles returns; adapt when template list endpoints appear |

Do not commit raw proxy passwords or auth tokens.
