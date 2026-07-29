from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.browser_use_worker import (
    BrowserUseWorker,
    ManagerClient,
    ManagerHTTPError,
    WorkerConfig,
    build_worker_config,
    decode_screenshot_payload,
    derive_browser_use_llm_timeout,
    detect_image_media_type,
    extract_screenshot_from_history,
    flatten_action_payload,
    main,
    sanitize_manager_error_message,
    sanitize_output_payload,
    select_llm_provider,
    validate_worker_token,
)


class FakeHTTPResponse:
    def __init__(self, status_code: int, data=None, content: bytes = b""):
        self.status_code = status_code
        self._data = data
        self.content = content
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


class FakeHTTPClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.responses:
            item = self.responses.pop(0)
            if callable(item):
                return item(method, url, kwargs)
            return item
        return FakeHTTPResponse(200, {})


PNG = b"\x89PNG\r\n\x1a\nfake"
JPEG = b"\xff\xd8\xff\xe0fakejpeg"


def _done_history(text: str = "ok"):
    return SimpleNamespace(
        final_result=lambda: text,
        is_done=lambda: True,
        is_successful=lambda: True,
        errors=lambda: [],
        has_errors=lambda: False,
        history=[],
    )


def _done_history_with_screenshots(*, paths=None, b64_shots=None, text: str = "ok"):
    hist = _done_history(text)
    path_list = list(paths or [])
    shot_list = list(b64_shots or [])
    hist.screenshot_paths = lambda n_last=None, return_none_if_not_screenshot=True: list(path_list)
    hist.screenshots = lambda n_last=None, return_none_if_not_screenshot=True: list(shot_list)
    return hist


def _max_steps_history():
    return SimpleNamespace(
        final_result=lambda: None,
        is_done=lambda: False,
        is_successful=lambda: None,
        errors=lambda: [None],
        has_errors=lambda: False,
        history=[],
    )


def _failed_history(message: str = "boom"):
    return SimpleNamespace(
        final_result=lambda: None,
        is_done=lambda: True,
        is_successful=lambda: False,
        errors=lambda: [message],
        has_errors=lambda: True,
        history=[],
    )


