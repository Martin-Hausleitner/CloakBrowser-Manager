"""Tests for CDP live HTML viewer (screencast keep-alive)."""

from __future__ import annotations

from backend import session_views


def test_cdp_live_html_uses_screencast_with_compositor_pulse():
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        cdp_ws_url="ws://127.0.0.1:18117/api/profiles/prof-1/cdp",
        metrics_url="http://127.0.0.1:18117/api/profiles/prof-1/live-metrics",
        interactive=True,
        cdp_list_url="http://127.0.0.1:18117/api/profiles/prof-1/cdp/json/list",
    )
    assert "Page.startScreencast" in html
    assert "Page.screencastFrameAck" in html
    assert "injectCompositorPulse" in html
    assert "__cbm_live_pulse" in html
    assert "createElement('canvas')" in html
    assert "createImageBitmap" in html
    assert "live · CDP cast" in html
    assert "cdpListUrl" in html
    assert "Page.frameNavigated" in html
    assert "setInterval(() => { injectCompositorPulse()" in html
    # Screenshot remains emergency fallback only.
    assert "Page.captureScreenshot" in html
    assert "startScreenshotFallback" in html
    assert session_views.cdp_fullscreen_path("prof-1") == "/session/prof-1/live"


def test_cdp_live_html_escapes_profile_name():
    html = session_views.render_cdp_live_html(
        profile_id="p",
        profile_name='<script>alert(1)</script>',
        cdp_ws_url="ws://example/cdp",
        metrics_url="http://example/metrics",
        interactive=False,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
