from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
from contextlib import suppress
from pathlib import Path
from typing import Any

from aiohttp import web
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.openai_compatible_toolloop import (
    DEFAULT_BASE_URL,
    OpenAICompatibleHTTPClient,
    ToolLoopError,
    run_openai_compatible_tool_loop,
)


def assistant_text(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_call(call_id: str, action: str = "inspect", arguments: dict[str, Any] | None = None):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "cbm_route_browser_action",
            "arguments": json.dumps({"action": action, "arguments": arguments or {}}),
        },
    }


def assistant_tools(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": list(calls)}}]}


class FakeClient:
    def __init__(self, *responses: dict[str, Any], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.timeouts: list[float] = []
        self.delay = delay

    async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.requests.append(payload)
        self.timeouts.append(timeout)
        if not self.responses:
            raise AssertionError("unexpected provider request")
        return self.responses.pop(0)


async def ok_router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "outcome": "completed",
        "classification": "success",
        "result": {"action": action, "arguments": arguments},
        "telemetry": {"secret": "cbm_worker_secret"},
    }


def run(coro):
    return asyncio.run(coro)


async def serve_once(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    assert sockets
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def test_valid_no_tool_completion_uses_bounded_schema_only() -> None:
    client = FakeClient(assistant_text("done"))

    result = run(
        run_openai_compatible_tool_loop(
            "open the page",
            ok_router,
            client=client,
            run_timeout_seconds=10,
        )
    )

    assert result.final_text == "done"
    assert result.tool_calls == 0
    request = client.requests[0]
    assert request["model"] == "grok-build-0.1"
    assert request["messages"][0]["role"] == "system"
    assert "browser action" in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "open the page"}
    assert len(request["tools"]) == 1
    schema = request["tools"][0]["function"]
    assert schema["name"] == "cbm_route_browser_action"
    action_schema = schema["parameters"]["properties"]["action"]
    assert action_schema["enum"] == ["inspect", "navigate", "click", "fill", "read_text"]
    serialized = json.dumps(request, sort_keys=True)
    assert "capability" not in serialized
    assert "profile" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized
    assert "proxy" not in serialized


def test_httpx_transport_posts_exact_path_ignores_proxy_env_and_rejects_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        seen: list[tuple[str, str]] = []
        app = web.Application()

        async def completions(request: web.Request) -> web.Response:
            seen.append((request.method, request.path))
            payload = await request.json()
            assert payload["model"] == "grok-build-0.1"
            return web.json_response(assistant_text("ok"))

        app.router.add_post("/v1/chat/completions", completions)
        runner, base_url = await serve_once(app)
        try:
            monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
            monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
            monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
            monkeypatch.setenv("NO_PROXY", "")
            result = await OpenAICompatibleHTTPClient(base_url).complete_chat(
                {"model": "grok-build-0.1", "messages": []},
                timeout=30,
            )
            assert result == assistant_text("ok")
            assert seen == [("POST", "/v1/chat/completions")]
        finally:
            await runner.cleanup()

        redirect_hits = 0
        redirected_hits = 0
        redirect_app = web.Application()

        async def redirect(request: web.Request) -> web.Response:
            nonlocal redirect_hits
            redirect_hits += 1
            raise web.HTTPFound("/redirected")

        async def redirected(request: web.Request) -> web.Response:
            nonlocal redirected_hits
            redirected_hits += 1
            return web.json_response(assistant_text("wrong"))

        redirect_app.router.add_post("/v1/chat/completions", redirect)
        redirect_app.router.add_get("/redirected", redirected)
        redirect_runner, redirect_base_url = await serve_once(redirect_app)
        try:
            with pytest.raises(ToolLoopError) as exc:
                await OpenAICompatibleHTTPClient(redirect_base_url).complete_chat({}, timeout=30)
            assert exc.value.code == "protocol_error"
            assert "redirect" in exc.value.reason
            assert redirect_hits == 1
            assert redirected_hits == 0
        finally:
            await redirect_runner.cleanup()

    run(scenario())


def test_httpx_transport_cancellation_closes_request_task() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cancelled_or_disconnected = asyncio.Event()
        app = web.Application()

        async def slow(request: web.Request) -> web.Response:
            started.set()
            with suppress(asyncio.CancelledError, ConnectionResetError):
                await asyncio.sleep(0.2)
            cancelled_or_disconnected.set()
            return web.json_response(assistant_text("late"))

        app.router.add_post("/v1/chat/completions", slow)
        runner, base_url = await serve_once(app)
        client = OpenAICompatibleHTTPClient(base_url)
        try:
            task = asyncio.create_task(client.complete_chat({"messages": []}, timeout=30))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.done()
            assert not [pending for pending in asyncio.all_tasks() if pending is task and not pending.done()]
        finally:
            await runner.cleanup()
        assert cancelled_or_disconnected.is_set()

    run(scenario())


def test_one_tool_call_adds_standard_assistant_and_tool_transcript() -> None:
    client = FakeClient(
        assistant_tools(tool_call("call_1", "navigate", {"url": "https://example.com"})),
        assistant_text("arrived"),
    )
    router_calls: list[tuple[str, dict[str, Any]]] = []

    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        router_calls.append((action, arguments))
        return {"ok": True, "outcome": "loaded", "classification": "success", "result": {"title": "Example"}}

    result = run(run_openai_compatible_tool_loop("go", router, client=client))

    assert result.final_text == "arrived"
    assert result.tool_calls == 1
    assert router_calls == [("navigate", {"url": "https://example.com"})]
    transcript = client.requests[1]["messages"]
    assert transcript[-2]["role"] == "assistant"
    assert transcript[-2]["tool_calls"][0]["id"] == "call_1"
    assert transcript[-1]["role"] == "tool"
    assert transcript[-1]["tool_call_id"] == "call_1"
    assert json.loads(transcript[-1]["content"])["tool_id"] == "call_1"


def test_multiple_sequential_tool_calls_preserve_ids() -> None:
    client = FakeClient(
        assistant_tools(tool_call("call_a", "inspect")),
        assistant_tools(tool_call("call_b", "read_text", {"selector": "main"})),
        assistant_text("summary"),
    )

    result = run(run_openai_compatible_tool_loop("inspect then read", ok_router, client=client))

    assert result.final_text == "summary"
    assert result.tool_calls == 2
    messages = client.requests[2]["messages"]
    assert [message.get("tool_call_id") for message in messages if message["role"] == "tool"] == [
        "call_a",
        "call_b",
    ]


def test_bounds_reject_too_many_assistant_tool_calls() -> None:
    client = FakeClient(assistant_tools(*(tool_call(f"call_{idx}") for idx in range(5))))

    with pytest.raises(ToolLoopError) as exc:
        run(run_openai_compatible_tool_loop("too many", ok_router, client=client))

    assert exc.value.code == "protocol_error"
    assert "too many tool calls" in exc.value.reason


def test_malformed_tool_calls_are_protocol_errors() -> None:
    client = FakeClient(assistant_tools({"id": "bad", "type": "function", "function": {"name": "wrong"}}))

    with pytest.raises(ToolLoopError) as exc:
        run(run_openai_compatible_tool_loop("bad", ok_router, client=client))

    assert exc.value.code == "protocol_error"
    assert "unknown tool" in exc.value.reason


def test_present_falsy_tool_calls_are_rejected() -> None:
    for malformed in [{}, "", 0, False]:
        response = {"choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": malformed}}]}
        with pytest.raises(ToolLoopError) as exc:
            run(run_openai_compatible_tool_loop("bad tool_calls", ok_router, client=FakeClient(response)))
        assert exc.value.code == "protocol_error"
        assert "tool_calls" in exc.value.reason


