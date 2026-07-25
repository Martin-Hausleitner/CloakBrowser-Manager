# Browser-Use Host Worker (VCVM)

Host-side Browser-Use worker for CloakBrowser Manager on the VCVM. The worker
runs as a **systemd user unit** against the loopback Manager, using a dedicated
venv and a token file. Cursor credentials stay on the host; this package does
**not** mount `~/.cursor` (or any Cursor credential directory) into containers.

Pinned runtime dependency: `browser-use==0.13.6` (see
`scripts/requirements-browser-use-worker.txt`). Production install does **not**
include pytest.

## Layout

| Path | Role |
| --- | --- |
| `scripts/requirements-browser-use-worker.txt` | Pinned worker deps |
| `scripts/browser_use_worker.py` | Worker process (claim / run / heartbeat) |
| `scripts/cursor_chat_model.py` | Argv-only Cursor chat adapter for Browser Use |
| `scripts/provision_browser_use_worker.py` | Idempotent secret-safe provisioner |
| `deploy/systemd/cloakbrowser-browser-use-worker.service.template` | Unit template |
| `docs/BROWSER_USE_WORKER.md` | This guide |

## Install the dedicated venv with uv

Use an explicit venv path (example only — choose your own absolute path):

```bash
uv venv /home/coder/.venvs/browser-use-worker
uv pip install \
  --python /home/coder/.venvs/browser-use-worker/bin/python \
  -r /home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/requirements-browser-use-worker.txt
```

Confirm the pin without printing unrelated environment secrets:

```bash
/home/coder/.venvs/browser-use-worker/bin/python -c "import browser_use; print(browser_use.__version__)"
```

Expected: `0.13.6`.

## Safe provisioning

All paths are **explicit**. The provisioner does not invent broad defaults and
does not perform destructive cleanup. It never prints the worker token.
`scripts/deploy_vcvm.sh` is intentionally unchanged: worker secrets live in a
**separate** file that deploy does not rewrite.

Example (adjust paths to your host):

```bash
python3 /home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/provision_browser_use_worker.py \
  --repo /home/coder/vk-repos/CloakBrowser-Manager-browser-use \
  --manager-url http://127.0.0.1:18115 \
  --worker-env-file /home/coder/cloakbrowser-manager/.env.worker.vcvm \
  --worker-key-file /home/coder/.config/cloakbrowser/browser-use-worker.key \
  --venv /home/coder/.venvs/browser-use-worker \
  --unit-output /home/coder/.config/systemd/user/cloakbrowser-browser-use-worker.service \
  --worker-id browser-use-worker
```

Behavior:

1. If the key file is **absent**, mint `cbm_worker_` + 64 lowercase hex and write
   it with mode `0600`.
2. If the key file **exists** and is valid, keep it (idempotent). Invalid existing
   tokens are refused.
3. Write a dedicated worker env file (recommended name `.env.worker.vcvm`)
   containing **only** `CBM_WORKER_ID` and `CBM_WORKER_TOKEN`, mode `0600`. A
   unique same-directory `.env.worker.vcvm.bak.<timestamp-random>` backup is
   created only when content actually changes (idempotent same-content runs
   create no backup). `worker_id` must match
   `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` (no whitespace, newlines, or `=`).
4. Render the systemd user unit with absolute quoted paths (safe for spaces and
   `%`), venv Python invoked as `-m scripts.browser_use_worker` from the repo
   `WorkingDirectory` (so `from scripts…` imports resolve), loopback
   `--manager-url` origin, `--worker-id`, and `--token-file` (never `--token`,
   never an inlined secret). `UMask=0077`, `Restart=on-failure`, and `PATH`
   including `~/.local/bin` for `cursor-agent`.

**Required unit shape:** systemd must run the venv interpreter as
`-m scripts.browser_use_worker` with `WorkingDirectory` set to the repo root.
Do not invoke the worker as a loose script path from another cwd.

Worker CLI flags used by the unit (current `browser_use_worker.py`):

- `--manager-url`
- `--worker-id`
- `--token-file`

## Runtime behavior (verified)

### Cursor adapter (`scripts/cursor_chat_model.py`)

