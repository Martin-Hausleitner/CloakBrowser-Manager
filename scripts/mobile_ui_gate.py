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
from urllib.request import Request, urlopen


VIEWPORTS = (
    ("iphone-14-portrait", 390, 844, "vertical"),
    ("iphone-se-portrait", 375, 667, "vertical"),
    ("iphone-pro-max-portrait", 430, 932, "vertical"),
    ("iphone-14-landscape", 844, 390, "horizontal"),
    ("touch-tablet-portrait", 768, 1024, "vertical"),
)
CODEX_COMPUTER_USE_TEST_REPLY_PREFIX = "Codex Computer Use test harness accepted:"


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


def codex_computer_use_test_reply(message: str) -> str:
    return f"{CODEX_COMPUTER_USE_TEST_REPLY_PREFIX} {message}"


def mobile_gate_init_script() -> str:
    return f"""(() => {{
  const nativeMatchMedia = window.matchMedia.bind(window);
  window.matchMedia = (query) => {{
    const nativeResult = nativeMatchMedia(query);
    const forceCoarse = query.trim() === '(pointer: coarse)';
    const forceMobileWorkspace = query.includes('(pointer: coarse)') &&
      query.includes('(max-width: 1024px)') && window.innerWidth <= 1024;
    if (!forceCoarse && !forceMobileWorkspace) return nativeResult;
    return new Proxy(nativeResult, {{
      get(target, property) {{
        if (property === 'matches') return true;
        const value = Reflect.get(target, property, target);
        return typeof value === 'function' ? value.bind(target) : value;
      }},
    }});
  }};

  const replyPrefix = {json.dumps(CODEX_COMPUTER_USE_TEST_REPLY_PREFIX)};
  window.cloakBrowserHarness = {{
    capabilities: {{
      chat: true,
      streaming: true,
      clipboard: true,
      browser_actions: ['copy', 'paste', 'screenshot', 'fullscreen'],
      metadata: {{
        mode: 'codex-computer-use-mobile-gate',
        provider: 'codex-computer-use',
      }},
    }},
    send: async (request) => {{
      window.__codexComputerUseLastRequest = request;
      const text = String(request?.text ?? '');
      return {{
        id: `mobile-ui-gate-${{Date.now()}}`,
        role: 'assistant',
        content: `${{replyPrefix}} ${{text}}`,
        created_at: new Date().toISOString(),
        metadata: {{
          provider: 'codex-computer-use',
          profile_id: request?.profile_id ?? null,
          request_metadata: request?.metadata ?? null,
        }},
      }};
    }},
    subscribe: () => () => undefined,
  }};
}})();
"""


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


def click_visible(browser: AgentBrowser, selector: str, label: str) -> None:
    """Click a visible, enabled HTML control without agent-browser auto-wait.

    Agent-browser's native click occasionally stays in its post-click wait
    phase after a React disclosure has already changed the DOM. The gate still
    validates that the actual visible control exists, then invokes its browser
    click handler directly so the test runner cannot stall on its transport
    layer instead of reporting the product result.
    """
    clicked = browser.eval(
        """(() => {
          const element = document.querySelector(%s);
          if (!(element instanceof HTMLElement) || element.hasAttribute('inert')) return false;
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          if (rect.width < 1 || rect.height < 1 || style.display === 'none' || style.visibility === 'hidden') return false;
          if (element instanceof HTMLButtonElement && element.disabled) return false;
          element.focus({preventScroll: true});
          element.click();
          return true;
        })()"""
        % json.dumps(selector)
    )
    if not clicked:
        raise GateError(f"Could not click visible {label}: {selector}")


