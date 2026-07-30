"""TDD tests for the synthetic auth scenario matrix.

No external network. Scenario definitions must never embed real credentials,
cookies, or OTP values that would be safe to log. Passkey scenarios are
synthetic CDP-only; real-provider flags stay false.
"""

from __future__ import annotations

import re

import pytest

from benchmarks.auth import scenarios as sc

# Full live matrix exercised by live_runner (16 scenarios).
REQUIRED_LIVE_SCENARIO_IDS = frozenset(
    {
        "password_success",
        "password_failure",
        "otp_handoff",
        "otp_expiry",
        "oauth_popup_success",
        "oauth_popup_cancel",
        "oauth_popup_close",
        "oauth_popup_reopen",
        "passkey_register_assert",
        "passkey_prompt_recovery",
        "passkey_uv_required",
        "passkey_no_authenticator",
        "passkey_revoked_credential",
        "passkey_conditional_mediation",
        "oauth_passkey_2fa_success",
        "oauth_passkey_2fa_popup_reopen",
    }
)

# Fixture-server-only paths (not run by live Chromium matrix).
REQUIRED_FIXTURE_ONLY_IDS = frozenset(
    {
        "oauth_wrong_origin_postmessage",
        "auth_timeout",
    }
)

FORBIDDEN_SECRET_PATTERNS = (
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"otp\s*[:=]\s*\d{4,}", re.IGNORECASE),
    re.compile(r"totp\s*[:=]", re.IGNORECASE),
    re.compile(r"cookie\s*[:=]", re.IGNORECASE),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
)


def test_matrix_includes_required_auth_scenarios():
    ids = {s.id for s in sc.SCENARIO_MATRIX}
    assert REQUIRED_LIVE_SCENARIO_IDS.issubset(ids)
    assert REQUIRED_FIXTURE_ONLY_IDS.issubset(ids)
    assert sc.validate_scenario_matrix() is True


def test_live_catalog_is_exactly_sixteen_scenarios():
    live = sc.live_scenario_ids()
    assert len(live) == 16
    assert set(live) == REQUIRED_LIVE_SCENARIO_IDS
    fixture_only = sc.fixture_only_scenario_ids()
    assert set(fixture_only) == REQUIRED_FIXTURE_ONLY_IDS
    # Live and fixture-only partitions must not overlap.
    assert set(live).isdisjoint(set(fixture_only))


def test_catalog_is_canonical_expected_outcome_source():
    """All scenarios — live + fixture-only — expose expected outcomes from catalog."""
    outcomes = sc.expected_outcomes()
    for scenario in sc.SCENARIO_MATRIX:
        assert outcomes[scenario.id] == scenario.expected_outcome

    live_outcomes = sc.expected_outcomes(live_only=True)
    assert set(live_outcomes) == REQUIRED_LIVE_SCENARIO_IDS
    assert "oauth_wrong_origin_postmessage" not in live_outcomes
    assert "auth_timeout" not in live_outcomes

    # Live OAuth reopen must expect success after RP callback completion.
    assert live_outcomes["oauth_popup_reopen"] == "success"
    assert sc.get_scenario("oauth_popup_reopen").expected_outcome == "success"


def test_scenario_lookup_and_ordered_ids():
    first = sc.get_scenario("password_success")
    assert first.id == "password_success"
    assert first.family == "password"
    assert first.expected_outcome == "success"

    with pytest.raises(KeyError):
        sc.get_scenario("does_not_exist")

    ordered = sc.scenario_ids()
    assert ordered == tuple(s.id for s in sc.SCENARIO_MATRIX)
    assert len(ordered) == len(set(ordered))


def test_password_scenarios_cover_success_and_failure():
    success = sc.get_scenario("password_success")
    failure = sc.get_scenario("password_failure")
    assert success.expected_outcome == "success"
    assert failure.expected_outcome == "failure"
    assert "submit_password" in success.steps
    assert "submit_password" in failure.steps
    assert success.allows_external_network is False
    assert failure.allows_external_network is False


def test_otp_scenarios_handoff_and_expiry_without_secret_values():
    handoff = sc.get_scenario("otp_handoff")
    expiry = sc.get_scenario("otp_expiry")
    assert handoff.family == "otp"
    assert expiry.family == "otp"
    assert handoff.expected_outcome == "success"
    assert expiry.expected_outcome == "expired"
    assert handoff.log_secrets is False
    assert expiry.log_secrets is False
    for scenario in (handoff, expiry):
        blob = " ".join([scenario.id, scenario.description, *scenario.steps])
        assert not re.search(r"\b\d{6}\b", blob), "OTP numeric codes must not appear in matrix text"
        for pattern in FORBIDDEN_SECRET_PATTERNS:
            assert pattern.search(blob) is None


