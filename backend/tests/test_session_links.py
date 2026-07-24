"""Unit tests for steel-adapted local/cloud session open links."""

from __future__ import annotations

from backend import session_links


def test_join_url_avoids_double_slash():
    assert session_links.join_url("http://127.0.0.1:18117/", "/api/x") == "http://127.0.0.1:18117/api/x"
    assert session_links.join_url("http://127.0.0.1:18117", "api/x") == "http://127.0.0.1:18117/api/x"


def test_request_base_url_prefers_forwarded_headers():
    base = session_links.request_base_url(
        scheme="http",
        host="127.0.0.1:18117",
        forwarded_proto="https",
        forwarded_host="manager.example.ts.net",
    )
    assert base == "https://manager.example.ts.net"


def test_build_session_open_links_local_and_cloud(monkeypatch):
    monkeypatch.setenv("CLOUD_BASE_URL", "https://cloak.example.ts.net")
    payload = session_links.build_session_open_links(
        "prof-1",
        local_base="http://127.0.0.1:18117",
        include_cdp=True,
        prefer="cloud",
        mode="cdp",
    )
    assert payload["prefer"] == "cloud"
    assert payload["mode"] == "cdp"
    assert payload["open_url"] == "https://cloak.example.ts.net/session/prof-1/live"
    assert payload["local"]["vnc_ws_url"] == "ws://127.0.0.1:18117/api/profiles/prof-1/vnc"
    assert payload["local"]["vnc_fullscreen_url"].endswith("&view=vnc&fullscreen=1")
    assert payload["cloud"]["cdp_http_url"] == "https://cloak.example.ts.net/api/profiles/prof-1/cdp"
    assert payload["cloud"]["cdp_ws_url"] == "wss://cloak.example.ts.net/api/profiles/prof-1/cdp"
    assert payload["cloud"]["live_url"] == "https://cloak.example.ts.net/session/prof-1/live"
    assert payload["local"]["debugger_url"].endswith("/cdp/json/version")


def test_build_session_open_links_falls_back_when_cloud_missing(monkeypatch):
    monkeypatch.delenv("CLOUD_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    payload = session_links.build_session_open_links(
        "prof-2",
        local_base="http://127.0.0.1:18117",
        include_cdp=False,
        prefer="cloud",
        mode="cdp",
    )
    assert payload["prefer"] == "local"
    assert payload["cloud"] is None
    assert payload["local"]["cdp_http_url"] is None
    # Without CDP permission, mode falls back to VNC fullscreen.
    assert payload["mode"] == "vnc"
    assert payload["open_url"] == "http://127.0.0.1:18117/?profile=prof-2&view=vnc&fullscreen=1"


def test_catalog_endpoint_map_stable():
    endpoints = session_links.catalog_endpoint_map()
    assert endpoints["catalog"] == "/api/extension/catalog"
    assert endpoints["open_session"] == "/api/extension/sessions/open"
    assert endpoints["defaults"] == "/api/extension/defaults"
    assert endpoints["templates"] == "/api/extension/templates"
    assert endpoints["list_proxies"] == "/api/proxies"
    assert endpoints["live_metrics"] == "/api/profiles/{profile_id}/live-metrics"


def test_extension_profile_summary_redacts_proxy_secret():
    summary = session_links.extension_profile_summary(
        {
            "id": "p1",
            "name": "Alpha",
            "proxy": "http://user:secret@10.0.0.1:8080",
            "project_id": "proxied",
            "folder_path": "auto",
            "sandbox_id": "default",
            "harness": "browser-use",
            "pinned": True,
            "timezone": "Europe/Vienna",
            "locale": "de-AT",
        },
        status="running",
        running=True,
    )
    assert summary["proxy_configured"] is True
    assert "secret" not in str(summary)
    assert "10.0.0.1" not in str(summary)
    assert summary["running"] is True
