from __future__ import annotations

import pytest

from benchmarks.auth.policy import (
    AuthBenchmarkPolicyError,
    assert_artifact_is_safe,
    validate_benchmark_origins,
)


@pytest.mark.parametrize(
    "origins",
    [
        [],
        ["https://accounts.google.com"],
        ["https://login.microsoftonline.com"],
        ["https://github.com/login/oauth"],
        ["http://example.com"],
        ["http://127.0.0.1:9010", "https://accounts.google.com"],
    ],
)
def test_auth_benchmark_rejects_empty_external_or_mixed_origins(origins: list[str]) -> None:
    with pytest.raises(AuthBenchmarkPolicyError):
        validate_benchmark_origins(origins)


def test_auth_benchmark_accepts_exact_loopback_origins() -> None:
    assert validate_benchmark_origins(
        ["http://127.0.0.1:19010", "http://localhost:19011"]
    ) == ("http://127.0.0.1:19010", "http://localhost:19011")


@pytest.mark.parametrize(
    "artifact",
    [
        "Authorization: Bearer synthetic-but-forbidden",
        "password=benchmark-value",
        "otp: 123456",
        "https://user:password@example.test",
        "cookie=session-value",
        "clientDataJSON=forbidden",
        "privateKey=forbidden",
    ],
)
def test_auth_benchmark_artifact_gate_rejects_auth_material(artifact: str) -> None:
    with pytest.raises(AuthBenchmarkPolicyError):
        assert_artifact_is_safe(artifact)


def test_auth_benchmark_artifact_gate_accepts_redacted_metrics() -> None:
    assert_artifact_is_safe(
        '{"scenario_id":"passkey-assert","outcome":"success","duration_ms":42}'
    )


def test_auth_benchmark_artifact_gate_accepts_explicit_redaction_markers() -> None:
    assert_artifact_is_safe("login attempt password=[REDACTED] otp=[REDACTED]")