def test_manager_client_prefers_token_file_and_redacts_auth_in_errors(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("cbm_worker_file_secret\n", encoding="utf-8")
    monkeypatch.setenv("CBM_WORKER_TOKEN", "cbm_worker_env_secret")
    client = ManagerClient(
        "https://manager.local/base",
        token_file=token_file,
        http=FakeHTTPClient([FakeHTTPResponse(500, {"detail": "Bearer cbm_worker_file_secret"})]),
    )

    assert client.token == "cbm_worker_file_secret"
    with pytest.raises(RuntimeError) as exc:
        client.post("/internal/task-runs/claim")
    assert "cbm_worker_file_secret" not in str(exc.value)


def test_claim_204_returns_none_and_uses_worker_auth():
    http = FakeHTTPClient([FakeHTTPResponse(204)])
    client = ManagerClient("https://manager.local", token="cbm_worker_secret", http=http)

    assert client.claim() is None
    method, url, kwargs = http.calls[0]
    assert method == "POST"
    assert url == "https://manager.local/internal/task-runs/claim?harness=browser-use"
    assert kwargs["headers"]["Authorization"] == "Bearer cbm_worker_secret"


def test_claim_requests_filtered_harness_query():
    http = FakeHTTPClient([FakeHTTPResponse(200, {"id": "run-1", "harness": "browser-use"})])
    client = ManagerClient("https://manager.local/base", token="cbm_worker_secret", http=http)

    claim = client.claim()

    assert claim["id"] == "run-1"
    method, url, _kwargs = http.calls[0]
    assert method == "POST"
    assert url == "https://manager.local/base/internal/task-runs/claim?harness=browser-use"
    assert "claim?" in url
    assert "harness=browser-use" in url
    assert url.endswith("harness=browser-use") or "harness=browser-use" in url.split("?", 1)[1]


def test_validate_worker_token_rejects_empty_whitespace_and_nonprintable():
    assert validate_worker_token("cbm_worker_ok") == "cbm_worker_ok"
    for bad in ("", "  ", "cbm worker", "cbm\nworker", "cbm\x00worker"):
        with pytest.raises(ValueError):
            validate_worker_token(bad)


def test_build_worker_config_requires_url_and_valid_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CBM_MANAGER_URL", raising=False)
    monkeypatch.delenv("CBM_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("CBM_WORKER_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError):
        build_worker_config([])

    token_file = tmp_path / "tok"
    token_file.write_text("cbm_worker_valid_token\n", encoding="utf-8")
    cfg = build_worker_config(
        [
            "--manager-url",
            "https://manager.local",
            "--worker-id",
            "w1",
            "--token-file",
            str(token_file),
            "--poll-interval",
            "0.05",
        ]
    )
    assert cfg.manager_url == "https://manager.local"
    assert cfg.worker_id == "w1"
    assert cfg.poll_interval_seconds == 0.05
    assert cfg.token == "cbm_worker_valid_token"
    assert cfg.llm_provider == "cursor-agent"


def test_build_worker_config_accepts_explicit_llm_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("CBM_BROWSER_USE_LLM_PROVIDER", raising=False)
    token_file = tmp_path / "tok"
    token_file.write_text("cbm_worker_valid_token\n", encoding="utf-8")

    cfg = build_worker_config(
        [
            "--manager-url",
            "https://manager.local",
            "--token-file",
            str(token_file),
            "--llm-provider",
            "claude-cli",
        ]
    )

    assert cfg.llm_provider == "claude-cli"


def test_select_llm_provider_defaults_to_cursor_and_rejects_unknown():
    assert select_llm_provider(None) == "cursor-agent"
    assert select_llm_provider("") == "cursor-agent"
    assert select_llm_provider("default") == "cursor-agent"
    assert select_llm_provider("cursor-agent") == "cursor-agent"
    assert select_llm_provider("claude-cli") == "claude-cli"
    assert select_llm_provider("grok-cli") == "grok-cli"
    with pytest.raises(ValueError):
        select_llm_provider("openai")


def test_capability_headers_and_allowed_origins_are_passed_to_browser_use(monkeypatch):
    constructed = {}

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            constructed["browser"] = kwargs
            self.closed = False

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            self.closed = True
            constructed["stopped"] = True

        async def close(self):
            await self.stop()

    class FakeAgent:
        def __init__(self, **kwargs):
            constructed["agent"] = kwargs
            self.stopped = False
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history("ok")

        def stop(self):
            self.stopped = True

    fake_browser_use = SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent)
    monkeypatch.setitem(sys.modules, "browser_use", fake_browser_use)

    claim = {
        "id": "run-1",
        "task": "visit example",
        "harness": "browser-use",
        "allowed_origins": ["https://example.com"],
        "max_steps": 4,
        "timeout_seconds": 30,
        "model_alias": "safe",
    }
    cap = {
        "cdp_url": "/api/profiles/profile-1/cdp/json/version",
        "headers": {"Authorization": "Bearer cbm_run_secret"},
    }
    calls = []

    client = SimpleNamespace(
        issue_capability=lambda run_id: cap,
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: calls.append(("output", kwargs)) or {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: calls.append(("upload", args, kwargs)),
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda *args, **kwargs: None,
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local/root", worker_id="w")
    )

    result = asyncio.run(worker.execute_claim(claim))

    assert result["status"] == "succeeded"
    assert constructed["browser"] == {
        "cdp_url": "https://manager.local/api/profiles/profile-1/cdp/json/version",
        "headers": {"Authorization": "Bearer cbm_run_secret"},
        "allowed_domains": ["https://example.com"],
        "keep_alive": True,
    }
    assert constructed["agent"]["enable_signal_handler"] is False
    assert any(c[0] == "upload" for c in calls)
    assert constructed.get("stopped") is True


def test_derive_browser_use_llm_timeout_leaves_cleanup_margin():
    # Live bug: timeout_seconds=180 with no llm_timeout fell back to Browser Use's 75s.
    assert derive_browser_use_llm_timeout(180) == 150
    assert derive_browser_use_llm_timeout(180) < 180
    assert derive_browser_use_llm_timeout(60) == 45
    assert derive_browser_use_llm_timeout(60) < 60
    # Always strictly less than the manager/run budget when budget > 1.
    for budget in (20, 45, 90, 180, 300):
        llm_t = derive_browser_use_llm_timeout(budget)
        assert 1 <= llm_t < budget


def test_agent_gets_explicit_llm_timeout_while_cursor_keeps_run_timeout(monkeypatch):
    constructed = {}
    llm_kwargs = {}

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            constructed["agent"] = kwargs
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history("ok")

        def stop(self):
            return None

    class FakeLLM:
        def __init__(self, **kwargs):
            llm_kwargs.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda *args, **kwargs: None,
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    worker._llm_factory = FakeLLM
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-af3a",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 4,
                "timeout_seconds": 180,
                "model_alias": "safe",
            }
        )
    )
    assert result["status"] == "succeeded"
    assert llm_kwargs["timeout_seconds"] == 180
    assert constructed["agent"]["llm_timeout"] == derive_browser_use_llm_timeout(180)
    assert constructed["agent"]["llm_timeout"] == 150
    assert constructed["agent"]["llm_timeout"] < 180
    assert constructed["agent"]["flash_mode"] is True
    assert constructed["agent"]["use_judge"] is False
    assert constructed["agent"]["max_clickable_elements_length"] == 10000
    assert constructed["agent"]["llm_screenshot_size"] == (640, 480)
    assert constructed["agent"]["enable_signal_handler"] is False
    assert "use_vision" not in constructed["agent"] or constructed["agent"].get("use_vision") is not False


