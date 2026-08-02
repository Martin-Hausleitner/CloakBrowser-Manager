#!/usr/bin/env python3
"""Live PhoneFit re-tap proof: matching dims must not update/stop/launch.

Idempotent local proof against a branch Vite UI proxied to a real Manager.

Env:
  PHONEFIT_BASE_URL     default http://127.0.0.1:5190/
  PHONEFIT_PROFILE_ID   default a8b99a1f-bd77-4249-917f-0ad681ea5519
  AUTH_TOKEN            Manager access token when login is required
  PHONEFIT_OUT_DIR      default docs/evidence
  PHONEFIT_SHOT_NAME    default phonefit-idempotent-local-retap-2026-08-02.png
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("PHONEFIT_BASE_URL", "http://127.0.0.1:5190/")
PROFILE_ID = os.environ.get(
    "PHONEFIT_PROFILE_ID", "a8b99a1f-bd77-4249-917f-0ad681ea5519"
)
AUTH = os.environ.get("AUTH_TOKEN") or os.environ.get("MANAGER_AUTH_TOKEN") or ""
OUT_DIR = Path(os.environ.get("PHONEFIT_OUT_DIR", "docs/evidence"))
SHOT_NAME = os.environ.get(
    "PHONEFIT_SHOT_NAME", "phonefit-idempotent-local-retap-2026-08-02.png"
)
REPORT_PATH = Path(
    os.environ.get(
        "PHONEFIT_REPORT_PATH",
        "docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.json",
    )
)
W, H = 390, 844


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shot = OUT_DIR / SHOT_NAME
    checks: list[dict] = []

    def check(name: str, passed: bool, evidence=None) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})
        status = "PASS" if passed else "FAIL"
        extra = f" :: {evidence}" if evidence is not None else ""
        print(f"[{status}] {name}{extra}")
        if not passed:
            raise SystemExit(f"CHECK FAILED: {name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                "Mobile/15E148 Safari/604.1"
            ),
        )
        page = context.new_page()
        page.add_init_script(
            """
            (() => {
              const nativeMatchMedia = window.matchMedia.bind(window);
              window.matchMedia = (query) => {
                const nativeResult = nativeMatchMedia(query);
                const forceCoarse = query.trim() === '(pointer: coarse)';
                const forceMobileWorkspace = query.includes('(pointer: coarse)') &&
                  query.includes('(max-width: 1024px)') && window.innerWidth <= 1024;
                if (!forceCoarse && !forceMobileWorkspace) return nativeResult;
                return new Proxy(nativeResult, {
                  get(target, property) {
                    if (property === 'matches') return true;
                    const value = Reflect.get(target, property, target);
                    return typeof value === 'function' ? value.bind(target) : value;
                  },
                });
              };
            })();
            """
        )

        mutate = {"update": 0, "stop": 0, "launch": 0}
        tracking = {"enabled": False}

        def on_request(req) -> None:
            if not tracking["enabled"]:
                return
            url = req.url
            method = req.method.upper()
            if "/api/profiles/" not in url:
                return
            if method == "POST" and "/stop" in url:
                mutate["stop"] += 1
            elif method == "POST" and "/launch" in url:
                mutate["launch"] += 1
            elif method in ("PUT", "PATCH") and "/api/profiles/" in url:
                path = url.split("?", 1)[0]
                tail = path.rsplit("/api/profiles/", 1)[-1]
                if "/" not in tail.rstrip("/"):
                    mutate["update"] += 1

        page.on("request", on_request)
        page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)

        token_input = page.locator("input[placeholder='Access token']")
        if token_input.count() and token_input.first.is_visible():
            if not AUTH:
                raise SystemExit("Login required but AUTH_TOKEN empty")
            token_input.first.fill(AUTH)
            for sel in (
                "button:has-text('Continue')",
                "button:has-text('Sign in')",
                "button:has-text('Unlock')",
                "button[type='submit']",
            ):
                btn = page.locator(sel)
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
                    break
            page.wait_for_timeout(1500)

        page.wait_for_selector(
            "select.mobile-top-profile-select, .mobile-workspace, body",
            timeout=30000,
        )
        page.wait_for_timeout(1000)

        sel = page.locator("select.mobile-top-profile-select")
        if sel.count():
            try:
                sel.first.select_option(PROFILE_ID)
            except Exception as exc:  # noqa: BLE001 - proof continues with default
                print("select_option note:", exc)
            page.wait_for_timeout(2000)

        opened = False
        for label in ("Full view", "Full View", "Fullscreen", "full view"):
            btn = page.get_by_role("button", name=label)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                opened = True
                break
        if not opened:
            cand = page.locator("button").filter(has_text="Full")
            if cand.count():
                cand.first.click()
                opened = True
        check("fullscreen control present/open", opened)

        page.wait_for_timeout(800)
        viewport_opened = False
        for label in ("Viewport", "viewport"):
            b = page.get_by_role("button", name=label)
            if b.count() and b.first.is_visible():
                b.first.click()
                viewport_opened = True
                break
        if not viewport_opened:
            b = page.locator("[aria-label*='Viewport'], button:has-text('Viewport')")
            if b.count():
                b.first.click()
                viewport_opened = True
        page.wait_for_selector(
            '[aria-label="Fullscreen viewport controls"]', timeout=15000
        )
        check("viewport panel open", True)

        root = page.locator('[aria-label="Fullscreen viewport controls"]')
        phone_fit = root.get_by_role("button", name="Phone fit")
        check(
            "Phone fit available",
            phone_fit.count() > 0 and phone_fit.first.is_visible(),
        )

        phone_fit.first.click()
        for _ in range(40):
            text = root.inner_text()
            if (
                ("Saved" in text or "Already matches - no restart" in text)
                and "Restarting" not in text
                and "Keeping live" not in text
                and "Saving viewport" not in text
            ):
                break
            page.wait_for_timeout(250)
        text1 = root.inner_text()
        w1 = root.locator("#mobile-fullscreen-viewport-width").input_value()
        h1 = root.locator("#mobile-fullscreen-viewport-height").input_value()
        check(
            "first Phone fit settled",
            "Saved" in text1 or "Already matches - no restart" in text1,
            text1[:120],
        )
        check(
            "first Phone fit size 390x844",
            w1 == str(W) and h1 == str(H),
            f"{w1}x{h1}",
        )

        mutate["update"] = mutate["stop"] = mutate["launch"] = 0
        tracking["enabled"] = True
        phone_fit.first.click()
        for _ in range(40):
            text = root.inner_text()
            if (
                ("Saved" in text or "Already matches - no restart" in text)
                and "Restarting" not in text
                and "Keeping live" not in text
                and "Saving viewport" not in text
            ):
                break
            page.wait_for_timeout(250)
        tracking["enabled"] = False
        page.wait_for_timeout(300)

        text2 = root.inner_text()
        w2 = root.locator("#mobile-fullscreen-viewport-width").input_value()
        h2 = root.locator("#mobile-fullscreen-viewport-height").input_value()
        canvas = page.locator(".mobile-browser-content canvas").count()
        claims_restart = (
            "Restarting live browser" in text2
            or "Restarts live browser to apply" in text2
        )
        check(
            "re-tap settled",
            "Saved" in text2 or "Already matches - no restart" in text2,
            text2[:160],
        )
        check("re-tap no restart claim", not claims_restart, text2[:160])
        check(
            "re-tap size still 390x844",
            w2 == str(W) and h2 == str(H),
            f"{w2}x{h2}",
        )
        check(
            "re-tap no update/stop/launch traffic",
            mutate["update"] == 0
            and mutate["stop"] == 0
            and mutate["launch"] == 0,
            dict(mutate),
        )
        check("re-tap single canvas (content)", canvas == 1, canvas)

        page.screenshot(path=str(shot), full_page=False)
        digest = hashlib.sha256(shot.read_bytes()).hexdigest()
        check(
            "screenshot written",
            shot.is_file(),
            {"path": str(shot), "sha256": digest, "bytes": shot.stat().st_size},
        )
        print("SHA-256", digest)
        browser.close()

    report = {
        "outcome": "PASS",
        "base_url": BASE,
        "profile_id": PROFILE_ID,
        "viewport": f"{W}x{H}",
        "mutate_on_retap": mutate,
        "checks": checks,
        "screenshot": str(shot),
        "sha256": hashlib.sha256(shot.read_bytes()).hexdigest(),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print("REPORT OK", len(checks), "checks ->", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
