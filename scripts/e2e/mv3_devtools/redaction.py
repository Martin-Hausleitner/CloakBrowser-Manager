"""Redaction helpers for MV3 DevTools E2E artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SECRET_KEY_RE = re.compile(
    r"(?i)^(authorization|cookie|cookies|password|passwd|token|otp|totp|passkey|cvc|cvv|"
    r"raw_value|secret|api[_-]?key|sessiontoken|control_token)$"
)
SECRET_TEXT_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|bearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"(?:password|token|secret|cookie|otp|totp|api[_-]?key)\s*[:=]\s*\S+)"
)
HOME_RE = re.compile(re.escape(str(Path.home())))


def redact_home(value: str) -> str:
    return HOME_RE.sub("~", value)


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except Exception:
        return "[redacted-url]"
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return value


def redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[truncated-depth]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.match(str(key)):
                out[str(key)] = "[redacted]"
            else:
                out[str(key)] = redact_value(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_value(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, str):
        text = redact_home(value)
        if SECRET_TEXT_RE.search(text):
            return SECRET_TEXT_RE.sub("[redacted]", text)
        if text.startswith(("http://", "https://", "ws://", "wss://")):
            return redact_url(text)
        return text
    return value


def artifact_is_safe(payload: Any) -> bool:
    encoded = str(redact_value(payload)).lower()
    banned = ("bearer ", "password=", "cookie:", "raw_value", "authorization: bearer")
    return not any(marker in encoded for marker in banned)
