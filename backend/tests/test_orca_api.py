"""API tests for Orca session start/read/send/close auth and ownership."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db
from backend import orca_adapter as oa

SYNTH_KEY = "cbm_agent_synth_test_key_01"


def _ok(result: dict[str, Any]) -> str:
    return json.dumps({"id": "test", "ok": True, "result": result})


def _write_key(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SYNTH_KEY + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: list[subprocess.CompletedProcess[str]] = []

    def queue(self, stdout: str, *, returncode: int = 0) -> None:
        self.responses.append(
            subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
        )

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if not self.responses:
            raise AssertionError(f"unexpected: {argv}")
        return self.responses.pop(0)


@pytest.fixture()
def client_access(tmp_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())

    key = _write_key(tmp_path / "orca-agent-key")
    runner = FakeRunner()
    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector="path:/repo",
        base_url_hint="http://127.0.0.1:18115",
        agent_key_file=str(key),
        probe_runtime=False,
    )
    monkeypatch.setattr(main, "orca_adapter", adapter)
    with TestClient(main.app) as client:
        yield client, adapter, runner


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def _create_agent(client: TestClient, *, grants: list[dict[str, str]], name: str) -> dict:
    created = client.post(
        "/api/access/agents",
        headers=bootstrap_headers(),
        json={
            "display_name": name,
            "paperclip_agent_id": f"paperclip-{name.lower().replace(' ', '-')}",
            "grants": grants,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _seed_profile(*, sandbox_id: str = "alpha") -> dict:
    return db.create_profile(name="Orca Profile", sandbox_id=sandbox_id, fingerprint_seed=42)


def test_capabilities_require_auth_and_deny_pause_resume(client_access):
    client, _adapter, _runner = client_access
    assert client.get("/api/orca/capabilities").status_code == 401
    caps = client.get("/api/orca/capabilities", headers=bootstrap_headers())
    assert caps.status_code == 200
    body = caps.json()
    assert body["available"] is True
    assert body["actions"]["pause"] is False
    assert body["actions"]["resume"] is False
    assert body["actions"]["start"] is True
    assert "codex" in body["agents"]
    assert any("owner-only" in note for note in body["notes"])
    assert any("vcvm_orca_preflight" in note for note in body["notes"])
    assert any("not mounted into the Manager container" in note for note in body["notes"])
    assert SYNTH_KEY not in caps.text


def test_close_is_owner_only_even_for_bootstrap_admin(client_access):
    """Admins must not get a cross-owner close bypass (least privilege)."""
    client, _adapter, runner = client_access
    profile = _seed_profile()
    agent_a = _create_agent(
        client,
        name="owner-close-a",
        grants=[
            {"sandbox_id": "alpha", "permission": "automate"},
            {"sandbox_id": "alpha", "permission": "interact"},
            {"sandbox_id": "alpha", "permission": "view"},
        ],
    )
    runner.queue(_ok({"terminal": {"handle": "term_api-owned-admin-close"}}))
    runner.queue(_ok({"ok": True}))
    started = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
        json={"profile_id": profile["id"], "agent": "codex"},
    )
    assert started.status_code == 201
    session_id = started.json()["id"]

    # Bootstrap admin is not the session owner — close must 404.
    assert (
        client.post(
            f"/api/orca/sessions/{session_id}/close",
            headers=bootstrap_headers(),
        ).status_code
        == 404
    )

    runner.queue(_ok({"ok": True}))
    closed = client.post(
        f"/api/orca/sessions/{session_id}/close",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_start_requires_automate_and_interact(client_access):
    client, adapter, runner = client_access
    profile = _seed_profile()

    view_only = _create_agent(
        client,
        name="view-only",
        grants=[{"sandbox_id": "alpha", "permission": "view"}],
    )
    denied = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {view_only['api_key']}"},
        json={"profile_id": profile["id"], "agent": "codex"},
    )
    assert denied.status_code == 404

    automate_only = _create_agent(
        client,
        name="automate-only",
        grants=[{"sandbox_id": "alpha", "permission": "automate"}],
    )
    # automate without interact still denied for composer start.
    denied_interact = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {automate_only['api_key']}"},
        json={"profile_id": profile["id"], "agent": "grok"},
    )
    assert denied_interact.status_code == 404

    runner.queue(_ok({"terminal": {"handle": "term_api-owned-1111"}}))
    runner.queue(_ok({"ok": True}))
    full = _create_agent(
        client,
        name="full-agent",
        grants=[
            {"sandbox_id": "alpha", "permission": "automate"},
            {"sandbox_id": "alpha", "permission": "interact"},
            {"sandbox_id": "alpha", "permission": "view"},
        ],
    )
    started = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {full['api_key']}"},
        json={
            "profile_id": profile["id"],
            "agent": "cursor-agent",
            "prompt": "Use the control skill for this profile",
        },
    )
    assert started.status_code == 201, started.text
    session = started.json()
    assert session["agent"] == "cursor-agent"
    assert session["terminal_handle"] == "term_api-owned-1111"
    assert session["capabilities"]["pause"] is False
    assert adapter.get_session(session["id"], owner_key=f"agent:{full['id']}").profile_id == profile["id"]


def test_ownership_blocks_cross_agent_read_send_close(client_access):
    client, _adapter, runner = client_access
    profile = _seed_profile()
    agent_a = _create_agent(
        client,
        name="owner-a",
        grants=[
            {"sandbox_id": "alpha", "permission": "automate"},
            {"sandbox_id": "alpha", "permission": "interact"},
            {"sandbox_id": "alpha", "permission": "view"},
        ],
    )
    agent_b = _create_agent(
        client,
        name="owner-b",
        grants=[
            {"sandbox_id": "alpha", "permission": "automate"},
            {"sandbox_id": "alpha", "permission": "interact"},
            {"sandbox_id": "alpha", "permission": "view"},
        ],
    )
    runner.queue(_ok({"terminal": {"handle": "term_api-owned-2222"}}))
    runner.queue(_ok({"ok": True}))
    started = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
        json={"profile_id": profile["id"], "agent": "codex"},
    )
    assert started.status_code == 201
    session_id = started.json()["id"]

    assert (
        client.get(
            f"/api/orca/sessions/{session_id}/output",
            headers={"Authorization": f"Bearer {agent_b['api_key']}"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/orca/sessions/{session_id}/send",
            headers={"Authorization": f"Bearer {agent_b['api_key']}"},
            json={"text": "hijack"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/orca/sessions/{session_id}/close",
            headers={"Authorization": f"Bearer {agent_b['api_key']}"},
        ).status_code
        == 404
    )

    runner.queue(
        _ok({"output": "Authorization: Bearer cbm_agent_SHOULD_NOT_LEAK", "nextCursor": 3})
    )
    output = client.get(
        f"/api/orca/sessions/{session_id}/output",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
    )
    assert output.status_code == 200
    assert "SHOULD_NOT_LEAK" not in output.json()["output"]
    assert "[redacted]" in output.json()["output"]

    runner.queue(_ok({"ok": True}))
    sent = client.post(
        f"/api/orca/sessions/{session_id}/send",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
        json={"text": "follow up"},
    )
    assert sent.status_code == 200
    assert sent.json()["ok"] is True

    runner.queue(_ok({"ok": True}))
    closed = client.post(
        f"/api/orca/sessions/{session_id}/close",
        headers={"Authorization": f"Bearer {agent_a['api_key']}"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_rejects_disallowed_agent_cli(client_access):
    client, _adapter, _runner = client_access
    profile = _seed_profile()
    agent = _create_agent(
        client,
        name="cli-guard",
        grants=[
            {"sandbox_id": "alpha", "permission": "automate"},
            {"sandbox_id": "alpha", "permission": "interact"},
        ],
    )
    resp = client.post(
        "/api/orca/sessions",
        headers={"Authorization": f"Bearer {agent['api_key']}"},
        json={"profile_id": profile["id"], "agent": "bash"},
    )
    assert resp.status_code == 422
