"""Immutable versioned health policy, snapshot, and automation gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

HEALTH_POLICY_VERSION = "run-health.v1"
DEFAULT_FRESHNESS_MINUTES = 10
MEASURED_AUTHENTICITY_THRESHOLD = 70

HEALTH_STATES = frozenset(
    {"pending", "running", "passed", "warning", "failed", "unavailable"}
)
WAITING_STATES = frozenset({"pending", "running"})
BLOCKING_STATES = frozenset({"failed", "unavailable"})
PROCEED_STATES = frozenset({"passed", "warning"})

CRITICAL_REASON_CODES = frozenset(
    {
        "proxy_unreachable",
        "proxy_exit_mismatch",
        "platform_ua_mismatch",
        "mobile_identity_inconsistent",
    }
)
NON_OVERRIDABLE_REASON_CODES = frozenset(
    {
        "proxy_unreachable",
        "measurement_error",
        "health_timestamp_in_future",
        "health_policy_version_mismatch",
    }
)


def _require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _require_optional_bool(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool or null")
    return value


def _require_optional_score(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer or null")
    if value < 0 or value > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")
    return value


def _require_policy_version(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.strip():
        raise ValueError("policy_version must be a non-empty string")
    return value


def _require_reasons(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("reasons must be a list or tuple of strings")
    reasons = tuple(value)
    if any(not isinstance(item, str) for item in reasons):
        raise ValueError("reasons must be a list or tuple of strings")
    return reasons


@dataclass(frozen=True)
class HealthPolicy:
    version: str = HEALTH_POLICY_VERSION
    freshness_minutes: int = DEFAULT_FRESHNESS_MINUTES
    measured_authenticity_threshold: int = MEASURED_AUTHENTICITY_THRESHOLD

    def __post_init__(self) -> None:
        if not isinstance(self.freshness_minutes, int) or isinstance(
            self.freshness_minutes, bool
        ):
            raise ValueError("freshness_minutes must be an integer from 1 to 60")
        if self.freshness_minutes < 1 or self.freshness_minutes > 60:
            raise ValueError("freshness_minutes must be between 1 and 60")
        if not isinstance(self.measured_authenticity_threshold, int) or isinstance(
            self.measured_authenticity_threshold, bool
        ):
            raise ValueError("measured_authenticity_threshold must be an integer")
        if (
            self.measured_authenticity_threshold < 0
            or self.measured_authenticity_threshold > 100
        ):
            raise ValueError("measured_authenticity_threshold must be between 0 and 100")


@dataclass(frozen=True)
class HealthSnapshot:
    state: str
    checked_at: datetime | None
    proxy_configured: bool
    proxy_reachable: bool | None
    measured_authenticity_score: int | None
    inferred_authenticity_score: int | None
    reasons: tuple[str, ...]
    measurement_error: bool
    policy_version: str = HEALTH_POLICY_VERSION
    outbound_ip_masked: str | None = None

    def __post_init__(self) -> None:
        if self.state not in HEALTH_STATES:
            raise ValueError(f"unsupported health state: {self.state}")
        object.__setattr__(
            self, "proxy_configured", _require_bool(self.proxy_configured, field_name="proxy_configured")
        )
        object.__setattr__(
            self,
            "proxy_reachable",
            _require_optional_bool(self.proxy_reachable, field_name="proxy_reachable"),
        )
        object.__setattr__(
            self,
            "measured_authenticity_score",
            _require_optional_score(
                self.measured_authenticity_score, field_name="measured_authenticity_score"
            ),
        )
        object.__setattr__(
            self,
            "inferred_authenticity_score",
            _require_optional_score(
                self.inferred_authenticity_score, field_name="inferred_authenticity_score"
            ),
        )
        object.__setattr__(self, "reasons", _require_reasons(self.reasons))
        object.__setattr__(
            self,
            "measurement_error",
            _require_bool(self.measurement_error, field_name="measurement_error"),
        )
        object.__setattr__(self, "policy_version", _require_policy_version(self.policy_version))
        if self.outbound_ip_masked is not None and not isinstance(self.outbound_ip_masked, str):
            raise ValueError("outbound_ip_masked must be a string or null")
        if self.checked_at is not None:
            if not isinstance(self.checked_at, datetime):
                raise ValueError("checked_at must be an ISO timestamp or null")
            object.__setattr__(
                self,
                "checked_at",
                _require_aware(self.checked_at, field_name="checked_at"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checked_at": None if self.checked_at is None else self.checked_at.isoformat(),
            "proxy_configured": self.proxy_configured,
            "proxy_reachable": self.proxy_reachable,
            "measured_authenticity_score": self.measured_authenticity_score,
            "inferred_authenticity_score": self.inferred_authenticity_score,
            "reasons": list(self.reasons),
            "measurement_error": self.measurement_error,
            "policy_version": self.policy_version,
            "outbound_ip_masked": self.outbound_ip_masked,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HealthSnapshot:
        checked_raw = data.get("checked_at")
        checked_at: datetime | None
        if checked_raw is None:
            checked_at = None
        elif isinstance(checked_raw, datetime):
            checked_at = checked_raw
        elif isinstance(checked_raw, str):
            checked_at = datetime.fromisoformat(checked_raw)
        else:
            raise ValueError("checked_at must be an ISO timestamp or null")
        if "reasons" not in data:
            reasons: Any = ()
        else:
            reasons = data["reasons"]
        if "policy_version" not in data:
            policy_version: Any = HEALTH_POLICY_VERSION
        else:
            policy_version = data["policy_version"]
        outbound = data.get("outbound_ip_masked")
        if outbound is not None and not isinstance(outbound, str):
            raise ValueError("outbound_ip_masked must be a string or null")
        return cls(
            state=data["state"],
            checked_at=checked_at,
            proxy_configured=data.get("proxy_configured"),
            proxy_reachable=data.get("proxy_reachable"),
            measured_authenticity_score=data.get("measured_authenticity_score"),
            inferred_authenticity_score=data.get("inferred_authenticity_score"),
            reasons=reasons,
            measurement_error=data.get("measurement_error"),
            policy_version=policy_version,
            outbound_ip_masked=outbound,
        )


@dataclass(frozen=True)
class HealthDecision:
    allowed: bool
    waiting: bool
    failed_reasons: tuple[str, ...]
    non_overridable_reasons: tuple[str, ...]
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "waiting": self.waiting,
            "failed_reasons": list(self.failed_reasons),
            "non_overridable_reasons": list(self.non_overridable_reasons),
            "policy_version": self.policy_version,
        }


def evaluate_health(
    snapshot: HealthSnapshot,
    policy: HealthPolicy | None = None,
    *,
    now: datetime | None = None,
) -> HealthDecision:
    """Deterministically gate automation from an immutable health snapshot."""
    active_policy = policy or HealthPolicy()
    instant = _require_aware(now or datetime.now(timezone.utc), field_name="now")
    failed: list[str] = []

    if snapshot.policy_version != active_policy.version:
        failed.append("health_policy_version_mismatch")
        if snapshot.state in WAITING_STATES:
            return _decision(False, False, failed, active_policy.version)

    if snapshot.state in WAITING_STATES:
        failed.append(snapshot.state)
        return _decision(False, True, failed, active_policy.version)

    if snapshot.state in BLOCKING_STATES:
        failed.append(snapshot.state)
    elif snapshot.state not in PROCEED_STATES:
        failed.append(snapshot.state)
        return _decision(False, False, failed, active_policy.version)

    if snapshot.state in BLOCKING_STATES:
        if snapshot.measurement_error:
            failed.append("measurement_error")
        if snapshot.proxy_configured and snapshot.proxy_reachable is not True:
            failed.append("proxy_unreachable")
        return _decision(False, False, failed, active_policy.version)

    if snapshot.checked_at is None:
        failed.append("health_stale")
    else:
        age = instant - snapshot.checked_at
        if age < timedelta(0):
            failed.append("health_timestamp_in_future")
        elif age > timedelta(minutes=active_policy.freshness_minutes):
            failed.append("health_stale")

    if snapshot.measurement_error:
        failed.append("measurement_error")

    if snapshot.proxy_configured and snapshot.proxy_reachable is not True:
        failed.append("proxy_unreachable")

    measured = snapshot.measured_authenticity_score
    if (
        not isinstance(measured, int)
        or isinstance(measured, bool)
        or measured < active_policy.measured_authenticity_threshold
    ):
        failed.append("measured_authenticity_below_threshold")

    for reason in snapshot.reasons:
        if reason in CRITICAL_REASON_CODES and reason not in failed:
            failed.append(reason)

    allowed = not failed
    return _decision(allowed, False, failed, active_policy.version)


def _decision(
    allowed: bool,
    waiting: bool,
    failed_reasons: list[str],
    policy_version: str,
) -> HealthDecision:
    unique_failed = tuple(dict.fromkeys(failed_reasons))
    non_overridable = tuple(
        reason for reason in unique_failed if reason in NON_OVERRIDABLE_REASON_CODES
    )
    return HealthDecision(
        allowed=allowed,
        waiting=waiting,
        failed_reasons=unique_failed,
        non_overridable_reasons=non_overridable,
        policy_version=policy_version,
    )
