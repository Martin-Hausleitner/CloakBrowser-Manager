from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


RUNNER = Path(__file__).resolve().with_name("secure_recorder_watchdog.py")
SPEC = importlib.util.spec_from_file_location("secure_recorder_watchdog", RUNNER)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


class FakeRunner:
    def __init__(self, statuses: list[watchdog.ServiceStatus]):
        self.statuses = list(statuses)
        self.restarted: list[str] = []

    def status(self, service: str) -> watchdog.ServiceStatus:
        return self.statuses.pop(0)

    def restart(self, service: str) -> None:
        self.restarted.append(service)


def test_probe_redacts_credentials_from_failure_output():
    result = watchdog.probe_http_health(
        url="http://127.0.0.1:18115/health",
        requester=lambda _url, _timeout: (_ for _ in ()).throw(
            RuntimeError("Authorization: Bearer raw-token failed at /Users/example/key")
        ),
        timeout_seconds=0.01,
    )

    assert result.ready is False
    rendered = result.message
    assert "raw-token" not in rendered
    assert "/Users/example" not in rendered
    assert "[redacted-secret]" in rendered
    assert "[redacted-local-path]" in rendered


def test_watchdog_restarts_only_allowed_unhealthy_services():
    runner = FakeRunner(
        [
            watchdog.ServiceStatus(
                service="cloakbrowser-recorder-bridge.service",
                active=False,
                message="inactive",
            )
        ]
    )

    result = watchdog.check_services(
        services=["cloakbrowser-recorder-bridge.service"],
        allowed_services=watchdog.DEFAULT_ALLOWED_SERVICES,
        runner=runner,
        restart=True,
        max_restarts=1,
    )

    assert result.passed is False
    assert runner.restarted == ["cloakbrowser-recorder-bridge.service"]
    assert result.restarted == ["cloakbrowser-recorder-bridge.service"]


def test_watchdog_refuses_out_of_scope_restart():
    runner = FakeRunner(
        [
            watchdog.ServiceStatus(
                service="ssh.service",
                active=False,
                message="inactive bearer leaked-secret",
            )
        ]
    )

    result = watchdog.check_services(
        services=["ssh.service"],
        allowed_services=watchdog.DEFAULT_ALLOWED_SERVICES,
        runner=runner,
        restart=True,
        max_restarts=1,
    )

    assert result.passed is False
    assert runner.restarted == []
    assert "ssh.service" in result.failures[0]
    assert "leaked-secret" not in result.failures[0]


def test_watchdog_bounds_restart_count_and_writes_redacted_json(tmp_path: Path):
    runner = FakeRunner(
        [
            watchdog.ServiceStatus("cloakbrowser-recorder-bridge.service", False, "down"),
            watchdog.ServiceStatus("cloakbrowser-acpx-worker.service", False, "down"),
        ]
    )
    output = tmp_path / "watchdog.json"

    result = watchdog.check_services(
        services=[
            "cloakbrowser-recorder-bridge.service",
            "cloakbrowser-acpx-worker.service",
        ],
        allowed_services=watchdog.DEFAULT_ALLOWED_SERVICES,
        runner=runner,
        restart=True,
        max_restarts=1,
        output_json=output,
    )

    assert result.passed is False
    assert runner.restarted == ["cloakbrowser-recorder-bridge.service"]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["restarted"] == ["cloakbrowser-recorder-bridge.service"]
    assert [item["service"] for item in payload["services"]] == [
        "cloakbrowser-recorder-bridge.service",
        "cloakbrowser-acpx-worker.service",
    ]


def test_validate_service_name_rejects_shell_metacharacters():
    with pytest.raises(ValueError):
        watchdog.validate_service_name("cloakbrowser-recorder-bridge.service;rm -rf /")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:18115/health",
        "http://example.com:18115/health",
        "file:///etc/passwd",
        "http://127.0.0.1:18115/admin",
        "http://user:pass@127.0.0.1:18115/health",
    ],
)
def test_validate_health_url_rejects_non_loopback_or_credentialed_targets(url: str):
    with pytest.raises(ValueError):
        watchdog.validate_health_url(url)


def test_validate_health_url_accepts_explicit_loopback_health_endpoint():
    assert (
        watchdog.validate_health_url("http://127.0.0.1:18115/health")
        == "http://127.0.0.1:18115/health"
    )
