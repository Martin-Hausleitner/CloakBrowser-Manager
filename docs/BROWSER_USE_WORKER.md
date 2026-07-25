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
   `%`), venv Python, `scripts/browser_use_worker.py`, loopback `--manager-url`
   origin, `--worker-id`, and `--token-file` (never `--token`, never an inlined
   secret). `UMask=0077`, `Restart=on-failure`, and `PATH` including
   `~/.local/bin` for `cursor-agent`.

Worker CLI flags used by the unit (current `browser_use_worker.py`):

- `--manager-url`
- `--worker-id`
- `--token-file`

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
- [ ] Unit `ExecStart` uses venv Python + `browser_use_worker.py` with
      `--manager-url` (loopback), `--worker-id`, `--token-file`; `UMask=0077`;
      `Restart=on-failure`; `PATH` includes `~/.local/bin`.
- [ ] No worker token appears in provisioner stdout, the unit file, journal
      snippets used for triage, or documentation.
- [ ] `scripts/deploy_vcvm.sh` remains untouched by worker provisioning.
- [ ] Cursor credentials remain on the host; no container bind of Cursor
      credential directories for this worker.
- [ ] Worker claims a `browser-use` run from Manager internal APIs and completes
      a bounded smoke task against an allow-listed profile (operator-owned).

## Out of scope

- Committing or deploying from this document alone.
- Printing or embedding real credentials.
- Mounting Cursor credential directories into the Manager or any worker container.