def test_agent_construction_sets_flash_judge_and_dom_cap(monkeypatch):
    constructed = {}

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            constructed["agent"] = dict(kwargs)
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history("ok")

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda *args, **kwargs: None,
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    asyncio.run(
        worker.execute_claim(
            {
                "id": "run-flash",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 2,
                "timeout_seconds": 90,
            }
        )
    )
    agent_kwargs = constructed["agent"]
    assert agent_kwargs["flash_mode"] is True
    assert agent_kwargs["use_judge"] is False
    assert agent_kwargs["max_clickable_elements_length"] == 10000
    assert agent_kwargs["llm_screenshot_size"] == (640, 480)
    assert agent_kwargs["llm_timeout"] == derive_browser_use_llm_timeout(90)
    assert agent_kwargs["enable_signal_handler"] is False
    # Vision / thinking not disabled by this latency patch.
    assert "use_vision" not in agent_kwargs or agent_kwargs.get("use_vision") is not False


def test_unrestricted_origins_pass_allowed_domains_none(monkeypatch):
    constructed = {}

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            constructed["browser"] = kwargs

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history("ok")

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda *args, **kwargs: None,
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    asyncio.run(
        worker.execute_claim(
            {
                "id": "run-unrestricted",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": [],
                "max_steps": 1,
                "timeout_seconds": 5,
            }
        )
    )
    assert constructed["browser"]["allowed_domains"] is None


def test_unsupported_harness_fails_safely_without_capability():
    calls = []
    client = SimpleNamespace(fail=lambda *args, **kwargs: calls.append((args, kwargs)))
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )

    result = asyncio.run(worker.execute_claim({"id": "run-2", "harness": "codex", "task": "x"}))

    assert result["status"] == "failed"
    assert calls[0][0][0] == "run-2"
    assert calls[0][1]["error_code"] == "internal_error"