def test_invalid_tool_arguments_are_protocol_errors() -> None:
    invalid_json = {
        "id": "bad_json",
        "type": "function",
        "function": {"name": "cbm_route_browser_action", "arguments": "not json"},
    }
    non_object = {
        "id": "non_object",
        "type": "function",
        "function": {"name": "cbm_route_browser_action", "arguments": "[]"},
    }
    bad_action = tool_call("bad_action", "screenshot")

    for call, expected in [
        (invalid_json, "not valid JSON"),
        (non_object, "must be an object"),
        (bad_action, "disallowed browser action"),
    ]:
        with pytest.raises(ToolLoopError) as exc:
            run(run_openai_compatible_tool_loop("bad args", ok_router, client=FakeClient(assistant_tools(call))))
        assert exc.value.code == "protocol_error"
        assert expected in exc.value.reason


def test_no_choice_and_empty_final_are_protocol_errors() -> None:
    for response, expected in [
        ({}, "no choices"),
        ({"choices": []}, "no choices"),
        (assistant_text(""), "empty"),
    ]:
        with pytest.raises(ToolLoopError) as exc:
            run(run_openai_compatible_tool_loop("bad response", ok_router, client=FakeClient(response)))
        assert exc.value.code == "protocol_error"
        assert expected in exc.value.reason


def test_duplicate_tool_call_ids_are_rejected() -> None:
    client = FakeClient(
        assistant_tools(tool_call("same")),
        assistant_tools(tool_call("same")),
    )

    with pytest.raises(ToolLoopError) as exc:
        run(run_openai_compatible_tool_loop("dupe", ok_router, client=client))

    assert exc.value.code == "protocol_error"
    assert "duplicate" in exc.value.reason


