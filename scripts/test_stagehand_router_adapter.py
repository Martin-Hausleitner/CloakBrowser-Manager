from __future__ import annotations

import asyncio
import json
import os
import signal
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


class FakeGatewayRunner:
    def __init__(self) -> None:
        self.cleaned = False

    async def cleanup(self) -> None:
        self.cleaned = True


class RecordingProcess:
    next_pid = 7500

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
        self.pid = RecordingProcess.next_pid
        RecordingProcess.next_pid += 1

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
        process_specs: list[dict[str, Any]] | None = None,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        returncode: int = 0,
        sleep: float = 0,
    ) -> None:
        self.process_specs = list(
            process_specs
            or [
                {"stdout_chunks": [preflight_output()]},
                {
                    "stdout_chunks": stdout_chunks or [success_output()],
                    "stderr_chunks": stderr_chunks or [],
                    "returncode": returncode,
                    "sleep": sleep,
                },
            ]
        )
        self.argvs: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str]] = []
        self.processes: list[RecordingProcess] = []
        self.request_modes: list[int] = []
        self.request_payloads: list[dict[str, Any]] = []

    async def __call__(self, *argv: str, **kwargs: Any) -> RecordingProcess:
        self.argvs.append(tuple(argv))
        self.envs.append(dict(kwargs["env"]))
        request_file = kwargs["env"].get("CBM_STAGEHAND_REQUEST_FILE")
        if request_file:
            path = Path(request_file)
            self.request_modes.append(path.stat().st_mode & 0o777)
            self.request_payloads.append(json.loads(path.read_text(encoding="utf-8")))
        spec = self.process_specs.pop(0) if self.process_specs else {}
        process = RecordingProcess(**spec)
        self.processes.append(process)
        return process


def run_context(tmp_path: Path, **overrides: Any) -> RunScopedBrowserContext:
    capability = tmp_path / "capability"
    capability.write_text("cbm_run_private_capability", encoding="utf-8")
    os.chmod(capability, 0o600)
    return RunScopedBrowserContext(
        manager_url=overrides.pop("manager_url", "https://manager.local"),
        profile_id=overrides.pop("profile_id", "profile-1"),
        task_run_id=overrides.pop("task_run_id", "run-1"),
        allowed_origins=overrides.pop("allowed_origins", ("https://example.com",)),
        capability_file=capability,
        capability_token=overrides.pop("capability_token", "cbm_run_private_capability"),
        lease_id=overrides.pop("lease_id", "lease-1"),
    )


def request(tmp_path: Path, action: str, arguments: dict[str, Any]) -> BrowserToolRequest:
    return BrowserToolRequest(
        tool_id="stagehand",
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


async def endpoint_reader(local_ws: str) -> str:
    assert local_ws == "ws://127.0.0.1:32000/nonce"
    return "ws://127.0.0.1:32000/nonce/json/version/browser"


def preflight_output(**overrides: Any) -> bytes:
    payload = {
        "ok": True,
        "mode": "preflight",
        "node": "20.19.0",
        "stagehandVersion": "3.7.1",
        "supportsCdpUrl": True,
        **overrides,
    }
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


def success_output(**overrides: Any) -> bytes:
    payload = {
        "ok": True,
        "connection_mode": "existing-cdp",
        "used_model": False,
        "action": "inspect",
        "url": "https://example.com/start",
        "title": "Example",
        **overrides,
    }
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


def test_adapter_preflights_stagehand_371_and_caches_success(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(
        process_specs=[
            {"stdout_chunks": [preflight_output()]},
            {"stdout_chunks": [success_output(action="inspect")]},
            {"stdout_chunks": [success_output(action="read_text", text="Visible")]},
        ]
    )
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, runner),
        endpoint_reader=endpoint_reader,
        process_launcher=launcher,
    )

    first = asyncio.run(adapter(request(tmp_path, "inspect", {})))
    second = asyncio.run(adapter(request(tmp_path, "read_text", {})))

    assert first.outcome == "succeeded"
    assert second.outcome == "succeeded"
    assert sum("--preflight" in argv for argv in launcher.argvs) == 1
    assert len(launcher.argvs) == 3


@pytest.mark.parametrize(
    "action",
    ["act", "extract", "observe", "agent"],
)
def test_semantic_actions_are_terminal_model_required_before_side_effects(
    tmp_path: Path,
    action: str,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway,
    )

    result = asyncio.run(adapter(request(tmp_path, action, {"prompt": "click the button"})))

    assert result.outcome == "failed"
    assert result.classification == "model_required"
    assert called is False


