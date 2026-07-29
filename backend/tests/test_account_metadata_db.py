"""Database contract for profile-linked, secret-free account metadata."""

from __future__ import annotations

import sqlite3

import pytest

from backend import database as db


def test_init_db_creates_account_metadata_schema(tmp_db):
    with db.get_db() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        migrations = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert {"accounts", "account_auth_events"}.issubset(tables)
    assert "account_metadata_v1" in migrations


def test_account_metadata_hashes_references_and_never_returns_them(tmp_db):
    profile = db.create_profile(
        "Account browser",
        sandbox_id="research",
        project_id="agents",
    )

    account = db.create_account_metadata(
        profile_id=profile["id"],
        provider="github",
        subject_label="agent@example.invalid",
        display_name="Research agent",
        origin="https://github.com",
        auth_state="signed_in",
        second_factor_state="enrolled",
        passkey_state="off",
        secret_ref="secretref-login-1",
        totp_ref="secretref-totp-1",
        actor_kind="agent",
        actor_id="agent-1",
    )

    assert account["profile_id"] == profile["id"]
    assert account["sandbox_id"] == "research"
    assert account["project_id"] == "agents"
    assert account["has_secret_reference"] is True
    assert account["has_totp_reference"] is True
    assert "secret_ref" not in account
    assert "totp_ref" not in account

    with db.get_db() as conn:
        stored = dict(
            conn.execute(
                "SELECT secret_ref_digest, totp_ref_digest FROM accounts WHERE id = ?",
                (account["id"],),
            ).fetchone()
        )
    assert stored["secret_ref_digest"] != "secretref-login-1"
    assert stored["totp_ref_digest"] != "secretref-totp-1"
    assert len(stored["secret_ref_digest"]) == 97
    assert len(stored["totp_ref_digest"]) == 97
    assert stored["secret_ref_digest"] != stored["totp_ref_digest"]


def test_account_auth_history_is_append_only_and_survives_account_delete(tmp_db):
    profile = db.create_profile("History browser", sandbox_id="alpha")
    account = db.create_account_metadata(
        profile_id=profile["id"],
        provider="google",
        subject_label="history@example.invalid",
        actor_kind="user",
        actor_id="user-1",
    )
    event = db.append_account_auth_event(
        account["id"],
        event_type="signed_in",
        auth_state="signed_in",
        actor_kind="agent",
        actor_id="agent-1",
    )

    with db.get_db() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE account_auth_events SET event_type = 'signed_out' WHERE id = ?",
                (event["id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM account_auth_events WHERE id = ?", (event["id"],))

    assert db.delete_account_metadata(account["id"]) is True
    assert db.get_account_metadata(account["id"]) is None
    history = db.list_account_auth_events(account["id"])
    assert [item["event_type"] for item in history] == ["created", "signed_in"]
    assert all(item["account_id_snapshot"] == account["id"] for item in history)