def test_outputs_screenshot_complete_fail_and_cleanup_are_idempotent():
    calls = []

    class Client:
        def issue_capability(self, run_id):
            calls.append(("capability", run_id))
            return {"cdp_url": "http://cdp", "headers": {}}

        def output(self, run_id, *, kind, summary, payload, idempotency_key):
            calls.append(("output", kind, idempotency_key, payload))
            return {"id": "output-shot"}

        def upload_screenshot(self, run_id, output_id, body, media_type):
            calls.append(
                ("upload", run_id, output_id, hashlib.sha256(body).hexdigest(), media_type)
            )

        def complete(self, run_id):
            calls.append(("complete", run_id))
            return {"status": "succeeded"}

        def fail(self, run_id, *, error_code, message):
            calls.append(("fail", run_id, error_code, message))
            return {"status": "failed"}

        def revoke_capability(self, run_id):
            calls.append(("revoke", run_id))

    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )

    asyncio.run(worker.emit_output("run-3", "status", "Started", {"status": "running"}, "same"))
    asyncio.run(worker.emit_output("run-3", "status", "Started", {"status": "running"}, "same"))
    asyncio.run(worker.emit_screenshot("run-3", PNG, media_type="image/png", idempotency_key="shot"))
    asyncio.run(worker.complete("run-3", summary="ok"))
    asyncio.run(worker.fail("run-4", error_code="internal_error", message="Bearer cbm_run_secret"))
    worker.cleanup("run-3")

    assert [c for c in calls if c[0] == "output" and c[2] == "same"] == [
        ("output", "status", "same", {"status": "running"})
    ]
    assert ("output", "screenshot", "shot", {}) in calls
    assert ("output", "summary", "summary:run-3", {"text": "ok"}) in calls
    assert any(c[0] == "upload" and c[4] == "image/png" for c in calls)
    assert ("complete", "run-3") in calls
    fail_call = next(c for c in calls if c[0] == "fail")
    assert fail_call[2] == "internal_error"
    assert "cbm_run_secret" not in fail_call[3]
    assert ("revoke", "run-3") in calls


def test_heartbeat_loop_stops_agent_when_manager_requests_cancel():
    events = []

    class Client:
        def heartbeat(self, run_id):
            events.append(("heartbeat", run_id))
            return {"cancel_requested": True, "heartbeat_interval_seconds": 0.01}

    agent = SimpleNamespace(stop=lambda: events.append(("stop",)))
    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )

    cancel = asyncio.run(worker.heartbeat_once("run-5", agent))

    assert cancel is True
    assert events == [("heartbeat", "run-5"), ("stop",)]


def test_heartbeat_404_aborts_without_fail_or_complete(monkeypatch):
    events = []

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            events.append("session-stop")

        async def close(self):
            await self.stop()

    class FakeAgent:
        def __init__(self, **kwargs):
            self.stopped = False
            self.browser_session = kwargs.get("browser_session")
            self._llm = kwargs.get("llm")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            for _ in range(50):
                if self.stopped:
                    return _done_history("should-not-complete")
                await asyncio.sleep(0.01)
            return _done_history("late")

        def stop(self):
            self.stopped = True
            events.append("stop")
            cancel = getattr(self._llm, "cancel", None)
            if callable(cancel):
                cancel()

    class FakeLLM:
        def __init__(self, **kwargs):
            pass

        def cancel(self):
            events.append("llm-cancel")

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    heartbeats = {"n": 0}

    class Client:
        def issue_capability(self, run_id):
            return {"cdp_url": "http://cdp", "headers": {}}

        def heartbeat(self, run_id):
            heartbeats["n"] += 1
            events.append("heartbeat")
            if heartbeats["n"] >= 2:
                raise ManagerHTTPError("not found", status_code=404)
            return {"cancel_requested": False, "heartbeat_interval_seconds": 0.01}

        def output(self, *args, **kwargs):
            return {"id": "out"}

        def upload_screenshot(self, *args, **kwargs):
            return None

        def complete(self, run_id):
            events.append("complete")
            return {"status": "succeeded"}

        def fail(self, run_id, *, error_code, message):
            events.append(("fail", error_code))
            return {"status": "failed"}

        def revoke_capability(self, run_id):
            events.append("revoke")

    worker = BrowserUseWorker(
        client=Client(),
        config=WorkerConfig(
            manager_url="https://manager.local", worker_id="w", poll_interval_seconds=0.01
        ),
    )
    worker._llm_factory = FakeLLM
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-lost",
                "task": "slow",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 20,
                "timeout_seconds": 30,
                "model_alias": "safe",
            }
        )
    )
    assert result["status"] in {"cancelled", "aborted", "lost"}
    assert "stop" in events
    assert "complete" not in events
    assert not any(isinstance(e, tuple) and e[0] == "fail" for e in events)


