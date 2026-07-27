#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="cloakbrowser-manager-vcvm"
COMPOSE_FILE="docker-compose.vcvm.yml"
DEFAULT_REMOTE_PATH="/home/coder/cloakbrowser-manager"
DEFAULT_TARGET_HOST="vcvm"
DEFAULT_MANAGER_PORT="18115"
DEFAULT_TAILSCALE_HTTPS_PORT="443"
DEFAULT_MIN_FREE_DISK_GIB="8"
MANAGED_MARKER=".cloakbrowser-manager-vcvm-managed"

usage() {
  cat <<'EOF'
Usage: scripts/deploy_vcvm.sh [--host vcvm] [--remote-path /home/coder/cloakbrowser-manager] [--port 18115] [--source-remote fork] [--expected-source-remote URL] [--apply]

Plan or deploy CloakBrowser Manager to the authorized VCVM Docker host.

Required:
  The default mode is a read-only dry run and never opens SSH, rsyncs, restarts,
  or mutates the VCVM. --apply currently fails closed until the full remote
  release, backup, build, verification and rollback contract is implemented.
  Legacy --auth-token-file and --serve-private inputs are rejected while live
  apply is unavailable; they are never accepted as successful no-op inputs.

Safety:
  - The Manager binds only to 127.0.0.1 on the VCVM.
  - ACCESS_CONTROL_ENABLED is always forced to 1.
  - Persistent browser data stays in Docker volume cloakbrowser-manager-vcvm-data.
  - Deployment refuses to start with less than 8 GiB free on the VCVM volume.
  - Release manifests record the exact measured capacity, source commit,
    branch, artifact hashes and migration set.
  - No shared-host cleanup, prune, restart or live deploy runs.
  - Optional Tailscale Serve is added only after auth/access checks pass.
  - Host Orca bridge requires /home/coder/orca, /home/coder/.local, and
    /home/coder/.config/orca (read-only mounts). Preflight fails closed if absent.
  - Agent keys stay in /home/coder/.config/cloakbrowser/orca-agent-key (mode 600);
    never written into .env.vcvm argv, UI, logs, or Git.
EOF
}

target_host="$DEFAULT_TARGET_HOST"
remote_path="${VCVM_REMOTE_PATH:-$DEFAULT_REMOTE_PATH}"
manager_port="${MANAGER_PORT:-$DEFAULT_MANAGER_PORT}"
auth_token_file="${AUTH_TOKEN_FILE:-}"
serve_private=0
apply=0
source_root=""
source_remote="${CBM_RELEASE_SOURCE_REMOTE:-fork}"
expected_source_remote="${CBM_EXPECTED_SOURCE_REMOTE:-https://github.com/Martin-Hausleitner/CloakBrowser-Manager.git}"
disk_free_bytes="${CBM_RELEASE_FREE_BYTES:-}"
tailscale_https_port="${TAILSCALE_HTTPS_PORT:-$DEFAULT_TAILSCALE_HTTPS_PORT}"
proxychecker_url="${PROXYCHECKER_URL-http://host.docker.internal:18899}"
min_free_disk_gib="${VCVM_MIN_FREE_DISK_GIB:-$DEFAULT_MIN_FREE_DISK_GIB}"

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
    --apply)
      apply=1
      shift
      ;;
    --source-root)
      source_root="${2:-}"
      shift 2
      ;;
    --source-remote)
      source_remote="${2:-}"
      shift 2
      ;;
    --expected-source-remote)
      expected_source_remote="${2:-}"
      shift 2
      ;;
    --disk-free-bytes)
      disk_free_bytes="${2:-}"
      shift 2
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

if [[ ! "$min_free_disk_gib" =~ ^[1-9][0-9]?$ ]] || (( min_free_disk_gib > 64 )); then
  echo "Refusing invalid VCVM_MIN_FREE_DISK_GIB; expected 1-64." >&2
  exit 64
fi

for disk_value in "$disk_free_bytes"; do
  if [[ -n "$disk_value" && ! "$disk_value" =~ ^[0-9]+$ ]]; then
    echo "Refusing invalid disk byte override value." >&2
    exit 64
  fi
done

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

if [[ -n "$auth_token_file" || "$serve_private" == "1" ]]; then
  echo "Refusing legacy live-deploy flags while VCVM apply is unavailable." >&2
  exit 64
fi

if [[ "$apply" == "1" ]]; then
  echo "Refusing --apply: live VCVM release is unavailable until CBM-022 remote verification is complete." >&2
  echo "Required missing gates: remote source-hash verification, DB/profile backup receipt, build-before-switch, failure rollback, worker/runtime skew checks, and service receipts." >&2
  exit 78
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "$source_root" ]]; then
  source_root="$repo_root"
fi
source_root="$(cd "$source_root" && pwd)"

if [[ ! -f "$source_root/$COMPOSE_FILE" || ! -f "$source_root/Dockerfile" || ! -d "$source_root/backend" || ! -d "$source_root/frontend" ]]; then
  echo "Refusing to deploy from an incomplete repository checkout." >&2
  exit 72
fi

remote_disk_args=()

manifest_args=(
  "$repo_root/scripts/cbm_release_manifest.py"
  --source-root "$source_root"
  --disk-path "$source_root"
  --host "$target_host"
  --remote-path "$remote_path"
  --source-remote "$source_remote"
  --require-migrations
)
if [[ -n "$expected_source_remote" ]]; then
  manifest_args+=(--expected-source-remote "$expected_source_remote")
fi
if [[ -n "$disk_free_bytes" ]]; then
  manifest_args+=(--disk-free-bytes "$disk_free_bytes")
fi
if [[ ${#remote_disk_args[@]} -gt 0 ]]; then
  manifest_args+=("${remote_disk_args[@]}")
fi
manifest_json="$(python3 "${manifest_args[@]}")"
release_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["release_id"])' <<<"$manifest_json")"

echo "DRY RUN: VCVM release manifest passed for $release_id."
echo "DRY RUN: no SSH, rsync, compose, restart, cleanup, prune, or symlink mutation was performed."
