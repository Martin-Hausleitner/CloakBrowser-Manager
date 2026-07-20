"""SQLite database operations for browser profiles."""

from __future__ import annotations

import datetime
import json
import os
import random
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ENV_DATA_DIR = "CLOAKBROWSER_MANAGER_DATA_DIR"
DOCKER_DATA_DIR = Path("/data")
LOCAL_DATA_DIR = Path(__file__).resolve().parent / ".data"


def _is_usable_data_dir(path: Path) -> bool:
    """Return True if path can be created and written to."""
    probe = path / f".write-test-{os.getpid()}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_data_dir() -> Path:
    """Resolve the profile data directory for Docker and local development."""
    configured = os.environ.get(ENV_DATA_DIR)
    if configured:
        return Path(configured).expanduser()

    if _is_usable_data_dir(DOCKER_DATA_DIR):
        return DOCKER_DATA_DIR

    return LOCAL_DATA_DIR


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "profiles.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
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
                notes TEXT,
                user_data_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_tags (
                profile_id TEXT REFERENCES profiles(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                color TEXT,
                PRIMARY KEY (profile_id, tag)
            );

            CREATE TABLE IF NOT EXISTS access_users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_agents (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                paperclip_agent_id TEXT,
                key_hash TEXT NOT NULL UNIQUE,
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_grants (
                principal_type TEXT NOT NULL CHECK (principal_type IN ('user', 'agent')),
                principal_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                permission TEXT NOT NULL CHECK (permission IN ('view', 'interact', 'operate', 'automate')),
                created_at TEXT NOT NULL,
                PRIMARY KEY (principal_type, principal_id, sandbox_id, permission)
            );

            CREATE TABLE IF NOT EXISTS access_audit_events (
                id TEXT PRIMARY KEY,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                action TEXT NOT NULL,
                sandbox_id TEXT,
                profile_id TEXT,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()

        # Migrations for existing databases
        cols = {row[1] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "clipboard_sync" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN clipboard_sync BOOLEAN DEFAULT 1")
            conn.commit()
        if "launch_args" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN launch_args TEXT DEFAULT '[]'")
            conn.commit()
        if "auto_launch" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN auto_launch BOOLEAN DEFAULT 0")
            conn.commit()
        if "color_scheme" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN color_scheme TEXT")
            conn.commit()
        if "search_engine" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN search_engine TEXT")
            conn.commit()
        if "sandbox_id" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN sandbox_id TEXT NOT NULL DEFAULT 'default'")
            conn.commit()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def create_profile(
    name: str,
    fingerprint_seed: int | None = None,
    **fields: Any,
) -> dict[str, Any]:
    profile_id = str(uuid.uuid4())
    seed = fingerprint_seed if fingerprint_seed is not None else random.randint(10000, 99999)
    user_data_dir = str(DATA_DIR / "profiles" / profile_id)
    now = _now()
    tags = fields.pop("tags", None) or []

    with get_db() as conn:
        conn.execute(
            """INSERT INTO profiles (
                id, name, sandbox_id, fingerprint_seed, proxy, timezone, locale, platform,
                user_agent, screen_width, screen_height, gpu_vendor, gpu_renderer,
                hardware_concurrency, humanize, human_preset, headless, geoip,
                clipboard_sync, auto_launch, color_scheme, search_engine, launch_args, notes,
                user_data_dir, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id, name, fields.get("sandbox_id", "default"), seed,
                fields.get("proxy"),
                fields.get("timezone"),
                fields.get("locale"),
                fields.get("platform", "windows"),
                fields.get("user_agent"),
                fields.get("screen_width", 1920),
                fields.get("screen_height", 1080),
                fields.get("gpu_vendor"),
                fields.get("gpu_renderer"),
                fields.get("hardware_concurrency"),
                fields.get("humanize", False),
                fields.get("human_preset", "default"),
                fields.get("headless", False),
                fields.get("geoip", False),
                fields.get("clipboard_sync", True),
                fields.get("auto_launch", False),
                fields.get("color_scheme"),
                fields.get("search_engine"),
                json.dumps(fields.get("launch_args") or []),
                fields.get("notes"),
                user_data_dir, now, now,
            ),
        )
        for t in tags:
            conn.execute(
                "INSERT INTO profile_tags (profile_id, tag, color) VALUES (?, ?, ?)",
                (profile_id, t["tag"], t.get("color")),
            )
        conn.commit()

    return get_profile(profile_id)  # type: ignore[return-value]


def get_profile(profile_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            return None
        profile = dict(row)
        profile["launch_args"] = json.loads(profile.get("launch_args") or "[]")
        tags = conn.execute(
            "SELECT tag, color FROM profile_tags WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
        profile["tags"] = [dict(t) for t in tags]
        return profile


def list_profiles() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY created_at DESC").fetchall()
        profiles = []
        for row in rows:
            profile = dict(row)
            profile["launch_args"] = json.loads(profile.get("launch_args") or "[]")
            tags = conn.execute(
                "SELECT tag, color FROM profile_tags WHERE profile_id = ?",
                (profile["id"],),
            ).fetchall()
            profile["tags"] = [dict(t) for t in tags]
            profiles.append(profile)
        return profiles


def update_profile(profile_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_profile(profile_id)
    if not existing:
        return None

    tags = fields.pop("tags", None)

    # Only update fields that were explicitly provided
    update_cols = []
    update_vals = []
    # Pre-serialize launch_args to JSON before the generic update loop
    if "launch_args" in fields:
        fields["launch_args"] = json.dumps(fields["launch_args"] or [])

    for col in (
        "name", "sandbox_id", "fingerprint_seed", "proxy", "timezone", "locale", "platform",
        "user_agent", "screen_width", "screen_height", "gpu_vendor", "gpu_renderer",
        "hardware_concurrency", "humanize", "human_preset", "headless", "geoip",
        "clipboard_sync", "auto_launch", "color_scheme", "search_engine", "launch_args", "notes",
    ):
        if col in fields:
            update_cols.append(f"{col} = ?")
            update_vals.append(fields[col])

    if update_cols:
        update_cols.append("updated_at = ?")
        update_vals.append(_now())
        update_vals.append(profile_id)
        with get_db() as conn:
            conn.execute(
                f"UPDATE profiles SET {', '.join(update_cols)} WHERE id = ?",
                update_vals,
            )
            conn.commit()

    if tags is not None:
        with get_db() as conn:
            conn.execute("DELETE FROM profile_tags WHERE profile_id = ?", (profile_id,))
            for t in tags:
                conn.execute(
                    "INSERT INTO profile_tags (profile_id, tag, color) VALUES (?, ?, ?)",
                    (profile_id, t["tag"], t.get("color")),
                )
            conn.commit()

    return get_profile(profile_id)


def delete_profile(profile_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
        return cursor.rowcount > 0


# ── Access control persistence ───────────────────────────────────────────────


def _access_grants(conn: sqlite3.Connection, principal_type: str, principal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT sandbox_id, permission FROM access_grants
        WHERE principal_type = ? AND principal_id = ?
        ORDER BY sandbox_id, permission""",
        (principal_type, principal_id),
    ).fetchall()
    return [dict(row) for row in rows]


def set_access_grants(principal_type: str, principal_id: str, grants: list[dict[str, Any]]) -> None:
    if principal_type not in {"user", "agent"}:
        raise ValueError("Unsupported access principal type")
    now = _now()
    with get_db() as conn:
        conn.execute(
            "DELETE FROM access_grants WHERE principal_type = ? AND principal_id = ?",
            (principal_type, principal_id),
        )
        for grant in grants:
            conn.execute(
                """INSERT INTO access_grants
                (principal_type, principal_id, sandbox_id, permission, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (principal_type, principal_id, grant["sandbox_id"], grant["permission"], now),
            )
        conn.commit()


def create_access_user(username: str, password_hash: str, role: str = "viewer", grants: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO access_users
            (id, username, password_hash, role, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (user_id, username, password_hash, role, now, now),
        )
        conn.commit()
    set_access_grants("user", user_id, grants or [])
    return get_access_user(user_id)  # type: ignore[return-value]


def get_access_user(user_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM access_users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        user = dict(row)
        user["grants"] = _access_grants(conn, "user", user_id)
        return user


def get_access_user_by_username(username: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM access_users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if not row:
            return None
        user = dict(row)
        user["grants"] = _access_grants(conn, "user", user["id"])
        return user


def list_access_users() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM access_users ORDER BY username COLLATE NOCASE").fetchall()
        users: list[dict[str, Any]] = []
        for row in rows:
            user = dict(row)
            user["grants"] = _access_grants(conn, "user", user["id"])
            users.append(user)
        return users


def update_access_user(user_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_access_user(user_id)
    if not existing:
        return None
    grants = fields.pop("grants", None)
    update_cols: list[str] = []
    values: list[Any] = []
    for col in ("password_hash", "role", "active"):
        if col in fields:
            update_cols.append(f"{col} = ?")
            values.append(fields[col])
    if update_cols:
        update_cols.append("updated_at = ?")
        values.append(_now())
        values.append(user_id)
        with get_db() as conn:
            conn.execute(f"UPDATE access_users SET {', '.join(update_cols)} WHERE id = ?", values)
            conn.commit()
    if grants is not None:
        set_access_grants("user", user_id, grants)
    return get_access_user(user_id)


def create_access_agent(
    display_name: str,
    key_hash: str,
    paperclip_agent_id: str | None = None,
    grants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    agent_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO access_agents
            (id, display_name, paperclip_agent_id, key_hash, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (agent_id, display_name, paperclip_agent_id, key_hash, now, now),
        )
        conn.commit()
    set_access_grants("agent", agent_id, grants or [])
    return get_access_agent(agent_id)  # type: ignore[return-value]


def get_access_agent(agent_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM access_agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        agent = dict(row)
        agent["grants"] = _access_grants(conn, "agent", agent_id)
        return agent


def get_access_agent_by_key_hash(key_hash: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM access_agents WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        if not row:
            return None
        agent = dict(row)
        agent["grants"] = _access_grants(conn, "agent", agent["id"])
        return agent


def list_access_agents() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM access_agents ORDER BY display_name COLLATE NOCASE").fetchall()
        agents: list[dict[str, Any]] = []
        for row in rows:
            agent = dict(row)
            agent["grants"] = _access_grants(conn, "agent", agent["id"])
            agents.append(agent)
        return agents


def update_access_agent(agent_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_access_agent(agent_id)
    if not existing:
        return None
    grants = fields.pop("grants", None)
    update_cols: list[str] = []
    values: list[Any] = []
    for col in ("display_name", "paperclip_agent_id", "key_hash", "active"):
        if col in fields:
            update_cols.append(f"{col} = ?")
            values.append(fields[col])
    if update_cols:
        update_cols.append("updated_at = ?")
        values.append(_now())
        values.append(agent_id)
        with get_db() as conn:
            conn.execute(f"UPDATE access_agents SET {', '.join(update_cols)} WHERE id = ?", values)
            conn.commit()
    if grants is not None:
        set_access_grants("agent", agent_id, grants)
    return get_access_agent(agent_id)


def record_access_audit_event(
    actor_type: str,
    actor_id: str | None,
    action: str,
    outcome: str,
    sandbox_id: str | None = None,
    profile_id: str | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO access_audit_events
            (id, actor_type, actor_id, action, sandbox_id, profile_id, outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), actor_type, actor_id, action, sandbox_id, profile_id, outcome, _now()),
        )
        conn.commit()
