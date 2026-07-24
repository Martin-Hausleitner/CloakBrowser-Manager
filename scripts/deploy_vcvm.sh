#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="cloakbrowser-manager-vcvm"
COMPOSE_FILE="docker-compose.vcvm.yml"
DEFAULT_REMOTE_PATH="/home/coder/cloakbrowser-manager"
DEFAULT_TARGET_HOST="vcvm"
DEFAULT_MANAGER_PORT="18115"
DEFAULT_TAILSCALE_HTTPS_PORT="443"
MANAGED_MARKER=".cloakbrowser-manager-vcvm-managed"

usage() {
  cat <<'EOF'
Usage: scripts/deploy_vcvm.sh [--host vcvm] [--remote-path /home/coder/cloakbrowser-manager] [--port 18115] [--auth-token-file PATH] [--serve-private]

Deploy CloakBrowser Manager to the authorized VCVM Docker host.

Required:
  AUTH_TOKEN or --auth-token-file PATH. The token is sent over SSH and written
  to .env.vcvm on the VCVM with mode 600. It is never printed.

Safety:
  - The Manager binds only to 127.0.0.1 on the VCVM.
  - ACCESS_CONTROL_ENABLED is always forced to 1.
  - Persistent browser data stays in Docker volume cloakbrowser-manager-vcvm-data.
  - Optional Tailscale Serve is added only after auth/access checks pass.
EOF
}

target_host="$DEFAULT_TARGET_HOST"
remote_path="${VCVM_REMOTE_PATH:-$DEFAULT_REMOTE_PATH}"
manager_port="${MANAGER_PORT:-$DEFAULT_MANAGER_PORT}"
auth_token_file="${AUTH_TOKEN_FILE:-}"
serve_private=0
tailscale_https_port="${TAILSCALE_HTTPS_PORT:-$DEFAULT_TAILSCALE_HTTPS_PORT}"
proxychecker_url="${PROXYCHECKER_URL-http://host.docker.internal:18899}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      target_host="${2:-}"
      shift 2
      ;;
    --remote-path)
      remote_path="${2:-}"
      shift 2
      ;;
    --port)
      manager_port="${2:-}"
      shift 2
      ;;
    --auth-token-file)
      auth_token_file="${2:-}"
      shift 2
      ;;
    --serve-private)
      serve_private=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
done

if [[ "$target_host" != "vcvm" && "$target_host" != *"@vcvm" ]]; then
  echo "Refusing unexpected target host: $target_host" >&2
  exit 64
fi

if [[ "$remote_path" != "$DEFAULT_REMOTE_PATH" ]]; then
  echo "Refusing unexpected remote path: $remote_path" >&2
  echo "Expected exactly: $DEFAULT_REMOTE_PATH" >&2
  exit 64
fi

if [[ ! "$manager_port" =~ ^[0-9]{2,5}$ ]] || (( manager_port < 1024 || manager_port > 65535 )); then
  echo "Refusing invalid manager port: $manager_port" >&2
  exit 64
fi

if [[ ! "$tailscale_https_port" =~ ^[0-9]{2,5}$ ]] || (( tailscale_https_port < 1024 && tailscale_https_port != 443 )) || (( tailscale_https_port > 65535 )); then
  echo "Refusing invalid Tailscale HTTPS port: $tailscale_https_port" >&2
  exit 64
fi

