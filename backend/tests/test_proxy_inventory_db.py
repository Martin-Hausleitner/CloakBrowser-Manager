"""Database tests for proxy inventory persistence and redaction."""

from __future__ import annotations

from backend import database as db
from backend.proxy_inventory import parse_proxy_line, redact_proxy_url


def test_init_db_creates_proxy_inventory(tmp_db):
    with db.get_db() as conn:
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "proxy_inventory" in names


def test_upsert_list_and_check_never_return_secret(tmp_db):
    proxy_url = parse_proxy_line("10.20.30.40:8080:alice:super-secret")
    redacted = redact_proxy_url(proxy_url)
    row = db.upsert_proxy_inventory_entry(proxy_url, redacted=redacted)
    assert "proxy_url" not in row
    assert "super-secret" not in str(row)
    assert row["host_masked"] == "10.20.x.x"
    assert row["username_masked"] == "a***e"

    listed = db.list_proxy_inventory()
    assert len(listed) == 1
    assert "proxy_url" not in listed[0]
    assert "super-secret" not in str(listed)

    updated = db.update_proxy_inventory_check(
        row["id"],
        {
            "check_state": "passed",
            "reachable": True,
            "latency_ms": 12.5,
            "risk_score": 8,
            "authenticity_score": 92,
            "country_code": "DE",
            "timezone_hint": "Europe/Berlin",
            "locale_hint": "de-DE",
            "warnings": [],
            "blockers": [],
        },
    )
    assert updated is not None
    assert updated["check_state"] == "passed"
    assert updated["country_code"] == "DE"
    assert "proxy_url" not in updated

    secret = db.get_proxy_inventory_entry(row["id"], include_secret=True)
    assert secret is not None
    assert secret["proxy_url"] == proxy_url
