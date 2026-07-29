"""Provider readiness storage and API contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db

WORKER_ID = "browser-use-worker-1"
WORKER_KEY = "cbm_worker_" + ("ab" * 32)


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_ID", WORKER_ID)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", WORKER_KEY)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(
        main.worker_runtime_service,
        "_clock",
        lambda: datetime.now(timezone.utc),
    )
    with TestClient(main.app) as client:
        main.worker_runtime_service.sync_configured_worker()
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def worker_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_KEY}"}


def test_provider_readiness_reports_exact_matrix_and_redacted_aliases(
    client_access: TestClient,
):
    from backend import main

    reported = client_access.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={
            "provider": "grok",
            "transport": "openai-compatible",
            "ready": True,
            "reason_code": "ready",
            "model_aliases": ["grok-build-0.1", "grok-build-0.1", "https://secret.local"],
        },
    )
    assert reported.status_code == 204, reported.text

    failed = client_access.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={
            "provider": "antigravity",
            "transport": "cli",
            "ready": False,
            "reason_code": "auth_required",
        },
    )
    assert failed.status_code == 204, failed.text

    response = client_access.get("/api/providers/readiness", headers=bootstrap_headers())
    assert response.status_code == 200
    body = response.json()
    targets = {(item["provider"], item["transport"]): item for item in body["providers"]}
    assert list(targets) == [
        ("antigravity", "cli"),
        ("grok", "cli"),
        ("codex", "acp"),
        ("claude", "acp"),
        ("cursor", "acp"),
        ("grok", "acp"),
        ("opencode", "acp"),
        ("grok", "openai-compatible"),
    ]
    assert targets[("grok", "openai-compatible")]["state"] == "ready"
    assert targets[("grok", "openai-compatible")]["ready"] is True
    assert targets[("grok", "openai-compatible")]["reason_code"] == "ready"
    assert targets[("grok", "openai-compatible")]["model_aliases"] == [
        "grok-build-0.1",
    ]
    assert targets[("antigravity", "cli")]["state"] == "failed"
    assert targets[("antigravity", "cli")]["reason_code"] == "auth_required"
    assert targets[("grok", "cli")] == {
        "provider": "grok",
        "transport": "cli",
        "ready": False,
        "state": "unavailable",
        "reason_code": "protocol_unavailable",
        "checked_at": None,
        "model_aliases": [],
    }
    assert targets[("codex", "acp")] == {
        "provider": "codex",
        "transport": "acp",
        "ready": False,
        "state": "unavailable",
        "reason_code": "protocol_unavailable",
        "checked_at": None,
        "model_aliases": [],
    }
    serialized = json.dumps(body)
    assert "worker_id" not in serialized
    assert "cbm_worker" not in serialized
    assert "Bearer" not in serialized

    checked_at = datetime.fromisoformat(
        targets[("grok", "openai-compatible")]["checked_at"]
    )
    main.worker_runtime_service._clock = lambda: checked_at + timedelta(seconds=301)
    stale = client_access.get("/api/providers/readiness", headers=bootstrap_headers()).json()
    stale_targets = {(item["provider"], item["transport"]): item for item in stale["providers"]}
    assert stale_targets[("grok", "openai-compatible")]["state"] == "stale"
    assert stale_targets[("grok", "openai-compatible")]["ready"] is False
    assert stale_targets[("grok", "openai-compatible")]["reason_code"] == "protocol_unavailable"
    assert stale_targets[("grok", "openai-compatible")]["model_aliases"] == []


def test_provider_readiness_auth_and_payload_are_strict(client_access: TestClient):
    payload = {
        "provider": "grok",
        "transport": "cli",
        "ready": False,
        "reason_code": "protocol_unavailable",
    }
    assert client_access.get("/api/providers/readiness").status_code == 401
    assert client_access.post(
        "/internal/providers/readiness",
        headers=bootstrap_headers(),
        json=payload,
    ).status_code == 401
    assert client_access.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={**payload, "detail": "must not persist"},
    ).status_code == 422
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM worker_provider_preflights").fetchone()[0] == 0


@pytest.mark.parametrize("provider", ["codex", "claude", "cursor", "grok", "opencode"])
def test_provider_readiness_accepts_exact_acp_targets(
    client_access: TestClient, provider: str
):
    reported = client_access.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={
            "provider": provider,
            "transport": "acp",
            "ready": True,
            "reason_code": "ready",
            "model_aliases": ["grok-build-0.1"] if provider == "grok" else [],
        },
    )
    assert reported.status_code == 204, reported.text


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        ("antigravity", "acp"),
        ("codex", "cli"),
        ("claude", "openai-compatible"),
        ("cursor", "cli"),
        ("opencode", "openai-compatible"),
    ],
)
def test_provider_readiness_rejects_unknown_or_direct_cli_targets(
    client_access: TestClient, provider: str, transport: str
):
    reported = client_access.post(
        "/internal/providers/readiness",
        headers=worker_headers(),
        json={
            "provider": provider,
            "transport": transport,
            "ready": False,
            "reason_code": "protocol_unavailable",
        },
    )
    assert reported.status_code == 422, reported.text


def test_provider_readiness_uses_newest_active_worker_result(client_access: TestClient):
    from backend import main

    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    main.worker_runtime_service._clock = lambda: now
    with db.get_db() as conn:
        conn.executemany(
            """
            INSERT INTO worker_identities
                (id, key_digest, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("worker-old", "digest-old", 1, now.isoformat(), now.isoformat()),
                ("worker-new", "digest-new", 1, now.isoformat(), now.isoformat()),
                ("worker-inactive", "digest-inactive", 0, now.isoformat(), now.isoformat()),
            ],
        )
        conn.executemany(
            """
            INSERT INTO worker_provider_preflights
                (worker_id, provider, transport, ready, reason_code, model_aliases_json, checked_at)
            VALUES (?, 'grok', 'cli', ?, ?, ?, ?)
            """,
            [
                ("worker-old", 0, "auth_required", "[]", (now - timedelta(seconds=20)).isoformat()),
                ("worker-new", 1, "ready", '["grok-build-0.1"]', (now - timedelta(seconds=5)).isoformat()),
                ("worker-inactive", 0, "model_unavailable", "[]", (now - timedelta(seconds=1)).isoformat()),
            ],
        )
        conn.commit()

    grok_cli = {
        (item["provider"], item["transport"]): item
        for item in main.worker_runtime_service.provider_preflights()["providers"]
    }[("grok", "cli")]
    assert grok_cli["state"] == "ready"
    assert grok_cli["ready"] is True
    assert grok_cli["reason_code"] == "ready"
    assert grok_cli["model_aliases"] == ["grok-build-0.1"]
