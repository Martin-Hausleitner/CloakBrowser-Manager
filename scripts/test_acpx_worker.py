from __future__ import annotations

import asyncio
import json
import os
import re
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

ROOT = Path(__file__).resolve().parents[1]


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


def write_worker_key(tmp_path: Path, token: str = "cbm_worker_private") -> Path:
    key = tmp_path / "worker.key"
    key.write_text(token + "\n", encoding="utf-8")
    os.chmod(key, 0o600)
    return key


def write_preflight_mcp(tmp_path: Path) -> Path:
    mcp = tmp_path / "preflight-mcp.json"
    mcp.write_text('{"mcpServers":[]}', encoding="utf-8")
    os.chmod(mcp, 0o600)
    return mcp


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


def test_checked_in_acpx_config_only_overrides_opencode_to_pure_local_acp():
    parsed = json.loads((ROOT / ".acpxrc.json").read_text(encoding="utf-8"))

    assert parsed == {
        "agents": {
            "opencode": {
                "command": "opencode",
                "args": ["acp", "--pure"],
            }
        }
    }
    serialized = json.dumps(parsed).lower()
    assert "auth" not in serialized
    assert "mcp" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized


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


def test_worker_reports_agent_preflights_and_cleans_empty_mcp_config(tmp_path: Path):
    manager = FakeManager()

    class DoctorRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.preflight_calls = []

        async def preflight_agent(self, *, cwd, agent, session_name, environment, mcp_config):
            assert environment == {"CBM_MANAGER_URL": "https://manager.local"}
            assert json.loads(Path(mcp_config).read_text(encoding="utf-8")) == {
                "mcpServers": []
            }
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


@pytest.mark.parametrize("transient_reason", ["adapter_unavailable", "protocol_error"])
def test_worker_preflight_retries_one_transient_failure_per_agent_before_reporting(
    tmp_path: Path,
    transient_reason: str,
):
    manager = FakeManager()

    class TransientRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.calls_by_agent = {}

        async def preflight_agent(self, *, agent, **_kwargs):
            count = self.calls_by_agent.get(agent, 0) + 1
            self.calls_by_agent[agent] = count
            if count == 1:
                return {"ready": False, "reason_code": transient_reason}
            return {"ready": True, "reason_code": "ok"}

    runtime = TransientRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    asyncio.run(worker.refresh_preflights())

    assert set(runtime.calls_by_agent) == {
        "claude",
        "codex",
        "cursor",
        "grok-build",
        "opencode",
    }
    assert set(runtime.calls_by_agent.values()) == {2}
    assert {ready for _agent, ready, _reason in manager.preflights} == {True}
    assert {reason for _agent, _ready, reason in manager.preflights} == {"ok"}


def test_worker_preflight_does_not_retry_auth_required_or_ready_results(tmp_path: Path):
    manager = FakeManager()

    class StableRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.calls_by_agent = {}

        async def preflight_agent(self, *, agent, **_kwargs):
            count = self.calls_by_agent.get(agent, 0) + 1
            self.calls_by_agent[agent] = count
            if agent == "opencode":
                return {"ready": False, "reason_code": "auth_required"}
            return {"ready": True, "reason_code": "ok"}

    runtime = StableRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    asyncio.run(worker.refresh_preflights())

    assert set(runtime.calls_by_agent.values()) == {1}
    assert ("opencode", False, "auth_required") in manager.preflights
    assert all(
        reason == "ok"
        for agent, ready, reason in manager.preflights
        if agent != "opencode" and ready
    )


def test_worker_preflight_reports_permanent_transient_failure_after_one_retry(
    tmp_path: Path,
):
    manager = FakeManager()

    class PermanentlyUnavailableRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.calls_by_agent = {}

        async def preflight_agent(self, *, agent, **_kwargs):
            count = self.calls_by_agent.get(agent, 0) + 1
            self.calls_by_agent[agent] = count
            return {"ready": False, "reason_code": "adapter_unavailable"}

    runtime = PermanentlyUnavailableRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    asyncio.run(worker.refresh_preflights())

    assert set(runtime.calls_by_agent.values()) == {2}
    assert {ready for _agent, ready, _reason in manager.preflights} == {False}
    assert {reason for _agent, _ready, reason in manager.preflights} == {
        "adapter_unavailable"
    }


