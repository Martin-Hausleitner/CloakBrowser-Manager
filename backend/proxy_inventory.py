"""Proxy pool inventory helpers: parse, redact, and geo-aligned profile defaults.

Credentials stay in SQLite only. API responses never include passwords or full
proxy URLs. Optional proxychecker enrichment is best-effort and fail-soft.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from backend.browser_manager import _normalize_proxy, _validate_proxy
from backend.profile_health import (
    NormalizedProxyCheckerResult,
    derive_authenticity_score,
    normalize_proxychecker_response,
)

_HOST_PORT_USER_PASS = re.compile(
    r"^(?P<host>[^:\s]+):(?P<port>\d{2,5}):(?P<user>[^:\s]+):(?P<password>.+)$"
)

# Conservative country → (timezone, locale) map for auto anti-stealth defaults.
_COUNTRY_DEFAULTS: dict[str, tuple[str, str]] = {
    "AT": ("Europe/Vienna", "de-AT"),
    "DE": ("Europe/Berlin", "de-DE"),
    "CH": ("Europe/Zurich", "de-CH"),
    "NL": ("Europe/Amsterdam", "nl-NL"),
    "BE": ("Europe/Brussels", "nl-BE"),
    "FR": ("Europe/Paris", "fr-FR"),
    "GB": ("Europe/London", "en-GB"),
    "IE": ("Europe/Dublin", "en-IE"),
    "US": ("America/New_York", "en-US"),
    "CA": ("America/Toronto", "en-CA"),
    "PL": ("Europe/Warsaw", "pl-PL"),
    "CZ": ("Europe/Prague", "cs-CZ"),
    "ES": ("Europe/Madrid", "es-ES"),
    "IT": ("Europe/Rome", "it-IT"),
    "SE": ("Europe/Stockholm", "sv-SE"),
    "NO": ("Europe/Oslo", "nb-NO"),
    "DK": ("Europe/Copenhagen", "da-DK"),
    "FI": ("Europe/Helsinki", "fi-FI"),
}

_DEFAULT_TZ_LOCALE = ("Europe/Berlin", "de-DE")


class ProxyParseError(ValueError):
    """Raised when a proxy inventory line cannot be parsed safely."""


def parse_proxy_line(raw: str) -> str:
    """Parse ``host:port:user:pass`` (or URL) into a normalized proxy URL."""
    text = (raw or "").strip()
    if not text or text.startswith("#"):
        raise ProxyParseError("empty proxy line")

    match = _HOST_PORT_USER_PASS.match(text)
    if match:
        host = match.group("host")
        port = match.group("port")
        user = match.group("user")
        password = match.group("password")
        if not host or host.startswith("/") or " " in host:
            raise ProxyParseError("invalid proxy host")
        try:
            port_i = int(port)
        except ValueError as exc:
            raise ProxyParseError("invalid proxy port") from exc
        if port_i < 1 or port_i > 65535:
            raise ProxyParseError("invalid proxy port")
        normalized = (
            f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port_i}"
        )
    else:
        normalized = _normalize_proxy(text)

    try:
        _validate_proxy(normalized)
    except ValueError as exc:
        raise ProxyParseError("invalid proxy URL") from exc
    return normalized


def proxy_fingerprint(proxy_url: str) -> str:
    """Stable non-reversible id for dedupe without storing a second secret."""
    return hashlib.sha256(proxy_url.encode("utf-8")).hexdigest()


def redact_proxy_url(proxy_url: str) -> dict[str, Any]:
    """Return overview fields without credentials."""
    parsed = urlparse(proxy_url)
    host = parsed.hostname or ""
    port = parsed.port
    username = parsed.username or ""
    host_masked = _mask_host(host)
    user_masked = _mask_username(username)
    return {
        "host_masked": host_masked,
        "port": port,
        "username_masked": user_masked,
        "has_credentials": bool(username),
        "label": f"{host_masked}:{port}" if port else host_masked,
    }


def _mask_host(host: str) -> str:
    if not host:
        return "unknown"
    if ":" in host and "." not in host:
        # IPv6 — keep first hextet only
        return host.split(":", 1)[0] + ":****"
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.x.x"
    if len(parts) >= 2:
        return f"*.{parts[-2]}.{parts[-1]}"
    return "***"


def _mask_username(username: str) -> str | None:
    if not username:
        return None
    if len(username) <= 2:
        return "*" * len(username)
    return f"{username[0]}***{username[-1]}"


def country_defaults(country_code: str | None) -> tuple[str, str]:
    if not country_code:
        return _DEFAULT_TZ_LOCALE
    code = country_code.strip().upper()
    return _COUNTRY_DEFAULTS.get(code, _DEFAULT_TZ_LOCALE)


def extract_country_code(payload: object) -> str | None:
    """Best-effort country extraction from a proxychecker payload."""
    if not isinstance(payload, Mapping):
        return None
    candidates: list[object] = []
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], Mapping):
        primary = results[0]
        candidates.extend(
            [
                primary.get("country"),
                primary.get("country_code"),
                primary.get("countryCode"),
            ]
        )
        for key in ("enrichment", "geo", "ip_api", "features", "providers"):
            nested = primary.get(key)
            if isinstance(nested, Mapping):
                candidates.extend(
                    [
                        nested.get("country"),
                        nested.get("country_code"),
                        nested.get("countryCode"),
                        nested.get("country_code2"),
                    ]
                )
                for nested_value in nested.values():
                    if isinstance(nested_value, Mapping):
                        candidates.extend(
                            [
                                nested_value.get("country"),
                                nested_value.get("country_code"),
                                nested_value.get("countryCode"),
                            ]
                        )
    features = payload.get("features")
    if isinstance(features, Mapping):
        candidates.extend(
            [
                features.get("country"),
                features.get("country_code"),
                features.get("countryCode"),
            ]
        )
    for value in candidates:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{2}", value.strip()):
            return value.strip().upper()
    return None


def build_auto_profile_defaults(
    *,
    proxy_url: str,
    country_code: str | None = None,
    name: str | None = None,
    project_id: str = "proxied",
    harness: str = "browser-use",
    sandbox_id: str = "default",
) -> dict[str, Any]:
    """Profile fields for automatic proxy-aligned anti-stealth creation."""
    timezone, locale = country_defaults(country_code)
    redacted = redact_proxy_url(proxy_url)
    label = redacted["label"] or "proxy"
    seed_material = f"{proxy_fingerprint(proxy_url)}:{timezone}:{locale}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16) % 2_147_483_647
    return {
        "name": name or f"Auto {label}",
        "sandbox_id": sandbox_id,
        "project_id": project_id,
        "folder_path": "auto",
        "pinned": False,
        "accent_color": "#10b981",
        "harness": harness,
        "fingerprint_seed": seed,
        "proxy": proxy_url,
        "timezone": timezone,
        "locale": locale,
        "platform": "windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 8,
        "gpu_vendor": "Google Inc. (NVIDIA)",
        "gpu_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "geoip": True,
        "humanize": True,
        "human_preset": "default",
        "clipboard_sync": True,
        "auto_launch": False,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "notes": "Auto-created from proxy inventory with geo-aligned stealth defaults.",
        "tags": [
            {"tag": "auto", "color": "#10b981"},
            {"tag": "proxied", "color": "#6366f1"},
        ],
    }


def summarize_check_payload(payload: object) -> dict[str, Any]:
    """Reduce a proxychecker response to redacted inventory fields."""
    normalized: NormalizedProxyCheckerResult = normalize_proxychecker_response(payload)
    country = extract_country_code(payload)
    timezone, locale = country_defaults(country)
    latency = normalized.latency_ms
    if latency is not None and (not math.isfinite(latency) or latency < 0):
        latency = None
    return {
        "reachable": normalized.reachable,
        "latency_ms": latency,
        "risk_score": normalized.risk_score,
        "authenticity_score": (
            normalized.authenticity_score
            if normalized.authenticity_score is not None
            else derive_authenticity_score(normalized.risk_score)
        ),
        "country_code": country,
        "timezone_hint": timezone,
        "locale_hint": locale,
        "warnings": list(normalized.warnings),
        "blockers": list(normalized.blockers),
        "check_state": _check_state(normalized),
    }


def _check_state(result: NormalizedProxyCheckerResult) -> str:
    if result.blockers:
        return "unavailable"
    if result.reachable is True:
        if result.risk_score is not None and result.risk_score >= 70:
            return "warning"
        return "passed"
    if result.reachable is False:
        return "failed"
    return "unavailable"
