# Direct browser control MVP — worker report

**Date:** 2026-07-25  
**Dispatch:** `task_7cc0250d79ee` / `ctx_7bd24056fafa`  
**Owned files:** `scripts/cbm_browser_ctl.py`, `scripts/test_cbm_browser_ctl.py`, `.agents/skills/cloakbrowser-orca-control/SKILL.md` (+ hardening report note)

## Commands

```bash
scripts/cbm_browser_ctl.py inspect --profile-id <id>
scripts/cbm_browser_ctl.py navigate --profile-id <id> --url https://example.com
scripts/cbm_browser_ctl.py click --profile-id <id> --selector "…"
scripts/cbm_browser_ctl.py fill --profile-id <id> --selector "…" --text "…"
scripts/cbm_browser_ctl.py text --profile-id <id> [--selector "…"]
scripts/cbm_browser_ctl.py screenshot --profile-id <id> --path shot.png
```

## Safety

- Auth: `CBM_AGENT_KEY` or `CBM_AGENT_KEY_FILE` (same as `cbm_agent_ctl.py`)
- One automation lease per command; always released in `finally`
- CDP via Manager proxy with header-only auth (no query tokens)
- Rejects non-http(s), credentialed URLs, long selectors/text, path escape
- JSON output never includes keys/lease tokens
- No new dependencies; Playwright imported lazily

## Tests

```text
.venv/bin/python -m pytest -q scripts/test_cbm_browser_ctl.py
5 passed in 0.09s
```

Covered: key-file auth, URL/selector/text/path validation, lease acquire+release on failure, CDP headers, command routing.
