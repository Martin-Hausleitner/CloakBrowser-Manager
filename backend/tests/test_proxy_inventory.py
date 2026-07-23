"""Tests for proxy inventory parse/redact/geo defaults."""

from __future__ import annotations

import pytest

from backend.proxy_inventory import (
    ProxyParseError,
    build_auto_profile_defaults,
    extract_country_code,
    parse_proxy_line,
    proxy_fingerprint,
    redact_proxy_url,
    summarize_check_payload,
)


def test_parse_host_port_user_pass_line():
    url = parse_proxy_line("10.0.0.5:8080:alice:s3cret")
    assert url == "http://alice:s3cret@10.0.0.5:8080"
    assert "s3cret" in url  # stored form may keep password; API must redact


def test_parse_rejects_empty_and_invalid():
    with pytest.raises(ProxyParseError):
        parse_proxy_line("")
    with pytest.raises(ProxyParseError):
        parse_proxy_line("# comment")
    with pytest.raises(ProxyParseError):
        parse_proxy_line("not-a-proxy")


def test_redact_masks_host_and_username_without_password():
    redacted = redact_proxy_url("http://alice:top-secret@84.55.0.94:5432")
    assert redacted["host_masked"] == "84.55.x.x"
    assert redacted["port"] == 5432
    assert redacted["username_masked"] == "a***e"
    assert redacted["has_credentials"] is True
    blob = str(redacted)
    assert "top-secret" not in blob
    assert "alice" not in blob
    assert "84.55.0.94" not in blob


def test_fingerprint_is_stable_and_non_reversible():
    a = proxy_fingerprint("http://u:p@10.0.0.1:1")
    b = proxy_fingerprint("http://u:p@10.0.0.1:1")
    c = proxy_fingerprint("http://u:p@10.0.0.1:2")
    assert a == b
    assert a != c
    assert "10.0.0.1" not in a


def test_auto_profile_defaults_enable_geoip_and_align_locale():
    defaults = build_auto_profile_defaults(
        proxy_url="http://u:p@10.0.0.1:8080",
        country_code="AT",
        harness="browser-use",
    )
    assert defaults["geoip"] is True
    assert defaults["timezone"] == "Europe/Vienna"
    assert defaults["locale"] == "de-AT"
    assert defaults["harness"] == "browser-use"
    assert defaults["proxy"] == "http://u:p@10.0.0.1:8080"
    assert defaults["folder_path"] == "auto"
    assert defaults["project_id"] == "proxied"


def test_extract_country_and_summarize_check_payload():
    payload = {
        "results": [
            {
                "ok": True,
                "latency_ms": 42.5,
                "enrichment": {"country_code": "de"},
            }
        ],
        "scoring": {"risk_score": 12, "reasons": []},
    }
    assert extract_country_code(payload) == "DE"
    summary = summarize_check_payload(payload)
    assert summary["reachable"] is True
    assert summary["latency_ms"] == 42.5
    assert summary["risk_score"] == 12
    assert summary["country_code"] == "DE"
    assert summary["timezone_hint"] == "Europe/Berlin"
    assert summary["check_state"] == "passed"
    assert "password" not in str(summary)
