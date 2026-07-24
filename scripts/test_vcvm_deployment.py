#!/usr/bin/env python3
"""Validate the VCVM deployment surface without exposing secrets."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.vcvm.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_vcvm.sh"
DOC_FILE = ROOT / "docs" / "VCVM-DEPLOYMENT.md"
DOCKERIGNORE_FILE = ROOT / ".dockerignore"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compose_config() -> dict:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as env_file:
        env_file.write("AUTH_TOKEN=unit-test-token-with-safe-length\n")
        env_file.write("MANAGER_PORT=18115\n")
        env_file.write("VCVM_CPUS=16.0\n")
        env_file.write("VCVM_MEMORY_LIMIT=32g\n")
        env_file.write("VCVM_SHM_SIZE=2gb\n")
        env_file.write("PROXYCHECKER_URL=http://host.docker.internal:18899\n")
        env_file.write("PROXYCHECKER_ALLOWED_HOSTS=host.docker.internal\n")
        env_path = pathlib.Path(env_file.name)
    try:
        result = run(
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        )
    finally:
        env_path.unlink(missing_ok=True)
    return json.loads(result.stdout)


def compose_quiet() -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as env_file:
        env_file.write("AUTH_TOKEN=unit-test-token-with-safe-length\n")
        env_path = pathlib.Path(env_file.name)
    try:
        run(
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--quiet",
        )
    finally:
        env_path.unlink(missing_ok=True)


def main() -> None:
    assert_true(COMPOSE_FILE.exists(), "missing docker-compose.vcvm.yml")
    assert_true(DEPLOY_SCRIPT.exists(), "missing scripts/deploy_vcvm.sh")
    assert_true(DOC_FILE.exists(), "missing docs/VCVM-DEPLOYMENT.md")
    assert_true(DOCKERIGNORE_FILE.exists(), "missing .dockerignore")

    run("bash", "-n", str(DEPLOY_SCRIPT))
    compose_quiet()

    config = compose_config()
    services = config.get("services", {})
    manager = services.get("manager", {})
    ports = manager.get("ports", [])
    env = manager.get("environment", {})
    volumes = config.get("volumes", {})

    assert_true(config.get("name") == "cloakbrowser-manager-vcvm", "unexpected compose project name")
    assert_true(manager.get("container_name") == "cloakbrowser-manager-vcvm", "unexpected container name")
    assert_true(manager.get("platform") == "linux/amd64", "VCVM stack must target x86_64 Docker")
    assert_true(manager.get("restart") == "unless-stopped", "missing restart policy")
    assert_true(manager.get("network_mode") == "bridge", "VCVM stack must reuse Docker bridge network")
    assert_true(manager.get("healthcheck") is not None, "missing healthcheck")
    assert_true(env.get("ACCESS_CONTROL_ENABLED") == "1", "access control must be forced on")
    assert_true(env.get("AUTH_TOKEN") == "unit-test-token-with-safe-length", "AUTH_TOKEN must come from env")
    assert_true(
        env.get("PROXYCHECKER_URL") == "http://host.docker.internal:18899",
        "proxychecker URL must remain explicit and environment-controlled",
    )
    assert_true(
        env.get("PROXYCHECKER_ALLOWED_HOSTS") == "host.docker.internal",
        "proxychecker allow-list must stay narrow",
    )
    assert_true(
        "host.docker.internal=host-gateway" in manager.get("extra_hosts", []),
        "manager must use the explicit Docker host gateway",
    )
    assert_true("cloakbrowser-manager-vcvm-data" in volumes, "missing named data volume")
    assert_true(str(manager.get("mem_limit")) == str(32 * 1024 * 1024 * 1024), "unexpected memory limit default")
    assert_true(str(manager.get("cpus")) in {"16.0", "16"}, "unexpected CPU limit default")

    port_json = json.dumps(ports)
    assert_true("127.0.0.1" in port_json, "manager must bind to loopback")
    assert_true("0.0.0.0" not in port_json, "manager must not bind to all interfaces")
    healthcheck_json = json.dumps(manager.get("healthcheck"))
    assert_true("/health" in healthcheck_json, "manager healthcheck must remain local liveness")
    assert_true("proxychecker" not in healthcheck_json.lower(), "manager healthcheck must not depend on proxychecker")

    deploy_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    dockerignore_text = DOCKERIGNORE_FILE.read_text(encoding="utf-8")
    assert_true("ACCESS_CONTROL_ENABLED=1" in deploy_text, "deploy script must force access control")
    assert_true("PROXYCHECKER_URL" in deploy_text, "deploy script must support optional proxychecker configuration")
    assert_true(
        "host.docker.internal" in deploy_text,
        "deploy script must restrict the proxychecker boundary to the Docker host gateway",
    )
    assert_true("tailscale serve --bg --https" in deploy_text, "deploy script must use private HTTPS Serve")
    assert_true("timeout 30s tailscale serve" in deploy_text, "Tailscale Serve must not hang indefinitely")
    assert_true("<tailscale-admin-enable-url>" in deploy_text, "Tailscale admin URLs must be scrubbed")
    assert_true("tailscale funnel" not in deploy_text.lower(), "deploy script must not use public funnel")
    assert_true("Refusing unexpected target host" in deploy_text, "deploy script must validate host")
    assert_true("Expected exactly: $DEFAULT_REMOTE_PATH" in deploy_text, "deploy script must validate path")
    assert_true(".cloakbrowser-manager-vcvm-managed" in deploy_text, "deploy script must use a managed marker")
    assert_true("--delete" in deploy_text and "--exclude \"$MANAGED_MARKER\"" in deploy_text, "rsync delete must preserve marker")
    assert_true("(.TCP // {}) | has(\\$port)" in deploy_text, "Serve collision check must inspect TCP map")
    assert_true("(.Web // {}) | keys" in deploy_text, "Serve collision check must inspect Web map")

    for pattern in (
        ".git",
        ".venv",
        "backend/.venv",
        "node_modules",
        "frontend/dist",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "*.pyc",
        "backend/.data",
        "artifacts",
        "benchmarks",
        "docker-compose.guacamole-benchmark.yml",
        "scripts/guacamole_benchmark_config.json",
        "scripts/run_guacamole_benchmark.sh",
        ".env.vcvm",
        "*token*",
    ):
        assert_true(pattern in dockerignore_text, f".dockerignore missing {pattern}")
        assert_true(pattern in deploy_text, f"rsync excludes missing {pattern}")

    print("VCVM deployment surface checks passed")


if __name__ == "__main__":
    main()
