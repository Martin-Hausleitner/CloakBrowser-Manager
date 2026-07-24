"""Unit tests for immutable, versioned run health gating."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.profile_health import (
    ProfileHealthResult,
    map_profile_health_gate_fields,
)
from backend.run_health import (
    CRITICAL_REASON_CODES,
    HEALTH_POLICY_VERSION,
    HealthDecision,
    HealthPolicy,
    HealthSnapshot,
    NON_OVERRIDABLE_REASON_CODES,
    evaluate_health,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
POLICY = HealthPolicy()


def _snapshot(**overrides: object) -> HealthSnapshot:
    base = {
        "state": "passed",
        "checked_at": NOW - timedelta(minutes=1),
        "proxy_configured": False,
        "proxy_reachable": None,
        "measured_authenticity_score": 90,
        "inferred_authenticity_score": None,
        "reasons": (),
        "measurement_error": False,
        "policy_version": HEALTH_POLICY_VERSION,
        "outbound_ip_masked": "203.0.113.x",
    }
    base.update(overrides)
    return HealthSnapshot(**base)  # type: ignore[arg-type]


def test_health_policy_defaults_and_freshness_bounds():
    assert POLICY.version == HEALTH_POLICY_VERSION
    assert POLICY.freshness_minutes == 10
    assert POLICY.measured_authenticity_threshold == 70
    assert HealthPolicy(freshness_minutes=1).freshness_minutes == 1
    assert HealthPolicy(freshness_minutes=60).freshness_minutes == 60
    with pytest.raises(ValueError):
        HealthPolicy(freshness_minutes=0)
    with pytest.raises(ValueError):
        HealthPolicy(freshness_minutes=61)


@pytest.mark.parametrize("state", ["pending", "running"])
def test_pending_and_running_wait_deterministically(state: str):
    decision = evaluate_health(_snapshot(state=state), POLICY, now=NOW)
    assert decision.allowed is False
    assert decision.waiting is True
    assert state in decision.failed_reasons
    assert decision.policy_version == HEALTH_POLICY_VERSION


@pytest.mark.parametrize("state", ["failed", "unavailable"])
def test_failed_and_unavailable_block(state: str):
    decision = evaluate_health(_snapshot(state=state), POLICY, now=NOW)
    assert decision.allowed is False
    assert decision.waiting is False
    assert state in decision.failed_reasons


def test_passed_proceeds_when_all_gates_pass():
    decision = evaluate_health(_snapshot(state="passed"), POLICY, now=NOW)
    assert decision.allowed is True
    assert decision.waiting is False
    assert decision.failed_reasons == ()
    assert decision.non_overridable_reasons == ()


def test_warning_proceeds_only_without_critical_reasons_and_with_score():
    ok = evaluate_health(
        _snapshot(state="warning", measured_authenticity_score=89, reasons=()),
        POLICY,
        now=NOW,
    )
    assert ok.allowed is True

    blocked = evaluate_health(
        _snapshot(
            state="warning",
            measured_authenticity_score=89,
            reasons=("platform_ua_mismatch",),
        ),
        POLICY,
        now=NOW,
    )
    assert blocked.allowed is False
    assert "platform_ua_mismatch" in blocked.failed_reasons


def test_fresh_stale_boundary_is_inclusive_at_exact_window():
    fresh = evaluate_health(
        _snapshot(checked_at=NOW - timedelta(minutes=10)),
        POLICY,
        now=NOW,
    )
    assert fresh.allowed is True

    stale = evaluate_health(
        _snapshot(checked_at=NOW - timedelta(minutes=10, seconds=1)),
        POLICY,
        now=NOW,
    )
    assert stale.allowed is False
    assert "health_stale" in stale.failed_reasons


def test_missing_checked_at_is_stale():
    decision = evaluate_health(_snapshot(checked_at=None), POLICY, now=NOW)
    assert decision.allowed is False
    assert "health_stale" in decision.failed_reasons


def test_measured_score_threshold_69_fails_70_passes():
    assert (
        evaluate_health(
            _snapshot(measured_authenticity_score=70),
            POLICY,
            now=NOW,
        ).allowed
        is True
    )
    decision = evaluate_health(
        _snapshot(measured_authenticity_score=69),
        POLICY,
        now=NOW,
    )
    assert decision.allowed is False
    assert "measured_authenticity_below_threshold" in decision.failed_reasons


def test_inferred_score_does_not_satisfy_measured_gate():
    decision = evaluate_health(
        _snapshot(
            measured_authenticity_score=None,
            inferred_authenticity_score=95,
        ),
        POLICY,
        now=NOW,
    )
    assert decision.allowed is False
    assert "measured_authenticity_below_threshold" in decision.failed_reasons
    assert decision.allowed is False


def test_configured_proxy_requires_reachable_true():
    ok = evaluate_health(
        _snapshot(proxy_configured=True, proxy_reachable=True),
        POLICY,
        now=NOW,
    )
    assert ok.allowed is True

    blocked = evaluate_health(
        _snapshot(proxy_configured=True, proxy_reachable=False),
        POLICY,
        now=NOW,
    )
    assert blocked.allowed is False
    assert "proxy_unreachable" in blocked.failed_reasons
    assert "proxy_unreachable" in blocked.non_overridable_reasons

    unknown = evaluate_health(
        _snapshot(proxy_configured=True, proxy_reachable=None),
        POLICY,
        now=NOW,
    )
    assert unknown.allowed is False
    assert "proxy_unreachable" in unknown.failed_reasons


def test_unconfigured_proxy_does_not_require_reachability():
    decision = evaluate_health(
        _snapshot(proxy_configured=False, proxy_reachable=None),
        POLICY,
        now=NOW,
    )
    assert decision.allowed is True


def test_measurement_error_blocks_and_is_non_overridable():
    decision = evaluate_health(
        _snapshot(measurement_error=True),
        POLICY,
        now=NOW,
    )
    assert decision.allowed is False
    assert "measurement_error" in decision.failed_reasons
    assert "measurement_error" in decision.non_overridable_reasons


@pytest.mark.parametrize("reason", sorted(CRITICAL_REASON_CODES))
def test_every_critical_reason_blocks(reason: str):
    decision = evaluate_health(_snapshot(reasons=(reason,)), POLICY, now=NOW)
    assert decision.allowed is False
    assert reason in decision.failed_reasons


def test_overrideability_exposes_stable_non_overridable_codes():
    assert "proxy_unreachable" in NON_OVERRIDABLE_REASON_CODES
    assert "measurement_error" in NON_OVERRIDABLE_REASON_CODES

    overridable = evaluate_health(
        _snapshot(reasons=("platform_ua_mismatch",), measured_authenticity_score=50),
        POLICY,
        now=NOW,
    )
    assert overridable.allowed is False
    assert "platform_ua_mismatch" in overridable.failed_reasons
    assert "measured_authenticity_below_threshold" in overridable.failed_reasons
    assert overridable.non_overridable_reasons == ()

    mixed = evaluate_health(
        _snapshot(
            proxy_configured=True,
            proxy_reachable=False,
            reasons=("platform_ua_mismatch",),
            measurement_error=True,
        ),
        POLICY,
        now=NOW,
    )
    assert set(mixed.non_overridable_reasons) == {
        "proxy_unreachable",
        "measurement_error",
    }


def test_snapshot_and_decision_serialize_immutably():
    snapshot = _snapshot(
        state="warning",
        reasons=("proxy_exit_mismatch",),
        measured_authenticity_score=88,
        inferred_authenticity_score=12,
    )
    payload = snapshot.to_dict()
    restored = HealthSnapshot.from_dict(payload)
    assert restored == snapshot
    assert restored.reasons == ("proxy_exit_mismatch",)
    assert restored.policy_version == HEALTH_POLICY_VERSION

    decision = evaluate_health(snapshot, POLICY, now=NOW)
    decision_payload = decision.to_dict()
    assert decision_payload["allowed"] is False
    assert decision_payload["waiting"] is False
    assert decision_payload["policy_version"] == HEALTH_POLICY_VERSION
    assert "proxy_exit_mismatch" in decision_payload["failed_reasons"]
    assert isinstance(decision, HealthDecision)
    # Frozen / immutable
    with pytest.raises(Exception):
        snapshot.state = "passed"  # type: ignore[misc]
    with pytest.raises(Exception):
        decision.allowed = True  # type: ignore[misc]


def test_map_profile_health_gate_fields_exposes_stable_semantics():
    result = ProfileHealthResult(
        state="warning",
        checked_at="2026-07-24T11:55:00+00:00",
        proxy_configured=True,
        proxy_reachable=False,
        outbound_ip_masked="203.0.113.x",
        proxy_latency_ms=12.0,
        proxy_risk_score=10,
        proxy_authenticity_score=90,
        fingerprint_consistency_score=80,
        browser_scan_score=None,
        warnings=("platform_mismatch", "user_agent_family_mismatch"),
        blockers=("network_timeout",),
        error_code="network_timeout",
        sources={
            "browser_network": "unavailable",
            "fingerprint_consistency": "measured",
            "browser_scan": "unavailable",
            "proxychecker": "measured",
            "proxy_authenticity": "derived",
        },
    )
    fields = map_profile_health_gate_fields(result)
    assert fields["measured_authenticity_score"] is None
    assert fields["inferred_authenticity_score"] == 90
    assert fields["measurement_error"] is True
    assert "proxy_unreachable" in fields["reasons"]
    assert "platform_ua_mismatch" in fields["reasons"]
    assert fields["policy_version"] == HEALTH_POLICY_VERSION
    assert fields["outbound_ip_masked"] == "203.0.113.x"

    measured = ProfileHealthResult(
        state="passed",
        checked_at="2026-07-24T11:55:00+00:00",
        proxy_configured=False,
        proxy_reachable=True,
        outbound_ip_masked=None,
        proxy_latency_ms=None,
        proxy_risk_score=None,
        proxy_authenticity_score=77,
        fingerprint_consistency_score=100,
        browser_scan_score=88,
        warnings=(),
        blockers=(),
        error_code=None,
        sources={"proxy_authenticity": "measured"},
    )
    measured_fields = map_profile_health_gate_fields(measured)
    assert measured_fields["measured_authenticity_score"] == 77
    assert measured_fields["inferred_authenticity_score"] is None
    assert measured_fields["measurement_error"] is False
    assert measured_fields["reasons"] == ()
