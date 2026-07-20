#!/usr/bin/env bash
# Configure a private Tailscale HTTPS proxy only for an already protected
# CloakBrowser Manager. This deliberately refuses open or legacy deployments.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/serve_private_tailnet.sh (--check|--apply) [http://127.0.0.1:PORT]

--check  Verify that the local Manager requires authentication and has scoped
         access control enabled. It never changes Tailscale configuration.
--apply  Run the same checks, then configure private HTTPS Tailscale Serve.
         It never enables Funnel or binds the Manager outside localhost.
EOF
}

mode="${1:-}"
target="${2:-http://127.0.0.1:8080}"

if [[ "$mode" != "--check" && "$mode" != "--apply" ]]; then
  usage >&2
  exit 64
fi

if [[ ! "$target" =~ ^http://127\.0\.0\.1:[0-9]{1,5}/?$ ]]; then
  echo "Refusing non-loopback target: $target" >&2
  exit 64
fi
target="${target%/}"

for command in curl jq tailscale; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 69
  fi
done

status="$(curl --fail --silent --show-error --max-time 10 "$target/api/auth/status")"
if ! jq -e '.auth_required == true and .access_control_enabled == true' >/dev/null <<<"$status"; then
  echo "Refusing to expose $target: AUTH_TOKEN and ACCESS_CONTROL_ENABLED=1 are both required." >&2
  exit 77
fi

echo "Verified protected local Manager: $target"
if [[ "$mode" == "--check" ]]; then
  echo "Preflight only; Tailscale configuration was not changed."
  exit 0
fi

existing_config="$(tailscale serve status --json)"
if ! jq -e 'type == "object" and length == 0' >/dev/null <<<"$existing_config"; then
  echo "Refusing to overwrite an existing Tailscale Serve configuration. Inspect it with: tailscale serve status" >&2
  exit 73
fi

if ! apply_output="$(tailscale serve --bg --https=443 "$target" 2>&1)"; then
  printf '%s\n' "$apply_output" >&2
  echo "Tailscale Serve could not be configured." >&2
  exit 78
fi
printf '%s\n' "$apply_output"

configured="$(tailscale serve status --json)"
if ! jq -e 'type == "object" and length > 0' >/dev/null <<<"$configured"; then
  echo "Tailscale Serve did not create a configuration; verify that Serve is enabled for this tailnet." >&2
  exit 78
fi

tailscale serve status
