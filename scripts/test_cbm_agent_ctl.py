"""Smoke tests for scripts/cbm_agent_ctl.py (no live Manager required)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
    assert "project-state" in text
    assert "worktree-audit" in text
    assert "tasks" in text
    assert "runs" in text
    assert "accounts" in text
    assert "extensions" in text


def test_extensions_list_filters_catalog_locally(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls: list[tuple[str, str]] = []
    printed: list[object] = []

    def fake_request(method, path, **_options):
        calls.append((method, path))
        return {
            "extensions": [
                {"id": "privacy-id", "name": "Privacy Helper", "tags": ["privacy"]},
                {"id": "media-id", "name": "Media Helper", "tags": ["media"]},
            ]
        }

    monkeypatch.setattr(mod, "_request", fake_request)
    monkeypatch.setattr(mod, "_print", lambda value, _json_mode: printed.append(value))
    args = mod.build_parser().parse_args(["extensions", "list", "--query", "privacy"])
    args.func(args)

    assert calls == [("GET", "/api/extension/defaults")]
    assert printed == [[{"id": "privacy-id", "name": "Privacy Helper", "tags": ["privacy"]}]]


def test_extensions_search_accepts_a_positional_query():
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    args = mod.build_parser().parse_args(["extensions", "search", "privacy helper"])

    assert args.query == "privacy helper"
    assert args.func is mod.cmd_extensions_list


def test_extensions_set_defaults_sends_trusted_ids(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    captured: dict = {}

    def fake_request(method, path, *, body=None, **options):
        if method == "GET":
            return {"extensions": [{"id": "one"}, {"id": "two"}]}
        captured.update({"method": method, "path": path, "body": body, "options": options})
        return {"selected_ids": body["selected_ids"]}

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "--idempotency-key",
            "extension-defaults-1",
            "extensions",
            "set-defaults",
            "--extension-id",
            "one",
            "--extension-id",
            "two",
        ]
    )
    args.func(args)

    assert captured == {
        "method": "PUT",
        "path": "/api/extension/defaults",
        "body": {"selected_ids": ["one", "two"]},
        "options": {"idempotency_key": "extension-defaults-1"},
    }


def test_extensions_enable_and_disable_update_only_catalog_ids(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls: list[dict] = []

    def fake_request(method, path, *, body=None, **options):
        calls.append({"method": method, "path": path, "body": body, "options": options})
        if method == "GET" and path == "/api/extension/defaults":
            return {"extensions": [{"id": "one"}, {"id": "two"}]}
        if method == "GET" and path.startswith("/api/profiles/"):
            return {"id": "profile-1", "extension_ids": ["one"]}
        return {"id": "profile-1", "extension_ids": body["extension_ids"]}

    monkeypatch.setattr(mod, "_request", fake_request)
    enable = mod.build_parser().parse_args(
        ["extensions", "enable", "profile-1", "two"]
    )
    enable.func(enable)
    disable = mod.build_parser().parse_args(
        ["extensions", "disable", "profile-1", "one"]
    )
    disable.func(disable)

    assert calls[2] == {
        "method": "PUT",
        "path": "/api/profiles/profile-1",
        "body": {"extension_ids": ["one", "two"]},
        "options": {},
    }
    assert calls[5] == {
        "method": "PUT",
        "path": "/api/profiles/profile-1",
        "body": {"extension_ids": []},
        "options": {},
    }


def test_extensions_enable_rejects_unknown_catalog_id(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(
        mod,
        "_request",
        lambda method, path, **_options: {"extensions": [{"id": "known"}]},
    )
    args = mod.build_parser().parse_args(
        ["extensions", "enable", "profile-1", "unknown"]
    )
    with pytest.raises(SystemExit, match="Unknown trusted catalog extension"):
        args.func(args)


def test_accounts_list_builds_filtered_request(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None, **_options):
        captured.update({"method": method, "path": path, "body": body, "query": query})
        return []

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "accounts",
            "list",
            "--profile-id",
            "profile-1",
            "--provider",
            "github",
            "--auth-state",
            "needs_2fa",
        ]
    )
    args.func(args)

    assert captured == {
        "method": "GET",
        "path": "/api/accounts",
        "body": None,
        "query": {
            "profile_id": "profile-1",
            "provider": "github",
            "auth_state": "needs_2fa",
        },
    }


def test_accounts_create_and_update_send_metadata_references_only(
    monkeypatch: pytest.MonkeyPatch,
):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls: list[dict] = []

    def fake_request(method, path, *, body=None, query=None, **options):
        calls.append({"method": method, "path": path, "body": body, "options": options})
        return {"id": "account-1"}

    monkeypatch.setattr(mod, "_request", fake_request)
    create = mod.build_parser().parse_args(
        [
            "--idempotency-key",
            "account-create-1",
            "accounts",
            "create",
            "--profile-id",
            "profile-1",
            "--provider",
            "github",
            "--subject-label",
            "agent@example.invalid",
            "--origin",
            "https://github.com",
            "--secret-ref",
            "secretref-login-1",
            "--totp-ref",
            "secretref-totp-1",
        ]
    )
    create.func(create)
    update = mod.build_parser().parse_args(
        [
            "--if-version",
            "2",
            "accounts",
            "update",
            "account-1",
            "--auth-state",
            "signed_in",
        ]
    )
    update.func(update)

    assert calls[0] == {
        "method": "POST",
        "path": "/api/accounts",
        "body": {
            "profile_id": "profile-1",
            "provider": "github",
            "subject_label": "agent@example.invalid",
            "origin": "https://github.com",
            "secret_ref": "secretref-login-1",
            "totp_ref": "secretref-totp-1",
        },
        "options": {"idempotency_key": "account-create-1"},
    }
    assert calls[1] == {
        "method": "PUT",
        "path": "/api/accounts/account-1",
        "body": {"auth_state": "signed_in"},
        "options": {"if_version": 2},
    }
    assert all("password" not in json.dumps(call) for call in calls)


def test_accounts_history_event_delete_and_secret_flags_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, *, body=None, query=None, **_options):
        calls.append((method, path, body if body is not None else query))
        return [] if method == "GET" else {"ok": True}

    monkeypatch.setattr(mod, "_request", fake_request)
    for argv in (
        ["accounts", "history", "account-1", "--limit", "25"],
        ["accounts", "event", "account-1", "--type", "observed"],
        ["accounts", "delete", "account-1"],
    ):
        args = mod.build_parser().parse_args(argv)
        args.func(args)

    assert calls == [
        ("GET", "/api/accounts/account-1/events", {"limit": "25"}),
        ("POST", "/api/accounts/account-1/events", {"event_type": "observed"}),
        ("DELETE", "/api/accounts/account-1", None),
    ]
    alias_args = mod.build_parser().parse_args(
        ["accounts", "event", "account-1", "--event-type", "observed"]
    )
    assert alias_args.event_type == "observed"
    with pytest.raises(SystemExit):
        mod.build_parser().parse_args(
            [
                "accounts",
                "create",
                "--profile-id",
                "profile-1",
                "--provider",
                "github",
                "--subject-label",
                "agent@example.invalid",
                "--password",
                "forbidden",
            ]
        )


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


def test_tasks_run_builds_grok_acpx_request(monkeypatch: pytest.MonkeyPatch):
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
        return {"id": "run-grok", "status": "queued"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "tasks",
            "run",
            "session-grok",
            "--profile-id",
            "profile-1",
            "--task",
            "Read the page title",
            "--allowed-origin",
            "https://example.com",
            "--harness",
            "acpx",
            "--agent",
            "grok-build",
        ]
    )
    args.func(args)

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/task-sessions/session-grok/runs"
    assert captured["body"]["harness"] == "acpx"
    assert captured["body"]["agent"] == "grok-build"


def test_tasks_run_builds_unbrowse_request_without_agent(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"id": "run-unbrowse", "status": "queued"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "tasks",
            "run",
            "session-1",
            "--profile-id",
            "profile-1",
            "--harness",
            "unbrowse",
            "--task",
            "Open https://example.com",
            "--allowed-origin",
            "https://example.com",
        ]
    )
    args.func(args)

    assert captured["body"]["harness"] == "unbrowse"
    assert "agent" not in captured["body"]


def test_tasks_run_requires_an_explicit_acpx_agent(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(
        mod,
        "_request",
        lambda *_args, **_kwargs: pytest.fail("invalid ACPX request reached the API"),
    )
    args = mod.build_parser().parse_args(
        [
            "tasks",
            "run",
            "session-grok",
            "--profile-id",
            "profile-1",
            "--task",
            "Read the page title",
            "--allowed-origin",
            "https://example.com",
            "--harness",
            "acpx",
        ]
    )

    with pytest.raises(SystemExit, match="--agent is required with --harness acpx"):
        args.func(args)


def test_tasks_run_rejects_acpx_agent_for_browser_use(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(
        mod,
        "_request",
        lambda *_args, **_kwargs: pytest.fail("invalid Browser Use request reached the API"),
    )
    args = mod.build_parser().parse_args(
        [
            "tasks",
            "run",
            "session-browser-use",
            "--profile-id",
            "profile-1",
            "--task",
            "Read the page title",
            "--harness",
            "browser-use",
            "--agent",
            "grok-build",
        ]
    )

    with pytest.raises(SystemExit, match="--agent is only valid with --harness acpx"):
        args.func(args)


def test_mutating_commands_emit_idempotency_and_version_headers(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None, idempotency_key=None, if_version=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        captured["idempotency_key"] = idempotency_key
        captured["if_version"] = if_version
        return {"api_version": "cloakbrowser.io/v1", "kind": "Profile", "metadata": {"id": "p1"}}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "--idempotency-key",
            "idem-1",
            "--if-version",
            "7",
            "profiles",
            "update",
            "profile-1",
            "--name",
            "renamed",
        ]
    )
    args.func(args)
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/profiles/profile-1"
    assert captured["idempotency_key"] == "idem-1"
    assert captured["if_version"] == 7


def test_resource_envelope_redacts_sensitive_fields_and_carries_version_metadata():
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    payload = mod.resource_envelope(
        "Proxy",
        {
            "id": "proxy-1",
            "redacted": "http://[redacted]@proxy.example:8080",
            "proxy_url": "http://user:pass@proxy.example:8080",
            "updated_at": "2026-07-26T12:05:00Z",
            "row_version": 3,
        },
        request_id="req-1",
    )
    assert payload["api_version"] == "cloakbrowser.io/v1"
    assert payload["kind"] == "Proxy"
    assert payload["metadata"]["id"] == "proxy-1"
    assert payload["metadata"]["resource_version"] == 3
    assert payload["metadata"]["request_id"] == "req-1"
    assert "proxy_url" not in payload["spec"]
    assert "user:pass" not in json.dumps(payload)


def test_resource_envelope_recursively_strips_sensitive_fields():
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    payload = mod.resource_envelope(
        "Run",
        {
            "id": "run-1",
            "nested": {
                "safe": "ok",
                "endpoint": "http://user:pass@proxy.example:8080",
                "message": "Bearer cbm_agent_secret",
                "token": "cbm_run_secret",
                "items": [
                    {"url": "https://example.com", "password": "secret"},
                    {"proxy_url": "http://user:pass@proxy.example:8080"},
                ],
            },
            "status": {"state": "queued", "authorization": "Bearer secret"},
            "links": [{"rel": "self", "cdp_ws_url": "ws://secret"}],
        },
    )
    encoded = json.dumps(payload)
    assert "cbm_run_secret" not in encoded
    assert "user:pass" not in encoded
    assert "cbm_agent_secret" not in encoded
    assert "password" not in encoded
    assert "proxy_url" not in encoded
    assert "authorization" not in encoded
    assert "cdp_ws_url" not in encoded
    assert payload["spec"]["nested"]["safe"] == "ok"
    assert payload["status"] == {"state": "queued"}
    assert payload["links"] == [{"rel": "self"}]


def test_capabilities_report_unavailable_targets_without_fallback(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    captured: list[tuple[str, str]] = []

    def fake_request(method, path, *, body=None, query=None, idempotency_key=None, if_version=None):
        captured.append((method, path))
        if path == "/api/v2/capabilities":
            return {
                "api_version": "cloakbrowser.io/v1",
                "kind": "CapabilitySet",
                "resources": [
                    {"id": "local-mac", "kind": "Box", "available": False, "reason_code": "unavailable"},
                    {"id": "orca-web", "kind": "OrcaWeb", "available": False, "reason_code": "not_configured"},
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(["api", "capabilities"])
    args.func(args)
    assert captured == [("GET", "/api/v2/capabilities")]


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


def test_runs_retry_health_posts_retry(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None, **_options):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"id": "run-1", "status": "queued"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(["runs", "retry-health", "run-1"])
    args.func(args)

    assert captured == {
        "method": "POST",
        "path": "/api/task-runs/run-1/retry-health",
        "body": None,
    }


def test_runs_override_health_posts_audited_reason(monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_request(method, path, *, body=None, query=None, **_options):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"id": "run-1", "status": "queued"}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(
        [
            "runs",
            "override-health",
            "run-1",
            "--reason",
            "Harmless example.com acceptance proof",
        ]
    )
    args.func(args)

    assert captured == {
        "method": "POST",
        "path": "/api/task-runs/run-1/override-health",
        "body": {"reason": "Harmless example.com acceptance proof"},
    }


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


def test_cli_profiles_create_assigns_catalog_extension_ids(monkeypatch: pytest.MonkeyPatch):
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
        return {"id": "p1", "name": body["name"]}

    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setattr(mod, "_request", fake_request)
    args = SimpleNamespace(
        name="demo",
        sandbox="default",
        harness="codex",
        project="default",
        folder="",
        pinned=False,
        geoip=False,
        timezone=None,
        locale=None,
        proxy=None,
        platform=None,
        extension_ids=["catalog-a", "catalog-b"],
        json=False,
    )
    mod.cmd_profiles_create(args)

    assert captured["body"]["extension_ids"] == ["catalog-a", "catalog-b"]


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


def test_cli_project_state_prints_json_and_writes_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        mod,
        "_git_output",
        lambda args, cwd=None: {
            ("remote",): "fork\norigin",
            ("config", "--get", "remote.fork.url"): "https://token@github.com/acme/repo.git",
            ("branch", "--show-current"): "feature/project-state",
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("status", "--porcelain=v1", "--untracked-files=all"): (
                " M backend/main.py\n?? docs/new.md\nR  old.md -> new.md\n"
            ),
        }[tuple(args)],
    )

    args = mod.build_parser().parse_args(
        [
            "project-state",
            "--mode",
            "hot_reload",
            "--owner",
            "agent-codex",
            "--active-ticket",
            "CBM-001",
            "--completed-receipt",
            "tests: red",
            "--next-safe-step",
            "Implement project_state.py",
            "--forbidden-action",
            "Do not commit or push",
            "--stop-condition",
            "Receipt is written and tested",
        ]
    )

    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["repo"] == "https://[REDACTED]@github.com/acme/repo.git"
    assert payload["branch"] == "feature/project-state"
    assert payload["worktree"] == str(tmp_path)
    assert payload["completed_receipts"] == ["tests: red"]
    assert payload["unmerged_files"] == ["backend/main.py", "docs/new.md", "new.md"]
    written = json.loads((tmp_path / ".cbm/state/project-state-v1.json").read_text(encoding="utf-8"))
    assert written == payload


def test_cli_worktree_audit_prints_json_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cbm_agent_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    captured: dict = {}

    def fake_audit_repository(config):
        captured["root"] = config.root
        captured["target_ref"] = config.target_ref
        captured["retention_days"] = config.retention_days
        captured["warn_free_gib"] = config.warn_free_gib
        captured["block_worktree_free_gib"] = config.block_worktree_free_gib
        captured["block_release_free_gib"] = config.block_release_free_gib
        return {
            "schema": "cbm.worktree_audit.v1",
            "disk": {"status": "ok"},
            "worktrees": [],
        }

    monkeypatch.setattr(mod, "audit_repository", fake_audit_repository)
    args = mod.build_parser().parse_args(
        [
            "worktree-audit",
            "--root",
            str(tmp_path),
            "--target-ref",
            "origin/dev",
            "--retention-days",
            "14",
            "--warn-free-gib",
            "20",
            "--block-worktree-free-gib",
            "12",
            "--block-release-free-gib",
            "8",
        ]
    )
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "cbm.worktree_audit.v1"
    assert captured == {
        "root": tmp_path,
        "target_ref": "origin/dev",
        "retention_days": 14,
        "warn_free_gib": 20.0,
        "block_worktree_free_gib": 12.0,
        "block_release_free_gib": 8.0,
    }
