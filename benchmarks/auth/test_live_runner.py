from __future__ import annotations

import os
import stat

import pytest

from benchmarks.auth import scenarios as sc
from benchmarks.auth.live_runner import (
    DEFAULT_SCENARIOS,
    default_chromium_binary,
    run_live_benchmark,
    write_report,
)


def test_live_runner_default_scenarios_match_catalog_live_ids() -> None:
    assert tuple(DEFAULT_SCENARIOS) == sc.live_scenario_ids()
    assert len(DEFAULT_SCENARIOS) == 16
    assert "oauth_wrong_origin_postmessage" not in DEFAULT_SCENARIOS
    assert "auth_timeout" not in DEFAULT_SCENARIOS


def test_live_runner_expected_outcomes_derive_from_catalog() -> None:
    """Canonical outcomes come from scenarios.expected_outcomes(live_only=True)."""
    catalog = sc.expected_outcomes(live_only=True)
    assert catalog["oauth_popup_reopen"] == "success"
    assert catalog["oauth_popup_success"] == "success"
    assert catalog["passkey_uv_required"] == "user_verification_required"
    # Import the same mapping the runner feeds into build_report.
    from benchmarks.auth import live_runner as lr

    assert lr.catalog_expected_outcomes() == catalog


@pytest.mark.asyncio
async def test_live_password_and_oauth_popup_benchmark_runs_in_parallel() -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    report = await run_live_benchmark(
        chromium_binary=chromium,
        scenario_ids=("password_success", "oauth_popup_reopen"),
        max_parallel=2,
    )
    assert report.passed is True, report.failures
    by_id = {item.scenario_id: item for item in report.results}
    assert by_id["password_success"].outcome == "success"
    assert by_id["oauth_popup_reopen"].outcome == "success"
    assert by_id["oauth_popup_reopen"].popup_count == 2
    assert by_id["oauth_popup_reopen"].popup_reopen_count == 1
    assert len({item.profile_isolation_id for item in report.results}) == 2


@pytest.mark.asyncio
async def test_live_oauth_success_verifies_rp_callback_not_click_alone() -> None:
    """OAuth success must reflect local RP callback outcome after IdP approval."""
    chromium = default_chromium_binary()
    assert chromium is not None
    report = await run_live_benchmark(
        chromium_binary=chromium,
        scenario_ids=("oauth_popup_success",),
        max_parallel=1,
    )
    assert report.passed is True, report.failures
    result = report.results[0]
    assert result.scenario_id == "oauth_popup_success"
    assert result.outcome == "success"
    assert result.popup_count == 1
    assert result.popup_reopen_count == 0


@pytest.mark.asyncio
async def test_live_passkey_prompt_recovery_and_failure_states() -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    report = await run_live_benchmark(
        chromium_binary=chromium,
        scenario_ids=(
            "passkey_register_assert",
            "passkey_prompt_recovery",
            "passkey_uv_required",
            "passkey_no_authenticator",
        ),
        max_parallel=2,
    )
    assert report.passed is True, report.failures
    by_id = {item.scenario_id: item for item in report.results}
    assert by_id["passkey_register_assert"].outcome == "success"
    assert by_id["passkey_prompt_recovery"].outcome == "success"
    assert by_id["passkey_prompt_recovery"].prompt_count == 3
    assert by_id["passkey_uv_required"].outcome == "user_verification_required"
    assert by_id["passkey_no_authenticator"].outcome == "no_authenticator"


@pytest.mark.asyncio
async def test_live_google_style_oauth_plus_passkey_2fa_recovery_chain() -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    report = await run_live_benchmark(
        chromium_binary=chromium,
        scenario_ids=(
            "oauth_passkey_2fa_success",
            "oauth_passkey_2fa_popup_reopen",
            "passkey_conditional_mediation",
        ),
        max_parallel=3,
    )
    assert report.passed is True, report.failures
    by_id = {item.scenario_id: item for item in report.results}
    assert by_id["oauth_passkey_2fa_success"].outcome == "success"
    assert by_id["oauth_passkey_2fa_success"].popup_count == 1
    assert by_id["oauth_passkey_2fa_success"].prompt_count == 1
    assert by_id["oauth_passkey_2fa_popup_reopen"].outcome == "success"
    assert by_id["oauth_passkey_2fa_popup_reopen"].popup_count == 2
    assert by_id["oauth_passkey_2fa_popup_reopen"].popup_reopen_count == 1
    assert by_id["oauth_passkey_2fa_popup_reopen"].prompt_count == 1
    assert by_id["passkey_conditional_mediation"].outcome == "success"


@pytest.mark.asyncio
async def test_live_runner_rejects_real_google_origin() -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    with pytest.raises(ValueError, match="unknown auth benchmark scenario"):
        await run_live_benchmark(
            chromium_binary=chromium,
            scenario_ids=("https://accounts.google.com",),
            max_parallel=1,
        )


@pytest.mark.asyncio
async def test_live_runner_rejects_fixture_only_scenarios() -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    with pytest.raises(ValueError, match="unknown auth benchmark scenario"):
        await run_live_benchmark(
            chromium_binary=chromium,
            scenario_ids=("oauth_wrong_origin_postmessage",),
            max_parallel=1,
        )


@pytest.mark.asyncio
async def test_live_report_is_private_and_contains_metrics_only(tmp_path) -> None:
    chromium = default_chromium_binary()
    assert chromium is not None
    report = await run_live_benchmark(
        chromium_binary=chromium,
        scenario_ids=("password_failure",),
        max_parallel=1,
    )
    path = tmp_path / "auth-benchmark.json"
    write_report(path, report)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    assert "password=" not in text.lower()
    assert "authorization" not in text.lower()
    assert "cookie" not in text.lower()
    assert os.path.expanduser("~") not in text
