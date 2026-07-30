# MV3 DevTools real-Chromium E2E

Strict VCVM E2E for the unpacked `extensions/cloak-profile-sync` MV3 extension.

## What it proves

1. Launch disposable Chromium (not Google Chrome branded, which ignores `--load-extension`) with:
   - temporary `--user-data-dir`
   - `--load-extension` + `--disable-extensions-except` for `cloak-profile-sync`
   - `--disable-features=DisableLoadExtensionCommandLineSwitch`
   - CDP via `--remote-debugging-port` + `--remote-allow-origins=*`
2. Start the local extension control bridge on loopback (`18766` when free).
3. Verify the extension **service worker** through CDP `Target.getTargets` and popup `chrome.runtime` probe.
4. Exercise local CLI ops `status|start|stop|export|compile` and MCP tool inventory **where the runtime allows**.
5. Write redacted step/blocker/summary JSON under `artifacts/mv3-devtools-e2e/` (gitignored).

## Commands

```bash
# unit helpers + live e2e
python3 -m pytest scripts/e2e/tests/test_mv3_devtools_helpers.py \
  scripts/e2e/tests/test_mv3_devtools_e2e.py -q

# standalone runner
python3 scripts/e2e/run_mv3_devtools_e2e.py --artifact-dir artifacts/mv3-devtools-e2e/manual
```

Optional:

```bash
export CBM_MV3_CHROMIUM_BINARY=~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome
```

## Safety

- No Manager accounts, bearer tokens, proxy credentials, or real profile data.
- Artifacts pass a redaction gate (secret keys, bearer/cookie-like text, home paths).
- Control token files are mode `0600` and deleted after the run.

## Known blocker (honest)

When the service worker is verified but every CLI op times out, the suite records:

`bridge_requires_origin_on_command_poll`

Chromium sends `Origin` on `POST /v1/extension/session` but often omits `Origin` on long-poll `GET /v1/extension/commands/next`. The control bridge authorizes extension calls with **both** Origin and session bearer, so command delivery never completes. Product fix belongs in `scripts/cbm_extension_bridge.py` (out of this E2E ownership slice).

Google Chrome stable on this host also ignores `--disable-extensions-except` / effectively does not load unpacked extensions via CLI; the harness therefore prefers Playwright Chromium or system Chromium.
