# Direct browser control — code-review follow-up (2026-07-25)

**Dispatch:** `task_12fd5bd446b6` / `ctx_29e56ee279ea`  
**Owned files:** `scripts/cbm_browser_ctl.py`, `scripts/test_cbm_browser_ctl.py`,
`.agents/skills/cloakbrowser-orca-control/SKILL.md`, this report.

No commit, push, deploy, key mutation, or production calls.

## Blocking findings fixed

1. **Lease heartbeat** — Background heartbeats run at Manager-returned
   `heartbeat_interval_seconds` for the duration of each Playwright command;
   the thread is stopped/joined before release. Heartbeat failure raises a
   redacted `lease_heartbeat_failed` error and still closes/releases safely.
2. **Release failure** — DELETE failures are never silent. Commands raise
   `BrowserCtlLifecycleIssue` with action evidence preserved plus
   `warnings`/`error`, and `main()` exits nonzero (`ok: false`).
3. **Tab targeting** — `--page-index` / `--page-url`; default is the **last**
   page in the first context (newest tab) with `bring_to_front()`.
4. **profile_id** — Validated as one safe path segment (reject `/ ? #`
   whitespace/controls); path-quoted into API URLs.
5. **Central redaction** — `redact_url` / `redact_text` strip userinfo,
   secret query values, bearer/`cbm_*` token patterns; applied to output URLs,
   titles/text, and unexpected exception messages.
6. **Lifecycle tests + docs** — Covered heartbeat long-action, heartbeat
   failure, release failure, multi-page targeting, profile_id validation,
   redaction, and client-only `browser.close()` disconnect. Skill + module
   docs state that a fresh live E2E must still verify the managed profile
   remains running after direct control.

## Verification

```text
python -m pytest -q scripts/test_cbm_browser_ctl.py
22 passed
```

## Live E2E still required

Mocked tests confirm `browser.close()` is invoked on the CDP client. They
cannot prove the Manager-managed Chromium profile stays up. Operators should
run one live inspect/navigate against a running profile and confirm
`profiles status` still reports running afterward.
