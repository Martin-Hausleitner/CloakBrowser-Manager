"""Tests for CDP live HTML viewer (observer screencast)."""

from __future__ import annotations

from backend import session_views


def test_cdp_live_html_uses_observer_screencast_only():
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        cdp_ws_url="ws://127.0.0.1:18117/api/profiles/prof-1/cdp-observer/devtools/page/x",
        metrics_url="http://127.0.0.1:18117/api/profiles/prof-1/live-metrics",
        interactive=True,
        cdp_list_url="http://127.0.0.1:18117/api/profiles/prof-1/cdp-observer/json/list",
    )
    assert "Page.startScreencast" in html
    assert "Page.screencastFrameAck" in html
    assert "Page.stopScreencast" in html
    assert "cdp-observer" in html
    assert "createImageBitmap" in html
    assert "live · CDP cast" in html
    assert "cdpListUrl" in html
    assert "disconnected" in html
    assert session_views.cdp_fullscreen_path("prof-1") == "/session/prof-1/live"
    for forbidden in (
        "Target.createTarget",
        "Target.attachToTarget",
        "Target.getTargets",
        "Runtime.evaluate",
        "Runtime.enable",
        "Page.navigate",
        "Input.dispatchMouseEvent",
        "Input.dispatchKeyEvent",
        "Page.captureScreenshot",
        "injectCompositorPulse",
        "/cdp/json/list",
    ):
        assert forbidden not in html


def test_cdp_live_html_escapes_profile_name():
    html = session_views.render_cdp_live_html(
        profile_id="p",
        profile_name='<script>alert(1)</script>',
        cdp_ws_url="ws://example/cdp-observer/devtools/page/x",
        metrics_url="http://example/metrics",
        interactive=False,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
