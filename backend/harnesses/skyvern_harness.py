"""Skyvern harness — route Skyvern automation through CloakBrowser CDP.

Skyvern (https://github.com/Skyvern-AI/skyvern) is AGPL-3.0. This module is MIT
adapter code only: it never vendors Skyvern sources. When the optional ``skyvern``
package is installed, tasks attach to a Manager profile via CDP so fingerprint,
proxy, and session state stay on the CloakBrowser side.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("cloakbrowser.manager.skyvern_harness")

SKYVERN_LICENSE = "AGPL-3.0"
SKYVERN_REPO = "https://github.com/Skyvern-AI/skyvern"
HARNESS_ID = "skyvern"
CDP_ROUTING = "cloakbrowser-manager"


@dataclass(frozen=True)
class SkyvernCdpTarget:
    profile_id: str
    browser_address: str
    headers: dict[str, str]
    direct_browser_address: str | None = None


def skyvern_importable() -> bool:
    return importlib.util.find_spec("skyvern") is not None


def llm_configured() -> bool:
    """True when an OpenAI-compatible endpoint or Skyvern cloud key is present."""
    if os.environ.get("SKYVERN_API_KEY"):
        return True
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("ENABLE_OPENAI"):
        return True
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ENABLE_ANTHROPIC"):
        return True
    # Local OpenAI-compatible proxies used on this fleet.
    if os.environ.get("OPENAI_BASE_URL") and (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("CLI_PROXY_API_KEY")
    ):
        return True
    if os.environ.get("CLI_PROXY_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        return True
    return bool(os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"))


def build_cdp_browser_address(
    *,
    base_url: str,
    profile_id: str,
) -> str:
    """Return absolute Manager CDP HTTP URL for Skyvern ``browser_address``."""
    base = base_url.rstrip("/")
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"base_url must be absolute, got {base_url!r}")
    return f"{base}/api/profiles/{profile_id}/cdp"


def build_cdp_connect_headers(auth_token: str | None) -> dict[str, str]:
    if not auth_token:
        return {}
    return {"Authorization": f"Bearer {auth_token}"}


def bind_profile_cdp(
    *,
    base_url: str,
    profile_id: str,
    profile_running: bool,
    auth_token: str | None = None,
    direct_cdp_port: int | None = None,
) -> SkyvernCdpTarget:
    if not profile_running:
        raise RuntimeError(
            f"Profile {profile_id} is not running; launch it before binding Skyvern."
        )
    direct = (
        f"http://127.0.0.1:{int(direct_cdp_port)}" if direct_cdp_port else None
    )
    return SkyvernCdpTarget(
        profile_id=profile_id,
        browser_address=build_cdp_browser_address(
            base_url=base_url, profile_id=profile_id
        ),
        headers=build_cdp_connect_headers(auth_token),
        direct_browser_address=direct,
    )


def preferred_browser_address(target: SkyvernCdpTarget, *, prefer_direct: bool = True) -> str:
    """Prefer co-located CloakBrowser CDP (still cloaked); else Manager proxy URL."""
    if prefer_direct and target.direct_browser_address:
        return target.direct_browser_address
    return target.browser_address


def capabilities() -> dict[str, Any]:
    installed = skyvern_importable()
    has_llm = llm_configured()
    if not installed:
        status = "unavailable"
    elif not has_llm:
        status = "degraded"
    else:
        status = "ready"
    return {
        "harness": HARNESS_ID,
        "status": status,
        "skyvern_installed": installed,
        "llm_configured": has_llm,
        "cdp_routing": CDP_ROUTING,
        "skyvern_license": SKYVERN_LICENSE,
        "skyvern_repo": SKYVERN_REPO,
        "capabilities": {
            "connect_over_cdp": installed,
            "run_task": installed and has_llm,
            "navigate_screenshot": installed,
        },
        "notes": (
            "Skyvern remains an optional AGPL-3.0 dependency. "
            "Automation attaches to CloakBrowser profiles via Manager CDP; "
            "do not launch vanilla Chromium for harness runs."
        ),
    }


async def run_cdp_navigate_proof(
    *,
    browser_address: str,
    headers: dict[str, str] | None,
    url: str,
    screenshot_path: Path,
) -> dict[str, Any]:
    """Connect with Skyvern's CDP helper (or Playwright fallback) and navigate.

    Prefer Skyvern's ``connect_to_browser_over_cdp`` so the proof exercises the
    real Skyvern browser attachment path. If Skyvern is missing, raise.
    """
    if not skyvern_importable():
        raise RuntimeError("skyvern package is not installed")

    screenshot_path = Path(screenshot_path)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    # Skyvern's public helper:
    #   Skyvern().connect_to_browser_over_cdp(cdp_url)
    # Under the hood this is Playwright connect_over_cdp — we call that same
    # path and, when possible, wrap via Skyvern for honest harness usage.
    skyvern_error: str | None = None
    try:
        from skyvern import Skyvern
        from skyvern.library.skyvern_browser import SkyvernBrowser

        sky = None
        init_mode = None
        try:
            sky = Skyvern.local(use_in_memory_db=True)
            init_mode = "Skyvern.local"
        except Exception as local_exc:  # noqa: BLE001
            logger.info("Skyvern.local unavailable (%s); trying api_key client", local_exc)
            api_key = (
                os.environ.get("SKYVERN_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or os.environ.get("CLI_PROXY_API_KEY")
                or "local-cloak-harness"
            )
            sky = Skyvern(api_key=api_key)
            init_mode = "Skyvern(api_key)"

        # Library connect_to_browser_over_cdp() currently omits HTTP headers, so
        # authenticated Manager CDP proxies 401. Use Skyvern's Playwright driver
        # with explicit headers, then wrap as SkyvernBrowser — same docking path
        # Skyvern uses for remote CDP browsers.
        playwright = await sky._get_playwright()  # noqa: SLF001 — intentional harness bridge
        try:
            raw_browser = await playwright.chromium.connect_over_cdp(
                browser_address, headers=headers or None
            )
        except TypeError:
            raw_browser = await playwright.chromium.connect_over_cdp(browser_address)
        context = raw_browser.contexts[0] if raw_browser.contexts else await raw_browser.new_context()
        browser = SkyvernBrowser(sky, context, browser_address=browser_address)
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        title = await page.title()
        final_url = page.url
        await page.screenshot(path=str(screenshot_path), full_page=True)
        try:
            await raw_browser.close()
        except Exception:  # noqa: BLE001 — profile must stay up
            logger.debug("skyvern raw browser close skipped", exc_info=True)
        return {
            "status": "ok",
            "mode": f"{init_mode}+SkyvernBrowser.connect_over_cdp",
            "url": final_url,
            "title": title,
            "screenshot": str(screenshot_path),
            "browser_address": browser_address,
            "headers_applied": bool(headers),
        }
    except Exception as exc:  # noqa: BLE001
        skyvern_error = str(exc)
        logger.warning(
            "Skyvern CDP attach failed (%s); "
            "falling back to Playwright CDP with same address",
            skyvern_error,
        )

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(
                browser_address, headers=headers or None
            )
        except TypeError:
            browser = await pw.chromium.connect_over_cdp(browser_address)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        title = await page.title()
        final_url = page.url
        await page.screenshot(path=str(screenshot_path), full_page=True)
        await browser.close()
        return {
            "status": "ok",
            "mode": "playwright.connect_over_cdp",
            "skyvern_error": skyvern_error,
            "url": final_url,
            "title": title,
            "screenshot": str(screenshot_path),
            "browser_address": browser_address,
            "headers_applied": bool(headers),
        }


async def run_agent_task(
    *,
    browser_address: str,
    prompt: str,
    url: str | None = None,
    max_steps: int = 5,
) -> dict[str, Any]:
    if not skyvern_importable():
        return {
            "status": "blocked",
            "reason": "skyvern package is not installed",
        }
    if not llm_configured():
        return {
            "status": "blocked",
            "reason": "LLM credentials/endpoint not configured for Skyvern agent loop",
        }
    from skyvern import Skyvern

    sky = Skyvern()
    result = await sky.run_task(
        prompt=prompt,
        url=url,
        max_steps=max_steps,
        browser_address=browser_address,
        wait_for_completion=True,
        timeout=300,
    )
    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    return {"status": "ok", "mode": "skyvern.run_task", "result": payload}
