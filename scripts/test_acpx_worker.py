from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.acpx_worker import (
    AcpxManagerClient,
    AcpxRuntime,
    AcpxRuntimeError,
    AcpxWorker,
    AcpxWorkerConfig,
    build_worker_config,
)


class FakeHTTPResponse:
    def __init__(self, status_code: int, data=None):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class FakeManager:
    def __init__(self, *, heartbeats=None):
        self.heartbeats = list(heartbeats or [{"heartbeat_interval_seconds": 1}])
        self.outputs = []
        self.completed = []
        self.failed = []
        self.revoked = []
        self.preflights = []
        self.heartbeat_calls = 0

    def issue_capability(self, run_id):
        return {
            "token": "cbm_run_private_capability",
            "profile_id": "profile-1",
            "run_id": run_id,
        }

    def heartbeat(self, run_id):
        self.heartbeat_calls += 1
        if self.heartbeats:
            return self.heartbeats.pop(0)
        return {"heartbeat_interval_seconds": 1}

    def output(self, run_id, **body):
        self.outputs.append((run_id, body))
        return {"id": f"output-{len(self.outputs)}"}

    def complete(self, run_id):
        self.completed.append(run_id)
        return {"status": "succeeded"}

    def fail(self, run_id, *, error_code, message):
        self.failed.append((run_id, error_code, message))
        return {"status": "failed"}

    def revoke_capability(self, run_id):
        self.revoked.append(run_id)

    def report_preflight(self, *, agent, ready, reason_code):
        self.preflights.append((agent, ready, reason_code))


class FakeRuntime:
    def __init__(self, *, wait_for_cancel=False):
        self.wait_for_cancel = wait_for_cancel
        self.version_checked = False
        self.ensure_calls = []
        self.prompt_calls = []
        self.cancel_calls = []

    async def validate_version(self):
        self.version_checked = True

    async def ensure_session(self, *, cwd, agent, session_name, environment):
        assert "CBM_RUN_CAPABILITY_FILE" in environment
        self.ensure_calls.append((cwd, agent, session_name))

    async def run_prompt(
        self,
        *,
        cwd,
        agent,
        session_name,
        prompt,
        timeout_seconds,
        environment,
        emit,
        cancel_event,
    ):
        capability_file = Path(environment["CBM_RUN_CAPABILITY_FILE"])
        assert capability_file.read_text(encoding="utf-8") == "cbm_run_private_capability"
        assert "cbm_run_private_capability" not in json.dumps(environment)
        self.prompt_calls.append((cwd, agent, session_name, prompt, timeout_seconds))
        if self.wait_for_cancel:
            await asyncio.wait_for(cancel_event.wait(), timeout=1)
            return None
        await emit(
            {
                "idempotency_key": "acpx-1",
                "kind": "summary",
                "summary": "ACPX completed",
                "payload": {"text": "ACPX completed"},
            }
        )
        return "ACPX completed"

    async def cancel(self, *, cwd, agent, session_name):
        self.cancel_calls.append((cwd, agent, session_name))


def make_config(tmp_path: Path) -> AcpxWorkerConfig:
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)
    capability_dir = tmp_path / "capabilities"
    capability_dir.mkdir(mode=0o700)
    return AcpxWorkerConfig(
        manager_url="https://manager.local",
        worker_id="acpx-worker",
        worktree=tmp_path,
        permission_policy=policy,
        mcp_config=mcp,
        capability_dir=capability_dir,
        token="cbm_worker_private",
        heartbeat_interval_seconds=0.01,
    )


def claim(**overrides):
    body = {
        "id": "run-1",
        "task_session_id": "task-session-1",
        "task": "Inspect the browser",
        "profile_id": "profile-1",
        "sandbox_id": "default",
        "harness": "acpx",
        "agent": "cursor",
        "timeout_seconds": 30,
    }
    body.update(overrides)
    return body


