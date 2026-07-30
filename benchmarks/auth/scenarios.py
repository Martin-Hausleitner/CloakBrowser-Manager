"""Synthetic auth scenario matrix for offline browser-agent benchmarks.

Defines deterministic password, OTP, OAuth-popup, passkey (CDP virtual), and
timeout scenarios without real Google accounts, credentials, cookies, or
hardware passkeys. Scenario text must stay safe to log: no OTP digits,
passwords, tokens, or provider secrets.

The catalog is the canonical source of expected outcomes for the live Chromium
matrix (16 scenarios) and fixture-only wrong-origin / timeout cases.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

MAX_SCENARIO_TIMEOUT_MS: Final[int] = 5_000

_REDACT_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"(?i)(password\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(otp\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(totp\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(cookie\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(access_token\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(refresh_token\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    # Bare 6-digit OTP-looking runs after known labels only; keep generic
    # scenario timeouts (milliseconds) intact.
    (re.compile(r"(?i)\b(otp|code|token)\b([^\n]{0,20}?)(\d{6})\b"), r"\1\2[REDACTED]"),
)


@dataclass(frozen=True, slots=True)
class AuthScenario:
    """One synthetic auth path exercised by the local fixture server and/or live runner."""

    id: str
    family: str
    expected_outcome: str
    description: str
    steps: tuple[str, ...]
    popup: bool = False
    timeout_ms: int = 0
    log_secrets: bool = False
    allows_external_network: bool = False
    requires_real_google: bool = False
    requires_real_credentials: bool = False
    # True only for real hardware/provider passkeys (forbidden). Synthetic CDP
    # virtual-authenticator scenarios keep this False.
    requires_passkey: bool = False
    # Fixture HTTP paths that are not part of the live Chromium matrix.
    fixture_only: bool = False


SCENARIO_MATRIX: Final[tuple[AuthScenario, ...]] = (
    AuthScenario(
        id="password_success",
        family="password",
        expected_outcome="success",
        description="Submit synthetic username and password on local RP; expect success.",
        steps=("open_login", "submit_password", "assert_session"),
    ),
    AuthScenario(
        id="password_failure",
        family="password",
        expected_outcome="failure",
        description="Submit wrong synthetic password on local RP; expect failure.",
        steps=("open_login", "submit_password", "assert_error"),
    ),
    AuthScenario(
        id="otp_handoff",
        family="otp",
        expected_outcome="success",
        description="After password, hand off OTP challenge and verify without logging codes.",
        steps=("open_login", "submit_password", "start_otp", "submit_otp", "assert_session"),
    ),
    AuthScenario(
        id="otp_expiry",
        family="otp",
        expected_outcome="expired",
        description="Start OTP challenge, expire it, then reject late submit without logging codes.",
        steps=("open_login", "submit_password", "start_otp", "expire_otp", "submit_otp", "assert_expired"),
    ),
    AuthScenario(
        id="oauth_popup_success",
        family="oauth",
        expected_outcome="success",
        description="Open local Google-style OAuth popup, approve, verify local RP callback.",
        steps=("open_login", "open_popup", "approve", "receive_callback", "assert_session"),
        popup=True,
    ),
    AuthScenario(
        id="oauth_popup_cancel",
        family="oauth",
        expected_outcome="cancel",
        description="Open local OAuth popup and cancel consent.",
        steps=("open_login", "open_popup", "cancel", "assert_cancel"),
        popup=True,
    ),
    AuthScenario(
        id="oauth_popup_close",
        family="oauth",
        expected_outcome="close",
        description="Open local OAuth popup and close without a decision.",
        steps=("open_login", "open_popup", "close_popup", "assert_close"),
        popup=True,
    ),
    AuthScenario(
        id="oauth_popup_reopen",
        family="oauth",
        expected_outcome="success",
        description=(
            "Close OAuth popup then reopen, approve on local IdP, verify RP callback success."
        ),
        steps=(
            "open_login",
            "open_popup",
            "close_popup",
            "reopen_popup",
            "approve",
            "receive_callback",
            "assert_session",
        ),
        popup=True,
    ),
    AuthScenario(
        id="oauth_wrong_origin_postmessage",
        family="oauth",
        expected_outcome="wrong_origin",
        description="Reject OAuth postMessage whose origin is not the local IdP.",
        steps=("open_login", "open_popup", "postmessage_wrong_origin", "assert_rejected"),
        popup=True,
        fixture_only=True,
    ),
    AuthScenario(
        id="auth_timeout",
        family="timeout",
        expected_outcome="timeout",
        description="Bound auth wait on local RP until synthetic timeout fires.",
        steps=("open_login", "wait_for_auth", "assert_timeout"),
        timeout_ms=250,
        fixture_only=True,
    ),
    AuthScenario(
        id="passkey_register_assert",
        family="passkey",
        expected_outcome="success",
        description="Register and assert a synthetic CDP virtual authenticator credential.",
        steps=("enable_webauthn", "create_credential", "get_assertion", "assert_success"),
    ),
    AuthScenario(
        id="passkey_prompt_recovery",
        family="passkey",
        expected_outcome="success",
        description="Abort two synthetic presence prompts then recover with a successful assertion.",
        steps=(
            "enable_webauthn",
            "create_credential",
            "abort_prompt",
            "abort_prompt",
            "restore_presence",
            "get_assertion",
            "assert_success",
        ),
    ),
    AuthScenario(
        id="passkey_uv_required",
        family="passkey",
        expected_outcome="user_verification_required",
        description="Fail closed when synthetic authenticator lacks user verification.",
        steps=("enable_webauthn", "create_credential", "clear_uv", "assert_uv_required"),
    ),
    AuthScenario(
        id="passkey_no_authenticator",
        family="passkey",
        expected_outcome="no_authenticator",
        description="Fail closed when the synthetic virtual authenticator is removed.",
        steps=("enable_webauthn", "create_credential", "remove_authenticator", "assert_missing"),
    ),
    AuthScenario(
        id="passkey_revoked_credential",
        family="passkey",
        expected_outcome="credential_revoked",
        description="Fail closed when a seeded synthetic credential is revoked.",
        steps=("enable_webauthn", "seed_credential", "revoke_credential", "assert_revoked"),
    ),
    AuthScenario(
        id="passkey_conditional_mediation",
        family="passkey",
        expected_outcome="success",
        description="Assert via synthetic conditional mediation on the local RP.",
        steps=("enable_webauthn", "create_credential", "conditional_get", "assert_success"),
    ),
    AuthScenario(
        id="oauth_passkey_2fa_success",
        family="oauth_passkey",
        expected_outcome="success",
        description=(
            "Local OAuth popup approve with RP callback, then synthetic CDP passkey 2FA."
        ),
        steps=(
            "open_login",
            "open_popup",
            "approve",
            "receive_callback",
            "enable_webauthn",
            "create_credential",
            "get_assertion",
            "assert_success",
        ),
        popup=True,
    ),
    AuthScenario(
        id="oauth_passkey_2fa_popup_reopen",
        family="oauth_passkey",
        expected_outcome="success",
        description=(
            "OAuth popup close/reopen with RP callback success, then synthetic CDP passkey 2FA."
        ),
        steps=(
            "open_login",
            "open_popup",
            "close_popup",
            "reopen_popup",
            "approve",
            "receive_callback",
            "enable_webauthn",
            "create_credential",
            "get_assertion",
            "assert_success",
        ),
        popup=True,
    ),
)

_BY_ID: Final[Mapping[str, AuthScenario]] = {s.id: s for s in SCENARIO_MATRIX}


def scenario_ids() -> tuple[str, ...]:
    return tuple(s.id for s in SCENARIO_MATRIX)


def live_scenario_ids() -> tuple[str, ...]:
    """Canonical live Chromium matrix (excludes fixture-only cases)."""
    return tuple(s.id for s in SCENARIO_MATRIX if not s.fixture_only)


def fixture_only_scenario_ids() -> tuple[str, ...]:
    return tuple(s.id for s in SCENARIO_MATRIX if s.fixture_only)


def expected_outcomes(*, live_only: bool = False) -> dict[str, str]:
    """Canonical expected-outcome map; optional live-only filter for the runner."""
    return {
        s.id: s.expected_outcome
        for s in SCENARIO_MATRIX
        if not live_only or not s.fixture_only
    }


def get_scenario(scenario_id: str) -> AuthScenario:
    try:
        return _BY_ID[scenario_id]
    except KeyError as exc:
        raise KeyError(f"unknown auth scenario: {scenario_id!r}") from exc


def scenarios_by_family() -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    for scenario in SCENARIO_MATRIX:
        groups.setdefault(scenario.family, []).append(scenario.id)
    return {family: tuple(ids) for family, ids in groups.items()}


def validate_scenario_matrix() -> bool:
    ids = scenario_ids()
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids in matrix")
    if not ids:
        raise ValueError("scenario matrix is empty")
    live = live_scenario_ids()
    if len(live) != 16:
        raise ValueError(f"live matrix must contain exactly 16 scenarios, got {len(live)}")
    for scenario in SCENARIO_MATRIX:
        if scenario.allows_external_network:
            raise ValueError(f"{scenario.id}: external network is forbidden")
        if scenario.requires_real_google or scenario.requires_real_credentials:
            raise ValueError(f"{scenario.id}: real providers/credentials are forbidden")
        if scenario.requires_passkey:
            raise ValueError(
                f"{scenario.id}: real passkeys are forbidden; use synthetic CDP only"
            )
        if scenario.log_secrets:
            raise ValueError(f"{scenario.id}: log_secrets must remain False")
        if scenario.timeout_ms < 0 or scenario.timeout_ms > MAX_SCENARIO_TIMEOUT_MS:
            raise ValueError(f"{scenario.id}: timeout_ms out of bounds")
        if not scenario.steps:
            raise ValueError(f"{scenario.id}: steps required")
        blob = f"{scenario.description} {' '.join(scenario.steps)}".lower()
        if "accounts.google.com" in blob or "googleapis.com" in blob:
            raise ValueError(f"{scenario.id}: real Google hosts forbidden in matrix text")
    return True


def sanitize_log_message(message: str) -> str:
    """Redact password/OTP/token/cookie material from log lines."""
    cleaned = message
    for pattern, repl in _REDACT_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)
    # Scrub bare high-entropy bearer-looking blobs if labeled earlier failed.
    cleaned = re.sub(
        r"(?i)\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        "[REDACTED]",
        cleaned,
    )
    return cleaned


__all__ = [
    "MAX_SCENARIO_TIMEOUT_MS",
    "SCENARIO_MATRIX",
    "AuthScenario",
    "expected_outcomes",
    "fixture_only_scenario_ids",
    "get_scenario",
    "live_scenario_ids",
    "sanitize_log_message",
    "scenario_ids",
    "scenarios_by_family",
    "validate_scenario_matrix",
]
