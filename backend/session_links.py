"""Build clean local/cloud open-session URLs for extensions and agents.

URL shaping is adapted from the steel-dev/steel-browser ``getBaseUrl`` /
``getUrl`` helpers (Apache-2.0). CloakBrowser keeps its own path layout
(VNC/CDP under ``/api/profiles/{id}/...``) and never embeds secrets.

Performance priority: CDP live (``/session/{id}/live``) is the snappy
Browser-Use-style path; VNC fullscreen remains available as a fallback.
"""

from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import urlparse

try:
    from .session_views import cdp_fullscreen_path, vnc_fullscreen_path
except ImportError:  # uvicorn main:app from backend/
    from session_views import cdp_fullscreen_path, vnc_fullscreen_path


def cloud_base_url() -> str | None:
    """Return a configured absolute public/cloud base URL, or None."""
    raw = (os.environ.get("CLOUD_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def request_base_url(
    *,
    scheme: str,
    host: str,
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> str:
    """Derive the client-facing local base from the inbound request."""
    proto = (forwarded_proto or scheme or "http").split(",")[0].strip().lower()
    if proto not in {"http", "https"}:
        proto = "http"
    netloc = (forwarded_host or host or "").split(",")[0].strip()
    if not netloc:
        netloc = "127.0.0.1"
    return f"{proto}://{netloc}".rstrip("/")


def ws_scheme_for(http_base: str) -> str:
    parsed = urlparse(http_base)
    return "wss" if parsed.scheme == "https" else "ws"


def join_url(base: str, path: str) -> str:
    """Join base + path without double slashes (steel getUrl style)."""
    root = base.rstrip("/")
    formatted = path if path.startswith("/") else f"/{path}"
    return f"{root}{formatted}"


def build_link_set(
    base: str,
    profile_id: str,
    *,
    include_cdp: bool,
) -> dict[str, str | None]:
    """Absolute open links for one origin (local tunnel or cloud)."""
    http_base = base.rstrip("/")
    ws_base = f"{ws_scheme_for(http_base)}://{urlparse(http_base).netloc}"
    viewer = join_url(http_base, f"/?profile={profile_id}")
    vnc_fs = join_url(http_base, vnc_fullscreen_path(profile_id))
    cdp_fs = join_url(http_base, cdp_fullscreen_path(profile_id)) if include_cdp else None
    vnc_ws = join_url(ws_base, f"/api/profiles/{profile_id}/vnc")
    status = join_url(http_base, f"/api/profiles/{profile_id}/status")
    metrics = join_url(http_base, f"/api/profiles/{profile_id}/live-metrics")
    cdp_http = join_url(http_base, f"/api/profiles/{profile_id}/cdp") if include_cdp else None
    cdp_ws = join_url(ws_base, f"/api/profiles/{profile_id}/cdp") if include_cdp else None
    debugger = (
        join_url(http_base, f"/api/profiles/{profile_id}/cdp/json/version") if include_cdp else None
    )
    return {
        "session_viewer_url": viewer,
        "vnc_fullscreen_url": vnc_fs,
        "cdp_fullscreen_url": cdp_fs,
        "live_url": cdp_fs,  # Browser-Use naming for snappy CDP screencast
        "vnc_ws_url": vnc_ws,
        "debug_url": status,
        "debugger_url": debugger,
        "cdp_http_url": cdp_http,
        "cdp_ws_url": cdp_ws,
        "live_metrics_url": metrics,
        "launch_path": f"/api/profiles/{profile_id}/launch",
        "stop_path": f"/api/profiles/{profile_id}/stop",
        "status_path": f"/api/profiles/{profile_id}/status",
        "live_metrics_path": f"/api/profiles/{profile_id}/live-metrics",
    }


def _preferred_open_url(link_set: dict[str, str | None], mode: str) -> str:
    if mode == "cdp" and link_set.get("cdp_fullscreen_url"):
        return str(link_set["cdp_fullscreen_url"])
    if mode == "vnc":
        return str(link_set["vnc_fullscreen_url"])
    return str(link_set["session_viewer_url"])


def build_session_open_links(
    profile_id: str,
    *,
    local_base: str,
    cloud_base: str | None = None,
    include_cdp: bool = False,
    prefer: str = "local",
    mode: str = "cdp",
) -> dict[str, Any]:
    """Steel-style session details: local + optional cloud open URLs."""
    resolved_cloud = cloud_base or cloud_base_url()
    prefer_key = prefer if prefer in {"local", "cloud"} else "local"
    if prefer_key == "cloud" and not resolved_cloud:
        prefer_key = "local"
    mode_key = mode if mode in {"cdp", "vnc", "shell"} else "cdp"
    if mode_key == "cdp" and not include_cdp:
        mode_key = "vnc"

    local = build_link_set(local_base, profile_id, include_cdp=include_cdp)
    cloud = (
        build_link_set(resolved_cloud, profile_id, include_cdp=include_cdp)
        if resolved_cloud
        else None
    )
    preferred = cloud if prefer_key == "cloud" and cloud else local
    open_url = _preferred_open_url(preferred, mode_key)
    return {
        "profile_id": profile_id,
        "prefer": prefer_key,
        "mode": mode_key,
        "open_url": open_url,
        "local": local,
        "cloud": cloud,
        "bases": {
            "local": local_base.rstrip("/"),
            "cloud": resolved_cloud,
        },
        # Flat compatibility fields for Profile Sync / agents.
        "session_viewer_url": preferred["session_viewer_url"],
        "vnc_fullscreen_url": preferred["vnc_fullscreen_url"],
        "cdp_fullscreen_url": preferred["cdp_fullscreen_url"],
        "live_url": preferred["live_url"],
        "debug_url": preferred["debug_url"],
        "debugger_url": preferred["debugger_url"],
        "websocket_url": preferred["vnc_ws_url"],
        "cdp_url": preferred["cdp_http_url"],
        "live_metrics_url": preferred["live_metrics_url"],
        "local_url": local["session_viewer_url"],
        "cloud_url": cloud["session_viewer_url"] if cloud else None,
        "local_vnc_fullscreen_url": local["vnc_fullscreen_url"],
        "local_cdp_fullscreen_url": local["cdp_fullscreen_url"],
        "cloud_vnc_fullscreen_url": cloud["vnc_fullscreen_url"] if cloud else None,
        "cloud_cdp_fullscreen_url": cloud["cdp_fullscreen_url"] if cloud else None,
    }


def catalog_endpoint_map() -> dict[str, str]:
    """Stable relative paths a Chrome extension or agent can call."""
    return {
        "catalog": "/api/extension/catalog",
        "defaults": "/api/extension/defaults",
        "templates": "/api/extension/templates",
        "open_session": "/api/extension/sessions/open",
        "open_links": "/api/profiles/{profile_id}/open-links",
        "live_metrics": "/api/profiles/{profile_id}/live-metrics",
        "cdp_live": "/session/{profile_id}/live",
        "vnc_fullscreen": "/?profile={profile_id}&view=vnc&fullscreen=1",
        "list_profiles": "/api/profiles",
        "create_profile": "/api/profiles",
        "list_proxies": "/api/proxies",
        "ingest_proxies": "/api/proxies/ingest",
        "create_profile_from_proxy": "/api/proxies/{proxy_id}/profiles",
        "launch_profile": "/api/profiles/{profile_id}/launch",
        "stop_profile": "/api/profiles/{profile_id}/stop",
        "profile_status": "/api/profiles/{profile_id}/status",
        "profile_health": "/api/profiles/{profile_id}/health",
        "profile_extensions": "/api/profiles/{profile_id}/extensions",
        "cdp": "/api/profiles/{profile_id}/cdp",
        "vnc": "/api/profiles/{profile_id}/vnc",
        "auth_status": "/api/auth/status",
        "auth_login": "/api/auth/login",
    }


def profile_templates() -> list[dict[str, Any]]:
    """Discoverable create-flow templates for extension/agent UIs."""
    return [
        {
            "id": "browser-use-proxied",
            "name": "Browser Use · proxied",
            "project_id": "proxied",
            "folder_path": "auto",
            "harness": "browser-use",
            "geoip": True,
            "create_path": "/api/profiles",
            "from_proxy_path": "/api/proxies/{proxy_id}/profiles",
        },
        {
            "id": "research-default",
            "name": "Research · default",
            "project_id": "research",
            "folder_path": "",
            "harness": "browser-use",
            "geoip": False,
            "create_path": "/api/profiles",
        },
        {
            "id": "mobile-viewport",
            "name": "Mobile · viewport",
            "project_id": "mobile",
            "folder_path": "",
            "harness": "browser-use",
            "screen_width": 390,
            "screen_height": 844,
            "create_path": "/api/profiles",
        },
    ]


def extension_profile_summary(
    profile: Mapping[str, Any],
    *,
    status: str,
    running: bool,
) -> dict[str, Any]:
    """Minimal redacted profile card for extension UIs."""
    return {
        "id": str(profile.get("id") or ""),
        "name": str(profile.get("name") or ""),
        "project_id": str(profile.get("project_id") or "default"),
        "folder_path": str(profile.get("folder_path") or ""),
        "sandbox_id": str(profile.get("sandbox_id") or "default"),
        "harness": str(profile.get("harness") or "codex"),
        "pinned": bool(profile.get("pinned")),
        "timezone": profile.get("timezone") if isinstance(profile.get("timezone"), str) else None,
        "locale": profile.get("locale") if isinstance(profile.get("locale"), str) else None,
        "proxy_configured": bool(profile.get("proxy")),
        "status": status,
        "running": running,
    }