def test_worker_preflight_uses_private_empty_mcp_without_cbm_run_env(tmp_path: Path):
    manager = FakeManager()
    seen_mcp_paths: list[Path] = []

    class EmptyMcpRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.preflight_calls = []

        async def preflight_agent(
            self,
            *,
            cwd,
            agent,
            session_name,
            environment,
            mcp_config,
        ):
            forbidden = {
                "CBM_RUN_CAPABILITY_FILE",
                "CBM_PROFILE_ID",
                "CBM_TASK_RUN_ID",
                "CBM_ALLOWED_ORIGINS",
            }
            assert forbidden.isdisjoint(environment)
            assert environment == {"CBM_MANAGER_URL": "https://manager.local"}
            mcp_path = Path(mcp_config)
            assert mcp_path.is_file()
            assert mcp_path.stat().st_mode & 0o077 == 0
            assert json.loads(mcp_path.read_text(encoding="utf-8")) == {"mcpServers": []}
            seen_mcp_paths.append(mcp_path)
            self.preflight_calls.append((agent, session_name))
            return {"ready": True, "reason_code": "ok"}

    runtime = EmptyMcpRuntime()
    worker = AcpxWorker(manager, make_config(tmp_path), runtime=runtime)

    asyncio.run(worker.refresh_preflights())

    assert {path.exists() for path in seen_mcp_paths} == {False}
    assert manager.preflights
    assert {ready for _agent, ready, _reason in manager.preflights} == {True}
    assert list((tmp_path / "capabilities").iterdir()) == []


def test_worker_preflight_empty_mcp_file_is_cleaned_after_runtime_error(tmp_path: Path):
    manager = FakeManager()
    seen_mcp_paths: list[Path] = []

    class FailingPreflightRuntime(FakeRuntime):
        async def preflight_agent(self, *, mcp_config, **_kwargs):
            seen_mcp_paths.append(Path(mcp_config))
            raise RuntimeError("boom")

    worker = AcpxWorker(manager, make_config(tmp_path), runtime=FailingPreflightRuntime())

    asyncio.run(worker.refresh_preflights())

    assert seen_mcp_paths
    assert {path.exists() for path in seen_mcp_paths} == {False}
    assert {ready for _agent, ready, _reason in manager.preflights} == {False}
    assert {reason for _agent, _ready, reason in manager.preflights} == {"protocol_error"}


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
        mcp_config=write_preflight_mcp(tmp_path),
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
        mcp_config=write_preflight_mcp(tmp_path),
    ))

    assert result == {"ready": True, "reason_code": "ok"}
    assert count_file.read_text(encoding="utf-8") == "2"


def test_real_preflight_passes_same_empty_mcp_config_to_ensure_and_close(tmp_path: Path):
    commands_file = tmp_path / "commands.jsonl"
    executable = tmp_path / "fake-acpx-preflight-mcp"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json, pathlib, stat, sys
commands_file = pathlib.Path({str(commands_file)!r})
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv or 'close' in sys.argv:
    mcp = pathlib.Path(sys.argv[sys.argv.index('--mcp-config') + 1])
    commands_file.open('a', encoding='utf-8').write(json.dumps({{
        'verb': 'ensure' if 'ensure' in sys.argv else 'close',
        'mcp': str(mcp),
        'mode': stat.S_IMODE(mcp.stat().st_mode),
        'body': json.loads(mcp.read_text(encoding='utf-8')),
    }}) + '\\n')
    print(json.dumps({{'ok': True}}))
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))
    preflight_mcp = write_preflight_mcp(tmp_path)

    result = asyncio.run(runtime.preflight_agent(
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        environment={},
        mcp_config=preflight_mcp,
    ))

    commands = [
        json.loads(line)
        for line in commands_file.read_text(encoding="utf-8").splitlines()
    ]
    assert result == {"ready": True, "reason_code": "ok"}
    assert [command["verb"] for command in commands] == ["ensure", "close"]
    assert {command["mcp"] for command in commands} == {str(preflight_mcp.resolve())}
    assert {command["mode"] for command in commands} == {0o600}
    assert {json.dumps(command["body"], sort_keys=True) for command in commands} == {
        '{"mcpServers": []}'
    }


