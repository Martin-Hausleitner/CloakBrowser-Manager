"""Secure action-recorder contracts for reference-only vault usage."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from backend import database as db
from backend.secure_action_recorder import (
    RecorderBatchError,
    SecureActionRecorderBatch,
    VaultProviderMetadata,
    canonical_recorder_signature_payload,
    ingest_secure_action_recorder_batch,
)


WORKER_ID = "browser-use-worker-1"
WORKER_TOKEN = "cbm_worker_" + ("ab" * 32)


@pytest.fixture()
def recorder_client(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-recorder-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_ID", WORKER_ID)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", WORKER_TOKEN)
    main._login_failures.clear()
    main.secure_action_recorder_nonce_cache.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        main.worker_runtime_service.sync_configured_worker()
        yield client


def worker_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_TOKEN}"}


def seed_passed_health(profile_id: str) -> None:
    db.upsert_profile_health(
        profile_id,
        state="passed",
        checked_at=datetime.now(timezone.utc).isoformat(),
        proxy_configured=False,
        proxy_reachable=True,
        proxy_authenticity_score=88,
        fingerprint_consistency_score=100,
        browser_scan_score=90,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={
            "fingerprint_consistency": "measured",
            "browser_scan": "measured",
        },
    )


def create_claimed_run(client: TestClient) -> dict:
    profile = db.create_profile("Recorder browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        headers={"Authorization": "Bearer bootstrap-recorder-test-secret"},
        json={
            "harness": "browser-use",
            "task": "Replay recorded login",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    claimed = client.post("/internal/task-runs/claim", headers=worker_headers())
    assert claimed.status_code == 200, claimed.text
    return claimed.json()


def valid_batch(**overrides) -> dict:
    explicit_signature = overrides.pop("signature", None)
    body = {
        "recorder_id": "extension-recorder-1",
        "nonce": "nonce-0123456789abcdef",
        "signed_at": "2026-07-30T10:00:00+00:00",
        "actions": [
            {
                "sequence": 1,
                "kind": "fill_secret_reference",
                "origin": "https://example.com",
                "selector": "#password",
                "secret_ref": "secretref-login-password",
                "vault": {
                    "provider": "vaultwarden",
                    "purpose": "human_credentials",
                    "item_ref": "secretref-vw-login-item",
                    "collection_ref": "secretref-vw-collection",
                },
            },
            {
                "sequence": 2,
                "kind": "machine_identity_reference",
                "origin": "https://example.com",
                "secret_ref": "secretref-infisical-machine",
                "vault": {
                    "provider": "infisical",
                    "purpose": "machine_identity",
                    "project_ref": "secretref-infisical-project",
                    "environment": "prod",
                    "identity_ref": "secretref-infisical-identity",
                },
            },
        ],
    }
    body.update(overrides)
    body["signature"] = explicit_signature or _signature_for(body)
    return body


def _signature_for(
    body: dict,
    *,
    key: str = WORKER_TOKEN,
    run_id: str = "run-test",
    worker_id: str = WORKER_ID,
    profile_id: str = "profile-test",
) -> str:
    unsigned = copy.deepcopy(body)
    unsigned.pop("signature", None)
    payload = canonical_recorder_signature_payload(
        unsigned,
        run_id=run_id,
        worker_id=worker_id,
        profile_id=profile_id,
    )
    digest = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signed_batch_for_run(run: dict, **overrides) -> dict:
    explicit_signature = overrides.pop("signature", None)
    body = valid_batch(**overrides)
    body["signed_at"] = datetime.now(timezone.utc).isoformat()
    body["signature"] = explicit_signature or _signature_for(
        body,
        run_id=str(run["id"]),
        worker_id=WORKER_ID,
        profile_id=str(run["profile_id"]),
    )
    return body


def test_model_accepts_reference_only_provider_metadata():
    batch = SecureActionRecorderBatch.model_validate(valid_batch())

    assert batch.actions[0].vault.provider == "vaultwarden"
    assert batch.actions[0].vault.purpose == "human_credentials"
    assert batch.actions[1].vault.provider == "infisical"
    assert batch.actions[1].vault.purpose == "machine_identity"
    assert "do-not-echo-this-secret" not in batch.model_dump_json()


def test_vault_metadata_supports_loopback_only_gopass_local_mode():
    metadata = VaultProviderMetadata.model_validate(
        {
            "provider": "gopass",
            "purpose": "local_mode",
            "store_ref": "secretref-gopass-store",
            "entry_ref": "secretref-gopass-login",
            "loopback_callback_url": "http://127.0.0.1:49152/cbm/gopass",
        }
    )
    assert metadata.loopback_callback_url == "http://127.0.0.1:49152/cbm/gopass"

    with pytest.raises(ValidationError):
        VaultProviderMetadata.model_validate(
            {
                "provider": "gopass",
                "purpose": "local_mode",
                "store_ref": "secretref-gopass-store",
                "entry_ref": "secretref-gopass-login",
                "loopback_callback_url": "https://example.com/cbm/gopass",
            }
        )


@pytest.mark.parametrize("field", ["password", "token", "cookie", "totp_seed"])
def test_model_rejects_raw_secret_fields(field: str):
    body = valid_batch()
    body["actions"][0][field] = "do-not-echo-this-secret"
    body["signature"] = _signature_for(body)

    with pytest.raises(ValidationError) as excinfo:
        SecureActionRecorderBatch.model_validate(body)

    assert "do-not-echo-this-secret" not in str(excinfo.value)


def test_model_rejects_secret_like_strings_without_echo():
    body = valid_batch()
    body["actions"][0]["selector"] = "password=do-not-echo-this-secret"
    body["signature"] = _signature_for(body)

    with pytest.raises(ValidationError) as excinfo:
        SecureActionRecorderBatch.model_validate(body)

    assert "do-not-echo-this-secret" not in str(excinfo.value)


def test_ingest_recorder_batch_stores_reference_only_payload(recorder_client: TestClient):
    run = create_claimed_run(recorder_client)
    response = recorder_client.post(
        f"/internal/task-runs/{run['id']}/recorder-batches",
        headers=worker_headers(),
        json=signed_batch_for_run(run),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "action"
    assert body["payload"]["action_count"] == 2
    assert body["payload"]["secret_reference_count"] == 2
    assert body["payload"]["vault_providers"] == ["infisical", "vaultwarden"]
    assert "secretref-" not in json.dumps(body)
    assert "password" not in json.dumps(body).lower()

    outputs = db.list_task_outputs(run["id"], after_sequence=0, limit=10)
    assert len(outputs) == 1
    stored = json.dumps(outputs[0], sort_keys=True)
    assert "secretref-" not in stored
    assert "do-not-echo" not in stored


def test_ingest_recorder_batch_rejects_bad_signature_without_echo(
    recorder_client: TestClient,
):
    run = create_claimed_run(recorder_client)
    body = signed_batch_for_run(run, signature="sha256=" + ("0" * 64))

    response = recorder_client.post(
        f"/internal/task-runs/{run['id']}/recorder-batches",
        headers=worker_headers(),
        json=body,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid recorder batch"}
    assert "secretref-" not in response.text


def test_ingest_recorder_batch_enforces_allowed_origin(recorder_client: TestClient):
    run = create_claimed_run(recorder_client)
    body = signed_batch_for_run(run)
    body["actions"][0]["origin"] = "https://evil.example"
    body["signature"] = _signature_for(
        body,
        run_id=str(run["id"]),
        worker_id=WORKER_ID,
        profile_id=str(run["profile_id"]),
    )

    response = recorder_client.post(
        f"/internal/task-runs/{run['id']}/recorder-batches",
        headers=worker_headers(),
        json=body,
    )

    assert response.status_code == 422
    assert "evil.example" not in response.text


def test_ingest_recorder_batch_rejects_nonce_replay(recorder_client: TestClient):
    run = create_claimed_run(recorder_client)
    first = recorder_client.post(
        f"/internal/task-runs/{run['id']}/recorder-batches",
        headers=worker_headers(),
        json=signed_batch_for_run(run),
    )
    assert first.status_code == 201, first.text

    replay = recorder_client.post(
        f"/internal/task-runs/{run['id']}/recorder-batches",
        headers=worker_headers(),
        json=signed_batch_for_run(run),
    )

    assert replay.status_code == 409
    assert replay.json() == {"detail": "Recorder nonce replay"}


def test_signature_helper_rejects_unsigned_payload():
    body = valid_batch()
    body.pop("signature")
    with pytest.raises(ValidationError):
        SecureActionRecorderBatch.model_validate(body)


def test_signature_is_bound_to_run_worker_and_profile_context():
    body = valid_batch()
    body["signed_at"] = datetime.now(timezone.utc).isoformat()
    body["signature"] = _signature_for(
        body,
        run_id="run-a",
        worker_id=WORKER_ID,
        profile_id="profile-a",
    )

    with pytest.raises(RecorderBatchError, match="invalid recorder batch signature"):
        ingest_secure_action_recorder_batch(
            run_id="run-b",
            worker_id=WORKER_ID,
            profile_id="profile-a",
            raw_body=json.dumps(body).encode("utf-8"),
            worker_key=WORKER_TOKEN,
            allowed_origins=["https://example.com"],
            nonce_cache={},
        )


def test_ingest_rejects_stale_signed_batch():
    body = valid_batch(signed_at="2020-01-01T00:00:00+00:00")
    body["signature"] = _signature_for(body)

    with pytest.raises(RecorderBatchError, match="outside freshness window"):
        ingest_secure_action_recorder_batch(
            run_id="run-test",
            worker_id=WORKER_ID,
            profile_id="profile-test",
            raw_body=json.dumps(body).encode("utf-8"),
            worker_key=WORKER_TOKEN,
            allowed_origins=["https://example.com"],
            nonce_cache={},
        )
