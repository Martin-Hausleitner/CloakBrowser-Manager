#!/usr/bin/env python3
"""Self-checks for streaming_benchmark_runner.py."""

from __future__ import annotations

import http.server
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "streaming_benchmark_runner.py"


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.headers.get("X-Benchmark-Test") != "ok":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args: object) -> None:
        return


class FlakyOnceHandler(http.server.BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self) -> None:
        type(self).calls += 1
        if type(self).calls == 1:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args: object) -> None:
        return


class StreamingBenchmarkRunnerTest(unittest.TestCase):
    def test_public_report_distinguishes_measured_and_unmeasured_candidates(self) -> None:
        with socketserver.TCPServer(("127.0.0.1", 0), HealthHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = root / "config.json"
                output = root / "out"
                latest = root / "latest.md"
                config.write_text(
                    json.dumps(
                        {
                            "candidates": [
                                {
                                    "id": "local-http",
                                    "name": "Local HTTP",
                                    "type": "http",
                                    "url": f"http://127.0.0.1:{server.server_address[1]}/health?token=url-secret",
                                    "headers": {
                                        "X-Benchmark-Test": "ok",
                                        "Authorization": "Bearer header-secret",
                                    },
                                },
                                {
                                    "id": "ready-command",
                                    "name": "Ready command",
                                    "type": "command",
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        "import time; print('READY', flush=True); time.sleep(10)",
                                    ],
                                    "ready_regex": "READY",
                                },
                                {
                                    "id": "missing-tool",
                                    "name": "Missing Tool",
                                    "type": "command",
                                    "command": ["definitely-not-installed-cloak-stream-tool"],
                                    "requires_executable": "definitely-not-installed-cloak-stream-tool",
                                },
                                {
                                    "id": "architecture",
                                    "name": "Architecture",
                                    "type": "architecture",
                                    "architecture_note": "documented only",
                                },
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        "--config",
                        str(config),
                        "--output-dir",
                        str(output),
                        "--iterations",
                        "2",
                        "--latest-markdown",
                        str(latest),
                        "--strict",
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                events = [json.loads(line) for line in completed.stdout.splitlines()]
                self.assertEqual(events[0]["event"], "run_started")
                self.assertEqual(events[-1]["event"], "run_finished")
                report = json.loads((output / "streaming-benchmark-report.json").read_text())
                by_id = {item["candidate"]["id"]: item for item in report["results"]}
                self.assertEqual(by_id["local-http"]["status"], "measured")
                self.assertEqual(by_id["local-http"]["availability"], "available")
                self.assertNotIn("headers", by_id["local-http"]["candidate"])
                self.assertNotIn("url", by_id["local-http"]["candidate"])
                self.assertEqual(by_id["local-http"]["summary"]["runs"], 2)
                self.assertIn("p95", by_id["local-http"]["summary"]["timings_ms"]["total_ms"])
                self.assertEqual(by_id["local-http"]["summary"]["success_rate_pct"], 100.0)
                self.assertEqual(by_id["ready-command"]["status"], "measured")
                self.assertEqual(by_id["ready-command"]["availability"], "available")
                self.assertTrue(by_id["ready-command"]["measurements"][0]["ready"])
                self.assertNotIn("stdout_tail", by_id["ready-command"]["measurements"][0])
                self.assertEqual(by_id["missing-tool"]["status"], "not_installed")
                self.assertEqual(by_id["architecture"]["status"], "architecture_only")
                self.assertIn("not_installed", latest.read_text(encoding="utf-8"))
                serialized_report = json.dumps(report)
                serialized_events = completed.stdout
                for private_value in (
                    "header-secret",
                    "url-secret",
                    str(config),
                    str(root),
                    "time.sleep(10)",
                ):
                    self.assertNotIn(private_value, serialized_report)
                    self.assertNotIn(private_value, serialized_events)
            server.shutdown()

    def test_strict_fails_when_measured_candidate_has_one_failed_sample(self) -> None:
        FlakyOnceHandler.calls = 0
        with socketserver.TCPServer(("127.0.0.1", 0), FlakyOnceHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = root / "config.json"
                output = root / "out"
                config.write_text(
                    json.dumps(
                        {
                            "candidates": [
                                {
                                    "id": "flaky-http",
                                    "name": "Flaky HTTP",
                                    "type": "http",
                                    "url": f"http://127.0.0.1:{server.server_address[1]}/health",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        "--config",
                        str(config),
                        "--output-dir",
                        str(output),
                        "--iterations",
                        "20",
                        "--strict",
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )

                self.assertEqual(completed.returncode, 1, completed.stderr)
                report = json.loads((output / "streaming-benchmark-report.json").read_text())
                result = report["results"][0]
                self.assertEqual(result["status"], "measured")
                self.assertEqual(result["availability"], "unavailable")
                self.assertEqual(result["summary"]["runs"], 20)
                self.assertEqual(result["summary"]["success_rate_pct"], 95.0)
            server.shutdown()

    def test_strict_passes_when_measured_candidate_has_all_samples_successful(self) -> None:
        with socketserver.TCPServer(("127.0.0.1", 0), HealthHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = root / "config.json"
                output = root / "out"
                config.write_text(
                    json.dumps(
                        {
                            "candidates": [
                                {
                                    "id": "stable-http",
                                    "name": "Stable HTTP",
                                    "type": "http",
                                    "url": f"http://127.0.0.1:{server.server_address[1]}/health",
                                    "headers": {"X-Benchmark-Test": "ok"},
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        "--config",
                        str(config),
                        "--output-dir",
                        str(output),
                        "--iterations",
                        "20",
                        "--strict",
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                report = json.loads((output / "streaming-benchmark-report.json").read_text())
                result = report["results"][0]
                self.assertEqual(result["availability"], "available")
                self.assertEqual(result["summary"]["runs"], 20)
                self.assertEqual(result["summary"]["success_rate_pct"], 100.0)
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
