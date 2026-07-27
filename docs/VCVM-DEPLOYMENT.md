# VCVM Deployment

This deployment path runs the whole CloakBrowser Manager product on the VCVM:
FastAPI, the built React UI, SQLite data, KasmVNC and launched browser profiles
stay inside one Docker service with one persistent Docker volume.

The Mac or iPhone is only a presentation and input client. Do not run a second
Manager, browser profile, database or VNC server on the Mac for this deployment.

## Safety model

- Host: `vcvm`
- Remote path: `/home/coder/cloakbrowser-manager`
- Docker project and container: `cloakbrowser-manager-vcvm`
- Data volume: `cloakbrowser-manager-vcvm-data`
- Manager bind: `127.0.0.1:${MANAGER_PORT:-18115}` on the VCVM only
- Required auth: `AUTH_TOKEN`
- Required policy layer: `ACCESS_CONTROL_ENABLED=1`
- Optional private iPhone access: Tailscale Serve HTTPS after the app proves
  `auth_required=true` and `access_control_enabled=true`

The compose file does not publish any raw VNC port. Browser viewing remains
behind the authenticated Manager proxy.

## Orca host bridge

The Manager container can invoke host Orca `1.4.x` only when these read-only
host paths are mounted and `HOME=/home/coder`:

- `/home/coder/orca`
- `/home/coder/.local`
- `/home/coder/.config/orca`

Environment written into `.env.vcvm` (never inline agent secrets):

| Variable | Value |
| --- | --- |
| `CBM_ORCA_BIN` | `/home/coder/.local/bin/orca-ide` |
| `CBM_ORCA_WORKTREE` | `path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use` |
| `CBM_ORCA_AGENT_WRAPPER` | `/home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/orca_agent_cli.sh` |
| `CBM_BASE_URL` | `http://127.0.0.1:${MANAGER_PORT}` (host-loopback publish) |
| `CBM_AGENT_KEY_FILE` | `/home/coder/.config/cloakbrowser/orca-agent-key` |

The deploy copy under `/home/coder/cloakbrowser-manager` is **not** an
Orca-registered git worktree. Agent terminals must use the vk-repos checkout
above. Place a mode-`600` scoped `cbm_agent_…` key in
`/home/coder/.config/cloakbrowser/orca-agent-key`; never put the key in argv,
compose env values, UI, logs, or Git.

**Do not mount** the scoped key directory
(`/home/coder/.config/cloakbrowser`) or the vk-repos checkout / host wrapper
(`…/scripts/orca_agent_cli.sh`) into the Manager container. `start_session`
still sends the fixed **host** wrapper path through host Orca
`terminal.create`; agent processes read the host key file outside the
container.

Readiness is split for security:

1. **Host preflight** (`scripts/vcvm_orca_preflight.py`, before compose): fail
   closed on missing/bad host Orca paths, host agent wrapper
   (`scripts/orca_agent_cli.sh` readable+executable), agent key file (`0600` /
   `cbm_agent_…` syntax; contents never printed), unreachable runtime, or
   failed `worktree show` for the registered vk-repos checkout. Wrapper/key
   readiness stays host-side.
2. **Container `capabilities.available`**: only what the Manager can prove
   without side effects or host-only Path probes — Orca binary reachable,
   bounded `orca status` ready, and configured registered `worktree show`
   succeeds. Missing container-local wrapper/key paths must **not** disable
   Launch.

Deploy preflight fails closed **before** `docker compose up` when:

1. Host Orca paths or the `orca-ide` binary are missing/non-executable.
2. The host agent wrapper (`…/scripts/orca_agent_cli.sh`) is missing or not
   readable/executable (never mounted into the container).
3. The agent key file is missing, not a regular file, not mode `0600`, not
   owned/readable by the current host user, empty, or fails `cbm_agent_` +
   safe-token syntax (contents are never printed).
4. Orca runtime is unreachable / not ready.
5. `orca-ide worktree show --worktree path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use --json`
   does not succeed for the registered vk-repos checkout.

Orca session **close** is owner-only: bootstrap admins cannot close another
agent's terminal session.

Profile health can optionally enrich its browser-path observation with the
existing VCVM proxychecker. This dependency is not part of `/health`, and a
stopped or unavailable proxychecker does not make the Manager unhealthy.

