"""REST and access-control contract for account metadata and auth history."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db


@pytest.fixture()
def accounts_client(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-account-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client


def _bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-account-test-secret"}


def _create_agent(
    client: TestClient,
    *,
    display_name: str,
    sandbox_id: str,
    permission: str,
) -> dict[str, str]:
    response = client.post(
        "/api/access/agents",
        headers=_bootstrap_headers(),
        json={
            "display_name": display_name,
            "grants": [{"sandbox_id": sandbox_id, "permission": permission}],
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['api_key']}"}


def test_create_list_update_and_history_are_profile_scoped(accounts_client: TestClient):
    profile = db.create_profile("Account API", sandbox_id="alpha", project_id="agents")
    operator = _create_agent(
        accounts_client,
        display_name="Account operator",
        sandbox_id="alpha",
        permission="operate",
    )

    created = accounts_client.post(
        "/api/accounts",
        headers=operator,
        json={
            "profile_id": profile["id"],
            "provider": "github",
            "subject_label": "agent@example.invalid",
            "display_name": "Automation agent",
            "origin": "https://GitHub.com",
            "auth_state": "unknown",
            "second_factor_state": "enrolled",
            "passkey_state": "off",
            "secret_ref": "secretref-login-1",
            "totp_ref": "secretref-totp-1",
        },
    )

    assert created.status_code == 201, created.text
    account = created.json()
    assert account["origin"] == "https://github.com"
    assert account["has_secret_reference"] is True
    assert account["has_totp_reference"] is True
    assert "secret_ref" not in account
    assert "totp_ref" not in account

    listed = accounts_client.get("/api/accounts", headers=operator)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [account["id"]]

    updated = accounts_client.put(
        f"/api/accounts/{account['id']}",
        headers=operator,
        json={"auth_state": "signed_in", "last_seen_at": "2026-07-29T04:00:00+00:00"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["auth_state"] == "signed_in"

    history = accounts_client.get(f"/api/accounts/{account['id']}/events", headers=operator)
    assert history.status_code == 200
    assert [item["event_type"] for item in history.json()] == ["created", "auth_state_changed"]


@pytest.mark.parametrize("field", ["password", "cookie", "token", "totp_seed", "passkey"])
def test_accounts_api_rejects_raw_secret_fields_without_echo(
    accounts_client: TestClient,
    field: str,
):
    profile = db.create_profile("Reject secrets", sandbox_id="alpha")
    payload = {
        "profile_id": profile["id"],
        "provider": "github",
        "subject_label": "reject@example.invalid",
        field: "do-not-echo-this-secret",
    }

    response = accounts_client.post(
        "/api/accounts",
        headers=_bootstrap_headers(),
        json=payload,
    )

    assert response.status_code == 422
    assert "do-not-echo-this-secret" not in response.text
    assert db.list_account_metadata(profile_id=profile["id"]) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "password=do-not-echo-this-secret"),
        ("display_name", "Authorization: Bearer do-not-echo-this-secret"),
        ("display_name", "https://user:do-not-echo-this-secret@example.invalid"),
        ("origin", "https://user:do-not-echo-this-secret@example.invalid"),
    ],
)
def test_accounts_api_rejects_secret_like_values_without_echo(
    accounts_client: TestClient,
    field: str,
    value: str,
):
    profile = db.create_profile("Reject secret values", sandbox_id="alpha")
    response = accounts_client.post(
        "/api/accounts",
        headers=_bootstrap_headers(),
        json={
            "profile_id": profile["id"],
            "provider": "github",
            "subject_label": "reject-values@example.invalid",
            field: value,
        },
    )

    assert response.status_code == 422
    assert "do-not-echo-this-secret" not in response.text
    assert db.list_account_metadata(profile_id=profile["id"]) == []


@pytest.mark.parametrize("field", ["secret_ref", "totp_ref"])
def test_accounts_api_requires_opaque_broker_reference_ids(
    accounts_client: TestClient,
    field: str,
):
    profile = db.create_profile("Reject raw reference values", sandbox_id="alpha")
    response = accounts_client.post(
        "/api/accounts",
        headers=_bootstrap_headers(),
        json={
            "profile_id": profile["id"],
            "provider": "github",
            "subject_label": "reference@example.invalid",
            field: "raw-credential-like-value",
        },
    )

    assert response.status_code == 422
    assert "raw-credential-like-value" not in response.text
    assert db.list_account_metadata(profile_id=profile["id"]) == []


def test_accounts_outside_granted_sandbox_are_indistinguishable_from_missing(
    accounts_client: TestClient,
):
    alpha = db.create_profile("Alpha account", sandbox_id="alpha")
    beta = db.create_profile("Beta account", sandbox_id="beta")
    alpha_account = db.create_account_metadata(
        profile_id=alpha["id"],
        provider="github",
        subject_label="alpha@example.invalid",
        actor_kind="bootstrap",
    )
    beta_account = db.create_account_metadata(
        profile_id=beta["id"],
        provider="github",
        subject_label="beta@example.invalid",
        actor_kind="bootstrap",
    )
    viewer = _create_agent(
        accounts_client,
        display_name="Alpha viewer",
        sandbox_id="alpha",
        permission="view",
    )

    listed = accounts_client.get("/api/accounts", headers=viewer)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [alpha_account["id"]]

    missing = accounts_client.get("/api/accounts/does-not-exist", headers=viewer)
    foreign = accounts_client.get(f"/api/accounts/{beta_account['id']}", headers=viewer)
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json() == {"detail": "Account not found"}

    denied_update = accounts_client.put(
        f"/api/accounts/{beta_account['id']}",
        headers=viewer,
        json={"auth_state": "signed_out"},
    )
    assert denied_update.status_code == 404
    assert denied_update.json() == {"detail": "Account not found"}

    with db.get_db() as conn:
        denied = conn.execute(
            """SELECT COUNT(*) AS count FROM access_audit_events
            WHERE action = 'account.permission.operate' AND outcome = 'denied'
              AND sandbox_id = 'beta'"""
        ).fetchone()["count"]
    assert denied == 1
    assert "beta@example.invalid" not in json.dumps(denied_update.json())


def test_automate_only_agent_cannot_mutate_account_metadata_or_history(
    accounts_client: TestClient,
):
    profile = db.create_profile("Automate only", sandbox_id="alpha")
    account = db.create_account_metadata(
        profile_id=profile["id"],
        provider="github",
        subject_label="automate@example.invalid",
        actor_kind="bootstrap",
    )
    automator = _create_agent(
        accounts_client,
        display_name="Automate-only account agent",
        sandbox_id="alpha",
        permission="automate",
    )

    assert accounts_client.get("/api/accounts", headers=automator).status_code == 200
    update = accounts_client.put(
        f"/api/accounts/{account['id']}",
        headers=automator,
        json={"auth_state": "signed_in"},
    )
    event = accounts_client.post(
        f"/api/accounts/{account['id']}/events",
        headers=automator,
        json={"event_type": "observed"},
    )
    assert update.status_code == event.status_code == 404
    assert db.get_account_metadata(account["id"])["auth_state"] == "unknown"
    assert [item["event_type"] for item in db.list_account_auth_events(account["id"])] == [
        "created"
    ]
