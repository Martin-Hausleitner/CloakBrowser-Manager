from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.cbm_extension_bridge import (
    ALLOWED_EXTENSION_ORIGIN,
    CommandBroker,
    ControlTokenFile,
    ExtensionSessionStore,
    UnsafeRecorderResult,
)


def test_control_token_file_is_private_and_stable(tmp_path: Path):
    token_path = tmp_path / "control-token"

    first = ControlTokenFile(token_path).load_or_create()
    second = ControlTokenFile(token_path).load_or_create()

    assert first == second
    assert len(first) >= 43
    assert token_path.stat().st_mode & 0o077 == 0
    assert first not in repr(ControlTokenFile(token_path))


def test_control_token_rejects_symlink_or_public_mode(tmp_path: Path):
    real = tmp_path / "real"
    real.write_text("not-a-valid-token", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular private file"):
        ControlTokenFile(link).load_or_create()

    public = tmp_path / "public"
    public.write_text("a" * 48, encoding="utf-8")
    os.chmod(public, 0o644)
    with pytest.raises(ValueError, match="0600"):
        ControlTokenFile(public).load_or_create()


def test_extension_session_is_origin_bound_and_expires():
    now = [1000.0]
    sessions = ExtensionSessionStore(now=lambda: now[0], ttl_seconds=30)

    with pytest.raises(PermissionError, match="extension origin"):
        sessions.issue("https://example.com")

    token = sessions.issue(ALLOWED_EXTENSION_ORIGIN)
    assert sessions.verify(token)
    now[0] += 31
    assert not sessions.verify(token)


def test_broker_allowlist_ttl_and_single_result_delivery():
    now = [1000.0]
    broker = CommandBroker(now=lambda: now[0], ttl_seconds=10, max_pending=2)

    with pytest.raises(ValueError, match="unsupported"):
        broker.enqueue("navigate")

    command = broker.enqueue("status")
    assert broker.next_command(wait_seconds=0)["id"] == command["id"]
    assert broker.next_command(wait_seconds=0) is None

    broker.complete(command["id"], {"active": False, "eventCount": 0})
    assert broker.result(command["id"])["state"] == "completed"
    with pytest.raises(KeyError, match="not pending"):
        broker.complete(command["id"], {"active": False})

    expired = broker.enqueue("start")
    now[0] += 11
    assert broker.result(expired["id"])["state"] == "expired"


@pytest.mark.parametrize(
    "unsafe",
    [
        {"password": "raw"},
        {"nested": {"token": "raw"}},
        {"message": "Authorization: Bearer abcdefghijklmnop"},
        {"cookie": "session=raw"},
    ],
)
def test_broker_rejects_raw_secret_results(unsafe: dict):
    broker = CommandBroker()
    command = broker.enqueue("export")
    broker.next_command(wait_seconds=0)
    with pytest.raises(UnsafeRecorderResult):
        broker.complete(command["id"], unsafe)


def test_broker_accepts_reference_only_recording_export():
    broker = CommandBroker()
    command = broker.enqueue("export")
    broker.next_command(wait_seconds=0)
    safe = {
        "schema": "cloakbrowser.secure-action-recording.v1",
        "id": "recording-1",
        "steps": [
            {
                "action": "fill",
                "url": "https://example.com/login",
                "selector": "#password",
                "secretRef": "secretref-abc123",
                "valueLength": 12,
            }
        ],
    }

    broker.complete(command["id"], safe)

    assert broker.result(command["id"])["result"] == safe