if [[ -n "$proxychecker_url" ]]; then
  if [[ ! "$proxychecker_url" =~ ^http://host\.docker\.internal:([0-9]{2,5})$ ]]; then
    echo "Refusing PROXYCHECKER_URL outside the VCVM Docker host gateway." >&2
    exit 64
  fi
  proxychecker_port="${BASH_REMATCH[1]}"
  if (( proxychecker_port < 1024 || proxychecker_port > 65535 )); then
    echo "Refusing invalid proxychecker port." >&2
    exit 64
  fi
fi

for command in ssh rsync; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required local command: $command" >&2
    exit 69
  fi
done

if [[ -n "$auth_token_file" ]]; then
  if [[ ! -r "$auth_token_file" ]]; then
    echo "Cannot read auth token file." >&2
    exit 66
  fi
  auth_token="$(tr -d '\r\n' < "$auth_token_file")"
else
  auth_token="${AUTH_TOKEN:-}"
fi

if [[ ${#auth_token} -lt 24 ]]; then
  echo "Refusing weak or missing AUTH_TOKEN; use at least 24 characters." >&2
  exit 77
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -f "$COMPOSE_FILE" || ! -f Dockerfile || ! -d backend || ! -d frontend ]]; then
  echo "Refusing to deploy from an incomplete repository checkout." >&2
  exit 72
fi

ssh "$target_host" "bash -s" <<REMOTE_PREFLIGHT
set -euo pipefail
remote_path='$remote_path'
marker="\$remote_path/$MANAGED_MARKER"
mkdir -p "\$remote_path"
if [[ -f "\$marker" ]]; then
  if ! grep -qx 'project=$PROJECT_NAME' "\$marker"; then
    echo "Refusing remote path with mismatched managed marker." >&2
    exit 73
  fi
else
  if find "\$remote_path" -mindepth 1 -maxdepth 1 | read -r _; then
    echo "Refusing to delete or overwrite an unmanaged non-empty remote path: \$remote_path" >&2
    echo "Expected marker: \$marker" >&2
    exit 73
  fi
  {
    printf '%s\n' 'managed-by=cloakbrowser-manager-vcvm-deploy'
    printf '%s\n' 'project=$PROJECT_NAME'
  } > "\$marker"
fi
REMOTE_PREFLIGHT

rsync -az --delete \
  --exclude '.git' \
  --exclude "$MANAGED_MARKER" \
  --exclude '.env.vcvm' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '*.env' \
  --exclude '*.token' \
  --exclude '*token*' \
  --exclude '.venv/' \
  --exclude 'backend/.venv/' \
  --exclude 'node_modules/' \
  --exclude 'frontend/node_modules/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude 'frontend/dist/' \
  --exclude 'dist/' \
  --exclude 'backend/.data/' \
  --exclude 'artifacts/' \
  --exclude 'frontend/tsconfig.tsbuildinfo' \
  --exclude 'benchmarks/' \
  --exclude 'docker-compose.guacamole-benchmark.yml' \
  --exclude 'scripts/guacamole_benchmark_config.json' \
  --exclude 'scripts/run_guacamole_benchmark.sh' \
  ./ "$target_host:$remote_path/"

{
  printf 'MANAGER_PORT=%s\n' "$manager_port"
  printf 'ACCESS_CONTROL_ENABLED=1\n'
  printf 'AUTH_TOKEN=%s\n' "$auth_token"
  printf 'PROXYCHECKER_URL=%s\n' "$proxychecker_url"
  printf 'PROXYCHECKER_ALLOWED_HOSTS=host.docker.internal\n'
  printf 'EXTENSION_CATALOG_DIR=/data/extension-catalog\n'
} | ssh "$target_host" "umask 077; cat > '$remote_path/.env.vcvm'"

ssh "$target_host" "cd '$remote_path' && docker compose --env-file .env.vcvm -p '$PROJECT_NAME' -f '$COMPOSE_FILE' up -d --build --remove-orphans"

# Ensure Comet/harvested extension binaries can land on the data volume.
ssh "$target_host" "docker exec cloakbrowser-manager-vcvm mkdir -p /data/extension-catalog" >/dev/null 2>&1 || true

ssh "$target_host" "bash -s" <<REMOTE_CHECK
set -euo pipefail
target='http://127.0.0.1:$manager_port'
for i in \$(seq 1 60); do
  if curl --fail --silent --max-time 5 "\$target/health" >/dev/null; then
    break
  fi
  sleep 2
  if [[ "\$i" == "60" ]]; then
    docker compose --env-file '$remote_path/.env.vcvm' -p '$PROJECT_NAME' -f '$remote_path/$COMPOSE_FILE' logs --no-color --tail=160 >&2 || true
    exit 70
  fi
done

status="\$(curl --fail --silent --max-time 10 "\$target/api/auth/status")"
if ! jq -e '.auth_required == true and .access_control_enabled == true' >/dev/null <<<"\$status"; then
  echo "VCVM Manager is not protected; stopping before publishing." >&2
  exit 77
fi
echo "VCVM Manager is healthy and protected at \$target"
REMOTE_CHECK

if [[ "$serve_private" == "1" ]]; then
  ssh "$target_host" "bash -s" <<REMOTE_SERVE
set -euo pipefail
target='http://127.0.0.1:$manager_port'
https_port='$tailscale_https_port'

for command in curl jq tailscale timeout; do
  if ! command -v "\$command" >/dev/null 2>&1; then
    echo "Missing required VCVM command for private HTTPS: \$command" >&2
    exit 69
  fi
done

status="\$(curl --fail --silent --max-time 10 "\$target/api/auth/status")"
if ! jq -e '.auth_required == true and .access_control_enabled == true' >/dev/null <<<"\$status"; then
  echo "Refusing private HTTPS because auth/access is not enforced." >&2
  exit 77
fi

existing="\$(tailscale serve status --json 2>/dev/null || printf '{}')"
if jq -e --arg port "\$https_port" '
  ((.TCP // {}) | has(\$port)) or
  (((.Web // {}) | keys) | any(endswith(":" + \$port)))
' >/dev/null <<<"\$existing"; then
  echo "Refusing to replace existing Tailscale Serve HTTPS port \$https_port." >&2
  exit 73
fi

if ! serve_output="\$(timeout 30s tailscale serve --bg --https="\$https_port" "\$target" 2>&1)"; then
  printf '%s\n' "\$serve_output" | sed -E 's#https://login\.tailscale\.com/[^[:space:]]+#<tailscale-admin-enable-url>#g' >&2
  echo "Tailscale Serve private HTTPS was not configured." >&2
  exit 78
fi
printf '%s\n' "\$serve_output" | sed -E 's#https://login\.tailscale\.com/[^[:space:]]+#<tailscale-admin-enable-url>#g'
tailscale serve status
REMOTE_SERVE
fi

echo "Deployment complete. Open the VCVM-local Manager through SSH or private Tailscale Serve."
