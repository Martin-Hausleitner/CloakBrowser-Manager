from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from scripts.cbm_mcp import CbmMcpController, RunContext
from scripts.cbm_browser_ctl import BrowserCtlError


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def inner_text(self):
        return self.page.body_text if self.selector == "body" else f"text:{self.selector}"


class FakePage:
    def __init__(self):
        self.url = "https://example.com/start"
        self.body_text = "Visible page text"
        self.actions = []

    def title(self):
        return "Example"

    def bring_to_front(self):
        self.actions.append(("front",))

    def goto(self, url, wait_until):
        self.url = url
        self.actions.append(("goto", url, wait_until))

    def click(self, selector):
        self.actions.append(("click", selector))
        if selector == "a.cross-origin":
            self.url = "https://evil.example/account"

    def fill(self, selector, text):
        self.actions.append(("fill", selector, text))

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [FakeContext(page)]
        self.closed = False

    def close(self):
        self.closed = True


def make_run_context(tmp_path: Path, **overrides) -> RunContext:
    capability = tmp_path / "capability"
    capability.write_text("cbm_run_private_capability", encoding="utf-8")
    os.chmod(capability, 0o600)
    env = {
        "CBM_MANAGER_URL": "https://manager.local",
        "CBM_RUN_CAPABILITY_FILE": str(capability),
        "CBM_PROFILE_ID": "profile-1",
        "CBM_TASK_RUN_ID": "run-1",
        "CBM_ALLOWED_ORIGINS": json.dumps(["https://example.com"]),
        **overrides,
    }
    return RunContext.from_environment(env)


def make_routing_context(tmp_path: Path) -> RunContext:
    routing_contract = {
        "provider": {"id": "grok", "transport": "acp"},
        "browser_tools": [
            {"id": "unbrowse", "enabled": True},
            {"id": "stagehand", "enabled": True},
            {"id": "browser-harness", "enabled": True},
        ],
        "routing_policy": {
            "mode": "ordered-fallback",
            "allow_second_browser": False,
            "max_tool_attempts": 2,
        },
    }
    return make_run_context(
        tmp_path,
        CBM_ROUTING_CONTRACT_JSON=json.dumps(routing_contract),
    )


def make_routing_context_with_marker(tmp_path: Path, marker: Path) -> RunContext:
    return make_run_context(
        tmp_path,
        CBM_ROUTING_CONTRACT_JSON=json.dumps({
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse", "enabled": True},
                {"id": "stagehand", "enabled": True},
                {"id": "browser-harness", "enabled": True},
            ],
            "routing_policy": {
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 2,
            },
        }),
        CBM_ROUTER_TERMINAL_FAILURE_FILE=str(marker),
    )


def controller(tmp_path: Path):
    page = FakePage()
    browser = FakeBrowser(page)
    calls = []

    def connect(endpoint, *, headers):
        calls.append((endpoint, headers))
        return browser

    ctl = CbmMcpController(make_run_context(tmp_path), connect_over_cdp=connect)
    return ctl, page, browser, calls


def registered_tools_for(controller_obj, monkeypatch):
    from scripts import cbm_mcp

    registered = []

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorate(fn):
                registered.append(fn.__name__)
                return fn

            return decorate

    monkeypatch.setattr(cbm_mcp, "_import_fastmcp", lambda: FakeFastMCP)
    cbm_mcp.build_server(controller_obj)
    return registered


def mcp_entrypoint_test_python() -> str:
    configured = os.environ.get("CBM_MCP_TEST_PYTHON") or sys.executable
    interpreter = Path(configured)
    assert interpreter.is_absolute()
    assert interpreter.is_file()
    assert os.access(interpreter, os.X_OK)
    return str(interpreter)


