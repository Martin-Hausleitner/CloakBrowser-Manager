from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("secure_recording_compiler.py")
SPEC = importlib.util.spec_from_file_location("secure_recording_compiler", MODULE_PATH)
assert SPEC and SPEC.loader
compiler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compiler
SPEC.loader.exec_module(compiler)


def recording() -> dict:
    return {
        "schema": "cloakbrowser.secure-action-recording.v1",
        "id": "recording-demo",
        "startedAt": 1,
        "stoppedAt": 2,
        "stepCount": 4,
        "secretRefs": ["secretref-login-password"],
        "prompt": "ignored and rebuilt",
        "steps": [
            {
                "id": "step-001",
                "action": "navigate",
                "url": "https://example.test/login",
            },
            {
                "id": "step-002",
                "action": "click",
                "url": "https://example.test/login",
                "selector": "#sign-in",
            },
            {
                "id": "step-003",
                "action": "fill",
                "url": "https://example.test/login",
                "selector": "input[name=password]",
                "secretRef": "secretref-login-password",
                "valueLength": 20,
            },
            {
                "id": "step-004",
                "action": "fill",
                "url": "https://example.test/profile",
                "selector": "input[name=nickname]",
                "valueLength": 6,
            },
        ],
    }


def test_compiles_deterministic_browser_use_contract_without_recorded_values():
    first = compiler.compile_recording(recording())
    second = compiler.compile_recording(recording())

    assert second == first
    assert first["schema"] == "cloakbrowser.browser-use-replay.v1"
    assert first["allowedOrigins"] == ["https://example.test"]
    assert first["secretRefs"] == ["secretref-login-password"]
    assert first["steps"][3]["requiresHumanInput"] is True
    assert "ignored and rebuilt" not in first["prompt"]
    assert "secretref-login-password" in first["prompt"]
    assert len(first["sha256"]) == 64


@pytest.mark.parametrize("key", ["value", "password", "token", "cookie", "otp"])
def test_rejects_raw_secret_fields_without_echo(key: str):
    flow = recording()
    flow["steps"][2][key] = "do-not-echo-this-secret"
    with pytest.raises(compiler.RecordingCompileError) as excinfo:
        compiler.compile_recording(flow)
    assert "do-not-echo-this-secret" not in str(excinfo.value)


def test_rejects_secret_like_text_and_unsupported_origins():
    secret_flow = recording()
    secret_flow["steps"][1]["selector"] = "Authorization: Bearer raw-secret-value"
    with pytest.raises(compiler.RecordingCompileError):
        compiler.compile_recording(secret_flow)

    invalid_origin = copy.deepcopy(recording())
    invalid_origin["steps"][0]["url"] = "file:///etc/passwd"
    with pytest.raises(compiler.RecordingCompileError):
        compiler.compile_recording(invalid_origin)


def test_cli_output_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "recording.json"
    source.write_text(json.dumps(recording()), encoding="utf-8")
    assert compiler.main([str(source)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "cloakbrowser.browser-use-replay.v1"

