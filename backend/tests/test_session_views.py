"""Tests for CDP live HTML viewer (observer screencast)."""

from __future__ import annotations

import json
import re

from backend import session_views


def _config_from_html(html: str) -> dict:
    match = re.search(r"const CONFIG = (\{.*?\});\s*\n", html, flags=re.DOTALL)
    assert match, "CONFIG object missing from live HTML"
    return json.loads(match.group(1))


def test_cdp_live_html_uses_same_origin_relative_paths_and_location_ws():
    """Live config must not depend on ASGI Host/proto; WS follows window.location."""
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        interactive=False,
    )
    config = _config_from_html(html)
    assert config["cdpListUrl"] == "/api/profiles/prof-1/cdp-observer/json/list"
    assert config["metricsUrl"] == "/api/profiles/prof-1/live-metrics"
    assert "cdpWsUrl" not in config
    assert config["cdpListUrl"].startswith("/")
    assert config["metricsUrl"].startswith("/")
    assert "window.location" in html
    assert "window.location.protocol" in html
    assert "window.location.host" in html
    assert "'wss:'" in html or '"wss:"' in html
    assert "'ws:'" in html or '"ws:"' in html
    # No server-baked absolute endpoints in the viewer bootstrap.
    assert "ws://" not in html
    assert "wss://" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "127.0.0.1" not in html
    assert "page/pending" not in html


def test_cdp_live_html_does_not_emit_or_open_pending_target():
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        interactive=False,
    )
    assert "page/pending" not in html
    assert "CONFIG.cdpWsUrl" not in html
    assert '"cdpWsUrl"' not in html
    # Without a discovered page target, reconnect — never invent a pending WS.
    assert "no observer target" in html
    connect_fn = html.split("async function connect()", 1)[1].split(
        "async function postMetrics()", 1
    )[0]
    assert "bindSocket(pageUrl)" in connect_fn or "bindSocket(page" in connect_fn
    assert "page/pending" not in connect_fn
    assert "cdpWsUrl" not in connect_fn

def test_cdp_live_html_uses_observer_screencast_only():
    html = session_views.render_cdp_live_html(
        profile_id="prof-1",
        profile_name="Demo",
        interactive=True,
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
        "page/pending",
    ):
        assert forbidden not in html


def test_cdp_live_html_escapes_profile_name():
    html = session_views.render_cdp_live_html(
        profile_id="p",
        profile_name='<script>alert(1)</script>',
        interactive=False,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