def test_tool_call_ids_and_echoed_envelopes_are_bounded() -> None:
    for call_id in ["x" * 129, "bad id", "bad/id", ""]:
        with pytest.raises(ToolLoopError) as exc:
            run(run_openai_compatible_tool_loop("bad id", ok_router, client=FakeClient(assistant_tools(tool_call(call_id)))))
        assert exc.value.code == "protocol_error"
        assert "tool call id" in exc.value.reason

    oversized = tool_call("call_safe")
    oversized["provider_internal"] = "x" * 20_000
    oversized["function"]["internal_routing"] = "secret"
    client = FakeClient(assistant_tools(oversized), assistant_text("ok"))

    run(run_openai_compatible_tool_loop("bounded echo", ok_router, client=client))

    echoed = json.dumps(client.requests[1]["messages"][-2]["tool_calls"], sort_keys=True)
    assert "provider_internal" not in echoed
    assert "internal_routing" not in echoed
    assert len(echoed) < 9_000
    json.loads(client.requests[1]["messages"][-1]["content"])


def test_terminal_router_classification_stops_with_redacted_error() -> None:
    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "classification": "auth_required",
            "message": "Bearer cbm_worker_secret cannot login",
            "result": {"token": "cbm_worker_secret"},
        }

    client = FakeClient(assistant_tools(tool_call("call_1")))

    with pytest.raises(ToolLoopError) as exc:
        run(run_openai_compatible_tool_loop("auth", router, client=client))

    assert exc.value.code == "auth_required"
    assert exc.value.classification == "auth_required"
    assert "cbm_worker_secret" not in exc.value.reason
    assert len(client.requests) == 1


def test_eligible_router_failure_continues_to_model_bounded() -> None:
    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "classification": "not_found",
            "message": "selector missing",
            "result": {"debug": "x" * 5000},
        }

    client = FakeClient(assistant_tools(tool_call("call_1", "click", {"selector": "#x"})), assistant_text("fallback"))

    result = run(run_openai_compatible_tool_loop("click", router, client=client))

    assert result.final_text == "fallback"
    content = client.requests[1]["messages"][-1]["content"]
    payload = json.loads(content)
    assert payload["ok"] is False
    assert payload["classification"] == "not_found"
    assert len(content) < 5000


def test_secret_redaction_filters_provider_visible_tool_results() -> None:
    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "classification": "success",
            "message": "Bearer cbm_worker_secret",
            "result": {"password": "secret-value", "safe": "ok"},
            "headers": {"authorization": "Bearer cbm_worker_secret"},
        }

    client = FakeClient(assistant_tools(tool_call("call_1")), assistant_text("safe"))

    run(run_openai_compatible_tool_loop("redact", router, client=client))

    visible = client.requests[1]["messages"][-1]["content"]
    assert "cbm_worker_secret" not in visible
    assert "secret-value" not in visible
    assert "authorization" not in visible
    assert json.loads(visible)["result"]["safe"] == "ok"


def test_nested_secret_keys_and_url_credentials_are_removed_from_tool_results() -> None:
    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "classification": "success",
            "message": "loaded https://user:pass@example.com/?token=abc&ok=1&x-api-key=hidden",
            "result": {
                "safe": "ok",
                "nested": {
                    "access_token": "tok_secret",
                    "x-api-key": "key_secret",
                    "profile_id": "profile_secret",
                    "sessionLease": "lease_secret",
                    "cdp_url": "ws://user:pass@127.0.0.1/devtools?auth=secret&safe=1",
                    "url": "https://user:pass@example.com/path?password=secret&visible=1",
                },
            },
            "telemetry": {"routing_contract": "internal", "duration_ms": 10},
        }

    client = FakeClient(assistant_tools(tool_call("call_1")), assistant_text("safe"))

    run(run_openai_compatible_tool_loop("deep redact", router, client=client))

    visible = client.requests[1]["messages"][-1]["content"]
    payload = json.loads(visible)
    assert payload["result"]["safe"] == "ok"
    assert "tok_secret" not in visible
    assert "key_secret" not in visible
    assert "profile_secret" not in visible
    assert "lease_secret" not in visible
    assert "routing_contract" not in visible
    assert "user:pass" not in visible
    assert "password=secret" not in visible
    assert "x-api-key=hidden" not in visible
    assert "visible=1" not in visible
    assert "visible=%5BREDACTED%5D" in visible


