#!/usr/bin/env bash
# Safe Orca terminal launcher for CloakBrowser Manager agent CLIs.
# Invoked by Manager via: orca terminal create --command "<this> <agent>"
# Never accepts secrets on argv. Never prints key material.
set -euo pipefail

AGENT="${1:-}"
if [[ $# -lt 1 ]]; then
  echo "usage: orca_agent_cli.sh <cursor-agent|grok|agy|codex> [args...]" >&2
  exit 64
fi
shift

case "$AGENT" in
  cursor-agent|grok|agy|codex)
    ;;
  *)
    echo "refusing unsupported agent CLI" >&2
    exit 64
    ;;
esac

# Host-loopback Manager (published on VCVM). Override via environment only.
export CBM_BASE_URL="${CBM_BASE_URL:-http://127.0.0.1:18115}"
# Key file path only — never load or echo contents here.
export CBM_AGENT_KEY_FILE="${CBM_AGENT_KEY_FILE:-/home/coder/.config/cloakbrowser/orca-agent-key}"

if ! command -v "$AGENT" >/dev/null 2>&1; then
  echo "agent CLI is not available on PATH" >&2
  exit 69
fi

case "$AGENT" in
  grok)
    # Keep Grok's screen in Orca's scrollback so the web UI can mirror it.
    exec grok --no-alt-screen "$@"
    ;;
  agy)
    # AGY is already authenticated on VCVM and stays interactive in the PTY.
    exec agy "$@"
    ;;
  cursor-agent|codex)
    exec "$AGENT" "$@"
    ;;
esac
