#!/usr/bin/env python3
"""Run reproducible mobile UI, UX, vision-artifact, and live VNC gates.

The runner deliberately uses the installed agent-browser CLI so it exercises the
production page in a real Chromium instance. With --profile-id it selects and,
if needed, launches that profile and requires a connected, single VNC canvas.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import urlopen


VIEWPORTS = (
    ("iphone-14-portrait", 390, 844, "vertical"),
    ("iphone-pro-max-portrait", 430, 932, "vertical"),
    ("iphone-14-landscape", 844, 390, "horizontal"),
    ("touch-tablet-portrait", 768, 1024, "vertical"),
)


class GateError(RuntimeError):
    pass


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def json_from_output(output: str) -> dict[str, Any]:
    output = output.strip()
    if not output:
        raise GateError("agent-browser returned no JSON output")
    try:
        value = json.loads(output)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise GateError("agent-browser output did not contain a JSON object")


@dataclass
class AgentBrowser:
    session: str
    timeout: float
    init_script: Path

    def run(self, *args: str, check: bool = True, json_output: bool = True) -> Any:
        command = [
            "agent-browser",
            "--session",
            self.session,
            "--headed",
            "false",
            "--init-script",
            str(self.init_script),
        ]
        if json_output:
            command.append("--json")
        command.extend(args)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown error").strip()
            raise GateError(f"agent-browser {' '.join(args[:2])} failed: {message[-800:]}")
        if not json_output:
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        payload = json_from_output(completed.stdout)
        if check and not payload.get("success", True):
            raise GateError(str(payload.get("error") or "agent-browser command failed"))
        return payload

    def eval(self, script: str) -> Any:
        payload = self.run("eval", script)
        return payload.get("data", {}).get("result")

    def wait_for(self, script: str, label: str, timeout: float | None = None) -> Any:
        deadline = time.monotonic() + (timeout or self.timeout)
        last_value: Any = None
        while time.monotonic() < deadline:
            try:
                last_value = self.eval(script)
            except (GateError, subprocess.TimeoutExpired):
                last_value = None
            if last_value:
                return last_value
            time.sleep(0.4)
        raise GateError(f"Timed out waiting for {label}; last value: {last_value!r}")


def png_metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise GateError(f"Screenshot is not a valid PNG: {path}")
    width, height = struct.unpack(">II", data[16:24])
    return {"path": str(path.resolve()), "width": width, "height": height, "bytes": len(data)}


def add_check(result: dict[str, Any], name: str, passed: bool, evidence: Any) -> None:
    result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
    if not passed:
        raise GateError(f"{name} failed: {evidence}")


STRUCTURE_SCRIPT = r"""(() => {
  const root = document.querySelector('.mobile-split-root');
  const live = document.querySelector('.mobile-live-pane');
  const controls = document.querySelector('.mobile-control-pane');
  const frame = document.querySelector('[data-testid="mobile-browser-frame"]');
  const chat = document.querySelector('[aria-label="Chat history"]');
  const composer = document.querySelector('#mobile-task-input');
  const required = [root, live, controls, frame, chat, composer];
  const rect = (node) => node ? node.getBoundingClientRect().toJSON() : null;
  return {
    ready: required.every(Boolean),
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollWidth: document.scrollingElement?.scrollWidth ?? 0,
    root: rect(root),
    live: rect(live),
    controls: rect(controls),
    frame: rect(frame),
    chat: rect(chat),
    composer: rect(composer),
    hasAttach: !!document.querySelector('button[aria-label="Attach files"]'),
    hasRunSettings: !!document.querySelector('button[aria-label="Run settings"]'),
    hasModel: !!document.querySelector('select[aria-label="Select demo model"], select.mobile-composer-select'),
    hasRunTask: !!document.querySelector('button[aria-label="Run task"]'),
  };
})()"""


TOUCH_TARGET_SCRIPT = r"""(() => {
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 1 && rect.height > 1;
  };
  const elements = [...document.querySelectorAll('button, select, textarea, input')]
    .filter((element) => !['hidden', 'file', 'checkbox', 'radio'].includes(element.type || ''))
    .filter(visible);
  const controls = elements.map((element) => {
    const rect = element.getBoundingClientRect();
    return {
      label: element.getAttribute('aria-label') || element.title || element.textContent?.trim().slice(0, 60) || element.tagName,
      tag: element.tagName,
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10,
    };
  });
  return {count: controls.length, offenders: controls.filter((item) => item.width < 44 || item.height < 44)};
})()"""


CANVAS_SCRIPT = r"""(() => {
  const canvases = [...document.querySelectorAll('.mobile-browser-content canvas')];
  const host = document.querySelector('[data-vnc-layout]');
  const hostRect = host?.getBoundingClientRect();
  return {
    connected: document.body.innerText.includes('Connected'),
    count: canvases.length,
    host: hostRect ? {width: hostRect.width, height: hostRect.height} : null,
    canvases: canvases.map((canvas) => {
      const rect = canvas.getBoundingClientRect();
      return {cssWidth: rect.width, cssHeight: rect.height, width: canvas.width, height: canvas.height};
    }),
  };
})()"""


def take_screenshot(
    browser: AgentBrowser,
    result: dict[str, Any],
    output_dir: Path,
    viewport_name: str,
    state: str,
    expected_width: int,
    expected_height: int,
) -> None:
    path = output_dir / f"{viewport_name}-{state}.png"
    browser.run("screenshot", str(path))
    metadata = png_metadata(path)
    add_check(
        result,
        f"vision artifact {state}",
        metadata["width"] == expected_width
        and metadata["height"] == expected_height
        and metadata["bytes"] >= 10_000,
        metadata,
    )
    result["screenshots"].append(metadata)


def select_and_connect(
    browser: AgentBrowser,
    result: dict[str, Any],
    profile_id: str,
    timeout: float,
) -> None:
    browser.run("select", "select.mobile-select", profile_id)
    browser.wait_for(
        f"document.querySelector('select.mobile-select')?.value === {json.dumps(profile_id)}",
        "profile selection",
        10,
    )
    launch_state = browser.eval(r"""(() => {
      const buttons = [...document.querySelectorAll('.mobile-toolbar button')];
      return {
        connected: document.body.innerText.includes('Connected'),
        hasLaunch: buttons.some((button) => button.textContent?.trim() === 'Launch' && !button.disabled),
      };
    })()""")
    if launch_state.get("hasLaunch") and not launch_state.get("connected"):
        clicked = browser.eval(r"""(() => {
          const button = [...document.querySelectorAll('.mobile-toolbar button')]
            .find((candidate) => candidate.textContent?.trim() === 'Launch' && !candidate.disabled);
          if (!button) return false;
          button.click();
          return true;
        })()""")
        add_check(result, "launch action available", bool(clicked), launch_state)

    started = time.monotonic()
    browser.wait_for("document.body.innerText.includes('Connected')", "live VNC connection", timeout)
    result["time_to_connected_ms"] = round((time.monotonic() - started) * 1000, 1)
    canvas = browser.eval(CANVAS_SCRIPT)
    add_check(result, "live VNC connected", bool(canvas.get("connected")), canvas)
    add_check(result, "exactly one live VNC canvas", canvas.get("count") == 1, canvas)


def current_remote_pages(base_url: str, profile_id: str, timeout: float) -> list[dict[str, Any]]:
    endpoint = urljoin(base_url.rstrip("/") + "/", f"api/profiles/{quote(profile_id)}/cdp/json/list")
    with urlopen(endpoint, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise GateError("CDP page list did not return a list")
    return [page for page in payload if page.get("type") == "page"]


def current_remote_clipboard(base_url: str, profile_id: str, timeout: float) -> str:
    endpoint = urljoin(base_url.rstrip("/") + "/", f"api/profiles/{quote(profile_id)}/clipboard")
    with urlopen(endpoint, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise GateError("Profile clipboard endpoint did not return text")
    return payload["text"]


def manual_remote_paste(
    browser: AgentBrowser,
    result: dict[str, Any],
    base_url: str,
    profile_id: str,
    timeout: float,
) -> None:
    """Exercise the iOS-safe fallback that does not depend on navigator.clipboard."""
    marker = f"mobile-manual-paste-{int(time.time() * 1000)}"
    browser.run("click", "button[aria-label='Paste text into remote browser']")
    browser.wait_for(
        "!!document.querySelector('textarea[id^=remote-paste-]')",
        "manual remote paste field",
        5,
    )
    panel_touch = browser.eval(TOUCH_TARGET_SCRIPT)
    add_check(result, "manual paste has 44px touch targets", not panel_touch.get("offenders"), panel_touch)

    browser.run("fill", "textarea[id^=remote-paste-]", marker)
    browser.run("click", "button[aria-label='Send pasted text to remote browser']")
    browser.wait_for(
        "!document.querySelector('textarea[id^=remote-paste-]') && document.body.innerText.includes('Connected')",
        "manual remote paste completion",
        10,
    )
    actual = current_remote_clipboard(base_url, profile_id, min(timeout, 10))
    matched = actual == marker
    add_check(
        result,
        "manual device paste reaches remote profile clipboard",
        matched,
        {
            "matched": matched,
            "expected_length": len(marker),
            "actual_length": len(actual),
        },
    )


def remote_keyboard_navigation(
    browser: AgentBrowser,
    result: dict[str, Any],
    base_url: str,
    profile_id: str,
    target_url: str,
    timeout: float,
) -> None:
    target = urlsplit(target_url)
    if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password:
        raise GateError("--remote-probe-url must be an HTTP(S) URL without embedded credentials")
    if not target_url.isascii():
        raise GateError("--remote-probe-url must be ASCII so keyboard mapping is deterministic")

    before = current_remote_pages(base_url, profile_id, min(timeout, 10))
    if any(page.get("url") == target_url for page in before):
        raise GateError("Remote probe URL is already active; use a unique harmless query value")

    browser.run("click", ".mobile-browser-content canvas")
    ctrl_l_script = r"""(() => {
      const canvas = document.querySelector('.mobile-browser-content canvas');
      if (!canvas) return false;
      canvas.focus();
      const fire = (type, key, code, extra = {}) => canvas.dispatchEvent(
        new KeyboardEvent(type, {key, code, bubbles: true, cancelable: true, ...extra})
      );
      fire('keydown', 'Control', 'ControlLeft', {ctrlKey: true});
      fire('keydown', 'l', 'KeyL', {ctrlKey: true});
      fire('keyup', 'l', 'KeyL', {ctrlKey: true});
      fire('keyup', 'Control', 'ControlLeft');
      return document.activeElement === canvas;
    })()"""
    focused = browser.eval(ctrl_l_script)
    add_check(result, "remote VNC canvas accepts keyboard focus", bool(focused), {"focused": focused})
    time.sleep(0.4)

    type_script = """(() => {
      const canvas = document.querySelector('.mobile-browser-content canvas');
      const text = %s;
      if (!canvas) return 0;
      const codes = {':': 'Semicolon', '/': 'Slash', '.': 'Period', '-': 'Minus',
        '_': 'Minus', '?': 'Slash', '=': 'Equal', '&': 'Digit7'};
      const fire = (type, key, code) => canvas.dispatchEvent(
        new KeyboardEvent(type, {key, code, bubbles: true, cancelable: true})
      );
      for (const char of text) {
        const code = /[a-z]/i.test(char) ? `Key${char.toUpperCase()}` :
          /[0-9]/.test(char) ? `Digit${char}` : (codes[char] || 'Unidentified');
        fire('keydown', char, code);
        fire('keyup', char, code);
      }
      fire('keydown', 'Enter', 'Enter');
      fire('keyup', 'Enter', 'Enter');
      return text.length;
    })()""" % json.dumps(target_url)
    sent_characters = browser.eval(type_script)

    deadline = time.monotonic() + timeout
    pages: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        pages = current_remote_pages(base_url, profile_id, min(timeout, 10))
        if any(page.get("url") == target_url for page in pages):
            break
        time.sleep(0.4)
    matched = any(page.get("url") == target_url for page in pages)
    add_check(
        result,
        "remote keyboard navigation verified by CDP",
        matched,
        {
            "target": public_url(target_url),
            "actual": [public_url(str(page.get("url", ""))) for page in pages],
            "characters": sent_characters,
        },
    )


def run_viewport(
    browser: AgentBrowser,
    base_url: str,
    output_dir: Path,
    profile_id: str | None,
    name: str,
    width: int,
    height: int,
    expected_layout: str,
    timeout: float,
    remote_probe_url: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "width": width,
        "height": height,
        "expected_layout": expected_layout,
        "checks": [],
        "screenshots": [],
        "errors": [],
    }
    browser.run("set", "viewport", str(width), str(height))
    navigation_started = time.monotonic()
    browser.run("open", base_url)
    browser.wait_for("!!document.querySelector('.mobile-split-root')", "mobile workspace", 20)
    result["workspace_ready_ms"] = round((time.monotonic() - navigation_started) * 1000, 1)

    structure = browser.eval(STRUCTURE_SCRIPT)
    add_check(result, "mobile workspace structure", bool(structure.get("ready")), structure)
    add_check(
        result,
        "Browser-Use-style composer controls",
        all(structure.get(key) for key in ("hasAttach", "hasRunSettings", "hasModel", "hasRunTask")),
        structure,
    )
    add_check(
        result,
        "no horizontal overflow",
        structure.get("scrollWidth", width) <= structure.get("innerWidth", width) + 1,
        {"scrollWidth": structure.get("scrollWidth"), "innerWidth": structure.get("innerWidth")},
    )

    root = structure.get("root") or {}
    add_check(
        result,
        "root matches visual viewport",
        abs(root.get("width", 0) - width) <= 1 and abs(root.get("height", 0) - height) <= 1,
        root,
    )

    live = structure.get("live") or {}
    controls = structure.get("controls") or {}
    if expected_layout == "horizontal":
        layout_ok = abs(live.get("right", 0) - controls.get("left", 0)) <= 2
        layout_evidence = {"liveRight": live.get("right"), "controlsLeft": controls.get("left")}
    else:
        layout_ok = abs(live.get("bottom", 0) - controls.get("top", 0)) <= 2
        layout_evidence = {"liveBottom": live.get("bottom"), "controlsTop": controls.get("top")}
    add_check(result, f"{expected_layout} split geometry", layout_ok, layout_evidence)

    if profile_id:
        select_and_connect(browser, result, profile_id, timeout)
        manual_remote_paste(browser, result, base_url, profile_id, timeout)
        if remote_probe_url:
            remote_keyboard_navigation(
                browser,
                result,
                base_url,
                profile_id,
                remote_probe_url,
                timeout,
            )
            take_screenshot(browser, result, output_dir, name, "remote-input", width, height)

    touch = browser.eval(TOUCH_TARGET_SCRIPT)
    add_check(result, "44px visible touch targets", not touch.get("offenders"), touch)

    unique_message = f"Mobile gate {name} {int(time.time() * 1000)}"
    browser.run("fill", "#mobile-task-input", unique_message)
    browser.run("click", "button[aria-label='Run task']")
    chat = browser.wait_for(
        f"document.body.innerText.includes({json.dumps(unique_message)}) && document.body.innerText.includes('Demo reply queued locally')",
        "local demo chat response",
        10,
    )
    add_check(result, "local demo chat round-trip", bool(chat), {"message": unique_message})

    browser.run("click", "button[aria-label='Run settings']")
    settings = browser.wait_for("!!document.querySelector('[aria-label=\"Demo run settings\"]')", "run settings", 5)
    browser.run("select", "select.mobile-composer-select", "browser-use-v4")
    selected_model = browser.eval("document.querySelector('select.mobile-composer-select')?.value")
    add_check(
        result,
        "demo run settings and model selection",
        bool(settings) and selected_model == "browser-use-v4",
        {"settings": bool(settings), "model": selected_model},
    )
    browser.run("click", "button[aria-label='Run settings']")
    take_screenshot(browser, result, output_dir, name, "workspace", width, height)

    browser.run("click", "button[aria-label='Toggle grid view']")
    grid = browser.wait_for("!!document.querySelector('[aria-label=\"Running browser grid\"]')", "running browser grid", 5)
    add_check(result, "grid opens", bool(grid), {"visible": bool(grid)})
    take_screenshot(browser, result, output_dir, name, "grid", width, height)
    browser.run("click", "button[aria-label='Toggle grid view']")

    # Navigating between mobile viewport sizes closes the previous noVNC
    # websocket. The viewer deliberately reconnects in the background, so
    # wait for that public, visible ready state before asserting fullscreen
    # behaviour. A persistent disconnect still fails the gate at this point.
    if profile_id:
        browser.wait_for(
            "document.body.innerText.includes('Connected') && "
            "document.querySelectorAll('.mobile-browser-content canvas').length === 1",
            "live VNC connection before fullscreen",
            timeout,
        )
    pre_fullscreen_canvas = browser.eval(CANVAS_SCRIPT)
    browser.run("click", "button[aria-label='Open fullscreen browser']")
    browser.wait_for("!!document.querySelector('[role=\"dialog\"][aria-label=\"Fullscreen browser viewer\"]')", "fullscreen dialog", 5)
    fullscreen = browser.eval(r"""(() => {
      const dialog = document.querySelector('[role="dialog"][aria-label="Fullscreen browser viewer"]');
      const rect = dialog?.getBoundingClientRect();
      const canvas = [...document.querySelectorAll('.mobile-browser-content canvas')];
      const active = document.activeElement;
      return {
        rect: rect ? rect.toJSON() : null,
        canvasCount: canvas.length,
        closeFocused: active?.getAttribute('aria-label') === 'Close fullscreen browser',
        backgroundInert: document.querySelector('.mobile-control-pane')?.hasAttribute('inert') ?? false,
      };
    })()""")
    full_rect = fullscreen.get("rect") or {}
    add_check(
        result,
        "fullscreen covers viewport",
        abs(full_rect.get("width", 0) - width) <= 1 and abs(full_rect.get("height", 0) - height) <= 1,
        fullscreen,
    )
    add_check(result, "fullscreen close control focused", bool(fullscreen.get("closeFocused")), fullscreen)
    add_check(result, "fullscreen background inert", bool(fullscreen.get("backgroundInert")), fullscreen)
    if profile_id:
        add_check(
            result,
            "fullscreen preserves one canvas",
            pre_fullscreen_canvas.get("count") == 1 and fullscreen.get("canvasCount") == 1,
            {"before": pre_fullscreen_canvas, "fullscreen": fullscreen},
        )
    take_screenshot(browser, result, output_dir, name, "fullscreen", width, height)

    browser.run("press", "Escape")
    browser.wait_for("!document.querySelector('[role=\"dialog\"]')", "fullscreen close", 5)
    after = browser.eval(r"""(() => ({
      overflow: (document.scrollingElement?.scrollWidth ?? 0) <= window.innerWidth + 1,
      canvasCount: document.querySelectorAll('.mobile-browser-content canvas').length,
      focusReturned: document.activeElement?.getAttribute('aria-label') === 'Open fullscreen browser',
    }))()""")
    add_check(result, "fullscreen closes without overflow", bool(after.get("overflow")), after)
    add_check(result, "fullscreen focus returns", bool(after.get("focusReturned")), after)
    if profile_id:
        add_check(result, "single canvas after fullscreen", after.get("canvasCount") == 1, after)

    navigation = browser.eval(r"""(() => {
      const entry = performance.getEntriesByType('navigation')[0];
      return entry ? {
        domContentLoadedMs: entry.domContentLoadedEventEnd,
        loadMs: entry.loadEventEnd,
        transferSize: entry.transferSize,
        decodedBodySize: entry.decodedBodySize,
      } : null;
    })()""")
    result["navigation"] = navigation
    result["passed"] = all(check["passed"] for check in result["checks"])
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/")
    parser.add_argument(
        "--profile-id",
        help="Existing profile to select and launch; enables the real VNC canvas gates.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/mobile-ui-gate"),
        help="Screenshot and JSON report directory (default: artifacts/mobile-ui-gate).",
    )
    parser.add_argument("--session", default=f"cloak-mobile-gate-{os.getpid()}")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--remote-probe-url",
        help="Harmless unique URL to type through the live noVNC canvas and verify through CDP.",
    )
    parser.add_argument("--keep-session", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    media_emulation_script = args.output_dir / "mobile-media-emulation.js"
    media_emulation_script.write_text(
        """(() => {
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
""",
        encoding="utf-8",
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": public_url(args.base_url),
        "profile_id": args.profile_id,
        "live_vnc_required": bool(args.profile_id),
        "pointer_emulation": "iPhone 14 device profile plus coarse-pointer matchMedia init script",
        "remote_probe_url": public_url(args.remote_probe_url) if args.remote_probe_url else None,
        "session": args.session,
        "viewports": [],
    }
    browser = AgentBrowser(args.session, args.timeout, media_emulation_script)

    try:
        browser.run("set", "device", "iPhone 14")
        for index, (name, width, height, expected_layout) in enumerate(VIEWPORTS):
            try:
                result = run_viewport(
                    browser,
                    args.base_url,
                    args.output_dir,
                    args.profile_id,
                    name,
                    width,
                    height,
                    expected_layout,
                    args.timeout,
                    args.remote_probe_url if index == 0 else None,
                )
            except Exception as exc:  # Collect every viewport before failing the gate.
                result = {
                    "name": name,
                    "width": width,
                    "height": height,
                    "expected_layout": expected_layout,
                    "passed": False,
                    "checks": [],
                    "screenshots": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
                try:
                    diagnostic = args.output_dir / f"{name}-failure.png"
                    browser.run("screenshot", str(diagnostic))
                    result["screenshots"].append(png_metadata(diagnostic))
                except Exception as screenshot_error:
                    result["errors"].append(f"diagnostic screenshot failed: {screenshot_error}")
            report["viewports"].append(result)
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if not args.keep_session:
            try:
                browser.run("close", check=False)
            except Exception:
                pass

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = (
        "fatal_error" not in report
        and len(report["viewports"]) == len(VIEWPORTS)
        and all(viewport.get("passed") for viewport in report["viewports"])
    )
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        "passed": report["passed"],
        "report": str(report_path.resolve()),
        "viewports": [
            {"name": item["name"], "passed": item.get("passed", False), "errors": item.get("errors", [])}
            for item in report["viewports"]
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