def test_all_query_values_and_nested_encoded_urls_are_redacted() -> None:
    encoded_next = (
        "https%3A%2F%2Fuser%3Apass%40inner.example%2Fcallback%3Ftoken%3Dabc%26q%3Dsecret"
    )

    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "classification": "success",
            "result": {
                "landing": (
                    "https://outer.example/path?"
                    f"q=search&url={encoded_next}&target=https%3A%2F%2Ftarget.example%2F%3Fkey%3Dhidden"
                    "&next=/done"
                ),
            },
        }

    client = FakeClient(assistant_tools(tool_call("call_1")), assistant_text("safe"))

    run(run_openai_compatible_tool_loop("nested url", router, client=client))

    visible = client.requests[1]["messages"][-1]["content"]
    assert "search" not in visible
    assert "user:pass" not in visible
    assert "token%3Dabc" not in visible
    assert "key%3Dhidden" not in visible
    assert "next=%5BREDACTED%5D" in visible
    assert "q=%5BREDACTED%5D" in visible
    assert "url=%5BREDACTED%5D" in visible
    assert "target=%5BREDACTED%5D" in visible


def test_url_fragments_are_not_provider_visible() -> None:
    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "classification": "success",
            "result": {
                "oauth": "https://auth.example/callback#code=oauth_secret&state=csrf",
                "mixed": "https://app.example/path?next=/home#tab=profile&token=fragment_secret",
            },
        }

    client = FakeClient(assistant_tools(tool_call("call_1")), assistant_text("safe"))

    run(run_openai_compatible_tool_loop("fragment redact", router, client=client))

    visible = client.requests[1]["messages"][-1]["content"]
    assert "oauth_secret" not in visible
    assert "csrf" not in visible
    assert "profile" not in visible
    assert "fragment_secret" not in visible
    assert "#code=" not in visible
    assert "#tab=" not in visible


def test_malformed_url_ports_are_safe_in_config_and_redaction() -> None:
    with pytest.raises(ToolLoopError) as config_exc:
        OpenAICompatibleHTTPClient("http://127.0.0.1:notaport")
    assert config_exc.value.code == "configuration_error"
    assert "notaport" not in config_exc.value.reason

    async def router(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "classification": "success",
            "result": {"url": "http://user:pass@127.0.0.1:notaport/path?token=secret"},
        }

    client = FakeClient(assistant_tools(tool_call("call_1")), assistant_text("safe"))

    run(run_openai_compatible_tool_loop("malformed url", router, client=client))

    visible = client.requests[1]["messages"][-1]["content"]
    assert "user:pass" not in visible
    assert "token=secret" not in visible
    assert "notaport" not in visible
    assert "[REDACTED_URL]" in visible


def test_base_url_must_be_loopback_http() -> None:
    assert OpenAICompatibleHTTPClient(DEFAULT_BASE_URL).base_url == DEFAULT_BASE_URL
    for url in ["https://127.0.0.1:8317", "http://localhost:8317", "http://10.0.0.1:8317"]:
        with pytest.raises(ToolLoopError) as exc:
            OpenAICompatibleHTTPClient(url)
        assert exc.value.code == "configuration_error"


def test_http_status_mapping_and_body_cap() -> None:
    class AuthClient:
        async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            raise urllib.error.HTTPError(DEFAULT_BASE_URL, 401, "unauthorized", {}, None)

    with pytest.raises(ToolLoopError) as exc:
        run(run_openai_compatible_tool_loop("auth", ok_router, client=AuthClient()))
    assert exc.value.code == "auth_required"

    class BigBodyClient:
        async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            raise ToolLoopError("protocol_error", "response body too large")

    with pytest.raises(ToolLoopError) as big_exc:
        run(run_openai_compatible_tool_loop("big", ok_router, client=BigBodyClient()))
    assert big_exc.value.code == "protocol_error"
    assert "too large" in big_exc.value.reason


def test_5xx_timeout_and_cancellation_mapping() -> None:
    class ServerErrorClient:
        async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            raise urllib.error.HTTPError(DEFAULT_BASE_URL, 503, "unavailable", {}, None)

    class TimeoutClient:
        async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
            assert timeout <= 30
            raise TimeoutError("provider call timeout")

    with pytest.raises(ToolLoopError) as server_exc:
        run(run_openai_compatible_tool_loop("down", ok_router, client=ServerErrorClient()))
    assert server_exc.value.code == "proxy_unavailable"

    with pytest.raises(ToolLoopError) as http_timeout_exc:
        run(run_openai_compatible_tool_loop("http timeout", ok_router, client=TimeoutClient()))
    assert http_timeout_exc.value.code == "timeout"

    client = FakeClient(assistant_text("late"), delay=0.05)
    with pytest.raises(ToolLoopError) as timeout_exc:
        run(
            run_openai_compatible_tool_loop(
                "timeout", ok_router, client=client, run_timeout_seconds=0.001
            )
        )
    assert timeout_exc.value.code == "timeout"

    async def cancel_run() -> None:
        task = asyncio.create_task(
            run_openai_compatible_tool_loop(
                "cancel", ok_router, client=FakeClient(assistant_text("late"), delay=1)
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancel_run())
