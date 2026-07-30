from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthBenchmarkResult:
    scenario_id: str
    outcome: str
    duration_ms: int
    prompt_count: int = 0
    popup_count: int = 0
    popup_reopen_count: int = 0
    sensitive_artifact_hits: tuple[str, ...] = ()
    profile_isolation_id: str = ""


@dataclass(frozen=True, slots=True)
class AuthBenchmarkReport:
    passed: bool
    p95_duration_ms: int
    failures: tuple[str, ...]
    results: tuple[AuthBenchmarkResult, ...]


def _nearest_rank_p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def build_report(
    results: list[AuthBenchmarkResult],
    *,
    max_prompt_count: int,
    p95_budget_ms: int,
    require_unique_profiles: bool = False,
    expected_outcomes: dict[str, str] | None = None,
) -> AuthBenchmarkReport:
    failures: list[str] = []
    seen_profiles: dict[str, str] = {}

    for result in results:
        expected = (expected_outcomes or {}).get(result.scenario_id)
        if expected is not None and result.outcome != expected:
            failures.append(
                f"{result.scenario_id}: outcome {result.outcome} does not match {expected}"
            )
        if result.prompt_count > max_prompt_count:
            failures.append(
                f"{result.scenario_id}: prompt_count {result.prompt_count} exceeds {max_prompt_count}"
            )
        if result.popup_reopen_count > 1:
            failures.append(
                f"{result.scenario_id}: popup_reopen_count {result.popup_reopen_count} exceeds 1"
            )
        if result.sensitive_artifact_hits:
            failures.append(f"{result.scenario_id}: sensitive artifact gate failed")
        if require_unique_profiles and result.profile_isolation_id:
            previous = seen_profiles.get(result.profile_isolation_id)
            if previous is not None:
                failures.append(
                    f"{result.scenario_id}: profile isolation id reused by {previous}"
                )
            else:
                seen_profiles[result.profile_isolation_id] = result.scenario_id

    p95 = _nearest_rank_p95([result.duration_ms for result in results])
    if p95 > p95_budget_ms:
        failures.append(f"suite: p95 duration {p95}ms exceeds {p95_budget_ms}ms")

    return AuthBenchmarkReport(
        passed=not failures,
        p95_duration_ms=p95,
        failures=tuple(failures),
        results=tuple(results),
    )
