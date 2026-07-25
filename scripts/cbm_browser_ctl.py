#!/usr/bin/env python3
"""Direct CloakBrowser profile control via Manager automation leases + CDP.

Orca-launched cursor-agent / grok / codex sessions should prefer this CLI for
immediate page actions on an already-running Manager profile. It never launches
a separate browser and never prints keys, bearer tokens, or lease tokens.

Auth (same contract as cbm_agent_ctl.py):
  export CBM_BASE_URL=http://127.0.0.1:18115
  export CBM_AGENT_KEY_FILE=/home/coder/.config/cloakbrowser/orca-agent-key
  # or: export CBM_AGENT_KEY=cbm_agent_…

Examples:
  scripts/cbm_browser_ctl.py inspect --profile-id <id>
  scripts/cbm_browser_ctl.py navigate --profile-id <id> --url https://example.com
  scripts/cbm_browser_ctl.py click --profile-id <id> --selector "text=Sign in"
  scripts/cbm_browser_ctl.py fill --profile-id <id> --selector "input[name=q]" --text "hello"
  scripts/cbm_browser_ctl.py text --profile-id <id>
  scripts/cbm_browser_ctl.py screenshot --profile-id <id> --path shot.png

Tab targeting (optional):
  --page-index N     Select page N in the first browser context (0-based)
  --page-url SUBSTR  Select the first page whose URL contains SUBSTR
  Default: last page in the first context (newest tab), then bring_to_front().

Lifecycle note: Playwright ``browser.close()`` disconnects the CDP client only.
A fresh live E2E must still verify the managed Manager profile remains running.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MAX_SELECTOR_CHARS = 500
MAX_TEXT_CHARS = 8_000
MAX_URL_CHARS = 2_048
MAX_PROFILE_ID_CHARS = 128
DEFAULT_ARTIFACT_ROOT = "/tmp/cbm-browser-artifacts"
LEASE_HEADER = "X-CBM-Automation-Lease"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
HEARTBEAT_JOIN_TIMEOUT_SECONDS = 5.0
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_QUERY_KEYS = re.compile(
    r"(?i)^(token|access_token|refresh_token|id_token|auth|authorization|"
    r"api[_-]?key|key|secret|password|passwd|lease|session)$"
)
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-+=/]{8,})"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_-])(?:cbm_agent_|cbm_run_|cbm_worker_|cbm_lease_|cbm_session_)[A-Za-z0-9_-]+"
    ),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|token)\s*[:=]\s*([^\s\"']+)"),
    re.compile(r"(?i)(https?://)([^:@\s/]+):([^@\s/]+)@"),
)


class BrowserCtlError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class BrowserCtlLifecycleIssue(BrowserCtlError):
    """Action evidence preserved, but lease lifecycle failed (nonzero exit)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        result: dict[str, Any],
        exit_code: int = 2,
    ):
        super().__init__(code, message, exit_code=exit_code)
        self.result = result


def redact_text(value: str) -> str:
    """Strip credential/token material from free-form text (never echo secrets)."""
    text = value or ""
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups >= 2 and "https?" in pattern.pattern:
            text = pattern.sub(r"\1[redacted]:[redacted]@", text)
        elif pattern.groups >= 2:
            text = pattern.sub(r"\1[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    return text


def redact_url(url: str) -> str:
    """Strip userinfo and token-like query values from URLs for safe output."""
    raw = (url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return redact_text(raw)
    # Drop credentials from netloc / userinfo.
    host = parsed.hostname or ""
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    else:
        netloc = host or parsed.netloc.split("@")[-1]
    query_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _SECRET_QUERY_KEYS.match(key) or re.search(
            r"(?i)(?:cbm_agent_|cbm_lease_|cbm_run_|bearer)", value
        ):
            query_pairs.append((key, "REDACTED"))
        else:
            query_pairs.append((key, value))
    # Do not run free-form redact_text on the rebuilt URL: token:= patterns would
    # swallow subsequent query pairs (e.g. token=[redacted]&q=ok).
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            urlencode(query_pairs),
            "",  # drop fragments that might carry tokens
        )
    )


def redact_error_message(message: str) -> str:
    return redact_text(redact_url(message))[:300]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _base_url() -> str:
    return (_env("CBM_BASE_URL", "http://127.0.0.1:18115") or "").rstrip("/")


