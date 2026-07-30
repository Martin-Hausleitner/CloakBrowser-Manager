from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.cbm_extension_ctl import ExtensionControlClient, validate_bridge_url


def test_bridge_url_is_loopback_http_only():
    assert validate_bridge_url("http://127.0.0.1:18766") == "http://127.0.0.1:18766"
    assert validate_bridge_url("http://localhost:18766/") == "http://localhost:18766"
    for unsafe in (
        "https://127.0.0.1:18766",
        "http://10.0.0.2:18766",
        "http://127.0.0.1:18766/path",
        "http://127.0.0.1:18766?token=raw",
        "http://user:pass@127.0.0.1:18766",
    ):
        with pytest.raises(ValueError):
            validate_bridge_url(unsafe)


def test_client_reads_private_token_file_and_never_puts_token_in_url(tmp_path: Path):
    token_path = tmp_path / "token"
    token_path.write_text("x" * 48, encoding="utf-8")
    os.chmod(token_path, 0o600)
    calls: list[dict] = []

    def transport(method, url, headers, body, timeout):
        calls.append(
            {"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        if method == "POST":
            return {"ok": True, "command": {"id": "cmd-1", "operation": body["operation"]}}
        return {"ok": True, "state": "completed", "result": {"active": False}}

    client = ExtensionControlClient(
        "http://127.0.0.1:18766", token_path=token_path, transport=transport
    )
    result = client.execute("status", timeout_seconds=1)

    assert result == {"active": False}
    assert all("x" * 48 not in call["url"] for call in calls)
    assert calls[0]["headers"]["Authorization"] == "Bearer " + "x" * 48
    assert "x" * 48 not in repr(client)


def test_client_compile_converts_only_reference_recording(tmp_path: Path):
    token_path = tmp_path / "token"
    token_path.write_text("x" * 48, encoding="utf-8")
    os.chmod(token_path, 0o600)
    flow = {
        "schema": "cloakbrowser.secure-action-recording.v1",
        "id": "recording-1",
        "steps": [
            {"action": "navigate", "url": "https://example.com/login"},
            {
                "action": "fill",
                "url": "https://example.com/login",
                "selector": "#password",
                "secretRef": "secretref-abc123",
                "valueLength": 12,
            },
        ],
    }

    def transport(method, _url, _headers, body, _timeout):
        if method == "POST":
            assert body == {"operation": "export"}
            return {"ok": True, "command": {"id": "cmd-1"}}
        return {"ok": True, "state": "completed", "result": flow}

    client = ExtensionControlClient(
        "http://127.0.0.1:18766", token_path=token_path, transport=transport
    )
    result = client.execute("compile", timeout_seconds=1)

    assert result["schema"] == "cloakbrowser.browser-use-replay.v1"
    assert result["secretRefs"] == ["secretref-abc123"]
    assert result["allowedOrigins"] == ["https://example.com"]