def ensure_browser_tools_open(browser: AgentBrowser, timeout: float = 5) -> None:
    opened = browser.eval(r"""(() => {
      if (document.querySelector('.mobile-tools-sheet')) return true;
      const button = document.querySelector('button[aria-label="Open browser tools"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    if not opened:
        raise GateError("Could not open browser tools")
    browser.wait_for("!!document.querySelector('.mobile-tools-sheet')", "browser tools sheet", timeout)


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


def mobile_shortcut_toggle_state(
    *,
    tools_after_k: Any,
    tools_closed_after_second_k: Any,
    chat_after_j: Any,
    fullscreen_after_b: Any,
    fullscreen_closed_after_second_b: Any,
    input_kept_closed: Any,
) -> dict[str, bool]:
    return {
        "toolsAfterK": bool(tools_after_k),
        "toolsClosedAfterSecondK": bool(tools_closed_after_second_k),
        "chatAfterJ": bool(chat_after_j),
        "fullscreenAfterB": bool(fullscreen_after_b),
        "fullscreenClosedAfterSecondB": bool(fullscreen_closed_after_second_b),
        "inputKeptToolsClosed": bool(input_kept_closed),
        "inputKeptFullscreenClosed": bool(input_kept_closed),
    }


def mobile_shortcut_toggle_passed(shortcut_state: dict[str, bool]) -> bool:
    return (
        bool(shortcut_state.get("toolsAfterK"))
        and bool(shortcut_state.get("toolsClosedAfterSecondK"))
        and bool(shortcut_state.get("chatAfterJ"))
        and bool(shortcut_state.get("fullscreenAfterB"))
        and bool(shortcut_state.get("fullscreenClosedAfterSecondB"))
        and bool(shortcut_state.get("inputKeptToolsClosed"))
        and bool(shortcut_state.get("inputKeptFullscreenClosed"))
    )


class CdpSession:
    def __init__(self, ws_url: str, timeout: float, auth_token: str | None = None):
        try:
            from inspect import signature
            from websockets.sync.client import connect
        except ImportError as exc:
            raise GateError(
                "the project's websockets dependency is required for the noVNC pointer hit-test CDP probe"
            ) from exc

        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        self._timeout = timeout
        connect_options: dict[str, Any] = {
            "additional_headers": headers,
            "open_timeout": timeout,
            "close_timeout": timeout,
        }
        # websockets 14 (still supported by backend/requirements.txt) does
        # not have a proxy parameter. Later versions do, and disabling it is
        # important for the loopback-only CDP endpoint used by this gate.
        if "proxy" in signature(connect).parameters:
            connect_options["proxy"] = None
        self._ws = connect(ws_url, **connect_options)
        self._next_id = 1

    def close(self) -> None:
        self._ws.close()

    def command(self, method: str, params: dict[str, Any] | None = None) -> Any:
        message_id = self._next_id
        self._next_id += 1
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = self._ws.recv(timeout=self._timeout)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            if payload.get("id") != message_id:
                continue
            if "error" in payload:
                raise GateError(f"CDP {method} failed: {payload['error']}")
            return payload.get("result")

    def evaluate(self, expression: str, *, return_by_value: bool = True) -> Any:
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": return_by_value,
            },
        )
        remote = (result or {}).get("result") or {}
        if "exceptionDetails" in (result or {}):
            raise GateError(f"CDP Runtime.evaluate failed: {result['exceptionDetails']}")
        return remote.get("value")


STRUCTURE_SCRIPT = r"""(() => {
  const root = document.querySelector('.mobile-split-root');
  const live = document.querySelector('.mobile-live-pane');
  const controls = document.querySelector('.mobile-control-pane');
  const frame = document.querySelector('[data-testid="mobile-browser-frame"]');
  const chatPanel = document.querySelector('#mobile-task-chat-panel');
  const chat = document.querySelector('[aria-label="Chat history"]');
  const chatHeader = document.querySelector('.mobile-chat-header');
  const tools = document.querySelector('[aria-label="Browser tools"]');
  const commandDock = document.querySelector('.mobile-command-dock');
  const composerForm = document.querySelector('.mobile-chat-form');
  const composer = document.querySelector('#mobile-task-input');
  const pinnedActions = document.querySelector('[aria-label="Pinned browser actions"]');
  const required = [root, live, controls, frame, commandDock, composerForm, composer];
  const rect = (node) => node ? node.getBoundingClientRect().toJSON() : null;
  const visible = (node) => {
    if (!node) return false;
    const value = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && value.width > 1 && value.height > 1;
  };
  const visibleHeight = (node) => {
    if (!node) return 0;
    const value = node.getBoundingClientRect();
    return Math.max(0, Math.min(value.bottom, window.innerHeight) - Math.max(value.top, 0));
  };
  const fullyVisible = (node) => {
    if (!node) return false;
    const value = node.getBoundingClientRect();
    return value.top >= -1 && value.bottom <= window.innerHeight + 1 && value.width > 1 && value.height > 1;
  };
  return {
    ready: required.every(Boolean),
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollWidth: document.scrollingElement?.scrollWidth ?? 0,
    root: rect(root),
    live: rect(live),
    controls: rect(controls),
    frame: rect(frame),
    chatPanel: rect(chatPanel),
    chat: rect(chat),
    chatHeader: rect(chatHeader),
    chatCollapsed: !chat && !visible(chatHeader),
    compactWorkspace: root?.classList.contains('mobile-workspace-collapsed') ?? false,
    chatVisibleHeight: Math.round(visibleHeight(chat) * 10) / 10,
    chatHeaderVisible: fullyVisible(chatHeader),
    toolsVisible: visible(tools),
    commandDock: rect(commandDock),
    primaryActionCount: (commandDock?.querySelectorAll('button')?.length ?? 0) +
      (document.querySelector('button[aria-label="Run task"]') ? 1 : 0),
    composerForm: rect(composerForm),
    composerFormVisible: fullyVisible(composerForm),
    composer: rect(composer),
    hasBrowserToolsToggle: !!document.querySelector('button[aria-label="Open browser tools"], button[aria-label="Close browser tools"]'),
    hasBrowserTools: !!document.querySelector('[aria-label="Browser tools"]'),
    hasAgentRunner: !!document.querySelector('select[aria-label="Select harness runner"]'),
    hasRunTask: !!document.querySelector('button[aria-label="Run task"]'),
    pinnedActionCount: pinnedActions?.querySelectorAll('button')?.length ?? 0,
    benchmarkNavAbsent: !document.body.innerText.includes('Benchmarks') &&
      !document.querySelector('button[aria-label="Streaming benchmark results"]'),
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


ACCESS_DASHBOARD_SCRIPT = r"""(() => {
  const dashboard = document.querySelector('main[aria-label="Browser access controls"]');
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 1 && rect.height > 1;
  };
  const elements = dashboard ? [...dashboard.querySelectorAll('button, select, textarea, input')]
    .filter((element) => !['hidden', 'file', 'checkbox', 'radio'].includes(element.type || ''))
    .filter(visible) : [];
  const controls = elements.map((element) => {
    const rect = element.getBoundingClientRect();
    return {
      label: element.getAttribute('aria-label') || element.title || element.textContent?.trim().slice(0, 60) || element.tagName,
      tag: element.tagName,
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10,
    };
  });
  const dashboardRect = dashboard?.getBoundingClientRect();
  return {
    ready: !!dashboard,
    innerWidth: window.innerWidth,
    scrollWidth: document.scrollingElement?.scrollWidth ?? 0,
    dashboard: dashboardRect ? {
      width: Math.round(dashboardRect.width * 10) / 10,
      height: Math.round(dashboardRect.height * 10) / 10,
      scrollWidth: dashboard.scrollWidth,
      clientWidth: dashboard.clientWidth,
    } : null,
    touchTargets: {
      count: controls.length,
      offenders: controls.filter((item) => item.width < 44 || item.height < 44),
    },
  };
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


LIVE_VIEWER_GEOMETRY_SCRIPT = r"""(() => {
  const livePane = document.querySelector('.mobile-live-pane');
  const frame = document.querySelector('[data-testid="mobile-browser-frame"]');
  const content = document.querySelector('.mobile-browser-content');
  const host = document.querySelector('[data-vnc-layout]');
  const canvas = document.querySelector('.mobile-browser-content canvas');
  const zoom = document.querySelector('#mobile-browser-zoom');
  const pane = document.querySelector('#mobile-pane-size');
  const zoomOutput = document.querySelector('[aria-label="Visual zoom level"]');
  const paneOutput = document.querySelector('[aria-label="Browser pane size"]');
  const rect = (node) => {
    if (!node) return null;
    const value = node.getBoundingClientRect();
    return {
      left: Math.round(value.left * 10) / 10,
      top: Math.round(value.top * 10) / 10,
      width: Math.round(value.width * 10) / 10,
      height: Math.round(value.height * 10) / 10,
    };
  };
  const style = (node) => {
    if (!node) return null;
    const computed = getComputedStyle(node);
    return {
      width: computed.width,
      height: computed.height,
      transform: computed.transform,
      inlineWidth: node.style?.width || '',
      inlineHeight: node.style?.height || '',
    };
  };
  const transformedAncestors = [];
  let current = canvas;
  while (current && current !== document.body) {
    const transform = getComputedStyle(current).transform;
    if (transform && transform !== 'none') {
      transformedAncestors.push({
        tag: current.tagName,
        className: current.className || '',
        transform,
      });
    }
    current = current.parentElement;
  }
  return {
    ready: !!(livePane && frame && content && host && canvas && zoom && pane),
    connected: document.body.innerText.includes('Connected'),
    livePane: rect(livePane),
    frame: rect(frame),
    content: rect(content),
    host: rect(host),
    canvas: canvas ? {
      rect: rect(canvas),
      width: canvas.width,
      height: canvas.height,
      style: style(canvas),
    } : null,
    hostStyle: style(host),
    contentStyle: style(content),
    livePaneStyle: livePane ? {
      basis: livePane.style.getPropertyValue('--mobile-live-pane-basis'),
    } : null,
    controls: {
      paneValue: pane?.value || null,
      paneOutput: paneOutput?.textContent?.trim() || null,
      zoomValue: zoom?.value || null,
      zoomOutput: zoomOutput?.textContent?.trim() || null,
    },
    transformedAncestors,
  };
})()"""


def live_viewer_state_when(condition: str) -> str:
    """Embed the reusable viewer probe without treating slider percent signs as formatting."""
    return """(() => {
      const state = __LIVE_VIEWER_STATE__;
      const controls = state.controls || {};
      return (__LIVE_VIEWER_CONDITION__) && state;
    })()""".replace("__LIVE_VIEWER_STATE__", LIVE_VIEWER_GEOMETRY_SCRIPT).replace(
        "__LIVE_VIEWER_CONDITION__", condition
    )


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
        and metadata["bytes"] >= 8_000,
        metadata,
    )
    result["screenshots"].append(metadata)


def capture_empty_mobile_state(
    browser: AgentBrowser,
    result: dict[str, Any],
    output_dir: Path,
    viewport_name: str,
    width: int,
    height: int,
) -> None:
    """Preserve the unselected mobile workspace before a live profile is chosen."""
    empty_state = browser.eval(r"""(() => {
      const select = document.querySelector('select.mobile-top-profile-select');
      const liveCanvas = document.querySelectorAll('.mobile-browser-content canvas');
      const root = document.querySelector('.mobile-split-root');
      return {
        selectedValue: select?.value ?? null,
        canvasCount: liveCanvas.length,
        rootVisible: Boolean(root && root.getBoundingClientRect().width > 1),
      };
    })()""")
    add_check(
        result,
        "empty mobile workspace has no selected browser canvas",
        empty_state.get("selectedValue") == "" and empty_state.get("canvasCount") == 0 and bool(empty_state.get("rootVisible")),
        empty_state,
    )
    take_screenshot(browser, result, output_dir, viewport_name, "empty", width, height)


def capture_viewport_editor(
    browser: AgentBrowser,
    result: dict[str, Any],
    output_dir: Path,
    viewport_name: str,
    width: int,
    height: int,
) -> None:
    """Capture the editable profile viewport without mutating the live profile."""
    ensure_browser_tools_open(browser)
    opened = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Edit browser viewport"]');
      if (!button) return false;
      button.scrollIntoView({block: 'center', inline: 'nearest'});
      if (button.getAttribute('aria-expanded') !== 'true') button.click();
      return true;
    })()""")
    add_check(result, "viewport editor action available", bool(opened), {"opened": bool(opened)})
    browser.wait_for(
        "!!document.querySelector('[aria-label=\"Viewport controls\"]')",
        "viewport editor",
        5,
    )
    editor = browser.eval(r"""(() => {
      const root = document.querySelector('[aria-label="Viewport controls"]');
      const rect = root?.getBoundingClientRect();
      const inputs = [...(root?.querySelectorAll('input[type="number"]') ?? [])];
      const apply = [...(root?.querySelectorAll('button') ?? [])]
        .some((button) => button.textContent?.trim() === 'Apply' && !button.disabled);
      return {
        visible: Boolean(rect && rect.width > 1 && rect.height > 1),
        inputCount: inputs.length,
        apply,
        rect: rect ? rect.toJSON() : null,
      };
    })()""")
    add_check(
        result,
        "editable viewport editor renders width height and apply controls",
        bool(editor.get("visible")) and editor.get("inputCount") == 2 and bool(editor.get("apply")),
        editor,
    )
    take_screenshot(browser, result, output_dir, viewport_name, "viewport-editor", width, height)
    click_visible(browser, "button[aria-label='Edit browser viewport']", "viewport editor toggle")
    browser.wait_for(
        "!document.querySelector('[aria-label=\"Viewport controls\"]')",
        "compact workspace after viewport editor",
        5,
    )


def select_and_connect(
    browser: AgentBrowser,
    result: dict[str, Any],
    profile_id: str,
    timeout: float,
) -> None:
    browser.run("select", "select.mobile-top-profile-select", profile_id)
    browser.wait_for(
        f"document.querySelector('select.mobile-top-profile-select')?.value === {json.dumps(profile_id)}",
        "profile selection",
        10,
    )
    browser.eval(r"""(() => {
      if (document.querySelector('.mobile-tools-sheet')) return true;
      const button = document.querySelector('button[aria-label="Open browser tools"]');
      if (button) button.click();
      return true;
    })()""")
    launch_state = browser.eval(r"""(() => {
      const buttons = [...document.querySelectorAll('.mobile-tools-sheet button')];
      return {
        connected: document.body.innerText.includes('Connected'),
        hasLaunch: buttons.some((button) => button.textContent?.trim() === 'Launch' && !button.disabled),
      };
    })()""")
    if launch_state.get("hasLaunch") and not launch_state.get("connected"):
        clicked = browser.eval(r"""(() => {
          const button = [...document.querySelectorAll('.mobile-tools-sheet button')]
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


def verify_live_viewport_controls(
    browser: AgentBrowser,
    result: dict[str, Any],
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None,
) -> None:
    ensure_browser_tools_open(browser)
    opened = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Edit browser viewport"]');
      if (!button) return false;
      button.scrollIntoView({block: 'center', inline: 'nearest'});
      if (button.getAttribute('aria-expanded') !== 'true') button.click();
      return true;
    })()""")
    add_check(result, "live profile viewport settings action available", bool(opened), {"clicked": opened})
    browser.wait_for(
        "!!document.querySelector('[aria-label=\"Viewport controls\"]')",
        "live profile viewport settings",
        5,
    )
    editor = browser.eval(r"""(() => {
      const root = document.querySelector('[aria-label="Viewport controls"]');
      const inputs = [...(root?.querySelectorAll('input[type="number"]') ?? [])];
      const apply = [...(root?.querySelectorAll('button') ?? [])]
        .some((button) => button.textContent?.trim() === 'Apply' && !button.disabled);
      return {visible: Boolean(root), inputCount: inputs.length, apply};
    })()""")
    add_check(
        result,
        "live profile viewport settings render width height and apply",
        bool(editor.get("visible")) and editor.get("inputCount") == 2 and bool(editor.get("apply")),
        editor,
    )
    live_controls_opened = browser.eval(
        "!!document.querySelector('#mobile-pane-size') && !!document.querySelector('#mobile-browser-zoom')"
    )
    add_check(result, "live view tuning controls available in browser tools", bool(live_controls_opened), {"visible": live_controls_opened})
    browser.wait_for(
        "!!document.querySelector('#mobile-pane-size') && !!document.querySelector('#mobile-browser-zoom')",
        "live view tuning drawer",
        5,
    )
    before = browser.eval(LIVE_VIEWER_GEOMETRY_SCRIPT)
    add_check(result, "live view tuning controls rendered", bool(before.get("ready")), before)

    pane_adjusted = browser.eval(r"""(() => {
      const setRange = (selector, value) => {
        const input = document.querySelector(selector);
        if (!input) return false;
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        if (!setter) return false;
        setter.call(input, String(value));
        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
      };
      return setRange('#mobile-pane-size', 64);
    })()""")
    add_check(
        result,
        "live ratio control accepts input",
        bool(pane_adjusted),
        {"adjusted": pane_adjusted},
    )
    after_pane = browser.wait_for(
        live_viewer_state_when("controls.paneOutput === '64%'"),
        "live ratio control output",
        5,
    )

    before_live = before.get("livePane") or {}
    after_live = after_pane.get("livePane") or {}
    pane_changed = (
        (before.get("livePaneStyle") or {}).get("basis") != (after_pane.get("livePaneStyle") or {}).get("basis")
        or abs((before_live.get("width") or 0) - (after_live.get("width") or 0)) >= 2
        or abs((before_live.get("height") or 0) - (after_live.get("height") or 0)) >= 2
    )
    add_check(
        result,
        "browser pane ratio updates live",
        pane_changed and (after_pane.get("controls") or {}).get("paneOutput") == "64%",
        {"before": before, "after": after_pane},
    )

    zoom_adjusted = browser.eval(r"""(() => {
      const input = document.querySelector('#mobile-browser-zoom');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      if (!setter) return false;
      setter.call(input, '135');
      input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })()""")
    add_check(
        result,
        "live zoom control accepts input",
        bool(zoom_adjusted),
        {"adjusted": zoom_adjusted},
    )
    after_zoom = browser.wait_for(
        live_viewer_state_when("controls.zoomOutput === '135%'"),
        "live zoom control output",
        5,
    )

    before_canvas = after_pane.get("canvas") or {}
    after_canvas = after_zoom.get("canvas") or {}
    before_canvas_rect = before_canvas.get("rect") or {}
    after_canvas_rect = after_canvas.get("rect") or {}
    canvas_geometry_changed = (
        abs((before_canvas_rect.get("width") or 0) - (after_canvas_rect.get("width") or 0)) >= 2
        or abs((before_canvas_rect.get("height") or 0) - (after_canvas_rect.get("height") or 0)) >= 2
        or before_canvas.get("width") != after_canvas.get("width")
        or before_canvas.get("height") != after_canvas.get("height")
    )
    before_host_rect = after_pane.get("host") or {}
    after_host_rect = after_zoom.get("host") or {}
    before_canvas_width = before_canvas_rect.get("width") or 0
    before_canvas_height = before_canvas_rect.get("height") or 0
    after_canvas_width = after_canvas_rect.get("width") or 0
    after_canvas_height = after_canvas_rect.get("height") or 0
    canvas_grew_for_requested_zoom = (
        before_canvas_width > 0
        and before_canvas_height > 0
        and after_canvas_width >= before_canvas_width * 1.25
        and after_canvas_height >= before_canvas_height * 1.25
    )
    add_check(
        result,
        "visual zoom changes noVNC canvas geometry without CSS transform",
        canvas_geometry_changed
        and canvas_grew_for_requested_zoom
        and (after_zoom.get("controls") or {}).get("zoomOutput") == "135%"
        and not after_zoom.get("transformedAncestors"),
        {
            "before": after_pane,
            "after": after_zoom,
            "canvasGeometryChanged": canvas_geometry_changed,
            "canvasGrewForRequestedZoom": canvas_grew_for_requested_zoom,
            "hostBefore": before_host_rect,
            "hostAfter": after_host_rect,
        },
    )
    remote_pointer_hit_test_at_zoom(browser, result, base_url, profile_id, timeout, auth_token)

    reset = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Reset live view"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    add_check(result, "live viewport controls reset", bool(reset), {"clicked": reset})
    expected_default_pane = browser.eval(
        "window.innerHeight <= 700 && window.innerHeight >= window.innerWidth ? '65%' : '68%'"
    )
    add_check(
        result,
        "live viewport reset picks the device-appropriate default pane",
        expected_default_pane in {"65%", "68%"},
        {"expectedPane": expected_default_pane},
    )
    browser.wait_for(
        "document.querySelector('[aria-label=\"Browser pane size\"]')?.textContent?.trim() === "
        f"{json.dumps(expected_default_pane)} && "
        "document.querySelector('[aria-label=\"Visual zoom level\"]')?.textContent?.trim() === '100%'",
        "live viewport controls reset state",
        5,
    )
    closed = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Close browser tools"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    add_check(result, "live controls return to compact workspace", bool(closed), {"clicked": closed})
    browser.wait_for(
        "!document.querySelector('#mobile-pane-size') && !document.querySelector('#mobile-browser-zoom')",
        "compact workspace after live controls",
        5,
    )


def _authenticated_request(url: str, timeout: float, auth_token: str | None):
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    return urlopen(Request(url, headers=headers), timeout=timeout)


def authenticate_workspace(browser: AgentBrowser, auth_token: str | None) -> None:
    if not auth_token:
        return
    browser.wait_for(
        r"""(() => Boolean(document.querySelector('.mobile-split-root, input[placeholder="Access token"]')) || [...document.querySelectorAll('button')].some((candidate) => candidate.textContent?.includes('administrator token')))()""",
        "mobile workspace or policy login",
        10,
    )
    if browser.eval("!!document.querySelector('.mobile-split-root')"):
        return
    token_input = browser.eval("!!document.querySelector(\"input[placeholder='Access token']\")")
    if not token_input:
        switched = browser.eval(r"""(() => {
          const button = [...document.querySelectorAll('button')]
            .find((candidate) => candidate.textContent?.includes('administrator token'));
          if (!button) return false;
          button.click();
          return true;
        })()""")
        if not switched:
            raise GateError("Could not switch the policy login page to administrator-token mode")
    browser.wait_for(
        "!!document.querySelector(\"input[placeholder='Access token']\")",
        "administrator token login",
        10,
    )
    # The token is read from a local environment variable and is never written
    # to JSON report or screenshot artifacts. The browser CLI receives it only
    # to enter the login form, so use this option exclusively with a disposable
    # token on an isolated E2E deployment, never a production administrator key.
    browser.run("fill", "input[placeholder='Access token']", auth_token)
    browser.run("click", "button[type='submit']")


def current_remote_pages(
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None = None,
) -> list[dict[str, Any]]:
    endpoint = urljoin(base_url.rstrip("/") + "/", f"api/profiles/{quote(profile_id)}/cdp/json/list")
    with _authenticated_request(endpoint, timeout, auth_token) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise GateError("CDP page list did not return a list")
    return [page for page in payload if page.get("type") == "page"]


def current_remote_page_ws_url(
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None = None,
) -> str:
    pages = current_remote_pages(base_url, profile_id, timeout, auth_token)
    if not pages:
        raise GateError("CDP page list did not include a page target")
    ws_url = pages[0].get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise GateError("CDP page target did not include webSocketDebuggerUrl")
    return ws_url


def current_remote_clipboard(
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None = None,
) -> str:
    endpoint = urljoin(base_url.rstrip("/") + "/", f"api/profiles/{quote(profile_id)}/clipboard")
    with _authenticated_request(endpoint, timeout, auth_token) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise GateError("Profile clipboard endpoint did not return text")
    return payload["text"]


def remote_pointer_hit_test_at_zoom(
    browser: AgentBrowser,
    result: dict[str, Any],
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None,
) -> None:
    marker = f"mobile-pointer-probe-{int(time.time() * 1000)}"
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mobile pointer probe</title>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: #071017;
      overflow: hidden;
      font-family: system-ui, sans-serif;
    }}
    #mobile-pointer-target {{
      position: fixed;
      left: 12vw;
      top: 18vh;
      width: 76vw;
      height: 64vh;
      border: 0;
      border-radius: 0;
      background: #2dd4bf;
      color: #04111b;
      font: 700 32px system-ui, sans-serif;
    }}
  </style>