def test_execute_claim_enforces_run_timeout(monkeypatch):
    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")
            self.stopped = False

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            await asyncio.sleep(1.0)
            return _done_history("late")

        def stop(self):
            self.stopped = True

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    fails = []

    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda run_id, **kwargs: fails.append(kwargs) or {"status": "failed"},
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-timeout",
                "task": "slow",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 5,
                "timeout_seconds": 0.05,
                "model_alias": "safe",
            }
        )
    )
    assert result["status"] == "failed"
    assert fails and fails[0]["error_code"] == "model_timeout"


def test_history_not_done_fails_max_steps(monkeypatch):
    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _max_steps_history()

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    fails = []
    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda run_id, **kwargs: fails.append(kwargs) or {"status": "failed"},
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-max",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 2,
                "timeout_seconds": 10,
            }
        )
    )
    assert result["status"] == "failed"
    assert fails[0]["error_code"] == "max_steps"


def test_history_unsuccessful_fails_internal_error(monkeypatch):
    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _failed_history("Bearer cbm_run_secret exploded")

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )
    fails = []
    client = SimpleNamespace(
        issue_capability=lambda run_id: {"cdp_url": "http://cdp", "headers": {}},
        heartbeat=lambda run_id: {"cancel_requested": False, "heartbeat_interval_seconds": 60},
        output=lambda *args, **kwargs: {"id": "out"},
        upload_screenshot=lambda *args, **kwargs: None,
        complete=lambda run_id: {"status": "succeeded"},
        fail=lambda run_id, **kwargs: fails.append(kwargs) or {"status": "failed"},
        revoke_capability=lambda run_id: None,
    )
    worker = BrowserUseWorker(
        client=client, config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-fail",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 2,
                "timeout_seconds": 10,
            }
        )
    )
    assert result["status"] == "failed"
    assert fails[0]["error_code"] == "internal_error"
    assert "cbm_run_secret" not in fails[0]["message"]


def test_flatten_action_and_decode_screenshot_helpers():
    nested = SimpleNamespace(model_dump=lambda **kw: {"navigate": {"url": "https://example.com"}})
    flat = flatten_action_payload(nested)
    assert flat["name"] == "navigate"
    assert flat["url"] == "https://example.com"

    raw_b64 = base64.b64encode(PNG).decode("ascii")
    assert decode_screenshot_payload(PNG).startswith(b"\x89PNG")
    assert decode_screenshot_payload(raw_b64).startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        decode_screenshot_payload("A" * (5 * 1024 * 1024 + 10))

    assert detect_image_media_type(PNG) == "image/png"
    assert detect_image_media_type(JPEG) == "image/jpeg"
    assert detect_image_media_type(b"not-an-image") is None


