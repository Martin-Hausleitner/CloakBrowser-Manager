"""Privacy controls for the local trace pipeline.

Default: content_capture=false. Never persist prompts, responses, cookies,
secrets, or raw browser content.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# Attribute / field names that must never be stored.
FORBIDDEN_KEYS = frozenset(
    {
        "prompt",
        "prompts",
        "response",
        "responses",
        "completion",
        "completions",
        "message",
        "messages",
        "content",
        "input",
        "output",
        "cookie",
        "cookies",
        "set_cookie",
        "set-cookie",
        "authorization",
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "api_key",
        "apikey",
        "api-key",
        "token",
        "access_token",
        "refresh_token",
        "bearer",
        "credential",
        "credentials",
        "private_key",
        "seed_phrase",
        "totp",
        "otp",
        "raw_value",
        "body",
        "html",
        "screenshot",
        "page_content",
        "system_prompt",
        "user_prompt",
        "assistant_message",
    }
)

# Substrings that mark a key as sensitive even if not exact-match.
# Note: bare "token" is intentionally omitted so gen_ai.usage.*_tokens metrics
# remain allowed; auth tokens are covered by exact keys and secret-like values.
_FORBIDDEN_KEY_SUBSTR = (
    "password",
    "secret",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "prompt",
    "response",
    "credential",
    "private_key",
    "access_token",
    "refresh_token",
    "bearer_token",
)

_AUTH_BEARER_RE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|cookie|api[_-]?key)\s*[:=]\s*\S+"
)
_PROXY_CREDENTIAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@")

# Safe allowlist for span attributes (correlation + operational metrics).
SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.environment",
        "cbm.run_id",
        "cbm.profile_id",
        "cbm.session_id",
        "cbm.harness",
        "cbm.worker_id",
        "cbm.lease_id",
        "cbm.task_id",
        "cbm.acp_session_id",
        "cbm.acpx_run_id",
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.operation.name",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read_input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
        "http.method",
        "http.status_code",
        "http.route",
        "error.type",
        "code.function",
        "code.namespace",
        "peer.service",
    }
)

DEFAULT_CONTENT_CAPTURE = False


class PrivacyError(ValueError):
    """Raised when a record would capture forbidden content."""


def is_forbidden_key(key: str) -> bool:
    raw = key.strip().lower()
    normalized = raw.replace("-", "_")
    if normalized in FORBIDDEN_KEYS or raw in FORBIDDEN_KEYS:
        return True
    # Metric counters such as input_tokens / gen_ai.usage.output_tokens are safe.
    if normalized.endswith("_tokens") or normalized.endswith(".tokens"):
        return False
    if raw in SAFE_ATTRIBUTE_KEYS or key.strip() in SAFE_ATTRIBUTE_KEYS:
        return False
    return any(part in raw for part in _FORBIDDEN_KEY_SUBSTR)


def looks_secret_like(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    if _AUTH_BEARER_RE.search(value):
        return True
    if _SENSITIVE_ASSIGNMENT_RE.search(value):
        return True
    if _PROXY_CREDENTIAL_RE.search(value.strip()):
        return True
    return False


def sanitize_attributes(
    attributes: Mapping[str, Any] | None,
    *,
    content_capture: bool = DEFAULT_CONTENT_CAPTURE,
    strict: bool = True,
) -> dict[str, Any]:
    """Return a redacted attribute map.

    When content_capture is False (default), only SAFE_ATTRIBUTE_KEYS are kept
    unless the caller passes an empty map. Forbidden keys always raise or drop.
    """
    if not attributes:
        return {}

    if content_capture:
        # Even with content_capture=true, absolute forbids remain blocked.
        out: dict[str, Any] = {}
        for key, value in attributes.items():
            key_s = str(key)
            if is_forbidden_key(key_s):
                if strict:
                    raise PrivacyError(f"forbidden attribute key: {key_s}")
                continue
            if isinstance(value, str) and looks_secret_like(value):
                if strict:
                    raise PrivacyError(f"secret-like value for attribute: {key_s}")
                continue
            out[key_s] = _coerce_attr_value(value)
        return out

    out = {}
    for key, value in attributes.items():
        key_s = str(key)
        if is_forbidden_key(key_s):
            if strict:
                raise PrivacyError(f"forbidden attribute key with content_capture=false: {key_s}")
            continue
        if key_s not in SAFE_ATTRIBUTE_KEYS:
            # Drop unknown keys rather than fail — keeps importers resilient.
            continue
        if isinstance(value, str) and looks_secret_like(value):
            if strict:
                raise PrivacyError(f"secret-like value for attribute: {key_s}")
            continue
        out[key_s] = _coerce_attr_value(value)
    return out


def _coerce_attr_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 512:
            return value[:512] + "…"
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_attr_value(v) for v in value[:32]]
    # Nested objects become string markers, never raw dumps of content.
    return str(type(value).__name__)


def assert_no_content_payload(payload: Mapping[str, Any], *, path: str = "") -> None:
    """Walk a mapping and raise if forbidden content keys appear."""
    for key, value in payload.items():
        key_s = str(key)
        full = f"{path}.{key_s}" if path else key_s
        if is_forbidden_key(key_s):
            raise PrivacyError(f"forbidden content key present: {full}")
        if isinstance(value, Mapping):
            assert_no_content_payload(value, path=full)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, Mapping):
                    assert_no_content_payload(item, path=f"{full}[{i}]")
