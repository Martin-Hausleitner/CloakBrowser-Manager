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