- Invokes `cursor-agent --print --mode ask --output-format json --workspace
  <per-invoke-tmp> --trust` (optional `--model <alias>`; omit for default).
- Does **not** pass `--sandbox` (unavailable / rejected on this VCVM host).
- Per-invoke workspace is a real directory mode `0700`. Symlink and
  non-directory workspace paths are rejected before chmod/write.
- Data-URL images are written under that workspace as `0600` temps and cleaned
  after invoke.
- Cancellation is per-invoke (not latched): `CancelledError` signals only the
  active invoke; `llm.cancel()` signals all active invokes. The adapter joins
  the runner/thread before unregistering or deleting the temp workspace.
- Structured output validates against the Browser Use schema with bounded
  retries (validation errors fed back into the prompt).

### Browser Use agent settings (`scripts/browser_use_worker.py`)

- `llm_timeout` is derived from the Manager run budget with a cleanup margin:
  `margin = clamp(run_timeout // 6, 15, 60)`, then
  `llm_timeout = clamp(run_timeout - margin, 1, run_timeout - 1)`
  (example: run `180` → llm `150`). Cursor subprocess timeout keeps the full
  run budget.
- Latency-oriented construction: `flash_mode=True`, `use_judge=False`,
  `max_clickable_elements_length=10000`, `llm_screenshot_size=(640, 480)`.
- Vision remains enabled (do not force `use_vision=False`).

### Final screenshot and public retrieval

On successful runs, screenshot retrieval prefers the newest safe
`AgentHistoryList` screenshot path, else `screenshots()` base64, then falls
back to live `BrowserSession.take_screenshot`. Bytes are size-bounded and
typed from PNG/JPEG magic (JPEG is never labeled PNG). Failures are soft.

Upload is two-phase: typed `screenshot` output, then PUT body to Manager.
Public retrieval is an authenticated
`GET /api/task-outputs/{output_id}/screenshot` (private, no-store).

### Reliability notes

- CDP WebSocket reconnect / HTTP 403 warnings still appear during long LLM
  waits. They are currently treated as a **non-blocking** reliability issue to
  track (can affect live-session screenshot fallback after success).
- First Cursor structured step latency still varies widely (~31–135s observed).
  Prefer a Manager/E2E run budget of **≥360s** until cold-start / schema /
  vision latency is reduced.

## Manager container restart

The Manager container loads worker bootstrap from a Compose long-syntax
`env_file` attachment (Docker Compose v2.24+ / verified on v5.1.0):

```yaml
env_file:
  - path: .env.worker.vcvm
    required: false
```

Missing `.env.worker.vcvm` is valid: the Manager starts without worker credentials
and the backend leaves worker auth disabled. No `${CBM_WORKER_*:}` interpolation
or insecure defaults are used. Place the file next to the VCVM compose project
(for example `/home/coder/cloakbrowser-manager/.env.worker.vcvm`).

After provisioning (or rotating) those values, **restart the Manager container**
so the new digest-backed worker identity is loaded:

```bash
cd /home/coder/cloakbrowser-manager
docker compose --env-file .env.vcvm -p cloakbrowser-manager-vcvm -f docker-compose.vcvm.yml up -d
```

`scripts/deploy_vcvm.sh` continues to rewrite only `.env.vcvm` and does not touch
`.env.worker.vcvm` (rsync already excludes `.env.*`). Do not paste the worker
token into compose argv, chat, or logs.

## Enable the systemd user unit

```bash
systemctl --user daemon-reload
systemctl --user enable --now cloakbrowser-browser-use-worker.service
systemctl --user status cloakbrowser-browser-use-worker.service --no-pager
```

If user lingering is required on this host so the unit survives logout:

```bash
loginctl enable-linger "$(whoami)"
```

## Secret-safe health checks

Never `cat` the key file, never `grep` tokens from `.env.worker.vcvm`, and never dump
unit Environment lines that might hold secrets (this unit uses `--token-file`
only).

Safe checks:

