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

## Deploy

Create a long bootstrap token in a local secret file with mode `600`. The token
is sent to the VCVM over SSH and written to
`/home/coder/cloakbrowser-manager/.env.vcvm` with mode `600`.

```bash
mkdir -p ~/.config/cloakbrowser
openssl rand -base64 48 > ~/.config/cloakbrowser/vcvm-auth-token
chmod 600 ~/.config/cloakbrowser/vcvm-auth-token
./scripts/deploy_vcvm.sh --auth-token-file ~/.config/cloakbrowser/vcvm-auth-token
```

The script syncs the current checkout to the VCVM, builds the Docker image on
the VCVM, starts the stack and checks:

1. `/health` answers locally on the VCVM.
2. `/api/auth/status` reports required auth.
3. `/api/auth/status` reports access control enabled.

Optional Browser-Use worker bootstrap uses a separate `.env.worker.vcvm` file
(only `CBM_WORKER_ID` / `CBM_WORKER_TOKEN`) attached by Compose
`env_file` with `required: false`. Absence remains valid; the backend leaves
worker auth disabled until that file is provisioned. See
[BROWSER_USE_WORKER.md](./BROWSER_USE_WORKER.md). `deploy_vcvm.sh` does not
manage the worker env file.

### Optional VCVM-local proxychecker

First ensure the already-authorized proxychecker listens only on the VCVM's
Docker bridge host address, not on a public or Tailnet-wide socket. The Manager
container reaches that boundary through the explicit `host.docker.internal`
host-gateway mapping.

Then deploy with the fixed local service URL:

```bash
PROXYCHECKER_URL=http://host.docker.internal:18899 \
  ./scripts/deploy_vcvm.sh --auth-token-file ~/.config/cloakbrowser/vcvm-auth-token
```

The deploy script defaults to `http://host.docker.internal:18899` when
`PROXYCHECKER_URL` is unset, rejects credentials/public hosts/arbitrary paths,
and writes only the validated URL plus the single `host.docker.internal`
allow-list entry into the mode-600 VCVM env file. Export
`PROXYCHECKER_URL=` (empty) to disable enrichment explicitly; browser
reachability, fingerprint consistency and conservative BrowserScan
classification continue independently.

## Private Tailscale HTTPS

If Tailscale Serve is enabled for the tailnet and a private HTTPS port is free:

```bash
./scripts/deploy_vcvm.sh --auth-token-file ~/.config/cloakbrowser/vcvm-auth-token --serve-private
```

Use a different private HTTPS port if `443` is already configured:

```bash
TAILSCALE_HTTPS_PORT=8443 ./scripts/deploy_vcvm.sh --auth-token-file ~/.config/cloakbrowser/vcvm-auth-token --serve-private
```

The script refuses to replace an existing Serve entry on the selected HTTPS
port. It also refuses to publish unless the protected Manager is already running
with scoped access control.

## Validation

Run the local deployment-surface checks before changing the VCVM:

```bash
python3 scripts/test_vcvm_deployment.py
```

Run a remote smoke after deploy:

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
