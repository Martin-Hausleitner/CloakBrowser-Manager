from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import pytest

from scripts.browser_tool_router import (
    BrowserToolRequest,
    RunScopedBrowserContext,
    route_browser_action,
    routing_contract_from_claim,
)


class FakeReadable:
    def __init__(self, chunks: list[bytes] | None = None, *, sleep: float = 0) -> None:
        self.chunks = list(chunks or [])
        self.sleep = sleep

    async def read(self, limit: int = -1) -> bytes:
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if limit >= 0 and len(chunk) > limit:
            self.chunks.insert(0, chunk[limit:])
            return chunk[:limit]
        return chunk


class FakeGatewayRunner:
    def __init__(self) -> None:
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True


class RecordingProcess:
    _next_pid = 41_000

    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        returncode: int = 0,
        sleep: float = 0,
    ) -> None:
        self.stdout = FakeReadable(stdout_chunks or [success_output()], sleep=sleep)
        self.stderr = FakeReadable(stderr_chunks or [])
        self.returncode = returncode
        self.killed = False
        self.waited = False
        RecordingProcess._next_pid += 1
        self.pid = RecordingProcess._next_pid

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class RecordingLauncher:
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        returncode: int = 0,
        sleep: float = 0,
        version_stdout_chunks: list[bytes] | None = None,
        help_stdout_chunks: list[bytes] | None = None,
        process_specs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.stdout_chunks = stdout_chunks
        self.stderr_chunks = stderr_chunks
        self.returncode = returncode
        self.sleep = sleep
        self.version_stdout_chunks = version_stdout_chunks
        self.help_stdout_chunks = help_stdout_chunks
        self.process_specs = list(process_specs or [])
        self.argv: tuple[str, ...] = ()
        self.argvs: list[tuple[str, ...]] = []
        self.env: dict[str, str] = {}
        self.processes: list[RecordingProcess] = []

    async def __call__(self, *argv: str, **kwargs: Any) -> RecordingProcess:
        self.argv = tuple(argv)
        self.argvs.append(tuple(argv))
        self.env = dict(kwargs["env"])
        if self.process_specs:
            spec = dict(self.process_specs.pop(0))
        elif tuple(argv[1:]) == ("eval", "version", "--json"):
            spec = {"stdout_chunks": list(self.version_stdout_chunks or [version_output()])}
        elif tuple(argv[1:]) == ("breath", "go", "--help"):
            spec = {"stdout_chunks": list(self.help_stdout_chunks or [help_output()])}
        else:
            spec = {
                "stdout_chunks": list(self.stdout_chunks or [success_output()]),
                "stderr_chunks": list(self.stderr_chunks or []),
                "returncode": self.returncode,
                "sleep": self.sleep,
            }
        process = RecordingProcess(
            stdout_chunks=spec.get("stdout_chunks"),
            stderr_chunks=spec.get("stderr_chunks"),
            returncode=int(spec.get("returncode", 0)),
            sleep=float(spec.get("sleep", 0)),
        )
        self.processes.append(process)
        return process


