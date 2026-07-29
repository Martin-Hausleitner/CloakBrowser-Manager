from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scripts.unbrowse_worker import (
    UnbrowseClient,
    UnbrowseWorker,
    _snapshot_title,
    _validated_browser_endpoint,
    select_task_target,
    start_unbrowse_gateway,
)


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
        "id": "run-unbrowse-1",
        "harness": "unbrowse",
        "task": "Open https://example.com and return the title.",
        "allowed_origins": ["https://example.com"],
        "timeout_seconds": 90,
    }


def test_client_claims_only_unbrowse_harness():
    http = FakeHTTP(FakeResponse(204))
    client = UnbrowseClient("https://manager.local", token="cbm_worker_" + "1" * 64, http=http)

    assert client.claim() is None
    method, url, _kwargs = http.calls[0]
    assert method == "POST"
    assert url == "https://manager.local/internal/task-runs/claim?harness=unbrowse"


def test_select_task_target_requires_explicit_allowed_url():
    assert select_task_target(claim_fixture()) == "https://example.com"

    cross_origin = claim_fixture()
    cross_origin["allowed_origins"] = ["https://allowed.example"]
    with pytest.raises(ValueError, match="allowed origin"):
        select_task_target(cross_origin)

    missing_url = claim_fixture()
    missing_url["task"] = "Open the homepage."
    with pytest.raises(ValueError, match="explicit URL"):
        select_task_target(missing_url)


def test_snapshot_title_reads_unbrowse_page_title_shape():
    assert _snapshot_title({"page_title": "Example Domain"}) == "Example Domain"


def test_validated_browser_endpoint_requires_same_nonce_gateway():
    gateway = "ws://127.0.0.1:45678/nonce"
    endpoint = "ws://127.0.0.1:45678/nonce/devtools/browser/browser-id"
    assert _validated_browser_endpoint(
        gateway,
        {"webSocketDebuggerUrl": endpoint},
    ) == endpoint

    with pytest.raises(ValueError, match="gateway"):
        _validated_browser_endpoint(
            gateway,
            {"webSocketDebuggerUrl": "ws://127.0.0.1:49999/devtools/browser/other"},
        )


def test_unbrowse_gateway_fails_closed_when_kuri_port_9222_is_busy():
    calls = []

    async def starter(*, upstream_http, headers, bind_port):
        calls.append((upstream_http, headers, bind_port))
        raise OSError("busy")

    with pytest.raises(RuntimeError, match="9222"):
        asyncio.run(
            start_unbrowse_gateway(
                upstream_http="http://manager/internal/cdp",
                headers={"X-CBM-Run": "opaque"},
                gateway_starter=starter,
            )
        )

    assert [call[2] for call in calls] == [9222]


def test_execute_claim_attaches_snaps_emits_typed_output_and_cleans_up():
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

    class FakeMCP:
        async def navigate(self, url):
            assert url == "https://example.com"
            return {"session_id": "unbrowse-session-1"}

        async def snap(self, session_id):
            assert session_id == "unbrowse-session-1"
            return {"page_title": "", "current_url": "https://example.com"}

        async def close(self):
            cleanup.append("mcp")

    async def mcp_factory(*, binary, browser_ws):
        assert binary == "unbrowse"
        assert browser_ws == "ws://127.0.0.1:45678/nonce"
        return FakeMCP()

    async def gateway_factory(**_kwargs):
        return FakeGateway(), "ws://127.0.0.1:45678/nonce"

    page_reads = []

    async def page_reader(browser_ws, expected_url):
        assert browser_ws == "ws://127.0.0.1:45678/nonce"
        assert expected_url == "https://example.com/"
        page_reads.append((browser_ws, expected_url))
        return (
            {"url": "https://example.com/", "title": "Example Domain"}
            if len(page_reads) > 1
            else {}
        )

    client = FakeClient()
    worker = UnbrowseWorker(
        client=client,
        unbrowse_bin="unbrowse",
        mcp_factory=mcp_factory,
        gateway_factory=gateway_factory,
        page_reader=page_reader,
    )

    result = asyncio.run(worker.execute_claim(claim_fixture()))

    assert result["status"] == "succeeded"
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
    assert client.outputs[2][1]["payload"] == {
        "title": "Example Domain",
        "url": "https://example.com/",
    }
    assert client.completed == ["run-unbrowse-1"]
    assert client.failed == []
    assert client.revoked == ["run-unbrowse-1"]
    assert cleanup == ["mcp", "gateway"]
    assert len(page_reads) == 2


def test_execute_claim_fails_closed_and_revokes_capability():
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

    async def bad_mcp(**_kwargs):
        raise RuntimeError("Bearer cbm_worker_secret failed")

    worker = UnbrowseWorker(client=FakeClient(), mcp_factory=bad_mcp)
    result = asyncio.run(worker.execute_claim(claim_fixture()))

    assert result == {"status": "failed", "error_code": "internal_error"}
    assert calls.completed == []
    assert calls.revoked == ["run-unbrowse-1"]
    assert len(calls.failed) == 1
    assert "cbm_worker_secret" not in calls.failed[0][1]["message"]
