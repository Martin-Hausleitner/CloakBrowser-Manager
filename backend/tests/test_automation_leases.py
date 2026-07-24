"""Unit tests for persisted direct automation lease service."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import database as db


NOW = datetime(2026, 7, 24, 18, 0, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, instant: datetime = NOW):
        self.instant = instant

    def __call__(self) -> datetime:
        return self.instant

    def advance(self, seconds: float) -> None:
        self.instant = self.instant + timedelta(seconds=seconds)


@pytest.fixture()
def clock() -> _Clock:
    return _Clock()


@pytest.fixture()
def service(tmp_db: Path, clock: _Clock):
    from backend.automation_leases import AutomationLeaseService

    svc = AutomationLeaseService(clock=clock)
    svc.ensure_schema()
    return svc


@pytest.fixture()
def profile_id(tmp_db: Path) -> str:
    return db.create_profile(name="Lease Profile", sandbox_id="alpha")["id"]


def _owner(actor: str) -> tuple[str, str]:
    return ("agent", actor)


def test_second_profile_lease_is_busy(service, profile_id: str):
    from backend.automation_leases import AutomationBusy

    first = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    with pytest.raises(AutomationBusy):
        service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-b")
    assert service.validate(
        first.lease_id,
        first.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
    )


def test_token_returned_raw_once_and_digest_stored(service, profile_id: str, tmp_db: Path):
    acquired = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    assert acquired.token.startswith("cbm_lease_")
    assert len(bytes.fromhex(acquired.token.removeprefix("cbm_lease_"))) == 32

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM automation_leases WHERE id = ?",
            (acquired.lease_id,),
        ).fetchone()
    assert row is not None
    columns = {k: row[k] for k in row.keys()}
    blob = " ".join(str(v) for v in columns.values())
    assert acquired.token not in blob
    assert "cbm_lease_" not in blob
    digest = hashlib.sha256(acquired.token.encode("utf-8")).hexdigest()
    assert columns["token_digest"] == digest
    assert hmac.compare_digest(columns["token_digest"], digest)


def test_expiry_allows_next_acquire(service, profile_id: str, clock: _Clock):
    from backend.automation_leases import AutomationBusy

    first = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    with pytest.raises(AutomationBusy):
        service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-b")

    clock.advance(46)
    second = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-b")
    assert second.lease_id != first.lease_id
    assert not service.validate(
        first.lease_id,
        first.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
    )
    assert service.validate(
        second.lease_id,
        second.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-b",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda lid, tok, pid: (lid, "cbm_lease_" + ("00" * 32), pid, "agent", "agent-a"), id="wrong-token"),
        pytest.param(lambda lid, tok, pid: (lid, tok, "missing-profile", "agent", "agent-a"), id="wrong-profile"),
        pytest.param(lambda lid, tok, pid: (lid, tok, pid, "agent", "agent-b"), id="wrong-actor"),
        pytest.param(lambda lid, tok, pid: (lid, tok, pid, "user", "agent-a"), id="wrong-kind"),
        pytest.param(lambda lid, tok, pid: ("other-lease", tok, pid, "agent", "agent-a"), id="wrong-lease-id"),
    ],
)
def test_validate_rejects_wrong_credentials_indistinguishably(
    service, profile_id: str, mutate
):
    acquired = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    lease_id, token, pid, kind, owner = mutate(acquired.lease_id, acquired.token, profile_id)
    assert (
        service.validate(lease_id, token, pid, owner_kind=kind, owner_id=owner) is None
    )


def test_validate_rejects_expired_and_released(service, profile_id: str, clock: _Clock):
    acquired = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    clock.advance(46)
    assert (
        service.validate(
            acquired.lease_id,
            acquired.token,
            profile_id,
            owner_kind="agent",
            owner_id="agent-a",
        )
        is None
    )
    service.expire_stale()

    fresh = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    assert service.release(
        fresh.lease_id,
        fresh.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
        reason="done",
    )
    assert (
        service.validate(
            fresh.lease_id,
            fresh.token,
            profile_id,
            owner_kind="agent",
            owner_id="agent-a",
        )
        is None
    )


def test_heartbeat_and_release_bind_owner_and_token(service, profile_id: str, clock: _Clock):
    from backend.automation_leases import AutomationLeaseInvalid

    acquired = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    assert acquired.expires_at == NOW + timedelta(seconds=45)
    assert acquired.heartbeat_interval_seconds == 15

    with pytest.raises(AutomationLeaseInvalid):
        service.heartbeat(
            acquired.lease_id,
            acquired.token,
            profile_id,
            owner_kind="agent",
            owner_id="agent-b",
        )
    with pytest.raises(AutomationLeaseInvalid):
        service.heartbeat(
            acquired.lease_id,
            "cbm_lease_" + ("11" * 32),
            profile_id,
            owner_kind="agent",
            owner_id="agent-a",
        )

    clock.advance(20)
    renewed = service.heartbeat(
        acquired.lease_id,
        acquired.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
    )
    assert renewed == clock.instant + timedelta(seconds=45)

    assert service.release(
        acquired.lease_id,
        acquired.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
        reason="manual",
    )
    # Idempotent for owning actor/token.
    assert service.release(
        acquired.lease_id,
        acquired.token,
        profile_id,
        owner_kind="agent",
        owner_id="agent-a",
        reason="manual",
    )
    with pytest.raises(AutomationLeaseInvalid):
        service.release(
            acquired.lease_id,
            acquired.token,
            profile_id,
            owner_kind="agent",
            owner_id="agent-b",
            reason="manual",
        )


def test_expire_stale_returns_ids_and_preserves_history(
    service, profile_id: str, clock: _Clock
):
    first = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    clock.advance(46)
    expired = service.expire_stale()
    assert expired == [(first.lease_id, profile_id)]

    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, released_at, release_reason FROM automation_leases"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == first.lease_id
    assert rows[0]["released_at"] is not None
    assert rows[0]["release_reason"] == "expired"


def test_concurrent_schema_init_is_idempotent(tmp_db: Path, clock: _Clock):
    from backend.automation_leases import AutomationLeaseService

    errors: list[BaseException] = []

    def init_once() -> None:
        try:
            AutomationLeaseService(clock=clock).ensure_schema()
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    threads = [threading.Thread(target=init_once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []

    with db.get_db() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='automation_leases'"
        ).fetchone()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='automation_leases'"
        ).fetchall()
    assert table is not None
    assert any("active" in (row["name"] or "") for row in indexes)


def test_concurrent_second_acquire_is_busy(tmp_db: Path, profile_id: str, clock: _Clock):
    from backend.automation_leases import AutomationBusy, AutomationLeaseService

    service = AutomationLeaseService(clock=clock)
    service.ensure_schema()
    barrier = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def attempt(owner_id: str) -> None:
        local = AutomationLeaseService(clock=clock)
        barrier.wait(timeout=5)
        try:
            acquired = local.acquire_direct(
                profile_id, owner_kind="agent", owner_id=owner_id
            )
            with lock:
                results.append(acquired)
        except AutomationBusy as exc:
            with lock:
                results.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, "a"), pool.submit(attempt, "b")]
        for future in futures:
            future.result(timeout=5)

    successes = [item for item in results if not isinstance(item, AutomationBusy)]
    failures = [item for item in results if isinstance(item, AutomationBusy)]
    assert len(successes) == 1
    assert len(failures) == 1


def test_partial_unique_index_enforces_one_unreleased_lease(
    service, profile_id: str
):
    first = service.acquire_direct(profile_id, owner_kind="agent", owner_id="agent-a")
    digest = hashlib.sha256(b"other").hexdigest()
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_db() as conn:
            conn.execute(
                """INSERT INTO automation_leases (
                    id, profile_id, owner_kind, owner_id, token_digest,
                    created_at, heartbeat_at, expires_at, released_at, release_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (
                    "forced-second",
                    profile_id,
                    "agent",
                    "agent-b",
                    digest,
                    first.created_at.isoformat(),
                    first.created_at.isoformat(),
                    first.expires_at.isoformat(),
                ),
            )
            conn.commit()
