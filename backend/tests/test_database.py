"""Tests for SQLite CRUD operations."""

from __future__ import annotations

import datetime
import sqlite3
import time
from pathlib import Path

import pytest

from backend import database as db


# ── data directory resolution ────────────────────────────────────────────────


def test_resolve_data_dir_uses_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(db.ENV_DATA_DIR, str(tmp_path))

    assert db._resolve_data_dir() == tmp_path


def test_resolve_data_dir_falls_back_to_local(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(db.ENV_DATA_DIR, raising=False)
    monkeypatch.setattr(db, "_is_usable_data_dir", lambda _path: False)

    assert db._resolve_data_dir() == db.LOCAL_DATA_DIR


# ── init_db ──────────────────────────────────────────────────────────────────


def test_init_db_creates_tables(tmp_db: Path):
    with db.get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
    assert "profiles" in names
    assert "profile_tags" in names
    assert "profile_health" in names


def test_init_db_idempotent(tmp_db: Path):
    # Second call should not crash
    db.init_db()
    with db.get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert len(tables) >= 2


def test_init_db_adds_profile_health_to_existing_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "profiles.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            """CREATE TABLE profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                fingerprint_seed INTEGER NOT NULL,
                user_data_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.commit()

    db.init_db()

    with db.get_db() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_health'"
        ).fetchone()
    assert table is not None


def test_init_db_migrates_profile_organization_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "profiles.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            """CREATE TABLE profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sandbox_id TEXT NOT NULL DEFAULT 'default',
                fingerprint_seed INTEGER NOT NULL,
                proxy TEXT,
                timezone TEXT,
                locale TEXT,
                platform TEXT DEFAULT 'windows',
                user_agent TEXT,
                screen_width INTEGER DEFAULT 1920,
                screen_height INTEGER DEFAULT 1080,
                gpu_vendor TEXT,
                gpu_renderer TEXT,
                hardware_concurrency INTEGER,
                humanize BOOLEAN DEFAULT 0,
                human_preset TEXT DEFAULT 'default',
                headless BOOLEAN DEFAULT 0,
                geoip BOOLEAN DEFAULT 0,
                clipboard_sync BOOLEAN DEFAULT 1,
                auto_launch BOOLEAN DEFAULT 0,
                color_scheme TEXT,
                search_engine TEXT,
                launch_args TEXT DEFAULT '[]',
                notes TEXT,
                user_data_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO profiles (
                id, name, sandbox_id, fingerprint_seed, user_data_dir, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("old-profile", "Old", "secure-sandbox", 12345, str(tmp_path / "profiles" / "old-profile"), now, now),
        )
        conn.commit()

    db.init_db()

    migrated = db.get_profile("old-profile")
    assert migrated["sandbox_id"] == "secure-sandbox"
    assert migrated["project_id"] == "default"
    assert migrated["folder_path"] == ""
    assert migrated["pinned"] == 0
    assert migrated["accent_color"] is None
    assert migrated["harness"] == "codex"


# ── create_profile ───────────────────────────────────────────────────────────


def test_create_profile_minimal(tmp_db: Path):
    p = db.create_profile("Test")
    assert p["name"] == "Test"
    assert isinstance(p["id"], str) and len(p["id"]) == 36  # UUID
    assert 10000 <= p["fingerprint_seed"] <= 99999  # random default
    assert p["user_data_dir"].startswith(str(tmp_db))
    assert p["platform"] == "windows"
    assert p["created_at"] is not None
    assert p["updated_at"] is not None


def test_create_profile_with_seed(tmp_db: Path):
    p = db.create_profile("Seeded", fingerprint_seed=42)
    assert p["fingerprint_seed"] == 42


def test_create_profile_all_fields(tmp_db: Path):
    p = db.create_profile(
        "Full",
        fingerprint_seed=99999,
        proxy="http://host:8080",
        timezone="America/New_York",
        locale="en-US",
        platform="macos",
        user_agent="Test UA",
        screen_width=2560,
        screen_height=1440,
        gpu_vendor="NVIDIA",
        gpu_renderer="RTX 3070",
        hardware_concurrency=16,
        humanize=True,
        human_preset="careful",
        headless=True,
        geoip=True,
        color_scheme="dark",
        search_engine="google",
        notes="test note",
    )
    assert p["proxy"] == "http://host:8080"
    assert p["platform"] == "macos"
    assert p["gpu_vendor"] == "NVIDIA"
    assert p["hardware_concurrency"] == 16
    assert p["humanize"] == 1  # SQLite stores bool as int
    assert p["human_preset"] == "careful"
    assert p["color_scheme"] == "dark"
    assert p["search_engine"] == "google"