def _read_agent_key_file(path: str) -> str:
    key_path = path.strip()
    if not key_path:
        raise SystemExit("CBM_AGENT_KEY_FILE is empty")
    try:
        raw = open(key_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise SystemExit("Unable to read CBM_AGENT_KEY_FILE") from exc
    key = raw.strip()
    if not key:
        raise SystemExit("CBM_AGENT_KEY_FILE is empty")
    return key


def _auth_header() -> dict[str, str]:
    agent_key = _env("CBM_AGENT_KEY")
    if not agent_key:
        key_file = _env("CBM_AGENT_KEY_FILE")
        if key_file:
            agent_key = _read_agent_key_file(key_file)
    if agent_key:
        return {"Authorization": f"Bearer {agent_key}"}
    admin = _env("CBM_ADMIN_TOKEN") or _env("AUTH_TOKEN")
    if admin:
        return {"Authorization": f"Bearer {admin}"}
    raise SystemExit(
        "Set CBM_AGENT_KEY or CBM_AGENT_KEY_FILE (preferred) or CBM_ADMIN_TOKEN/AUTH_TOKEN."
    )


def validate_profile_id(profile_id: str) -> str:
    """Require one safe path segment (no slash/query/fragment/whitespace/controls)."""
    raw = profile_id if isinstance(profile_id, str) else ""
    if not raw or len(raw) > MAX_PROFILE_ID_CHARS:
        raise BrowserCtlError("invalid_profile_id", "profile_id is missing or too long")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise BrowserCtlError(
            "invalid_profile_id",
            "profile_id must not contain whitespace or control characters",
        )
    for banned in ("/", "\\", "?", "#", "%", "&", "=", ":", "@"):
        if banned in raw:
            raise BrowserCtlError(
                "invalid_profile_id",
                "profile_id must be a single safe path segment",
            )
    if not PROFILE_ID_RE.fullmatch(raw):
        raise BrowserCtlError(
            "invalid_profile_id",
            "profile_id contains unsupported characters",
        )
    return raw


def profile_api_path(profile_id: str, *parts: str) -> str:
    """Build `/api/profiles/{id}/…` with a quoted single path segment."""
    safe = validate_profile_id(profile_id)
    quoted = urllib.parse.quote(safe, safe="-_.")
    suffix = "".join(f"/{urllib.parse.quote(part, safe='-_.')}" for part in parts)
    return f"/api/profiles/{quoted}{suffix}"


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expect_json: bool = True,
) -> Any:
    if "?" in path or "#" in path:
        raise BrowserCtlError("invalid_path", "API path must not contain query or fragment")
    url = f"{_base_url()}{path}"
    data = None
    req_headers = {
        "Accept": "application/json",
        **_auth_header(),
        **(headers or {}),
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return {"ok": True, "status": resp.status}
            if not expect_json:
                return raw
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _ = exc.read()  # drain; do not echo body (may contain secrets)
        raise BrowserCtlError(
            "http_error",
            redact_error_message(f"HTTP {exc.code} {method} {path}"),
            exit_code=1,
        ) from None
    except urllib.error.URLError as exc:
        raise BrowserCtlError(
            "transport_error",
            redact_error_message(f"Request failed for {_base_url()}{path}"),
            exit_code=69,
        ) from None


def validate_http_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned or len(cleaned) > MAX_URL_CHARS:
        raise BrowserCtlError("invalid_url", "URL is missing or too long")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserCtlError("invalid_url", "Only http/https navigation is allowed")
    if parsed.username or parsed.password:
        raise BrowserCtlError("invalid_url", "URLs must not include credentials")
    if not parsed.netloc:
        raise BrowserCtlError("invalid_url", "URL must include a host")
    return cleaned


def validate_selector(selector: str) -> str:
    cleaned = (selector or "").strip()
    if not cleaned:
        raise BrowserCtlError("invalid_selector", "Selector is required")
    if len(cleaned) > MAX_SELECTOR_CHARS:
        raise BrowserCtlError("invalid_selector", "Selector exceeds maximum length")
    return cleaned


def validate_text(text: str) -> str:
    if text is None:
        raise BrowserCtlError("invalid_text", "Text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise BrowserCtlError("invalid_text", "Text exceeds maximum length")
    return text


def artifact_root() -> Path:
    return Path(_env("CBM_BROWSER_ARTIFACT_ROOT", DEFAULT_ARTIFACT_ROOT) or DEFAULT_ARTIFACT_ROOT).resolve()


def validate_screenshot_path(path: str) -> Path:
    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BrowserCtlError(
            "invalid_path",
            "Screenshot path must stay under CBM_BROWSER_ARTIFACT_ROOT",
        ) from exc
    if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise BrowserCtlError("invalid_path", "Screenshot path must end in .png/.jpg/.jpeg/.webp")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@dataclass
class LeaseHandle:
    lease_id: str
    token: str
    heartbeat_interval_seconds: float


def acquire_lease(profile_id: str) -> LeaseHandle:
    body = _request("POST", profile_api_path(profile_id, "automation-leases"))
    lease_id = str(body.get("lease_id") or "")
    token = str(body.get("token") or "")
    if not lease_id or not token.startswith("cbm_lease_"):
        raise BrowserCtlError("lease_invalid", "Manager did not return a usable automation lease")
    interval_raw = body.get("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
    try:
        interval = float(interval_raw)
    except (TypeError, ValueError):
        interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    if interval <= 0:
        interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    return LeaseHandle(
        lease_id=lease_id,
        token=token,
        heartbeat_interval_seconds=interval,
    )


def heartbeat_lease(profile_id: str, lease: LeaseHandle) -> None:
    _request(
        "POST",
        profile_api_path(profile_id, "automation-leases", lease.lease_id, "heartbeat"),
        headers={LEASE_HEADER: lease.token},
        expect_json=True,
    )


def release_lease(profile_id: str, lease: LeaseHandle) -> None:
    """Release the lease; raises BrowserCtlError on failure (never silent)."""
    _request(
        "DELETE",
        profile_api_path(profile_id, "automation-leases", lease.lease_id),
        headers={LEASE_HEADER: lease.token},
        expect_json=True,
    )


class LeaseHeartbeat:
    """Background heartbeats at Manager-returned interval; stop/join before release."""

    def __init__(
        self,
        profile_id: str,
        lease: LeaseHandle,
        *,
        heartbeat_fn: Callable[[str, LeaseHandle], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._profile_id = profile_id
        self._lease = lease
        self._heartbeat_fn = heartbeat_fn or heartbeat_lease
        self._clock = clock or time.monotonic
        self._stop = threading.Event()
        self._error: BrowserCtlError | None = None
        self._thread: threading.Thread | None = None
        self._beats = 0
        self._lock = threading.Lock()

    @property
    def beats(self) -> int:
        with self._lock:
            return self._beats

    def start(self) -> LeaseHeartbeat:
        self._thread = threading.Thread(
            target=self._run,
            name="cbm-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        interval = max(0.05, float(self._lease.heartbeat_interval_seconds))
        while not self._stop.wait(interval):
            try:
                self._heartbeat_fn(self._profile_id, self._lease)
                with self._lock:
                    self._beats += 1
            except BrowserCtlError as exc:
                self._error = BrowserCtlError(
                    "lease_heartbeat_failed",
                    redact_error_message(exc.message),
                    exit_code=1,
                )
                self._stop.set()
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._error = BrowserCtlError(
                    "lease_heartbeat_failed",
                    redact_error_message(str(exc)),
                    exit_code=1,
                )
                self._stop.set()
                return

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def stop_and_join(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=HEARTBEAT_JOIN_TIMEOUT_SECONDS)
        self._thread = None


def _import_playwright_sync():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserCtlError(
            "playwright_missing",
            "Playwright is not installed in this environment; install repo/VCVM Playwright to use direct control",
            exit_code=69,
        ) from exc
    return sync_playwright


def cdp_endpoint(profile_id: str) -> str:
    return f"{_base_url()}{profile_api_path(profile_id, 'cdp')}"


def cdp_headers(lease_token: str) -> dict[str, str]:
    headers = dict(_auth_header())
    headers[LEASE_HEADER] = lease_token
    return headers


def select_page(
    browser: Any,
    *,
    page_index: int | None = None,
    page_url: str | None = None,
) -> Any:
    """Pick a page deterministically; default to last page in first context."""
    contexts = list(getattr(browser, "contexts", []) or [])
    if not contexts:
        context = browser.new_context()
        page = context.new_page()
        _bring_to_front(page)
        return page

    context = contexts[0]
    pages = list(getattr(context, "pages", []) or [])

    if page_url is not None:
        needle = page_url.strip()
        if not needle:
            raise BrowserCtlError("invalid_page_url", "page-url filter must not be empty")
        matches = [p for p in pages if needle in str(getattr(p, "url", "") or "")]
        if not matches:
            raise BrowserCtlError("page_not_found", "No page matched --page-url")
        page = matches[0]
        _bring_to_front(page)
        return page

    if page_index is not None:
        if page_index < 0 or page_index >= len(pages):
            raise BrowserCtlError("page_not_found", "page-index is out of range")
        page = pages[page_index]
        _bring_to_front(page)
        return page

    if pages:
        page = pages[-1]  # newest / last tab in first context
        _bring_to_front(page)
        return page

    page = context.new_page()
    _bring_to_front(page)
    return page


def _bring_to_front(page: Any) -> None:
    bring = getattr(page, "bring_to_front", None)
    if callable(bring):
        bring()


@dataclass
class LeaseSession:
    page: Any
    warnings: list[dict[str, str]] = field(default_factory=list)
    browser_closed: bool = False
    heartbeat_beats: int = 0


@contextmanager
def leased_page(
    profile_id: str,
    *,
    page_index: int | None = None,
    page_url: str | None = None,
    connect_over_cdp: Callable[..., Any] | None = None,
    heartbeat_fn: Callable[[str, LeaseHandle], None] | None = None,
) -> Iterator[LeaseSession]:
    """Acquire one lease, heartbeat during work, disconnect CDP client, release."""
    safe_profile = validate_profile_id(profile_id)
    lease = acquire_lease(safe_profile)
    browser = None
    playwright_cm = None
    session = LeaseSession(page=None)
    heartbeat = LeaseHeartbeat(
        safe_profile,
        lease,
        heartbeat_fn=heartbeat_fn,
    ).start()
    action_error: BaseException | None = None
    try:
        headers = cdp_headers(lease.token)
        endpoint = cdp_endpoint(safe_profile)
        if connect_over_cdp is not None:
            browser = connect_over_cdp(endpoint, headers=headers)
        else:
            sync_playwright = _import_playwright_sync()
            playwright_cm = sync_playwright()
            playwright = playwright_cm.__enter__()
            browser = playwright.chromium.connect_over_cdp(
                endpoint,
                headers=headers,
            )
        session.page = select_page(
            browser,
            page_index=page_index,
            page_url=page_url,
        )
        yield session
        heartbeat.raise_if_failed()
    except BrowserCtlError:
        raise
    except Exception as exc:
        action_error = exc
        heartbeat.raise_if_failed()
        raise
    finally:
        heartbeat.stop_and_join()
        session.heartbeat_beats = heartbeat.beats
        # browser.close() disconnects this CDP client only — it must not stop the
        # Manager-managed profile browser. Live E2E must still verify the profile
        # remains running after direct-control commands.
        if browser is not None:
            try:
                browser.close()
                session.browser_closed = True
            except Exception:
                session.browser_closed = True
        if playwright_cm is not None:
            try:
                playwright_cm.__exit__(None, None, None)
            except Exception:
                pass
        try:
            release_lease(safe_profile, lease)
        except BrowserCtlError as release_exc:
            session.warnings.append(
                {
                    "code": "lease_release_failed",
                    "message": redact_error_message(release_exc.message),
                }
            )
        if action_error is None:
            heartbeat.raise_if_failed()


def _safe_page_url(page: Any) -> str:
    try:
        return redact_url(str(getattr(page, "url", "") or ""))
    except Exception:
        return ""


def _safe_page_title(page: Any) -> str:
    try:
        return redact_text(str(page.title()))
    except Exception as exc:
        raise BrowserCtlError(
            "page_error",
            redact_error_message(str(exc)),
        ) from None


def _finalize_payload(payload: dict[str, Any], session: LeaseSession) -> dict[str, Any]:
    clean = dict(payload)
    if "url" in clean and isinstance(clean["url"], str):
        clean["url"] = redact_url(clean["url"])
    if "title" in clean and isinstance(clean["title"], str):
        clean["title"] = redact_text(clean["title"])
    if "text" in clean and isinstance(clean["text"], str):
        clean["text"] = redact_text(clean["text"])
    if session.warnings:
        raise BrowserCtlLifecycleIssue(
            "lease_release_failed",
            session.warnings[0]["message"],
            result={**clean, "warnings": list(session.warnings)},
        )
    return clean


def _page_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "page_index": getattr(args, "page_index", None),
        "page_url": getattr(args, "page_url", None),
    }


def _emit(payload: dict[str, Any]) -> None:
    blocked = {
        "token",
        "lease_token",
        "authorization",
        "api_key",
        "cbm_agent_key",
        "headers",
    }
    clean = {key: value for key, value in payload.items() if key.lower() not in blocked}
    printed = json.dumps(clean, indent=2, sort_keys=True)
    print(redact_text(printed))


def cmd_inspect(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        payload = {
            "ok": True,
            "command": "inspect",
            "profile_id": validate_profile_id(args.profile_id),
            "url": _safe_page_url(session.page),
            "title": _safe_page_title(session.page),
        }
    return _finalize_payload(payload, session)


def cmd_navigate(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    url = validate_http_url(args.url)
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        session.page.goto(url, wait_until="domcontentloaded")
        payload = {
            "ok": True,
            "command": "navigate",
            "profile_id": validate_profile_id(args.profile_id),
            "url": _safe_page_url(session.page),
            "title": _safe_page_title(session.page),
        }
    return _finalize_payload(payload, session)


def cmd_click(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    selector = validate_selector(args.selector)
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        session.page.click(selector)
        payload = {
            "ok": True,
            "command": "click",
            "profile_id": validate_profile_id(args.profile_id),
            "selector": selector,
            "url": _safe_page_url(session.page),
        }
    return _finalize_payload(payload, session)


def cmd_fill(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    selector = validate_selector(args.selector)
    text = validate_text(args.text)
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        session.page.fill(selector, text)
        payload = {
            "ok": True,
            "command": "fill",
            "profile_id": validate_profile_id(args.profile_id),
            "selector": selector,
            "text_length": len(text),
            "url": _safe_page_url(session.page),
        }
    return _finalize_payload(payload, session)


def cmd_text(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    selector = validate_selector(args.selector) if args.selector else None
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        if selector:
            value = session.page.locator(selector).inner_text()
        else:
            value = session.page.locator("body").inner_text()
        payload = {
            "ok": True,
            "command": "text",
            "profile_id": validate_profile_id(args.profile_id),
            "selector": selector,
            "text": value,
            "url": _safe_page_url(session.page),
        }
    return _finalize_payload(payload, session)


def cmd_screenshot(args: argparse.Namespace, *, connect_over_cdp=None, heartbeat_fn=None) -> dict[str, Any]:
    path = validate_screenshot_path(args.path)
    with leased_page(
        args.profile_id,
        connect_over_cdp=connect_over_cdp,
        heartbeat_fn=heartbeat_fn,
        **_page_kwargs(args),
    ) as session:
        session.page.screenshot(path=str(path), full_page=bool(args.full_page))
        payload = {
            "ok": True,
            "command": "screenshot",
            "profile_id": validate_profile_id(args.profile_id),
            "path": str(path),
            "url": _safe_page_url(session.page),
        }
    return _finalize_payload(payload, session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--profile-id", required=True, help="Existing CloakBrowser profile id")
        p.add_argument(
            "--page-index",
            type=int,
            default=None,
            help="0-based page index in the first browser context",
        )
        p.add_argument(
            "--page-url",
            default=None,
            help="Select first page whose URL contains this substring",
        )

    p_inspect = sub.add_parser("inspect", help="Current page URL and title")
    add_common(p_inspect)
    p_inspect.set_defaults(func=cmd_inspect)

    p_nav = sub.add_parser("navigate", help="Navigate to an http/https URL")
    add_common(p_nav)
    p_nav.add_argument("--url", required=True)
    p_nav.set_defaults(func=cmd_navigate)

    p_click = sub.add_parser("click", help="Click a selector")
    add_common(p_click)
    p_click.add_argument("--selector", required=True)
    p_click.set_defaults(func=cmd_click)

    p_fill = sub.add_parser("fill", help="Fill a selector with text")
    add_common(p_fill)
    p_fill.add_argument("--selector", required=True)
    p_fill.add_argument("--text", required=True)
    p_fill.set_defaults(func=cmd_fill)

    p_text = sub.add_parser("text", help="Read visible text")
    add_common(p_text)
    p_text.add_argument("--selector", default=None)
    p_text.set_defaults(func=cmd_text)

    p_shot = sub.add_parser("screenshot", help="Capture a screenshot under the artifact root")
    add_common(p_shot)
    p_shot.add_argument("--path", required=True, help="Relative or absolute path under artifact root")
    p_shot.add_argument("--full-page", action="store_true")
    p_shot.set_defaults(func=cmd_screenshot)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
        _emit(payload)
        return 0
    except BrowserCtlLifecycleIssue as exc:
        out = dict(exc.result)
        out["ok"] = False
        if "warnings" not in out:
            out["warnings"] = [{"code": exc.code, "message": exc.message}]
        if "error" not in out:
            out["error"] = {"code": exc.code, "message": exc.message}
        _emit(out)
        return exc.exit_code
    except BrowserCtlError as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": redact_error_message(exc.message),
                },
            }
        )
        return exc.exit_code
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - unexpected runtime failures
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": redact_error_message(str(exc)),
                },
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
