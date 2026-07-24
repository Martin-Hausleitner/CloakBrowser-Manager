"""Regression tests for the first agent-workspace database migration."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
from pathlib import Path

import pytest

from backend import database as db


def _create_legacy_database(path: Path) -> Path:
    database_path = path / "profiles.db"
    with sqlite3.connect(str(database_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sandbox_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL DEFAULT 'default',
                fingerprint_seed INTEGER NOT NULL,
                user_data_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE task_sessions (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                sandbox_id TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE task_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES task_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
                content TEXT NOT NULL,
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE task_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES task_sessions(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """INSERT INTO profiles
            (id, name, sandbox_id, project_id, fingerprint_seed, user_data_dir,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "profile-1",
                    "Alpha one",
                    "alpha",
                    "default",
                    10001,
                    str(path / "profiles" / "profile-1"),
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-02T00:00:00+00:00",
                ),
                (
                    "profile-2",
                    "Alpha two",
                    "alpha",
                    "default",
                    10002,
                    str(path / "profiles" / "profile-2"),
                    "2026-01-03T00:00:00+00:00",
                    "2026-01-04T00:00:00+00:00",
                ),
                (
                    "profile-3",
                    "Beta research",
                    "beta",
                    "research",
                    10003,
                    str(path / "profiles" / "profile-3"),
                    "2026-02-01T00:00:00+00:00",
                    "2026-02-03T00:00:00+00:00",
                ),
            ],
        )
        conn.executemany(
            """INSERT INTO task_sessions
            (id, profile_id, sandbox_id, title, status, created_by_kind, created_by_id,
             created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "task-1",
                    "profile-1",
                    "alpha",
                    "Legacy task",
                    "active",
                    "user",
                    "owner-1",
                    "2026-03-01T00:00:00+00:00",
                    "2026-03-02T00:00:00+00:00",
                    '{"source":"legacy"}',
                ),
                (
                    "task-2",
                    "profile-3",
                    "beta",
                    "Archived task",
                    "archived",
                    "agent",
                    "agent-1",
                    "2026-04-01T00:00:00+00:00",
                    "2026-04-02T00:00:00+00:00",
                    "{}",
                ),
            ],
        )
        conn.execute(
            """INSERT INTO task_messages
            (id, session_id, role, content, created_by_kind, created_by_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "message-1",
                "task-1",
                "user",
                "preserve me",
                "user",
                "owner-1",
                "2026-03-01T00:01:00+00:00",
                '{"kind":"legacy"}',
            ),
        )
        conn.execute(
            """INSERT INTO task_events
            (id, session_id, type, payload, created_by_kind, created_by_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "event-1",
                "task-1",
                "task_session.created",
                '{"legacy":true}',
                "user",
                "owner-1",
                "2026-03-01T00:02:00+00:00",
            ),
        )
        conn.commit()
    return database_path


@pytest.fixture()
def legacy_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database_path = _create_legacy_database(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", database_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    return database_path


def test_workspace_migration_preserves_history_and_snapshots_ownership(
    legacy_database: Path,
):
    db.init_db()

    task = db.get_task_session("task-1")
    archived = db.get_task_session("task-2")
    assert task == {
        "id": "task-1",
        "profile_id": "profile-1",
        "sandbox_id": "alpha",
        "project_id": "default",
        "title": "Legacy task",
        "status": "active",
        "workflow_state": "open",
        "done_at": None,
        "archived_at": None,
        "retention_class": "legacy",
        "expires_at": None,
        "activity_at": "2026-03-02T00:00:00+00:00",
        "row_version": 1,
        "created_by_kind": "user",
        "created_by_id": "owner-1",
        "created_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-02T00:00:00+00:00",
        "metadata": {"source": "legacy"},
    }
    assert archived["archived_at"] == "2026-04-02T00:00:00+00:00"
    assert db.list_task_messages("task-1")[0] == {
        "id": "message-1",
        "session_id": "task-1",
        "role": "user",
        "content": "preserve me",
        "created_by_kind": "user",
        "created_by_id": "owner-1",
        "created_at": "2026-03-01T00:01:00+00:00",
        "metadata": {"kind": "legacy"},
    }
    assert db.list_task_events("task-1")[0] == {
        "id": "event-1",
        "session_id": "task-1",
        "type": "task_session.created",
        "payload": {"legacy": True},
        "created_by_kind": "user",
        "created_by_id": "owner-1",
        "created_at": "2026-03-01T00:02:00+00:00",
    }

    with db.get_db() as conn:
        projects = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM projects ORDER BY sandbox_id, id"
            ).fetchall()
        ]
        migrations = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        profile_fk = next(
            row
            for row in conn.execute("PRAGMA foreign_key_list(task_sessions)").fetchall()
            if row["from"] == "profile_id"
        )

    assert projects == [
        {
            "sandbox_id": "alpha",
            "id": "default",
            "name": "default",
            "accent_color": None,
            "description": None,
            "default_retention": "project",
            "archived_at": None,
            "created_by_kind": "migration",
            "created_by_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-04T00:00:00+00:00",
        },
        {
            "sandbox_id": "beta",
            "id": "research",
            "name": "research",
            "accent_color": None,
            "description": None,
            "default_retention": "project",
            "archived_at": None,
            "created_by_kind": "migration",
            "created_by_id": None,
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-03T00:00:00+00:00",
        },
    ]
    assert [row["version"] for row in migrations] == [
        "agent_workspace_v1",
        "task_runs_v1",
    ]
    assert profile_fk["table"] == "profiles"
    assert profile_fk["on_delete"] == "SET NULL"

    with db.get_db() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        run_profile_fk = next(
            row
            for row in conn.execute("PRAGMA foreign_key_list(task_runs)").fetchall()
            if row["from"] == "profile_id"
        )
    assert "task_runs" in tables
    assert "task_outputs" in tables
    assert run_profile_fk["on_delete"] == "SET NULL"


def test_workspace_migration_is_idempotent(legacy_database: Path):
    db.init_db()
    db.init_db()

    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
        assert {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        } == {"agent_workspace_v1", "task_runs_v1"}
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM task_sessions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM task_messages").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_outputs").fetchone()[0] == 0


def test_workspace_migration_serializes_concurrent_initialization(
    legacy_database: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    real_connect = sqlite3.connect
    with real_connect(str(legacy_database)) as conn:
        conn.executescript(
            """
            ALTER TABLE profiles ADD COLUMN clipboard_sync BOOLEAN DEFAULT 1;
            ALTER TABLE profiles ADD COLUMN launch_args TEXT DEFAULT '[]';
            ALTER TABLE profiles ADD COLUMN auto_launch BOOLEAN DEFAULT 0;
            ALTER TABLE profiles ADD COLUMN color_scheme TEXT;
            ALTER TABLE profiles ADD COLUMN search_engine TEXT;
            ALTER TABLE profiles ADD COLUMN folder_path TEXT NOT NULL DEFAULT '';
            ALTER TABLE profiles ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0;
            ALTER TABLE profiles ADD COLUMN accent_color TEXT;
            ALTER TABLE profiles ADD COLUMN harness TEXT NOT NULL DEFAULT 'codex';
            """
        )
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    unsafe_check_barrier = threading.Barrier(2)

    class SynchronizedMigrationConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=()):  # type: ignore[no-untyped-def]
            cursor = super().execute(sql, parameters)
            if (
                not self.in_transaction
                and sql.lstrip().startswith("SELECT 1 FROM schema_migrations")
            ):
                unsafe_check_barrier.wait(timeout=5)
            return cursor

    def synchronized_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        return real_connect(*args, factory=SynchronizedMigrationConnection, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", synchronized_connect)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        initializations = [executor.submit(db.init_db) for _ in range(2)]
        for initialization in initializations:
            initialization.result(timeout=10)

    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
        assert {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        } == {"agent_workspace_v1", "task_runs_v1"}
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM task_sessions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 0