def test_create_profile_with_tags(tmp_db: Path):
    p = db.create_profile(
        "Tagged",
        tags=[
            {"tag": "work", "color": "#ff0000"},
            {"tag": "dev", "color": "#00ff00"},
        ],
    )
    assert len(p["tags"]) == 2
    tag_names = {t["tag"] for t in p["tags"]}
    assert tag_names == {"work", "dev"}


def test_create_profile_defaults(tmp_db: Path):
    p = db.create_profile("Defaults")
    assert p["project_id"] == "default"
    assert p["folder_path"] == ""
    assert p["pinned"] == 0
    assert p["accent_color"] is None
    assert p["harness"] == "codex"
    assert p["platform"] == "windows"
    assert p["screen_width"] == 1920
    assert p["screen_height"] == 1080
    assert p["humanize"] == 0
    assert p["headless"] == 0
    assert p["geoip"] == 0
    assert p["human_preset"] == "default"
    assert p["launch_args"] == []


def test_create_profile_with_launch_args(tmp_db: Path):
    p = db.create_profile("WithArgs", launch_args=["--load-extension=/tmp/ext", "--disable-features=Foo"])
    assert p["launch_args"] == ["--load-extension=/tmp/ext", "--disable-features=Foo"]


def test_create_profile_with_organization_fields(tmp_db: Path):
    p = db.create_profile(
        "Organized",
        sandbox_id="secure-alpha",
        project_id="project-alpha",
        folder_path="research/phase-1",
        pinned=True,
        accent_color="#1A2B3C",
        harness="claude-code",
    )

    assert p["sandbox_id"] == "secure-alpha"
    assert p["project_id"] == "project-alpha"
    assert p["folder_path"] == "research/phase-1"
    assert p["pinned"] == 1
    assert p["accent_color"] == "#1A2B3C"
    assert p["harness"] == "claude-code"


def test_get_profile_launch_args_roundtrip(tmp_db: Path):
    p = db.create_profile("Args", launch_args=["--flag1", "--flag2"])
    fetched = db.get_profile(p["id"])
    assert fetched["launch_args"] == ["--flag1", "--flag2"]


def test_update_profile_launch_args(tmp_db: Path):
    p = db.create_profile("Args")
    assert p["launch_args"] == []
    updated = db.update_profile(p["id"], launch_args=["--new-flag"])
    assert updated["launch_args"] == ["--new-flag"]


def test_update_profile_launch_args_none_becomes_empty(tmp_db: Path):
    p = db.create_profile("Args", launch_args=["--flag"])
    updated = db.update_profile(p["id"], launch_args=None)
    assert updated["launch_args"] == []


def test_list_profiles_includes_launch_args(tmp_db: Path):
    db.create_profile("A", launch_args=["--arg1"])
    db.create_profile("B")
    profiles = db.list_profiles()
    args_by_name = {p["name"]: p["launch_args"] for p in profiles}
    assert args_by_name["A"] == ["--arg1"]
    assert args_by_name["B"] == []


# ── get_profile ──────────────────────────────────────────────────────────────


def test_get_profile_exists(sample_profile: dict):
    p = db.get_profile(sample_profile["id"])
    assert p is not None
    assert p["name"] == "Test Profile"
    assert p["fingerprint_seed"] == 12345


def test_get_profile_not_found(tmp_db: Path):
    assert db.get_profile("nonexistent") is None


def test_get_profile_includes_tags(tmp_db: Path):
    p = db.create_profile("Tagged", tags=[{"tag": "test", "color": "#aaa"}])
    fetched = db.get_profile(p["id"])
    assert len(fetched["tags"]) == 1
    assert fetched["tags"][0]["tag"] == "test"


# ── list_profiles ────────────────────────────────────────────────────────────


def test_list_profiles_empty(tmp_db: Path):
    assert db.list_profiles() == []


def test_list_profiles_ordered(tmp_db: Path):
    db.create_profile("First")
    time.sleep(0.01)  # ensure different timestamps
    db.create_profile("Second")
    profiles = db.list_profiles()
    assert len(profiles) == 2
    assert [profile["name"] for profile in profiles] == ["First", "Second"]