```bash
# Modes only
stat -c '%a %n' /home/coder/.config/cloakbrowser/browser-use-worker.key
stat -c '%a %n' /home/coder/cloakbrowser-manager/.env.worker.vcvm

# Unit references token file path, not the secret
systemctl --user cat cloakbrowser-browser-use-worker.service | grep -E 'token-file|ExecStart|UMask|WorkingDirectory'

# Manager loopback health (no worker token involved)
curl -fsS http://127.0.0.1:18115/health

# Worker journal without grepping for cbm_worker_
journalctl --user -u cloakbrowser-browser-use-worker.service -n 50 --no-pager
```

Expected: key and worker-env modes are `600`; unit shows `--token-file` and no
`cbm_worker_` material; `/health` succeeds when Manager is up.

## Rollback

1. Stop the user unit:

   ```bash
   systemctl --user disable --now cloakbrowser-browser-use-worker.service
   ```

2. Restore a prior worker env generation from the same directory
   (`.env.worker.vcvm.bak.<timestamp-random>`). Prefer the newest valid backup
   that matches the Manager digest you intend to roll back to, then restart the
   Manager container as above. Do not overwrite newer backups while restoring.

3. Optionally remove or replace the worker key file only after Manager no longer
   trusts its digest; do not commit key, env, or `*.bak*` files to git.

## Verified live evidence (VCVM, 2026-07-25)

Successful run `a5c8f034-5e46-4048-8e58-360be635e716`:

- Model: `cursor-grok-4.5-low`
- Outcome: succeeded with typed **action**, **2 observations**, **screenshot**,
  and **summary**
- Created `2026-07-25T17:20:13.798674Z`; first action
  `2026-07-25T17:21:05.357723Z` (**~51.6s** to first action)
- Screenshot: authenticated `GET /api/task-outputs/{output_id}/screenshot`
  returned **200** `image/png`, **20,445** bytes, **1920×1080**, content hash
  matched upload; response marked private / no-store
- Worker unit stayed **active** with **NRestarts=0**

Honest caveat — earlier run `b9e05637-e4c9-4cd6-99e5-d44232520db6` with
`timeout_seconds=180` was **revoked at deadline** before the first structured
action after ~135s. Observed first-step Cursor variance on this host is about
**31–135s**. Use an E2E budget of **≥360s** until cold-start / schema / vision
latency is reduced. CDP reconnect/403 warnings remain a tracked reliability
issue and did not block the successful `a5c8f034…` retrieval path above.

## E2E acceptance checklist

- [ ] Dedicated venv created with `uv`; `browser-use==0.13.6` importable; pytest
      not installed in that venv.
- [ ] Provisioner run with explicit `--repo`, `--manager-url`,
      `--worker-env-file`, `--worker-key-file`, `--venv`, `--unit-output`.
- [ ] Key file mode `600`; `.env.worker.vcvm` mode `600` and contains only
      `CBM_WORKER_*`; unique `.bak.<stamp>` exists only when content changed.
- [ ] Re-running provisioner with the existing valid key keeps the same token,
      does not duplicate `CBM_WORKER_*` keys, and creates no new backup when
      env content is unchanged.
- [ ] Manager container restarted after worker env write; compose attaches
      `.env.worker.vcvm` via `env_file.required: false`.
- [ ] `systemctl --user daemon-reload` and `enable --now` succeed.
- [ ] Unit `ExecStart` uses venv Python `-m scripts.browser_use_worker` with
      repo-root `WorkingDirectory`, `--manager-url` (loopback), `--worker-id`,
      `--token-file`; `UMask=0077`; `Restart=on-failure`; `PATH` includes
      `~/.local/bin`.
- [ ] No worker token appears in provisioner stdout, the unit file, journal
      snippets used for triage, or documentation.
- [ ] `scripts/deploy_vcvm.sh` remains untouched by worker provisioning.
- [ ] Cursor credentials remain on the host; no container bind of Cursor
      credential directories for this worker.
- [ ] Worker claims a `browser-use` run from Manager internal APIs and completes
      a bounded smoke task against an allow-listed profile (operator-owned).
- [ ] Smoke run budget ≥360s until first-step Cursor latency is reduced; confirm
      action/observation/screenshot/summary outputs and authenticated screenshot
      GET.

## Out of scope

- Committing or deploying from this document alone.
- Printing or embedding real credentials.
- Mounting Cursor credential directories into the Manager or any worker container.
