# ACPX Host Worker (VCVM)

Host-side ACPX worker provisioning is local, explicit, idempotent, and
secret-safe. The provisioner validates the ACPX runtime and local files, then
writes only the supplied worker key path and systemd unit path.

Pinned ACPX runtime: `0.12.1`. Production installs use the checked-in host
runtime locks only.

## Layout

| Path | Role |
| --- | --- |
| `scripts/acpx_worker.py` | Worker process |
| `scripts/acpx_runner.py` | ACPX command and event adapter |
| `scripts/acpx_runtime_lock.py` | Read-only runtime lock verifier |
| `scripts/provision_acpx_worker.py` | Local secret-safe provisioner |
| `deploy/acpx-runtime/package.json` | Private pinned ACPX npm runtime |
| `deploy/acpx-runtime/package-lock.json` | npm lockfileVersion 3 runtime lock |
| `scripts/requirements-acpx-worker.in` | Exact Python runtime inputs |
| `scripts/requirements-acpx-worker.linux-x86_64.py312.txt` | Production Python lock with hashes |
| `scripts/requirements-acpx-worker.txt` | Legacy non-production compatibility list |
| `deploy/systemd/cloakbrowser-acpx-worker.service.template` | Unit template |
| `docs/ACPX_WORKER.md` | This guide |

## Locked Runtime Install

Build the host ACPX runtime from the checked-in npm lock:

```bash
cd /home/coder/vk-repos/CloakBrowser-Manager-browser-use/deploy/acpx-runtime
npm ci --omit=dev --ignore-scripts --audit=false --fund=false
```

Build the worker virtualenv from the checked-in Linux x86_64 CPython 3.12 lock:

```bash
uv venv --python 3.12 /home/coder/.venvs/acpx-worker
uv pip sync \
  --python /home/coder/.venvs/acpx-worker/bin/python \
  /home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/requirements-acpx-worker.linux-x86_64.py312.txt
```

Before provisioning, verify the locks and installed runtime:

```bash
python3 /home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/acpx_runtime_lock.py \
  --repo /home/coder/vk-repos/CloakBrowser-Manager-browser-use \
  --installed-root /home/coder/vk-repos/CloakBrowser-Manager-browser-use/deploy/acpx-runtime \
  --venv /home/coder/.venvs/acpx-worker
```

The verifier is read-only. It does not install packages, run `systemctl`, SSH,
or mutate npm/uv state. It emits a secret-safe JSON receipt and requires the
ACPX executable path to be exactly
`deploy/acpx-runtime/node_modules/acpx/dist/cli.js` under the repo.

## Required Inputs

All paths must be absolute and supplied on the CLI:

- `--repo`: repo worktree containing `.git` and `scripts/acpx_worker.py`
- `--manager-url`: loopback Manager origin, for example `http://127.0.0.1:18115`
- `--worker-key-file`: local token file, created mode `0600` if absent
- `--venv`: worker virtualenv with Python 3.12 and exact locked packages
- `--unit-output`: local rendered systemd user unit path
- `--permission-policy`: private mode-`0600` ACPX permission policy JSON
- `--mcp-config`: private mode-`0600` ACPX MCP config JSON
- `--capability-dir`: private mode-`0700` run capability directory
- `--acpx`: checked-in runtime executable path whose `--version` output is
  exactly `0.12.1`

The provisioner refuses symlink targets for security-sensitive paths.

## Dry Run

Dry-run validates inputs and returns a JSON receipt without creating the key or
unit:

```bash
python3 /home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/provision_acpx_worker.py \
  --dry-run \
  --repo /home/coder/vk-repos/CloakBrowser-Manager-browser-use \
  --manager-url http://127.0.0.1:18115 \
  --worker-key-file /home/coder/.config/cloakbrowser/acpx-worker.key \
  --venv /home/coder/.venvs/acpx-worker \
  --unit-output /home/coder/.config/systemd/user/cloakbrowser-acpx-worker.service \
  --permission-policy /home/coder/.config/cloakbrowser/acpx-permission-policy.json \
  --mcp-config /home/coder/.config/cloakbrowser/acpx-mcp.json \
  --capability-dir /home/coder/.local/state/cloakbrowser/acpx-capabilities \
  --acpx /home/coder/vk-repos/CloakBrowser-Manager-browser-use/deploy/acpx-runtime/node_modules/acpx/dist/cli.js \
  --worker-id acpx-worker
```

## Apply

Normal apply creates or reuses `cbm_worker_` + 64 lowercase hex in the key file
with mode `0600`, then renders the checked-in systemd template to the explicit
unit output path with mode `0600`.

The rendered unit runs:

- `-m scripts.acpx_worker` from the repo `WorkingDirectory`
- `--token-file`, never `--token`
- `--permission-policy`, `--mcp-config`, `--capability-dir`, and `--acpx`
- `UMask=0077`
- `Restart=on-failure`
- a bounded `PATH` containing `~/.local/bin`, `/usr/local/bin`, `/usr/bin`, and `/bin`

The JSON receipt never includes the worker token. The provisioner does not run
`systemctl`, SSH, `pip`, `npm`, `uv`, or mutate the VCVM deployment. Apply any
receipt to the VCVM only through a separately reviewed deployment step.

## Safe Checks

Do not print the key file. Safe checks:

```bash
stat -c '%a %n' /home/coder/.config/cloakbrowser/acpx-worker.key
systemctl --user cat cloakbrowser-acpx-worker.service | grep -E 'ExecStart|token-file|UMask|WorkingDirectory'
```

Expected: key mode `600`; the unit references the token file path and contains
no `cbm_worker_` token material.