The current acceptance deployment uses custom loopback port `18116` to avoid a
legacy preview on `18115`; the documented default remains `18115`. Use the same
port consistently for deploy, smoke checks and an SSH tunnel.

## Worktree audit receipts

Agent cleanup must start with a read-only worktree receipt, never with removal.
Run the audit from any checkout path:

```bash
python3 scripts/cbm_worktree_audit.py --root /home/coder/vk-repos/CloakBrowser-Manager-browser-use
python3 scripts/cbm_agent_ctl.py worktree-audit --root /home/coder/vk-repos/CloakBrowser-Manager-browser-use
```

The receipt is JSON only and includes secret-safe display paths, path hashes,
branch, commit, changed modules, stale activity, size, upstream/merge state,
overlap markers and cleanup candidacy. A worktree is a cleanup candidate only
when it is clean, already merged into the target ref, past the retention window,
not known to have unpushed commits, and not oversized. The audit never deletes
files or runs cleanup.

Disk policy is fail-closed for VCVM work:

- `< 16 GiB` free: warn.
- `< 12 GiB` free: block creating new worktrees.
- `< 8 GiB` free: block release/deploy.

## Release manifest dry-run gate

VCVM release operations are dry-run by default. A normal invocation builds a
local release manifest, checks the clean git commit, branch, artifact hashes,
migration set and at least 8 GiB measured free capacity, then exits before SSH,
rsync, compose, restart, cleanup, prune or symlink mutation. A dry-run
measurement is marked as `local` or `override`; it is not represented as exact
VCVM capacity:

```bash
./scripts/deploy_vcvm.sh
```

The manifest contract is versioned in
[`contracts/vcvm-release-v1.json`](./contracts/vcvm-release-v1.json). It
records:

- source branch, commit, named source remote and non-credentialed remote URL;
- release ID, created time, target host, release directory and `current`
  symlink path;
- `measurement_source`, `total_bytes`, `used_bytes`, `free_bytes` and
  `free_gib`;
- artifact SHA-256 hashes and the detected migration set;
- a deterministic `artifact_set_sha256` over the selected release artifacts;
- release policy flags for the dry-run manifest gate.

`--apply` remains unavailable until the transaction engine re-review gate is
green for the live VCVM path.
The independent engine can be tested with fake executors, but the wrapper does
not delegate to live SSH, transfer, Docker or systemd operations.

Legacy live flags such as `--auth-token-file` and `--serve-private` are rejected
while apply is unavailable; they are not accepted as successful dry-run no-ops.

Rollback is also dry-run by default:

```bash
./scripts/rollback_vcvm_release.sh
```

`rollback_vcvm_release.sh --apply` also remains unavailable until the
transaction engine re-review gate is green for rollback.

## Deploy

Create a dry-run release receipt from a clean checkout with the authorized fork
remote configured:

```bash
./scripts/deploy_vcvm.sh \
  --source-remote fork \
  --expected-source-remote https://github.com/Martin-Hausleitner/CloakBrowser-Manager.git
```

There is no wrapper-enabled live VCVM deploy command in this slice. `--apply`
currently fails closed on the transaction re-review gate instead of performing
a partial release.

Optional Browser-Use worker bootstrap uses a separate `.env.worker.vcvm` file
(only `CBM_WORKER_ID` / `CBM_WORKER_TOKEN`) attached by Compose
`env_file` with `required: false`. Absence remains valid; the backend leaves
worker auth disabled until that file is provisioned. See
[BROWSER_USE_WORKER.md](./BROWSER_USE_WORKER.md). `deploy_vcvm.sh` does not
manage the worker env file.

Optional ACPX worker bootstrap is local-only in this slice: install the pinned
host runtime with
`npm ci --omit=dev --ignore-scripts --audit=false --fund=false` from
`deploy/acpx-runtime`, sync the worker venv with
`uv pip sync scripts/requirements-acpx-worker.linux-x86_64.py312.txt`, verify
both with `scripts/acpx_runtime_lock.py`, then validate and render a secret-safe
receipt with `scripts/provision_acpx_worker.py`. The provisioner creates only
the explicit local key/unit paths and does not run `systemctl`, SSH, `pip`,
`uv`, or `npm`. See [ACPX_WORKER.md](./ACPX_WORKER.md).

