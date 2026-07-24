#!/usr/bin/env python3
"""E2E-ish verification of Manager APIs used by cloak-profile-sync.

Never prints proxy passwords, auth tokens, or raw credentials.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("CLOAK_MANAGER_BASE", "http://127.0.0.1:18117").rstrip("/")
TOKEN_PATH = Path.home() / ".config" / "cloakbrowser" / "vcvm-auth-token"


def load_token() -> str:
    env = os.environ.get("CLOAK_MANAGER_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    raise SystemExit(f"No token in CLOAK_MANAGER_TOKEN or {TOKEN_PATH}")


def request(path: str, token: str, method: str = "GET", body: dict | None = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": raw[:200]}
        return exc.code, parsed


def mask_proxy(value: object) -> str:
    if not value:
        return "none"
    text = str(value)
    if "@" in text:
        return "http://***:***@*** (redacted)"
    return "configured (redacted)"


def main() -> int:
    token = load_token()
    print(f"Manager base: {BASE}")
    results: list[tuple[str, str]] = []

    code, health = request("/health", token)
    results.append(("/health", "ok" if code == 200 else f"fail {code}"))

    code, catalog = request("/api/extension/catalog", token)
    catalog_ok = code == 200
    results.append(
        (
            "/api/extension/catalog",
            f"ok profiles={len(catalog.get('profiles', []))}" if catalog_ok and isinstance(catalog, dict)
            else f"fallback ({code})",
        )
    )

    code, profiles = request("/api/profiles", token)
    if code != 200 or not isinstance(profiles, list):
        print("FATAL: /api/profiles failed", code, profiles)
        return 1
    results.append(("/api/profiles", f"ok count={len(profiles)}"))

    code, proxies = request("/api/proxies", token)
    results.append(
        (
            "/api/proxies",
            f"ok count={len(proxies)}" if code == 200 and isinstance(proxies, list) else f"fail {code}",
        )
    )

    if not profiles:
        print("No profiles to probe further.")
    else:
        sample = profiles[0]
        pid = sample["id"]
        print(f"Sample profile: {sample.get('name')} ({pid[:8]}…)")
        print(f"  status={sample.get('status')} proxy={mask_proxy(sample.get('proxy'))}")

        code, exts = request(f"/api/profiles/{pid}/extensions", token)
        n = len(exts.get("extensions", [])) if isinstance(exts, dict) else "?"
        results.append((f"/api/profiles/{{id}}/extensions", f"{code} count={n}"))

        code, healthp = request(f"/api/profiles/{pid}/health", token)
        if isinstance(healthp, dict):
            results.append(
                (
                    "/api/profiles/{id}/health",
                    f"{code} state={healthp.get('state')} auth={healthp.get('proxy_authenticity_score')}",
                )
            )
        else:
            results.append(("/api/profiles/{id}/health", f"fail {code}"))

        code, opened = request(
            "/api/extension/sessions/open",
            token,
            method="POST",
            body={"profile_id": pid, "launch": False, "prefer": "cloud"},
        )
        if code == 200 and isinstance(opened, dict):
            url = opened.get("open_url") or ""
            results.append(
                (
                    "/api/extension/sessions/open",
                    f"ok status={opened.get('status')} open_url_host={url.split('/')[2] if '//' in url else 'n/a'}",
                )
            )
        else:
            results.append(("/api/extension/sessions/open", f"fallback ({code})"))

        code, defaults = request("/api/extension/defaults", token)
        results.append(
            (
                "/api/extension/defaults",
                "ok" if code == 200 else f"extension-point ({code})",
            )
        )

    # Local bridge optional
    try:
        req = urllib.request.Request("http://127.0.0.1:18765/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            results.append(("/local-bridge/health", f"ok cloakbrowser={payload.get('cloakbrowser')}"))
    except Exception:
        results.append(("/local-bridge/health", "offline (start host/local_bridge.py for Open Local)"))

    print("\nChecks:")
    for name, status in results:
        print(f"  {name}: {status}")

    print("\nLoad unpacked: extensions/cloak-profile-sync/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
