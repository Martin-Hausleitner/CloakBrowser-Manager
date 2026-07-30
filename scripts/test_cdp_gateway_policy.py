from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientSession, WSMsgType, web

from scripts.unbrowse_managed_helper import (
    CDP_CLIENT_COMMAND_MAX_BYTES,
    CDP_DISCOVERY_MAX_BYTES,
    CDP_UPSTREAM_EVENT_MAX_BYTES,
    CDP_UPSTREAM_RESPONSE_MAX_BYTES,
    start_cdp_gateway,
)


class FakeCdpUpstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.ws: web.WebSocketResponse | None = None
        self.runner: web.AppRunner | None = None
        self.base_url = ""
        self.discovery_body: bytes | None = None

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/cdp/json/version", self.discovery)
        app.router.add_get("/cdp/devtools/browser/browser-id", self.websocket)
        app.router.add_get("/cdp/devtools/page/page-id", self.websocket)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        sockets = getattr(site, "_server").sockets
        port = int(sockets[0].getsockname()[1])
        self.base_url = f"http://127.0.0.1:{port}/cdp"
        return self.base_url

    async def cleanup(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def discovery(self, _request: web.Request) -> web.Response:
        body = (
            self.discovery_body
            or json.dumps(
                {
                    "webSocketDebuggerUrl": (
                        f"ws://127.0.0.1:{urlsplit(self.base_url).port}"
                        "/cdp/devtools/browser/browser-id"
                    )
                },
                separators=(",", ":"),
            ).encode()
        )
        return web.Response(body=body, content_type="application/json")

    async def websocket(self, _request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=CDP_UPSTREAM_RESPONSE_MAX_BYTES)
        await ws.prepare(_request)
        self.ws = ws
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                payload = json.loads(message.data)
                self.requests.append(payload)
                response = {
                    "id": payload.get("id"),
                    "result": {"ok": True, "method": payload.get("method")},
                }
                if payload.get("sessionId") is not None:
                    response["sessionId"] = payload.get("sessionId")
                await ws.send_str(
                    json.dumps(
                        response,
                        separators=(",", ":"),
                    )
                )
            elif message.type == WSMsgType.BINARY:
                self.requests.append({"binary": len(message.data)})
        return ws


async def with_gateway() -> tuple[FakeCdpUpstream, web.AppRunner, str]:
    upstream = FakeCdpUpstream()
    upstream_http = await upstream.start()
    runner, local_ws = await start_cdp_gateway(
        upstream_http=upstream_http,
        headers={"X-CBM-Run-Capability": "opaque"},
    )
    return upstream, runner, local_ws


async def receive_json(ws: Any) -> dict[str, Any]:
    message = await ws.receive(timeout=2)
    assert message.type == WSMsgType.TEXT
    payload = json.loads(message.data)
    assert isinstance(payload, dict)
    return payload


def test_gateway_allows_required_attach_and_action_methods_with_session_routing() -> (
    None
):
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_json({"id": 1, "method": "Target.getTargets"})
                    assert (await receive_json(ws))["result"][
                        "method"
                    ] == "Target.getTargets"

                    await ws.send_json(
                        {
                            "id": "call-2",
                            "sessionId": "session-1",
                            "method": "Runtime.evaluate",
                            "params": {"expression": "location.href"},
                        }
                    )
                    payload = await receive_json(ws)
                    assert payload["id"] == "call-2"
                    assert payload["sessionId"] == "session-1"
                    assert payload["result"]["method"] == "Runtime.evaluate"

            assert [item["method"] for item in upstream.requests] == [
                "Target.getTargets",
                "Runtime.evaluate",
            ]
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_denies_high_risk_methods_without_forwarding() -> None:
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_json(
                        {"id": 7, "method": "Browser.close", "params": {}}
                    )
                    payload = await receive_json(ws)
                    assert payload["id"] == 7
                    assert payload["error"]["code"] == -32000
                    assert "rejected" in payload["error"]["message"]
            assert upstream.requests == []
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_rejects_malformed_binary_and_batch_without_forwarding() -> None:
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_str('[{"id":1,"method":"Target.getTargets"}]')
                    assert (await ws.receive(timeout=2)).type in {
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSED,
                        WSMsgType.CLOSING,
                    }
                    assert ws.close_code == 1008

                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_bytes(b'{"id":2,"method":"Target.getTargets"}')
                    assert (await ws.receive(timeout=2)).type in {
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSED,
                        WSMsgType.CLOSING,
                    }
                    assert ws.close_code == 1008

                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_str("{not-json")
                    assert (await ws.receive(timeout=2)).type in {
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSED,
                        WSMsgType.CLOSING,
                    }
                    assert ws.close_code == 1008

            assert upstream.requests == []
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_closes_oversize_client_frame_without_forwarding() -> None:
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id",
                    max_msg_size=CDP_CLIENT_COMMAND_MAX_BYTES * 2,
                ) as ws:
                    oversized = {
                        "id": 3,
                        "method": "Runtime.evaluate",
                        "params": {"expression": "x" * CDP_CLIENT_COMMAND_MAX_BYTES},
                    }
                    await ws.send_str(json.dumps(oversized))
                    assert (await ws.receive(timeout=2)).type in {
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSED,
                        WSMsgType.CLOSING,
                    }
                    assert ws.close_code == 1009
            assert upstream.requests == []
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_enforces_upstream_event_and_response_caps() -> None:
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id",
                    max_msg_size=CDP_UPSTREAM_RESPONSE_MAX_BYTES + 1024,
                ) as ws:
                    await ws.send_json({"id": 10, "method": "Target.getTargets"})
                    assert (await receive_json(ws))["id"] == 10

                    assert upstream.ws is not None
                    await upstream.ws.send_str(
                        json.dumps(
                            {
                                "method": "Runtime.consoleAPICalled",
                                "params": {"blob": "x" * CDP_UPSTREAM_EVENT_MAX_BYTES},
                            },
                            separators=(",", ":"),
                        )
                    )
                    await upstream.ws.send_str(
                        json.dumps({"method": "Runtime.executionContextCreated"})
                    )
                    event = await receive_json(ws)
                    assert event["method"] == "Runtime.executionContextCreated"

                    await upstream.ws.send_str(
                        json.dumps(
                            {
                                "id": 11,
                                "result": {
                                    "blob": "x" * CDP_UPSTREAM_RESPONSE_MAX_BYTES
                                },
                            },
                            separators=(",", ":"),
                        )
                    )
                    payload = await receive_json(ws)
                    assert payload["id"] == 11
                    assert payload["error"]["code"] == -32000
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_drops_upstream_frames_with_invalid_id_or_session_id() -> None:
    async def scenario() -> None:
        upstream, runner, local_ws = await with_gateway()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"{local_ws}/devtools/browser/browser-id"
                ) as ws:
                    await ws.send_json({"id": 10, "method": "Target.getTargets"})
                    assert (await receive_json(ws))["id"] == 10

                    assert upstream.ws is not None
                    invalid_frames = [
                        {"id": -1, "result": {"bad": "negative-id"}},
                        {"id": "x" * 129, "result": {"bad": "overlong-id"}},
                        {
                            "id": 11,
                            "sessionId": "s" * 257,
                            "result": {"bad": "overlong-response-session"},
                        },
                        {
                            "method": "Runtime.consoleAPICalled",
                            "sessionId": "s" * 257,
                            "params": {"bad": "overlong-event-session"},
                        },
                    ]
                    for frame in invalid_frames:
                        await upstream.ws.send_str(
                            json.dumps(frame, separators=(",", ":"))
                        )
                    await upstream.ws.send_str(
                        json.dumps(
                            {"method": "Runtime.executionContextCreated"},
                            separators=(",", ":"),
                        )
                    )

                    payload = await receive_json(ws)
                    assert payload == {"method": "Runtime.executionContextCreated"}
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_caps_http_discovery_reads() -> None:
    async def scenario() -> None:
        upstream = FakeCdpUpstream()
        upstream_http = await upstream.start()
        upstream.discovery_body = (
            b'{"blob":"' + (b"x" * CDP_DISCOVERY_MAX_BYTES) + b'"}'
        )
        runner, local_ws = await start_cdp_gateway(
            upstream_http=upstream_http,
            headers={"X-CBM-Run-Capability": "opaque"},
        )
        try:
            parsed = urlsplit(local_ws)
            async with ClientSession() as session:
                async with session.get(
                    f"http://{parsed.netloc}/{parsed.path.lstrip('/')}/json/version"
                ) as response:
                    assert response.status == 502
        finally:
            await runner.cleanup()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_gateway_returns_502_for_invalid_discovery_json() -> None:
    async def scenario() -> None:
        for body in (b"\xff\xfe", b'{"broken"'):
            upstream = FakeCdpUpstream()
            upstream_http = await upstream.start()
            upstream.discovery_body = body
            runner, local_ws = await start_cdp_gateway(
                upstream_http=upstream_http,
                headers={"X-CBM-Run-Capability": "opaque"},
            )
            try:
                parsed = urlsplit(local_ws)
                async with ClientSession() as session:
                    async with session.get(
                        f"http://{parsed.netloc}/{parsed.path.lstrip('/')}/json/version"
                    ) as response:
                        assert response.status == 502
                        payload = await response.json()
                        assert "discovery" in payload["error"]
            finally:
                await runner.cleanup()
                await upstream.cleanup()

    asyncio.run(scenario())