def test_mcp_entrypoint_starts_from_outside_the_release_worktree(tmp_path: Path):
    capability = tmp_path / "capability"
    capability.write_text("cbm_run_test_only", encoding="utf-8")
    os.chmod(capability, 0o600)
    root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "CBM_MANAGER_URL": "http://127.0.0.1:18115",
        "CBM_RUN_CAPABILITY_FILE": str(capability),
        "CBM_PROFILE_ID": "profile-1",
        "CBM_TASK_RUN_ID": "run-1",
        "CBM_ALLOWED_ORIGINS": '["https://example.com"]',
        "CBM_MCP_PYTHON": mcp_entrypoint_test_python(),
    }

    result = subprocess.run(
        [str(root / "scripts" / "cbm-mcp")],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


@pytest.mark.parametrize(
    ("interpreter", "message"),
    [
        ("python3", "absolute"),
        ("/nonexistent/cbm-mcp-python", "existing executable file"),
        ("/tmp/cbm mcp python", "must not contain whitespace"),
    ],
)
def test_mcp_entrypoint_rejects_invalid_configured_python(
    tmp_path: Path,
    interpreter: str,
    message: str,
):
    root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "CBM_MCP_PYTHON": interpreter,
    }

    result = subprocess.run(
        [str(root / "scripts" / "cbm-mcp")],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_run_context_requires_private_capability_and_exact_profile(tmp_path: Path):
    context = make_run_context(tmp_path)
    assert context.profile_id == "profile-1"
    assert context.capability_token == "cbm_run_private_capability"
    assert "cbm_run_private_capability" not in repr(context)

    capability = Path(context.capability_file)
    os.chmod(capability, 0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        RunContext.from_environment(
            {
                "CBM_MANAGER_URL": "https://manager.local",
                "CBM_RUN_CAPABILITY_FILE": str(capability),
                "CBM_PROFILE_ID": "profile-1",
                "CBM_TASK_RUN_ID": "run-1",
                "CBM_ALLOWED_ORIGINS": "[]",
            }
        )


def test_inspect_uses_run_capability_without_exposing_it(tmp_path: Path):
    ctl, _page, browser, calls = controller(tmp_path)

    result = ctl.inspect()

    assert result == {
        "ok": True,
        "command": "inspect",
        "profile_id": "profile-1",
        "url": "https://example.com/start",
        "title": "Example",
    }
    assert calls[0][0] == "https://manager.local/api/profiles/profile-1/cdp"
    assert calls[0][1] == {"Authorization": "Bearer cbm_run_private_capability"}
    assert "cbm_run_private_capability" not in json.dumps(result)
    assert browser.closed is True


def test_navigation_is_exact_origin_scoped(tmp_path: Path):
    ctl, page, _browser, _calls = controller(tmp_path)
    result = ctl.navigate("https://example.com/account")
    assert result["url"] == "https://example.com/account"
    assert page.actions[-1] == (
        "goto",
        "https://example.com/account",
        "domcontentloaded",
    )

    with pytest.raises(BrowserCtlError, match="allowed origin"):
        ctl.navigate("https://evil.example/account")


def test_every_tool_blocks_disallowed_current_or_redirected_origin(tmp_path: Path):
    ctl, page, _browser, _calls = controller(tmp_path)
    page.url = "https://evil.example/account"
    with pytest.raises(BrowserCtlError, match="allowed origin"):
        ctl.inspect()
    with pytest.raises(BrowserCtlError, match="allowed origin"):
        ctl.read_text()
    with pytest.raises(BrowserCtlError, match="allowed origin"):
        ctl.fill("input", "value")

    page.url = "https://example.com/start"
    with pytest.raises(BrowserCtlError, match="allowed origin"):
        ctl.click("a.cross-origin")


def test_click_fill_and_read_text_are_bounded_browser_tools(tmp_path: Path):
    ctl, page, _browser, _calls = controller(tmp_path)

    assert ctl.click("button[type=submit]")["selector"] == "button[type=submit]"
    fill = ctl.fill("input[name=email]", "person@example.com")
    assert fill["text_length"] == len("person@example.com")
    assert "person@example.com" not in json.dumps(fill)
    assert ctl.read_text()["text"] == "Visible page text"
    assert ctl.read_text("h1")["text"] == "text:h1"
    assert ("click", "button[type=submit]") in page.actions


def test_server_factory_registers_only_bounded_tools(tmp_path: Path, monkeypatch):
    ctl = controller(tmp_path)[0]
    registered = registered_tools_for(ctl, monkeypatch)
    assert registered == [
        "browser_inspect",
        "browser_navigate",
        "browser_click",
        "browser_fill",
        "browser_read_text",
        "control_plane_capabilities",
        "control_plane_resource_schema",
        "orca_web_capabilities",
    ]
    assert ctl.control_plane_capabilities()["mcp_contract"]["tools"] == registered


def test_routed_server_registers_only_router_and_safe_discovery_tools(
    tmp_path: Path,
    monkeypatch,
):
    ctl = CbmMcpController(make_routing_context(tmp_path))

    registered = registered_tools_for(ctl, monkeypatch)

    assert registered == [
        "cbm_route_browser_action",
        "control_plane_capabilities",
        "control_plane_resource_schema",
        "orca_web_capabilities",
    ]
    capabilities = ctl.control_plane_capabilities()
    assert capabilities["mcp_contract"]["tools"] == registered
    serialized = json.dumps(capabilities)
    assert "browser_inspect" not in serialized
    assert "browser_navigate" not in serialized
    assert capabilities["resources"]["profiles"]["available"]["mcp"] is False


def test_direct_browser_methods_fail_closed_without_cdp_connect_on_routed_context(
    tmp_path: Path,
):
    calls = []

    def connect(*_args, **_kwargs):
        calls.append("connect")
        raise AssertionError("routed context must not connect through direct tools")

    ctl = CbmMcpController(
        make_routing_context(tmp_path),
        connect_over_cdp=connect,
    )

    for invoke in (
        ctl.inspect,
        lambda: ctl.navigate("https://example.com/account"),
        lambda: ctl.click("button"),
        lambda: ctl.fill("input", "value"),
        ctl.read_text,
    ):
        with pytest.raises(BrowserCtlError, match="router"):
            invoke()
    assert calls == []


def test_fastmcp_browser_tools_offload_sync_playwright_from_event_loop(monkeypatch):
    from scripts import cbm_mcp

    registered = {}

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorate(fn):
                registered[fn.__name__] = fn
                return fn

            return decorate

    worker_threads = []

    class ThreadRecordingController:
        def navigate(self, url):
            worker_threads.append(threading.get_ident())
            return {"ok": True, "url": url}

    monkeypatch.setattr(cbm_mcp, "_import_fastmcp", lambda: FakeFastMCP)
    cbm_mcp.build_server(ThreadRecordingController())
    navigate = registered["browser_navigate"]
    event_loop_thread = threading.get_ident()

    assert inspect.iscoroutinefunction(navigate)
    assert asyncio.run(navigate("https://example.com")) == {
        "ok": True,
        "url": "https://example.com",
    }
    assert worker_threads and worker_threads[0] != event_loop_thread


def test_mcp_resource_tools_return_bounded_envelopes_without_secret_paths(tmp_path: Path):
    ctl = CbmMcpController(make_run_context(tmp_path))

    capabilities = ctl.control_plane_capabilities()
    assert capabilities["api_version"] == "cloakbrowser.io/v1"
    assert capabilities["kind"] == "CapabilitySet"
    assert capabilities["resources"]["profiles"]["available"]["rest"] is True
    assert capabilities["resources"]["profiles"]["available"]["mcp"] is False
    assert capabilities["resources"]["secret-references"]["available"]["mcp"] is True
    assert capabilities["resources"]["secret-references"]["reveal"] is False
    assert capabilities["resources"]["secret-references"]["secrets"] == "reference-only"
    assert capabilities["resources"]["orca-web"]["available"]["mcp"] is False
    assert capabilities["mcp_contract"]["manager_resource_tools"] is False
    assert "fallback" not in json.dumps(capabilities).lower()

    schema = ctl.control_plane_resource_schema()
    assert schema["api_version"] == "cloakbrowser.io/v1"
    assert schema["kind"] == "ContractSchema"
    assert "raw_cdp" not in json.dumps(schema).lower()
    assert "credential_reveal" not in json.dumps(schema).lower()

    orca = ctl.orca_web_capabilities()
    assert orca["resources"]["orca-web"]["available"] == {
        "rest": False,
        "cli": False,
        "mcp": False,
        "skill": False,
    }
    assert orca["resources"]["orca-web"]["reason_code"] == "capability_unavailable"


def _private_marker(tmp_path: Path) -> Path:
    marker = tmp_path / "router-terminal-failure.json"
    marker.write_text("", encoding="utf-8")
    os.chmod(marker, 0o600)
    return marker


def test_router_facade_writes_private_terminal_marker_for_stop_classification(
    tmp_path: Path,
):
    marker = _private_marker(tmp_path)

    async def unbrowse(_request):
        from scripts.browser_tool_router import BrowserToolResult

        return BrowserToolResult(
            outcome="failed",
            classification="policy_denied",
            message="Bearer cbm_run_private_capability 401 Unauthorized /tmp/secret",
        )

    ctl = CbmMcpController(
        make_routing_context_with_marker(tmp_path, marker),
        router_adapters={"unbrowse": unbrowse},
    )

    result = asyncio.run(ctl.route_browser_action("inspect", {}))

    assert result["ok"] is False
    assert result["classification"] == "policy_denied"
    marker_body = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_body == {
        "classification": "policy_denied",
        "tool_id": "unbrowse",
    }
    serialized = json.dumps(marker_body)
    assert "cbm_run_private_capability" not in serialized
    assert "Unauthorized" not in serialized
    assert "/tmp/secret" not in serialized


def test_router_facade_does_not_mark_success_or_failover_classification(
    tmp_path: Path,
):
    marker = _private_marker(tmp_path)

    async def unbrowse(_request):
        from scripts.browser_tool_router import BrowserToolResult

        return BrowserToolResult(
            outcome="failed",
            classification="route_miss",
            message="Bearer cbm_run_private_capability route miss",
        )

    async def stagehand(_request):
        from scripts.browser_tool_router import BrowserToolResult

        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload={"title": "Example"},
        )

    ctl = CbmMcpController(
        make_routing_context_with_marker(tmp_path, marker),
        router_adapters={"unbrowse": unbrowse, "stagehand": stagehand},
    )

    result = asyncio.run(ctl.route_browser_action("inspect", {}))

    assert result["ok"] is True
    assert marker.read_text(encoding="utf-8") == ""

    failover_only = CbmMcpController(
        make_routing_context_with_marker(tmp_path, marker),
        router_adapters={"unbrowse": unbrowse},
    )

    result = asyncio.run(failover_only.route_browser_action("inspect", {}))

    assert result["ok"] is False
    assert result["classification"] == "tool_unavailable"
    assert marker.read_text(encoding="utf-8") == ""


def test_router_facade_routes_with_run_context_and_redacted_attempt_telemetry(
    tmp_path: Path,
):
    seen = []

    async def unbrowse(request):
        seen.append((request.tool_id, request.context))
        from scripts.browser_tool_router import BrowserToolResult

        return BrowserToolResult(
            outcome="failed",
            classification="route_miss",
            message="Bearer cbm_run_private_capability route miss",
        )

    async def stagehand(request):
        seen.append((request.tool_id, request.context))
        from scripts.browser_tool_router import BrowserToolResult

        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload={"title": "Example"},
        )

    ctl = CbmMcpController(
        make_routing_context(tmp_path),
        router_adapters={"unbrowse": unbrowse, "stagehand": stagehand},
    )

    result = asyncio.run(ctl.route_browser_action("inspect", {}))

    assert [item[0] for item in seen] == ["unbrowse", "stagehand"]
    assert seen[0][1] == seen[1][1]
    assert result["ok"] is True
    assert result["tool_id"] == "stagehand"
    assert result["result"] == {"title": "Example"}
    assert [set(item) for item in result["telemetry"]] == [
        {"tool_id", "action_class", "duration_ms", "result_class", "fallback_reason"},
        {"tool_id", "action_class", "duration_ms", "result_class", "fallback_reason"},
    ]
    assert result["telemetry"][0]["fallback_reason"] == "route_miss"
    assert result["telemetry"][1]["fallback_reason"] is None
    serialized = json.dumps(result)
    assert "cbm_run_private_capability" not in serialized
    assert "Bearer" not in serialized
    assert "manager_url" not in serialized
    assert "allowed_origins" not in serialized
    assert "capability_file" not in serialized
    assert "lease-1" not in serialized


def test_router_facade_is_explicitly_unavailable_without_normalized_contract(
    tmp_path: Path,
):
    ctl = CbmMcpController(make_run_context(tmp_path))

    result = asyncio.run(ctl.route_browser_action("inspect", {}))
    assert result["ok"] is False
    assert result["classification"] == "tool_unavailable"
    assert "telemetry" in result


def test_default_router_mapping_uses_real_unbrowse_stagehand_and_browser_harness_adapters():
    from scripts import cbm_mcp
    from scripts.browser_harness_adapter import BrowserHarnessAdapter
    from scripts.stagehand_router_adapter import StagehandRouterAdapter
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapters = cbm_mcp._default_router_adapters()

    assert isinstance(adapters["unbrowse"], UnbrowseRouterAdapter)
    assert isinstance(adapters["stagehand"], StagehandRouterAdapter)
    assert isinstance(adapters["browser-harness"], BrowserHarnessAdapter)