def version_output(**overrides: Any) -> bytes:
    payload = {
        "ok": True,
        "subcommand": "eval version",
        "op_kind": "eval:version",
        "version": "11.2.0-preview.3",
        "buildSha": "0c552cf6f8b0",
        **overrides,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def help_output(**overrides: Any) -> bytes:
    payload = {
        "help": True,
        "subcommand": "breath go",
        "op_kind": "breath:navigate",
        "flags": [
            {"name": "--timeout", "value_expected": True},
            {"name": "--json", "value_expected": False},
            {"name": "--ws", "value_expected": True},
        ],
        **overrides,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def success_output(**overrides: Any) -> bytes:
    payload = {
        "ok": True,
        "subcommand": "act go",
        "op_kind": "go",
        "session_id": "018f6ef4-6439-7788-9e8a-7a5691a6a67b",
        "target_id": "target-1",
        "context_id": "",
        "chrome_ws_url": "ws://127.0.0.1:32000/nonce",
        "url": "https://example.com/start?token=secret",
        "audit": {"internal": "do-not-expose"},
        **overrides,
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def run_context(tmp_path: Path, **overrides: Any) -> RunScopedBrowserContext:
    capability = tmp_path / "capability"
    capability.write_text("cbm_run_private_capability", encoding="utf-8")
    os.chmod(capability, 0o600)
    values = {
        "manager_url": "https://manager.local",
        "profile_id": "profile-1",
        "task_run_id": "run-1",
        "allowed_origins": ("https://example.com",),
        "capability_file": capability,
        "capability_token": "cbm_run_private_capability",
        "lease_id": "lease-1",
        **overrides,
    }
    return RunScopedBrowserContext(**values)


def request(tmp_path: Path, action: str, arguments: dict[str, Any]) -> BrowserToolRequest:
    return BrowserToolRequest(
        tool_id="unbrowse",
        attempt_index=1,
        action=action,
        arguments=arguments,
        context=run_context(tmp_path),
    )


def gateway_factory(record: dict[str, Any], runner: FakeGatewayRunner):
    async def start_gateway(**kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        record.update(kwargs)
        return runner, "ws://127.0.0.1:32000/nonce"

    return start_gateway


def test_navigate_invokes_breath_go_with_nonce_ws_and_no_daemon_or_proxy(
    tmp_path: Path,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    gateway_record: dict[str, Any] = {}
    runner = FakeGatewayRunner()
    launcher = RecordingLauncher()
    launcher.stdout_chunks = [success_output(url="https://example.com/start?q=visible")]
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory(gateway_record, runner),
        process_launcher=launcher,
        timeout_seconds=12.5,
    )

    result = asyncio.run(
        adapter(
                request(
                    tmp_path,
                    "navigate",
                    {"url": "https://example.com/start?q=visible"},
                )
            )
        )

    assert result.outcome == "succeeded"
    assert result.classification == "ok"
    assert result.payload == {
        "url": "https://example.com/start?q=visible",
        "attached": True,
    }
    assert launcher.argv == (
        "/bin/unbrowse",
        "breath",
        "go",
        "https://example.com/start?q=visible",
        "--ws",
        "ws://127.0.0.1:32000/nonce",
        "--timeout",
        "12500",
        "--json",
    )
    assert "serve" not in launcher.argv
    assert ("--" + "proxy") not in launcher.argv
    assert ("--" + "auth") not in launcher.argv
    assert ("--" + "browser") not in launcher.argv
    assert "--ws" in launcher.argv
    assert launcher.argv[launcher.argv.index("--ws") + 1] == "ws://127.0.0.1:32000/nonce"
    assert launcher.argvs[0] == ("/bin/unbrowse", "eval", "version", "--json")
    assert launcher.argvs[1] == ("/bin/unbrowse", "breath", "go", "--help")
    assert gateway_record == {
        "upstream_http": "https://manager.local/api/profiles/profile-1/cdp",
        "headers": {"Authorization": "Bearer cbm_run_private_capability"},
    }
    assert runner.cleaned is True


@pytest.mark.parametrize("action", ["inspect", "click", "fill", "read_text", "raw_cdp"])
def test_only_navigate_is_supported_before_any_side_effect(
    tmp_path: Path,
    action: str,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
    )

    result = asyncio.run(adapter(request(tmp_path, action, {"url": "https://example.com"})))

    assert result.outcome == "failed"
    assert result.classification == "unsupported_action"
    assert called is False


def test_origin_denied_happens_before_binary_lookup_gateway_or_process(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    side_effects: list[str] = []

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        side_effects.append("gateway")
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: side_effects.append("which") or "/bin/unbrowse",
        gateway_starter=gateway,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://evil.example/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "origin_denied"
    assert side_effects == []


def test_missing_unbrowse_binary_returns_tool_unavailable_before_gateway(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: None,
        gateway_starter=gateway,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False


@pytest.mark.parametrize(
    "version_stdout",
    [
        version_output(version="11.2.0-preview.2"),
        version_output(version="3.8.0"),
        version_output(subcommand="eval status"),
        version_output(op_kind="eval:status"),
        version_output(buildSha="wrong"),
        b"not-json\n",
        b'{"ok":true}\n',
    ],
)
def test_version_preflight_mismatch_or_malformed_returns_tool_unavailable_without_gateway_or_action(
    tmp_path: Path,
    version_stdout: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(version_stdout_chunks=[version_stdout])
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False
    assert launcher.argvs == [("/bin/unbrowse", "eval", "version", "--json")]


@pytest.mark.parametrize(
    "help_stdout",
    [
        help_output(flags=[]),
        help_output(flags=[{"name": "--ws", "value_expected": False}]),
        help_output(flags=[{"name": "--ws", "value_expected": True}, {"name": "--ws", "value_expected": True}]),
        help_output(subcommand="act go"),
        help_output(op_kind="breath:go"),
        b"not-json\n",
        b'{"help":true}\n',
    ],
)
def test_help_preflight_mismatch_or_malformed_returns_tool_unavailable_without_gateway_or_action(
    tmp_path: Path,
    help_stdout: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(help_stdout_chunks=[help_stdout])
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False
    assert launcher.argvs == [
        ("/bin/unbrowse", "eval", "version", "--json"),
        ("/bin/unbrowse", "breath", "go", "--help"),
    ]


def test_help_preflight_accepts_valid_schema_with_usage_exit_code_64(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(
        process_specs=[
            {"stdout_chunks": [version_output()]},
            {"stdout_chunks": [help_output()], "returncode": 64},
            {"stdout_chunks": [success_output(url="https://example.com/start")]},
        ]
    )
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "succeeded"
    assert result.classification == "ok"
    assert launcher.argvs == [
        ("/bin/unbrowse", "eval", "version", "--json"),
        ("/bin/unbrowse", "breath", "go", "--help"),
        (
            "/bin/unbrowse",
            "breath",
            "go",
            "https://example.com/start",
            "--ws",
            "ws://127.0.0.1:32000/nonce",
            "--timeout",
            "30000",
            "--json",
        ),
    ]
    assert runner.cleaned is True


@pytest.mark.parametrize("help_returncode", [1, 2, 65])
def test_help_preflight_rejects_valid_schema_with_unapproved_nonzero_exit(
    tmp_path: Path,
    help_returncode: int,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(
        process_specs=[
            {"stdout_chunks": [version_output()]},
            {"stdout_chunks": [help_output()], "returncode": help_returncode},
        ]
    )
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False
    assert launcher.argvs == [
        ("/bin/unbrowse", "eval", "version", "--json"),
        ("/bin/unbrowse", "breath", "go", "--help"),
    ]


def test_help_preflight_rejects_malformed_schema_even_with_usage_exit_code_64(
    tmp_path: Path,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(
        process_specs=[
            {"stdout_chunks": [version_output()]},
            {"stdout_chunks": [b'{"help":true}\n'], "returncode": 64},
        ]
    )
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False
    assert launcher.argvs == [
        ("/bin/unbrowse", "eval", "version", "--json"),
        ("/bin/unbrowse", "breath", "go", "--help"),
    ]


def test_preflight_fixtures_match_actual_vcvm_shapes_without_invented_command_matrix():
    version = json.loads(version_output())
    help_payload = json.loads(help_output())
    invented_matrix_key = "com" + "mands"

    assert version["subcommand"] == "eval version"
    assert version["op_kind"] == "eval:version"
    assert version["version"] == "11.2.0-preview.3"
    assert version["buildSha"] == "0c552cf6f8b0"
    assert invented_matrix_key not in version
    assert help_payload["subcommand"] == "breath go"
    assert help_payload["op_kind"] == "breath:navigate"
    assert help_payload["flags"].count({"name": "--ws", "value_expected": True}) == 1


def test_successful_preflight_is_cached_per_adapter_instance(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    launcher = RecordingLauncher()
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    first = asyncio.run(adapter(request(tmp_path, "navigate", {"url": "https://example.com/one"})))
    second = asyncio.run(adapter(request(tmp_path, "navigate", {"url": "https://example.com/two"})))

    assert first.outcome == "succeeded"
    assert second.outcome == "succeeded"
    assert [argv[1:] for argv in launcher.argvs].count(("eval", "version", "--json")) == 1
    assert [argv[1:] for argv in launcher.argvs].count(("breath", "go", "--help")) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://user@example.com/start",
        "https://example.com/start#fragment",
        "https://example.com/start?token=abc",
        "https://example.com/start?code=abc",
        "https://example.com/start?state=abc",
        "https://example.com/start?q=access_token",
        "https://example.com/start?next=session%3Dabc",
    ],
)
def test_sensitive_urls_return_secret_boundary_before_argv_gateway_or_raw_message(
    tmp_path: Path,
    url: str,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher()
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "navigate", {"url": url})))

    assert result.outcome == "failed"
    assert result.classification == "secret_boundary_violation"
    assert "example.com" not in result.message
    assert "abc" not in result.message
    assert launcher.argvs == []
    assert called is False


def test_router_stops_sensitive_url_without_invoking_fallback_adapters(
    tmp_path: Path,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    calls: list[tuple[str, dict[str, Any]]] = []
    launcher = RecordingLauncher()
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    async def stagehand(request: BrowserToolRequest):
        calls.append((request.tool_id, dict(request.arguments)))
        raise AssertionError("stagehand must not receive sensitive URL")

    async def browser_harness(request: BrowserToolRequest):
        calls.append((request.tool_id, dict(request.arguments)))
        raise AssertionError("browser-harness must not receive sensitive URL")

    contract = routing_contract_from_claim(
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse", "enabled": True},
                {"id": "stagehand", "enabled": True},
                {"id": "browser-harness", "enabled": True},
            ],
            "routing_policy": {
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 3,
            },
        }
    )
    result = asyncio.run(
        route_browser_action(
            contract=contract,
            context=run_context(tmp_path),
            action="navigate",
            arguments={"url": "https://example.com/start?token=abc"},
            adapters={
                "unbrowse": adapter,
                "stagehand": stagehand,
                "browser-harness": browser_harness,
            },
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "secret_boundary_violation"
    assert calls == []
    assert launcher.argvs == []
    public = json.dumps(result.public_json())
    assert "token=abc" not in public
    assert "https://example.com/start" not in public


def test_minimal_environment_strips_generic_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    monkeypatch.setenv("OPENAI_" + "API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_" + "API_KEY", "anthropic-secret")
    monkeypatch.setenv("GITHUB_" + "TOKEN", "github-secret")
    monkeypatch.setenv("AWS_SECRET_" + "ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("CBM_RUN_CAPABILITY_FILE", "/tmp/capability")
    monkeypatch.setenv("UNBROWSE_" + "API_KEY", "unbrowse-secret")
    monkeypatch.setenv("UNBROWSE_CONFIG", "/safe/non-secret/config")
    launcher = RecordingLauncher()
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    asyncio.run(adapter(request(tmp_path, "navigate", {"url": "https://example.com"})))

    assert launcher.env.get("UNBROWSE_CONFIG") == "/safe/non-secret/config"
    for forbidden in (
        "OPENAI_" + "API_KEY",
        "ANTHROPIC_" + "API_KEY",
        "GITHUB_" + "TOKEN",
        "AWS_SECRET_" + "ACCESS_KEY",
        "CBM_RUN_CAPABILITY_FILE",
        "UNBROWSE_" + "API_KEY",
    ):
        assert forbidden not in launcher.env
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR", "UNBROWSE_CONFIG"}
    assert set(launcher.env) <= allowed


def test_timeout_kills_process_and_always_cleans_gateway(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(sleep=60)
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
        timeout_seconds=0.01,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "transient_timeout"
    action_process = launcher.processes[2]
    assert action_process.waited is True
    assert runner.cleaned is True


def test_timeout_prefers_process_group_kill_then_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    killed_groups: list[tuple[int, int]] = []

    def fake_killpg(pid: int, sig: int) -> None:
        killed_groups.append((pid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg)
    launcher = RecordingLauncher(sleep=60)
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
        timeout_seconds=0.01,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
    )

    action_process = launcher.processes[2]
    assert result.classification == "transient_timeout"
    assert killed_groups == [(action_process.pid, signal.SIGKILL)]
    assert action_process.killed is False
    assert action_process.waited is True


def test_process_group_kill_falls_back_to_process_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    def fail_killpg(_pid: int, _sig: int) -> None:
        raise OSError("no process group")

    monkeypatch.setattr(os, "killpg", fail_killpg)
    launcher = RecordingLauncher(sleep=60)
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
        timeout_seconds=0.01,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
    )

    action_process = launcher.processes[2]
    assert result.classification == "transient_timeout"
    assert action_process.killed is True
    assert action_process.waited is True


def test_cancellation_kills_process_cleans_gateway_and_propagates(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(sleep=60)
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    async def run_and_cancel() -> None:
        task = asyncio.create_task(
            adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
        )
        for _ in range(20):
            await asyncio.sleep(0)
            if len(launcher.processes) > 2:
                break
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_and_cancel())

    action_process = launcher.processes[2]
    assert action_process.waited is True
    assert runner.cleaned is True


def test_streaming_stdout_overflow_kills_and_cleans_up(tmp_path: Path):
    from scripts.unbrowse_router_adapter import MAX_OUTPUT_BYTES, UnbrowseRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(stdout_chunks=[b"x" * (MAX_OUTPUT_BYTES + 1), b"never"])
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert "exceeds size bound" in result.message
    action_process = launcher.processes[2]
    assert action_process.waited is True
    assert runner.cleaned is True


def test_sanitizes_output_keys_values_and_does_not_expose_ws_or_secrets(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    launcher = RecordingLauncher(
        stdout_chunks=[
            success_output(
                **{
                    "Bearer cbm_run_private_capability": "https://u:p@example.com?token=secret",
                    "cookies": "session=secret",
                    "proxy": "http://u:p@proxy.local",
                }
            )
        ]
    )
    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(
            request(
                tmp_path,
                "navigate",
                {"url": "https://example.com/start?token=secret"},
            )
        )
    )

    serialized = json.dumps(result.payload)
    assert "cbm_run_private_capability" not in serialized
    assert "ws://127.0.0.1" not in serialized
    assert "chrome_ws_url" not in serialized
    assert "session_id" not in serialized
    assert "target_id" not in serialized
    assert "context_id" not in serialized
    assert "audit" not in serialized
    assert "nonce" not in serialized
    assert "u:p" not in serialized
    assert "secret" not in serialized
    assert "cookies" not in serialized.lower()
    assert "proxy" not in serialized.lower()


@pytest.mark.parametrize(
    "stdout",
    [
        success_output(url=""),
        success_output(url="https://evil.example"),
        success_output(chrome_ws_url="ws://127.0.0.1:1/wrong"),
        success_output(opened_second_browser=True),
    ],
)
def test_absent_bad_final_url_or_second_browser_signal_fails_closed(
    tmp_path: Path,
    stdout: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout_chunks=[stdout]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification in {
        "policy_denied",
        "origin_denied",
        "second_browser_attempt",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"chrome_ws_url": ""},
        {"chrome_ws_url": "ws://127.0.0.1:1/wrong"},
        {"ws": "ws://127.0.0.1:32000/nonce", "chrome_ws_url": ""},
        {"nested": {"chrome_ws_url": "ws://127.0.0.1:32000/nonce"}, "chrome_ws_url": ""},
        {"session_id": ""},
        {"session_id": "x" * 129},
        {"target_id": ""},
        {"target_id": "x" * 257},
        {"subcommand": "breath go"},
        {"subcommand": "act click"},
        {"op_kind": "navigate"},
        {"op_kind": "click"},
        {"ok": "true"},
    ],
)
def test_success_schema_requires_exact_top_level_attachment_contract(
    tmp_path: Path,
    override: dict[str, Any],
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout_chunks=[success_output(**override)]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification in {"policy_denied", "origin_denied", "second_browser_attempt"}


def test_valid_json_followed_by_trailing_garbage_fails_closed(tmp_path: Path):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(
            stdout_chunks=[success_output() + b"not-json\n"]
        ),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"


@pytest.mark.parametrize(
    "stdout",
    [
        b"not-json\n",
        b'{"ok":true,"subcommand":"act go"}\n',
        b'{"ok":true,"subcommand":"act go","op_kind":"go","session_id":"s","target_id":"t","chrome_ws_url":"ws://127.0.0.1:32000/nonce","url":"https://evil.example"}\n',
    ],
)
def test_post_preflight_malformed_or_bad_schema_is_terminal_policy_denied(
    tmp_path: Path,
    stdout: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout_chunks=[stdout]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification in {"policy_denied", "origin_denied"}


def test_nonzero_after_preflight_with_unrecognized_structured_error_is_policy_denied(
    tmp_path: Path,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(
            returncode=1,
            stdout_chunks=[b'{"ok":false,"code":"SURPRISE","error":"nope"}\n'],
        ),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"


@pytest.mark.parametrize(
    ("stdout", "classification"),
    [
        (b'{"ok":false,"code":"AUTH_REQUIRED","error":"login"}\n', "auth_required"),
        (
            b'{"ok":false,"code":"SECOND_BROWSER_ATTEMPT","error":"spawn denied"}\n',
            "second_browser_attempt",
        ),
    ],
)
def test_explicit_unbrowse_error_codes_map_to_terminal_classifications(
    tmp_path: Path,
    stdout: bytes,
    classification: str,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout_chunks=[stdout]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == classification


@pytest.mark.parametrize(
    "stderr",
    [
        b"AUTH_REQUIRED",
        b"authentication " + b"required",
        b"un" + b"authorized",
        b"second " + b"browser",
        b"Target." + b"create" + b"Target",
    ],
)
def test_stderr_substrings_never_map_to_terminal_classifications(
    tmp_path: Path,
    stderr: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(returncode=1, stderr_chunks=[stderr]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"


@pytest.mark.parametrize(
    "stdout",
    [
        b'{"ok":false,"code":"auth_required","error":"case mismatch"}\n',
        b'{"ok":false,"name":"Unauthorized","error":"not approved exact code"}\n',
        b'{"ok":false,"error":"AUTH_REQUIRED free text is not enough"}\n',
        b'{"ok":false,"code":"AUTH_REQUIRED","error":"login"}\nnot-json\n',
    ],
)
def test_auth_required_requires_final_valid_structured_approved_code(
    tmp_path: Path,
    stdout: bytes,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout_chunks=[stdout]),
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/start"}))
    )

    assert result.outcome == "failed"
    assert result.classification != "auth_required"


def test_default_ports_and_case_are_normalized_for_requested_and_final_origin(
    tmp_path: Path,
):
    from scripts.unbrowse_router_adapter import UnbrowseRouterAdapter

    adapter = UnbrowseRouterAdapter(
        executable_resolver=lambda _name: "/bin/unbrowse",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(
            stdout_chunks=[
                success_output(url="https://EXAMPLE.com:443/start"),
            ],
        ),
    )

    result = asyncio.run(
        adapter(
            request(
                tmp_path,
                "navigate",
                {"url": "https://example.com/start"},
            )
        )
    )

    assert result.outcome == "succeeded"
    assert result.payload == {"url": "https://example.com:443/start", "attached": True}