def test_deterministic_action_invokes_fixed_runner_with_private_file_and_minimal_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-secret")
    monkeypatch.setenv("STAGEHAND_API_KEY", "stagehand-secret")
    monkeypatch.setenv("CBM_RUN_CAPABILITY_FILE", "/tmp/secret-cap")
    gateway_record: dict[str, Any] = {}
    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(
        stdout_chunks=[
            success_output(
                action="navigate",
                url="https://example.com/done?token=secret",
            )
        ],
    )
    adapter = StagehandRouterAdapter(
        timeout_seconds=5,
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory(gateway_record, runner),
        endpoint_reader=endpoint_reader,
        process_launcher=launcher,
    )

    result = asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://example.com/done?token=secret"}))
    )

    assert result.outcome == "succeeded"
    assert result.classification == "ok"
    assert result.payload == {
        "action": "navigate",
        "connection_mode": "existing-cdp",
        "used_model": False,
        "url": "https://example.com/done?token=REDACTED",
    }
    assert launcher.argvs[0] == ("/bin/node", str(adapter.runner_path), "--preflight")
    assert launcher.argvs[1] == ("/bin/node", str(adapter.runner_path))
    assert launcher.request_modes == [0o600]
    assert launcher.request_payloads == [
        {
            "action": "navigate",
            "arguments": {"url": "https://example.com/done?token=secret"},
            "allowed_origins": ["https://example.com"],
            "cdpUrl": "ws://127.0.0.1:32000/nonce/json/version/browser",
            "connectTimeoutMs": 15000,
        }
    ]
    assert not Path(launcher.envs[1]["CBM_STAGEHAND_REQUEST_FILE"]).exists()
    assert gateway_record == {
        "upstream_http": "https://manager.local/api/profiles/profile-1/cdp",
        "headers": {"Authorization": "Bearer cbm_run_private_capability"},
    }
    assert runner.cleaned is True
    assert set(launcher.envs[1]) <= {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_RUNTIME_DIR",
        "CBM_STAGEHAND_REQUEST_FILE",
    }
    assert "OPENAI_API_KEY" not in launcher.envs[1]
    assert "BROWSERBASE_API_KEY" not in launcher.envs[1]
    assert "STAGEHAND_API_KEY" not in launcher.envs[1]
    assert "CBM_RUN_CAPABILITY_FILE" not in launcher.envs[1]


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("inspect", {}),
        ("click", {"selector": "button"}),
        ("fill", {"selector": "input", "text": "value"}),
        ("read_text", {"selector": "main"}),
    ],
)
def test_current_origin_is_preflighted_before_read_or_mutation_in_runner_source(
    action: str,
    arguments: dict[str, Any],
):
    runner = Path("scripts/stagehand_runtime/router-runner.mjs").read_text(encoding="utf-8")

    assert "assertCurrentOriginAllowed" in runner
    assert runner.index("await assertCurrentOriginAllowed") < runner.index("case \"click\"")
    assert "await page.evaluate" in runner
    assert "document.querySelector" in runner
    assert action in runner or arguments == {}


def test_runner_static_source_is_existing_context_only_and_model_free():
    source = Path("scripts/stagehand_runtime/router-runner.mjs").read_text(encoding="utf-8")
    lower = source.lower()

    assert 'import { Stagehand } from "@browserbasehq/stagehand"' in source
    assert 'env: "LOCAL"' in source
    assert "localBrowserLaunchOptions" in source
    assert "cdpUrl" in source
    assert "keepAlive: true" in source
    assert "disablePino: true" in source
    assert "context.pages()" in source
    assert "newPage" not in source
    assert ".act(" not in source
    assert ".extract(" not in source
    assert ".observe(" not in source
    assert ".agent(" not in source
    assert "browserbase" not in lower.replace("@browserbasehq/stagehand", "")
    assert "modelname" not in lower
    assert "modelclient" not in lower
    assert "api_key" not in lower
    assert "process.env[REQUEST_FILE_ENV]" in source
    assert "process.env.OPENAI_API_KEY" not in source
    assert "process.env.BROWSERBASE_API_KEY" not in source
    assert "child_process" not in source
    assert "eval(" not in source
    assert "function(" not in source


