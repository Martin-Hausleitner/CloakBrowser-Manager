#!/usr/bin/env python3
"""Bounded health probe for the secure recorder bridge/runtime.

The watchdog only checks caller-selected health URLs and an allowlist of
CloakBrowser recorder services. It redacts credentials before producing output
and only restarts services explicitly present in the in-scope allowlist.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_ALLOWED_SERVICES = frozenset(
    {
        "cloakbrowser-recorder-bridge.service",
        "cloakbrowser-browser-use-worker.service",
        "cloakbrowser-acpx-worker.service",
    }
)
DEFAULT_HEALTH_URL = "http://127.0.0.1:18115/health"
MAX_MESSAGE_BYTES = 512

SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)((?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*)[^\s,;&]+"
    ),
    re.compile(r"(?i)([?&](?:token|key|secret|password)=)[^&\s]+"),
)
LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![\w:])/(?:Users|home|private|tmp|var/folders|workspace|workspaces|Volumes)/[^\s,;|)]+"),
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\s,;|)]+"),
)
SERVICE_RE = re.compile(r"^cloakbrowser-[A-Za-z0-9_.@-]+\.service$")


@dataclass(frozen=True)
class ProbeResult:
    ready: bool
    message: str


@dataclass(frozen=True)
class ServiceStatus:
    service: str
    active: bool
    message: str


@dataclass(frozen=True)
class WatchdogResult:
    passed: bool
    health: ProbeResult | None
    services: list[ServiceStatus]
    restarted: list[str]
    failures: list[str]


class SystemdUserRunner:
    """Small adapter around systemctl --user for injectable tests."""

    def status(self, service: str) -> ServiceStatus:
        validate_service_name(service)
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        output = completed.stdout or completed.stderr or f"exit={completed.returncode}"
        return ServiceStatus(
            service=service,
            active=completed.returncode == 0 and output.strip() == "active",
            message=redact_text(output),
        )

    def restart(self, service: str) -> None:
        validate_service_name(service)
        subprocess.run(
            ["systemctl", "--user", "restart", service],
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted-secret]", text)
    for pattern in LOCAL_PATH_PATTERNS:
        text = pattern.sub("[redacted-local-path]", text)
    if len(text.encode("utf-8", errors="replace")) > MAX_MESSAGE_BYTES:
        text = text[:MAX_MESSAGE_BYTES] + "...[truncated]"
    return text


def validate_service_name(service: str) -> str:
    value = str(service or "").strip()
    if SERVICE_RE.fullmatch(value) is None:
        raise ValueError("service must be a cloakbrowser *.service unit")
    return value


def validate_health_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ValueError("health URL must use http")
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("health URL must target loopback")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("health URL must not include credentials")
    if parsed.port is None or not (1024 <= parsed.port <= 65535):
        raise ValueError("health URL must use an explicit high port")
    if parsed.path != "/health" or parsed.query or parsed.fragment:
        raise ValueError("health URL must target the exact /health path")
    return value


def _default_requester(url: str, timeout: float) -> tuple[int, str]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - caller controls loopback health URL.
        payload = response.read(2048).decode("utf-8", errors="replace")
        return int(response.status), payload


def probe_http_health(
    *,
    url: str,
    requester: Callable[[str, float], tuple[int, str]] = _default_requester,
    timeout_seconds: float = 2.0,
) -> ProbeResult:
    url = validate_health_url(url)
    try:
        status, body = requester(url, timeout_seconds)
    except (OSError, RuntimeError, URLError) as exc:
        return ProbeResult(ready=False, message=redact_text(exc))
    if 200 <= status < 300:
        return ProbeResult(ready=True, message="healthy")
    return ProbeResult(ready=False, message=redact_text(f"health status={status}: {body}"))


def _write_json(path: Path, result: WatchdogResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True), encoding="utf-8")


def check_services(
    *,
    services: Sequence[str],
    allowed_services: frozenset[str],
    runner: SystemdUserRunner,
    restart: bool,
    max_restarts: int,
    health_url: str | None = None,
    output_json: Path | None = None,
) -> WatchdogResult:
    if max_restarts < 0 or max_restarts > len(DEFAULT_ALLOWED_SERVICES):
        raise ValueError("max_restarts must be bounded to the recorder service allowlist")

    failures: list[str] = []
    statuses: list[ServiceStatus] = []
    restarted: list[str] = []
    health = probe_http_health(url=health_url) if health_url else None
    if health and not health.ready:
        failures.append(f"health probe failed: {health.message}")

    for raw_service in services:
        try:
            service = validate_service_name(raw_service)
        except ValueError as exc:
            failures.append(redact_text(f"{raw_service}: {exc}"))
            continue
        if service not in allowed_services:
            failures.append(f"{service}: restart refused outside recorder allowlist")
            continue
        status = runner.status(service)
        status = ServiceStatus(status.service, status.active, redact_text(status.message))
        statuses.append(status)
        if status.active:
            continue
        failures.append(f"{service}: {status.message}")
        if restart and len(restarted) < max_restarts:
            runner.restart(service)
            restarted.append(service)

    result = WatchdogResult(
        passed=not failures,
        health=health,
        services=statuses,
        restarted=restarted,
        failures=[redact_text(failure) for failure in failures],
    )
    if output_json is not None:
        _write_json(output_json, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secure_recorder_watchdog")
    parser.add_argument(
        "--service",
        action="append",
        default=[],
        help="In-scope cloakbrowser service to inspect; may be repeated.",
    )
    parser.add_argument("--health-url", default="", help="Optional recorder/manager health URL.")
    parser.add_argument("--restart", action="store_true", help="Restart unhealthy in-scope services.")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the bounded watchdog configuration without probing or restarting.",
    )
    parser.add_argument("--max-restarts", type=int, default=1)
    parser.add_argument("--output-json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    services = args.service or sorted(DEFAULT_ALLOWED_SERVICES)
    try:
        if args.validate_only:
            for service in services:
                validated = validate_service_name(service)
                if validated not in DEFAULT_ALLOWED_SERVICES:
                    raise ValueError(f"{validated}: service is outside recorder allowlist")
            if args.health_url:
                validate_health_url(args.health_url)
            result = WatchdogResult(
                passed=True,
                health=None,
                services=[],
                restarted=[],
                failures=[],
            )
            if args.output_json is not None:
                _write_json(args.output_json, result)
        else:
            result = check_services(
                services=services,
                allowed_services=DEFAULT_ALLOWED_SERVICES,
                runner=SystemdUserRunner(),
                restart=bool(args.restart),
                max_restarts=args.max_restarts,
                health_url=args.health_url or None,
                output_json=args.output_json,
            )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with redacted text.
        print(redact_text(exc), file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
