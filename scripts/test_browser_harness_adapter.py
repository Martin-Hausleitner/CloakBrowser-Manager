from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts.browser_tool_router import BrowserToolRequest, RunScopedBrowserContext


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


class FakeWritable:
    def __init__(self, launcher: "RecordingLauncher") -> None:
        self.launcher = launcher
        self.closed = False

    def write(self, data: bytes) -> None:
        self.launcher.stdin += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeGatewayRunner:
    def __init__(self) -> None:
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True


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
        tool_id="browser-harness",
        attempt_index=1,
        action=action,
        arguments=arguments,
        context=run_context(tmp_path),
    )


class RecordingProcess:
    def __init__(
        self,
        launcher: "RecordingLauncher",
        *,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
        returncode: int = 0,
        sleep: float = 0,
        cancel: bool = False,
        pid: int | None = 4321,
    ) -> None:
        self.launcher = launcher
        self.stdout = stdout if stdout is not None else b'{"ok":true,"url":"https://example.com","title":"Example"}\n'
        self.stderr = stderr if stderr is not None else b""
        self.returncode = returncode
        self.sleep = sleep
        self.cancel = cancel
        self.pid = pid
        self.killed = False
        self.waited = False
        self.stdin = FakeWritable(launcher)
        self.stdout = FakeReadable([self.stdout], sleep=sleep)
        self.stderr = FakeReadable([self.stderr])

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
        self.launcher.stdin = stdin or b""
        request_path = Path(self.launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"])
        self.launcher.request_mode = request_path.stat().st_mode & 0o777
        self.launcher.request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        if self.cancel:
            raise asyncio.CancelledError
        if self.sleep:
            await asyncio.sleep(self.sleep)
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class RecordingLauncher:
    def __init__(self, **process_kwargs: Any) -> None:
        self.process_kwargs = process_kwargs
        self.argv: tuple[str, ...] = ()
        self.env: dict[str, str] = {}
        self.processes: list[RecordingProcess] = []
        self.stdin: bytes = b""
        self.request_mode: int | None = None
        self.request_payload: dict[str, Any] | None = None

    async def __call__(self, *argv: str, **kwargs: Any) -> RecordingProcess:
        self.argv = tuple(argv)
        self.env = dict(kwargs["env"])
        if "CBM_BROWSER_HARNESS_REQUEST_FILE" in self.env:
            request_path = Path(self.env["CBM_BROWSER_HARNESS_REQUEST_FILE"])
            self.request_mode = request_path.stat().st_mode & 0o777
            self.request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        process = RecordingProcess(self, **self.process_kwargs)
        self.processes.append(process)
        return process


class StreamingProcess:
    def __init__(
        self,
        launcher: RecordingLauncher,
        *,
        stdout_chunks: list[bytes],
        stderr_chunks: list[bytes] | None = None,
        returncode: int = 0,
        pid: int | None = 9876,
    ) -> None:
        self.launcher = launcher
        self.stdin = FakeWritable(launcher)
        self.stdout = FakeReadable(stdout_chunks)
        self.stderr = FakeReadable(stderr_chunks or [])
        self.returncode = returncode
        self.pid = pid
        self.killed = False
        self.waited = False

    async def communicate(self, _stdin: bytes | None = None) -> tuple[bytes, bytes]:
        raise AssertionError("adapter must not use communicate()")

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class StreamingLauncher(RecordingLauncher):
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes],
        stderr_chunks: list[bytes] | None = None,
        returncode: int = 0,
        pid: int | None = 9876,
    ) -> None:
        super().__init__()
        self.stdout_chunks = stdout_chunks
        self.stderr_chunks = stderr_chunks or []
        self.returncode = returncode
        self.pid = pid

    async def __call__(self, *argv: str, **kwargs: Any) -> StreamingProcess:
        self.argv = tuple(argv)
        self.env = dict(kwargs["env"])
        request_path = Path(self.env["CBM_BROWSER_HARNESS_REQUEST_FILE"])
        self.request_mode = request_path.stat().st_mode & 0o777
        self.request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        process = StreamingProcess(
            self,
            stdout_chunks=list(self.stdout_chunks),
            stderr_chunks=list(self.stderr_chunks),
            returncode=self.returncode,
            pid=self.pid,
        )
        self.processes.append(process)
        return process