def test_real_preflight_scrubs_ambient_cbm_run_env_from_ensure_and_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    forbidden = [
        "CBM_RUN_CAPABILITY_FILE",
        "CBM_PROFILE_ID",
        "CBM_TASK_RUN_ID",
        "CBM_ALLOWED_ORIGINS",
    ]
    for key in forbidden:
        monkeypatch.setenv(key, f"ambient-{key.lower()}")
    monkeypatch.setenv("CBM_MANAGER_URL", "https://ambient-manager.local")

    env_file = tmp_path / "preflight-env.jsonl"
    executable = tmp_path / "fake-acpx-preflight-env"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json, os, pathlib, sys
env_file = pathlib.Path({str(env_file)!r})
if '--version' in sys.argv:
    print('0.12.1')
elif 'ensure' in sys.argv or 'close' in sys.argv:
    env_file.open('a', encoding='utf-8').write(json.dumps({{
        'verb': 'ensure' if 'ensure' in sys.argv else 'close',
        'manager': os.environ.get('CBM_MANAGER_URL'),
        'forbidden': {{key: os.environ.get(key) for key in {forbidden!r}}},
    }}) + '\\n')
    print(json.dumps({{'ok': True}}))
""",
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    runtime = AcpxRuntime(replace(make_config(tmp_path), acpx_executable=str(executable)))

    result = asyncio.run(runtime.preflight_agent(
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        environment={
            "CBM_MANAGER_URL": "https://manager.local",
            "CBM_TASK_RUN_ID": "explicit-preflight-run",
        },
        mcp_config=write_preflight_mcp(tmp_path),
    ))

    records = [
        json.loads(line)
        for line in env_file.read_text(encoding="utf-8").splitlines()
    ]
    assert result == {"ready": True, "reason_code": "ok"}
    assert [record["verb"] for record in records] == ["ensure", "close"]
    assert {record["manager"] for record in records} == {"https://manager.local"}
    assert all(
        value is None
        for record in records
        for value in record["forbidden"].values()
    )


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


def test_worker_execute_claim_uses_manager_capability_and_real_mcp_config(tmp_path: Path):
    manager = FakeManager()
    config = make_config(tmp_path)

    class RealRunRuntime(FakeRuntime):
        async def ensure_session(self, *, cwd, agent, session_name, environment):
            assert environment["CBM_RUN_CAPABILITY_FILE"]
            assert Path(environment["CBM_RUN_CAPABILITY_FILE"]).read_text(
                encoding="utf-8"
            ) == "cbm_run_private_capability"
            assert environment["CBM_PROFILE_ID"] == "profile-1"
            assert environment["CBM_TASK_RUN_ID"] == "run-1"
            assert json.loads(environment["CBM_ALLOWED_ORIGINS"]) == ["https://app.local"]
            assert json.loads(
                Path(config.mcp_config).read_text(encoding="utf-8")
            ) == {
                "mcpServers": [
                    {"name": "cloakbrowser", "command": "cbm-mcp", "args": []}
                ]
            }
            await super().ensure_session(
                cwd=cwd,
                agent=agent,
                session_name=session_name,
                environment=environment,
            )

    runtime = RealRunRuntime()
    worker = AcpxWorker(manager, config, runtime=runtime)

    result = asyncio.run(
        worker.execute_claim(claim(allowed_origins=["https://app.local"]))
    )

    assert result == {"status": "succeeded"}
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
    token_file = write_worker_key(tmp_path)

    config = build_worker_config(
        [
            "--manager-url",
            "https://manager.local",
            "--token-file",
            str(token_file),
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
                "--token-file",
                str(token_file),
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


def test_build_worker_config_rejects_inline_cli_token_and_env_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    token = "cbm_worker_" + ("ab" * 32)
    with pytest.raises(ValueError, match="token file"):
        build_worker_config(["--manager-url", "https://manager.local", "--token", token])

    monkeypatch.setenv("CBM_WORKER_TOKEN", token)
    monkeypatch.delenv("CBM_WORKER_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError, match="token file"):
        build_worker_config(["--manager-url", "https://manager.local"])


def test_build_worker_config_requires_private_regular_token_file(tmp_path: Path):
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
    good = write_worker_key(tmp_path)
    base = [
        "--manager-url",
        "https://manager.local",
        "--worktree",
        str(tmp_path),
        "--permission-policy",
        str(policy),
        "--mcp-config",
        str(mcp),
        "--capability-dir",
        str(cap),
    ]

    config = build_worker_config(["--token-file", str(good), *base])
    assert config.token == "cbm_worker_private"
    assert config.token_file == str(good)

    missing = tmp_path / "missing.key"
    with pytest.raises(ValueError, match="token file"):
        build_worker_config(["--token-file", str(missing), *base])

    public = tmp_path / "public.key"
    public.write_text("cbm_worker_private\n", encoding="utf-8")
    os.chmod(public, 0o644)
    with pytest.raises(ValueError, match="0600"):
        build_worker_config(["--token-file", str(public), *base])

    owner_execute = tmp_path / "owner-execute.key"
    owner_execute.write_text("cbm_worker_private\n", encoding="utf-8")
    os.chmod(owner_execute, 0o700)
    with pytest.raises(ValueError, match="0600"):
        build_worker_config(["--token-file", str(owner_execute), *base])

    read_only = tmp_path / "read-only.key"
    read_only.write_text("cbm_worker_private\n", encoding="utf-8")
    os.chmod(read_only, 0o400)
    with pytest.raises(ValueError, match="0600"):
        build_worker_config(["--token-file", str(read_only), *base])

    special_bits = tmp_path / "special-bits.key"
    special_bits.write_text("cbm_worker_private\n", encoding="utf-8")
    os.chmod(special_bits, 0o4600)
    with pytest.raises(ValueError, match="0600"):
        build_worker_config(["--token-file", str(special_bits), *base])

    real = tmp_path / "real.key"
    real.write_text("cbm_worker_private\n", encoding="utf-8")
    os.chmod(real, 0o600)
    link = tmp_path / "linked.key"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        build_worker_config(["--token-file", str(link), *base])


def test_acpx_worker_main_sanitizes_inline_token_rejection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    from scripts import acpx_worker

    token = "cbm_worker_" + ("cd" * 32)
    rc = acpx_worker.main(["--manager-url", "https://manager.local", "--token", token])
    captured = capsys.readouterr()
    assert rc == 2
    assert token not in captured.out + captured.err
    assert "CBM_WORKER_TOKEN" not in captured.out + captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["--bogus", "cbm_worker_" + ("ef" * 32)],
        ["--poll-interval", "cbm_worker_" + ("01" * 32)],
        ["--token=cbm_worker_" + ("23" * 32)],
    ],
)
def test_acpx_worker_main_intercepts_argparse_errors_without_systemexit_or_secret_leak(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
):
    from scripts import acpx_worker

    rc = acpx_worker.main(argv)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 2
    assert "usage:" not in combined.lower()
    assert "--bogus" not in combined
    assert "--poll-interval" not in combined
    assert "--token" not in combined
    assert "CBM_WORKER_TOKEN" not in combined
    assert re.search(r"cbm_worker_[0-9a-fA-F]{16,}", combined) is None


def test_acpx_worker_main_help_exits_without_systemexit_or_secret_error(
    capsys: pytest.CaptureFixture[str],
):
    from scripts import acpx_worker

    rc = acpx_worker.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "usage:" in captured.out.lower()
    assert captured.err == ""


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
