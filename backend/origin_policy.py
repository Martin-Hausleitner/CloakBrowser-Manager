"""Deterministic HTTP(S) origin normalization for run navigation policy."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection, Iterable
from urllib.parse import urlparse

import idna

_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}

# RFC 1035: wire form is at most 255 octets including labels lengths + root;
# the textual name without a trailing dot is therefore at most 253 characters.
_MAX_DNS_NAME_LENGTH = 253
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DOTTED_NUMERIC_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){3}$")
_HEX_COMPONENT_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")


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


def _looks_like_dotted_numeric(host: str) -> bool:
    """True when host is exactly four dot-separated all-digit components."""
    return _DOTTED_NUMERIC_RE.fullmatch(host) is not None


def _looks_like_legacy_ipv4_number(host: str) -> bool:
    """True for WHATWG-style 1..4 component numeric/hex IPv4-number hosts."""
    parts = host.split(".")
    if not (1 <= len(parts) <= 4):
        return False
    for part in parts:
        if not part:
            return False
        if part.isdigit():
            continue
        if _HEX_COMPONENT_RE.fullmatch(part) is not None:
            continue
        return False
    return True


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

    # Ambiguous dotted-quad lookalikes that ipaddress rejected must not fall
    # through as DNS names (e.g. octal-looking 010.000.000.001, out-of-range).
    if _looks_like_dotted_numeric(host):
        raise ValueError("origin host is ambiguous")

    # Reject WHATWG legacy IPv4-number forms browsers may reinterpret as IPs.
    if _looks_like_legacy_ipv4_number(host):
        raise ValueError("origin host is ambiguous")

    try:
        idna_host = idna.encode(host, uts46=True, std3_rules=True).decode("ascii").lower()
    except idna.IDNAError as exc:
        raise ValueError("origin host is invalid") from exc
    if not idna_host or idna_host.startswith(".") or idna_host.endswith("."):
        raise ValueError("origin host is invalid")
    if _looks_like_dotted_numeric(idna_host):
        raise ValueError("origin host is ambiguous")
    if _looks_like_legacy_ipv4_number(idna_host):
        raise ValueError("origin host is ambiguous")
    if len(idna_host) > _MAX_DNS_NAME_LENGTH:
        raise ValueError("origin host is invalid")

    labels = idna_host.split(".")
    if any(_DNS_LABEL_RE.fullmatch(label) is None for label in labels):
        raise ValueError("origin host is invalid")
    return idna_host