def gateway_factory(record: dict[str, Any], runner: FakeGatewayRunner):
    async def start_gateway(**kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        record.update(kwargs)
        return runner, "ws://127.0.0.1:32000/nonce"

    return start_gateway


def execute_static_program(
    tmp_path: Path,
    *,
    action: str,
    arguments: dict[str, Any],
    page_url: str,
    allowed_origins: tuple[str, ...] = ("https://example.com",),
) -> tuple[dict[str, Any], list[str]]:
    from scripts.browser_harness_adapter import STATIC_BROWSER_HARNESS_PROGRAM

    request_file = tmp_path / "request.json"
    request_file.write_text(
        json.dumps(
            {
                "action": action,
                "arguments": arguments,
                "allowed_origins": list(allowed_origins),
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def page_info() -> dict[str, Any]:
        calls.append("page_info")
        return {"ok": True, "url": page_url, "title": "Current"}

    def goto_url(url: str) -> dict[str, Any]:
        calls.append("goto_url")
        return {"ok": True, "navigation": url}

    def fill_input(selector: str, text: str) -> dict[str, Any]:
        calls.append(f"fill_input:{selector}:{text}")
        return {"ok": True, "filled": selector}

    def js(expression: str) -> Any:
        calls.append(f"js:{expression}")
        if "innerText" in expression:
            return "Visible"
        return True

    old_env = os.environ.get("CBM_BROWSER_HARNESS_REQUEST_FILE")
    stdout = io.StringIO()
    try:
        os.environ["CBM_BROWSER_HARNESS_REQUEST_FILE"] = str(request_file)
        with contextlib.redirect_stdout(stdout):
            exec(
                STATIC_BROWSER_HARNESS_PROGRAM,
                {
                    "__name__": "__main__",
                    "goto_url": goto_url,
                    "page_info": page_info,
                    "fill_input": fill_input,
                    "js": js,
                },
            )
    finally:
        if old_env is None:
            os.environ.pop("CBM_BROWSER_HARNESS_REQUEST_FILE", None)
        else:
            os.environ["CBM_BROWSER_HARNESS_REQUEST_FILE"] = old_env
    return json.loads(stdout.getvalue()), calls


def test_adapter_invokes_constant_harness_program_with_private_request_file(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    gateway_record: dict[str, Any] = {}
    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(
        stdout=b'noise\n{"ok":true,"command":"page_info","url":"https://example.com/start?token=secret","title":"Example"}\n'
    )
    adapter = BrowserHarnessAdapter(
        executable="browser-harness",
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory(gateway_record, runner),
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

    assert result.outcome == "succeeded"
    assert result.classification == "ok"
    assert result.payload["url"] == "https://example.com/start?token=REDACTED"
    assert launcher.argv == ("/bin/browser-harness",)
    assert launcher.env["BU_AUTOSPAWN"] == "0"
    assert launcher.env["BU_CDP_WS"] == "ws://127.0.0.1:32000/nonce"
    assert launcher.env["BU_CDP_URL"] == "http://127.0.0.1:32000/nonce"
    assert launcher.env["BU_NAME"].startswith("cbm-run-1-profile-1-")
    assert launcher.env["BH_RECORD"] == "0"
    assert launcher.request_mode == 0o600
    assert launcher.request_payload == {
        "action": "navigate",
        "arguments": {"url": "https://example.com/start?token=secret"},
        "allowed_origins": ["https://example.com"],
    }
    assert not Path(launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"]).exists()
    assert gateway_record == {
        "upstream_http": "https://manager.local/api/profiles/profile-1/cdp",
        "headers": {"Authorization": "Bearer cbm_run_private_capability"},
    }
    assert runner.cleaned is True


def test_static_program_does_not_embed_agent_supplied_code_or_args(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    launcher = RecordingLauncher()
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    asyncio.run(
        adapter(
            request(
                tmp_path,
                "click",
                {"selector": "button'); __import__('os').system('touch /tmp/pwn') #"},
            )
        )
    )

    program = launcher.stdin.decode("utf-8")
    assert "__import__('os')" not in program
    assert "touch /tmp/pwn" not in program
    assert "system(" not in program
    assert "eval(" not in program
    assert "exec(" not in program
    assert "from browser_harness" not in program
    assert "import browser_harness" not in program
    assert "click(arguments" not in program
    assert "read_text(" not in program
    assert "page_info()" in program
    assert "goto_url(" in program
    assert "fill_input(" in program
    assert "js(" in program
    lower_program = program.lower()
    assert "remote" not in lower_program
    assert "daemon" not in lower_program
    assert "record" not in lower_program
    assert "auth" not in lower_program
    assert "browser_use_api_key" not in lower_program
    assert launcher.request_payload["arguments"]["selector"].startswith("button")


def test_attach_only_env_strips_inherited_browser_use_cloud_auth_and_remote_selectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    monkeypatch.setenv("BROWSER_USE_API_KEY", "secret-api-key")
    monkeypatch.setenv("BROWSER_USE_BASE_URL", "https://cloud.browser-use.com")
    monkeypatch.setenv("BROWSER_USE_SESSION_ID", "remote-session")
    monkeypatch.setenv("BROWSER_USE_AUTH_TOKEN", "remote-token")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("CBM_ARBITRARY_PARENT", "must-not-leak")
    monkeypatch.setenv("BU_REMOTE_SESSION_ID", "remote")
    monkeypatch.setenv("BU_AUTOSPAWN", "1")
    monkeypatch.setenv("BU_NAME", "inherited")
    launcher = RecordingLauncher()
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert launcher.env["BU_AUTOSPAWN"] == "0"
    assert launcher.env["BU_NAME"] != "inherited"
    assert launcher.env["BH_RECORD"] == "0"
    for forbidden in (
        "BROWSER_USE_API_KEY",
        "BROWSER_USE_BASE_URL",
        "BROWSER_USE_SESSION_ID",
        "BROWSER_USE_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "CBM_ARBITRARY_PARENT",
        "BU_REMOTE_SESSION_ID",
    ):
        assert forbidden not in launcher.env
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_RUNTIME_DIR",
        "BU_AUTOSPAWN",
        "BU_CDP_WS",
        "BU_CDP_URL",
        "BU_NAME",
        "BH_RECORD",
        "CBM_BROWSER_HARNESS_REQUEST_FILE",
    }
    assert set(launcher.env) <= allowed


@pytest.mark.parametrize(
    ("action", "arguments", "forbidden_prefix"),
        [
            ("inspect", {}, "inspect-effect"),
        ("click", {"selector": "button"}, "js:"),
        ("fill", {"selector": "input", "text": "secret"}, "fill_input:"),
        ("read_text", {"selector": "main"}, "js:"),
    ],
)
def test_static_program_denies_out_of_origin_current_page_before_read_or_mutation_helpers(
    tmp_path: Path,
    action: str,
    arguments: dict[str, Any],
    forbidden_prefix: str,
):
    result, calls = execute_static_program(
        tmp_path,
        action=action,
        arguments=arguments,
        page_url="https://evil.example/account",
    )

    assert result == {
        "ok": False,
        "classification": "origin_denied",
        "error": "current page origin is outside the run allowed origin set",
    }
    assert calls == ["page_info"]
    assert not any(call.startswith(forbidden_prefix) for call in calls)


def test_static_program_allows_effective_default_port_origin_before_helper(
    tmp_path: Path,
):
    result, calls = execute_static_program(
        tmp_path,
        action="read_text",
        arguments={},
        page_url="https://example.com:443/account",
    )

    assert result["ok"] is True
    assert result["url"] == "https://example.com:443/account"
    assert calls[0] == "page_info"
    assert calls[1].startswith("js:")


def test_static_program_uses_actual_v018_helper_namespace_and_final_page_info(
    tmp_path: Path,
):
    result, calls = execute_static_program(
        tmp_path,
        action="click",
        arguments={"selector": "button.submit"},
        page_url="https://example.com/done",
    )

    assert result["ok"] is True
    assert result["url"] == "https://example.com/done"
    assert calls[0] == "page_info"
    assert calls[1].startswith("js:")
    assert calls[2] == "page_info"
    assert not any(call.startswith("click:") or call.startswith("read_text:") for call in calls)


def test_static_program_contract_probe_matches_browser_harness_v018_source():
    from scripts.browser_harness_adapter import STATIC_BROWSER_HARNESS_PROGRAM

    program = STATIC_BROWSER_HARNESS_PROGRAM
    assert "from browser_harness import" not in program
    assert "goto_url(" in program
    assert "page_info()" in program
    assert "fill_input(" in program
    assert "js(" in program
    assert "start_remote_daemon" not in program
    assert "stop_remote_daemon" not in program


def test_missing_browser_harness_binary_returns_tool_unavailable(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(executable_resolver=lambda _name: None)

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"


def test_unsupported_action_is_rejected_before_gateway_or_process(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "raw_cdp", {"method": "Target.createTarget"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "unsupported_action"
    assert called is False


def test_origin_policy_blocks_navigation_before_gateway_or_process(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://evil.example"}))
    )

    assert result.outcome == "failed"
    assert result.classification == "origin_denied"
    assert called is False


def test_argument_bounds_are_enforced_before_gateway_or_process(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(executable_resolver=lambda _name: "/bin/browser-harness")

    result = asyncio.run(
        adapter(request(tmp_path, "fill", {"selector": "input", "text": "x" * 9000}))
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"


def test_timeout_kills_process_group_deletes_request_and_cleans_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import browser_harness_adapter as adapter_module
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(sleep=60)
    group_kills: list[tuple[int, int]] = []

    def fake_killpg(pid: int, sig: int) -> None:
        group_kills.append((pid, sig))

    monkeypatch.setattr(adapter_module.os, "killpg", fake_killpg)
    adapter = BrowserHarnessAdapter(
        timeout_seconds=0.01,
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "transient_timeout"
    assert group_kills == [(4321, adapter_module.signal.SIGKILL)]
    assert launcher.processes[0].killed is False
    assert launcher.processes[0].waited is True
    assert not Path(launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"]).exists()
    assert runner.cleaned is True


def test_cancellation_propagates_after_process_kill_and_gateway_cleanup(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(sleep=60)
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    async def run_and_cancel() -> None:
        task = asyncio.create_task(adapter(request(tmp_path, "inspect", {})))
        await asyncio.sleep(0)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_and_cancel())

    assert launcher.processes[0].killed is True
    assert launcher.processes[0].waited is True
    assert not Path(launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"]).exists()
    assert runner.cleaned is True


def test_nonzero_exit_redacts_and_bounds_stderr(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    launcher = RecordingLauncher(
        returncode=1,
        stderr=(
            b"Authorization: Bearer cbm_run_private_capability "
            + b"https://u:p@example.com?token=secret "
            + (b"x" * 5000)
        ),
    )
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert "cbm_run_private_capability" not in result.message
    assert "u:p" not in result.message
    assert "secret" not in result.message
    assert len(result.message) <= 700


def test_streaming_stdout_overflow_kills_process_before_eof_and_cleans_up(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter, MAX_OUTPUT_BYTES

    runner = FakeGatewayRunner()
    launcher = StreamingLauncher(stdout_chunks=[b"x" * (MAX_OUTPUT_BYTES + 1), b"never"])
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert "exceeds size bound" in result.message
    assert launcher.processes[0].killed is True
    assert launcher.processes[0].waited is True
    assert not Path(launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"]).exists()
    assert runner.cleaned is True


def test_streaming_overflow_falls_back_to_process_kill_when_group_kill_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import browser_harness_adapter as adapter_module
    from scripts.browser_harness_adapter import BrowserHarnessAdapter, MAX_OUTPUT_BYTES

    runner = FakeGatewayRunner()
    launcher = StreamingLauncher(stdout_chunks=[b"x" * (MAX_OUTPUT_BYTES + 1)])

    def missing_group(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(adapter_module.os, "killpg", missing_group)
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, runner),
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert launcher.processes[0].killed is True
    assert launcher.processes[0].waited is True
    assert not Path(launcher.env["CBM_BROWSER_HARNESS_REQUEST_FILE"]).exists()
    assert runner.cleaned is True


def test_oversized_or_invalid_output_fails_closed_without_secret_leak(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout=b"{" + (b"x" * 70000)),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert len(result.message) <= 700


def test_successful_output_without_final_url_fails_closed(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout=b'{"ok":true,"title":"No URL"}\n'),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"


def test_successful_output_with_disallowed_final_url_fails_origin_denied(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(stdout=b'{"ok":true,"url":"https://evil.example"}\n'),
    )

    result = asyncio.run(adapter(request(tmp_path, "click", {"selector": "button"})))

    assert result.outcome == "failed"
    assert result.classification == "origin_denied"


def test_second_browser_signal_fails_closed(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=RecordingLauncher(
            stdout=b'{"ok":true,"opened_second_browser":true,"url":"https://example.com"}\n'
        ),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "second_browser_attempt"


def test_read_text_defaults_to_body_and_returns_bounded_redacted_text(tmp_path: Path):
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    launcher = RecordingLauncher(
        stdout=(
            b'{"ok":true,"command":"read_text","selector":null,"text":"Bearer cbm_run_private_capability '
            + (b"x" * 12000)
            + b'","url":"https://example.com"}\n'
        )
    )
    adapter = BrowserHarnessAdapter(
        executable_resolver=lambda _name: "/bin/browser-harness",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "read_text", {})))

    assert launcher.request_payload == {
        "action": "read_text",
        "arguments": {},
        "allowed_origins": ["https://example.com"],
    }
    assert result.outcome == "succeeded"
    assert result.payload["selector"] is None
    assert "cbm_run_private_capability" not in result.payload["text"]
    assert len(result.payload["text"]) <= 8000


def test_default_mcp_router_registers_real_browser_harness_adapter():
    from scripts.cbm_mcp import _default_router_adapters
    from scripts.browser_harness_adapter import BrowserHarnessAdapter

    adapters = _default_router_adapters()

    assert adapters["unbrowse"] is not adapters["browser-harness"]
    assert adapters["stagehand"] is not adapters["browser-harness"]
    assert isinstance(adapters["browser-harness"], BrowserHarnessAdapter)
