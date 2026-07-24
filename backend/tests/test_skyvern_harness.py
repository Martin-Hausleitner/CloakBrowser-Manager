"""Unit tests for the Skyvern harness adapter."""

from __future__ import annotations

import pytest

from backend.harnesses import skyvern_harness as harness


def test_capabilities_reports_agpl_and_unavailable_without_skyvern(monkeypatch):
    monkeypatch.setattr(harness, "skyvern_importable", lambda: False)
    monkeypatch.setattr(harness, "llm_configured", lambda: False)
    caps = harness.capabilities()
    assert caps["harness"] == "skyvern"
    assert caps["status"] == "unavailable"
    assert caps["skyvern_license"] == "AGPL-3.0"
    assert caps["cdp_routing"] == "cloakbrowser-manager"
    assert caps["capabilities"]["run_task"] is False


def test_capabilities_degraded_without_llm(monkeypatch):
    monkeypatch.setattr(harness, "skyvern_importable", lambda: True)
    monkeypatch.setattr(harness, "llm_configured", lambda: False)
    caps = harness.capabilities()
    assert caps["status"] == "degraded"
    assert caps["capabilities"]["connect_over_cdp"] is True
    assert caps["capabilities"]["run_task"] is False


def test_build_cdp_browser_address():
    url = harness.build_cdp_browser_address(
        base_url="http://127.0.0.1:8080/",
        profile_id="abc-123",
    )
    assert url == "http://127.0.0.1:8080/api/profiles/abc-123/cdp"


def test_bind_requires_running_profile():
    with pytest.raises(RuntimeError, match="not running"):
        harness.bind_profile_cdp(
            base_url="http://127.0.0.1:8080",
            profile_id="p1",
            profile_running=False,
        )


def test_bind_prefers_direct_cdp_port():
    target = harness.bind_profile_cdp(
        base_url="http://127.0.0.1:8080",
        profile_id="p1",
        profile_running=True,
        auth_token="secret",
        direct_cdp_port=9333,
    )
    assert target.browser_address.endswith("/api/profiles/p1/cdp")
    assert target.direct_browser_address == "http://127.0.0.1:9333"
    assert target.headers["Authorization"] == "Bearer secret"
    assert (
        harness.preferred_browser_address(target, prefer_direct=True)
        == "http://127.0.0.1:9333"
    )
    assert (
        harness.preferred_browser_address(target, prefer_direct=False)
        == target.browser_address
    )


@pytest.mark.asyncio
async def test_run_agent_task_blocked_without_llm(monkeypatch):
    monkeypatch.setattr(harness, "skyvern_importable", lambda: True)
    monkeypatch.setattr(harness, "llm_configured", lambda: False)
    result = await harness.run_agent_task(
        browser_address="http://127.0.0.1:9333",
        prompt="open example.com",
    )
    assert result["status"] == "blocked"
    assert "LLM" in result["reason"]