def test_list_profiles_orders_by_pin_project_folder_name(tmp_db: Path):
    db.create_profile("Zulu", project_id="beta", folder_path="")
    db.create_profile("Alpha", project_id="alpha", folder_path="z")
    db.create_profile("Bravo", project_id="alpha", folder_path="a", pinned=True)
    db.create_profile("Alpha", project_id="alpha", folder_path="a", pinned=True)
    db.create_profile("Charlie", project_id="alpha", folder_path="a")

    profiles = db.list_profiles()

    assert [p["name"] for p in profiles] == ["Alpha", "Bravo", "Charlie", "Alpha", "Zulu"]


def test_list_profiles_orders_identical_visible_keys_by_id(tmp_db: Path):
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    base_values = {
        "name": "Same",
        "sandbox_id": "same-sandbox",
        "project_id": "same-project",
        "folder_path": "same-folder",
        "pinned": 1,
        "fingerprint_seed": 12345,
        "user_data_dir": str(tmp_db / "profiles" / "placeholder"),
        "created_at": created_at,
        "updated_at": created_at,
    }
    with db.get_db() as conn:
        for profile_id in ("b-profile", "a-profile"):
            values = dict(base_values, id=profile_id, user_data_dir=str(tmp_db / "profiles" / profile_id))
            conn.execute(
                """INSERT INTO profiles (
                    id, name, sandbox_id, project_id, folder_path, pinned,
                    fingerprint_seed, user_data_dir, created_at, updated_at
                ) VALUES (
                    :id, :name, :sandbox_id, :project_id, :folder_path, :pinned,
                    :fingerprint_seed, :user_data_dir, :created_at, :updated_at
                )""",
                values,
            )
        conn.commit()

    profiles = db.list_profiles()

    assert [profile["id"] for profile in profiles] == ["a-profile", "b-profile"]


def test_list_profiles_includes_tags(tmp_db: Path):
    db.create_profile("Tagged", tags=[{"tag": "x"}])
    profiles = db.list_profiles()
    assert len(profiles[0]["tags"]) == 1


# ── profile health ──────────────────────────────────────────────────────────


def test_profile_health_upsert_roundtrip_and_replace(tmp_db: Path):
    profile = db.create_profile("Health")

    first = db.upsert_profile_health(
        profile["id"],
        state="warning",
        checked_at="2026-07-22T12:00:00+00:00",
        proxy_configured=True,
        proxy_reachable=True,
        outbound_ip_masked="203.0.113.x",
        proxy_latency_ms=42.5,
        proxy_risk_score=12,
        proxy_authenticity_score=88,
        fingerprint_consistency_score=90,
        browser_scan_score=None,
        warnings=["fingerprint_mismatch"],
        blockers=["browser_scan_consent"],
        error_code=None,
        sources={
            "browser_network": "measured",
            "proxy_authenticity": "derived",
            "browser_scan": "unavailable",
        },
    )

    assert first["profile_id"] == profile["id"]
    assert first["proxy_configured"] is True
    assert first["proxy_reachable"] is True
    assert first["warnings"] == ["fingerprint_mismatch"]
    assert first["blockers"] == ["browser_scan_consent"]
    assert first["sources"]["proxy_authenticity"] == "derived"

    replaced = db.upsert_profile_health(
        profile["id"],
        state="passed",
        checked_at="2026-07-22T12:05:00+00:00",
        proxy_configured=False,
        proxy_reachable=True,
        outbound_ip_masked="2001:db8:abcd:…",
        proxy_latency_ms=18.0,
        proxy_risk_score=None,
        proxy_authenticity_score=None,
        fingerprint_consistency_score=100,
        browser_scan_score=99,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={"browser_network": "measured", "browser_scan": "measured"},
    )

    with db.get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM profile_health WHERE profile_id = ?",
            (profile["id"],),
        ).fetchone()["count"]

    assert count == 1
    assert replaced["state"] == "passed"
    assert replaced["proxy_configured"] is False
    assert replaced["warnings"] == []
    assert db.get_profile_health(profile["id"]) == replaced


