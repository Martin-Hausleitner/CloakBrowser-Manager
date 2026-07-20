#!/usr/bin/env python3
"""Exercise the mobile manager shell with Safari/WebKit through SafariDriver.

This complements ``mobile_ui_gate.py``: the existing gate performs the full
live-VNC/RFB/CDP flow in Chromium, while this one verifies that a real WebKit
browser can render, navigate, and screenshot the mobile shell.  It never
changes Safari's remote-automation preference.  If that preference is off,
the report is marked ``blocked`` with the Safari-provided reason instead of
pretending the iOS/WebKit gate passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_DRIVER_URL = "http://127.0.0.1:4444"


class GateError(RuntimeError):
    """A reproducible gate failure that is safe to report."""


class SafariAutomationDisabled(GateError):
    """Safari requires an explicit user-controlled Remote Automation opt-in."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_url(value: str) -> str:
    """Drop query strings, fragments, and user info from persisted evidence."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def loopback_driver_url(value: str) -> tuple[str, int]:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GateError("--driver-url must be a plain http loopback URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise GateError("--driver-url must stay on loopback")
    if parsed.path not in {"", "/"}:
        raise GateError("--driver-url may not contain a path")
    return f"http://{parsed.netloc}", parsed.port or 80


def png_metadata(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise GateError("SafariDriver did not produce a valid PNG screenshot")
    width, height = struct.unpack(">II", payload[16:24])
    return {
        "path": str(path.resolve()),
        "width": width,
        "height": height,
        "bytes": len(payload),
    }


def add_check(report: dict[str, Any], name: str, passed: bool, evidence: Any) -> None:
    report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
    if not passed:
        raise GateError(f"{name} failed")


@dataclass
class WebDriverClient:
    base_url: str
    timeout: float

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=encoded,
            method=method,
            headers={"Content-Type": "application/json"} if encoded else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                value = json.loads(raw.decode("utf-8")).get("value") or {}
                message = str(value.get("message") or value.get("error") or f"HTTP {exc.code}")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = f"HTTP {exc.code}"
            if "Allow remote automation" in message:
                raise SafariAutomationDisabled(message) from exc
            raise GateError(message) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise GateError(f"SafariDriver is unavailable: {type(exc).__name__}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateError("SafariDriver returned a non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise GateError("SafariDriver returned an invalid response")
        return decoded

    def status(self) -> bool:
        try:
            response = self.request("GET", "/status")
        except GateError:
            return False
        return bool((response.get("value") or {}).get("ready"))

    def create_session(self) -> str:
        response = self.request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "safari",
                        "safari:automaticInspection": False,
                    }
                }
            },
        )
        value = response.get("value") or {}
        session_id = value.get("sessionId") or response.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise GateError("SafariDriver returned no WebDriver session id")
        return session_id

    def execute(self, session_id: str, script: str) -> Any:
        response = self.request(
            "POST",
            f"/session/{session_id}/execute/sync",
            {"script": script, "args": []},
        )
        return response.get("value")

    def close(self, session_id: str) -> None:
        try:
            self.request("DELETE", f"/session/{session_id}")
        except GateError:
            pass


def start_safaridriver(port: int, executable: str) -> subprocess.Popen[bytes]:
    if port <= 0 or port > 65535:
        raise GateError("SafariDriver port must be between 1 and 65535")
    return subprocess.Popen(
        [executable, "-p", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_driver(client: WebDriverClient, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.status():
            return
        time.sleep(0.25)
    raise GateError("SafariDriver did not become ready")


def parse_browser_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise GateError(f"SafariDriver returned no {label} value")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise GateError(f"SafariDriver returned invalid {label} JSON") from exc
    if not isinstance(decoded, dict):
        raise GateError(f"SafariDriver returned invalid {label} data")
    return decoded


INITIAL_STATE_SCRIPT = r"""return JSON.stringify({
  ready: Boolean(document.querySelector('.mobile-split-root')),
  browserFrame: Boolean(document.querySelector('[data-testid="mobile-browser-frame"]')),
  composer: Boolean(document.querySelector('#mobile-task-input')),
  benchmarkButton: Boolean(document.querySelector('button[aria-label="Streaming benchmark results"]')),
  width: window.innerWidth,
  height: window.innerHeight,
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth
});"""

OPEN_BENCHMARKS_SCRIPT = r"""const button = document.querySelector(
  'button[aria-label="Streaming benchmark results"]'
);
if (!button) return false;
button.click();
return true;"""

BENCHMARK_STATE_SCRIPT = r"""return JSON.stringify({
  heading: document.querySelector('h1')?.textContent || null,
  cards: Array.from(document.querySelectorAll('section h2')).map((node) => node.textContent || ''),
  measured: document.body.innerText.includes('Measured'),
  notInstalled: document.body.innerText.includes('Not Installed'),
  architectureOnly: document.body.innerText.includes('Architecture Only'),
  width: window.innerWidth,
  height: window.innerHeight,
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth
});"""


def wait_for_benchmark_state(
    client: WebDriverClient,
    session_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = parse_browser_value(client.execute(session_id, BENCHMARK_STATE_SCRIPT), "benchmark state")
        if state.get("heading") == "Live streaming benchmark results":
            return state
        time.sleep(0.2)
    raise GateError("Timed out waiting for the streaming benchmark dashboard")


def run_gate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    driver_url, port = loopback_driver_url(args.driver_url)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": utc_now(),
        "engine": "Safari/WebKit via SafariDriver",
        "base_url": public_url(args.base_url),
        "viewport": {"requested_width": args.width, "requested_height": args.height},
        "checks": [],
        "status": "running",
    }
    client = WebDriverClient(driver_url, args.timeout)
    spawned: subprocess.Popen[bytes] | None = None
    session_id: str | None = None
    exit_code = 1
    try:
        if not client.status():
            if not args.start_driver:
                raise GateError("SafariDriver is not running; use --start-driver or start safaridriver on loopback")
            spawned = start_safaridriver(port, args.safaridriver)
            wait_for_driver(client, args.timeout)

        session_id = client.create_session()
        client.request(
            "POST",
            f"/session/{session_id}/window/rect",
            {"width": args.width, "height": args.height},
        )
        client.request("POST", f"/session/{session_id}/url", {"url": args.base_url})
        time.sleep(args.settle_seconds)

        initial = parse_browser_value(client.execute(session_id, INITIAL_STATE_SCRIPT), "initial mobile state")
        report["viewport"]["actual_width"] = initial.get("width")
        report["viewport"]["actual_height"] = initial.get("height")
        add_check(
            report,
            "mobile workspace shell renders in WebKit",
            bool(initial.get("ready") and initial.get("browserFrame") and initial.get("composer")),
            initial,
        )
        add_check(
            report,
            "mobile workspace has no horizontal overflow in WebKit",
            initial.get("scrollWidth") == initial.get("clientWidth"),
            initial,
        )
        opened = client.execute(session_id, OPEN_BENCHMARKS_SCRIPT)
        add_check(report, "streaming benchmark action opens", opened is True, {"opened": opened})

        benchmark = wait_for_benchmark_state(client, session_id, args.timeout)
        add_check(
            report,
            "streaming benchmark dashboard renders in WebKit",
            benchmark.get("heading") == "Live streaming benchmark results",
            benchmark,
        )
        add_check(
            report,
            "benchmark report has expected candidate coverage",
            len(benchmark.get("cards") or []) >= args.min_benchmark_cards,
            benchmark,
        )
        add_check(
            report,
            "benchmark report preserves measured and unmeasured labels",
            bool(benchmark.get("measured"))
            and bool(benchmark.get("notInstalled"))
            and bool(benchmark.get("architectureOnly")),
            benchmark,
        )
        add_check(
            report,
            "benchmark dashboard has no horizontal overflow in WebKit",
            benchmark.get("scrollWidth") == benchmark.get("clientWidth"),
            benchmark,
        )

        screenshot = client.request("GET", f"/session/{session_id}/screenshot").get("value")
        if not isinstance(screenshot, str):
            raise GateError("SafariDriver returned no screenshot")
        screenshot_path = args.output_dir / "safari-mobile-benchmark-dashboard.png"
        screenshot_path.write_bytes(base64.b64decode(screenshot))
        metadata = png_metadata(screenshot_path)
        add_check(
            report,
            "Safari/WebKit benchmark screenshot artifact",
            metadata["bytes"] > 4_096 and metadata["width"] > 0 and metadata["height"] > 0,
            metadata,
        )
        report["status"] = "passed"
        exit_code = 0
    except SafariAutomationDisabled as exc:
        report["status"] = "blocked"
        report["blocker"] = (
            "Safari Remote Automation is disabled. Enable it deliberately in Safari Settings > "
            "Developer before running this WebKit gate."
        )
        report["error"] = str(exc)
        exit_code = 3
    except (GateError, OSError, ValueError) as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 1
    finally:
        if session_id:
            client.close(session_id)
        if spawned and spawned.poll() is None:
            spawned.terminate()
            try:
                spawned.wait(timeout=2)
            except subprocess.TimeoutExpired:
                spawned.kill()
                spawned.wait(timeout=2)

    report["finished_at"] = utc_now()
    report["passed"] = report["status"] == "passed"
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed": report["passed"],
                "report": str(report_path.resolve()),
                "checks": [{"name": item["name"], "passed": item["passed"]} for item in report["checks"]],
                "blocker": report.get("blocker"),
            },
            ensure_ascii=False,
        )
    )
    return report, exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Local Manager URL to validate.")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/mobile-webkit-gate"))
    parser.add_argument("--driver-url", default=DEFAULT_DRIVER_URL)
    parser.add_argument("--start-driver", action="store_true", help="Start a loopback SafariDriver for this run.")
    parser.add_argument("--safaridriver", default="safaridriver", help="SafariDriver executable for --start-driver.")
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--min-benchmark-cards", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.width < 1 or args.height < 1:
        parser.error("--width and --height must be positive")
    if args.min_benchmark_cards < 1:
        parser.error("--min-benchmark-cards must be at least 1")
    if args.timeout <= 0 or args.settle_seconds < 0:
        parser.error("--timeout must be positive and --settle-seconds cannot be negative")
    return args


if __name__ == "__main__":
    _, code = run_gate(parse_args())
    sys.exit(code)