</head>
<body data-marker="{marker}">
  <button id="mobile-pointer-target" type="button">Pointer target</button>
  <script>
    window.__mobilePointerProbe = {{clicked: false, marker: {json.dumps(marker)}}};
    document.getElementById('mobile-pointer-target').addEventListener('click', (event) => {{
      document.body.dataset.clicked = 'true';
      window.__mobilePointerProbe = {{
        clicked: true,
        marker: {json.dumps(marker)},
        targetId: event.target.id,
        clientX: Math.round(event.clientX),
        clientY: Math.round(event.clientY),
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight
      }};
    }});
  </script>
</body>
</html>"""
    data_url = "data:text/html;charset=utf-8," + quote(html, safe="")
    ws_url = current_remote_page_ws_url(base_url, profile_id, min(timeout, 10), auth_token)
    cdp = CdpSession(ws_url, min(timeout, 10), auth_token)
    try:
        cdp.command("Page.enable")
        cdp.command("Runtime.enable")
        cdp.command("Page.bringToFront")
        cdp.command("Page.navigate", {"url": data_url})
        deadline = time.monotonic() + min(timeout, 20)
        ready: Any = None
        ready_expression = f"""(() => {{
          const target = document.getElementById('mobile-pointer-target');
          if (document.readyState !== 'complete' || !target) return null;
          const rect = target.getBoundingClientRect();
          return {{
            marker: document.body.dataset.marker,
            readyState: document.readyState,
            target: {{
              left: Math.round(rect.left),
              top: Math.round(rect.top),
              width: Math.round(rect.width),
              height: Math.round(rect.height)
            }},
            viewport: {{width: window.innerWidth, height: window.innerHeight}}
          }};
        }})()"""
        while time.monotonic() < deadline:
            ready = cdp.evaluate(ready_expression)
            if ready and ready.get("marker") == marker:
                break
            time.sleep(0.4)
        add_check(
            result,
            "remote pointer probe page ready via CDP",
            bool(ready and ready.get("marker") == marker),
            ready,
        )

        geometry = browser.eval(LIVE_VIEWER_GEOMETRY_SCRIPT)
        canvas = (geometry.get("canvas") or {}).get("rect") or {}
        content = geometry.get("content") or {}
        visible_left = max(canvas.get("left") or 0, content.get("left") or 0)
        visible_top = max(canvas.get("top") or 0, content.get("top") or 0)
        visible_right = min(
            (canvas.get("left") or 0) + (canvas.get("width") or 0),
            (content.get("left") or 0) + (content.get("width") or 0),
        )
        visible_bottom = min(
            (canvas.get("top") or 0) + (canvas.get("height") or 0),
            (content.get("top") or 0) + (content.get("height") or 0),
        )
        visible_width = visible_right - visible_left
        visible_height = visible_bottom - visible_top
        ready_target = (ready or {}).get("target") or {}
        ready_viewport = (ready or {}).get("viewport") or {}
        if (
            ready_target.get("width")
            and ready_target.get("height")
            and ready_viewport.get("width")
            and ready_viewport.get("height")
            and canvas.get("width")
            and canvas.get("height")
        ):
            click_x = round(
                (canvas.get("left") or 0)
                + ((ready_target.get("left") or 0) + (ready_target.get("width") or 0) / 2)
                * ((canvas.get("width") or 0) / (ready_viewport.get("width") or 1))
            )
            click_y = round(
                (canvas.get("top") or 0)
                + ((ready_target.get("top") or 0) + (ready_target.get("height") or 0) / 2)
                * ((canvas.get("height") or 0) / (ready_viewport.get("height") or 1))
            )
        else:
            click_x = round(visible_left + visible_width / 2)
            click_y = round(visible_top + visible_height / 2)
        add_check(
            result,
            "remote pointer probe has visible canvas coordinate",
            visible_width > 20
            and visible_height > 20
            and visible_left <= click_x <= visible_right
            and visible_top <= click_y <= visible_bottom,
            {
                "geometry": geometry,
                "click": {"x": click_x, "y": click_y},
                "visibleCanvas": {
                    "left": visible_left,
                    "top": visible_top,
                    "width": visible_width,
                    "height": visible_height,
                },
            },
        )

        browser.run("mouse", "move", str(click_x), str(click_y))
        browser.run("mouse", "down")
        browser.run("mouse", "up")

        clicked: Any = None
        click_expression = """(() => window.__mobilePointerProbe || null)()"""
        deadline = time.monotonic() + min(timeout, 10)
        while time.monotonic() < deadline:
            clicked = cdp.evaluate(click_expression)
            if (
                clicked
                and clicked.get("clicked") is True
                and clicked.get("marker") == marker
                and clicked.get("targetId") == "mobile-pointer-target"
            ):
                break
            time.sleep(0.4)
        add_check(
            result,
            "zoomed visible noVNC pointer reaches remote page target",
            bool(
                clicked
                and clicked.get("clicked") is True
                and clicked.get("marker") == marker
                and clicked.get("targetId") == "mobile-pointer-target"
            ),
            {
                "clicked": clicked,
                "click": {"x": click_x, "y": click_y},
                "ready": ready,
                "geometry": geometry,
            },
        )
    finally:
        cdp.close()


def manual_remote_paste(
    browser: AgentBrowser,
    result: dict[str, Any],
    base_url: str,
    profile_id: str,
    timeout: float,
    auth_token: str | None,
) -> None:
    """Exercise the iOS-safe fallback that does not depend on navigator.clipboard."""
    marker = f"mobile-manual-paste-{int(time.time() * 1000)}"
    tools_opened = browser.eval(r"""(() => {
      if (document.querySelector('button[aria-label="Paste text into remote browser"]')) return true;
      const toggle = document.querySelector('button[aria-label="Open browser tools"]');
      if (!toggle) return false;
      toggle.click();
      return true;
    })()""")
    add_check(result, "compact remote tools action available", bool(tools_opened), {"opened": tools_opened})
    browser.wait_for(
        "!!document.querySelector('button[aria-label=\"Paste text into remote browser\"]')",
        "remote paste tool",
        5,
    )
    click_visible(browser, "button[aria-label='Paste text into remote browser']", "remote paste tool")
    browser.wait_for(
        "!!document.querySelector('textarea[id^=remote-paste-]')",
        "manual remote paste field",
        5,
    )
    panel_touch = browser.eval(TOUCH_TARGET_SCRIPT)
    add_check(result, "manual paste has 44px touch targets", not panel_touch.get("offenders"), panel_touch)

    browser.run("fill", "textarea[id^=remote-paste-]", marker)
    # The agent-browser click command can remain in its auto-wait phase after
    # this React form has already submitted. Submit through the live DOM so the
    # gate still exercises the visible iOS fallback, while avoiding a runner
    # hang that masks the actual product result.
    submitted = browser.eval(
        r"""(() => {
          const field = document.querySelector('textarea[id^=remote-paste-]');
          const form = field?.closest('form');
          const button = form?.querySelector("button[aria-label='Send pasted text to remote browser']");
          if (!(form instanceof HTMLFormElement) || !(button instanceof HTMLButtonElement)) return false;
          form.requestSubmit(button);
          return true;
        })()"""
    )
    add_check(result, "manual paste submit control is available", bool(submitted), {"submitted": bool(submitted)})
    browser.wait_for(
        "!document.querySelector('textarea[id^=remote-paste-]') && document.body.innerText.includes('Connected')",
        "manual remote paste completion",
        10,
    )
    actual = current_remote_clipboard(base_url, profile_id, min(timeout, 10), auth_token)
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
    auth_token: str | None,
) -> None:
    target = urlsplit(target_url)
    if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password:
        raise GateError("--remote-probe-url must be an HTTP(S) URL without embedded credentials")
    if not target_url.isascii():
        raise GateError("--remote-probe-url must be ASCII so keyboard mapping is deterministic")

    before = current_remote_pages(base_url, profile_id, min(timeout, 10), auth_token)
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
        pages = current_remote_pages(base_url, profile_id, min(timeout, 10), auth_token)
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
    auth_token: str | None,
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
    authenticate_workspace(browser, auth_token)
    browser.wait_for("!!document.querySelector('.mobile-split-root')", "mobile workspace", 20)
    result["workspace_ready_ms"] = round((time.monotonic() - navigation_started) * 1000, 1)

    if name == "iphone-14-portrait":
        capture_empty_mobile_state(browser, result, output_dir, name, width, height)

    structure = browser.eval(STRUCTURE_SCRIPT)
    add_check(result, "mobile workspace structure", bool(structure.get("ready")), structure)
    add_check(
        result,
        "compact primary controls are Full Tools Chat and Send",
        bool(structure.get("hasBrowserToolsToggle"))
        and bool(structure.get("hasRunTask"))
        and (structure.get("primaryActionCount") or 0) <= 4
        and bool(structure.get("chatCollapsed"))
        and bool(structure.get("compactWorkspace"))
        and bool(structure.get("benchmarkNavAbsent"))
        and not bool(structure.get("hasAgentRunner")),
        structure,
    )
    tools_opened = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Open browser tools"]');
      if (!button) return !!document.querySelector('[aria-label="Browser tools"]');
      button.click();
      return true;
    })()""")
    add_check(result, "browser tools action available", bool(tools_opened), {"clicked": tools_opened})
    browser.wait_for(
        "!!document.querySelector('[aria-label=\"Browser tools\"]')",
        "browser tools",
        5,
    )
    tools_structure = browser.eval(STRUCTURE_SCRIPT)
    add_check(
        result,
        "browser tools use three pinned Codex actions without a harness picker",
        bool(tools_structure.get("toolsVisible"))
        and tools_structure.get("pinnedActionCount") == 3
        and not bool(tools_structure.get("hasAgentRunner")),
        tools_structure,
    )
    browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Close browser tools"]');
      if (button) button.click();
      return true;
    })()""")
    chat_opened = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Expand task chat"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    chat_visible = browser.wait_for("!!document.querySelector('[aria-label=\"Chat history\"]')", "expanded chat", 5)
    tools_opened_after_chat = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Open browser tools"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    tools_visible_after_chat = browser.wait_for(
        "!!document.querySelector('[aria-label=\"Browser tools\"]')",
        "browser tools after chat",
        5,
    )
    chat_closed_by_tools = browser.eval("!document.querySelector('[aria-label=\"Chat history\"]')")
    chat_reopened = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Expand task chat"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    chat_visible_again = browser.wait_for("!!document.querySelector('[aria-label=\"Chat history\"]')", "chat after tools", 5)
    tools_closed_by_chat = browser.eval("!document.querySelector('[aria-label=\"Browser tools\"]')")
    exclusive = {
        "ready": bool(chat_opened) and bool(tools_opened_after_chat) and bool(chat_reopened),
        "chatOpen": bool(chat_visible),
        "toolsOpen": bool(tools_visible_after_chat),
        "chatClosedByTools": bool(chat_closed_by_tools),
        "toolsClosedByChat": bool(tools_closed_by_chat) and bool(chat_visible_again),
    }
    add_check(
        result,
        "expanded chat and browser tools are mutually exclusive",
        bool(exclusive.get("ready"))
        and bool(exclusive.get("chatOpen"))
        and bool(exclusive.get("toolsOpen"))
        and bool(exclusive.get("chatClosedByTools"))
        and bool(exclusive.get("toolsClosedByChat")),
        exclusive,
    )
    dispatch_shortcut = r"""((key, extra = {}) => {
      window.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ctrlKey: true, ...extra}));
      return true;
    })"""
    browser.eval(f"{dispatch_shortcut}('k')")
    tools_after_k = browser.wait_for("!!document.querySelector('[aria-label=\"Browser tools\"]')", "tools shortcut open", 5)
    browser.eval(f"{dispatch_shortcut}('k')")
    tools_after_second_k = browser.wait_for("!document.querySelector('[aria-label=\"Browser tools\"]')", "tools shortcut close", 5)
    browser.eval(f"{dispatch_shortcut}('j')")
    chat_after_j = browser.wait_for("!!document.querySelector('[aria-label=\"Chat history\"]')", "chat shortcut open", 5)
    browser.eval(f"{dispatch_shortcut}('b', {{metaKey: true, ctrlKey: false}})")
    fullscreen_after_b = browser.wait_for(
        "!!document.querySelector('[role=\"dialog\"][aria-label=\"Fullscreen browser viewer\"]')",
        "fullscreen shortcut open",
        5,
    )
    browser.eval(f"{dispatch_shortcut}('b')")
    fullscreen_after_second_b = browser.wait_for(
        "!document.querySelector('[role=\"dialog\"][aria-label=\"Fullscreen browser viewer\"]')",
        "fullscreen shortcut close",
        5,
    )
    input_kept_closed = browser.eval(r"""(() => {
      const dispatch = (target, key, extra = {}) => target?.dispatchEvent(
        new KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ctrlKey: true, ...extra})
      );
      const chat = document.querySelector('button[aria-label="Collapse task chat"]');
      if (chat) chat.click();
      const input = document.querySelector('#mobile-task-input');
      input?.focus();
      dispatch(input, 'k');
      dispatch(input, 'j');
      dispatch(input, 'b', {metaKey: true, ctrlKey: false});
      return !document.querySelector('[aria-label="Browser tools"]') &&
        !document.querySelector('[role="dialog"][aria-label="Fullscreen browser viewer"]');
    })()""")
    shortcut_state = mobile_shortcut_toggle_state(
        tools_after_k=tools_after_k,
        tools_closed_after_second_k=tools_after_second_k,
        chat_after_j=chat_after_j,
        fullscreen_after_b=fullscreen_after_b,
        fullscreen_closed_after_second_b=fullscreen_after_second_b,
        input_kept_closed=input_kept_closed,
    )
    add_check(
        result,
        "Ctrl shortcuts toggle tools and chat but ignore task input",
        mobile_shortcut_toggle_passed(shortcut_state),
        shortcut_state,
    )
    browser.eval(r"""(() => {
      const chat = document.querySelector('button[aria-label="Collapse task chat"]');
      if (chat) chat.click();
      const tools = document.querySelector('button[aria-label="Close browser tools"]');
      if (tools) tools.click();
      return true;
    })()""")
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
        verify_live_viewport_controls(browser, result, base_url, profile_id, timeout, auth_token)
        if name == "iphone-pro-max-portrait":
            capture_viewport_editor(browser, result, output_dir, name, width, height)
        compact_structure = browser.eval(STRUCTURE_SCRIPT)
        if expected_layout == "vertical":
            live_rect = compact_structure.get("live") or {}
            live_ratio = (live_rect.get("height") or 0) / max(1, compact_structure.get("innerHeight") or height)
            add_check(
                result,
                "portrait keeps collapsed chat hidden and composer visible beside live VNC",
                bool(compact_structure.get("chatCollapsed"))
                and not bool(compact_structure.get("chatHeaderVisible"))
                and bool(compact_structure.get("composerFormVisible")),
                compact_structure,
            )
            if width == 375 and height == 667:
                add_check(
                    result,
                    "iPhone SE live browser uses at least 55 percent of the viewport",
                    live_ratio >= 0.55,
                    {"liveRatio": round(live_ratio, 3), "structure": compact_structure},
                )
        if expected_layout == "horizontal":
            live_rect = compact_structure.get("live") or {}
            live_ratio = (live_rect.get("width") or 0) / max(1, compact_structure.get("innerWidth") or width)
            add_check(
                result,
                "landscape live browser pane uses at least 65 percent of the viewport",
                live_ratio >= 0.65,
                {"liveRatio": round(live_ratio, 3), "structure": compact_structure},
            )
        manual_remote_paste(browser, result, base_url, profile_id, timeout, auth_token)
        if remote_probe_url:
            remote_keyboard_navigation(
                browser,
                result,
                base_url,
                profile_id,
                remote_probe_url,
                timeout,
                auth_token,
            )
            take_screenshot(browser, result, output_dir, name, "remote-input", width, height)

    touch = browser.eval(TOUCH_TARGET_SCRIPT)
    add_check(result, "44px visible touch targets", not touch.get("offenders"), touch)

    browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Open browser tools"]');
      if (button) button.click();
      return true;
    })()""")
    harness_ready = browser.wait_for(
        r"""(() => {
          const input = document.querySelector('#mobile-task-input');
          const host = window.cloakBrowserHarness;
          const hasHost = !!host && typeof host.send === 'function';
          const capture = document.querySelector(
            'button[aria-label="Run Capture with Codex Computer Use"]'
          );
          const connected = !!capture && !capture.disabled;
          if (!hasHost || !input || input.disabled || !connected) return false;
          return {
            hasHost,
            inputDisabled: input.disabled,
            placeholder: input.getAttribute('placeholder'),
            connected,
            captureDisabled: capture.disabled,
          };
        })()""",
        "Codex Computer Use test host harness",
        10,
    )
    add_check(
        result,
        "Codex Computer Use test host harness is injected and available",
        bool(harness_ready),
        harness_ready,
    )
    pinned_action = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Run Capture with Codex Computer Use"]');
      if (!button || button.disabled) return false;
      button.click();
      return true;
    })()""")
    pinned_request = browser.wait_for(
        "window.__codexComputerUseLastRequest?.commands?.[0]?.kind === 'screenshot' && "
        "window.__codexComputerUseLastRequest?.metadata?.source === 'pinned-action'",
        "structured Codex pinned action",
        10,
    )
    add_check(
        result,
        "pinned screenshot action uses structured Codex Computer Use command",
        bool(pinned_action) and bool(pinned_request),
        {"clicked": pinned_action, "requestObserved": bool(pinned_request)},
    )
    unique_message = f"Mobile gate {name} {int(time.time() * 1000)}"
    expected_reply = codex_computer_use_test_reply(unique_message)
    browser.run("fill", "#mobile-task-input", unique_message)
    click_visible(browser, "button[aria-label='Run task']", "local agent task submit")
    browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Expand task chat"]');
      if (button) button.click();
      return true;
    })()""")
    chat = browser.wait_for(
        f"document.body.innerText.includes({json.dumps(unique_message)})"
        f" && document.body.innerText.includes({json.dumps(expected_reply)})"
        " && !document.body.innerText.includes('Codex Computer Use could not queue that task')"
        " && !document.body.innerText.includes('Codex Computer Use Bridge unavailable')",
        "Codex Computer Use test harness response",
        10,
    )
    add_check(
        result,
        "Codex Computer Use test harness round-trip",
        bool(chat),
        {"message": unique_message, "expectedReply": expected_reply},
    )
    take_screenshot(browser, result, output_dir, name, "workspace", width, height)

    click_visible(browser, "button[aria-label='Open browser tools']", "browser tools open for grid")
    click_visible(browser, "button[aria-label='Toggle grid view']", "grid view open")
    browser.wait_for("!!document.querySelector('[aria-label=\"Running browser grid\"]')", "running browser grid", 5)
    grid = browser.eval(r"""(() => {
      const root = document.querySelector('[aria-label="Running browser grid"]');
      const text = root?.innerText || '';
      return {
        visible: !!root,
        hasName: /QA|Browser|Checkout|Live/i.test(text),
        hasStatus: /\b(Live|Idle|Running|Stopped)\b/i.test(text),
        hasResolution: /\b\d{3,4}\s*x\s*\d{3,4}\b/.test(text),
        hasOldGradientThumb: !!root?.querySelector('.mobile-grid-thumb'),
        hasSimulatedPreview: !!root?.querySelector('.mobile-grid-preview'),
      };
    })()""")
    add_check(
        result,
        "grid uses honest session cards with status name and resolution",
        bool(grid.get("visible"))
        and bool(grid.get("hasName"))
        and bool(grid.get("hasStatus"))
        and bool(grid.get("hasResolution"))
        and not bool(grid.get("hasOldGradientThumb"))
        and not bool(grid.get("hasSimulatedPreview")),
        grid,
    )
    take_screenshot(browser, result, output_dir, name, "grid", width, height)
    click_visible(browser, "button[aria-label='Toggle grid view']", "grid view close")

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
    click_visible(browser, "button[aria-label='Open fullscreen browser']", "fullscreen browser open")
    browser.wait_for("!!document.querySelector('[role=\"dialog\"][aria-label=\"Fullscreen browser viewer\"]')", "fullscreen dialog", 5)
    fullscreen = browser.eval(r"""(() => {
      const dialog = document.querySelector('[role="dialog"][aria-label="Fullscreen browser viewer"]');
      const rect = dialog?.getBoundingClientRect();
      const canvas = [...document.querySelectorAll('.mobile-browser-content canvas')];
      const active = document.activeElement;
      const strip = document.querySelector('[aria-label="Fullscreen browser controls"]');
      return {
        rect: rect ? rect.toJSON() : null,
        canvasCount: canvas.length,
        closeFocused: active?.getAttribute('aria-label') === 'Close fullscreen browser',
        backgroundInert: document.querySelector('.mobile-control-pane')?.hasAttribute('inert') ?? false,
        controlsStrip: Boolean(strip),
        controlsViewToggle: Boolean(strip?.querySelector('button[aria-label="Toggle fullscreen view controls"]')),
        controlsViewport: Boolean(strip?.querySelector('button[aria-label="Edit fullscreen browser viewport"]')),
        controlsExit: Boolean(strip?.querySelector('button[aria-label="Close fullscreen browser"]')),
        remoteToolsOpen: Boolean(document.querySelector('[aria-label="Remote browser tools"]')),
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
            "fullscreen local controls available",
            bool(fullscreen.get("controlsStrip"))
            and bool(fullscreen.get("controlsViewToggle"))
            and bool(fullscreen.get("controlsViewport"))
            and bool(fullscreen.get("controlsExit")),
            fullscreen,
        )
        add_check(
            result,
            "fullscreen starts with remote browser tools collapsed",
            not bool(fullscreen.get("remoteToolsOpen")),
            fullscreen,
        )
        add_check(
            result,
            "fullscreen preserves one canvas",
            pre_fullscreen_canvas.get("count") == 1 and fullscreen.get("canvasCount") == 1,
            {"before": pre_fullscreen_canvas, "fullscreen": fullscreen},
        )
    take_screenshot(browser, result, output_dir, name, "fullscreen", width, height)

    if profile_id:
        opened_fullscreen_tuning = browser.eval(r"""(() => {
          const button = document.querySelector('button[aria-label="Toggle fullscreen view controls"]');
          if (!button) return false;
          button.click();
          return true;
        })()""")
        add_check(
            result,
            "fullscreen view tuning action available",
            bool(opened_fullscreen_tuning),
            {"clicked": bool(opened_fullscreen_tuning)},
        )
        browser.wait_for(
            "!!document.querySelector('[aria-label=\"Fullscreen view controls\"]')",
            "fullscreen view tuning drawer",
            5,
        )
        fullscreen_tuning = browser.eval(r"""(() => {
          const root = document.querySelector('[aria-label="Fullscreen view controls"]');
          const zoom = root?.querySelector('#mobile-fullscreen-browser-zoom');
          return {
            visible: Boolean(root),
            zoom: Boolean(zoom),
            zoomValue: zoom?.value ?? null,
          };
        })()""")
        add_check(
            result,
            "fullscreen view tuning drawer exposes zoom",
            bool(fullscreen_tuning.get("visible")) and bool(fullscreen_tuning.get("zoom")),
            fullscreen_tuning,
        )

        opened_fullscreen_viewport = browser.eval(r"""(() => {
          const button = document.querySelector('button[aria-label="Edit fullscreen browser viewport"]');
          if (!button) return false;
          button.click();
          return true;
        })()""")
        add_check(
            result,
            "fullscreen viewport editor action available",
            bool(opened_fullscreen_viewport),
            {"clicked": bool(opened_fullscreen_viewport)},
        )
        browser.wait_for(
            "!!document.querySelector('[aria-label=\"Fullscreen viewport controls\"]')",
            "fullscreen viewport editor",
            5,
        )
        fullscreen_editor = browser.eval(r"""(() => {
          const root = document.querySelector('[aria-label="Fullscreen viewport controls"]');
          const inputs = [...(root?.querySelectorAll('input[type="number"]') ?? [])];
          const apply = [...(root?.querySelectorAll('button') ?? [])]
            .find((button) => button.textContent?.trim() === 'Apply' && !button.disabled);
          return {
            visible: Boolean(root),
            inputCount: inputs.length,
            values: inputs.map((input) => input.value),
            canApply: Boolean(apply),
          };
        })()""")
        add_check(
            result,
            "fullscreen viewport editor renders editable width height and apply",
            bool(fullscreen_editor.get("visible"))
            and fullscreen_editor.get("inputCount") == 2
            and bool(fullscreen_editor.get("canApply")),
            fullscreen_editor,
        )
        applied = browser.eval(r"""(() => {
          const root = document.querySelector('[aria-label="Fullscreen viewport controls"]');
          const apply = [...(root?.querySelectorAll('button') ?? [])]
            .find((button) => button.textContent?.trim() === 'Apply' && !button.disabled);
          if (!apply) return false;
          apply.click();
          return true;
        })()""")
        add_check(result, "fullscreen viewport apply action available", bool(applied), {"clicked": applied})
        browser.wait_for(
            "document.querySelector('[aria-label=\"Fullscreen viewport controls\"]')?.innerText.includes('Saved')",
            "fullscreen viewport save",
            timeout,
        )
        take_screenshot(browser, result, output_dir, name, "fullscreen-viewport", width, height)

    escaped = browser.eval(r"""(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      return true;
    })()""")
    add_check(result, "fullscreen escape action available", bool(escaped), {"dispatched": bool(escaped)})
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


def run_access_dashboard_gate(
    browser: AgentBrowser,
    base_url: str,
    output_dir: Path,
    auth_token: str,
    timeout: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": "access-dashboard-iphone-14-portrait",
        "width": 390,
        "height": 844,
        "checks": [],
        "errors": [],
        "screenshots": [],
    }
    browser.run("set", "viewport", "390", "844")
    browser.run("open", base_url)
    authenticate_workspace(browser, auth_token)
    browser.wait_for("!!document.querySelector('.mobile-split-root')", "mobile workspace", 20)
    ensure_browser_tools_open(browser)
    browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Toggle browser administration"]');
      if (button?.getAttribute('aria-expanded') !== 'true') button?.click();
      return Boolean(button);
    })()""")
    clicked = browser.eval(r"""(() => {
      const button = document.querySelector('button[aria-label="Browser access controls"]');
      if (!button) return false;
      button.click();
      return true;
    })()""")
    add_check(result, "access controls action available", bool(clicked), {"clicked": clicked})
    browser.wait_for(
        "!!document.querySelector('main[aria-label=\"Browser access controls\"]')",
        "access dashboard",
        timeout,
    )
    dashboard = browser.eval(ACCESS_DASHBOARD_SCRIPT)
    add_check(result, "access dashboard rendered", bool(dashboard.get("ready")), dashboard)
    add_check(
        result,
        "access dashboard has no horizontal overflow",
        dashboard.get("scrollWidth", 390) <= dashboard.get("innerWidth", 390) + 1
        and (dashboard.get("dashboard") or {}).get("scrollWidth", 0)
        <= (dashboard.get("dashboard") or {}).get("clientWidth", 0) + 1,
        {
            "pageScrollWidth": dashboard.get("scrollWidth"),
            "innerWidth": dashboard.get("innerWidth"),
            "dashboard": dashboard.get("dashboard"),
        },
    )
    touch = dashboard.get("touchTargets") or {}
    add_check(result, "access dashboard 44px visible touch targets", not touch.get("offenders"), touch)
    take_screenshot(
        browser,
        result,
        output_dir,
        result["name"],
        "dashboard",
        result["width"],
        result["height"],
    )
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
    parser.add_argument(
        "--auth-token-env",
        help=(
            "Optional environment-variable name containing a disposable E2E "
            "bootstrap token; its value is never reported."
        ),
    )
    parser.add_argument(
        "--access-dashboard",
        action="store_true",
        help="Also run an authenticated iPhone-size Access Dashboard touch-target and overflow gate.",
    )
    parser.add_argument("--keep-session", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    auth_token = os.environ.get(args.auth_token_env) if args.auth_token_env else None
    if args.auth_token_env and not auth_token:
        raise GateError(f"--auth-token-env is set but {args.auth_token_env} is empty")
    if args.access_dashboard and not auth_token:
        raise GateError("--access-dashboard requires --auth-token-env")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    media_emulation_script = args.output_dir / "mobile-media-emulation.js"
    media_emulation_script.write_text(
        mobile_gate_init_script(),
        encoding="utf-8",
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": public_url(args.base_url),
        "profile_id": args.profile_id,
        "live_vnc_required": bool(args.profile_id),
        "authenticated_run": bool(auth_token),
        "pointer_emulation": "iPhone 14 device profile plus coarse-pointer matchMedia init script",
        "remote_probe_url": public_url(args.remote_probe_url) if args.remote_probe_url else None,
        "session": args.session,
        "access_dashboard_required": bool(args.access_dashboard),
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
                    auth_token,
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
        if args.access_dashboard:
            try:
                report["access_dashboard"] = run_access_dashboard_gate(
                    browser,
                    args.base_url,
                    args.output_dir,
                    auth_token or "",
                    args.timeout,
                )
            except Exception as exc:
                report["access_dashboard"] = {
                    "name": "access-dashboard-iphone-14-portrait",
                    "width": 390,
                    "height": 844,
                    "passed": False,
                    "checks": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
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
        and (not args.access_dashboard or bool((report.get("access_dashboard") or {}).get("passed")))
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
        "access_dashboard": (
            {
                "passed": (report.get("access_dashboard") or {}).get("passed", False),
                "errors": (report.get("access_dashboard") or {}).get("errors", []),
            }
            if args.access_dashboard
            else None
        ),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
