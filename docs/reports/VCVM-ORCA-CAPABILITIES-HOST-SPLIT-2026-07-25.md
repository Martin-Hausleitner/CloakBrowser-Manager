# Capabilities container/host split — final review blocker (2026-07-25)

**Dispatch:** `task_f8f32ccfdecf` / `ctx_0a63e7805791`

## Problem

`capabilities()` Path-checked the host wrapper and scoped agent key inside the
Manager container. Those paths are intentionally **not** mounted, so
`available` flipped false after deploy and disabled Launch even though
`start_session` correctly sends the host wrapper path through host Orca.

## Contract

| Layer | Proves | Must not |
| --- | --- | --- |
| Host `vcvm_orca_preflight.py` (before compose) | Orca paths/bin, host wrapper readable+executable, agent key `0600`/syntax, status, worktree show | Mount key/wrapper into the container; print key contents |
| Container `capabilities.available` | Orca binary reachable, bounded status ready, registered worktree show | Path-probe host wrapper/key; mount vk-repos or `.config/cloakbrowser` |

`start_session` keeps the fixed host wrapper path for `terminal.create`.

## Verification

```text
pytest backend/tests/test_orca_adapter.py backend/tests/test_orca_api.py \
  scripts/test_vcvm_deployment.py scripts/test_vcvm_orca_preflight.py
34 passed
```

No commit/push/deploy/key mutation.
