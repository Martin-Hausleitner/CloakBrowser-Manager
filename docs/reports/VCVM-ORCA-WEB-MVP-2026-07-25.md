# VCVM Orca Web MVP — worker report

**Date:** 2026-07-25  
**Branch:** `feature/browser-use-agent-workspace`  
**Dispatch:** `task_48b08118297c` / `ctx_0f716dbfea96`  
**Commit policy:** no commit / no push / no deploy (per task)

## What shipped

Compact operator workspace in the CloakBrowser web app:

- **LEFT:** real Orca-managed `cursor-agent` / `grok` / `codex` CLI transcript + prompt composer
- **RIGHT:** selected real CloakBrowser `ProfileViewer`
- Desktop profile selection opens this workspace; mobile split-screen path unchanged

Backend adapter `backend/orca_adapter.py` invokes only allowlisted `/home/coder/.local/bin/orca-ide` operations with argv arrays (`shell=False`), tracks owned terminal handles, enforces timeouts/output bounds, and redacts secrets. Pause/resume are honestly unavailable.

## Files changed

| Path | Role |
| --- | --- |
| `backend/orca_adapter.py` | Allowlisted Orca CLI adapter + session registry |
| `backend/models.py` | Orca request/response models |
| `backend/main.py` | `/api/orca/capabilities`, `/api/orca/sessions` start/get/output/send/close |
| `backend/tests/test_orca_adapter.py` | Unit: allowlist, ownership, redaction, timeouts, shell=False |
| `backend/tests/test_orca_api.py` | API: auth, automate+interact, ownership, redaction |
| `.agents/skills/cloakbrowser-orca-control/SKILL.md` | Control path + lifecycle for CLI agents |
| `frontend/src/components/workspace/AgentBrowserWorkspace.tsx` | Dense workspace UI |
| `frontend/src/components/workspace/AgentBrowserWorkspace.test.tsx` | Layout / disabled / start-send-read / profile switch |
| `frontend/src/lib/api.ts` + `api.test.ts` | Client methods |
| `frontend/src/App.tsx` | Desktop `view` → AgentBrowserWorkspace |

## Tests run

- `pytest backend/tests/test_orca_adapter.py backend/tests/test_orca_api.py` → pass
- `pytest` focused access/agent/task suites with orca → 70 passed
- `cd frontend && npm test` → **142 passed**
- `cd frontend && npm run build` → success

## Remaining integration limits

1. Live E2E against a real profile + Orca terminal was not deployed; adapter is covered with injected runners.
2. Browser-Use sidecar / run claiming is still separate unfinished work; Orca sessions steer via control skill + public task/run APIs.
3. Pause/resume not exposed (Orca has no honest terminal pause API).
4. Sessions are process-local (in-memory); Manager restart drops ownership maps (terminals may remain in Orca until closed there).
5. No Docker/production/compose changes by design.
