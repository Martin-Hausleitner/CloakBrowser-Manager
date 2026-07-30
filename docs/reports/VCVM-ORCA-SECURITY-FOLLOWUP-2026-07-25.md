# Security-review follow-up — VCVM Orca gaps (2026-07-25)

## Scope

Fixed three verified gaps in the uncommitted VCVM/Orca worktree. No commit,
push, deploy, key provisioning, or secret output.

## (1) Agent key file preflight (fail closed)

- Added `backend/orca_agent_key.py` as the single validator:
  exists, regular file (symlinks refused), mode exactly `0600`, owned/readable
  by current host user, non-empty, syntax `cbm_agent_` + safe token chars
  (`[A-Za-z0-9_-]{16,}`). Errors never include key contents.
- `scripts/vcvm_orca_preflight.py` always checks the key (default
  `/home/coder/.config/cloakbrowser/orca-agent-key`, overridable via
  `--agent-key-file` / `CBM_AGENT_KEY_FILE`), including with `--skip-runtime`.
- `scripts/deploy_vcvm.sh` passes `--agent-key-file` before compose.
- Docs + deployment contract tests updated.

## (2) Truthful `capabilities().available` (container-safe)

- **Correction (same-day follow-up):** the Manager container deliberately does
  **not** mount the host wrapper or scoped agent key. Container-local Path
  probes for those files incorrectly disabled Launch after deploy.
- `OrcaAdapter.capabilities()` / `available` now requires only what the
  container can prove without side effects: Orca binary reachable, bounded
  `status` ready, and configured registered `worktree.show` success.
- Host wrapper + agent key readiness remain fail-closed in
  `scripts/vcvm_orca_preflight.py` before compose. `start_session` still sends
  the fixed host wrapper path through host Orca `terminal.create`.
- Safe notes explain the split; UI stays disabled only when container
  readiness fails.
- Added `worktree.show` to the allowlisted operations.

## (3) Owner-only close (least privilege)

- Removed the unreachable/misleading `identity.is_admin` bypass on
  `POST /api/orca/sessions/{id}/close`.
- Ownership remains enforced by `get_session(..., owner_key=...)`.
- Regression: bootstrap admin cannot close another agent's session (404);
  owner close still succeeds.
- Documented in capabilities notes and `docs/VCVM-DEPLOYMENT.md`.

## Verification

| Check | Result |
| --- | --- |
| `pytest` focused (adapter/api/key/preflight/deploy) | 35 passed |
| Frontend `AgentBrowserWorkspace.test.tsx` | 5 passed |
| Frontend `npm run build` | passed |
| `docker compose -f docker-compose.vcvm.yml config` (placeholder AUTH_TOKEN) | passed |
| Live `vcvm_orca_preflight.py` with temporary synthetic `0600` key | passed; key removed afterward |
| Live preflight with missing key + `--skip-runtime` | exit 78 (fail closed) |

## Files touched (high level)

- `backend/orca_agent_key.py` (new)
- `backend/orca_adapter.py`, `backend/main.py`
- `backend/tests/test_orca_*.py`, `backend/tests/test_orca_agent_key.py`
- `scripts/vcvm_orca_preflight.py`, `scripts/deploy_vcvm.sh`
- `scripts/test_vcvm_orca_preflight.py`, `scripts/test_vcvm_deployment.py`
- `docs/VCVM-DEPLOYMENT.md`
- `frontend/.../AgentBrowserWorkspace.tsx` (+ test)

## Left

Nothing remaining for this security-review dispatch. Production host still needs
a real mode-`0600` agent key at the documented path for deploy/runtime readiness
(operator-provisioned; not created by this worker).
