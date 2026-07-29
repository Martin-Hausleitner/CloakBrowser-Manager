---
name: cloakbrowser-control
description: Use for CloakBrowser Manager CLI/MCP control of profiles, sessions, runs, Browser Use views, Orca-Web availability, VCVM capability checks, or local-Mac launch capability checks; do not use for generic UI design or unrelated browser troubleshooting.
---

# CloakBrowser Control

Use Manager-owned CLI and MCP surfaces only. Produce JSON-only stdout and report unavailable capabilities exactly as returned.

## When to Use

Use this skill when the task asks an agent to inspect or operate CloakBrowser Manager resources through:

- `scripts/cbm_agent_ctl.py`
- `scripts/cbm_mcp.py`
- profiles, sessions, views, tasks, runs, or Browser Use outputs
- Orca-Web capability discovery
- VCVM or local-Mac capability discovery

Do not use this skill for generic frontend UI work, unrelated Playwright scripts, general browser debugging, or direct shell access inside a runtime.

## Required Discovery

Run discovery before every mutation or browser-control action:

```bash
scripts/cbm_agent_ctl.py --json api version
scripts/cbm_agent_ctl.py --json api capabilities
scripts/cbm_agent_ctl.py --json whoami
```

Inspect the returned `resources.RESOURCE.available` object for the requested channel:

- `rest`
- `cli`
- `mcp`
- `skill`

If the requested channel is `false`, stop that resource path and return the resource JSON with `reason_code` when present. Do not substitute another runtime, host, harness, MCP tool, browser socket, or shell path.

## Safe CLI Commands

Use only commands that exist in the CLI. Add `--idempotency-key` for create and side-effecting operations so callers can correlate retries, but do not claim the server enforces idempotency globally.

```bash
scripts/cbm_agent_ctl.py --json profiles list
scripts/cbm_agent_ctl.py --json profiles get PROFILE_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY profiles create --name NAME --sandbox SANDBOX --harness browser-use
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY profiles update PROFILE_ID --name NAME
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY profiles launch PROFILE_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY profiles stop PROFILE_ID
scripts/cbm_agent_ctl.py --json profiles open-links PROFILE_ID --mode vnc
scripts/cbm_agent_ctl.py --json accounts list --profile-id PROFILE_ID
scripts/cbm_agent_ctl.py --json accounts get ACCOUNT_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY accounts create --profile-id PROFILE_ID --provider PROVIDER --subject-label LABEL
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY accounts update ACCOUNT_ID --auth-state signed_in
scripts/cbm_agent_ctl.py --json accounts history ACCOUNT_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY accounts event ACCOUNT_ID --event-type observed
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY accounts delete ACCOUNT_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks create --profile-id PROFILE_ID --title TITLE
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks run TASK_ID --profile-id PROFILE_ID --task TASK --allowed-origin ORIGIN
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks run TASK_ID --profile-id PROFILE_ID --harness browser-use --task TASK --allowed-origin ORIGIN
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks run TASK_ID --profile-id PROFILE_ID --harness unbrowse --task TASK --allowed-origin ORIGIN
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks run TASK_ID --profile-id PROFILE_ID --harness stagehand --task TASK --allowed-origin ORIGIN
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY tasks run TASK_ID --profile-id PROFILE_ID --harness acpx --agent grok-build --task TASK --allowed-origin ORIGIN
scripts/cbm_agent_ctl.py --json runs get RUN_ID
scripts/cbm_agent_ctl.py --json runs outputs RUN_ID
scripts/cbm_agent_ctl.py --json --idempotency-key IDEMPOTENCY_KEY runs cancel RUN_ID
```

Do not invent project, proxy, extension, approval, operation, box, runtime, local-Mac, or Orca-Web CLI commands when `api capabilities` reports `cli: false`.

## MCP Scope

Use `scripts/cbm_mcp.py` only inside a Manager-granted run context.

MCP parity is limited to discovery/schema tools and run-scoped browser tools:

- `browser_inspect`
- `browser_navigate`
- `browser_click`
- `browser_fill`
- `browser_read_text`
- `control_plane_capabilities`
- `control_plane_resource_schema`
- `orca_web_capabilities`

There are no general Manager resource MCP tools for profiles, projects, proxies, extensions, accounts, approvals, operations, boxes, runtimes, local-Mac, or Orca-Web sessions yet. If a task asks for those over MCP, report the capability JSON instead of falling back.

## Limitations

- `Idempotency-Key` and `If-Match` are client-supported headers only; server enforcement is pending unless the capability/schema response says otherwise.
- Account metadata is reference-only: account commands accept opaque `secretref-*` identifiers and never accept or return passwords, cookies, tokens, TOTP seeds, passkey material, or recovery codes.
- Browser Use, Unbrowse, Stagehand, and ACPX runs use the same Manager-owned task/run/output contract. A detected worker heartbeat proves process presence; only a completed run with typed outputs proves end-to-end browser control.
- Secret broker value access, approvals, operations, box resources, runtime resources, local-Mac resource launch, and Orca-Web resource control are unavailable in this slice.
- The CLI must not print credentials, raw proxy URLs with userinfo, cookies, bearer tokens, vault values, OTP/TOTP seeds, provider locators, unrestricted CDP endpoints, or local secret paths.
- The MCP server does not grant raw CDP, shell, credential, cookie, filesystem, or arbitrary Manager resource access.

## Output Contract

Treat command stdout as the authoritative Manager JSON; do not wrap it in an
invented success schema. For unavailable resources, preserve the exact
`available` channel map and `reason_code` from capability discovery. Put human
explanation on stderr or in the surrounding agent response, never mixed into
JSON stdout.

## Validation

Before reporting completion, run:

```bash
scripts/cbm_agent_ctl.py --json api version
python3 -m json.tool docs/contracts/control-plane-resource-v1.json
python3 -m json.tool docs/contracts/orca-web-resource-v1.json
```

For code changes to this control plane, also run the targeted Python tests, Ruff, py_compile, skill validation, and diff check required by the current task.

## Forbidden Actions

Never use bootstrap admin by default.

Never pass proxy userinfo in profile arguments.

Never open unrestricted raw CDP or export browser session storage.

Never pass free-form Chromium launch flags or manager-owned runtime flags.

Never store credential values in task prompts, task messages, MCP results, logs, or screenshots.
