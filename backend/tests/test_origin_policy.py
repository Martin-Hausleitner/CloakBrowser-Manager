"""Unit tests for deterministic HTTP(S) origin normalization."""

from __future__ import annotations

import pytest

from backend.origin_policy import (
    is_top_level_origin_allowed,
    normalize_origin,
    normalize_origin_set,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Example.COM", "https://example.com"),
        ("HTTPS://example.com:443", "https://example.com"),
        ("http://example.com:80", "http://example.com"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("http://example.com:8080", "http://example.com:8080"),
        ("https://bücher.de", "https://xn--bcher-kva.de"),
        ("https://münchen.example.com:443", "https://xn--mnchen-3ya.example.com"),
        ("https://192.0.2.10", "https://192.0.2.10"),
        ("http://192.0.2.10:8080", "http://192.0.2.10:8080"),
        ("https://[2001:db8::1]", "https://[2001:db8::1]"),
        ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
    ],
)
def test_normalize_origin_idna_ports_and_ip_literals(value: str, expected: str):
    assert normalize_origin(value) == expected


def test_normalize_origin_set_dedupes_deterministically():
    assert normalize_origin_set(
        [
            "https://Example.COM",
            "HTTPS://example.com:443",
            "https://bücher.de",
            "https://xn--bcher-kva.de",
            "https://other.example",
        ]
    ) == (
        "https://example.com",
        "https://other.example",
        "https://xn--bcher-kva.de",
    )


def test_exact_ip_literal_is_allowed_only_when_listed():
    allowed = normalize_origin_set(["https://192.0.2.10", "https://example.com"])
    assert is_top_level_origin_allowed("https://192.0.2.10", allowed) is True
    assert is_top_level_origin_allowed("https://192.0.2.11", allowed) is False
    # Never treat an IP as a suffix/prefix match against another host.
    assert is_top_level_origin_allowed("https://192.0.2.10.evil.example", allowed) is False
    assert is_top_level_origin_allowed("https://example.com.evil", allowed) is False


@pytest.mark.parametrize(
    "value",
    [
        "*.example.com",
        "https://*.example.com",
        ".example.com",
        "https://.example.com",
        "https://user@example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com/",
        "https://example.com?q=1",
        "https://example.com#frag",
        "javascript:alert(1)",
        "ftp://example.com",
        "file:///tmp",
        "//example.com",
        "example.com",
        "https://",
        "https:///path",
        "",
        "   ",
        "https://example.com:99999",
        "https://example.com:0",
        "https://example.com:notaport",
        "https://exa mple.com",
        "https://example.com.",
    ],
)
def test_rejects_ambiguous_or_invalid_origins(value: str):
    with pytest.raises(ValueError):
        normalize_origin(value)
