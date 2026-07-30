"""Loopback bridge for authenticated HTTP/HTTPS upstream proxies."""

from __future__ import annotations

import asyncio
import base64
import ssl
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import unquote, urlparse


def proxy_requires_bridge(proxy_url: str) -> bool:
    """Return true for authenticated HTTP(S) proxies and reject auth SOCKS."""
    parsed = urlparse(proxy_url)
    has_auth = parsed.username is not None or parsed.password is not None
    if not has_auth:
        return False
    if parsed.scheme == "socks5":
        raise ValueError("Authenticated SOCKS proxies are not supported")
    if parsed.scheme in ("http", "https"):
        return True
    return False


@dataclass
class ProxyBridge:
    """A local unauthenticated HTTP proxy that authenticates to one upstream."""

    _server: asyncio.AbstractServer | None
    _host: str
    _port: int
    _use_tls: bool
    _auth_header: bytes = field(repr=False)
    _client_writers: set[asyncio.StreamWriter] = field(default_factory=set)
    _upstream_writers: set[asyncio.StreamWriter] = field(default_factory=set)

    @classmethod
    async def start(cls, upstream_url: str) -> Self:
        parsed = urlparse(upstream_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Proxy bridge supports only http and https upstream proxies")
        if not parsed.hostname or not parsed.port:
            raise ValueError("Proxy URL missing hostname or port")
        if parsed.username is None and parsed.password is None:
            raise ValueError("Proxy bridge requires upstream proxy credentials")

        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        bridge = cls(
            _server=None,
            _host=parsed.hostname,
            _port=parsed.port,
            _use_tls=parsed.scheme == "https",
            _auth_header=f"Proxy-Authorization: Basic {token}\r\n".encode(),
        )
        bridge._server = await asyncio.start_server(
            bridge._handle_client,
            "127.0.0.1",
            0,
        )
        return bridge

    @property
    def port(self) -> int:
        assert self._server is not None
        sock = self._server.sockets[0]
        return int(sock.getsockname()[1])

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        writers = list(self._client_writers | self._upstream_writers)
        for writer in writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in writers),
            return_exceptions=True,
        )
        self._client_writers.clear()
        self._upstream_writers.clear()

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        self._client_writers.add(client_writer)
        try:
            initial = await client_reader.readuntil(b"\r\n\r\n")
            ssl_context = ssl.create_default_context() if self._use_tls else None
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._host,
                self._port,
                ssl=ssl_context,
                server_hostname=self._host if self._use_tls else None,
            )
            self._upstream_writers.add(upstream_writer)
            upstream_writer.write(self._with_proxy_authorization(initial))
            await upstream_writer.drain()

            await self._relay(client_reader, client_writer, upstream_reader, upstream_writer)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            OSError,
        ):
            pass
        finally:
            if upstream_writer is not None:
                self._upstream_writers.discard(upstream_writer)
                upstream_writer.close()
                await upstream_writer.wait_closed()
            self._client_writers.discard(client_writer)
            client_writer.close()
            await client_writer.wait_closed()

    def _with_proxy_authorization(self, request: bytes) -> bytes:
        head, separator, tail = request.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        kept_headers = [
            line
            for line in lines[1:]
            if not line.lower().startswith(b"proxy-authorization:")
        ]
        return (
            b"\r\n".join([lines[0], *kept_headers])
            + b"\r\n"
            + self._auth_header
            + separator
            + tail
        )

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        client_to_upstream = asyncio.create_task(
            self._copy_stream(client_reader, upstream_writer)
        )
        upstream_to_client = asyncio.create_task(
            self._copy_stream(upstream_reader, client_writer)
        )
        done, pending = await asyncio.wait(
            {client_to_upstream, upstream_to_client},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)

    async def _copy_stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while not reader.at_eof():
            data = await reader.read(64 * 1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
