from __future__ import annotations

from benchmarks.auth.report import AuthBenchmarkResult, build_report


def result(
    scenario_id: str,
    *,
    outcome: str = "success",
    duration_ms: int = 100,
    prompt_count: int = 1,
    popup_count: int = 0,
    popup_reopen_count: int = 0,
    sensitive_artifact_hits: tuple[str, ...] = (),
) -> AuthBenchmarkResult:
    return AuthBenchmarkResult(
        scenario_id=scenario_id,
        outcome=outcome,
        duration_ms=duration_ms,
        prompt_count=prompt_count,
        popup_count=popup_count,
        popup_reopen_count=popup_reopen_count,
        sensitive_artifact_hits=sensitive_artifact_hits,
        profile_isolation_id="profile-redacted-1",
    )


def test_report_flags_unbounded_reappearing_passkey_prompts() -> None:
    report = build_report(
        [result("passkey-repeat", prompt_count=4)],
        max_prompt_count=3,
        p95_budget_ms=5_000,
    )
    assert report.passed is False
    assert report.failures == ("passkey-repeat: prompt_count 4 exceeds 3",)


def test_report_flags_popup_reopen_loop_and_sensitive_artifact() -> None:
    report = build_report(
        [
            result(
                "oauth-reopen",
                popup_count=3,
                popup_reopen_count=2,
                sensitive_artifact_hits=("forbidden-auth-material",),
            )
        ],
        max_prompt_count=3,
        p95_budget_ms=5_000,
    )
    assert report.passed is False
    assert report.failures == (
        "oauth-reopen: popup_reopen_count 2 exceeds 1",
        "oauth-reopen: sensitive artifact gate failed",
    )


def test_report_computes_p95_and_passes_bounded_scenarios() -> None:
    report = build_report(
        [
            result("password", duration_ms=100, prompt_count=0),
            result("passkey", duration_ms=250),
            result("oauth", duration_ms=400, popup_count=1),
        ],
        max_prompt_count=3,
        p95_budget_ms=1_000,
    )
    assert report.passed is True
    assert report.p95_duration_ms == 400
    assert report.failures == ()


def test_report_requires_unique_profile_isolation_for_parallel_results() -> None:
    first = result("parallel-a")
    second = result("parallel-b")
    report = build_report(
        [first, second],
        max_prompt_count=3,
        p95_budget_ms=5_000,
        require_unique_profiles=True,
    )
    assert report.passed is False
    assert report.failures == (
        "parallel-b: profile isolation id reused by parallel-a",
    )


def test_report_fails_when_scenario_outcome_does_not_match_contract() -> None:
    report = build_report(
        [result("passkey-register", outcome="SecurityError")],
        max_prompt_count=3,
        p95_budget_ms=5_000,
        expected_outcomes={"passkey-register": "success"},
    )
    assert report.passed is False
    assert report.failures == (
        "passkey-register: outcome SecurityError does not match success",
    )
