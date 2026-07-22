#!/usr/bin/env python3
"""Self-checks for release_acceptance_gate.py."""

from __future__ import annotations

import importlib.util
import json
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


NOW = "2026-07-21T16:00:00+00:00"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def mobile_report() -> dict[str, object]:
    return {
        "passed": True,
        "finished_at": "2026-07-21T15:50:00+00:00",
        "base_url": "http://127.0.0.1:18109/?token=local-secret",
        "viewports": [
            {
                "name": "iphone-14-portrait",
                "passed": True,
                "checks": [
                    {"name": name, "passed": True}
                    for name in sorted(release_gate.REQUIRED_MOBILE_CHECKS)
                ],
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