def test_extract_history_screenshot_prefers_newest_existing_path(tmp_path):
    older = tmp_path / "older.png"
    newer = tmp_path / "newer.jpg"
    older.write_bytes(PNG)
    newer.write_bytes(JPEG)
    missing = tmp_path / "gone.png"
    history = _done_history_with_screenshots(
        paths=[str(older), str(missing), str(newer)],
        b64_shots=[base64.b64encode(PNG).decode("ascii")],
    )
    body, media_type = extract_screenshot_from_history(history)
    assert media_type == "image/jpeg"
    assert body == JPEG
    assert body.startswith(b"\xff\xd8\xff")


def test_extract_history_screenshot_falls_back_to_base64_screenshots():
    history = _done_history_with_screenshots(
        paths=[None, "/no/such/file.png"],
        b64_shots=[None, base64.b64encode(PNG).decode("ascii")],
    )
    body, media_type = extract_screenshot_from_history(history)
    assert media_type == "image/png"
    assert body.startswith(b"\x89PNG")


def test_execute_claim_uploads_history_screenshot_when_session_capture_fails(
    tmp_path, monkeypatch
):
    shot_file = tmp_path / "hist.jpg"
    shot_file.write_bytes(JPEG)
    events: list[tuple] = []

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            raise RuntimeError("CDP reconnect failed")

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history_with_screenshots(
                paths=[str(shot_file)],
                text="summary-from-history",
            )

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )

    class Client:
        def issue_capability(self, run_id):
            return {"cdp_url": "http://cdp", "headers": {}}

        def heartbeat(self, run_id):
            return {"cancel_requested": False, "heartbeat_interval_seconds": 60}

        def output(self, run_id, *, kind, summary, payload, idempotency_key):
            events.append(("output", kind, idempotency_key))
            return {"id": f"out-{kind}"}

        def upload_screenshot(self, run_id, output_id, body, media_type):
            events.append(("upload", output_id, media_type, body[:3], len(body)))

        def complete(self, run_id):
            events.append(("complete", run_id))
            return {"status": "succeeded"}

        def fail(self, *args, **kwargs):
            events.append(("fail", args, kwargs))
            return {"status": "failed"}

        def revoke_capability(self, run_id):
            return None

    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-hist-shot",
                "task": "x",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 2,
                "timeout_seconds": 30,
            }
        )
    )
    assert result["status"] == "succeeded"
    assert ("output", "screenshot", "final-shot:run-hist-shot") in events
    shot_out_idx = next(
        i for i, e in enumerate(events) if e[0] == "output" and e[1] == "screenshot"
    )
    upload_idx = next(i for i, e in enumerate(events) if e[0] == "upload")
    complete_idx = next(i for i, e in enumerate(events) if e[0] == "complete")
    assert shot_out_idx < upload_idx < complete_idx
    upload = next(e for e in events if e[0] == "upload")
    assert upload[2] == "image/jpeg"
    assert upload[3] == b"\xff\xd8\xff"
    assert "fail" not in [e[0] for e in events]