def test_manager_client_claims_only_acpx_runs():
    http = FakeHTTP([FakeHTTPResponse(200, claim())])
    client = AcpxManagerClient(
        "https://manager.local/base", token="cbm_worker_private", http=http
    )

    assert client.claim()["agent"] == "cursor"
    method, url, _kwargs = http.calls[0]
    assert method == "POST"
    assert url == "https://manager.local/base/internal/task-runs/claim?harness=acpx"


def test_worker_executes_acpx_session_streams_outputs_and_cleans_capability(tmp_path: Path):
    manager = FakeManager()
    runtime = FakeRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    result = asyncio.run(worker.execute_claim(claim()))

    assert result == {"status": "succeeded"}
    assert runtime.version_checked is True
    assert runtime.ensure_calls[0][1] == "cursor"
    assert runtime.prompt_calls[0][3] == "Inspect the browser"
    assert manager.outputs[0][1]["kind"] == "summary"
    assert manager.completed == ["run-1"]
    assert manager.failed == []
    assert manager.revoked == ["run-1"]
    assert list((tmp_path / "capabilities").iterdir()) == []


def test_worker_reports_agent_preflights_and_cleans_doctor_capability(tmp_path: Path):
    manager = FakeManager()

    class DoctorRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.preflight_calls = []

        async def preflight_agent(self, *, cwd, agent, session_name, environment):
            capability_file = Path(environment["CBM_RUN_CAPABILITY_FILE"])
            assert capability_file.is_file()
            assert capability_file.stat().st_mode & 0o077 == 0
            self.preflight_calls.append((agent, session_name))
            if agent == "cursor":
                return {"ready": True, "reason_code": "ok"}
            return {"ready": False, "reason_code": "auth_required"}

    runtime = DoctorRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    asyncio.run(worker.refresh_preflights())
    first_sessions = list(runtime.preflight_calls)
    asyncio.run(worker.refresh_preflights())
    second_sessions = runtime.preflight_calls[len(first_sessions):]

    assert runtime.version_checked is True
    assert [item[0] for item in manager.preflights] == [
        "claude",
        "codex",
        "cursor",
        "grok-build",
        "opencode",
    ] * 2
    assert first_sessions == second_sessions
    assert ("cursor", True, "ok") in manager.preflights
    assert ("codex", False, "auth_required") in manager.preflights
    assert list((tmp_path / "capabilities").iterdir()) == []


