"""Smoke tests for scripts/cbm_agent_ctl.py (no live Manager required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("cbm_agent_ctl.py")


def test_cli_help_lists_control_plane_commands():
    import runpy
    import sys
    from io import StringIO

    buf = StringIO()
    old = sys.stdout
    sys.argv = ["cbm_agent_ctl.py", "--help"]
    try:
        sys.stdout = buf
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exc.value.code == 0
    finally:
        sys.stdout = old
        sys.argv = ["pytest"]
    text = buf.getvalue()
    assert "profiles" in text
    assert "open-links" in text
    assert "open-session" in text
    assert "tasks" in text
    assert "runs" in text


def test_auth_header_reads_key_file_when_env_key_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    key_file = tmp_path / "orca-agent-key"
    key_file.write_text("cbm_agent_from_file_not_real\n", encoding="utf-8")
    monkeypatch.delenv("CBM_AGENT_KEY", raising=False)
    monkeypatch.delenv("CBM_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CBM_AGENT_KEY_FILE", str(key_file))

    headers = mod._auth_header()
    assert headers["Authorization"] == "Bearer cbm_agent_from_file_not_real"


def test_tasks_run_builds_browser_use_request(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"id": "run-1", "status": "queued"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "tasks",
            "run",
            "session-1",
            "--profile-id",
            "profile-1",
            "--task",
            "Read the page title",
            "--allowed-origin",
            "https://example.com",
        ]
    )
    args.func(args)
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/task-sessions/session-1/runs"
    assert captured["body"]["harness"] == "browser-use"
    assert captured["body"]["profile_id"] == "profile-1"
    assert captured["body"]["allowed_origins"] == ["https://example.com"]


def test_runs_cancel_posts_cancel(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None):
        captured["method"] = method
        captured["path"] = path
        return {"id": "run-1", "status": "cancelled"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(["runs", "cancel", "run-1"])
    args.func(args)
    assert captured == {"method": "POST", "path": "/api/task-runs/run-1/cancel"}


def test_cli_profiles_create_builds_expected_request(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["query"] = query
        return {"id": "p1", "name": body["name"]}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)

    args = mod.build_parser().parse_args(
        [
            "profiles",
            "create",
            "--name",
            "demo",
            "--sandbox",
            "agents",
            "--harness",
            "codex",
            "--geoip",
        ]
    )
    args.func(args)
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/profiles"
    assert captured["body"]["sandbox_id"] == "agents"
    assert captured["body"]["geoip"] is True


def test_cli_open_links_field_extraction(monkeypatch: pytest.MonkeyPatch, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")

    def fake_request(method, path, *, body=None, query=None):
        assert method == "GET"
        assert path.endswith("/open-links")
        assert query == {"prefer": "local", "mode": "vnc"}
        return {
            "vnc_fullscreen_url": "http://127.0.0.1:18117/?profile=p1&view=vnc&fullscreen=1",
            "cdp_fullscreen_url": "http://127.0.0.1:18117/session/p1/live",
        }

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        ["profiles", "open-links", "p1", "--mode", "vnc", "--field", "vnc_fullscreen_url"]
    )
    args.func(args)
    assert capsys.readouterr().out.strip().endswith("fullscreen=1")
