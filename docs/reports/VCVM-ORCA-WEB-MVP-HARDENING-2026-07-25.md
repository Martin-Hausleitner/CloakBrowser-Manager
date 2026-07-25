# VCVM Orca Web MVP hardening — worker report

**Date:** 2026-07-25  
**Branch:** `feature/browser-use-agent-workspace`  
**Dispatch:** `task_e4b979a36d3a` / `ctx_58e0f4cdf4f2`  
**Commit policy:** no commit / no push / no deploy

## Correction applied

Pinned Orca worktree/wrapper to the registered vk-repos checkout (not the deploy copy):

- `CBM_ORCA_WORKTREE=path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use`
- `CBM_ORCA_AGENT_WRAPPER=/home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/orca_agent_cli.sh`

Preflight now requires:

```bash
orca-ide worktree show --worktree path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use --json
```

## What shipped

1. **Host Orca bridge** in `docker-compose.vcvm.yml`: RO mounts for `/home/coder/orca`, `/home/coder/.local`, `/home/coder/.config/orca`; `HOME=/home/coder`; `CBM_ORCA_BIN`, worktree, wrapper, host-loopback `CBM_BASE_URL`, `CBM_AGENT_KEY_FILE`.
2. **`scripts/orca_agent_cli.sh`**: allowlists `cursor-agent|grok|codex`, exports base URL + key file path only, `exec`s the agent (no secrets on argv).
3. **`backend/orca_adapter.py`**: `terminal.create --command` uses fixed wrapper + validated agent (not bare agent string).
4. **`scripts/cbm_agent_ctl.py`**: reads `CBM_AGENT_KEY_FILE` when `CBM_AGENT_KEY` absent; adds `tasks create|run` and `runs get|cancel|outputs`.
5. **`scripts/vcvm_orca_preflight.py`** + deploy hook: fail closed on missing paths/runtime/unregistered worktree; never prints secrets.
6. Docs/skill updated with exact commands and truthful Browser-Use worker dependency.

## Tests

| Check | Result |
| --- | --- |
| `pytest backend/tests/test_orca_adapter.py backend/tests/test_orca_api.py` | 21 passed |
| `pytest scripts/test_vcvm_orca_preflight.py scripts/test_cbm_agent_ctl.py` | 10 passed |
| `python3 scripts/test_vcvm_deployment.py` | passed |
| `docker compose … config -q` | ok |
| live `python3 scripts/vcvm_orca_preflight.py` | passed |
| `cd frontend && npm test` | 142 passed |
| `cd frontend && npm run build` | success |

## Remaining limits

- No deploy performed; production `.env.vcvm` / key file provisioning on VCVM still operator-owned.
- Browser-Use worker sidecar still required for async run execution after queue.
- Process-local Orca session maps remain in-memory across Manager restarts.

## Follow-up: direct browser control CLI

`scripts/cbm_browser_ctl.py` is the preferred immediate-action path for
Orca-launched agents: one automation lease per command, Playwright
`connect_over_cdp` through Manager CDP with `Authorization` +
`X-CBM-Automation-Lease` headers only, always released in `finally`. Focused
mocked tests: `scripts/test_cbm_browser_ctl.py` (5 passed). Skill updated to
prefer this path over async Browser-Use runs for navigate/click/fill/text/screenshot.
