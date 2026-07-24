"""Deterministic HTTP(S) origin normalization for run navigation policy."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Iterable
from urllib.parse import urlparse

_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}


def normalize_origin(value: str) -> str:
    """Normalize one explicit HTTP(S) origin or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError("origin must be a string")
    if value.strip() != value:
        raise ValueError("origin has surrounding whitespace")
    raw = value
    if not raw:
        raise ValueError("origin is empty")
    if any(ch.isspace() for ch in raw):
        raise ValueError("origin contains whitespace")
    if "*" in raw:
        raise ValueError("wildcard origins are not allowed")

    try:
        parts = urlparse(raw)
    except ValueError as exc:
        raise ValueError("origin is malformed") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError("origin scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("origin must not include credentials")
    if parts.path:
        raise ValueError("origin must not include a path")
    if parts.params:
        raise ValueError("origin must not include params")
    if parts.query:
        raise ValueError("origin must not include a query")
    if parts.fragment:
        raise ValueError("origin must not include a fragment")

    host = parts.hostname
    if host is None or host == "":
        raise ValueError("origin host is required")
    if host.startswith(".") or "*" in host:
        raise ValueError("suffix or wildcard hosts are not allowed")
    if host.endswith("."):
        raise ValueError("origin host is ambiguous")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("origin port is invalid") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError("origin port is invalid")

    normalized_host = _normalize_host(host)
    default_port = _DEFAULT_PORTS[scheme]
    if port is None or port == default_port:
        authority = normalized_host
    else:
        authority = f"{normalized_host}:{port}"
    return f"{scheme}://{authority}"


def normalize_origin_set(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize origins and return a sorted unique tuple."""
    normalized = {normalize_origin(value) for value in values}
    return tuple(sorted(normalized))


def is_top_level_origin_allowed(candidate: str, allowed_origins: Collection[str]) -> bool:
    """Return True only for exact normalized origin membership (never suffix)."""
    try:
        normalized_candidate = normalize_origin(candidate)
        allowed_normalized = set(normalize_origin_set(allowed_origins))
    except ValueError:
        return False
    return normalized_candidate in allowed_normalized


def _normalize_host(host: str) -> str:
    # Exact IP literals normalize because listing them is explicit.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.version == 6:
            return f"[{address.compressed}]"
        return str(address)

    try:
        idna_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("origin host is invalid") from exc
    if not idna_host or idna_host.startswith(".") or idna_host.endswith("."):
        raise ValueError("origin host is invalid")
    return idna_host
