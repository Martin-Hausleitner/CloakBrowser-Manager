"""Persisted exclusive direct automation leases for profile CDP access."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

if __package__:
    from . import database as db
else:
    import database as db

HEARTBEAT_INTERVAL_SECONDS = 15
LEASE_TTL_SECONDS = 45
TOKEN_PREFIX = "cbm_lease_"
TOKEN_BYTES = 32


class AutomationBusy(Exception):
    """Raised when a profile already has an unreleased unexpired lease."""


class AutomationLeaseInvalid(Exception):
    """Raised when lease credentials do not bind to an active lease."""


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


def _normalize_owner_id(owner_id: str | None) -> str:
    return "" if owner_id is None else str(owner_id)


def _digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mint_token() -> tuple[str, str]:
    raw = secrets.token_bytes(TOKEN_BYTES)
    token = f"{TOKEN_PREFIX}{raw.hex()}"
    return token, _digest_token(token)


@dataclass(frozen=True)
class AcquiredLease:
    lease_id: str
    token: str
    profile_id: str
    owner_kind: str
    owner_id: str
    created_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    profile_id: str
    owner_kind: str
    owner_id: str
    created_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None


class AutomationLeaseService:
    """SQLite-backed exclusive direct automation leases."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        get_db: Callable = db.get_db,
    ) -> None:
        self._clock = clock or _utc_now
        self._get_db = get_db

    def ensure_schema(self) -> None:
        """Idempotently create the automation_leases table and indexes."""
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_leases (
                        id TEXT PRIMARY KEY,
                        profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
                        owner_kind TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        token_digest TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        released_at TEXT,
                        release_reason TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    ux_automation_leases_active_profile
                    ON automation_leases(profile_id)
                    WHERE released_at IS NULL AND profile_id IS NOT NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    ix_automation_leases_expires
                    ON automation_leases(expires_at)
                    WHERE released_at IS NULL
                    """
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def acquire_direct(
        self,
        profile_id: str,
        *,
        owner_kind: str,
        owner_id: str | None,
    ) -> AcquiredLease:
        now = self._clock()
        expires = now + timedelta(seconds=LEASE_TTL_SECONDS)
        owner = _normalize_owner_id(owner_id)
        lease_id = str(uuid.uuid4())
        token, digest = _mint_token()

        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._retire_expired_locked(conn, now)
                active = conn.execute(
                    """
                    SELECT id FROM automation_leases
                    WHERE profile_id = ? AND released_at IS NULL
                    LIMIT 1
                    """,
                    (profile_id,),
                ).fetchone()
                if active is not None:
                    conn.commit()
                    raise AutomationBusy("automation_busy")

                try:
                    conn.execute(
                        """
                        INSERT INTO automation_leases (
                            id, profile_id, owner_kind, owner_id, token_digest,
                            created_at, heartbeat_at, expires_at, released_at, release_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            lease_id,
                            profile_id,
                            owner_kind,
                            owner,
                            digest,
                            _iso(now),
                            _iso(now),
                            _iso(expires),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise AutomationBusy("automation_busy") from exc
                conn.commit()
            except AutomationBusy:
                raise
            except Exception:
                conn.rollback()
                raise

        return AcquiredLease(
            lease_id=lease_id,
            token=token,
            profile_id=profile_id,
            owner_kind=owner_kind,
            owner_id=owner,
            created_at=now,
            heartbeat_at=now,
            expires_at=expires,
        )

    def validate(
        self,
        lease_id: str,
        token: str,
        profile_id: str,
        *,
        owner_kind: str,
        owner_id: str | None,
    ) -> LeaseRecord | None:
        now = self._clock()
        owner = _normalize_owner_id(owner_id)
        digest = _digest_token(token)
        with self._get_db() as conn:
            row = conn.execute(
                """
                SELECT * FROM automation_leases
                WHERE id = ? AND profile_id = ?
                  AND owner_kind = ? AND owner_id = ?
                  AND released_at IS NULL
                """,
                (lease_id, profile_id, owner_kind, owner),
            ).fetchone()
        return self._row_if_valid(row, digest, now)

    def validate_for_actor(
        self,
        token: str,
        profile_id: str,
        *,
        owner_kind: str,
        owner_id: str | None,
    ) -> LeaseRecord | None:
        """Bind token to the caller's active lease on a profile (CDP routes)."""
        now = self._clock()
        owner = _normalize_owner_id(owner_id)
        digest = _digest_token(token)
        with self._get_db() as conn:
            row = conn.execute(
                """
                SELECT * FROM automation_leases
                WHERE profile_id = ?
                  AND owner_kind = ? AND owner_id = ?
                  AND released_at IS NULL
                """,
                (profile_id, owner_kind, owner),
            ).fetchone()
        return self._row_if_valid(row, digest, now)

    def _row_if_valid(
        self,
        row: sqlite3.Row | None,
        digest: str,
        now: datetime,
    ) -> LeaseRecord | None:
        if row is None:
            return None
        if not hmac.compare_digest(str(row["token_digest"]), digest):
            return None
        expires_at = _parse_dt(row["expires_at"])
        if expires_at is None or expires_at <= now:
            return None
        return LeaseRecord(
            lease_id=str(row["id"]),
            profile_id=str(row["profile_id"]),
            owner_kind=str(row["owner_kind"]),
            owner_id=str(row["owner_id"]),
            created_at=_parse_dt(row["created_at"]) or now,
            heartbeat_at=_parse_dt(row["heartbeat_at"]) or now,
            expires_at=expires_at,
            released_at=_parse_dt(row["released_at"]),
            release_reason=row["release_reason"],
        )

    def heartbeat(
        self,
        lease_id: str,
        token: str,
        profile_id: str,
        *,
        owner_kind: str,
        owner_id: str | None,
    ) -> datetime:
        record = self.validate(
            lease_id,
            token,
            profile_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
        )
        if record is None:
            raise AutomationLeaseInvalid("invalid_lease")
        now = self._clock()
        expires = now + timedelta(seconds=LEASE_TTL_SECONDS)
        digest = _digest_token(token)
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                updated = conn.execute(
                    """
                    UPDATE automation_leases
                    SET heartbeat_at = ?, expires_at = ?
                    WHERE id = ? AND profile_id = ?
                      AND owner_kind = ? AND owner_id = ?
                      AND token_digest = ? AND released_at IS NULL
                    """,
                    (
                        _iso(now),
                        _iso(expires),
                        lease_id,
                        profile_id,
                        owner_kind,
                        _normalize_owner_id(owner_id),
                        digest,
                    ),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    raise AutomationLeaseInvalid("invalid_lease")
                conn.commit()
            except AutomationLeaseInvalid:
                raise
            except Exception:
                conn.rollback()
                raise
        return expires

    def release(
        self,
        lease_id: str,
        token: str,
        profile_id: str,
        *,
        owner_kind: str,
        owner_id: str | None,
        reason: str = "released",
    ) -> bool:
        """Idempotently release for the owning actor/token. Wrong actor fails."""
        owner = _normalize_owner_id(owner_id)
        digest = _digest_token(token)
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT token_digest, released_at, owner_kind, owner_id, profile_id
                    FROM automation_leases
                    WHERE id = ?
                    """,
                    (lease_id,),
                ).fetchone()
                if row is None:
                    conn.commit()
                    raise AutomationLeaseInvalid("invalid_lease")
                if str(row["profile_id"] or "") != profile_id:
                    conn.commit()
                    raise AutomationLeaseInvalid("invalid_lease")
                if str(row["owner_kind"]) != owner_kind or str(row["owner_id"]) != owner:
                    conn.commit()
                    raise AutomationLeaseInvalid("invalid_lease")
                if not hmac.compare_digest(str(row["token_digest"]), digest):
                    conn.commit()
                    raise AutomationLeaseInvalid("invalid_lease")
                if row["released_at"] is not None:
                    conn.commit()
                    return True
                conn.execute(
                    """
                    UPDATE automation_leases
                    SET released_at = ?, release_reason = ?
                    WHERE id = ? AND released_at IS NULL
                    """,
                    (_iso(now), reason, lease_id),
                )
                conn.commit()
                return True
            except AutomationLeaseInvalid:
                raise
            except Exception:
                conn.rollback()
                raise

    def expire_stale(self) -> list[tuple[str, str]]:
        """Mark expired unreleased leases released; return (lease_id, profile_id)."""
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                expired = self._retire_expired_locked(conn, now)
                conn.commit()
                return expired
            except Exception:
                conn.rollback()
                raise

    def _retire_expired_locked(
        self, conn: sqlite3.Connection, now: datetime
    ) -> list[tuple[str, str]]:
        rows = conn.execute(
            """
            SELECT id, profile_id FROM automation_leases
            WHERE released_at IS NULL AND expires_at <= ?
            """,
            (_iso(now),),
        ).fetchall()
        result: list[tuple[str, str]] = []
        for row in rows:
            lease_id = str(row["id"])
            profile_id = str(row["profile_id"] or "")
            conn.execute(
                """
                UPDATE automation_leases
                SET released_at = ?, release_reason = ?
                WHERE id = ? AND released_at IS NULL
                """,
                (_iso(now), "expired", lease_id),
            )
            result.append((lease_id, profile_id))
        return result
