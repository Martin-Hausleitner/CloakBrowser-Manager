# VCVM Four-Hour MVP Report

**Date:** 2026-07-28
**Scope:** VCVM-only Linux runtime
**Live URL:** `https://vcvm.tail6a40cd.ts.net/`
**Release line:** `release-20260728-acpx-browser-bridge`

## Result

The VCVM MVP is visible at `https://vcvm.tail6a40cd.ts.net/`.

Verified user-facing surface:

- Left pane: compact CLI / ACP / ACPX switch.
- Right pane: managed browser showing Example Domain.
- Screenshot evidence: `/tmp/cbm-four-hour-mvp-visible.png`.
- Browser/runtime error check: `0` console errors, `0` page errors, and `0`
  core request errors were recorded for the visible MVP pass.

## ACPX Run Evidence

Final post-deploy ACPX run `4a3fda4f-7d7b-4be6-a0d7-20f15a84bd54` exercised the managed
CloakBrowser MCP browser controls:

- `mcp__cloakbrowser__browser_navigate`
- `mcp__cloakbrowser__browser_inspect`
- `mcp__cloakbrowser__browser_read_text`

The run evidence is redacted. This report does not record credential values,
secret values, private endpoints, or Mac runtime paths.

## Security And Redaction

Verified security properties in scope:

- Auth status remains secret-safe: unauthenticated auth status does not expose
  credential or secret material.
- Screenshot/artifact retrieval is private and marked `no-store` where the
  authenticated output API returns screenshot bytes.
- Proxy inventory responses expose redacted fields only, including masked host
  and username fields plus credential presence, not raw proxy URLs or raw
  credential fields.
- Authenticated HTTP/HTTPS proxy credentials stay inside a Manager-owned
  loopback bridge. Chromium process arguments contain no proxy userinfo and
  receive only a credential-free `127.0.0.1` proxy endpoint.
- A live credentialed-proxy launch recorded `0` process arguments containing
  proxy userinfo. Proxy reachability, BrowserScan, and fingerprint checks all
  completed through the deployed VCVM Manager.
- The E2E secret-marker checks reject known secret strings in serialized
  payloads.

## Redacted Test Matrix

| ID | Surface | Exact redacted check | Verified result | Evidence |
| --- | --- | --- | --- | --- |
| TM-01 | Live URL | `GET https://vcvm.tail6a40cd.ts.net/` | HTTP `200`, `text/html; charset=utf-8` | Fresh GET check on 2026-07-28 |
| TM-02 | Visible UI | Open live URL and inspect first viewport | Left compact CLI / ACP / ACPX switch visible; right managed Example Domain browser visible | `/tmp/cbm-four-hour-mvp-visible.png` |
| TM-03 | Browser diagnostics | Console/page/core request error counters | `0` console errors; `0` page errors; `0` core request errors | Visible MVP pass diagnostics |
| TM-04 | ACPX browser control | Run `4a3fda4f-7d7b-4be6-a0d7-20f15a84bd54` | Used `browser_navigate`, `browser_inspect`, and `browser_read_text` through CloakBrowser MCP | Redacted ACPX run evidence |
| TM-05 | Live auth status | Read-only unauthenticated auth status check | Auth required and access control enabled; identity absent; no secret markers in payload | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-06 | Proxy contract | Read-only OpenAPI proxy schema check | Proxy inventory exposes `host_masked`, `username_masked`, and `has_credentials`; raw proxy URL and raw credential fields absent | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-07 | Proxy inventory redaction | Ingest synthetic credentialed proxy into temporary ASGI test database | Response masks host and username, reports credential presence, omits raw proxy URL, and contains no secret markers | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-08 | ACPX profile routing | Create ACPX run in temporary ASGI test database | Run remains bound to selected ACPX profile and redacted payload | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-09 | Antigravity routing | Create Antigravity task session in temporary ASGI test database | Antigravity mode creates queued ACPX Claude run bound to selected profile | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-10 | Antigravity guard | Attempt Antigravity run with non-Claude ACPX agent | Request rejected with `422` and strict mode message | `e2e/tests/test_antigravity_acpx_manager_e2e.py` |
| TM-11 | Authenticated proxy runtime | Launch the existing credentialed proxy profile through the deployed Manager | Proxy reachable; Chromium userinfo-argument count `0`; only loopback proxy arguments present | Redacted live process and health aggregates |
| TM-12 | Proxy bridge tests | Exercise CONNECT, absolute-form HTTP, cleanup, launch failure, and unsupported SOCKS handling | `49 passed` across bridge and BrowserManager tests | `backend/tests/test_proxy_bridge.py`, `backend/tests/test_browser_manager.py` |

Fresh verification run:

```text
.venv/bin/python -m pytest backend/tests/test_api.py backend/tests/test_auth.py backend/tests/test_browser_use_internal_api.py backend/tests/test_models.py e2e/tests/test_antigravity_acpx_manager_e2e.py scripts/test_acpx_worker.py scripts/test_acpx_runner.py scripts/test_cbm_mcp.py scripts/test_unbrowse_managed_helper.py -q
272 passed, 1 warning

.venv/bin/python -m pytest backend/tests/test_proxy_bridge.py backend/tests/test_browser_manager.py -q
49 passed
```

## Current Limitations

- Authenticated SOCKS proxies remain fail-closed until a credential-safe SOCKS
  bridge is implemented. Authenticated HTTP/HTTPS proxies are supported.
- Browser Use external signup is not proven in this MVP evidence set.
- This report describes only the VCVM Linux runtime. It does not validate or
  document a Mac runtime path.

## Rollback Note

For `release-20260728-acpx-browser-bridge`, rollback should restore the prior
release runtime from the reviewed release transaction capture and re-run the
redacted live checks before treating the previous runtime as accepted. Do not
reuse this MVP screenshot or ACPX run as rollback evidence after the runtime
changes.
