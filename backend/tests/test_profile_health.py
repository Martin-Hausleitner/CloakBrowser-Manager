"""Unit tests for redacted profile-health normalization and classification."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from backend.profile_health import (
    ProfileHealthProbe,
    classify_browserscan_text,
    derive_authenticity_score,
    is_trusted_proxychecker_url,
    mask_ip_address,
    normalize_proxychecker_response,
    score_fingerprint_consistency,
)


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: object):
        self.payload = payload
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url: str, *, json: dict[str, object]):
        self.posts.append((url, json))
        return _FakeResponse(self.payload)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("203.0.113.42", "203.0.113.x"),
        ("2001:db8:abcd:12::1", "2001:db8:abcd:…"),
        ("not-an-ip", None),
        ("", None),
    ],
)
def test_mask_ip_address_never_returns_complete_input(value: str, expected: str | None):
    assert mask_ip_address(value) == expected
    if expected is not None:
        assert expected != value


@pytest.mark.parametrize(
    ("risk", "expected"),
    [(0, 100), (12.6, 87), (100, 0), (250, 0), (-10, 100), (None, None), (True, None)],
)
def test_derive_authenticity_score_is_clamped_and_explicitly_derived(risk: object, expected: int | None):
    assert derive_authenticity_score(risk) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8899",
        "http://[::1]:8899",
        "http://localhost:8899",
        "http://host.docker.internal:8899",
        "https://proxychecker.internal:8899",
    ],
)
def test_proxychecker_url_accepts_only_local_or_explicitly_allowed_hosts(url: str):
    assert is_trusted_proxychecker_url(url, allowed_hosts={"proxychecker.internal"}) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/check",
        "http://user:password@127.0.0.1:8899",
        "ftp://127.0.0.1/check",
        "//127.0.0.1:8899",
        "not-a-url",
        "",
    ],
)
def test_proxychecker_url_rejects_public_credentials_and_invalid_schemes(url: str):
    assert is_trusted_proxychecker_url(url) is False


def test_normalize_proxychecker_response_keeps_only_safe_fields():
    raw = {
        "results": [
            {
                "proxy": "http://secret-user:secret-pass@proxy.example:8080",
                "ok": True,
                "latency_ms": 41.25,
                "ip": "203.0.113.42",
                "error": "provider said a secret thing",
            }
        ],
        "scoring": {
            "risk_score": 23.4,
            "verdict_final": "DEGRADED",
            "reasons": ["VPN provider signal", "unknown provider prose containing secret-value"],
        },
    }

    result = normalize_proxychecker_response(raw)

    assert result.reachable is True
    assert result.latency_ms == 41.25
    assert result.risk_score == 23
    assert result.authenticity_score == 77
    assert result.warnings == ("vpn_detected",)
    assert result.blockers == ()
    assert "secret" not in repr(result)
    assert "203.0.113.42" not in repr(result)
    assert "proxy.example" not in repr(result)


def test_normalize_proxychecker_response_marks_malformed_payload_unavailable():
    result = normalize_proxychecker_response({"results": "not-a-list", "scoring": []})

    assert result.reachable is None
    assert result.risk_score is None
    assert result.blockers == ("proxychecker_invalid_response",)


def test_score_fingerprint_consistency_reports_matches_without_raw_values():
    config = {
        "platform": "macos",
        "screen_width": 390,
        "screen_height": 844,
        "timezone": "Europe/Vienna",
        "locale": "de-AT",
        "hardware_concurrency": 8,
        "user_agent": "Mozilla/5.0 Safari/605.1.15",
    }
    runtime = {
        "platform": "MacIntel",
        "screen_width": 390,
        "screen_height": 844,
        "timezone": "Europe/Vienna",
        "language": "de-AT",
        "hardware_concurrency": 8,
        "user_agent": "Mozilla/5.0 Safari/605.1.15",
    }

    result = score_fingerprint_consistency(config, runtime)

    assert result.score == 100
    assert result.warnings == ()
    assert result.blockers == ()
    assert "Safari/605" not in repr(result)
    assert "Europe/Vienna" not in repr(result)


def test_score_fingerprint_consistency_separates_mismatch_and_missing_signals():
    config = {
        "platform": "windows",
        "screen_width": 390,
        "screen_height": 844,
        "timezone": "Europe/Vienna",
        "locale": "de-AT",
        "hardware_concurrency": 8,
        "user_agent": "Mozilla/5.0 Chrome/126.0.0.0",
    }
    runtime = {
        "platform": "Linux x86_64",
        "screen_width": 1920,
        "screen_height": 1080,
        "timezone": "UTC",
        "language": "en-US",
        "hardware_concurrency": None,
        "user_agent": "Mozilla/5.0 Firefox/128.0",
    }

    result = score_fingerprint_consistency(config, runtime)

    assert result.score == 0
    assert result.warnings == (
        "platform_mismatch",
        "screen_mismatch",
        "timezone_mismatch",
        "locale_mismatch",
        "user_agent_family_mismatch",
    )
    assert result.blockers == ("hardware_concurrency_missing",)


def test_classify_browserscan_text_extracts_only_score_and_whitelisted_warnings():
    text = """
    BrowserScan report
    Authenticity 94%
    WebRTC network mismatch detected
    Canvas fingerprint warning
    account-id: private-12345
    """

    result = classify_browserscan_text(text)

    assert result.score == 94
    assert result.warnings == ("network_mismatch", "fingerprint_warning")
    assert result.blockers == ()
    assert "private-12345" not in repr(result)


@pytest.mark.parametrize(
    ("text", "blocker"),
    [
        ("Verify you are human. Authenticity 99%", "browser_scan_challenge"),
        ("Cookie consent: Accept all cookies. Authenticity 99%", "browser_scan_consent"),
        ("BrowserScan dashboard loaded without a score", "browser_scan_score_missing"),
    ],
)
def test_classify_browserscan_text_fails_closed(text: str, blocker: str):
    result = classify_browserscan_text(text)

    assert result.score is None
    assert result.warnings == ()
    assert result.blockers == (blocker,)


@pytest.mark.asyncio
async def test_profile_health_probe_measures_existing_context_and_closes_temporary_pages():
    network_page = AsyncMock()
    network_page.text_content = AsyncMock(return_value='{"ip":"203.0.113.42"}')
    network_page.evaluate = AsyncMock(
        return_value={
            "platform": "MacIntel",
            "screen_width": 390,
            "screen_height": 844,
            "timezone": "Europe/Vienna",
            "language": "de-AT",
            "hardware_concurrency": 8,
            "user_agent": "Mozilla/5.0 Safari/605.1.15",
        }
    )
    browserscan_page = AsyncMock()
    browserscan_page.text_content = AsyncMock(return_value="Authenticity 98%")
    context = AsyncMock()
    context.new_page = AsyncMock(side_effect=[network_page, browserscan_page])
    running = type("Running", (), {"context": context})()
    client = _FakeHttpClient(
        {
            "results": [{"ok": True, "latency_ms": 22.5, "ip": "203.0.113.42"}],
            "scoring": {"risk_score": 5, "verdict_final": "GOOD", "reasons": []},
        }
    )
    ticks = iter([10.0, 10.125])
    probe = ProfileHealthProbe(
        proxychecker_url="http://127.0.0.1:8899",
        http_client_factory=lambda **_kwargs: client,
        monotonic=lambda: next(ticks),
        now=lambda: "2026-07-22T12:00:00+00:00",
    )
    profile = {
        "id": "profile-1",
        "proxy": "http://secret-user:secret-password@proxy.example:8080",
        "platform": "macos",
        "screen_width": 390,
        "screen_height": 844,
        "timezone": "Europe/Vienna",
        "locale": "de-AT",
        "hardware_concurrency": 8,
        "user_agent": "Mozilla/5.0 Safari/605.1.15",
    }

    result = await probe.run(profile, running)

    assert result.state == "passed"
    assert result.checked_at == "2026-07-22T12:00:00+00:00"
    assert result.proxy_configured is True
    assert result.proxy_reachable is True
    assert result.outbound_ip_masked == "203.0.113.x"
    assert result.proxy_latency_ms == 22.5
    assert result.proxy_risk_score == 5
    assert result.proxy_authenticity_score == 95
    assert result.fingerprint_consistency_score == 100
    assert result.browser_scan_score == 98
    assert result.sources == {
        "browser_network": "measured",
        "browser_scan": "measured",
        "fingerprint_consistency": "measured",
        "proxy_authenticity": "derived",
        "proxychecker": "measured",
    }
    assert result.warnings == ()
    assert result.blockers == ()
    assert "secret-password" not in repr(result)
    assert "proxy.example" not in repr(result)
    network_page.close.assert_awaited_once()
    browserscan_page.close.assert_awaited_once()
    assert not any(call[0] == "click" for call in browserscan_page.method_calls)
    assert client.posts[0][0] == "http://127.0.0.1:8899/check"


@pytest.mark.asyncio
async def test_profile_health_probe_keeps_partial_measurement_when_external_sources_are_unavailable():
    network_page = AsyncMock()
    network_page.goto = AsyncMock(side_effect=TimeoutError("private upstream detail"))
    network_page.evaluate = AsyncMock(
        return_value={
            "platform": "Win32",
            "screen_width": 390,
            "screen_height": 844,
            "timezone": "UTC",
            "language": "en-US",
            "hardware_concurrency": 4,
            "user_agent": "Mozilla/5.0 Chrome/126.0.0.0",
        }
    )
    browserscan_page = AsyncMock()
    browserscan_page.goto = AsyncMock(side_effect=ConnectionError("private provider detail"))
    context = AsyncMock()
    context.new_page = AsyncMock(side_effect=[network_page, browserscan_page])
    running = type("Running", (), {"context": context})()
    ticks = iter([20.0, 20.1])
    probe = ProfileHealthProbe(
        monotonic=lambda: next(ticks),
        now=lambda: "2026-07-22T12:05:00+00:00",
    )
    profile = {
        "id": "profile-2",
        "proxy": None,
        "platform": "windows",
        "screen_width": 390,
        "screen_height": 844,
        "timezone": "UTC",
        "locale": "en-US",
        "hardware_concurrency": 4,
        "user_agent": "Mozilla/5.0 Chrome/126.0.0.0",
    }

    result = await probe.run(profile, running)

    assert result.state == "warning"
    assert result.proxy_configured is False
    assert result.proxy_reachable is False
    assert result.fingerprint_consistency_score == 100
    assert result.browser_scan_score is None
    assert result.error_code == "network_timeout"
    assert result.sources == {
        "browser_network": "unavailable",
        "browser_scan": "unavailable",
        "fingerprint_consistency": "measured",
        "proxychecker": "skipped",
    }
    assert result.blockers == ("network_timeout", "browser_scan_unavailable")
    assert "private" not in repr(result)
    network_page.close.assert_awaited_once()
    browserscan_page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_health_probe_returns_unavailable_when_no_component_can_be_measured():
    context = AsyncMock()
    context.new_page = AsyncMock(side_effect=RuntimeError("private context detail"))
    running = type("Running", (), {"context": context})()
    probe = ProfileHealthProbe(now=lambda: "2026-07-22T12:10:00+00:00")

    result = await probe.run({"id": "profile-3", "proxy": None}, running)

    assert result.state == "unavailable"
    assert result.error_code == "browser_context_unavailable"
    assert result.blockers == (
        "browser_context_unavailable",
        "browser_scan_unavailable",
    )
    assert result.sources["browser_network"] == "unavailable"
    assert result.sources["fingerprint_consistency"] == "unavailable"
    assert result.sources["browser_scan"] == "unavailable"
    assert result.sources["proxychecker"] == "skipped"
    assert "private context detail" not in repr(result)


def test_profile_health_probe_rejects_untrusted_proxychecker_url():
    with pytest.raises(ValueError, match="trusted local endpoint"):
        ProfileHealthProbe(proxychecker_url="https://example.com/check")
