#!/usr/bin/env python3
"""TDD self-checks for soniox_stt_adapter.py.

Deterministic injected-transport coverage: success, 401, 402, timeout,
malformed data, redaction, origin denial, and no-network default.
No external network, no raw keys on argv/report.
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "soniox_stt_adapter.py"
SPEC = importlib.util.spec_from_file_location("soniox_stt_adapter", RUNNER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load {RUNNER}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


FAKE_KEY = "sk_test_soniox_deadbeefcafebabe0123456789abcdef"


def _write_key_file(path: Path, content: str = FAKE_KEY, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


class FakeTransport:
    """Injected transport returning a canned result; records probe args."""

    def __init__(self, result: mod.TransportResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def probe_session(
        self,
        *,
        url: str,
        model: str,
        api_key: str,
        timeout_s: float,
    ) -> mod.TransportResult:
        self.calls.append(
            {
                "url": url,
                "model": model,
                "api_key": api_key,
                "timeout_s": timeout_s,
            }
        )
        return self.result


class SonioxSttAdapterTest(unittest.TestCase):
    def test_module_default_has_no_live_network_imports_for_contract(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        # Optional live path may import urllib; contract path must not force requests/websockets.
        for needle in ("import requests", "import websocket", "from websockets", "import soniox"):
            self.assertNotIn(needle, source, f"forbidden dependency: {needle}")

    def test_no_network_default_contract_ready_with_valid_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=False,
            )
            self.assertEqual(report.state, mod.STATE_READY)
            self.assertTrue(report.ready)
            self.assertFalse(report.network_used)
            payload = report.to_public_dict()
            text = json.dumps(payload)
            self.assertNotIn(FAKE_KEY, text)
            self.assertEqual(payload["mode"], "contract")
            self.assertEqual(payload["model"], mod.DEFAULT_MODEL)

    def test_no_network_default_never_calls_injected_transport(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(status_code=200, finished=True, latency_ms=1)
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=False,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_READY)
            self.assertEqual(transport.calls, [])

    def test_missing_credentials_auth_required(self) -> None:
        report = mod.probe(live=False)
        self.assertEqual(report.state, mod.STATE_AUTH_REQUIRED)
        self.assertFalse(report.ready)
        self.assertFalse(report.network_used)

    def test_valid_secret_ref_contract_ready(self) -> None:
        report = mod.probe(secret_ref="secretref-soniox-stt-prod", live=False)
        self.assertEqual(report.state, mod.STATE_READY)
        self.assertTrue(report.ready)
        payload = report.to_public_dict()
        self.assertEqual(payload["credential_source"], "secret_ref")
        # Opaque source label only; never emit raw key fields or the ref as a secret value.
        self.assertNotIn("api_key", payload)
        self.assertNotIn("authorization", payload)
        self.assertNotIn("secret_ref", payload)

    def test_invalid_secret_ref_policy_denied(self) -> None:
        report = mod.probe(secret_ref="not-a-valid-ref", live=False)
        self.assertEqual(report.state, mod.STATE_POLICY_DENIED)
        self.assertFalse(report.ready)

    def test_key_file_mode_not_0600_policy_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "open.key", mode=0o644)
            report = mod.probe(key_file=str(key_path), live=False)
            self.assertEqual(report.state, mod.STATE_POLICY_DENIED)
            self.assertFalse(report.ready)
            text = json.dumps(report.to_public_dict())
            self.assertNotIn(FAKE_KEY, text)

    def test_key_file_empty_auth_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "empty.key", content="  \n")
            report = mod.probe(key_file=str(key_path), live=False)
            self.assertEqual(report.state, mod.STATE_AUTH_REQUIRED)

    def test_key_file_missing_auth_required(self) -> None:
        missing = Path(tempfile.gettempdir()) / "does-not-exist-soniox-key-xyz.key"
        report = mod.probe(key_file=str(missing), live=False)
        self.assertEqual(report.state, mod.STATE_AUTH_REQUIRED)

    def test_origin_denial_policy_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                ws_url="wss://evil.example.com/transcribe",
                live=False,
            )
            self.assertEqual(report.state, mod.STATE_POLICY_DENIED)
            self.assertFalse(report.ready)

    def test_http_scheme_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                ws_url="ws://stt-rt.soniox.com/transcribe-websocket",
                live=False,
            )
            self.assertEqual(report.state, mod.STATE_POLICY_DENIED)

    def test_disallowed_model_policy_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                model="stt-rt-v3-legacy",
                live=False,
            )
            self.assertEqual(report.state, mod.STATE_POLICY_DENIED)

    def test_injected_transport_success_ready(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(
                status_code=200,
                finished=True,
                latency_ms=42,
                request_id="req-success-1",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_READY)
            self.assertTrue(report.ready)
            self.assertTrue(report.network_used)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0]["model"], mod.DEFAULT_MODEL)
            self.assertEqual(transport.calls[0]["api_key"], FAKE_KEY)
            payload = report.to_public_dict()
            self.assertEqual(payload["metrics"]["latency_ms"], 42)
            self.assertEqual(payload["metrics"]["request_id"], "req-success-1")
            self.assertNotIn(FAKE_KEY, json.dumps(payload))

    def test_injected_transport_401_auth_required(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(
                status_code=401,
                error_type="unauthenticated",
                request_id="req-401",
                latency_ms=10,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_AUTH_REQUIRED)
            self.assertFalse(report.ready)

    def test_injected_transport_402_budget_exhausted(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(
                status_code=402,
                error_type="organization_balance_exhausted",
                request_id="req-402",
                latency_ms=11,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_BUDGET_EXHAUSTED)
            self.assertFalse(report.ready)

    def test_injected_transport_timeout_transient_failure(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(timed_out=True, latency_ms=5000)
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_TRANSIENT_FAILURE)
            self.assertFalse(report.ready)

    def test_injected_transport_malformed_adapter_unavailable(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(malformed=True, latency_ms=5)
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_ADAPTER_UNAVAILABLE)
            self.assertFalse(report.ready)

    def test_injected_transport_503_transient_failure(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(
                status_code=503,
                error_type="service_unavailable",
                request_id="req-503",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            self.assertEqual(report.state, mod.STATE_TRANSIENT_FAILURE)

    def test_redaction_strips_key_like_material_from_text(self) -> None:
        sample = (
            f"Authorization: Bearer {FAKE_KEY} "
            f'api_key="{FAKE_KEY}" '
            "https://user:pass@stt-rt.soniox.com/path"
        )
        redacted = mod.redact_text(sample)
        self.assertNotIn(FAKE_KEY, redacted)
        self.assertNotIn("user:pass", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_public_report_never_contains_raw_key(self) -> None:
        transport = FakeTransport(
            mod.TransportResult(
                status_code=401,
                error_type="unauthenticated",
                error_message=f"Incorrect API key provided: {FAKE_KEY}",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=transport,
            )
            blob = json.dumps(report.to_public_dict())
            self.assertNotIn(FAKE_KEY, blob)
            self.assertNotIn("api_key", blob)

    def test_live_without_key_file_auth_required(self) -> None:
        # secret_ref alone cannot live-probe (no material to present).
        report = mod.probe(secret_ref="secretref-soniox-stt-prod", live=True)
        self.assertEqual(report.state, mod.STATE_AUTH_REQUIRED)
        self.assertFalse(report.network_used)

    def test_live_without_transport_and_no_network_adapter_unavailable(self) -> None:
        """Live mode without injectable transport refuses silent real network by default
        unless LiveTransport is explicitly constructed via CLI --live path.

        Library call with live=True and transport=None uses built-in live transport
        factory only when allow_builtin_live=True (CLI path).
        """
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            report = mod.probe(
                key_file=str(key_path),
                live=True,
                transport=None,
                allow_builtin_live=False,
            )
            self.assertEqual(report.state, mod.STATE_ADAPTER_UNAVAILABLE)
            self.assertFalse(report.network_used)

    def test_map_error_types_budget_variants(self) -> None:
        for error_type in (
            "organization_balance_exhausted",
            "organization_monthly_budget_exhausted",
            "project_monthly_budget_exhausted",
        ):
            state = mod.map_transport_result(
                mod.TransportResult(status_code=402, error_type=error_type)
            )
            self.assertEqual(state, mod.STATE_BUDGET_EXHAUSTED, error_type)

    def test_cli_contract_default_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            out_path = Path(tmp) / "report.json"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(
                    [
                        "--key-file",
                        str(key_path),
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(code, 0)
            stdout = buf.getvalue()
            self.assertNotIn(FAKE_KEY, stdout)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["state"], "ready")
            self.assertEqual(report["mode"], "contract")
            self.assertFalse(report["network_used"])
            self.assertNotIn(FAKE_KEY, out_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(out_path.stat().st_mode), 0o600)

    def test_cli_rejects_raw_api_key_flag(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main(["--api-key", FAKE_KEY])
        self.assertEqual(ctx.exception.code, 2)
        combined = buf.getvalue() + err.getvalue()
        self.assertNotIn(FAKE_KEY, combined)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["state"], "policy_denied")
        self.assertEqual(payload["detail"], "raw_key_on_argv_denied")

    def test_cli_rejects_positional_raw_secret(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main([FAKE_KEY])
        self.assertEqual(ctx.exception.code, 2)
        combined = buf.getvalue() + err.getvalue()
        self.assertNotIn(FAKE_KEY, combined)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["detail"], "raw_key_on_argv_denied")

    def test_cli_rejects_unknown_option_secret_value(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main([f"--not-a-real-flag={FAKE_KEY}"])
        self.assertEqual(ctx.exception.code, 2)
        combined = buf.getvalue() + err.getvalue()
        self.assertNotIn(FAKE_KEY, combined)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["detail"], "raw_key_on_argv_denied")

    def test_cli_rejects_unknown_option_space_separated_secret(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main(["--mystery-flag", FAKE_KEY])
        self.assertEqual(ctx.exception.code, 2)
        combined = buf.getvalue() + err.getvalue()
        self.assertNotIn(FAKE_KEY, combined)

    def test_argv_scanner_allows_secret_ref_and_paths(self) -> None:
        key_path = str(Path(tempfile.gettempdir()) / "keys" / "soniox.key")
        self.assertFalse(
            mod.argv_contains_raw_secret(
                ["--secret-ref", "secretref-soniox-stt-prod", "--key-file", key_path]
            )
        )
        self.assertTrue(mod.argv_contains_raw_secret([FAKE_KEY]))
        self.assertTrue(mod.argv_contains_raw_secret([f"--token={FAKE_KEY}"]))

    def test_validate_timeout_rejects_zero_negative_inf_nan_too_large(self) -> None:
        cases = (
            (0, "timeout_not_positive"),
            (0.0, "timeout_not_positive"),
            (-1, "timeout_not_positive"),
            (-0.01, "timeout_not_positive"),
            (float("inf"), "timeout_not_finite"),
            (float("-inf"), "timeout_not_finite"),
            (float("nan"), "timeout_not_finite"),
            (mod.MAX_TIMEOUT_S + 0.001, "timeout_too_large"),
            (1_000_000, "timeout_too_large"),
            ("not-a-number", "timeout_invalid"),
        )
        for value, expected in cases:
            ok, reason = mod.validate_timeout_s(value)
            self.assertFalse(ok, msg=f"value={value!r}")
            self.assertEqual(reason, expected, msg=f"value={value!r}")

        ok, reason = mod.validate_timeout_s(mod.DEFAULT_TIMEOUT_S)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        ok, reason = mod.validate_timeout_s(mod.MAX_TIMEOUT_S)
        self.assertTrue(ok)

    def test_probe_rejects_invalid_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            for timeout in (0, -5, float("inf"), float("nan"), mod.MAX_TIMEOUT_S + 1):
                report = mod.probe(key_file=str(key_path), live=False, timeout_s=timeout)
                self.assertEqual(report.state, mod.STATE_POLICY_DENIED)
                self.assertFalse(report.ready)
                self.assertTrue(str(report.detail).startswith("timeout_"))

    def test_cli_rejects_invalid_timeouts_without_echoing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = _write_key_file(Path(tmp) / "soniox.key")
            for raw in ("0", "-1", "inf", "nan", "100000"):
                buf = io.StringIO()
                err = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(err):
                    code = mod.main(
                        ["--key-file", str(key_path), "--timeout", raw]
                    )
                self.assertNotEqual(code, 0, msg=raw)
                combined = buf.getvalue() + err.getvalue()
                self.assertNotIn(FAKE_KEY, combined)
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["state"], "policy_denied")
                self.assertTrue(str(payload["detail"]).startswith("timeout_"), msg=raw)

    def test_cli_live_requires_key_file(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = mod.main(["--live", "--secret-ref", "secretref-soniox-stt-prod"])
        self.assertNotEqual(code, 0)
        self.assertNotIn(FAKE_KEY, buf.getvalue())

    def test_allowed_hosts_are_official_only(self) -> None:
        self.assertIn("stt-rt.soniox.com", mod.ALLOWED_WS_HOSTS)
        self.assertIn("api.soniox.com", mod.ALLOWED_API_HOSTS)
        self.assertEqual(mod.DEFAULT_MODEL, "stt-rt-v5")
        self.assertTrue(mod.DEFAULT_WS_URL.startswith("wss://"))
        self.assertTrue(mod.DEFAULT_API_BASE.startswith("https://"))

    def test_validate_ws_url_accepts_official(self) -> None:
        ok, reason = mod.validate_ws_url(mod.DEFAULT_WS_URL)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_validate_ws_url_rejects_non_soniox(self) -> None:
        ok, reason = mod.validate_ws_url("wss://stt-rt.soniox.evil.com/transcribe-websocket")
        self.assertFalse(ok)
        self.assertEqual(reason, "origin_denied")


if __name__ == "__main__":
    unittest.main()
