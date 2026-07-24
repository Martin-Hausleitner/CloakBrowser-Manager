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
PROFILE_HEALTH_SOURCE_STATES = {"missing", "measured", "derived", "unavailable", "skipped"}


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


def _create_workspace_task_sessions_table(conn: sqlite3.Connection, table_name: str) -> None:
    if table_name not in {"task_sessions", "task_sessions_workspace_v1"}:
        raise ValueError("Unsupported task sessions table name")
    conn.execute(
        f"""CREATE TABLE {table_name} (
            id TEXT PRIMARY KEY,
            profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
            sandbox_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            title TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
            workflow_state TEXT NOT NULL DEFAULT 'open'
                CHECK (workflow_state IN ('open', 'done')),
            done_at TEXT,
            archived_at TEXT,
            retention_class TEXT NOT NULL DEFAULT 'project'
                CHECK (retention_class IN ('temporary', 'project', 'legacy')),
            expires_at TEXT,
            activity_at TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            created_by_kind TEXT NOT NULL,
            created_by_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{{}}'
        )"""
    )


def _migrate_agent_workspace_v1(conn: sqlite3.Connection) -> None:
    """Snapshot legacy task ownership and make profile deletion history-safe."""
    migration_version = "agent_workspace_v1"
    foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.commit()
    if foreign_keys_enabled:
        conn.execute("PRAGMA foreign_keys=OFF")

    try:
        conn.execute("BEGIN IMMEDIATE")
        already_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone()
        if already_applied:
            conn.commit()
            return

        conn.execute(
            """INSERT OR IGNORE INTO projects (
                sandbox_id, id, name, accent_color, description, default_retention,
                archived_at, created_by_kind, created_by_id, created_at, updated_at
            )
            SELECT
                sandbox_id,
                project_id,
                project_id,
                NULL,
                NULL,
                'project',
                NULL,
                'migration',
                NULL,
                MIN(created_at),
                MAX(updated_at)
            FROM profiles
            GROUP BY sandbox_id, project_id"""
        )

        task_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(task_sessions)").fetchall()
        }
        profile_foreign_key = next(
            (
                row
                for row in conn.execute("PRAGMA foreign_key_list(task_sessions)").fetchall()
                if row["from"] == "profile_id"
            ),
            None,
        )
        required_columns = {
            "project_id",
            "workflow_state",
            "done_at",
            "archived_at",
            "retention_class",
            "expires_at",
            "activity_at",
            "row_version",
        }
        needs_rebuild = not required_columns.issubset(task_columns) or (
            profile_foreign_key is None or profile_foreign_key["on_delete"] != "SET NULL"
        )

        if needs_rebuild:
            conn.execute("DROP TABLE IF EXISTS task_sessions_workspace_v1")
            _create_workspace_task_sessions_table(conn, "task_sessions_workspace_v1")

            workflow_state = (
                "task_sessions.workflow_state" if "workflow_state" in task_columns else "'open'"
            )
            done_at = "task_sessions.done_at" if "done_at" in task_columns else "NULL"
            if "archived_at" in task_columns:
                archived_at = "task_sessions.archived_at"
            else:
                archived_at = (
                    "CASE WHEN task_sessions.status = 'archived' "
                    "THEN task_sessions.updated_at ELSE NULL END"
                )
            retention_class = (
                "task_sessions.retention_class"
                if "retention_class" in task_columns
                else "'legacy'"
            )
            expires_at = "task_sessions.expires_at" if "expires_at" in task_columns else "NULL"
            activity_at = (
                "task_sessions.activity_at"
                if "activity_at" in task_columns
                else "task_sessions.updated_at"
            )
            row_version = (
                "task_sessions.row_version" if "row_version" in task_columns else "1"
            )

            conn.execute(
                f"""INSERT INTO task_sessions_workspace_v1 (
                    id, profile_id, sandbox_id, project_id, title, status,
                    workflow_state, done_at, archived_at, retention_class, expires_at,
                    activity_at, row_version, created_by_kind, created_by_id,
                    created_at, updated_at, metadata
                )
                SELECT
                    task_sessions.id,
                    task_sessions.profile_id,
                    COALESCE(profiles.sandbox_id, task_sessions.sandbox_id, 'default'),
                    COALESCE(profiles.project_id, 'default'),
                    task_sessions.title,
                    task_sessions.status,
                    {workflow_state},
                    {done_at},
                    {archived_at},
                    {retention_class},
                    {expires_at},
                    {activity_at},
                    {row_version},
                    task_sessions.created_by_kind,
                    task_sessions.created_by_id,
                    task_sessions.created_at,
                    task_sessions.updated_at,
                    task_sessions.metadata
                FROM task_sessions
                LEFT JOIN profiles ON profiles.id = task_sessions.profile_id"""
            )
            conn.execute("DROP TABLE task_sessions")
            conn.execute(
                "ALTER TABLE task_sessions_workspace_v1 RENAME TO task_sessions"
            )
            conn.execute(
                """CREATE INDEX idx_task_sessions_profile
                ON task_sessions(profile_id, created_at DESC)"""
            )
            conn.execute(
                """CREATE INDEX idx_task_sessions_sandbox
                ON task_sessions(sandbox_id, created_at DESC)"""
            )

        violation = conn.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise RuntimeError(
                f"Foreign key violation after {migration_version}: {tuple(violation)}"
            )
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (migration_version, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")


def _migrate_task_runs_v1(conn: sqlite3.Connection) -> None:
    """Add task_runs and task_outputs without rebuilding task_sessions."""
    migration_version = "task_runs_v1"
    conn.execute("BEGIN IMMEDIATE")
    try:
        already_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone()
        if already_applied:
            conn.commit()
            return

        # Individual executes keep DDL inside BEGIN IMMEDIATE (executescript commits).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id TEXT PRIMARY KEY,
                task_session_id TEXT NOT NULL REFERENCES task_sessions(id) ON DELETE CASCADE,
                task_message_id TEXT NOT NULL REFERENCES task_messages(id),
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
                profile_id_snapshot TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                harness TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'queued', 'health_check', 'blocked_health', 'running',
                        'succeeded', 'failed', 'cancelled', 'revoked'
                    )
                ),
                launch_if_stopped BOOLEAN NOT NULL DEFAULT 0,
                allowed_origins_json TEXT NOT NULL DEFAULT '[]',
                max_steps INTEGER NOT NULL CHECK (max_steps >= 1),
                timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds >= 1),
                model_alias TEXT,
                deadline_at TEXT NOT NULL,
                health_snapshot_json TEXT NOT NULL,
                health_decision_json TEXT NOT NULL,
                health_override_json TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                first_action_sequence INTEGER,
                first_action_at TEXT,
                next_output_sequence INTEGER NOT NULL DEFAULT 0 CHECK (next_output_sequence >= 0),
                claimed_by TEXT,
                claim_expires_at TEXT,
                worker_id TEXT,
                claim_eligible_at TEXT,
                cancelled_at TEXT,
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_runs_session
                ON task_runs(task_session_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_runs_status
                ON task_runs(status, created_at ASC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_runs_profile
                ON task_runs(profile_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_outputs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL CHECK (sequence >= 1),
                idempotency_key TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (
                    kind IN (
                        'status', 'action', 'observation', 'screenshot',
                        'extracted_data', 'link', 'metric', 'error',
                        'approval', 'summary'
                    )
                ),
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE (run_id, idempotency_key),
                UNIQUE (run_id, sequence)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_outputs_run_sequence
                ON task_outputs(run_id, sequence ASC)
            """
        )
        violation = conn.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise RuntimeError(
                f"Foreign key violation after {migration_version}: {tuple(violation)}"
            )
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (migration_version, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sandbox_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL DEFAULT 'default',
                folder_path TEXT NOT NULL DEFAULT '',
                pinned BOOLEAN NOT NULL DEFAULT 0,
                accent_color TEXT,
                harness TEXT NOT NULL DEFAULT 'codex',
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

            CREATE TABLE IF NOT EXISTS profile_health (
                profile_id TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                state TEXT NOT NULL DEFAULT 'unavailable'
                    CHECK (state IN ('pending', 'running', 'passed', 'warning', 'failed', 'unavailable')),
                checked_at TEXT,
                proxy_configured BOOLEAN NOT NULL DEFAULT 0,
                proxy_reachable BOOLEAN,
                outbound_ip_masked TEXT,
                proxy_latency_ms REAL,
                proxy_risk_score INTEGER,
                proxy_authenticity_score INTEGER,
                fingerprint_consistency_score INTEGER,
                browser_scan_score INTEGER,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                blockers_json TEXT NOT NULL DEFAULT '[]',
                error_code TEXT,
                sources_json TEXT NOT NULL DEFAULT '{}'
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

            CREATE TABLE IF NOT EXISTS access_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT,
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_group_members (
                group_id TEXT NOT NULL REFERENCES access_groups(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES access_users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS access_group_grants (
                group_id TEXT NOT NULL REFERENCES access_groups(id) ON DELETE CASCADE,
                sandbox_id TEXT NOT NULL,
                permission TEXT NOT NULL CHECK (permission IN ('view', 'interact', 'operate', 'automate')),
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, sandbox_id, permission)
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

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                sandbox_id TEXT NOT NULL,
                id TEXT NOT NULL,
                name TEXT NOT NULL,
                accent_color TEXT,
                description TEXT,
                default_retention TEXT NOT NULL DEFAULT 'project'
                    CHECK (default_retention IN ('temporary', 'project')),
                archived_at TEXT,
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (sandbox_id, id)
            );

            CREATE TABLE IF NOT EXISTS task_sessions (
                id TEXT PRIMARY KEY,
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
                sandbox_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT 'default',
                title TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                workflow_state TEXT NOT NULL DEFAULT 'open'
                    CHECK (workflow_state IN ('open', 'done')),
                done_at TEXT,
                archived_at TEXT,
                retention_class TEXT NOT NULL DEFAULT 'project'
                    CHECK (retention_class IN ('temporary', 'project', 'legacy')),
                expires_at TEXT,
                activity_at TEXT NOT NULL,
                row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_task_sessions_profile
                ON task_sessions(profile_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_task_sessions_sandbox
                ON task_sessions(sandbox_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS task_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES task_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
                content TEXT NOT NULL,
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_task_messages_session
                ON task_messages(session_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS task_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES task_sessions(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_by_kind TEXT NOT NULL,
                created_by_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_task_events_session
                ON task_events(session_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS proxy_inventory (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                proxy_url TEXT NOT NULL,
                host_masked TEXT NOT NULL,
                port INTEGER,
                username_masked TEXT,
                has_credentials BOOLEAN NOT NULL DEFAULT 0,
                label TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT 1,
                check_state TEXT NOT NULL DEFAULT 'missing'
                    CHECK (check_state IN ('missing', 'passed', 'warning', 'failed', 'unavailable')),
                reachable BOOLEAN,
                latency_ms REAL,
                risk_score INTEGER,
                authenticity_score INTEGER,
                country_code TEXT,
                timezone_hint TEXT,
                locale_hint TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                blockers_json TEXT NOT NULL DEFAULT '[]',
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_proxy_inventory_active
                ON proxy_inventory(active, updated_at DESC);
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
        if "project_id" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
            conn.commit()
        if "folder_path" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''")
            conn.commit()
        if "pinned" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0")
            conn.commit()
        if "accent_color" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN accent_color TEXT")
            conn.commit()
        if "harness" not in cols:
            conn.execute("ALTER TABLE profiles ADD COLUMN harness TEXT NOT NULL DEFAULT 'codex'")
            conn.commit()
        _migrate_agent_workspace_v1(conn)
        _migrate_task_runs_v1(conn)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class OptimisticConcurrencyError(Exception):
    """Raised when an update loses an optimistic row_version check."""


class ProjectConflictError(Exception):
    """Raised when creating a project that already exists in a sandbox."""


class TaskArchivedError(Exception):
    """Raised when appending to an archived task session."""


def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def ensure_project(
    sandbox_id: str,
    project_id: str,
    *,
    name: str | None = None,
    created_by_kind: str = "system",
    created_by_id: str | None = None,
    default_retention: str = "project",
    accent_color: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a project row if missing; never overwrite an existing one."""
    now = _now()
    sid = str(sandbox_id or "default")
    pid = str(project_id or "default")
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO projects (
                sandbox_id, id, name, accent_color, description, default_retention,
                archived_at, created_by_kind, created_by_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (
                sid,
                pid,
                name or pid,
                accent_color,
                description,
                default_retention,
                created_by_kind,
                created_by_id,
                now,
                now,
            ),
        )
        conn.commit()
    project = get_project(sid, pid)
    if project is None:
        raise RuntimeError(f"Failed to ensure project {sid}/{pid}")
    return project


def create_project(
    sandbox_id: str,
    project_id: str,
    name: str,
    *,
    created_by_kind: str,
    created_by_id: str | None = None,
    accent_color: str | None = None,
    description: str | None = None,
    default_retention: str = "project",
) -> dict[str, Any]:
    now = _now()
    sid = str(sandbox_id or "default")
    pid = str(project_id)
    with get_db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM projects WHERE sandbox_id = ? AND id = ?",
                (sid, pid),
            ).fetchone()
            if existing:
                conn.commit()
                raise ProjectConflictError(f"Project already exists: {sid}/{pid}")
            conn.execute(
                """INSERT INTO projects (
                    sandbox_id, id, name, accent_color, description, default_retention,
                    archived_at, created_by_kind, created_by_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    sid,
                    pid,
                    name,
                    accent_color,
                    description,
                    default_retention,
                    created_by_kind,
                    created_by_id,
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ProjectConflictError(f"Project already exists: {sid}/{pid}") from exc
    project = get_project(sid, pid)
    if project is None:
        raise RuntimeError(f"Failed to create project {sid}/{pid}")
    return project


def get_project(sandbox_id: str, project_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE sandbox_id = ? AND id = ?",
            (str(sandbox_id or "default"), str(project_id)),
        ).fetchone()
        return _project_from_row(row) if row else None


def list_projects(sandbox_id: str, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM projects
            WHERE sandbox_id = ?
            ORDER BY archived_at IS NOT NULL, name ASC, id ASC
            LIMIT ?""",
            (str(sandbox_id or "default"), safe_limit),
        ).fetchall()
        return [_project_from_row(row) for row in rows]


def update_project(sandbox_id: str, project_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_project(sandbox_id, project_id)
    if not existing:
        return None

    updates: dict[str, Any] = {}
    if "name" in fields and fields["name"] is not None:
        updates["name"] = fields["name"]
    if "accent_color" in fields:
        updates["accent_color"] = fields["accent_color"]
    if "description" in fields:
        updates["description"] = fields["description"]
    if "default_retention" in fields and fields["default_retention"] is not None:
        updates["default_retention"] = fields["default_retention"]
    if "archived" in fields and fields["archived"] is not None:
        updates["archived_at"] = _now() if fields["archived"] else None

    if not updates:
        return existing

    updates["updated_at"] = _now()
    columns = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values()) + [str(sandbox_id or "default"), str(project_id)]
    with get_db() as conn:
        conn.execute(
            f"UPDATE projects SET {columns} WHERE sandbox_id = ? AND id = ?",
            values,
        )
        conn.commit()
    return get_project(sandbox_id, project_id)


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
    sandbox_id = fields.get("sandbox_id", "default")
    project_id = fields.get("project_id", "default")

    with get_db() as conn:
        conn.execute(
            """INSERT INTO profiles (
                id, name, sandbox_id, project_id, folder_path, pinned, accent_color, harness,
                fingerprint_seed, proxy, timezone, locale, platform,
                user_agent, screen_width, screen_height, gpu_vendor, gpu_renderer,
                hardware_concurrency, humanize, human_preset, headless, geoip,
                clipboard_sync, auto_launch, color_scheme, search_engine, launch_args, notes,
                user_data_dir, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id, name, sandbox_id,
                project_id,
                fields.get("folder_path", ""),
                fields.get("pinned", False),
                fields.get("accent_color"),
                fields.get("harness", "codex"),
                seed,
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

    ensure_project(
        str(sandbox_id or "default"),
        str(project_id or "default"),
        created_by_kind="system",
    )
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
        rows = conn.execute(
            """SELECT * FROM profiles
            ORDER BY pinned DESC, project_id ASC, folder_path ASC, name ASC, created_at DESC, id ASC"""
        ).fetchall()
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
        "name", "sandbox_id", "project_id", "folder_path", "pinned", "accent_color", "harness",
        "fingerprint_seed", "proxy", "timezone", "locale", "platform",
        "user_agent", "screen_width", "screen_height", "gpu_vendor", "gpu_renderer",
        "hardware_concurrency", "humanize", "human_preset", "headless", "geoip",
        "clipboard_sync", "auto_launch", "color_scheme", "search_engine", "launch_args", "notes",
    ):
        if col in fields:
            update_cols.append(f"{col} = ?")
            update_vals.append(fields[col])

    if update_cols:
        update_cols.append("updated_at = ?")
        now = _now()
        update_vals.append(now)
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

    updated = get_profile(profile_id)
    if updated is not None:
        ensure_project(
            str(updated.get("sandbox_id") or "default"),
            str(updated.get("project_id") or "default"),
            created_by_kind="system",
        )
    return updated


def bulk_organize_profiles(
    profile_ids: list[str],
    *,
    project_id: str | None = None,
    folder_path: str | None = None,
    pinned: bool | None = None,
) -> list[dict[str, Any]]:
    """Apply organization fields to many profiles using the normal update path."""
    if not profile_ids:
        return []
    fields: dict[str, Any] = {}
    if project_id is not None:
        fields["project_id"] = project_id
    if folder_path is not None:
        fields["folder_path"] = folder_path
    if pinned is not None:
        fields["pinned"] = pinned
    if not fields:
        raise ValueError("No organization fields provided")

    updated: list[dict[str, Any]] = []
    for profile_id in profile_ids:
        row = update_profile(profile_id, **fields)
        if row is not None:
            updated.append(row)
    return updated


def delete_profile(profile_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
        return cursor.rowcount > 0


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _bounded_string_list(value: object, *, max_length: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and len(item) <= max_length]


def _json_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return _bounded_string_list(decoded)


def _bounded_string_map(value: object, *, max_length: int = 64) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
        and isinstance(item, str)
        and len(key) <= max_length
        and len(item) <= max_length
    }


def _profile_health_from_row(row: sqlite3.Row) -> dict[str, Any]:
    health = dict(row)
    health["proxy_configured"] = bool(health.get("proxy_configured"))
    if health.get("proxy_reachable") is not None:
        health["proxy_reachable"] = bool(health["proxy_reachable"])
    health["warnings"] = _json_string_list(health.pop("warnings_json", None))
    health["blockers"] = _json_string_list(health.pop("blockers_json", None))
    sources = _bounded_string_map(_json_object(health.pop("sources_json", None)))
    health["sources"] = {
        key: value for key, value in sources.items() if value in PROFILE_HEALTH_SOURCE_STATES
    }
    return health


def get_profile_health(profile_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM profile_health WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
    return _profile_health_from_row(row) if row else None


def upsert_profile_health(
    profile_id: str,
    *,
    state: str,
    checked_at: str | None = None,
    proxy_configured: bool = False,
    proxy_reachable: bool | None = None,
    outbound_ip_masked: str | None = None,
    proxy_latency_ms: float | None = None,
    proxy_risk_score: int | None = None,
    proxy_authenticity_score: int | None = None,
    fingerprint_consistency_score: int | None = None,
    browser_scan_score: int | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    error_code: str | None = None,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized_warnings = _bounded_string_list(warnings or [])
    normalized_blockers = _bounded_string_list(blockers or [])
    normalized_sources = {
        key: value
        for key, value in _bounded_string_map(sources or {}).items()
        if value in PROFILE_HEALTH_SOURCE_STATES
    }
    safe_error_code = error_code if isinstance(error_code, str) and len(error_code) <= 64 else None
    safe_masked_ip = (
        outbound_ip_masked
        if isinstance(outbound_ip_masked, str) and len(outbound_ip_masked) <= 64
        else None
    )

    with get_db() as conn:
        conn.execute(
            """INSERT INTO profile_health (
                profile_id, state, checked_at, proxy_configured, proxy_reachable,
                outbound_ip_masked, proxy_latency_ms, proxy_risk_score,
                proxy_authenticity_score, fingerprint_consistency_score,
                browser_scan_score, warnings_json, blockers_json, error_code, sources_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                state = excluded.state,
                checked_at = excluded.checked_at,
                proxy_configured = excluded.proxy_configured,
                proxy_reachable = excluded.proxy_reachable,
                outbound_ip_masked = excluded.outbound_ip_masked,
                proxy_latency_ms = excluded.proxy_latency_ms,
                proxy_risk_score = excluded.proxy_risk_score,
                proxy_authenticity_score = excluded.proxy_authenticity_score,
                fingerprint_consistency_score = excluded.fingerprint_consistency_score,
                browser_scan_score = excluded.browser_scan_score,
                warnings_json = excluded.warnings_json,
                blockers_json = excluded.blockers_json,
                error_code = excluded.error_code,
                sources_json = excluded.sources_json""",
            (
                profile_id,
                state,
                checked_at,
                proxy_configured,
                proxy_reachable,
                safe_masked_ip,
                proxy_latency_ms,
                proxy_risk_score,
                proxy_authenticity_score,
                fingerprint_consistency_score,
                browser_scan_score,
                json.dumps(normalized_warnings, separators=(",", ":")),
                json.dumps(normalized_blockers, separators=(",", ":")),
                safe_error_code,
                json.dumps(normalized_sources, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.commit()

    health = get_profile_health(profile_id)
    if health is None:  # pragma: no cover - the successful upsert always creates the row
        raise RuntimeError("profile health upsert did not create a row")
    return health


# ── Task session persistence ────────────────────────────────────────────────


def _task_session_from_row(row: sqlite3.Row) -> dict[str, Any]:
    session = dict(row)
    session["metadata"] = _json_object(session.get("metadata"))
    return session


def _task_message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    message = dict(row)
    message["metadata"] = _json_object(message.get("metadata"))
    return message


def _task_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    event["payload"] = _json_object(event.get("payload"))
    return event


def create_task_session(
    profile_id: str,
    sandbox_id: str,
    created_by_kind: str,
    created_by_id: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        profile = conn.execute(
            "SELECT project_id FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        project_id = str(profile["project_id"] or "default") if profile else "default"
        conn.execute(
            """INSERT INTO task_sessions
            (id, profile_id, sandbox_id, project_id, title, status, workflow_state,
             retention_class, activity_at, row_version, created_by_kind, created_by_id,
             created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, 'active', 'open', 'project', ?, 1, ?, ?, ?, ?, ?)""",
            (
                session_id,
                profile_id,
                sandbox_id,
                project_id,
                title,
                now,
                created_by_kind,
                created_by_id,
                now,
                now,
                json.dumps(metadata or {}, separators=(",", ":")),
            ),
        )
        conn.commit()
    ensure_project(str(sandbox_id or "default"), project_id, created_by_kind="system")
    return get_task_session(session_id)  # type: ignore[return-value]


def get_task_session(session_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM task_sessions WHERE id = ?", (session_id,)).fetchone()
        return _task_session_from_row(row) if row else None


def list_task_sessions(profile_id: str, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM task_sessions
            WHERE profile_id = ?
            ORDER BY created_at DESC
            LIMIT ?""",
            (profile_id, safe_limit),
        ).fetchall()
        return [_task_session_from_row(row) for row in rows]


def update_task_session(
    session_id: str,
    *,
    expected_row_version: int,
    title: str | None = None,
    workflow_state: str | None = None,
    archived: bool | None = None,
    retention_class: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Apply lifecycle updates with optimistic concurrency on row_version."""
    now = _now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM task_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        current = _task_session_from_row(row)
        if int(current["row_version"]) != int(expected_row_version):
            conn.commit()
            raise OptimisticConcurrencyError(
                f"Task session row_version conflict for {session_id}"
            )

        updates: dict[str, Any] = {
            "updated_at": now,
            "activity_at": now,
            "row_version": int(current["row_version"]) + 1,
        }
        if title is not None:
            updates["title"] = title
        if metadata is not None:
            updates["metadata"] = json.dumps(metadata, separators=(",", ":"))
        if retention_class is not None:
            if retention_class not in {"temporary", "project"}:
                conn.commit()
                raise ValueError("retention_class must be temporary or project")
            updates["retention_class"] = retention_class
        if workflow_state is not None:
            if workflow_state not in {"open", "done"}:
                conn.commit()
                raise ValueError("workflow_state must be open or done")
            updates["workflow_state"] = workflow_state
            updates["done_at"] = now if workflow_state == "done" else None
        if archived is not None:
            if archived:
                updates["archived_at"] = now
                updates["status"] = "archived"
            else:
                updates["archived_at"] = None
                updates["status"] = "active"

        columns = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [session_id, int(expected_row_version)]
        cursor = conn.execute(
            f"""UPDATE task_sessions
            SET {columns}
            WHERE id = ? AND row_version = ?""",
            values,
        )
        if cursor.rowcount != 1:
            conn.commit()
            raise OptimisticConcurrencyError(
                f"Task session row_version conflict for {session_id}"
            )
        conn.commit()
    return get_task_session(session_id)


def append_task_message(
    session_id: str,
    role: str,
    content: str,
    created_by_kind: str,
    created_by_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, archived_at FROM task_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is not None and (
            row["status"] == "archived" or row["archived_at"] is not None
        ):
            conn.commit()
            raise TaskArchivedError(f"Task session is archived: {session_id}")
        conn.execute(
            """INSERT INTO task_messages
            (id, session_id, role, content, created_by_kind, created_by_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                session_id,
                role,
                content,
                created_by_kind,
                created_by_id,
                now,
                json.dumps(metadata or {}, separators=(",", ":")),
            ),
        )
        conn.execute(
            "UPDATE task_sessions SET updated_at = ?, activity_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        conn.commit()
    return get_task_message(message_id)  # type: ignore[return-value]


def get_task_message(message_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM task_messages WHERE id = ?", (message_id,)).fetchone()
        return _task_message_from_row(row) if row else None


def list_task_messages(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM task_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?""",
            (session_id, safe_limit),
        ).fetchall()
        return [_task_message_from_row(row) for row in rows]


def record_task_event(
    session_id: str,
    event_type: str,
    created_by_kind: str,
    created_by_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO task_events
            (id, session_id, type, payload, created_by_kind, created_by_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                session_id,
                event_type,
                json.dumps(payload or {}, separators=(",", ":")),
                created_by_kind,
                created_by_id,
                now,
            ),
        )
        conn.commit()
    return get_task_event(event_id)  # type: ignore[return-value]


def get_task_event(event_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM task_events WHERE id = ?", (event_id,)).fetchone()
        return _task_event_from_row(row) if row else None


def list_task_events(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM task_events
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?""",
            (session_id, safe_limit),
        ).fetchall()
        return [_task_event_from_row(row) for row in rows]


# ── Task run persistence ─────────────────────────────────────────────────────


TASK_RUN_STATUSES = frozenset(
    {
        "queued",
        "health_check",
        "blocked_health",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "revoked",
    }
)
TASK_RUN_CANCELABLE_STATUSES = frozenset(
    {"queued", "health_check", "blocked_health", "running"}
)
TASK_RUN_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "revoked"}
)


class TaskOutputConflictError(Exception):
    """Raised when an idempotency key is reused with a conflicting payload."""


def _task_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    run["launch_if_stopped"] = bool(run.get("launch_if_stopped"))
    run["allowed_origins"] = _json_string_list(run.pop("allowed_origins_json", None))
    run["health_snapshot"] = _json_object(run.pop("health_snapshot_json", None))
    run["health_decision"] = _json_object(run.pop("health_decision_json", None))
    override = run.pop("health_override_json", None)
    run["health_override"] = _json_object(override) if override else None
    run.pop("next_output_sequence", None)
    return run


def _task_output_from_row(row: sqlite3.Row) -> dict[str, Any]:
    output = dict(row)
    output["payload"] = _json_object(output.pop("payload_json", None))
    return output


def _status_from_health_decision(decision: dict[str, Any]) -> str:
    if decision.get("waiting"):
        return "health_check"
    if not decision.get("allowed"):
        return "blocked_health"
    return "queued"


def build_run_health_gate(profile_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy an immutable health snapshot/decision from the current profile row."""
    if __package__:
        from .profile_health import ProfileHealthResult, map_profile_health_gate_fields
        from .run_health import HEALTH_POLICY_VERSION, HealthSnapshot, evaluate_health
    else:  # pragma: no cover - flat uvicorn import path
        from profile_health import ProfileHealthResult, map_profile_health_gate_fields
        from run_health import HEALTH_POLICY_VERSION, HealthSnapshot, evaluate_health

    row = get_profile_health(profile_id)
    if row is None:
        snapshot = HealthSnapshot(
            state="unavailable",
            checked_at=None,
            proxy_configured=False,
            proxy_reachable=None,
            measured_authenticity_score=None,
            inferred_authenticity_score=None,
            reasons=(),
            measurement_error=True,
            policy_version=HEALTH_POLICY_VERSION,
            outbound_ip_masked=None,
        )
    else:
        result = ProfileHealthResult(
            state=str(row["state"]),
            checked_at=str(row["checked_at"] or ""),
            proxy_configured=bool(row["proxy_configured"]),
            proxy_reachable=row.get("proxy_reachable"),
            outbound_ip_masked=row.get("outbound_ip_masked"),
            proxy_latency_ms=row.get("proxy_latency_ms"),
            proxy_risk_score=row.get("proxy_risk_score"),
            proxy_authenticity_score=row.get("proxy_authenticity_score"),
            fingerprint_consistency_score=row.get("fingerprint_consistency_score"),
            browser_scan_score=row.get("browser_scan_score"),
            warnings=tuple(row.get("warnings") or ()),
            blockers=tuple(row.get("blockers") or ()),
            error_code=row.get("error_code"),
            sources=dict(row.get("sources") or {}),
        )
        fields = map_profile_health_gate_fields(result)
        checked_raw = row.get("checked_at")
        snapshot = HealthSnapshot(
            state=str(fields["state"]),
            checked_at=(
                datetime.datetime.fromisoformat(str(checked_raw))
                if isinstance(checked_raw, str) and checked_raw
                else None
            ),
            proxy_configured=bool(fields["proxy_configured"]),
            proxy_reachable=fields["proxy_reachable"],  # type: ignore[arg-type]
            measured_authenticity_score=fields["measured_authenticity_score"],  # type: ignore[arg-type]
            inferred_authenticity_score=fields["inferred_authenticity_score"],  # type: ignore[arg-type]
            reasons=tuple(fields["reasons"]),  # type: ignore[arg-type]
            measurement_error=bool(fields["measurement_error"]),
            policy_version=str(fields["policy_version"]),
            outbound_ip_masked=(
                str(fields["outbound_ip_masked"])
                if fields.get("outbound_ip_masked") is not None
                else None
            ),
        )
    decision = evaluate_health(snapshot)
    return snapshot.to_dict(), decision.to_dict()


def _insert_task_run_on_conn(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    task_session_id: str,
    task_message_id: str,
    profile_id: str,
    sandbox_id: str,
    harness: str,
    status: str,
    launch_if_stopped: bool,
    allowed_origins: list[str],
    max_steps: int,
    timeout_seconds: int,
    model_alias: str | None,
    deadline_at: str,
    health_snapshot: dict[str, Any],
    health_decision: dict[str, Any],
    created_by_kind: str,
    created_by_id: str | None,
    now: str,
) -> None:
    """Insert a task_run and bump session activity on an open connection."""
    conn.execute(
        """INSERT INTO task_runs (
            id, task_session_id, task_message_id, profile_id, profile_id_snapshot,
            sandbox_id, harness, status, launch_if_stopped, allowed_origins_json,
            max_steps, timeout_seconds, model_alias, deadline_at,
            health_snapshot_json, health_decision_json, health_override_json,
            retry_count, first_action_sequence, first_action_at, next_output_sequence,
            claimed_by, claim_expires_at, worker_id, claim_eligible_at, cancelled_at,
            created_by_kind, created_by_id, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
            0, NULL, NULL, 0, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?
        )""",
        (
            run_id,
            task_session_id,
            task_message_id,
            profile_id,
            profile_id,
            sandbox_id,
            harness,
            status,
            bool(launch_if_stopped),
            json.dumps(list(allowed_origins), separators=(",", ":")),
            int(max_steps),
            int(timeout_seconds),
            model_alias,
            deadline_at,
            json.dumps(health_snapshot, separators=(",", ":"), sort_keys=True),
            json.dumps(health_decision, separators=(",", ":"), sort_keys=True),
            created_by_kind,
            created_by_id,
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE task_sessions SET updated_at = ?, activity_at = ? WHERE id = ?",
        (now, now, task_session_id),
    )


def create_task_run(
    *,
    task_session_id: str,
    task_message_id: str,
    profile_id: str,
    sandbox_id: str,
    harness: str,
    launch_if_stopped: bool,
    allowed_origins: list[str],
    max_steps: int,
    timeout_seconds: int,
    model_alias: str | None,
    health_snapshot: dict[str, Any],
    health_decision: dict[str, Any],
    created_by_kind: str,
    created_by_id: str | None = None,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    now = _now()
    created_at = datetime.datetime.fromisoformat(now)
    deadline_at = (
        created_at + datetime.timedelta(seconds=int(timeout_seconds))
    ).isoformat()
    status = _status_from_health_decision(health_decision)
    with get_db() as conn:
        _insert_task_run_on_conn(
            conn,
            run_id=run_id,
            task_session_id=task_session_id,
            task_message_id=task_message_id,
            profile_id=profile_id,
            sandbox_id=sandbox_id,
            harness=harness,
            status=status,
            launch_if_stopped=launch_if_stopped,
            allowed_origins=allowed_origins,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            model_alias=model_alias,
            deadline_at=deadline_at,
            health_snapshot=health_snapshot,
            health_decision=health_decision,
            created_by_kind=created_by_kind,
            created_by_id=created_by_id,
            now=now,
        )
        conn.commit()
    run = get_task_run(run_id)
    if run is None:  # pragma: no cover
        raise RuntimeError("task run insert did not create a row")
    return run


def create_task_run_with_message(
    *,
    task_session_id: str,
    content: str,
    profile_id: str,
    sandbox_id: str,
    harness: str,
    launch_if_stopped: bool,
    allowed_origins: list[str],
    max_steps: int,
    timeout_seconds: int,
    model_alias: str | None,
    health_snapshot: dict[str, Any],
    health_decision: dict[str, Any],
    created_by_kind: str,
    created_by_id: str | None = None,
    message_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert the user task message and task_run in one BEGIN IMMEDIATE transaction."""
    run_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    now = _now()
    created_at = datetime.datetime.fromisoformat(now)
    deadline_at = (
        created_at + datetime.timedelta(seconds=int(timeout_seconds))
    ).isoformat()
    status = _status_from_health_decision(health_decision)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT status, archived_at FROM task_sessions WHERE id = ?",
                (task_session_id,),
            ).fetchone()
            if row is not None and (
                row["status"] == "archived" or row["archived_at"] is not None
            ):
                conn.commit()
                raise TaskArchivedError(f"Task session is archived: {task_session_id}")
            conn.execute(
                """INSERT INTO task_messages
                (id, session_id, role, content, created_by_kind, created_by_id, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    task_session_id,
                    "user",
                    content,
                    created_by_kind,
                    created_by_id,
                    now,
                    json.dumps(message_metadata or {}, separators=(",", ":")),
                ),
            )
            _insert_task_run_on_conn(
                conn,
                run_id=run_id,
                task_session_id=task_session_id,
                task_message_id=message_id,
                profile_id=profile_id,
                sandbox_id=sandbox_id,
                harness=harness,
                status=status,
                launch_if_stopped=launch_if_stopped,
                allowed_origins=allowed_origins,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
                model_alias=model_alias,
                deadline_at=deadline_at,
                health_snapshot=health_snapshot,
                health_decision=health_decision,
                created_by_kind=created_by_kind,
                created_by_id=created_by_id,
                now=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    run = get_task_run(run_id)
    if run is None:  # pragma: no cover
        raise RuntimeError("task run insert did not create a row")
    return run


def get_task_run(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        return _task_run_from_row(row) if row else None


def cancel_task_run(run_id: str) -> dict[str, Any] | None:
    now = _now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            conn.commit()
            return None
        current = _task_run_from_row(row)
        if current["status"] in TASK_RUN_TERMINAL_STATUSES:
            conn.commit()
            return current
        if current["status"] not in TASK_RUN_CANCELABLE_STATUSES:
            conn.commit()
            return current
        conn.execute(
            """UPDATE task_runs
            SET status = 'cancelled', cancelled_at = ?, updated_at = ?
            WHERE id = ? AND status IN ('queued', 'health_check', 'blocked_health', 'running')""",
            (now, now, run_id),
        )
        conn.commit()
    return get_task_run(run_id)


def retry_task_run_health(run_id: str) -> dict[str, Any] | None:
    run = get_task_run(run_id)
    if run is None:
        return None
    if run["status"] in TASK_RUN_TERMINAL_STATUSES:
        return run
    profile_id = run.get("profile_id") or run.get("profile_id_snapshot")
    if not profile_id:
        return run
    # Health measurement may run outside the DB lock; re-check under BEGIN IMMEDIATE.
    snapshot, decision = build_run_health_gate(str(profile_id))
    status = _status_from_health_decision(decision)
    now = _now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            conn.commit()
            return None
        current = _task_run_from_row(row)
        if current["status"] in TASK_RUN_TERMINAL_STATUSES:
            conn.commit()
            return current
        if current["status"] not in TASK_RUN_CANCELABLE_STATUSES:
            conn.commit()
            return current
        conn.execute(
            """UPDATE task_runs
            SET health_snapshot_json = ?,
                health_decision_json = ?,
                status = ?,
                retry_count = retry_count + 1,
                updated_at = ?
            WHERE id = ?
              AND status IN ('queued', 'health_check', 'blocked_health', 'running')
              AND cancelled_at IS NULL""",
            (
                json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
                json.dumps(decision, separators=(",", ":"), sort_keys=True),
                status,
                now,
                run_id,
            ),
        )
        conn.commit()
    return get_task_run(run_id)


def override_task_run_health(
    run_id: str,
    *,
    reason: str,
    actor_kind: str,
    actor_id: str | None,
) -> dict[str, Any] | None:
    if __package__:
        from .run_health import NON_OVERRIDABLE_REASON_CODES
    else:  # pragma: no cover
        from run_health import NON_OVERRIDABLE_REASON_CODES

    now = _now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            conn.commit()
            return None
        current = _task_run_from_row(row)
        if current["status"] in TASK_RUN_TERMINAL_STATUSES:
            conn.commit()
            return current
        decision = dict(current.get("health_decision") or {})
        failed_reasons = [
            item for item in decision.get("failed_reasons", []) if isinstance(item, str)
        ]
        non_overridable = [
            item for item in failed_reasons if item in NON_OVERRIDABLE_REASON_CODES
        ]
        if non_overridable:
            override = {
                "applied": False,
                "reason": reason,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "applied_at": now,
                "failed_reasons": failed_reasons,
                "non_overridable_reasons": non_overridable,
                "policy_version": decision.get("policy_version"),
            }
            conn.execute(
                """UPDATE task_runs
                SET health_override_json = ?, updated_at = ?
                WHERE id = ?
                  AND status NOT IN ('succeeded', 'failed', 'cancelled', 'revoked')""",
                (
                    json.dumps(override, separators=(",", ":"), sort_keys=True),
                    now,
                    run_id,
                ),
            )
            conn.commit()
            return get_task_run(run_id)

        override = {
            "applied": True,
            "reason": reason,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "applied_at": now,
            "failed_reasons": failed_reasons,
            "non_overridable_reasons": [],
            "policy_version": decision.get("policy_version"),
        }
        updated_decision = {
            **decision,
            "allowed": True,
            "waiting": False,
            "failed_reasons": [],
            "non_overridable_reasons": [],
        }
        conn.execute(
            """UPDATE task_runs
            SET health_override_json = ?,
                health_decision_json = ?,
                status = 'queued',
                updated_at = ?
            WHERE id = ?
              AND status NOT IN ('succeeded', 'failed', 'cancelled', 'revoked')
              AND cancelled_at IS NULL""",
            (
                json.dumps(override, separators=(",", ":"), sort_keys=True),
                json.dumps(updated_decision, separators=(",", ":"), sort_keys=True),
                now,
                run_id,
            ),
        )
        conn.commit()
    return get_task_run(run_id)


def append_task_output(
    run_id: str,
    *,
    idempotency_key: str,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Atomically append a typed output with idempotent retries."""
    now = _now()
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run_row = conn.execute(
            "SELECT * FROM task_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            conn.commit()
            raise KeyError(run_id)

        existing = conn.execute(
            """SELECT * FROM task_outputs
            WHERE run_id = ? AND idempotency_key = ?""",
            (run_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            current = _task_output_from_row(existing)
            same = (
                current["kind"] == kind
                and current["summary"] == summary
                and json.dumps(current["payload"], separators=(",", ":"), sort_keys=True)
                == payload_json
            )
            conn.commit()
            if same:
                return current
            raise TaskOutputConflictError(
                f"Conflicting output for idempotency key {idempotency_key}"
            )

        next_sequence = int(run_row["next_output_sequence"]) + 1
        output_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO task_outputs (
                id, run_id, sequence, idempotency_key, kind, summary, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                output_id,
                run_id,
                next_sequence,
                idempotency_key,
                kind,
                summary,
                payload_json,
                now,
            ),
        )
        if kind == "action" and run_row["first_action_sequence"] is None:
            conn.execute(
                """UPDATE task_runs
                SET next_output_sequence = ?,
                    first_action_sequence = ?,
                    first_action_at = ?,
                    updated_at = ?
                WHERE id = ?""",
                (next_sequence, next_sequence, now, now, run_id),
            )
        else:
            conn.execute(
                """UPDATE task_runs
                SET next_output_sequence = ?, updated_at = ?
                WHERE id = ?""",
                (next_sequence, now, run_id),
            )
        conn.execute(
            "UPDATE task_sessions SET updated_at = ?, activity_at = ? WHERE id = ?",
            (now, now, run_row["task_session_id"]),
        )
        conn.commit()
    output = get_task_output(output_id)
    if output is None:  # pragma: no cover
        raise RuntimeError("task output insert did not create a row")
    return output


def get_task_output(output_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM task_outputs WHERE id = ?",
            (output_id,),
        ).fetchone()
        return _task_output_from_row(row) if row else None


def list_task_outputs(
    run_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_after = max(0, int(after_sequence))
    safe_limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM task_outputs
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?""",
            (run_id, safe_after, safe_limit),
        ).fetchall()
        return [_task_output_from_row(row) for row in rows]


# ── Access control persistence ───────────────────────────────────────────────


def _access_grants(conn: sqlite3.Connection, principal_type: str, principal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT sandbox_id, permission FROM access_grants
        WHERE principal_type = ? AND principal_id = ?
        ORDER BY sandbox_id, permission""",
        (principal_type, principal_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _access_group_grants(conn: sqlite3.Connection, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT sandbox_id, permission FROM access_group_grants
        WHERE group_id = ?
        ORDER BY sandbox_id, permission""",
        (group_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _access_group_member_ids(conn: sqlite3.Connection, group_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT user_id FROM access_group_members
        WHERE group_id = ?
        ORDER BY user_id""",
        (group_id,),
    ).fetchall()
    return [str(row["user_id"]) for row in rows]


def _access_user_group_ids(conn: sqlite3.Connection, user_id: str, *, active_only: bool = False) -> list[str]:
    where = "m.user_id = ?"
    if active_only:
        where += " AND g.active = 1"
    rows = conn.execute(
        f"""SELECT g.id FROM access_groups g
        JOIN access_group_members m ON m.group_id = g.id
        WHERE {where}
        ORDER BY g.name COLLATE NOCASE""",
        (user_id,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _dedupe_grants(grants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for grant in grants:
        key = (str(grant["sandbox_id"]), str(grant["permission"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"sandbox_id": key[0], "permission": key[1]})
    return sorted(deduped, key=lambda item: (item["sandbox_id"], item["permission"]))


def _access_user_effective_grants(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    grants = _access_grants(conn, "user", user_id)
    rows = conn.execute(
        """SELECT gg.sandbox_id, gg.permission
        FROM access_group_grants gg
        JOIN access_groups g ON g.id = gg.group_id
        JOIN access_group_members m ON m.group_id = g.id
        WHERE m.user_id = ? AND g.active = 1
        ORDER BY gg.sandbox_id, gg.permission""",
        (user_id,),
    ).fetchall()
    grants.extend(dict(row) for row in rows)
    return _dedupe_grants(grants)


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


def _set_access_user_group_memberships(conn: sqlite3.Connection, user_id: str, group_ids: list[str]) -> None:
    now = _now()
    conn.execute("DELETE FROM access_group_members WHERE user_id = ?", (user_id,))
    for group_id in group_ids:
        conn.execute(
            """INSERT INTO access_group_members (group_id, user_id, created_at)
            VALUES (?, ?, ?)""",
            (group_id, user_id, now),
        )


def set_access_user_groups(user_id: str, group_ids: list[str]) -> dict[str, Any] | None:
    if not get_access_user(user_id):
        return None
    with get_db() as conn:
        _set_access_user_group_memberships(conn, user_id, group_ids)
        conn.commit()
    return get_access_user(user_id)


def create_access_user(
    username: str,
    password_hash: str,
    role: str = "viewer",
    grants: list[dict[str, Any]] | None = None,
    group_ids: list[str] | None = None,
) -> dict[str, Any]:
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
    if group_ids is not None:
        set_access_user_groups(user_id, group_ids)
    return get_access_user(user_id)  # type: ignore[return-value]


def get_access_user(user_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM access_users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        user = dict(row)
        user["grants"] = _access_grants(conn, "user", user_id)
        user["group_ids"] = _access_user_group_ids(conn, user_id)
        user["effective_grants"] = _access_user_effective_grants(conn, user_id)
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
        user["group_ids"] = _access_user_group_ids(conn, user["id"])
        user["effective_grants"] = _access_user_effective_grants(conn, user["id"])
        return user


def list_access_users() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM access_users ORDER BY username COLLATE NOCASE").fetchall()
        users: list[dict[str, Any]] = []
        for row in rows:
            user = dict(row)
            user["grants"] = _access_grants(conn, "user", user["id"])
            user["group_ids"] = _access_user_group_ids(conn, user["id"])
            user["effective_grants"] = _access_user_effective_grants(conn, user["id"])
            users.append(user)
        return users


def update_access_user(user_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_access_user(user_id)
    if not existing:
        return None
    grants = fields.pop("grants", None)
    group_ids = fields.pop("group_ids", None)
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
    if group_ids is not None:
        set_access_user_groups(user_id, group_ids)
    return get_access_user(user_id)


def _set_access_group_members(conn: sqlite3.Connection, group_id: str, member_user_ids: list[str]) -> None:
    now = _now()
    conn.execute("DELETE FROM access_group_members WHERE group_id = ?", (group_id,))
    for user_id in member_user_ids:
        conn.execute(
            """INSERT INTO access_group_members (group_id, user_id, created_at)
            VALUES (?, ?, ?)""",
            (group_id, user_id, now),
        )


def _set_access_group_grants(conn: sqlite3.Connection, group_id: str, grants: list[dict[str, Any]]) -> None:
    now = _now()
    conn.execute("DELETE FROM access_group_grants WHERE group_id = ?", (group_id,))
    for grant in grants:
        conn.execute(
            """INSERT INTO access_group_grants (group_id, sandbox_id, permission, created_at)
            VALUES (?, ?, ?, ?)""",
            (group_id, grant["sandbox_id"], grant["permission"], now),
        )


def create_access_group(
    name: str,
    description: str | None = None,
    active: bool = True,
    member_user_ids: list[str] | None = None,
    grants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    group_id = str(uuid.uuid4())
    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO access_groups
            (id, name, description, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (group_id, name, description, active, now, now),
        )
        _set_access_group_members(conn, group_id, member_user_ids or [])
        _set_access_group_grants(conn, group_id, grants or [])
        conn.commit()
    return get_access_group(group_id)  # type: ignore[return-value]


def get_access_group(group_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM access_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return None
        group = dict(row)
        group["member_user_ids"] = _access_group_member_ids(conn, group_id)
        group["grants"] = _access_group_grants(conn, group_id)
        return group


def list_access_groups() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM access_groups ORDER BY name COLLATE NOCASE").fetchall()
        groups: list[dict[str, Any]] = []
        for row in rows:
            group = dict(row)
            group["member_user_ids"] = _access_group_member_ids(conn, group["id"])
            group["grants"] = _access_group_grants(conn, group["id"])
            groups.append(group)
        return groups


def update_access_group(group_id: str, **fields: Any) -> dict[str, Any] | None:
    existing = get_access_group(group_id)
    if not existing:
        return None
    member_user_ids = fields.pop("member_user_ids", None)
    grants = fields.pop("grants", None)
    update_cols: list[str] = []
    values: list[Any] = []
    for col in ("name", "description", "active"):
        if col in fields:
            update_cols.append(f"{col} = ?")
            values.append(fields[col])
    with get_db() as conn:
        if update_cols:
            update_cols.append("updated_at = ?")
            values.append(_now())
            values.append(group_id)
            conn.execute(f"UPDATE access_groups SET {', '.join(update_cols)} WHERE id = ?", values)
        if member_user_ids is not None:
            _set_access_group_members(conn, group_id, member_user_ids)
        if grants is not None:
            _set_access_group_grants(conn, group_id, grants)
        conn.commit()
    return get_access_group(group_id)


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


def delete_access_agent(agent_id: str) -> bool:
    """Permanently revoke an agent key and remove its sandbox grants."""
    existing = get_access_agent(agent_id)
    if not existing:
        return False
    with get_db() as conn:
        conn.execute(
            "DELETE FROM access_grants WHERE principal_type = ? AND principal_id = ?",
            ("agent", agent_id),
        )
        conn.execute("DELETE FROM access_agents WHERE id = ?", (agent_id,))
        conn.commit()
    return True


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


def _proxy_inventory_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("warnings_json", "blockers_json"):
        raw = data.pop(key, "[]")
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            parsed = []
        data[key.replace("_json", "")] = parsed if isinstance(parsed, list) else []
    data["has_credentials"] = bool(data.get("has_credentials"))
    data["active"] = bool(data.get("active"))
    # Never return the secret URL from row helpers used by API list paths.
    data.pop("proxy_url", None)
    return data


def upsert_proxy_inventory_entry(proxy_url: str, *, redacted: dict[str, Any]) -> dict[str, Any]:
    """Insert or reactivate a proxy inventory row. Secrets stay server-side only."""
    from backend.proxy_inventory import proxy_fingerprint

    fingerprint = proxy_fingerprint(proxy_url)
    now = _now()
    entry_id = str(uuid.uuid4())
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM proxy_inventory WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE proxy_inventory SET
                    proxy_url = ?, host_masked = ?, port = ?, username_masked = ?,
                    has_credentials = ?, label = ?, active = 1, updated_at = ?
                WHERE fingerprint = ?""",
                (
                    proxy_url,
                    redacted.get("host_masked") or "unknown",
                    redacted.get("port"),
                    redacted.get("username_masked"),
                    1 if redacted.get("has_credentials") else 0,
                    redacted.get("label") or "proxy",
                    now,
                    fingerprint,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM proxy_inventory WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            return _proxy_inventory_row(row) or {}

        conn.execute(
            """INSERT INTO proxy_inventory (
                id, fingerprint, proxy_url, host_masked, port, username_masked,
                has_credentials, label, active, check_state, warnings_json, blockers_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'missing', '[]', '[]', ?, ?)""",
            (
                entry_id,
                fingerprint,
                proxy_url,
                redacted.get("host_masked") or "unknown",
                redacted.get("port"),
                redacted.get("username_masked"),
                1 if redacted.get("has_credentials") else 0,
                redacted.get("label") or "proxy",
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM proxy_inventory WHERE id = ?", (entry_id,)).fetchone()
        return _proxy_inventory_row(row) or {}


def list_proxy_inventory(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    with get_db() as conn:
        if include_inactive:
            rows = conn.execute(
                "SELECT * FROM proxy_inventory ORDER BY updated_at DESC, label ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM proxy_inventory
                   WHERE active = 1
                   ORDER BY updated_at DESC, label ASC"""
            ).fetchall()
    return [item for item in (_proxy_inventory_row(row) for row in rows) if item]


def get_proxy_inventory_entry(entry_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM proxy_inventory WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    if include_secret:
        data = dict(row)
        for key in ("warnings_json", "blockers_json"):
            raw = data.pop(key, "[]")
            try:
                parsed = json.loads(raw or "[]")
            except (TypeError, json.JSONDecodeError):
                parsed = []
            data[key.replace("_json", "")] = parsed if isinstance(parsed, list) else []
        data["has_credentials"] = bool(data.get("has_credentials"))
        data["active"] = bool(data.get("active"))
        return data
    return _proxy_inventory_row(row)


def update_proxy_inventory_check(entry_id: str, summary: dict[str, Any]) -> dict[str, Any] | None:
    now = _now()
    with get_db() as conn:
        conn.execute(
            """UPDATE proxy_inventory SET
                check_state = ?, reachable = ?, latency_ms = ?, risk_score = ?,
                authenticity_score = ?, country_code = ?, timezone_hint = ?, locale_hint = ?,
                warnings_json = ?, blockers_json = ?, last_checked_at = ?, updated_at = ?
            WHERE id = ?""",
            (
                summary.get("check_state") or "unavailable",
                summary.get("reachable"),
                summary.get("latency_ms"),
                summary.get("risk_score"),
                summary.get("authenticity_score"),
                summary.get("country_code"),
                summary.get("timezone_hint"),
                summary.get("locale_hint"),
                json.dumps(summary.get("warnings") or []),
                json.dumps(summary.get("blockers") or []),
                now,
                now,
                entry_id,
            ),
        )
        conn.commit()
    return get_proxy_inventory_entry(entry_id)
