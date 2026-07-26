#!/usr/bin/env python3
"""Run-scoped CloakBrowser MCP server for ACPX agents.

The server exposes only bounded browser interactions for the profile and task
run granted by the Manager. It cannot list other profiles, reveal credentials,
acquire arbitrary leases, execute shell commands, or connect to raw external
CDP endpoints.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from backend.origin_policy import is_top_level_origin_allowed
from scripts.cbm_browser_ctl import (
    MAX_TEXT_CHARS,
    BrowserCtlError,
    _import_playwright_sync,
    _safe_page_title,
    _safe_page_url,
    redact_text,
    select_page,
    validate_http_url,
    validate_profile_id,
    validate_selector,
    validate_text,
)


@dataclass(frozen=True)
class RunContext:
    manager_url: str
    profile_id: str
    task_run_id: str
    allowed_origins: tuple[str, ...]
    capability_file: Path
    capability_token: str = field(repr=False)

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "RunContext":
        env = environment if environment is not None else os.environ
        manager_url = str(env.get("CBM_MANAGER_URL") or "").strip().rstrip("/")
        if not manager_url.startswith(("http://", "https://")):
            raise ValueError("CBM_MANAGER_URL must be an http/https URL")
        profile_id = validate_profile_id(str(env.get("CBM_PROFILE_ID") or ""))
        task_run_id = str(env.get("CBM_TASK_RUN_ID") or "").strip()
        if not task_run_id or len(task_run_id) > 128:
            raise ValueError("CBM_TASK_RUN_ID is required")
        capability_file = Path(str(env.get("CBM_RUN_CAPABILITY_FILE") or ""))
        if not capability_file.is_absolute() or not capability_file.is_file():
            raise ValueError("CBM_RUN_CAPABILITY_FILE must be an existing absolute file")
        if capability_file.stat().st_mode & 0o077:
            raise ValueError("CBM_RUN_CAPABILITY_FILE must use mode 0600")
        token = capability_file.read_text(encoding="utf-8").strip()
        if not token or any(ch.isspace() for ch in token) or not token.isprintable():
            raise ValueError("run capability is invalid")
        try:
            raw_origins = json.loads(str(env.get("CBM_ALLOWED_ORIGINS") or "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("CBM_ALLOWED_ORIGINS must be a JSON array") from exc
        if not isinstance(raw_origins, list) or any(
            not isinstance(item, str) for item in raw_origins
        ):
            raise ValueError("CBM_ALLOWED_ORIGINS must be a JSON string array")
        return cls(
            manager_url=manager_url,
            profile_id=profile_id,
            task_run_id=task_run_id,
            allowed_origins=tuple(raw_origins),
            capability_file=capability_file.resolve(),
            capability_token=token,
        )


class CbmMcpController:
    """Pure bounded browser tool implementation used by FastMCP handlers."""

    def __init__(
        self,
        context: RunContext,
        *,
        connect_over_cdp: Callable[..., Any] | None = None,
    ) -> None:
        self.context = context
        self._connect_over_cdp = connect_over_cdp

    def _require_allowed_url(self, url: str) -> None:
        if not self.context.allowed_origins:
            return
        from urllib.parse import urlparse

        parts = urlparse(str(url or ""))
        candidate_origin = f"{parts.scheme}://{parts.netloc}"
        if not is_top_level_origin_allowed(
            candidate_origin, self.context.allowed_origins
        ):
            raise BrowserCtlError(
                "navigation_blocked", "URL is outside the run allowed origin set"
            )

    def _require_allowed_page(self, page: Any) -> None:
        self._require_allowed_url(str(getattr(page, "url", "") or ""))

    @contextmanager
    def _page(self) -> Iterator[Any]:
        endpoint = (
            f"{self.context.manager_url}/api/profiles/{self.context.profile_id}/cdp"
        )
        headers = {"Authorization": f"Bearer {self.context.capability_token}"}
        browser = None
        playwright_cm = None
        try:
            if self._connect_over_cdp is not None:
                browser = self._connect_over_cdp(endpoint, headers=headers)
            else:
                sync_playwright = _import_playwright_sync()
                playwright_cm = sync_playwright()
                playwright = playwright_cm.__enter__()
                browser = playwright.chromium.connect_over_cdp(endpoint, headers=headers)
            yield select_page(browser)
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright_cm is not None:
                try:
                    playwright_cm.__exit__(None, None, None)
                except Exception:
                    pass

    def inspect(self) -> dict[str, Any]:
        with self._page() as page:
            self._require_allowed_page(page)
            return {
                "ok": True,
                "command": "inspect",
                "profile_id": self.context.profile_id,
                "url": _safe_page_url(page),
                "title": _safe_page_title(page),
            }

    def navigate(self, url: str) -> dict[str, Any]:
        safe_url = validate_http_url(url)
        self._require_allowed_url(safe_url)
        with self._page() as page:
            page.goto(safe_url, wait_until="domcontentloaded")
            self._require_allowed_page(page)
            return {
                "ok": True,
                "command": "navigate",
                "profile_id": self.context.profile_id,
                "url": _safe_page_url(page),
                "title": _safe_page_title(page),
            }

    def click(self, selector: str) -> dict[str, Any]:
        safe_selector = validate_selector(selector)
        with self._page() as page:
            self._require_allowed_page(page)
            page.click(safe_selector)
            self._require_allowed_page(page)
            return {
                "ok": True,
                "command": "click",
                "profile_id": self.context.profile_id,
                "selector": safe_selector,
                "url": _safe_page_url(page),
            }

    def fill(self, selector: str, text: str) -> dict[str, Any]:
        safe_selector = validate_selector(selector)
        safe_text = validate_text(text)
        with self._page() as page:
            self._require_allowed_page(page)
            page.fill(safe_selector, safe_text)
            self._require_allowed_page(page)
            return {
                "ok": True,
                "command": "fill",
                "profile_id": self.context.profile_id,
                "selector": safe_selector,
                "text_length": len(safe_text),
                "url": _safe_page_url(page),
            }

    def read_text(self, selector: str | None = None) -> dict[str, Any]:
        safe_selector = validate_selector(selector) if selector else None
        with self._page() as page:
            self._require_allowed_page(page)
            locator = page.locator(safe_selector or "body")
            visible_text = redact_text(str(locator.inner_text()))[:MAX_TEXT_CHARS]
            return {
                "ok": True,
                "command": "read_text",
                "profile_id": self.context.profile_id,
                "selector": safe_selector,
                "text": visible_text,
                "url": _safe_page_url(page),
            }


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "Official MCP Python SDK is missing; install mcp>=1.27,<2"
        ) from exc
    return FastMCP


def build_server(controller: CbmMcpController | None = None):
    """Build the official FastMCP stdio server with an explicit tool allowlist."""
    FastMCP = _import_fastmcp()
    ctl = controller or CbmMcpController(RunContext.from_environment())
    server = FastMCP(
        "CloakBrowser Run Control",
        instructions=(
            "Control only the Manager-granted browser profile for this task run. "
            "Never request or reveal credentials, tokens, cookies, raw CDP, or shell access."
        ),
        json_response=True,
    )

    @server.tool()
    def browser_inspect() -> dict[str, Any]:
        """Return the current managed tab URL and title."""
        return ctl.inspect()

    @server.tool()
    def browser_navigate(url: str) -> dict[str, Any]:
        """Navigate within the exact Manager-approved origin set."""
        return ctl.navigate(url)

    @server.tool()
    def browser_click(selector: str) -> dict[str, Any]:
        """Click one bounded Playwright selector in the managed tab."""
        return ctl.click(selector)

    @server.tool()
    def browser_fill(selector: str, text: str) -> dict[str, Any]:
        """Fill one bounded selector without returning the submitted text."""
        return ctl.fill(selector, text)

    @server.tool()
    def browser_read_text(selector: str | None = None) -> dict[str, Any]:
        """Read bounded visible text, never raw HTML or DOM snapshots."""
        return ctl.read_text(selector)

    return server


def main() -> int:
    server = build_server()
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
