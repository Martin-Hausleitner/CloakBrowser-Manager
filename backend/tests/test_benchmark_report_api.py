"""Tests for the browser-visible streaming benchmark report endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient


def _patch_lifespan_processes(main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())


@pytest.fixture()
def benchmark_report_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "latest-benchmark-report.json"
    monkeypatch.setenv("BENCHMARK_REPORT_PATH", str(path))
    return path


@pytest.fixture()
def client_no_auth(tmp_db, benchmark_report_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", None)
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", False)
    _patch_lifespan_processes(main, monkeypatch)

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def client_auth(tmp_db, benchmark_report_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", False)
    _patch_lifespan_processes(main, monkeypatch)

    with TestClient(main.app) as client:
        yield client


@pytest.fixture()
def client_access_control(tmp_db, benchmark_report_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    _patch_lifespan_processes(main, monkeypatch)

    with TestClient(main.app) as client:
        yield client


def _write_report(path: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "started_at": "2026-07-20T07:59:00Z",
        "finished_at": "2026-07-20T08:00:00Z",
        "report_url": "/api/benchmarks/latest",
        "config": {"path": "/private/config.json", "token": "config-secret"},
        "environment": {"cwd": "/private/worktree"},
        "reproduce_command": "run --token command-secret",
        "results": [
            {
                "candidate": {
                    "id": "manager-vnc",
                    "name": "KasmVNC through manager",
                    "type": "websocket",
                    "url": "ws://127.0.0.1:8080/private-profile-id/vnc?token=url-secret",
                    "command": "private-command --token command-secret",
                    "metadata": {
                        "technology": "KasmVNC + noVNC",
                        "comparison_role": "live browser stream",
                        "private_note": "metadata-secret",
                    },
                },
                "state": "measured",
                "status": "measured",
                "availability": "available",
                "measurements": [
                    {
                        "available": True,
                        "stdout_tail": "process-secret",
                        "timings_ms": {"handshake_ms": 42.5},
                    }
                ],
                "summary": {
                    "runs": 1,
                    "success_rate_pct": 100,
                    "timings_ms": {"handshake_ms": {"min": 42.5, "median": 42.5, "p95": 42.5}},
                },
            },
            {
                "candidate": {
                    "id": "sunshine-native",
                    "name": "Sunshine architecture reference",
                    "type": "architecture",
                },
                "status": "architecture_only",
                "availability": "not_measured",
                "reason": "Native-client architecture is not browser-embedded.",
                "measurements": [],
                "summary": {"runs": 0},
            },
        ],
        "producer_specific_extra": {"kept": True},
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def _write_legacy_dashboard_report(path: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "run": {"id": "legacy-dashboard", "state": "complete"},
        "expected_technologies": ["KasmVNC", {"name": "Selkies", "version": "1.x"}],
        "candidates": [
            {
                "name": "KasmVNC through manager",
                "technology": "KasmVNC",
                "state": "measured",
                "metrics": {"p95_latency_ms": 42.5, "samples": 25},
            },
            {
                "name": "Sunshine architecture reference",
                "technology": "Sunshine/Moonlight",
                "state": "not_measured",
                "not_measured_reason": "Native-client architecture is not browser-embedded.",
            },
        ],
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def test_latest_benchmark_report_serves_a_redacted_browser_dto_without_auth(
    client_no_auth: TestClient,
    benchmark_report_path: Path,
):
    _write_report(benchmark_report_path)

    response = client_no_auth.get("/api/benchmarks/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "schema_version": 1,
        "started_at": "2026-07-20T07:59:00Z",
        "finished_at": "2026-07-20T08:00:00Z",
        "results": [
            {
                "candidate": {
                    "id": "manager-vnc",
                    "name": "KasmVNC through manager",
                    "type": "websocket",
                    "metadata": {
                        "technology": "KasmVNC + noVNC",
                        "comparison_role": "live browser stream",
                    },
                },
                "status": "measured",
                "availability": "available",
                "summary": {
                    "runs": 1,
                    "success_rate_pct": 100,
                    "timings_ms": {"handshake_ms": {"min": 42.5, "median": 42.5, "p95": 42.5}},
                },
            },
            {
                "candidate": {
                    "id": "sunshine-native",
                    "name": "Sunshine architecture reference",
                    "type": "architecture",
                },
                "status": "architecture_only",
                "availability": "not_measured",
                "summary": {"runs": 0},
                "reason": "Architecture-only candidate; no comparable live browser measurement was run.",
            },
        ],
    }
    rendered = response.text
    for secret in (
        "config-secret",
        "command-secret",
        "url-secret",
        "metadata-secret",
        "process-secret",
        "/private/config.json",
        "/private/worktree",
    ):
        assert secret not in rendered


def test_latest_benchmark_report_accepts_legacy_dashboard_candidates(
    client_no_auth: TestClient,
    benchmark_report_path: Path,
):
    _write_legacy_dashboard_report(benchmark_report_path)

    response = client_no_auth.get("/api/benchmarks/latest")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "started_at": None,
        "finished_at": None,
        "candidates": [
            {
                "name": "KasmVNC through manager",
                "technology": "KasmVNC",
                "state": "measured",
                "measured": True,
                "metrics": {"p95_latency_ms": 42.5, "samples": 25},
            },
            {
                "name": "Sunshine architecture reference",
                "technology": "Sunshine/Moonlight",
                "state": "not_measured",
                "measured": False,
                "not_measured_reason": "No comparable live measurement was reported.",
            },
        ],
    }


def test_latest_benchmark_report_requires_token_when_auth_enabled(
    client_auth: TestClient,
    benchmark_report_path: Path,
):
    _write_report(benchmark_report_path)

    assert client_auth.get("/api/benchmarks/latest").status_code == 401

    response = client_auth.get(
        "/api/benchmarks/latest",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "measured"


def test_latest_benchmark_report_respects_access_control_auth_policy(
    client_access_control: TestClient,
    benchmark_report_path: Path,
):
    _write_report(benchmark_report_path)

    assert client_access_control.get("/api/benchmarks/latest").status_code == 401

    response = client_access_control.get(
        "/api/benchmarks/latest",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["candidate"]["id"] == "manager-vnc"


def test_latest_benchmark_report_is_not_available_to_scoped_viewers(
    client_access_control: TestClient,
    benchmark_report_path: Path,
):
    _write_report(benchmark_report_path)
    created = client_access_control.post(
        "/api/access/users",
        headers={"Authorization": "Bearer test-secret"},
        json={
            "username": "benchmark-viewer",
            "password": "benchmark-viewer-password-123",
            "grants": [],
        },
    )
    assert created.status_code == 201

    client_access_control.cookies.clear()
    assert client_access_control.post(
        "/api/auth/login",
        json={"username": "benchmark-viewer", "password": "benchmark-viewer-password-123"},
    ).status_code == 200

    response = client_access_control.get("/api/benchmarks/latest")
    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator access required"}
    assert "config-secret" not in response.text


def test_latest_benchmark_report_missing_file_returns_safe_404(
    client_no_auth: TestClient,
    benchmark_report_path: Path,
):
    response = client_no_auth.get("/api/benchmarks/latest")

    assert response.status_code == 404
    assert response.json() == {"detail": "Benchmark report not found"}
    assert str(benchmark_report_path) not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        "[1, 2, 3]",
        json.dumps({"schema_version": [], "results": []}),
        json.dumps({"started_at": 123, "results": []}),
        json.dumps({"results": "complete"}),
        json.dumps({"results": [{"candidate": "manager-vnc", "status": "measured", "availability": "available", "measurements": []}]}),
        json.dumps({"results": [{"candidate": {}, "status": 200, "availability": "available", "measurements": []}]}),
        json.dumps({"results": [{"candidate": {}, "status": "measured", "availability": "available", "measurements": {}}]}),
        json.dumps({"run": "complete", "candidates": []}),
        json.dumps({"candidates": [{"name": "bad", "metrics": []}]}),
        json.dumps({"expected_technologies": [{"version": "missing-name"}]}),
    ],
)
def test_latest_benchmark_report_malformed_contract_returns_safe_422(
    client_no_auth: TestClient,
    benchmark_report_path: Path,
    payload: str,
):
    benchmark_report_path.write_text(payload, encoding="utf-8")

    response = client_no_auth.get("/api/benchmarks/latest")

    assert response.status_code == 422
    assert response.json() == {"detail": "Benchmark report is malformed"}
    assert str(benchmark_report_path) not in response.text


def test_latest_benchmark_report_rejects_oversized_file(
    client_no_auth: TestClient,
    benchmark_report_path: Path,
):
    from backend import main

    benchmark_report_path.write_bytes(b" " * (main._BENCHMARK_REPORT_MAX_BYTES + 1))

    response = client_no_auth.get("/api/benchmarks/latest")

    assert response.status_code == 413
    assert response.json() == {"detail": "Benchmark report is too large"}
