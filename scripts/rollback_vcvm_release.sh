#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REMOTE_PATH="/home/coder/cloakbrowser-manager"
DEFAULT_TARGET_HOST="vcvm"
STATE_FILE=".vcvm-release-state.json"

usage() {
  cat <<'EOF'
Usage: scripts/rollback_vcvm_release.sh [--host vcvm] [--remote-path /home/coder/cloakbrowser-manager] [--target-release RELEASE_ID] [--apply]

Dry-run is the default. Rollback is bounded to the previous release recorded in
.vcvm-release-state.json and refuses arbitrary release IDs. --apply currently
fails closed until rollback can restart previous compose, verify health/auth,
validate backup compatibility and update state only after success.
EOF
}

target_host="$DEFAULT_TARGET_HOST"
remote_path="${VCVM_REMOTE_PATH:-$DEFAULT_REMOTE_PATH}"
target_release=""
apply=0

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
    --target-release)
      target_release="${2:-}"
      shift 2
      ;;
    --apply)
      apply=1
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

release_id_re='^[A-Za-z0-9][A-Za-z0-9._-]{11,80}$'
if [[ -n "$target_release" && ( ! "$target_release" =~ $release_id_re || "$target_release" == *".."* ) ]]; then
  echo "Refusing unsafe rollback target." >&2
  exit 64
fi

if [[ "$apply" != "1" ]]; then
  echo "DRY RUN: would validate previous recorded release, backup compatibility, compose restart, health/auth, and state update."
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Refusing --apply: rollback is unavailable until transaction engine re-review is complete." >&2
echo "Run scripts/vcvm_release_transaction.py rollback directly with a fake RemoteExecutor for review-only testing." >&2
exit 78
