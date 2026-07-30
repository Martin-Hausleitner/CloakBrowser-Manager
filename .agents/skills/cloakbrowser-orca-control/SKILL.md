---
name: cloakbrowser-orca-control
description: Use when an Orca-managed AGY, Grok, cursor-agent, or Codex CLI session must control the selected CloakBrowser Manager profile through the Manager public API — prefer direct lease+CDP control for immediate actions, and Browser-Use task/run APIs for async worker-backed automation.
metadata:
  category: integration-documentation
  triggers:
    - CloakBrowser Orca
    - AgentBrowserWorkspace
    - agy
    - cursor-agent
    - grok
    - codex
    - cbm_browser_ctl
    - task-sessions runs
---

# CloakBrowser Orca control path

You are running inside an **Orca-managed terminal** started by CloakBrowser Manager's
Agent Browser workspace via `scripts/orca_agent_cli.sh <cursor-agent|grok|agy|codex>`.
A real CloakBrowser profile is already selected in the UI live viewer. Do **not**
launch Chromium, call Browser-Use Cloud, invent CDP ports, or drive the browser
with arbitrary shell.

Worktree (must already be registered in Orca):

```text
path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use
```

## Interactive CLI startup

- Prefer **AGY** for the default live terminal. Manager injects the selected
  `profile_id`, this skill path, the safe key-file path, and the operator task,
  then commits AGY's multi-line draft automatically.
- **Grok** starts with `--no-alt-screen` so its real login/chat screen remains
  visible in browser terminal scrollback. If Grok asks for authentication,
  complete it inside that terminal; never paste a token into the Manager chat.
- The Antigravity managed preset currently routes through **ACPX + Grok Build**,
  not Claude. Provider preflight remains fail-closed when Grok Build is not signed in.
- Follow-up text entered below the transcript is sent to the same owned Orca
  terminal. Do not launch a second CLI or browser for the selected task.

## Auth (never print secrets)

```bash
export CBM_BASE_URL=http://127.0.0.1:18115
export CBM_AGENT_KEY_FILE=/home/coder/.config/cloakbrowser/orca-agent-key
# optional override: export CBM_AGENT_KEY=cbm_agent_…   # still never print it
```

Both `scripts/cbm_browser_ctl.py` and `scripts/cbm_agent_ctl.py` read
`CBM_AGENT_KEY_FILE` when `CBM_AGENT_KEY` is unset.

## Preferred path: direct control (immediate actions)

For navigate/click/fill/read/screenshot on the **already running** selected
profile, prefer the direct controller. It acquires one automation lease per
command, heartbeats at the Manager-returned `heartbeat_interval_seconds`
throughout the Playwright action, connects with Playwright `connect_over_cdp`
through the Manager proxy (`Authorization` + `X-CBM-Automation-Lease` headers
only — never query tokens), disconnects the CDP client with `browser.close()`,
and always attempts lease release (release failure is a nonzero lifecycle error
that still preserves action evidence).

```bash
scripts/cbm_browser_ctl.py inspect --profile-id <profile_id>
scripts/cbm_browser_ctl.py navigate --profile-id <profile_id> --url https://example.com
scripts/cbm_browser_ctl.py click --profile-id <profile_id> --selector "text=Example"
scripts/cbm_browser_ctl.py fill --profile-id <profile_id> --selector "input[name=q]" --text "hello"
scripts/cbm_browser_ctl.py text --profile-id <profile_id>
scripts/cbm_browser_ctl.py text --profile-id <profile_id> --selector "h1"
scripts/cbm_browser_ctl.py screenshot --profile-id <profile_id> --path shot.png
# Optional tab targeting (default: last/newest page in first context + bring_to_front):
scripts/cbm_browser_ctl.py inspect --profile-id <profile_id> --page-index 0
scripts/cbm_browser_ctl.py inspect --profile-id <profile_id> --page-url "example.com"
```

Notes:

- Only `http`/`https` navigation is allowed (no `javascript:`, credentials, or file URLs).
- `profile_id` must be one safe path segment (no `/`, `?`, `#`, whitespace, or controls).
- Screenshots must land under `CBM_BROWSER_ARTIFACT_ROOT` (default `/tmp/cbm-browser-artifacts`).
- Output is structured JSON without keys, bearer tokens, or lease tokens; URLs are redacted (no userinfo / secret query values).
- Requires Playwright in the environment; fails clearly if missing.
- Requires `automate` on the profile sandbox; does not launch/stop the profile.
- `browser.close()` disconnects this CDP client only. A fresh live E2E must still
  verify the managed Manager profile remains running after direct-control commands.
- Heartbeat failure aborts safely (redacted error) and still releases the lease.

Profile lifecycle helpers remain on `cbm_agent_ctl.py`:

```bash
scripts/cbm_agent_ctl.py whoami
scripts/cbm_agent_ctl.py profiles status <profile_id>
scripts/cbm_agent_ctl.py profiles launch <profile_id>
scripts/cbm_agent_ctl.py profiles open-links <profile_id> --mode cdp
```

## Async path: Browser-Use task/run (worker-dependent)

Use this when you need the Browser-Use sidecar to claim and execute a longer
run. Queuing a run is **not** execution proof — the VCVM Browser-Use worker must
claim it.

```bash
scripts/cbm_agent_ctl.py tasks create --profile-id <profile_id> --title "demo"
scripts/cbm_agent_ctl.py tasks run <session_id> \
  --profile-id <profile_id> \
  --task "Navigate and return the page title" \
  --allowed-origin https://example.com
scripts/cbm_agent_ctl.py runs get <run_id>
scripts/cbm_agent_ctl.py runs outputs <run_id>
scripts/cbm_agent_ctl.py runs cancel <run_id>
```

## Lifecycle (honest)

| Action | How | Notes |
| --- | --- | --- |
| start | Manager `POST /api/orca/sessions` | `terminal.create --command "<wrapper> <agent>"` |
| read | Manager `GET /api/orca/sessions/{id}/output` | Bounded, redacted transcript |
| send | Manager `POST /api/orca/sessions/{id}/send` | Operator prompt into the live CLI |
| close | Manager `POST /api/orca/sessions/{id}/close` | Closes the Orca terminal handle |
| pause / resume | **Not supported** | Orca terminal APIs do not expose pause/resume |
| direct browser action | `scripts/cbm_browser_ctl.py …` | Lease + proxied CDP; preferred for immediate actions |
| async Browser-Use | `tasks run` / `runs get` | Requires worker claim |

## Permissions

| Grant | Needed for |
| --- | --- |
| `view` | Read session metadata/output and watch the live ProfileViewer |
| `interact` | Send prompts / close the Orca composer session |
| `automate` | Direct lease+CDP control, Orca agent start, Browser-Use runs |
| `operate` | Launch/stop the underlying CloakBrowser profile |

## Anti-patterns

- Do not launch a second browser, guess CDP ports, or put lease/agent tokens in URLs.
- Do not use the deploy-copy path as the Orca worktree.
- Do not put agent keys in argv, UI, logs, compose env values, or Git.
- Do not claim Browser-Use success unless a Manager run/output record says so.
