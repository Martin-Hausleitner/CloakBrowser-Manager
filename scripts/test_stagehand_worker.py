from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scripts.stagehand_worker import StagehandClient, StagehandWorker, select_task_target


class FakeResponse:
    def __init__(self, status_code: int, data=None):
        self.status_code = status_code
        self._data = data
        self.text = ""

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def claim_fixture() -> dict:
    return {
        "id": "run-stagehand-1",
        "harness": "stagehand",
        "task": "Open https://example.com and return the title.",
        "allowed_origins": ["https://example.com"],
        "timeout_seconds": 90,
    }


def test_client_claims_only_stagehand_harness():
    http = FakeHTTP(FakeResponse(204))
    client = StagehandClient(
        "https://manager.local",
        token="cbm_worker_" + "1" * 64,
        http=http,
    )

    assert client.claim() is None
    method, url, _kwargs = http.calls[0]
    assert method == "POST"
    assert url == "https://manager.local/internal/task-runs/claim?harness=stagehand"


def test_select_task_target_requires_explicit_allowed_url():
    assert select_task_target(claim_fixture()) == "https://example.com"

    cross_origin = claim_fixture()
    cross_origin["allowed_origins"] = ["https://allowed.example"]
    with pytest.raises(ValueError, match="allowed origin"):
        select_task_target(cross_origin)


def test_execute_claim_attaches_stagehand_to_manager_browser_and_cleans_up():
    class FakeClient:
        def __init__(self):
            self.base_url = "https://manager.local"
            self.outputs = []
            self.completed = []
            self.failed = []
            self.revoked = []

        def issue_capability(self, run_id):
            return {"cdp_url": "/api/run/cdp", "headers": {"X-CBM-Run": "opaque"}}

        def heartbeat(self, run_id):
            return {"heartbeat_interval_seconds": 15}

        def output(self, run_id, **kwargs):
            self.outputs.append((run_id, kwargs))
            return {"id": f"out-{len(self.outputs)}"}

        def complete(self, run_id):
            self.completed.append(run_id)

        def fail(self, run_id, **kwargs):
            self.failed.append((run_id, kwargs))

        def revoke_capability(self, run_id):
            self.revoked.append(run_id)

    cleanup = []

    class FakeGateway:
        async def cleanup(self):
            cleanup.append("gateway")

    class FakeRunner:
        async def run(self):
            return {"url": "https://example.com/", "title": "Example Domain"}

        async def close(self):
            cleanup.append("runner")

    async def gateway_factory(**_kwargs):
        return FakeGateway(), "ws://127.0.0.1:45678/nonce"

    async def endpoint_reader(browser_ws):
        assert browser_ws == "ws://127.0.0.1:45678/nonce"
        return "ws://127.0.0.1:45678/nonce/devtools/browser/browser-id"

    async def runner_factory(*, node_bin, script_path, browser_ws, target_url):
        assert node_bin == "node"
        assert script_path == "/runtime/stagehand_runner.mjs"
        assert browser_ws.endswith("/devtools/browser/browser-id")
        assert target_url == "https://example.com/"
        return FakeRunner()

    page_reads = []

    async def page_reader(browser_ws, expected_url):
        page_reads.append((browser_ws, expected_url))
        return {"url": expected_url, "title": "Example Domain"}

    client = FakeClient()
    worker = StagehandWorker(
        client=client,
        node_bin="node",
        script_path="/runtime/stagehand_runner.mjs",
        gateway_factory=gateway_factory,
        endpoint_reader=endpoint_reader,
        runner_factory=runner_factory,
        page_reader=page_reader,
    )

    result = asyncio.run(worker.execute_claim(claim_fixture()))

    assert result == {"status": "succeeded", "title": "Example Domain"}
    assert [item[1]["kind"] for item in client.outputs] == [
        "action",
        "status",
        "observation",
        "summary",
    ]
    assert client.outputs[0][1]["payload"] == {
        "name": "navigate",
        "url": "https://example.com/",
        "step": 1,
    }
    assert client.completed == ["run-stagehand-1"]
    assert client.failed == []
    assert client.revoked == ["run-stagehand-1"]
    assert cleanup == ["runner", "gateway"]
    assert len(page_reads) == 1


def test_execute_claim_fails_closed_and_redacts_manager_error():
    calls = SimpleNamespace(failed=[], revoked=[], completed=[])

    class FakeClient:
        base_url = "https://manager.local"

        def issue_capability(self, run_id):
            return {"cdp_url": "/api/run/cdp", "headers": {}}

        def heartbeat(self, run_id):
            return {"heartbeat_interval_seconds": 15}

        def output(self, *args, **kwargs):
            return {}

        def fail(self, run_id, **kwargs):
            calls.failed.append((run_id, kwargs))

        def complete(self, run_id):
            calls.completed.append(run_id)

        def revoke_capability(self, run_id):
            calls.revoked.append(run_id)

    async def bad_gateway(**_kwargs):
        raise RuntimeError("Bearer cbm_worker_secret failed")

    worker = StagehandWorker(client=FakeClient(), gateway_factory=bad_gateway)
    result = asyncio.run(worker.execute_claim(claim_fixture()))

    assert result == {"status": "failed", "error_code": "internal_error"}
    assert calls.completed == []
    assert calls.revoked == ["run-stagehand-1"]
    assert "cbm_worker_secret" not in calls.failed[0][1]["message"]


def test_cleanup_revokes_capability_when_runner_close_races_with_process_exit():
    calls = SimpleNamespace(outputs=[], completed=[], revoked=[], gateway=[])

    class FakeClient:
        base_url = "https://manager.local"

        def issue_capability(self, run_id):
            return {"cdp_url": "/api/run/cdp", "headers": {}}

        def heartbeat(self, run_id):
            return {"heartbeat_interval_seconds": 15}

        def output(self, run_id, **kwargs):
            calls.outputs.append((run_id, kwargs))
            return {}

        def complete(self, run_id):
            calls.completed.append(run_id)

        def fail(self, *args, **kwargs):
            raise AssertionError("successful run must not fail")

        def revoke_capability(self, run_id):
            calls.revoked.append(run_id)

    class FakeGateway:
        async def cleanup(self):
            calls.gateway.append("cleaned")

    class RacingRunner:
        async def run(self):
            return {"url": "https://example.com/", "title": "Example Domain"}

        async def close(self):
            raise ProcessLookupError("runner already exited")

    async def gateway_factory(**_kwargs):
        return FakeGateway(), "ws://127.0.0.1:45678/nonce"

    async def endpoint_reader(_browser_ws):
        return "ws://127.0.0.1:45678/nonce/devtools/browser/browser-id"

    async def runner_factory(**_kwargs):
        return RacingRunner()

    async def page_reader(_browser_ws, expected_url):
        return {"url": expected_url, "title": "Example Domain"}

    worker = StagehandWorker(
        client=FakeClient(),
        gateway_factory=gateway_factory,
        endpoint_reader=endpoint_reader,
        runner_factory=runner_factory,
        page_reader=page_reader,
    )

    result = asyncio.run(worker.execute_claim(claim_fixture()))

    assert result == {"status": "succeeded", "title": "Example Domain"}
    assert calls.completed == ["run-stagehand-1"]
    assert calls.gateway == ["cleaned"]
    assert calls.revoked == ["run-stagehand-1"]