def test_step_callbacks_emit_flattened_action_and_observation(monkeypatch):
    outputs = []

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return base64.b64encode(PNG).decode("ascii")

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")
            self._on_step = kwargs.get("register_new_step_callback")
            self.history = SimpleNamespace(
                history=[
                    SimpleNamespace(
                        result=[SimpleNamespace(extracted_content="saw title", error=None)]
                    )
                ]
            )

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            action = SimpleNamespace(
                model_dump=lambda **kw: {"go_to_url": {"url": "https://example.com"}}
            )
            model_output = SimpleNamespace(action=[action])
            if self._on_step:
                maybe = self._on_step(None, model_output, 1)
                if asyncio.iscoroutine(maybe):
                    await maybe
            if on_step_end:
                maybe = on_step_end(self)
                if asyncio.iscoroutine(maybe):
                    await maybe
            return _done_history("done")

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )

    class Client:
        def issue_capability(self, run_id):
            return {"cdp_url": "http://cdp", "headers": {}}

        def heartbeat(self, run_id):
            return {"cancel_requested": False, "heartbeat_interval_seconds": 60}

        def output(self, run_id, *, kind, summary, payload, idempotency_key):
            outputs.append((kind, idempotency_key, payload))
            return {"id": f"out-{kind}-{idempotency_key}"}

        def upload_screenshot(self, *args, **kwargs):
            return None

        def complete(self, run_id):
            return {"status": "succeeded"}

        def fail(self, *args, **kwargs):
            return {"status": "failed"}

        def revoke_capability(self, run_id):
            return None

    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    result = asyncio.run(
        worker.execute_claim(
            {
                "id": "run-stream",
                "task": "stream",
                "harness": "browser-use",
                "allowed_origins": ["https://example.com"],
                "max_steps": 3,
                "timeout_seconds": 10,
                "model_alias": "safe",
            }
        )
    )
    assert result["status"] == "succeeded"
    kinds = [k for k, _, _ in outputs]
    assert "action" in kinds
    assert "observation" in kinds
    assert "screenshot" in kinds
    assert "summary" in kinds
    action_payload = next(p for k, _, p in outputs if k == "action")
    assert action_payload["name"] == "go_to_url"
    assert action_payload["url"] == "https://example.com"
    summary_payload = next(p for k, _, p in outputs if k == "summary")
    assert summary_payload == {"text": "done"}


def test_run_forever_polls_claims_and_shuts_down_cleanly(monkeypatch):
    claims = [
        {
            "id": "run-loop",
            "task": "one",
            "harness": "browser-use",
            "allowed_origins": ["https://example.com"],
            "max_steps": 1,
            "timeout_seconds": 5,
            "model_alias": "safe",
        },
        None,
        None,
    ]
    executed = []

    class FakeBrowserSession:
        def __init__(self, **kwargs):
            pass

        async def take_screenshot(self, **kwargs):
            return PNG

        async def stop(self):
            return None

        async def close(self):
            return None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.browser_session = kwargs.get("browser_session")

        async def run(self, max_steps, on_step_start=None, on_step_end=None):
            return _done_history("ok")

        def stop(self):
            return None

    monkeypatch.setitem(
        sys.modules,
        "browser_use",
        SimpleNamespace(BrowserSession=FakeBrowserSession, Agent=FakeAgent),
    )

    class Client:
        def claim(self):
            return claims.pop(0) if claims else None

        def issue_capability(self, run_id):
            return {"cdp_url": "http://cdp", "headers": {}}

        def heartbeat(self, run_id):
            return {"cancel_requested": False, "heartbeat_interval_seconds": 60}

        def output(self, *args, **kwargs):
            return {"id": "out"}

        def upload_screenshot(self, *args, **kwargs):
            return None

        def complete(self, run_id):
            executed.append(run_id)
            return {"status": "succeeded"}

        def fail(self, *args, **kwargs):
            return {"status": "failed"}

        def revoke_capability(self, run_id):
            return None

    stop = asyncio.Event()
    worker = BrowserUseWorker(
        client=Client(),
        config=WorkerConfig(
            manager_url="https://manager.local", worker_id="w", poll_interval_seconds=0.01
        ),
    )

    async def drive():
        task = asyncio.create_task(worker.run_forever(stop_event=stop))
        for _ in range(50):
            if executed:
                stop.set()
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert executed == ["run-loop"]


