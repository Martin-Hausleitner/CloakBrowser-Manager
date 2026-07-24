#!/usr/bin/env python3
"""Local CloakBrowser launch bridge for the Profile Sync Chrome extension.

Listens on 127.0.0.1 only. Fetches a Manager profile with the caller's token
and launches cloakbrowser with proxy + fingerprint alignment (proxy-on-start).

Usage:
  python3 extensions/cloak-profile-sync/host/local_bridge.py
  python3 extensions/cloak-profile-sync/host/local_bridge.py --port 18765
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_PORT = 18765
DEFAULT_DATA_ROOT = Path.home() / ".cloakbrowser" / "profile-sync" / "profiles"

_lock = threading.Lock()
_running: dict[str, dict[str, Any]] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    data = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _manager_request(
    base: str,
    path: str,
    *,
    token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    url = base.rstrip("/") + path
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Manager HTTP {exc.code}: {detail[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Manager unreachable: {exc.reason}") from exc


def _ensure_auth(base: str, payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    token = (payload.get("token") or "").strip() or None
    username = (payload.get("username") or "").strip() or None
    password = payload.get("password") or None
    if token:
        return token, None, None
    if username and password:
        # Cookie auth is awkward from urllib; prefer token. Attempt login for
        # deployments that accept repeated username/password on each call is N/A.
        # Ask Manager login — if it only sets cookies, require token instead.
        raise RuntimeError(
            "local_bridge requires a bearer token (password login cookies are not portable). "
            "Paste the Manager auth token into the extension."
        )
    # Fall back to local token file used by VCVM tunnel workflows.
    token_path = Path.home() / ".config" / "cloakbrowser" / "vcvm-auth-token"
    if token_path.is_file():
        file_token = token_path.read_text(encoding="utf-8").strip()
        if file_token:
            return file_token, None, None
    raise RuntimeError("No auth token provided for Manager fetch")


def _normalize_proxy(raw: str) -> str:
    if raw.startswith(("http://", "https://", "socks5://")):
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return f"http://{user}:{passwd}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{raw}"
    return raw


def _build_fingerprint_args(profile: dict[str, Any]) -> list[str]:
    """Mirror Manager browser_manager fingerprint args (no VNC swiftshader)."""
    args: list[str] = ["--disable-infobars"]
    seed = profile.get("fingerprint_seed")
    if seed is not None:
        args.append(f"--fingerprint={seed}")
    platform = profile.get("platform")
    if platform:
        args.append(f"--fingerprint-platform={platform}")
    vendor = profile.get("gpu_vendor")
    if vendor:
        args.append(f"--fingerprint-gpu-vendor={vendor}")
    renderer = profile.get("gpu_renderer")
    if renderer:
        args.append(f"--fingerprint-gpu-renderer={renderer}")
    hw = profile.get("hardware_concurrency")
    if hw is not None:
        args.append(f"--fingerprint-hardware-concurrency={hw}")
    sw = profile.get("screen_width")
    if sw:
        args.append(f"--fingerprint-screen-width={sw}")
    sh = profile.get("screen_height")
    if sh:
        args.append(f"--fingerprint-screen-height={sh}")
    # Preserve --load-extension paths from Manager profile when present.
    for arg in profile.get("launch_args") or []:
        if isinstance(arg, str) and (
            arg.startswith("--load-extension=")
            or arg.startswith("--disable-extensions-except=")
            or arg.startswith("--fingerprint")
        ):
            args.append(arg)
    return args


def _launch_profile(profile: dict[str, Any], *, require_proxy_if_configured: bool) -> dict[str, Any]:
    try:
        from cloakbrowser import launch_persistent_context
    except ImportError as exc:
        raise RuntimeError(
            "cloakbrowser package not installed. pip install cloakbrowser && cloakbrowser install"
        ) from exc

    profile_id = str(profile["id"])
    with _lock:
        existing = _running.get(profile_id)
        if existing and existing.get("context") is not None:
            return {
                "ok": True,
                "already_running": True,
                "profile_id": profile_id,
                "user_data_dir": existing.get("user_data_dir"),
                "proxy_applied": bool(existing.get("proxy_applied")),
            }

    raw_proxy = profile.get("proxy") or None
    proxy = _normalize_proxy(raw_proxy) if raw_proxy else None
    if require_proxy_if_configured and raw_proxy and not proxy:
        raise RuntimeError("Profile has proxy configured but it could not be normalized")
    if require_proxy_if_configured and profile.get("proxy") in ("", None):
        # not configured — fine
        pass

    user_data_dir = DEFAULT_DATA_ROOT / profile_id
    user_data_dir.mkdir(parents=True, exist_ok=True)

    extra_args = _build_fingerprint_args(profile)
    viewport = {
        "width": int(profile.get("screen_width") or 1920),
        "height": max(600, int(profile.get("screen_height") or 1080) - 80),
    }

    # Proxy-on-start: always pass proxy when the Manager profile has one.
    context = launch_persistent_context(
        user_data_dir=str(user_data_dir),
        headless=False,
        proxy=proxy,
        args=extra_args,
        timezone=profile.get("timezone") or None,
        locale=profile.get("locale") or None,
        humanize=bool(profile.get("humanize", False)),
        human_preset=profile.get("human_preset") or "default",
        geoip=bool(profile.get("geoip", False)),
        color_scheme=profile.get("color_scheme") or None,
        user_agent=profile.get("user_agent") or None,
        viewport=viewport,
    )

    def _on_close() -> None:
        with _lock:
            _running.pop(profile_id, None)

    try:
        context.on("close", lambda _=None: _on_close())
    except Exception:
        pass

    with _lock:
        _running[profile_id] = {
            "context": context,
            "user_data_dir": str(user_data_dir),
            "proxy_applied": bool(proxy),
        }

    # Open a blank page so the window is visible immediately.
    try:
        page = context.pages[0] if context.pages else context.new_page()
        if page.url in ("", "about:blank"):
            page.goto("about:blank")
    except Exception:
        pass

    return {
        "ok": True,
        "already_running": False,
        "profile_id": profile_id,
        "user_data_dir": str(user_data_dir),
        "proxy_applied": bool(proxy),
        "name": profile.get("name"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CloakProfileSyncBridge/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, 204, {})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            info: dict[str, Any] = {"ok": True, "service": "cloak-profile-sync-bridge"}
            try:
                import cloakbrowser

                info["cloakbrowser"] = getattr(cloakbrowser, "__version__", "installed")
            except Exception:
                info["cloakbrowser"] = None
            with _lock:
                info["running_profiles"] = list(_running.keys())
            _json_response(self, 200, info)
            return
        _json_response(self, 404, {"ok": False, "detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/launch":
            _json_response(self, 404, {"ok": False, "detail": "Not found"})
            return
        try:
            payload = _read_json(self)
            profile_id = str(payload.get("profile_id") or "").strip()
            if not profile_id:
                raise ValueError("profile_id is required")
            manager_base = str(payload.get("manager_base") or "http://127.0.0.1:18117").rstrip("/")
            token, username, password = _ensure_auth(manager_base, payload)
            profile = _manager_request(
                manager_base,
                f"/api/profiles/{profile_id}",
                token=token,
                username=username,
                password=password,
            )
            result = _launch_profile(
                profile,
                require_proxy_if_configured=bool(payload.get("require_proxy_if_configured", True)),
            )
            # Never echo proxy credentials back to the extension.
            _json_response(self, 200, result)
        except Exception as exc:
            traceback.print_exc()
            _json_response(self, 400, {"ok": False, "detail": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="CloakBrowser Profile Sync local bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Refusing to bind non-loopback host", file=sys.stderr)
        return 2

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CloakBrowser local bridge on http://{args.host}:{args.port}", flush=True)
    print(f"Profile data root: {DEFAULT_DATA_ROOT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping bridge", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
