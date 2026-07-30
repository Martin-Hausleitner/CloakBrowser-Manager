"""Tests for the authenticated upstream proxy loopback bridge."""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlparse

import pytest

from backend.proxy_bridge import ProxyBridge, proxy_requires_bridge


class _RecordingUpstream:
    def __init__(self) -> None:
        self.requests: asyncio.Queue[bytes] = asyncio.Queue()
        self._server: asyncio.AbstractServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        sock = self._server.sockets[0]
        host, port = sock.getsockname()[:2]
        return f"http://proxy-user:top-secret@{host}:{port}"

    async def __aenter__(self) -> "_RecordingUpstream":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            data = await reader.readuntil(b"\r\n\r\n")
            await self.requests.put(data)
            if data.startswith(b"CONNECT "):
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
                )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def _read_headers(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)


@pytest.mark.asyncio
async def test_bridge_injects_proxy_authorization_for_connect_without_exposing_credentials():
    async with _RecordingUpstream() as upstream:
        bridge = await ProxyBridge.start(upstream.url)
        try:
            local_url = bridge.proxy_url
            assert "proxy-user" not in local_url
            assert "top-secret" not in local_url
            assert urlparse(local_url).hostname == "127.0.0.1"

            reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
            writer.write(
                b"CONNECT example.test:443 HTTP/1.1\r\n"
                b"Host: example.test:443\r\n\r\n"
            )
            await writer.drain()

            response = await _read_headers(reader)
            assert response.startswith(b"HTTP/1.1 200")
            writer.close()
            await writer.wait_closed()

            request = await asyncio.wait_for(upstream.requests.get(), timeout=2)
            expected_auth = base64.b64encode(b"proxy-user:top-secret").decode()
            assert f"Proxy-Authorization: Basic {expected_auth}\r\n".encode() in request
            assert b"top-secret" not in request.split(b"\r\n\r\n", 1)[1]
        finally:
            await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_injects_proxy_authorization_for_absolute_form_http_requests():
    async with _RecordingUpstream() as upstream:
        bridge = await ProxyBridge.start(upstream.url)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
            writer.write(
                b"GET http://example.test/path HTTP/1.1\r\n"
                b"Host: example.test\r\n"
                b"Proxy-Authorization: Basic stale\r\n\r\n"
            )
            await writer.drain()

            response = await asyncio.wait_for(reader.read(), timeout=2)
            assert response.endswith(b"\r\n\r\nOK")
            writer.close()
            await writer.wait_closed()

            request = await asyncio.wait_for(upstream.requests.get(), timeout=2)
            expected_auth = base64.b64encode(b"proxy-user:top-secret").decode()
            assert request.count(b"Proxy-Authorization:") == 1
            assert f"Proxy-Authorization: Basic {expected_auth}\r\n".encode() in request
            assert b"Proxy-Authorization: Basic stale" not in request
        finally:
            await bridge.stop()


def test_authenticated_socks_proxy_is_not_bridgeable_until_secure_support_exists():
    with pytest.raises(ValueError, match="Authenticated SOCKS proxies are not supported"):
        proxy_requires_bridge("socks5://proxy-user:top-secret@127.0.0.1:1080")


def test_unauthenticated_proxy_does_not_require_bridge():
    assert proxy_requires_bridge("http://127.0.0.1:8080") is False