def test_profile_health_defensively_normalizes_corrupt_json(tmp_db: Path):
    profile = db.create_profile("Corrupt health")
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO profile_health (
                profile_id, state, proxy_configured, warnings_json, blockers_json, sources_json
            ) VALUES (?, 'unavailable', 0, ?, ?, ?)""",
            (profile["id"], '{"not":"a-list"}', '["ok", 42, null]', '["not-an-object"]'),
        )
        conn.commit()

    health = db.get_profile_health(profile["id"])

    assert health["warnings"] == []
    assert health["blockers"] == ["ok"]
    assert health["sources"] == {}


def test_profile_health_missing_returns_none(tmp_db: Path):
    profile = db.create_profile("No health")

    assert db.get_profile_health(profile["id"]) is None


def test_profile_health_is_deleted_with_profile(tmp_db: Path):
    profile = db.create_profile("Cascade health")
    db.upsert_profile_health(
        profile["id"],
        state="unavailable",
        proxy_configured=False,
        warnings=[],
        blockers=[],
        sources={},
    )

    assert db.delete_profile(profile["id"]) is True
    assert db.get_profile_health(profile["id"]) is None


# ── task sessions ────────────────────────────────────────────────────────────


def test_task_session_message_and_event_roundtrip(tmp_db: Path):
    profile = db.create_profile(
        "Task Browser",
        sandbox_id="tasks",
        project_id="research",
    )
    session = db.create_task_session(
        profile["id"],
        profile["sandbox_id"],
        "user",
        "user-1",
        "Research",
        {"source": "test"},
    )

    message = db.append_task_message(
        session["id"],
        "user",
        "Open the dashboard",
        "user",
        "user-1",
        {"intent": "navigate"},
    )
    event = db.record_task_event(
        session["id"],
        "task_command.appended",
        "user",
        "user-1",
        {"message_id": message["id"]},
    )

    stored_session = db.get_task_session(session["id"])
    assert stored_session["project_id"] == "research"
    assert stored_session["metadata"] == {"source": "test"}
    assert db.list_task_sessions(profile["id"])[0]["id"] == session["id"]
    assert db.list_task_messages(session["id"]) == [message]
    assert db.list_task_events(session["id"]) == [event]


def test_profile_delete_sets_null_and_preserves_task_history(tmp_db: Path):
    profile = db.create_profile("Task Browser")
    session = db.create_task_session(
        profile["id"],
        profile["sandbox_id"],
        "bootstrap",
        None,
    )
    db.append_task_message(session["id"], "user", "hello", "bootstrap")
    db.record_task_event(session["id"], "task_session.created", "bootstrap")

    assert db.delete_profile(profile["id"]) is True

    assert db.get_task_session(session["id"])["profile_id"] is None
    assert db.list_task_messages(session["id"])[0]["content"] == "hello"
    assert db.list_task_events(session["id"])[0]["type"] == "task_session.created"


# ── update_profile ───────────────────────────────────────────────────────────


def test_update_profile_partial(sample_profile: dict):
    updated = db.update_profile(sample_profile["id"], name="Renamed")
    assert updated["name"] == "Renamed"
    assert updated["fingerprint_seed"] == 12345  # unchanged


def test_update_profile_organization_fields(sample_profile: dict):
    updated = db.update_profile(
        sample_profile["id"],
        project_id="project-2",
        folder_path="ops/on-call",
        pinned=True,
        accent_color="#ABCDEF",
        harness="antigravity",
    )

    assert updated["project_id"] == "project-2"
    assert updated["folder_path"] == "ops/on-call"
    assert updated["pinned"] == 1
    assert updated["accent_color"] == "#ABCDEF"
    assert updated["harness"] == "antigravity"


def test_update_profile_tags_replace(tmp_db: Path):
    p = db.create_profile("Tagged", tags=[{"tag": "old"}])
    updated = db.update_profile(p["id"], tags=[{"tag": "new", "color": "#fff"}])
    assert len(updated["tags"]) == 1
    assert updated["tags"][0]["tag"] == "new"


def test_update_profile_not_found(tmp_db: Path):
    assert db.update_profile("nonexistent", name="x") is None


def test_update_profile_no_fields(sample_profile: dict):
    # No-op update — profile should be unchanged
    updated = db.update_profile(sample_profile["id"])
    assert updated["name"] == sample_profile["name"]


def test_update_profile_updates_timestamp(sample_profile: dict):
    time.sleep(0.01)
    updated = db.update_profile(sample_profile["id"], name="New")
    assert updated["updated_at"] > sample_profile["created_at"]


# ── delete_profile ───────────────────────────────────────────────────────────


def test_delete_profile_exists(sample_profile: dict):
    assert db.delete_profile(sample_profile["id"]) is True
    assert db.get_profile(sample_profile["id"]) is None


def test_delete_profile_not_found(tmp_db: Path):
    assert db.delete_profile("nonexistent") is False


def test_delete_profile_cascades_tags(tmp_db: Path):
    p = db.create_profile("Tagged", tags=[{"tag": "a"}, {"tag": "b"}])
    db.delete_profile(p["id"])
    # Verify tags are gone
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM profile_tags WHERE profile_id = ?", (p["id"],)
        ).fetchall()
    assert len(rows) == 0
