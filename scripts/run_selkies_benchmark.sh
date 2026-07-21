#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.selkies-benchmark.yml"
CONFIG_FILE="$ROOT_DIR/scripts/selkies_benchmark_config.json"
OUTPUT_DIR="${1:-$ROOT_DIR/artifacts/selkies-benchmark/$(date -u +%Y%m%dT%H%M%SZ)}"
ITERATIONS="${SELKIES_BENCHMARK_ITERATIONS:-5}"
TIMEOUT_SECONDS="${SELKIES_BENCHMARK_TIMEOUT:-60}"
PROJECT_NAME="cloak-selkies-benchmark"

cleanup() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
}

if [[ "${SELKIES_BENCHMARK_KEEP_RUNNING:-0}" != "1" ]]; then
  trap cleanup EXIT
fi

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --quiet-pull

deadline=$((SECONDS + TIMEOUT_SECONDS))
until curl --fail --silent --show-error --max-time 2 http://127.0.0.1:18122/ >/dev/null; do
  if (( SECONDS >= deadline )); then
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --no-color --tail=120 >&2 || true
    echo "Selkies HTTP shell did not become ready on http://127.0.0.1:18122/ within ${TIMEOUT_SECONDS}s" >&2
    exit 1
  fi
  sleep 1
done

until python3 - <<'PY'
import base64
import hashlib
import socket
import time

key = base64.b64encode(hashlib.sha256(str(time.time_ns()).encode()).digest()[:16]).decode()
request = (
    "GET /websockets HTTP/1.1\r\n"
    "Host: 127.0.0.1:18122\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    "Origin: http://127.0.0.1:18122\r\n\r\n"
).encode("ascii")
try:
    with socket.create_connection(("127.0.0.1", 18122), timeout=2) as sock:
        sock.sendall(request)
        response = sock.recv(512).decode("iso-8859-1", errors="replace")
except OSError:
    raise SystemExit(1)
status_line = response.splitlines()[0] if response.splitlines() else ""
raise SystemExit(0 if " 101 " in status_line else 1)
PY
do
  if (( SECONDS >= deadline )); then
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --no-color --tail=160 >&2 || true
    echo "Selkies WebSocket did not become ready on ws://127.0.0.1:18122/websockets within ${TIMEOUT_SECONDS}s" >&2
    exit 1
  fi
  sleep 1
done

python3 "$ROOT_DIR/scripts/streaming_benchmark_runner.py" \
  --config "$CONFIG_FILE" \
  --output-dir "$OUTPUT_DIR" \
  --iterations "$ITERATIONS" \
  --timeout "$TIMEOUT_SECONDS" \
  --strict

echo "Selkies benchmark report: $OUTPUT_DIR/streaming-benchmark-report.json"
