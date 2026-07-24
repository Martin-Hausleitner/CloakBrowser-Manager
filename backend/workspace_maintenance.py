"""Clock-injected temporary-task retention and artifact cleanup."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

if __package__:
    from . import database as db
else:
    import database as db

logger = logging.getLogger(__name__)

INACTIVITY_ARCHIVE_AFTER = timedelta(days=7)
PURGE_AFTER_ARCHIVE = timedelta(days=30)
MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class WorkspaceMaintenance:
    """Idempotent retention cleanup with an injected UTC clock."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        artifact_store: Any,
        get_db: Callable = db.get_db,
        audit: Callable[..., None] | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._artifact_store = artifact_store
        self._get_db = get_db
        self._audit = audit or self._default_audit

    @staticmethod
    def _default_audit(action: str, **fields: Any) -> None:
        # IDs and counts only — never paths, bodies, or secrets.
        safe = {key: value for key, value in fields.items() if "path" not in key.lower()}
        logger.info("workspace_maintenance action=%s %s", action, safe)

    def cleanup_retention_once(self) -> dict[str, int]:
        """Run one idempotent retention pass."""
        self._artifact_store.ensure_schema()
        now = self._clock()
        archived = self._archive_inactive_temporary_tasks(now)
        expired = self._artifact_store.expire_due_once()
        purged = self._purge_archived_temporary_tasks(now)
        result = {
            "archived_tasks": archived,
            "artifacts_deleted": int(expired.get("deleted") or 0),
            "artifact_delete_failures": int(expired.get("delete_failures") or 0),
            "purged_tasks": purged,
        }
        self._audit(
            "cleanup_retention_once",
            archived_tasks=result["archived_tasks"],
            artifacts_deleted=result["artifacts_deleted"],
            artifact_delete_failures=result["artifact_delete_failures"],
            purged_tasks=result["purged_tasks"],
        )
        return result

    def _archive_inactive_temporary_tasks(self, now: datetime) -> int:
        archived_count = 0
        expires_at = _iso(now + timedelta(days=7))
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT id, activity_at FROM task_sessions
                    WHERE retention_class = 'temporary'
                      AND archived_at IS NULL
                      AND status != 'archived'
                    """
                ).fetchall()
                for row in rows:
                    activity = _parse_dt(row["activity_at"])
                    if activity is None:
                        continue
                    # Strictly more than 7 days of inactivity.
                    if now <= activity + INACTIVITY_ARCHIVE_AFTER:
                        continue
                    task_id = str(row["id"])
                    cursor = conn.execute(
                        """
                        UPDATE task_sessions
                        SET archived_at = ?, status = 'archived', updated_at = ?
                        WHERE id = ? AND archived_at IS NULL
                        """,
                        (_iso(now), _iso(now), task_id),
                    )
                    if cursor.rowcount != 1:
                        continue
                    archived_count += 1
                    # Schedule screenshot expiry in the same transaction.
                    conn.execute(
                        """
                        UPDATE task_artifacts
                        SET expires_at = ?
                        WHERE task_session_id = ?
                          AND deleted_at IS NULL
                        """,
                        (expires_at, task_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if archived_count:
            self._audit("archive_temporary_tasks", count=archived_count)
        return archived_count

    def _purge_archived_temporary_tasks(self, now: datetime) -> int:
        purged = 0
        with self._get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, archived_at FROM task_sessions
                WHERE retention_class = 'temporary'
                  AND archived_at IS NOT NULL
                """
            ).fetchall()
        for row in rows:
            archived_at = _parse_dt(row["archived_at"])
            if archived_at is None:
                continue
            if archived_at + PURGE_AFTER_ARCHIVE >= now:
                continue
            task_id = str(row["id"])
            if self._artifact_store.task_has_pending_bytes(task_id):
                # Attempt one more expiry/deletion pass for this task's due artifacts.
                self._artifact_store.expire_due_once()
                if self._artifact_store.task_has_pending_bytes(task_id):
                    self._audit("purge_blocked_pending_artifacts", task_id=task_id)
                    continue
            if self._purge_task(task_id):
                purged += 1
        if purged:
            self._audit("purge_temporary_tasks", count=purged)
        return purged

    def _purge_task(self, task_id: str) -> bool:
        """Purge a task after screenshot bytes are absent. Never touches profiles."""
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                pending = conn.execute(
                    """
                    SELECT 1 FROM task_artifacts
                    WHERE task_session_id = ? AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if pending is not None:
                    conn.commit()
                    return False
                # Drop artifact metadata first so output FK RESTRICT cannot block.
                conn.execute(
                    "DELETE FROM task_artifacts WHERE task_session_id = ?",
                    (task_id,),
                )
                cursor = conn.execute(
                    "DELETE FROM task_sessions WHERE id = ?",
                    (task_id,),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                self._audit("purged_task", task_id=task_id)
                return True
            except Exception:
                conn.rollback()
                raise


async def run_daily_maintenance_loop(
    maintenance: WorkspaceMaintenance,
    *,
    interval_seconds: float = MAINTENANCE_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Best-effort daily loop; recoverable errors do not stop the process."""
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            await asyncio.to_thread(maintenance.cleanup_retention_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workspace_maintenance_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
