#!/usr/bin/env python3
"""Contract tests for the Safari/WebKit mobile gate without a real Safari session."""

from __future__ import annotations

import argparse
import base64
import http.server
import importlib.util
import json
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "mobile_webkit_gate.py"
SPEC = importlib.util.spec_from_file_location("mobile_webkit_gate", RUNNER_PATH)
assert SPEC and SPEC.loader
webkit_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = webkit_gate
SPEC.loader.exec_module(webkit_gate)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def realistic_png() -> bytes:
    """Build a valid, nontrivial one-pixel PNG like a browser screenshot."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixel = zlib.compress(b"\x00\x00\x00\x00\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"tEXt", b"webKitGate\x00" + b"x" * 5000)
        + png_chunk(b"IDAT", pixel)
        + png_chunk(b"IEND", b"")
    )


class FakeSafariDriver(http.server.BaseHTTPRequestHandler):
    screenshot = base64.b64encode(realistic_png()).decode("ascii")

    def respond(self, status: int, value: object) -> None:
        payload = json.dumps({"value": value}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - WebDriver route naming
        if self.path == "/status":
            self.respond(200, {"ready": True})
        elif self.path == "/session/fake-safari/screenshot":
            self.respond(200, self.screenshot)
        else:
            self.respond(404, {"error": "unknown command"})

    def do_DELETE(self) -> None:  # noqa: N802 - WebDriver route naming
        self.respond(200, None)

    def do_POST(self) -> None:  # noqa: N802 - WebDriver route naming
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/session":
            self.respond(200, {"sessionId": "fake-safari"})
        elif self.path.endswith("/execute/sync"):
            script = body.get("script", "")
            if "mobile-split-root" in script:
                self.respond(
                    200,
                    json.dumps(
                        {
                            "ready": True,
                            "browserFrame": True,
                            "composer": True,
                            "benchmarkButton": True,
                            "width": 390,
                            "height": 844,
                            "scrollWidth": 390,
                            "clientWidth": 390,
                        }
                    ),
                )
            elif "button.click" in script:
                self.respond(200, True)
            else:
                self.respond(
                    200,
                    json.dumps(
                        {
                            "heading": "Live streaming benchmark results",
                            "cards": ["Guacamole", "KasmVNC", "noVNC", "Selkies", "Sunshine"],
                            "measured": True,
                            "notInstalled": True,
                            "architectureOnly": True,
                            "width": 390,
                            "height": 844,
                            "scrollWidth": 390,
                            "clientWidth": 390,
                        }
                    ),
                )
        else:
            self.respond(200, None)

    def log_message(self, *_args: object) -> None:
        return


class MobileWebKitGateTest(unittest.TestCase):
    def test_public_url_removes_user_info_and_query(self) -> None:
        self.assertEqual(
            webkit_gate.public_url("http://user:secret@127.0.0.1:8080/path?key=secret#fragment"),
            "http://127.0.0.1:8080/path",
        )

    def test_loopback_driver_url_rejects_nonlocal_hosts(self) -> None:
        with self.assertRaises(webkit_gate.GateError):
            webkit_gate.loopback_driver_url("http://example.com:4444")

    def test_gate_publishes_a_passed_webkit_report_from_a_webdriver_contract(self) -> None:
        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSafariDriver) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "webkit-report"
                args = argparse.Namespace(
                    base_url="http://127.0.0.1:18095/?test-secret=ignored",
                    output_dir=output,
                    driver_url=f"http://127.0.0.1:{server.server_address[1]}",
                    start_driver=False,
                    safaridriver="unused",
                    width=390,
                    height=844,
                    min_benchmark_cards=5,
                    timeout=2.0,
                    settle_seconds=0.0,
                )
                report, code = webkit_gate.run_gate(args)
                self.assertEqual(code, 0)
                self.assertTrue(report["passed"])
                self.assertEqual(report["status"], "passed")
                self.assertEqual(len(report["checks"]), 8)
                persisted = json.loads((output / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["base_url"], "http://127.0.0.1:18095/")
                self.assertNotIn("test-secret", json.dumps(persisted))
                self.assertTrue((output / "safari-mobile-benchmark-dashboard.png").exists())
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
