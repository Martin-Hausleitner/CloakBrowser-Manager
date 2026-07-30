from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


class AuthBenchmarkPolicyError(ValueError):
    """Raised when a benchmark could touch real authentication material."""


_FORBIDDEN_ARTIFACT_PATTERNS = (
    re.compile(r"authorization\s*:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b(?:password|passwd|pwd|cookie|otp|totp)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE),
    re.compile(r"\b(?:clientDataJSON|privateKey|authenticatorData|attestationObject)\s*[:=]", re.IGNORECASE),
)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_benchmark_origins(origins: list[str]) -> tuple[str, ...]:
    if not origins:
        raise AuthBenchmarkPolicyError("auth benchmark origins must be non-empty")

    validated: list[str] = []
    for value in origins:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not _is_loopback_host(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise AuthBenchmarkPolicyError("auth benchmark origins must be exact loopback origins")
        try:
            port = parsed.port
        except ValueError as exc:
            raise AuthBenchmarkPolicyError("auth benchmark origin port is invalid") from exc
        if port is None or not (1024 <= port <= 65535):
            raise AuthBenchmarkPolicyError("auth benchmark origins require an unprivileged port")
        validated.append(value.rstrip("/"))

    return tuple(validated)


def assert_artifact_is_safe(text: str) -> None:
    if not isinstance(text, str):
        raise AuthBenchmarkPolicyError("auth benchmark artifact must be text")
    scan_text = re.sub(
        r"(?i)\b(?:authorization\s*:\s*bearer|password|passwd|pwd|cookie|otp|totp)"
        r"\s*[:=]?\s*\[REDACTED\]",
        "[REDACTED_FIELD]",
        text,
    )
    if any(pattern.search(scan_text) for pattern in _FORBIDDEN_ARTIFACT_PATTERNS):
        raise AuthBenchmarkPolicyError("auth benchmark artifact contains forbidden authentication material")