def test_worker_keeps_claim_polling_while_preflight_runs_in_background(tmp_path: Path):
    class PollingManager(FakeManager):
        def __init__(self):
            super().__init__()
            self.claim_calls = 0

        def claim(self):
            self.claim_calls += 1
            return None

    class SlowPreflightRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def preflight_agent(self, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()

    async def scenario():
        manager = PollingManager()
        runtime = SlowPreflightRuntime()
        worker = AcpxWorker(
            manager,
            replace(make_config(tmp_path), poll_interval_seconds=0.01),
            runtime=runtime,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_forever(stop_event=stop))
        await asyncio.wait_for(runtime.started.wait(), timeout=1)
        await asyncio.wait_for(_wait_for(lambda: manager.claim_calls > 0), timeout=1)
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        return manager.claim_calls

    assert asyncio.run(scenario()) > 0


def test_real_preflight_fails_closed_when_cleanup_fails(tmp_path: Path):
    executable = tmp_path / "fake-acpx-preflight"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv:
    print(json.dumps({'acpxRecordId': 'record-1', 'acpxSessionId': 'session-1'}))
elif 'close' in sys.argv:
    print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': 'cleanup failed'}}))
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    result = asyncio.run(runtime.preflight_agent(
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        environment={},
    ))

    assert result == {"ready": False, "reason_code": "protocol_error"}


def test_real_preflight_retries_close_before_reporting_ready(tmp_path: Path):
    count_file = tmp_path / "close-count.txt"
    executable = tmp_path / "fake-acpx-preflight-retry"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json, pathlib, sys
count_file = pathlib.Path({str(count_file)!r})
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv:
    print(json.dumps({{'acpxRecordId': 'record-1', 'acpxSessionId': 'session-1'}}))
elif 'close' in sys.argv:
    count = int(count_file.read_text() or '0') if count_file.exists() else 0
    count_file.write_text(str(count + 1))
    if count == 0:
        print(json.dumps({{'jsonrpc': '2.0', 'id': None, 'error': {{'code': -32603, 'message': 'transient cleanup failed'}}}}))
        raise SystemExit(1)
    print(json.dumps({{'closed': True}}))
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    result = asyncio.run(runtime.preflight_agent(
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        environment={},
    ))

    assert result == {"ready": True, "reason_code": "ok"}
    assert count_file.read_text(encoding="utf-8") == "2"


def test_preflight_reports_missing_adapter_separately_from_version_mismatch(tmp_path: Path):
    manager = FakeManager()
    config = replace(make_config(tmp_path), acpx_executable=str(tmp_path / "missing-acpx"))
    runtime = AcpxRuntime(config)
    worker = AcpxWorker(manager, config, runtime=runtime)

    asyncio.run(worker.refresh_preflights())

    assert manager.preflights
    assert {ready for _agent, ready, _reason in manager.preflights} == {False}
    assert {reason for _agent, _ready, reason in manager.preflights} == {"adapter_unavailable"}


def test_validate_version_classifies_drift_as_version_mismatch(tmp_path: Path):
    executable = tmp_path / "fake-acpx-version-drift"
    executable.write_text(
        """#!/usr/bin/env python3
print('acpx 9.9.9')
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    with pytest.raises(AcpxRuntimeError) as exc:
        asyncio.run(runtime.validate_version())
    assert exc.value.reason_code == "version_mismatch"


def test_run_control_cancellation_cleans_child_process(tmp_path: Path):
    pid_file = tmp_path / "child.pid"
    executable = tmp_path / "fake-acpx-slow-control"
    executable.write_text(
        f"""#!/usr/bin/env python3
import pathlib, time, os
pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))
time.sleep(30)
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    async def scenario():
        task = asyncio.create_task(runtime._run_control([str(executable)], timeout=30))
        await asyncio.wait_for(_wait_for(lambda: pid_file.exists()), timeout=1)
        pid = int(pid_file.read_text(encoding="utf-8"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(_wait_for(lambda: not _process_exists(pid)), timeout=1)

    asyncio.run(scenario())


def test_worker_propagates_manager_cancellation_to_acpx(tmp_path: Path):
    manager = FakeManager(
        heartbeats=[
            {"cancel_requested": True, "heartbeat_interval_seconds": 1},
        ]
    )
    runtime = FakeRuntime(wait_for_cancel=True)
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    result = asyncio.run(worker.execute_claim(claim()))

    assert result == {"status": "cancelled"}
    assert runtime.cancel_calls and runtime.cancel_calls[0][1] == "cursor"
    assert manager.completed == []
    assert manager.failed == []
    assert manager.revoked == ["run-1"]


def test_worker_starts_heartbeat_before_slow_session_ensure(tmp_path: Path):
    manager = FakeManager()

    class SlowEnsureRuntime(FakeRuntime):
        async def ensure_session(self, *, cwd, agent, session_name, environment):
            await asyncio.wait_for(_wait_for(lambda: manager.heartbeat_calls > 0), timeout=1)
            await super().ensure_session(
                cwd=cwd,
                agent=agent,
                session_name=session_name,
                environment=environment,
            )

    runtime = SlowEnsureRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)
    assert asyncio.run(worker.execute_claim(claim())) == {"status": "succeeded"}
    assert manager.heartbeat_calls > 0


@pytest.mark.parametrize(
    "bad_claim",
    [claim(harness="browser-use"), claim(agent=None), claim(agent="shell")],
)
def test_worker_rejects_invalid_claim_without_starting_runtime(tmp_path: Path, bad_claim: dict):
    manager = FakeManager()
    runtime = FakeRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    result = asyncio.run(worker.execute_claim(bad_claim))

    assert result["status"] == "failed"
    assert manager.failed == [("run-1", "internal_error", "invalid ACPX claim")]
    assert runtime.version_checked is False


def test_build_worker_config_requires_private_files_and_absolute_worktree(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)
    cap = tmp_path / "cap"
    cap.mkdir(mode=0o700)

    config = build_worker_config(
        [
            "--manager-url",
            "https://manager.local",
            "--token",
            "cbm_worker_private",
            "--worktree",
            str(tmp_path),
            "--permission-policy",
            str(policy),
            "--mcp-config",
            str(mcp),
            "--capability-dir",
            str(cap),
        ]
    )
    assert config.worktree == tmp_path.resolve()

    with pytest.raises(ValueError, match="absolute directory"):
        build_worker_config(
            [
                "--manager-url",
                "https://manager.local",
                "--token",
                "cbm_worker_private",
                "--worktree",
                "relative",
                "--permission-policy",
                str(policy),
                "--mcp-config",
                str(mcp),
                "--capability-dir",
                str(cap),
            ]
        )


def test_real_runtime_streams_versioned_acpx_events_from_stdin(tmp_path: Path):
    executable = tmp_path / "fake-acpx"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv:
    print(json.dumps({'acpxRecordId': 'record-1', 'acpxSessionId': 'session-1'}))
else:
    prompt = sys.stdin.read()
    assert prompt == 'Inspect the browser'
    print(json.dumps({'eventVersion': 1, 'sessionId': 'session-1', 'requestId': 'r1', 'seq': 1, 'stream': 'prompt', 'type': 'assistant_message', 'text': 'ACPX_OK'}))
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    config = replace(make_config(tmp_path), acpx_executable=str(executable))
    runtime = AcpxRuntime(config)
    outputs = []

    async def scenario():
        await runtime.validate_version()
        await runtime.ensure_session(
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            environment={},
        )
        return await runtime.run_prompt(
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            prompt="Inspect the browser",
            timeout_seconds=5,
            environment={},
            emit=lambda output: _append_async(outputs, output),
            cancel_event=asyncio.Event(),
        )

    assert asyncio.run(scenario()) == "ACPX_OK"
    assert outputs[0]["kind"] == "summary"


def test_real_runtime_converts_raw_jsonrpc_auth_error_without_leaking_secret(tmp_path: Path):
    executable = tmp_path / "fake-acpx-error"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv:
    print(json.dumps({'acpxRecordId': 'record-1', 'acpxSessionId': 'session-1'}))
else:
    sys.stdin.read()
    print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': 'AUTH_REQUIRED token=top-secret'}}))
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    async def scenario():
        await runtime.run_prompt(
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            prompt="Inspect",
            timeout_seconds=5,
            environment={},
            emit=lambda output: _append_async([], output),
            cancel_event=asyncio.Event(),
        )

    with pytest.raises(AcpxRuntimeError) as exc:
        asyncio.run(scenario())
    assert "top-secret" not in str(exc.value)


def test_real_runtime_drains_large_stderr_without_deadlock(tmp_path: Path):
    executable = tmp_path / "fake-acpx-stderr"
    executable.write_text(
        """#!/usr/bin/env python3
import json, sys
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv:
    print(json.dumps({'acpxRecordId': 'record-1', 'acpxSessionId': 'session-1'}))
else:
    sys.stdin.read()
    sys.stderr.write('x' * 200000)
    sys.stderr.flush()
    print(json.dumps({'eventVersion': 1, 'sessionId': 's', 'requestId': 'r', 'seq': 1, 'stream': 'prompt', 'type': 'assistant_message', 'text': 'done'}))
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    async def scenario():
        return await runtime.run_prompt(
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            prompt="Inspect",
            timeout_seconds=2,
            environment={},
            emit=lambda output: _append_async([], output),
            cancel_event=asyncio.Event(),
        )

    assert asyncio.run(scenario()) == "done"


async def _append_async(items: list, item) -> None:
    items.append(item)


async def _wait_for(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