The independent VCVM release transaction also has an explicit
`bootstrap_acpx` engine mode for the first ACPX promotion candidate. It is not
part of the public deploy/rollback wrappers. Normal releases still require the
existing ACPX preflight to pass before any mutation. In bootstrap mode the
engine first probes for genuine absence only: missing ACPX binary, systemd
unit, key, venv or capability directory. Existing but stale, misconfigured,
bad-auth or wrong-version ACPX state remains blocking.

Direct first-rollout ACPX bootstrap must use the reviewed transaction engine
path, not the public wrapper:

```bash
python3 scripts/vcvm_release_transaction.py release \
  --source-root . \
  --source-remote fork \
  --expected-source-remote https://github.com/Martin-Hausleitner/CloakBrowser-Manager.git \
  --expected-current-worker-commit <full-40-hex-current-browser-use-worker-commit> \
  --bootstrap-acpx \
  --apply
```

Omitting `--apply` keeps this command in dry-run mode and does not connect over
SSH. Live bootstrap also requires the exact current Browser-Use worker commit
and exact authorized source remote before the transaction constructs an SSH
executor.

When that absence gate passes, the transaction stages release-specific ACPX
npm/Python runtimes from the checked-in locks, provisions a temporary
candidate-bound worker against Manager `127.0.0.1:18116`, and verifies worker
presence plus adapter readiness before stopping the old Manager or workers.
After the normal Manager switch, the worker is promoted to
`127.0.0.1:18115` and verified with the release runtime. Any failure cleans up
only release-specific temporary ACPX artifacts; failures after quiesce also
restore the captured old Manager/runtime/unit state before returning failure.

For the first ACPX rollout only, `--bootstrap-acpx --apply` also accepts the
legacy live Manager image if it has an immutable image id/digest but no OCI
revision label. The transaction records `previous_revision_available=false`
in the capture and `revision_available=false` in `previous_runtime`; the
revision stays empty and is never synthesized from another value. Normal
release mode still requires the Manager revision label before any remote
mutation. Rollback of an unlabeled previous runtime verifies exact immutable
image id and digest, and skips only revision-label equality.

Manager verification requests always include the expected immutable image id,
the expected commit, and whether that revision label is available. The helper
response must match the image id exactly; revision equality is required only
when `revision_available=true`. Orca verification is also structured: the
helper must report `ok=true`, `runtime_state=ready`, and `graph_state=ready`
before the transaction can continue.

### Optional VCVM-local proxychecker

First ensure the already-authorized proxychecker listens only on the VCVM's
Docker bridge host address, not on a public or Tailnet-wide socket. The Manager
container reaches that boundary through the explicit `host.docker.internal`
host-gateway mapping.

The manifest dry-run keeps validating the fixed local service URL:

```bash
PROXYCHECKER_URL=http://host.docker.internal:18899 \
  ./scripts/deploy_vcvm.sh
```

The deploy script defaults to `http://host.docker.internal:18899` when
`PROXYCHECKER_URL` is unset, rejects credentials/public hosts/arbitrary paths,
and keeps the validated URL plus the single `host.docker.internal` allow-list
entry in the dry-run manifest boundary. Export
`PROXYCHECKER_URL=` (empty) to disable enrichment explicitly; browser
reachability, fingerprint consistency and conservative BrowserScan
classification continue independently.

## Private Tailscale HTTPS

Tailscale Serve publication is not performed by this dry-run gate. It is one of
the required live service receipts before `--apply` can be enabled.

## Validation

Run the local deployment-surface checks before changing the VCVM:

```bash
python3 scripts/test_vcvm_deployment.py
pytest scripts/test_vcvm_deployment.py scripts/test_cbm_release_manifest.py -q
```

After a future live implementation, run a remote smoke after deploy:

```bash
ssh vcvm 'curl -fsS http://127.0.0.1:18115/health'
ssh vcvm 'curl -fsS http://127.0.0.1:18115/api/auth/status'
```

The second command must report `auth_required: true` and
`access_control_enabled: true`; do not publish a URL if it does not.

For Mac-only validation of a loopback Manager, keep the runtime on the VCVM and
forward only the UI connection:

```bash
ssh -N -L 18118:127.0.0.1:18116 vcvm
```

Then open `http://127.0.0.1:18118/`. This URL is a client tunnel, not a local
Manager. For iPhone access, use private Tailscale Serve HTTPS once the tailnet
administrator has enabled Serve; never replace it with a raw Tailnet HTTP bind.
