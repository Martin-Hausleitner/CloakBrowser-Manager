#!/usr/bin/env python3
"""Self-checks for mobile_ui_gate.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "mobile_ui_gate.py"
SPEC = importlib.util.spec_from_file_location("mobile_ui_gate", RUNNER_PATH)
assert SPEC and SPEC.loader
mobile_ui_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mobile_ui_gate
SPEC.loader.exec_module(mobile_ui_gate)


class MobileUiGateTest(unittest.TestCase):
    def test_shortcut_toggle_state_treats_second_shortcuts_as_closed(self) -> None:
        state = mobile_ui_gate.mobile_shortcut_toggle_state(
            tools_after_k=True,
            tools_closed_after_second_k=True,
            chat_after_j=True,
            fullscreen_after_b=True,
            fullscreen_closed_after_second_b=True,
            input_kept_closed=True,
        )

        self.assertTrue(mobile_ui_gate.mobile_shortcut_toggle_passed(state))
        self.assertTrue(state["toolsClosedAfterSecondK"])
        self.assertTrue(state["fullscreenClosedAfterSecondB"])

    def test_shortcut_toggle_state_fails_when_second_shortcuts_leave_ui_open(self) -> None:
        state = mobile_ui_gate.mobile_shortcut_toggle_state(
            tools_after_k=True,
            tools_closed_after_second_k=False,
            chat_after_j=True,
            fullscreen_after_b=True,
            fullscreen_closed_after_second_b=False,
            input_kept_closed=True,
        )

        self.assertFalse(mobile_ui_gate.mobile_shortcut_toggle_passed(state))

    def test_codex_computer_use_test_reply_is_deterministic(self) -> None:
        self.assertEqual(
            mobile_ui_gate.codex_computer_use_test_reply("Mobile gate iphone"),
            "Codex Computer Use test harness accepted: Mobile gate iphone",
        )

    def test_mobile_gate_init_script_injects_available_codex_host_harness(self) -> None:
        script = mobile_ui_gate.mobile_gate_init_script()

        self.assertIn("window.cloakBrowserHarness", script)
        self.assertIn("codex-computer-use-mobile-gate", script)
        self.assertIn("codex-computer-use-test-host", script)
        self.assertIn("chat: true", script)
        self.assertIn("send: async", script)
        self.assertIn(mobile_ui_gate.CODEX_COMPUTER_USE_TEST_REPLY_PREFIX, script)
        self.assertNotIn("Queued locally:", script)
        self.assertNotIn("mode: 'unavailable'", script)

    def test_access_dashboard_opens_browser_tools_before_access_controls(self) -> None:
        class FakeBrowser:
            def __init__(self) -> None:
                self.events: list[str] = []

            def run(self, *args: str) -> None:
                self.events.append(f"run:{':'.join(args)}")

            def wait_for(self, script: str, label: str, _timeout: float) -> bool:
                self.events.append(f"wait:{label}")
                return True

            def eval(self, script: str):
                if "Browser access controls" in script and "button.click" in script:
                    self.events.append("click:access-controls")
                    return True
                if "ACCESS_DASHBOARD_SCRIPT" in script:
                    raise AssertionError("script constant name should not be passed to eval")
                self.events.append("eval:access-dashboard")
                return {
                    "ready": True,
                    "scrollWidth": 390,
                    "innerWidth": 390,
                    "dashboard": {"scrollWidth": 390, "clientWidth": 390},
                    "touchTargets": {"offenders": []},
                }

        browser = FakeBrowser()

        def fake_tools_open(fake_browser: FakeBrowser, timeout: float = 5) -> None:
            self.assertIs(fake_browser, browser)
            self.assertEqual(timeout, 5)
            browser.events.append("tools:open")

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            mobile_ui_gate,
            "authenticate_workspace",
            lambda _browser, _token: browser.events.append("auth"),
        ), patch.object(
            mobile_ui_gate,
            "ensure_browser_tools_open",
            fake_tools_open,
        ), patch.object(
            mobile_ui_gate,
            "take_screenshot",
            lambda *_args, **_kwargs: None,
        ):
            result = mobile_ui_gate.run_access_dashboard_gate(
                browser,
                "http://127.0.0.1:8080/",
                Path(temp_dir),
                "token",
                2,
            )

        self.assertTrue(result["passed"])
        self.assertLess(browser.events.index("tools:open"), browser.events.index("click:access-controls"))


if __name__ == "__main__":
    unittest.main()
