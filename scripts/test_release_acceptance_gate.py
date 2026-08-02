#!/usr/bin/env python3
"""Self-checks for release_acceptance_gate.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "release_acceptance_gate.py"
SPEC = importlib.util.spec_from_file_location("release_acceptance_gate", RUNNER)
assert SPEC and SPEC.loader
release_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_gate
SPEC.loader.exec_module(release_gate)

PROOF_PATH = REPO_ROOT / "scripts" / "phonefit_idempotent_retap_proof.py"
PROOF_SPEC = importlib.util.spec_from_file_location(
    "phonefit_idempotent_retap_proof", PROOF_PATH
)
assert PROOF_SPEC and PROOF_SPEC.loader
# mobile_ui_gate is imported by the proof script; load it under the same name first.
_MOBILE_PATH = REPO_ROOT / "scripts" / "mobile_ui_gate.py"
_MOBILE_SPEC = importlib.util.spec_from_file_location("mobile_ui_gate", _MOBILE_PATH)
assert _MOBILE_SPEC and _MOBILE_SPEC.loader
_mobile_ui_gate = importlib.util.module_from_spec(_MOBILE_SPEC)
sys.modules["mobile_ui_gate"] = _mobile_ui_gate
_MOBILE_SPEC.loader.exec_module(_mobile_ui_gate)
phonefit_proof = importlib.util.module_from_spec(PROOF_SPEC)
sys.modules[PROOF_SPEC.name] = phonefit_proof
PROOF_SPEC.loader.exec_module(phonefit_proof)


NOW = "2026-07-21T16:00:00+00:00"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def phone_fit_re_tap_check() -> dict[str, object]:
    """Minimal re-tap check that satisfies traffic fail-closed evidence."""
    return {
        "name": release_gate.PHONE_FIT_RE_TAP_CHECK,
        "passed": True,
        "evidence": {
            "trafficChecked": True,
            "trafficOk": True,
            "counterInstalled": True,
            "updateCount": 0,
            "stopCount": 0,
            "launchCount": 0,
            "passed": True,
        },
    }


def mobile_report() -> dict[str, object]:
    checks: list[dict[str, object]] = []
    for name in sorted(release_gate.REQUIRED_MOBILE_CHECKS):
        if name == release_gate.PHONE_FIT_RE_TAP_CHECK:
            checks.append(phone_fit_re_tap_check())
        else:
            checks.append({"name": name, "passed": True})
    return {
        "passed": True,
        "finished_at": "2026-07-21T15:50:00+00:00",
        "base_url": "http://127.0.0.1:18109/?token=local-secret",
        "viewports": [
            {
                "name": "iphone-14-portrait",
                "passed": True,
                "checks": checks,
                "screenshots": [{"path": "/Users/example/private/screen.png"}],
            },
            {
                "name": "iphone-se-portrait",
                "passed": True,
                "checks": [{"name": "touch targets", "passed": True}],
                "screenshots": [{"path": "/Users/example/private/screen-2.png"}],
            },
        ],
    }


def vision_report() -> dict[str, object]:
    return {
        "verdict": "PASS",
        "generated_at": "2026-07-21T15:55:00+00:00",
        "summary": "UI passes at http://127.0.0.1:18109/ with token=vision-secret",
    }


def streaming_report() -> dict[str, object]:
    return {
        "finished_at": "2026-07-21T15:58:00+00:00",
        "config": {"iterations": 3},
        "results": [
            {
                "candidate": {"id": "kasm-vnc", "name": "Kasm VNC", "type": "websocket"},
                "status": "measured",
                "availability": "available",
                "summary": {"runs": 3, "success_rate_pct": 100.0},
            },
            {
                "candidate": {"id": "selkies", "name": "Selkies", "type": "websocket"},
                "status": "not_installed",
                "availability": "not_measured",
                "reason": "not installed",
                "summary": {"runs": 0},
            },
        ],
    }


class ReleaseAcceptanceGateTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("python3.11"), "Python 3.11 is not installed")
    def test_release_gate_compiles_on_python_311(self) -> None:
        python_311 = shutil.which("python3.11")
        assert python_311
        completed = subprocess.run(
            [
                python_311,
                "-c",
                "from pathlib import Path; "
                "source = Path(__import__('sys').argv[1]).read_text(encoding='utf-8'); "
                "compile(source, __import__('sys').argv[1], 'exec')",
                str(RUNNER),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def run_gate(self, root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--mobile-report",
                str(root / "mobile.json"),
                "--vision-verdict",
                str(root / "vision.json"),
                "--streaming-report",
                str(root / "streaming.json"),
                "--quality-command",
                f"unit tests::{sys.executable} -c \"print('ok token=quality-secret')\"",
                "--output-json",
                str(root / "release.json"),
                "--output-markdown",
                str(root / "release.md"),
                "--now",
                NOW,
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_release_gate_passes_with_fresh_redacted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_json(root / "mobile.json", mobile_report())
            write_json(root / "vision.json", vision_report())
            write_json(root / "streaming.json", streaming_report())

            completed = self.run_gate(root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((root / "release.json").read_text(encoding="utf-8"))
            markdown = (root / "release.md").read_text(encoding="utf-8")
            self.assertTrue(report["passed"])
            self.assertEqual(
                report["gates"]["mobile_ui_ux"]["total_checks"],
                len(release_gate.REQUIRED_MOBILE_CHECKS) + 1,
            )
            self.assertEqual(report["gates"]["mobile_ui_ux"]["total_screenshots"], 2)
            self.assertEqual(report["gates"]["streaming"]["measured_candidates"], 1)
            combined = json.dumps(report) + markdown + completed.stdout
            for private_value in (
                "127.0.0.1",
                "18109",
                "local-secret",
                "vision-secret",
                "quality-secret",
                str(root),
                "/Users/example",
            ):
                self.assertNotIn(private_value, combined)
            self.assertIn("Release Acceptance Gate", markdown)
            self.assertIn("Status: `PASS`", markdown)

    def test_release_gate_fails_closed_on_stale_mobile_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stale_mobile = mobile_report()
            stale_mobile["finished_at"] = "2026-07-18T15:50:00+00:00"
            write_json(root / "mobile.json", stale_mobile)
            write_json(root / "vision.json", vision_report())
            write_json(root / "streaming.json", streaming_report())

            completed = self.run_gate(root)

            self.assertEqual(completed.returncode, 1)
            report = json.loads((root / "release.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertFalse(report["gates"]["mobile_ui_ux"]["passed"])
            self.assertTrue(any("stale" in failure for failure in report["failures"]))

    def test_release_gate_fails_closed_on_failed_quality_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_json(root / "mobile.json", mobile_report())
            write_json(root / "vision.json", vision_report())
            write_json(root / "streaming.json", streaming_report())
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--mobile-report",
                    str(root / "mobile.json"),
                    "--vision-verdict",
                    str(root / "vision.json"),
                    "--quality-command",
                    f"broken::{sys.executable} -c \"import sys; sys.exit(7)\"",
                    "--output-json",
                    str(root / "release.json"),
                    "--output-markdown",
                    str(root / "release.md"),
                    "--now",
                    NOW,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            report = json.loads((root / "release.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertFalse(report["gates"]["quality"]["passed"])

    def test_release_gate_fails_closed_on_missing_required_vision_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_json(root / "mobile.json", mobile_report())
            write_json(root / "streaming.json", streaming_report())

            completed = self.run_gate(root)

            self.assertEqual(completed.returncode, 1)
            report = json.loads((root / "release.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertFalse(report["gates"]["vision"]["passed"])
            self.assertTrue(any("vision is missing" in failure for failure in report["failures"]))

    def test_streaming_report_fails_when_measured_candidate_is_unavailable(self) -> None:
        report = streaming_report()
        first = report["results"][0]  # type: ignore[index]
        assert isinstance(first, dict)
        first["availability"] = "unavailable"
        with self.assertRaises(release_gate.GateError):
            release_gate.summarize_streaming(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

    def test_streaming_report_fails_when_measured_candidate_has_partial_sample_failure(self) -> None:
        report = streaming_report()
        report["config"] = {"iterations": 20}
        first = report["results"][0]  # type: ignore[index]
        assert isinstance(first, dict)
        first["summary"] = {"runs": 20, "success_rate_pct": 95.0}

        with self.assertRaises(release_gate.GateError):
            release_gate.summarize_streaming(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

    def test_streaming_report_passes_when_measured_candidate_has_all_samples_successful(self) -> None:
        report = streaming_report()
        report["config"] = {"iterations": 20}
        first = report["results"][0]  # type: ignore[index]
        assert isinstance(first, dict)
        first["summary"] = {"runs": 20, "success_rate_pct": 100.0}

        summary = release_gate.summarize_streaming(
            report,
            release_gate.parse_time(NOW, "now"),
            24,
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["measured_candidates"], 1)

    def test_mobile_summary_requires_authenticated_access_dashboard_evidence(self) -> None:
        report = mobile_report()
        report["access_dashboard_required"] = True
        report["authenticated_run"] = True
        report["access_dashboard"] = {
            "passed": True,
            "checks": [
                {"name": name, "passed": True}
                for name in sorted(release_gate.REQUIRED_ACCESS_DASHBOARD_CHECKS)
            ],
            "screenshots": [{"path": "/Users/example/private/access.png"}],
        }

        summary = release_gate.summarize_mobile(
            report,
            release_gate.parse_time(NOW, "now"),
            24,
        )

        self.assertEqual(
            summary["total_checks"],
            len(release_gate.REQUIRED_MOBILE_CHECKS)
            + len(release_gate.REQUIRED_ACCESS_DASHBOARD_CHECKS)
            + 1,
        )
        self.assertEqual(summary["total_screenshots"], 3)
        self.assertTrue(summary["authenticated_run"])
        assert isinstance(report["access_dashboard"], dict)
        report["access_dashboard"]["passed"] = False  # type: ignore[index]
        with self.assertRaises(release_gate.GateError):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

    def test_mobile_summary_requires_critical_mobile_regression_checks(self) -> None:
        report = mobile_report()
        first_viewport = report["viewports"][0]  # type: ignore[index]
        assert isinstance(first_viewport, dict)
        checks = first_viewport["checks"]
        assert isinstance(checks, list)
        checks.pop()

        with self.assertRaisesRegex(
            release_gate.GateError,
            "mobile UI/UX gate is missing required checks",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

    def test_required_mobile_checks_include_phonefit_re_tap_idempotent(self) -> None:
        """Release must fail closed without PhoneFit re-tap idempotency evidence."""
        self.assertIn(
            release_gate.PHONE_FIT_RE_TAP_CHECK,
            release_gate.REQUIRED_MOBILE_CHECKS,
        )
        report = mobile_report()
        first_viewport = report["viewports"][0]  # type: ignore[index]
        assert isinstance(first_viewport, dict)
        checks = first_viewport["checks"]
        assert isinstance(checks, list)
        first_viewport["checks"] = [
            item
            for item in checks
            if isinstance(item, dict)
            and item.get("name") != release_gate.PHONE_FIT_RE_TAP_CHECK
        ]

        with self.assertRaisesRegex(
            release_gate.GateError,
            "fullscreen Phone fit re-tap stays idempotent",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

    def test_phone_fit_re_tap_requires_zero_mutate_traffic_evidence(self) -> None:
        """Re-tap check name alone is not enough; traffic zeros must be proven."""
        report = mobile_report()
        first_viewport = report["viewports"][0]  # type: ignore[index]
        assert isinstance(first_viewport, dict)
        checks = first_viewport["checks"]
        assert isinstance(checks, list)
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item["evidence"] = {"passed": True}  # UI-only, no traffic
                break

        with self.assertRaisesRegex(
            release_gate.GateError,
            "must prove update/stop/launch traffic",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

        # Missing evidence object entirely
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item.pop("evidence", None)
                break
        with self.assertRaisesRegex(
            release_gate.GateError,
            "missing mutate-traffic evidence",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

        # Non-zero launch must fail
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item["evidence"] = {
                    "trafficChecked": True,
                    "trafficOk": False,
                    "counterInstalled": True,
                    "updateCount": 0,
                    "stopCount": 0,
                    "launchCount": 1,
                }
                break
        with self.assertRaisesRegex(
            release_gate.GateError,
            "must prove update/stop/launch traffic",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

        # Zeros without installed counter hooks must fail (false-zero guard)
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item["evidence"] = {
                    "trafficChecked": True,
                    "trafficOk": True,
                    "counterInstalled": False,
                    "updateCount": 0,
                    "stopCount": 0,
                    "launchCount": 0,
                }
                break
        with self.assertRaisesRegex(
            release_gate.GateError,
            "installed counters",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

        # Missing counterInstalled key must fail
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item["evidence"] = {
                    "trafficChecked": True,
                    "trafficOk": True,
                    "updateCount": 0,
                    "stopCount": 0,
                    "launchCount": 0,
                }
                break
        with self.assertRaisesRegex(
            release_gate.GateError,
            "installed counters",
        ):
            release_gate.summarize_mobile(
                report,
                release_gate.parse_time(NOW, "now"),
                24,
            )

        # Healthy traffic evidence passes
        for item in checks:
            if isinstance(item, dict) and item.get("name") == release_gate.PHONE_FIT_RE_TAP_CHECK:
                item["evidence"] = phone_fit_re_tap_check()["evidence"]
                break
        summary = release_gate.summarize_mobile(
            report,
            release_gate.parse_time(NOW, "now"),
            24,
        )
        self.assertTrue(summary["passed"])

    def test_proof_builder_evidence_satisfies_release_traffic_contract(self) -> None:
        """Durable proof evidence must be shape-compatible with release fail-closed."""
        healthy = phonefit_proof.build_retap_gate_evidence(
            status_text="Already matches - no restart",
            canvas_count=1,
            width="390",
            height="844",
            page_mutate={"installed": True, "update": 0, "stop": 0, "launch": 0},
            playwright_mutate={"update": 0, "stop": 0, "launch": 0},
        )
        self.assertTrue(healthy["passed"])
        self.assertTrue(healthy["counterInstalled"])
        self.assertTrue(healthy["trafficChecked"])
        self.assertTrue(healthy["trafficOk"])
        self.assertTrue(healthy["playwrightTrafficOk"])
        # Release acceptance must accept this evidence blob as-is.
        release_gate.assert_phone_fit_re_tap_traffic_evidence(
            [
                {
                    "name": release_gate.PHONE_FIT_RE_TAP_CHECK,
                    "passed": True,
                    "evidence": healthy,
                }
            ]
        )

        missing_hooks = phonefit_proof.build_retap_gate_evidence(
            status_text="Already matches - no restart",
            canvas_count=1,
            width="390",
            height="844",
            page_mutate={"installed": False, "update": 0, "stop": 0, "launch": 0},
            playwright_mutate={"update": 0, "stop": 0, "launch": 0},
        )
        self.assertFalse(missing_hooks["passed"])
        self.assertIs(missing_hooks["counterInstalled"], False)

        pw_leak = phonefit_proof.build_retap_gate_evidence(
            status_text="Already matches - no restart",
            canvas_count=1,
            width="390",
            height="844",
            page_mutate={"installed": True, "update": 0, "stop": 0, "launch": 0},
            playwright_mutate={"update": 0, "stop": 0, "launch": 1},
        )
        self.assertFalse(pw_leak["passed"])
        self.assertFalse(pw_leak["playwrightTrafficOk"])

        page_leak = phonefit_proof.build_retap_gate_evidence(
            status_text="Already matches - no restart",
            canvas_count=1,
            width="390",
            height="844",
            page_mutate={"installed": True, "update": 1, "stop": 0, "launch": 0},
            playwright_mutate={"update": 0, "stop": 0, "launch": 0},
        )
        self.assertFalse(page_leak["passed"])
        self.assertFalse(page_leak["trafficOk"])

    def test_redaction_covers_tailnet_ipv6_and_local_paths(self) -> None:
        source = (
            "http://device.example.ts.net:8080/?token=secret "
            "http://[fd7a:115c:a1e0::1]:3000/live "
            "/tmp/private/report.json C:\\Users\\example\\report.json"
        )

        redacted = release_gate.redact_text(source)

        self.assertNotIn("example.ts.net", redacted)
        self.assertNotIn("fd7a:115c", redacted)
        self.assertNotIn("/tmp/private", redacted)
        self.assertNotIn("C:\\Users", redacted)
        self.assertIn("[redacted-local-endpoint]", redacted)
        self.assertIn("[redacted-local-path]", redacted)


if __name__ == "__main__":
    unittest.main()