def test_runner_source_rejects_non_private_request_file_before_reading():
    source = Path("scripts/stagehand_runtime/router-runner.mjs").read_text(encoding="utf-8")

    assert "stat.mode & 0o077" in source
    assert "stat.isFile()" in source
    assert "stat.size > MAX_REQUEST_BYTES" in source
    assert source.index("stat.mode & 0o077") < source.index("readFile(requestFile")
    assert (0o644 & 0o077) != 0
    assert (0o600 & 0o077) == 0


@pytest.mark.parametrize(
    ("node_version", "accepted"),
    [
        ("20.18.9", False),
        ("20.19.0", True),
        ("20.19.1", True),
        ("20.99.0", True),
        ("21.0.0", False),
        ("21.9.9", False),
        ("22.11.9", False),
        ("22.12.0", True),
        ("22.12.1", True),
        ("23.0.0", True),
        ("26.5.0", True),
    ],
)
def test_preflight_node_version_matches_package_json_engine_boundaries(
    tmp_path: Path,
    node_version: str,
    accepted: bool,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway,
        process_launcher=RecordingLauncher(
            process_specs=[{"stdout_chunks": [preflight_output(node=node_version)]}]
        ),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    if accepted:
        assert result.classification != "tool_unavailable"
        assert called is True
    else:
        assert result.outcome == "failed"
        assert result.classification == "tool_unavailable"
        assert called is False


@pytest.mark.parametrize(
    "preflight_stdout",
    [
        preflight_output(stagehandVersion="3.7.2"),
        preflight_output(supportsCdpUrl=False),
        preflight_output(mode="runtime"),
        b"log\n" + preflight_output(),
        preflight_output() + preflight_output(),
        b"{}\ntrailing\n",
        b"not-json\n",
        b'{"ok":true}\n',
    ],
)
def test_bad_preflight_returns_tool_unavailable_without_gateway(
    tmp_path: Path,
    preflight_stdout: bytes,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway,
        process_launcher=RecordingLauncher(process_specs=[{"stdout_chunks": [preflight_stdout]}]),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert called is False


def test_missing_runtime_or_node_returns_tool_unavailable_before_gateway(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    missing_runner = tmp_path / "missing.mjs"
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        runner_path=missing_runner,
        gateway_starter=gateway,
    )
    assert asyncio.run(adapter(request(tmp_path, "inspect", {}))).classification == "tool_unavailable"

    adapter = StagehandRouterAdapter(node_resolver=lambda _name: None, gateway_starter=gateway)
    assert asyncio.run(adapter(request(tmp_path, "inspect", {}))).classification == "tool_unavailable"
    assert called is False


def test_public_preflight_runs_exact_stagehand_probe_without_gateway(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    gateway_called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal gateway_called
        gateway_called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(process_specs=[{"stdout_chunks": [preflight_output()]}])
    runner = tmp_path / "router-runner.mjs"
    runner.write_text("// runtime", encoding="utf-8")
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        runner_path=runner,
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter.preflight())

    assert result == {"ready": True, "reason_code": "ready"}
    assert launcher.argvs == [("/bin/node", str(runner.resolve()), "--preflight")]
    assert gateway_called is False


def test_public_preflight_reports_missing_stagehand_runtime_without_gateway_or_process(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    gateway_called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal gateway_called
        gateway_called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    launcher = RecordingLauncher(process_specs=[])
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        runner_path=tmp_path / "missing.mjs",
        gateway_starter=gateway,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter.preflight())

    assert result == {"ready": False, "reason_code": "runtime_missing"}
    assert launcher.argvs == []
    assert gateway_called is False


def test_public_preflight_sanitizes_stagehand_incompatible_runtime(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    runner = tmp_path / "router-runner.mjs"
    runner.write_text("// runtime", encoding="utf-8")
    launcher = RecordingLauncher(
        process_specs=[{"stdout_chunks": [preflight_output(stagehandVersion="3.7.2")]}]
    )
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        runner_path=runner,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter.preflight())

    assert result == {"ready": False, "reason_code": "incompatible_runtime"}
    assert set(result) == {"ready", "reason_code"}


def test_origin_and_argument_validation_happen_before_gateway(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    called = False

    async def gateway(**_kwargs: Any) -> tuple[FakeGatewayRunner, str]:
        nonlocal called
        called = True
        return FakeGatewayRunner(), "ws://127.0.0.1:1/nonce"

    adapter = StagehandRouterAdapter(node_resolver=lambda _name: "/bin/node", gateway_starter=gateway)

    assert asyncio.run(
        adapter(request(tmp_path, "navigate", {"url": "https://evil.example"}))
    ).classification == "origin_denied"
    assert asyncio.run(
        adapter(request(tmp_path, "raw_cdp", {"method": "Target.createTarget"}))
    ).classification == "unsupported_action"
    assert asyncio.run(
        adapter(request(tmp_path, "fill", {"selector": "input", "text": "x" * 9000}))
    ).classification == "policy_denied"
    assert called is False


def test_endpoint_reader_cannot_redirect_stagehand_to_non_gateway_cdp(tmp_path: Path):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher()

    async def bad_endpoint_reader(_local_ws: str) -> str:
        return "ws://127.0.0.1:4444/devtools/browser/other"

    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, runner),
        endpoint_reader=bad_endpoint_reader,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert len(launcher.argvs) == 1
    assert runner.cleaned is True


@pytest.mark.parametrize(
    ("stdout", "classification"),
    [
        (success_output(url=""), "policy_denied"),
        (success_output(url="https://evil.example"), "origin_denied"),
        (success_output(used_model=True), "policy_denied"),
        (success_output(connection_mode="new-browser"), "second_browser_attempt"),
        (success_output(ok=False, classification="origin_denied", error="bad origin"), "origin_denied"),
        (b"not-json\n", "policy_denied"),
        (success_output(**{"Authorization": "Bearer cbm_run_private_capability"}), "ok"),
    ],
)
def test_result_schema_redaction_and_classification_mapping(
    tmp_path: Path,
    stdout: bytes,
    classification: str,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, FakeGatewayRunner()),
        endpoint_reader=endpoint_reader,
        process_launcher=RecordingLauncher(stdout_chunks=[stdout]),
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.classification == classification
    serialized = json.dumps(result.payload) + result.message
    assert "cbm_run_private_capability" not in serialized
    assert "ws://127.0.0.1" not in serialized
    assert "nonce" not in serialized
    assert "secret" not in serialized


def test_timeout_uses_process_group_kill_deletes_request_and_cleans_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    killed_groups: list[tuple[int, int]] = []

    def fake_killpg(pid: int, sig: int) -> None:
        killed_groups.append((pid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg)
    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(stdout_chunks=[success_output()], sleep=60)
    adapter = StagehandRouterAdapter(
        timeout_seconds=0.01,
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, runner),
        endpoint_reader=endpoint_reader,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    action_process = launcher.processes[1]
    assert result.classification == "transient_timeout"
    assert killed_groups == [(action_process.pid, signal.SIGKILL)]
    assert action_process.waited is True
    assert not Path(launcher.envs[1]["CBM_STAGEHAND_REQUEST_FILE"]).exists()
    assert runner.cleaned is True


def test_streaming_overflow_kills_action_and_cleans_gateway(tmp_path: Path):
    from scripts.stagehand_router_adapter import MAX_OUTPUT_BYTES, StagehandRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(stdout_chunks=[b"x" * (MAX_OUTPUT_BYTES + 1), b"never"])
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, runner),
        endpoint_reader=endpoint_reader,
        process_launcher=launcher,
    )

    result = asyncio.run(adapter(request(tmp_path, "inspect", {})))

    assert result.classification == "policy_denied"
    assert "exceeds size bound" in result.message
    assert launcher.processes[1].waited is True
    assert runner.cleaned is True


def test_cancellation_propagates_after_kill_file_deletion_and_gateway_cleanup(
    tmp_path: Path,
):
    from scripts.stagehand_router_adapter import StagehandRouterAdapter

    runner = FakeGatewayRunner()
    launcher = RecordingLauncher(stdout_chunks=[success_output()], sleep=60)
    adapter = StagehandRouterAdapter(
        node_resolver=lambda _name: "/bin/node",
        gateway_starter=gateway_factory({}, runner),
        endpoint_reader=endpoint_reader,
        process_launcher=launcher,
    )

    async def run_and_cancel() -> None:
        task = asyncio.create_task(adapter(request(tmp_path, "inspect", {})))
        for _ in range(20):
            await asyncio.sleep(0)
            if len(launcher.processes) > 1:
                break
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_and_cancel())

    assert launcher.processes[1].waited is True
    assert not Path(launcher.envs[1]["CBM_STAGEHAND_REQUEST_FILE"]).exists()
    assert runner.cleaned is True