def test_main_exits_nonzero_on_invalid_config(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["browser_use_worker.py", "--manager-url", "https://manager.local", "--worker-id", "w"],
    )
    monkeypatch.delenv("CBM_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("CBM_WORKER_TOKEN_FILE", raising=False)
    code = main()
    assert code != 0
    err = capsys.readouterr().err
    assert "token" in err.lower() or "invalid" in err.lower() or "required" in err.lower()
    assert "cbm_worker_" not in err


def test_manager_client_raises_typed_http_error():
    http = FakeHTTPClient([FakeHTTPResponse(404, {"detail": "Not found"})])
    client = ManagerClient("https://manager.local", token="cbm_worker_secret", http=http)
    with pytest.raises(ManagerHTTPError) as exc:
        client.heartbeat("run-x")
    assert exc.value.status_code == 404
    assert "cbm_worker_secret" not in str(exc.value)


def test_sanitize_manager_error_message_strips_bearer_and_authorization():
    dirty = "CDP failed: Bearer cbm_run_deadbeef Authorization: secret"
    clean = sanitize_manager_error_message(dirty)
    assert clean
    assert len(clean) <= 500
    lowered = clean.lower()
    assert "bearer " not in lowered
    assert "authorization" not in lowered
    assert "cbm_run_deadbeef" not in clean
    assert "secret" not in lowered


def test_worker_fail_uses_sterile_message_and_retries_once_on_422():
    messages: list[str] = []

    class Client:
        def fail(self, run_id, *, error_code, message):
            messages.append(message)
            if len(messages) == 1 and (
                "bearer " in message.lower() or "authorization" in message.lower()
            ):
                raise ManagerHTTPError("Invalid request", status_code=422)
            return {"status": "failed"}

    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    # Even if a buggy caller somehow bypassed sanitization upstream, worker.fail sanitizes.
    asyncio.run(
        worker.fail(
            "run-sterile",
            error_code="internal_error",
            message="CDP failed: Bearer cbm_run_deadbeef Authorization: secret",
        )
    )
    assert messages
    for msg in messages:
        lowered = msg.lower()
        assert "bearer " not in lowered
        assert "authorization" not in lowered
        assert "cbm_run_deadbeef" not in msg
        assert msg
        assert len(msg) <= 500


def test_manager_client_fail_sanitizes_and_retries_sterile_on_422():
    seen: list[str] = []

    def responder(method, url, kwargs):
        body = kwargs.get("json") or {}
        msg = body.get("message", "")
        seen.append(msg)
        if len(seen) == 1:
            # Simulate Manager rejecting a message that somehow still had a banned needle.
            return FakeHTTPResponse(422, {"detail": "Invalid request"})
        return FakeHTTPResponse(200, {"status": "failed"})

    http = FakeHTTPClient([responder, responder])
    client = ManagerClient("https://manager.local", token="cbm_worker_secret", http=http)
    client.fail(
        "run-1",
        error_code="internal_error",
        message="CDP failed: Bearer cbm_run_deadbeef Authorization: secret",
    )
    assert len(seen) == 2
    for msg in seen:
        lowered = msg.lower()
        assert "bearer " not in lowered
        assert "authorization" not in lowered
        assert "cbm_run_deadbeef" not in msg
    assert seen[1]  # sterile retry message


def test_emit_output_recursively_sanitizes_payload_strings():
    captured = []

    class Client:
        def output(self, run_id, *, kind, summary, payload, idempotency_key):
            captured.append(payload)
            return {"id": "out"}

    worker = BrowserUseWorker(
        client=Client(), config=WorkerConfig(manager_url="https://manager.local", worker_id="w")
    )
    dirty = {
        "name": "navigate",
        "url": "https://example.com",
        "nested": {
            "note": "Authorization: Bearer cbm_run_deadbeef",
            "items": ["token=secret", {"deep": "Bearer abcdefghijklmnop"}],
        },
    }
    asyncio.run(
        worker.emit_output("run-p", "action", "navigate", dirty, "act-1")
    )
    assert captured
    blob = json.dumps(captured[0])
    lowered = blob.lower()
    assert "cbm_run_deadbeef" not in blob
    assert "bearer " not in lowered
    assert "authorization" not in lowered
    assert "token=secret" not in blob
    # sanitize_output_payload helper itself
    cleaned = sanitize_output_payload(dirty)
    assert "cbm_run_deadbeef" not in json.dumps(cleaned)
