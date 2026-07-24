"""Scoped Browser-Use worker runtime: identity sync, claim, capability, maintenance."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

if __package__:
    from . import access_control as access
    from . import automation_leases
    from . import database as db
else:  # pragma: no cover
    import access_control as access
    import automation_leases
    import database as db

logger = logging.getLogger(__name__)

CLAIM_TTL_SECONDS = 45
HEARTBEAT_INTERVAL_SECONDS = 15
DEFAULT_ELIGIBILITY_TIMEOUT_SECONDS = 60
MIN_ELIGIBILITY_TIMEOUT_SECONDS = 30
MAX_ELIGIBILITY_TIMEOUT_SECONDS = 300
WORKER_MAINTENANCE_INTERVAL_SECONDS = 5

ALLOWLISTED_FAIL_CODES = frozenset(
    {
        "worker_lost",
        "worker_unavailable",
        "navigation_blocked",
        "model_timeout",
        "model_rate_limit",
        "max_steps",
        "health_blocked",
        "capability_revoked",
        "internal_error",
    }
)


class WorkerNotFound(LookupError):
    """Bound worker/claim mismatch — callers map to indistinguishable 404."""


class CapabilityConflict(Exception):
    """Capability already issued for this claim."""


class CapabilityNotReady(Exception):
    """Health gate not yet allowing CDP issuance."""

    def __init__(
        self,
        *,
        waiting: bool,
        blocked: bool,
        decision: dict[str, Any],
        lease_ids: tuple[str, ...] = (),
    ):
        super().__init__("capability_not_ready")
        self.waiting = waiting
        self.blocked = blocked
        self.decision = decision
        self.lease_ids = lease_ids


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


def _clamp_eligibility_timeout(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_ELIGIBILITY_TIMEOUT_SECONDS
    return max(
        MIN_ELIGIBILITY_TIMEOUT_SECONDS,
        min(MAX_ELIGIBILITY_TIMEOUT_SECONDS, value),
    )


def eligibility_timeout_seconds() -> int:
    return _clamp_eligibility_timeout(
        os.environ.get("CBM_CLAIM_ELIGIBILITY_TIMEOUT_SECONDS")
    )


@dataclass(frozen=True)
class TerminalCleanup:
    """Lease IDs that must have sockets closed after the DB transaction commits."""

    lease_ids: tuple[str, ...] = ()


class WorkerRuntimeService:
    """Manager-side worker claim, capability, and recovery runtime."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        get_db: Callable = db.get_db,
        lease_service: automation_leases.AutomationLeaseService | None = None,
        worker_id_env: Callable[[], str | None] | None = None,
        worker_token_env: Callable[[], str | None] | None = None,
        eligibility_timeout: Callable[[], int] | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._get_db = get_db
        if lease_service is None:
            self._leases = automation_leases.AutomationLeaseService(
                clock=lambda: self._clock(), get_db=get_db
            )
        else:
            self._leases = lease_service
            # Keep lease validation/expiry on the same injected Manager clock.
            self._leases._clock = lambda: self._clock()
        self._worker_id_env = worker_id_env or (
            lambda: os.environ.get("CBM_WORKER_ID") or None
        )
        self._worker_token_env = worker_token_env or (
            lambda: os.environ.get("CBM_WORKER_TOKEN") or None
        )
        self._eligibility_timeout = eligibility_timeout or eligibility_timeout_seconds

    # ── Bootstrap / rotation ─────────────────────────────────────────────────

    def sync_configured_worker(
        self,
        *,
        worker_id: str | None = None,
        worker_token: str | None = None,
    ) -> dict[str, Any]:
        """Upsert configured worker digest; rotate revokes active claims/leases.

        ``CBM_WORKER_TOKEN`` is accepted only as provisioning input when it is a
        valid strong ``cbm_worker_`` key. Plaintext is never persisted.

        Uses one ``BEGIN IMMEDIATE`` transaction to make the configured worker
        the sole active internal identity: upsert current ID/digest, deactivate
        every other active worker, and revoke their active claims/capabilities/
        run leases (including same-ID digest changes). Returns affected lease
        IDs for socket closure after commit — never awaits sockets in-txn.
        """
        wid = (worker_id if worker_id is not None else self._worker_id_env()) or None
        token = worker_token if worker_token is not None else self._worker_token_env()
        if not wid or not access.is_valid_worker_key(token):
            return {"configured": False, "rotated": False, "worker_id": wid}
        assert token is not None
        digest = access.hash_worker_key(token)
        now = self._clock()
        lease_ids: list[str] = []
        rotated = False
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT id, key_digest, active FROM worker_identities WHERE id = ?",
                    (wid,),
                ).fetchone()
                digest_changed = bool(
                    existing
                    and existing["key_digest"]
                    and existing["key_digest"] != digest
                )
                # Workers that will lose active status (other IDs, or same-ID digest change).
                digest_holder_rows = conn.execute(
                    """
                    SELECT id FROM worker_identities
                    WHERE key_digest = ? AND id != ?
                    """,
                    (digest, wid),
                ).fetchall()
                digest_holder_ids = [str(row["id"]) for row in digest_holder_rows]
                stale_rows = conn.execute(
                    """
                    SELECT id FROM worker_identities
                    WHERE active = 1 AND id != ?
                    """,
                    (wid,),
                ).fetchall()
                stale_ids = [str(row["id"]) for row in stale_rows]
                if digest_changed:
                    rotated = True
                if stale_ids:
                    rotated = True
                if digest_holder_ids:
                    rotated = True

                # key_digest is globally unique. If the configured token is
                # intentionally reused with a new worker ID, retire the digest
                # from the previous identity before upserting the configured
                # worker. The retired digest is deterministic, non-secret, and
                # keeps the historical row without preserving a bearer-valid
                # digest on the old identity.
                for digest_holder_id in digest_holder_ids:
                    retired_digest = access.hash_worker_key(
                        f"retired-worker-digest:{digest_holder_id}:{digest}"
                    )
                    conn.execute(
                        """
                        UPDATE worker_identities
                        SET key_digest = ?, active = 0, updated_at = ?
                        WHERE id = ?
                        """,
                        (retired_digest, _iso(now), digest_holder_id),
                    )

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO worker_identities
                            (id, key_digest, active, created_at, updated_at)
                        VALUES (?, ?, 1, ?, ?)
                        """,
                        (wid, digest, _iso(now), _iso(now)),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE worker_identities
                        SET key_digest = ?, active = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (digest, _iso(now), wid),
                    )

                if stale_ids:
                    conn.execute(
                        f"""
                        UPDATE worker_identities
                        SET active = 0, updated_at = ?
                        WHERE id IN ({",".join("?" for _ in stale_ids)})
                        """,
                        (_iso(now), *stale_ids),
                    )

                revoke_ids = list(stale_ids)
                if digest_changed:
                    revoke_ids.append(wid)
                for revoke_wid in revoke_ids:
                    lease_ids.extend(
                        self._revoke_worker_claims_on_conn(
                            conn, revoke_wid, now=now, reason="credential_rotation"
                        )
                    )
                # De-dupe while preserving order.
                seen: set[str] = set()
                unique_leases: list[str] = []
                for lid in lease_ids:
                    if lid not in seen:
                        seen.add(lid)
                        unique_leases.append(lid)
                lease_ids = unique_leases
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "configured": True,
            "rotated": rotated,
            "worker_id": wid,
            "lease_ids": lease_ids,
        }

    def _revoke_worker_claims_on_conn(
        self,
        conn: sqlite3.Connection,
        worker_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> list[str]:
        """Revoke active claims/capabilities/run leases for one worker on ``conn``.

        Caller must hold ``BEGIN IMMEDIATE``. Does not commit/rollback.
        """
        lease_ids: list[str] = []
        rows = conn.execute(
            """
            SELECT id, lease_id, profile_id FROM task_runs
            WHERE worker_id = ?
              AND status IN ('health_check', 'running')
            """,
            (worker_id,),
        ).fetchall()
        for row in rows:
            lid = row["lease_id"]
            if lid:
                lease_ids.append(str(lid))
                self._leases.release_on_conn(
                    conn, str(lid), now=now, reason=reason
                )
            conn.execute(
                """
                UPDATE task_runs
                SET status = 'revoked',
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    worker_id = NULL,
                    lease_id = NULL,
                    capability_digest = NULL,
                    error_code = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (reason, "Worker credential rotated", _iso(now), row["id"]),
            )
            if row["profile_id"]:
                self._refresh_profile_eligibility_on_conn(
                    conn, str(row["profile_id"]), now
                )
        extra = conn.execute(
            """
            SELECT id FROM automation_leases
            WHERE owner_kind = ? AND owner_id = ? AND released_at IS NULL
            """,
            (automation_leases.RUN_OWNER_KIND, worker_id),
        ).fetchall()
        for row in extra:
            lid = str(row["id"])
            if lid not in lease_ids:
                lease_ids.append(lid)
            self._leases.release_on_conn(conn, lid, now=now, reason=reason)
        return lease_ids

    def _revoke_worker_claims(self, worker_id: str, *, reason: str) -> list[str]:
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lease_ids = self._revoke_worker_claims_on_conn(
                    conn, worker_id, now=now, reason=reason
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return lease_ids

    # ── Eligibility ──────────────────────────────────────────────────────────

    def refresh_claim_eligibility(self) -> None:
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._leases._retire_expired_locked(conn, now)
                profiles = conn.execute(
                    """
                    SELECT DISTINCT profile_id FROM task_runs
                    WHERE status = 'queued' AND profile_id IS NOT NULL
                    """
                ).fetchall()
                for row in profiles:
                    self._refresh_profile_eligibility_on_conn(
                        conn, str(row["profile_id"]), now
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _refresh_profile_eligibility_on_conn(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        now: datetime,
    ) -> None:
        # Clear all first.
        conn.execute(
            """
            UPDATE task_runs
            SET claim_eligible_at = NULL
            WHERE profile_id = ? AND status = 'queued'
            """,
            (profile_id,),
        )
        if automation_leases.profile_has_active_lease(conn, profile_id, now):
            return
        head = conn.execute(
            """
            SELECT id FROM task_runs
            WHERE profile_id = ? AND status = 'queued'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (profile_id,),
        ).fetchone()
        if head is None:
            return
        conn.execute(
            """
            UPDATE task_runs
            SET claim_eligible_at = COALESCE(claim_eligible_at, ?)
            WHERE id = ?
            """,
            (_iso(now), head["id"]),
        )
        # Ensure only the head has eligibility (COALESCE above preserves continuous clock).
        # Re-read and force-set if we cleared it above — we cleared all, so set fresh or keep.
        # After clear, COALESCE(NULL, now) = now. Continuous eligibility requires preserving
        # prior timestamp when still eligible. Fix: read prior before clear.
        # Handled by caller path that preserves: see claim/release helpers.

    def _refresh_profile_eligibility_preserving(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        now: datetime,
    ) -> None:
        prior = conn.execute(
            """
            SELECT id, claim_eligible_at FROM task_runs
            WHERE profile_id = ? AND status = 'queued' AND claim_eligible_at IS NOT NULL
            """,
            (profile_id,),
        ).fetchall()
        prior_map = {str(r["id"]): r["claim_eligible_at"] for r in prior}
        conn.execute(
            """
            UPDATE task_runs
            SET claim_eligible_at = NULL
            WHERE profile_id = ? AND status = 'queued'
            """,
            (profile_id,),
        )
        if automation_leases.profile_has_active_lease(conn, profile_id, now):
            return
        # Profile must still exist in same sandbox for head selection.
        head = conn.execute(
            """
            SELECT r.id
            FROM task_runs r
            JOIN profiles p ON p.id = r.profile_id
            WHERE r.profile_id = ?
              AND r.status = 'queued'
              AND p.sandbox_id = r.sandbox_id
            ORDER BY r.created_at ASC, r.id ASC
            LIMIT 1
            """,
            (profile_id,),
        ).fetchone()
        if head is None:
            return
        head_id = str(head["id"])
        eligible_at = prior_map.get(head_id) or _iso(now)
        conn.execute(
            "UPDATE task_runs SET claim_eligible_at = ? WHERE id = ?",
            (eligible_at, head_id),
        )

    # ── Claim ────────────────────────────────────────────────────────────────

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        """Atomically claim the globally oldest eligible queued run."""
        now = self._clock()
        claim_expires = now + timedelta(seconds=CLAIM_TTL_SECONDS)
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._leases._retire_expired_locked(conn, now)
                # Refresh eligibility for all queued profiles under this lock.
                profiles = conn.execute(
                    """
                    SELECT DISTINCT profile_id FROM task_runs
                    WHERE status = 'queued' AND profile_id IS NOT NULL
                    """
                ).fetchall()
                for row in profiles:
                    self._refresh_profile_eligibility_preserving(
                        conn, str(row["profile_id"]), now
                    )

                candidate = conn.execute(
                    """
                    SELECT r.*
                    FROM task_runs r
                    JOIN profiles p ON p.id = r.profile_id
                    WHERE r.status = 'queued'
                      AND r.claim_eligible_at IS NOT NULL
                      AND r.claimed_by IS NULL
                      AND r.profile_id IS NOT NULL
                      AND p.sandbox_id = r.sandbox_id
                    ORDER BY r.created_at ASC, r.id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if candidate is None:
                    conn.commit()
                    return None

                profile_id = str(candidate["profile_id"])
                if automation_leases.profile_has_active_lease(conn, profile_id, now):
                    # Losing race: clear eligibility and retry none this round.
                    conn.execute(
                        "UPDATE task_runs SET claim_eligible_at = NULL WHERE id = ?",
                        (candidate["id"],),
                    )
                    conn.commit()
                    return None

                try:
                    lease_id, _lease_exp = self._leases.acquire_run_lease_on_conn(
                        conn,
                        profile_id=profile_id,
                        worker_id=worker_id,
                        now=now,
                        expires_at=claim_expires,
                    )
                except automation_leases.AutomationBusy:
                    conn.execute(
                        "UPDATE task_runs SET claim_eligible_at = NULL WHERE id = ?",
                        (candidate["id"],),
                    )
                    conn.commit()
                    return None

                conn.execute(
                    """
                    UPDATE task_runs
                    SET status = 'health_check',
                        claimed_by = ?,
                        worker_id = ?,
                        claim_expires_at = ?,
                        lease_id = ?,
                        claim_eligible_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND status = 'queued' AND claimed_by IS NULL
                    """,
                    (
                        worker_id,
                        worker_id,
                        _iso(claim_expires),
                        lease_id,
                        _iso(now),
                        candidate["id"],
                    ),
                )
                # Refresh remaining queue for this profile.
                self._refresh_profile_eligibility_preserving(conn, profile_id, now)
                conn.commit()
                run_id = str(candidate["id"])
            except Exception:
                conn.rollback()
                raise

        return self._claim_response(run_id)

    def _claim_response(self, run_id: str) -> dict[str, Any] | None:
        with self._get_db() as conn:
            row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            msg = conn.execute(
                "SELECT content FROM task_messages WHERE id = ?",
                (row["task_message_id"],),
            ).fetchone()
        run = db._task_run_from_row(row)
        return {
            "id": run["id"],
            "task_session_id": run["task_session_id"],
            "task": (msg["content"] if msg else ""),
            "profile_id": run.get("profile_id") or run.get("profile_id_snapshot"),
            "sandbox_id": run["sandbox_id"],
            "harness": run["harness"],
            "status": run["status"],
            "allowed_origins": run["allowed_origins"],
            "max_steps": run["max_steps"],
            "timeout_seconds": run["timeout_seconds"],
            "model_alias": run.get("model_alias"),
            "deadline_at": run["deadline_at"],
            "claim_expires_at": run.get("claim_expires_at"),
            "worker_id": run.get("worker_id"),
            "launch_if_stopped": run.get("launch_if_stopped", False),
        }

    # ── Heartbeat ────────────────────────────────────────────────────────────

    def heartbeat(self, worker_id: str, run_id: str) -> dict[str, Any]:
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM task_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None or str(row["worker_id"] or "") != worker_id:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                if row["status"] not in {"health_check", "running"}:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                claim_exp = _parse_dt(row["claim_expires_at"])
                if claim_exp is None or claim_exp <= now:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                deadline = _parse_dt(row["deadline_at"]) or (now + timedelta(seconds=CLAIM_TTL_SECONDS))
                new_exp = min(now + timedelta(seconds=CLAIM_TTL_SECONDS), deadline)
                lease_id = row["lease_id"]
                if not lease_id:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                ok = self._leases.heartbeat_on_conn(
                    conn,
                    str(lease_id),
                    now=now,
                    expires_at=new_exp,
                    owner_kind=automation_leases.RUN_OWNER_KIND,
                    owner_id=worker_id,
                )
                if not ok:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                conn.execute(
                    """
                    UPDATE task_runs
                    SET claim_expires_at = ?, updated_at = ?
                    WHERE id = ? AND worker_id = ?
                    """,
                    (_iso(new_exp), _iso(now), run_id, worker_id),
                )
                cancel_requested = row["cancelled_at"] is not None or row["status"] == "cancelled"
                conn.commit()
            except WorkerNotFound:
                raise
            except Exception:
                conn.rollback()
                raise
        return {
            "claim_expires_at": _iso(new_exp),
            "lease_expires_at": _iso(new_exp),
            "cancel_requested": bool(cancel_requested),
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        }

    # ── Capability ───────────────────────────────────────────────────────────

    def issue_capability(self, worker_id: str, run_id: str) -> dict[str, Any]:
        now = self._clock()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM task_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None or str(row["worker_id"] or "") != worker_id:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                if row["status"] not in {"health_check", "running"}:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                claim_exp = _parse_dt(row["claim_expires_at"])
                deadline = _parse_dt(row["deadline_at"])
                if claim_exp is None or claim_exp <= now:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                if deadline is not None and deadline <= now:
                    conn.commit()
                    raise WorkerNotFound(run_id)
                if row["capability_digest"]:
                    conn.commit()
                    raise CapabilityConflict("already_issued")
                lease_id = row["lease_id"]
                if not lease_id:
                    conn.commit()
                    raise WorkerNotFound(run_id)

                # Fresh health evaluation from current profile measurement.
                profile_id = str(row["profile_id"] or row["profile_id_snapshot"])
                snapshot, decision = db.build_run_health_gate(profile_id)
                # Also honour immutable override already on the run.
                run_decision = db._json_object(row["health_decision_json"])
                if row["health_override_json"]:
                    # Override previously returned to queued/allowed.
                    decision = run_decision
                    snapshot = db._json_object(row["health_snapshot_json"])
                else:
                    conn.execute(
                        """
                        UPDATE task_runs
                        SET health_snapshot_json = ?, health_decision_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            __import__("json").dumps(
                                snapshot, separators=(",", ":"), sort_keys=True
                            ),
                            __import__("json").dumps(
                                decision, separators=(",", ":"), sort_keys=True
                            ),
                            _iso(now),
                            run_id,
                        ),
                    )

                if decision.get("waiting"):
                    conn.commit()
                    raise CapabilityNotReady(
                        waiting=True, blocked=False, decision=decision
                    )
                if not decision.get("allowed"):
                    lease_ids: list[str] = []
                    if lease_id:
                        lease_ids.append(str(lease_id))
                        self._leases.release_on_conn(
                            conn,
                            str(lease_id),
                            now=now,
                            reason="health_blocked",
                        )
                    conn.execute(
                        """
                        UPDATE task_runs
                        SET status = 'blocked_health',
                            claimed_by = NULL,
                            worker_id = NULL,
                            claim_expires_at = NULL,
                            lease_id = NULL,
                            capability_digest = NULL,
                            claim_eligible_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (_iso(now), run_id),
                    )
                    self._refresh_profile_eligibility_preserving(conn, profile_id, now)
                    conn.commit()
                    raise CapabilityNotReady(
                        waiting=False,
                        blocked=True,
                        decision=decision,
                        lease_ids=tuple(lease_ids),
                    )

                token = access.generate_run_capability_token()
                digest = access.hash_run_capability_token(token)
                if not self._leases.set_token_digest_on_conn(
                    conn,
                    str(lease_id),
                    digest,
                    owner_kind=automation_leases.RUN_OWNER_KIND,
                    owner_id=worker_id,
                ):
                    conn.commit()
                    raise WorkerNotFound(run_id)

                expires_at = min(
                    claim_exp,
                    deadline or claim_exp,
                )
                conn.execute(
                    """
                    UPDATE task_runs
                    SET status = 'running',
                        capability_digest = ?,
                        updated_at = ?
                    WHERE id = ? AND capability_digest IS NULL
                    """,
                    (digest, _iso(now), run_id),
                )
                conn.commit()
                return {
                    "token": token,
                    "cdp_url": f"/api/profiles/{profile_id}/cdp",
                    "headers": {"Authorization": f"Bearer {token}"},
                    "expires_at": _iso(expires_at),
                    "profile_id": profile_id,
                    "run_id": run_id,
                }
            except (WorkerNotFound, CapabilityConflict, CapabilityNotReady):
                raise
            except Exception:
                conn.rollback()
                raise

    def revoke_capability(
        self, worker_id: str, run_id: str, *, reason: str = "capability_revoked"
    ) -> TerminalCleanup:
        now = self._clock()
        lease_ids: list[str] = []
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM task_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None or str(row["worker_id"] or "") != worker_id:
                    # Idempotent terminal / wrong actor → treat missing as not found
                    # unless already terminal for same worker historically.
                    if row is not None and row["status"] in db.TASK_RUN_TERMINAL_STATUSES:
                        conn.commit()
                        return TerminalCleanup()
                    conn.commit()
                    raise WorkerNotFound(run_id)
                if row["status"] in db.TASK_RUN_TERMINAL_STATUSES:
                    conn.commit()
                    return TerminalCleanup()
                lid = row["lease_id"]
                if lid:
                    lease_ids.append(str(lid))
                    self._leases.release_on_conn(
                        conn, str(lid), now=now, reason=reason
                    )
                new_status = "revoked" if row["status"] == "running" else row["status"]
                if row["status"] in {"health_check", "running"}:
                    new_status = "revoked"
                conn.execute(
                    """
                    UPDATE task_runs
                    SET status = ?,
                        claimed_by = NULL,
                        claim_expires_at = NULL,
                        capability_digest = NULL,
                        lease_id = NULL,
                        error_code = COALESCE(error_code, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (new_status, reason, _iso(now), run_id),
                )
                if row["profile_id"]:
                    self._refresh_profile_eligibility_preserving(
                        conn, str(row["profile_id"]), now
                    )
                conn.commit()
            except WorkerNotFound:
                raise
            except Exception:
                conn.rollback()
                raise
        return TerminalCleanup(lease_ids=tuple(lease_ids))

    def validate_run_capability(
        self, token: str, profile_id: str
    ) -> automation_leases.LeaseRecord | None:
        if not access.is_run_capability_token(token):
            return None
        now = self._clock()
        digest = access.hash_run_capability_token(token)
        with self._get_db() as conn:
            row = conn.execute(
                """
                SELECT r.*, l.token_digest AS lease_digest, l.expires_at AS lease_expires,
                       l.released_at AS lease_released, l.owner_kind, l.owner_id,
                       l.created_at AS lease_created, l.heartbeat_at AS lease_heartbeat,
                       l.id AS lease_row_id
                FROM task_runs r
                JOIN automation_leases l ON l.id = r.lease_id
                WHERE r.profile_id = ?
                  AND r.capability_digest IS NOT NULL
                  AND r.status = 'running'
                  AND l.released_at IS NULL
                """,
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        import hmac as hmac_mod

        if not hmac_mod.compare_digest(str(row["capability_digest"]), digest):
            return None
        if not hmac_mod.compare_digest(str(row["lease_digest"]), digest):
            return None
        claim_exp = _parse_dt(row["claim_expires_at"])
        deadline = _parse_dt(row["deadline_at"])
        lease_exp = _parse_dt(row["lease_expires"])
        if claim_exp is None or claim_exp <= now:
            return None
        if deadline is not None and deadline <= now:
            return None
        if lease_exp is None or lease_exp <= now:
            return None
        if str(row["worker_id"] or "") == "":
            return None
        return automation_leases.LeaseRecord(
            lease_id=str(row["lease_row_id"]),
            profile_id=profile_id,
            owner_kind=str(row["owner_kind"]),
            owner_id=str(row["owner_id"]),
            created_at=_parse_dt(row["lease_created"]) or now,
            heartbeat_at=_parse_dt(row["lease_heartbeat"]) or now,
            expires_at=lease_exp,
        )

    # ── Terminal transitions ─────────────────────────────────────────────────

    def complete(self, worker_id: str, run_id: str) -> tuple[dict[str, Any], TerminalCleanup]:
        return self._terminal(
            worker_id,
            run_id,
            status="succeeded",
            allowed_statuses={"running"},
            error_code=None,
            error_message=None,
        )

    def fail(
        self,
        worker_id: str,
        run_id: str,
        *,
        error_code: str,
        message: str,
    ) -> tuple[dict[str, Any], TerminalCleanup]:
        if error_code not in ALLOWLISTED_FAIL_CODES:
            raise ValueError("invalid_error_code")
        cleaned = (message or "").strip()
        if not cleaned or len(cleaned) > 500:
            raise ValueError("invalid_message")
        lowered = cleaned.lower()
        for needle in ("bearer ", "authorization"):
            if needle in lowered:
                raise ValueError("invalid_message")
        if access.contains_persisted_cbm_token(cleaned):
            raise ValueError("invalid_message")
        return self._terminal(
            worker_id,
            run_id,
            status="failed",
            allowed_statuses={"health_check", "running"},
            error_code=error_code,
            error_message=cleaned,
        )

    def cancel_run(self, run_id: str) -> tuple[dict[str, Any] | None, TerminalCleanup]:
        """Public cancel: revoke claim/capability/lease; browser stays running."""
        now = self._clock()
        lease_ids: list[str] = []
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM task_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None, TerminalCleanup()
                current = db._task_run_from_row(row)
                if current["status"] in db.TASK_RUN_TERMINAL_STATUSES:
                    conn.commit()
                    return current, TerminalCleanup()
                if current["status"] not in db.TASK_RUN_CANCELABLE_STATUSES:
                    conn.commit()
                    return current, TerminalCleanup()
                lid = row["lease_id"]
                if lid:
                    lease_ids.append(str(lid))
                    self._leases.release_on_conn(
                        conn, str(lid), now=now, reason="cancelled"
                    )
                conn.execute(
                    """
                    UPDATE task_runs
                    SET status = 'cancelled',
                        cancelled_at = ?,
                        claimed_by = NULL,
                        claim_expires_at = NULL,
                        capability_digest = NULL,
                        lease_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('queued', 'health_check', 'blocked_health', 'running')
                    """,
                    (_iso(now), _iso(now), run_id),
                )
                if row["profile_id"]:
                    self._refresh_profile_eligibility_preserving(
                        conn, str(row["profile_id"]), now
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        run = db.get_task_run(run_id)
        return run, TerminalCleanup(lease_ids=tuple(lease_ids))

    def _terminal(
        self,
        worker_id: str,
        run_id: str,
        *,
        status: str,
        allowed_statuses: set[str],
        error_code: str | None,
        error_message: str | None,
    ) -> tuple[dict[str, Any], TerminalCleanup]:
        now = self._clock()
        lease_ids: list[str] = []
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM task_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row["worker_id"] or "") != worker_id
                    or row["status"] not in allowed_statuses
                ):
                    conn.commit()
                    raise WorkerNotFound(run_id)
                lid = row["lease_id"]
                if lid:
                    lease_ids.append(str(lid))
                    self._leases.release_on_conn(
                        conn, str(lid), now=now, reason=status
                    )
                conn.execute(
                    """
                    UPDATE task_runs
                    SET status = ?,
                        claimed_by = NULL,
                        claim_expires_at = NULL,
                        capability_digest = NULL,
                        lease_id = NULL,
                        error_code = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE id = ? AND worker_id = ?
                    """,
                    (
                        status,
                        error_code,
                        error_message,
                        _iso(now),
                        run_id,
                        worker_id,
                    ),
                )
                if row["profile_id"]:
                    self._refresh_profile_eligibility_preserving(
                        conn, str(row["profile_id"]), now
                    )
                conn.commit()
            except WorkerNotFound:
                raise
            except Exception:
                conn.rollback()
                raise
        run = db.get_task_run(run_id)
        if run is None:  # pragma: no cover
            raise WorkerNotFound(run_id)
        return run, TerminalCleanup(lease_ids=tuple(lease_ids))

    def require_bound_claim(self, worker_id: str, run_id: str) -> dict[str, Any]:
        run = db.get_task_run(run_id)
        if run is None:
            raise WorkerNotFound(run_id)
        if str(run.get("worker_id") or "") != worker_id:
            raise WorkerNotFound(run_id)
        if run["status"] not in {"health_check", "running"}:
            raise WorkerNotFound(run_id)
        claim_exp = _parse_dt(run.get("claim_expires_at"))
        if claim_exp is None or claim_exp <= self._clock():
            raise WorkerNotFound(run_id)
        return run

    # ── Maintenance ──────────────────────────────────────────────────────────

    def maintain_once(self) -> TerminalCleanup:
        """Expire claims and eligibility timeouts; return lease IDs to close."""
        now = self._clock()
        timeout = timedelta(seconds=self._eligibility_timeout())
        lease_ids: list[str] = []
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._leases._retire_expired_locked(conn, now)

                # Claim expiry / worker loss.
                expired_claims = conn.execute(
                    """
                    SELECT * FROM task_runs
                    WHERE status IN ('health_check', 'running')
                      AND claim_expires_at IS NOT NULL
                      AND claim_expires_at <= ?
                    """,
                    (_iso(now),),
                ).fetchall()
                for row in expired_claims:
                    lids = self._handle_claim_loss_on_conn(conn, row, now)
                    lease_ids.extend(lids)

                # Eligibility timeout → worker_unavailable.
                overdue = conn.execute(
                    """
                    SELECT * FROM task_runs
                    WHERE status = 'queued'
                      AND claim_eligible_at IS NOT NULL
                    """
                ).fetchall()
                for row in overdue:
                    eligible = _parse_dt(row["claim_eligible_at"])
                    if eligible is None:
                        continue
                    if now < eligible + timeout:
                        continue
                    conn.execute(
                        """
                        UPDATE task_runs
                        SET status = 'failed',
                            error_code = 'worker_unavailable',
                            error_message = 'Claim eligibility timeout',
                            claim_eligible_at = NULL,
                            updated_at = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (_iso(now), row["id"]),
                    )
                    if row["profile_id"]:
                        self._refresh_profile_eligibility_preserving(
                            conn, str(row["profile_id"]), now
                        )

                # Deadline expiry for active claims.
                deadlines = conn.execute(
                    """
                    SELECT * FROM task_runs
                    WHERE status IN ('health_check', 'running', 'queued', 'blocked_health')
                      AND deadline_at <= ?
                    """,
                    (_iso(now),),
                ).fetchall()
                for row in deadlines:
                    lid = row["lease_id"]
                    if lid:
                        lease_ids.append(str(lid))
                        self._leases.release_on_conn(
                            conn, str(lid), now=now, reason="deadline"
                        )
                    conn.execute(
                        """
                        UPDATE task_runs
                        SET status = 'failed',
                            error_code = COALESCE(error_code, 'model_timeout'),
                            error_message = COALESCE(error_message, 'Run deadline exceeded'),
                            claimed_by = NULL,
                            claim_expires_at = NULL,
                            capability_digest = NULL,
                            lease_id = NULL,
                            claim_eligible_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (_iso(now), row["id"]),
                    )
                    if row["profile_id"]:
                        self._refresh_profile_eligibility_preserving(
                            conn, str(row["profile_id"]), now
                        )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return TerminalCleanup(lease_ids=tuple(dict.fromkeys(lease_ids)))

    def _handle_claim_loss_on_conn(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        now: datetime,
    ) -> list[str]:
        lease_ids: list[str] = []
        lid = row["lease_id"]
        if lid:
            lease_ids.append(str(lid))
            self._leases.release_on_conn(conn, str(lid), now=now, reason="claim_expired")

        retry_count = int(row["retry_count"] or 0)
        first_action = row["first_action_at"]
        can_retry = first_action is None and retry_count == 0

        if can_retry:
            conn.execute(
                """
                UPDATE task_runs
                SET status = 'queued',
                    retry_count = retry_count + 1,
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    worker_id = NULL,
                    lease_id = NULL,
                    capability_digest = NULL,
                    queued_at = ?,
                    claim_eligible_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (_iso(now), _iso(now), row["id"]),
            )
        else:
            conn.execute(
                """
                UPDATE task_runs
                SET status = 'failed',
                    error_code = 'worker_lost',
                    error_message = 'Worker heartbeat lost',
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    worker_id = NULL,
                    lease_id = NULL,
                    capability_digest = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (_iso(now), row["id"]),
            )
        if row["profile_id"]:
            self._refresh_profile_eligibility_preserving(
                conn, str(row["profile_id"]), now
            )
        return lease_ids


async def run_worker_maintenance_loop(
    service: WorkerRuntimeService,
    *,
    on_cleanup: Callable[[TerminalCleanup], None] | None = None,
    interval_seconds: float = WORKER_MAINTENANCE_INTERVAL_SECONDS,
    stop_event=None,
) -> None:
    """Frequent claim/eligibility maintenance with safe cancellation."""
    import asyncio

    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            cleanup = await asyncio.to_thread(service.maintain_once)
            if on_cleanup and cleanup.lease_ids:
                on_cleanup(cleanup)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker_runtime_maintenance_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