def test_oauth_popup_scenarios_cover_success_cancel_close_reopen():
    mapping = {
        "oauth_popup_success": "success",
        "oauth_popup_cancel": "cancel",
        "oauth_popup_close": "close",
        "oauth_popup_reopen": "success",
    }
    for sid, outcome in mapping.items():
        scenario = sc.get_scenario(sid)
        assert scenario.family == "oauth"
        assert scenario.expected_outcome == outcome
        assert scenario.popup is True
        assert scenario.fixture_only is False
        assert "open_popup" in scenario.steps


def test_wrong_origin_postmessage_and_timeout_are_fixture_only():
    wrong = sc.get_scenario("oauth_wrong_origin_postmessage")
    timeout = sc.get_scenario("auth_timeout")
    assert wrong.family == "oauth"
    assert wrong.expected_outcome == "wrong_origin"
    assert wrong.fixture_only is True
    assert "postmessage" in " ".join(wrong.steps).lower()
    assert timeout.expected_outcome == "timeout"
    assert timeout.fixture_only is True
    assert timeout.timeout_ms > 0
    assert timeout.timeout_ms <= sc.MAX_SCENARIO_TIMEOUT_MS


def test_passkey_scenarios_are_synthetic_only_with_real_provider_fields_false():
    passkey_ids = (
        "passkey_register_assert",
        "passkey_prompt_recovery",
        "passkey_uv_required",
        "passkey_no_authenticator",
        "passkey_revoked_credential",
        "passkey_conditional_mediation",
        "oauth_passkey_2fa_success",
        "oauth_passkey_2fa_popup_reopen",
    )
    expected = {
        "passkey_register_assert": "success",
        "passkey_prompt_recovery": "success",
        "passkey_uv_required": "user_verification_required",
        "passkey_no_authenticator": "no_authenticator",
        "passkey_revoked_credential": "credential_revoked",
        "passkey_conditional_mediation": "success",
        "oauth_passkey_2fa_success": "success",
        "oauth_passkey_2fa_popup_reopen": "success",
    }
    for sid in passkey_ids:
        scenario = sc.get_scenario(sid)
        assert scenario.expected_outcome == expected[sid]
        assert scenario.fixture_only is False
        assert scenario.allows_external_network is False
        assert scenario.requires_real_google is False
        assert scenario.requires_real_credentials is False
        # Catalog flag means real hardware/provider passkeys, not synthetic CDP.
        assert scenario.requires_passkey is False
        assert scenario.log_secrets is False
        blob = f"{scenario.description} {' '.join(scenario.steps)}".lower()
        assert "accounts.google.com" not in blob
        assert "googleapis.com" not in blob
        # Must stay synthetic / virtual — no real provider language.
        assert "real google" not in blob
        assert "hardware" not in blob


def test_matrix_forbids_external_network_and_real_providers():
    for scenario in sc.SCENARIO_MATRIX:
        assert scenario.allows_external_network is False
        assert scenario.requires_real_google is False
        assert scenario.requires_real_credentials is False
        assert scenario.requires_passkey is False
        blob = f"{scenario.description} {' '.join(scenario.steps)}".lower()
        assert "accounts.google.com" not in blob
        assert "googleapis.com" not in blob


def test_sanitize_log_redacts_passwords_otp_and_tokens():
    raw = (
        "login password=super-secret-value otp=123456 "
        "Authorization: Bearer abc.def.ghi cookie=session=xyz"
    )
    cleaned = sc.sanitize_log_message(raw)
    assert "super-secret-value" not in cleaned
    assert "123456" not in cleaned
    assert "abc.def.ghi" not in cleaned
    assert "session=xyz" not in cleaned
    assert "[REDACTED]" in cleaned


def test_scenarios_by_family_groups_password_otp_oauth_passkey():
    groups = sc.scenarios_by_family()
    assert set(groups["password"]) >= {"password_success", "password_failure"}
    assert set(groups["otp"]) >= {"otp_handoff", "otp_expiry"}
    assert "oauth_popup_success" in groups["oauth"]
    assert "auth_timeout" in groups.get("timeout", groups.get("auth", ()))
    assert "passkey_register_assert" in groups["passkey"]
    assert "oauth_passkey_2fa_success" in groups.get("oauth_passkey", groups.get("passkey", ()))
