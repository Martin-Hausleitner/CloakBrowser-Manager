#!/usr/bin/env python3
"""Authenticated VCVM worker for Manager-owned Unbrowse task runs.

The worker claims only ``unbrowse`` runs and never launches a second browser
intentionally. A nonce-protected loopback relay exposes the Manager's
short-lived CDP capability to one persistent Unbrowse MCP stdio process, so
``navigate`` and ``snap`` share the same in-process Unbrowse session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from aiohttp import ClientSession, ClientTimeout

from scripts.browser_use_worker import ManagerClient, ManagerHTTPError, redact_text
from scripts.unbrowse_managed_helper import (
    normalized_navigation_url,
    normalized_origin,
    start_cdp_gateway,
)


logger = logging.getLogger(__name__)
HARNESS = "unbrowse"
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
MCP_PROTOCOL_VERSION = "2025-03-26"
MAX_MCP_LINE_BYTES = 1_048_576

GatewayFactory = Callable[..., Awaitable[tuple[Any, str]]]
MCPFactory = Callable[..., Awaitable[Any]]
TitleReader = Callable[[str, str], Awaitable[str]]


class RunCancelled(RuntimeError):
    """The Manager asked the worker to release the browser lease."""


class UnbrowseMCPError(RuntimeError):
    """Sterile/redacted Unbrowse MCP protocol or tool failure."""

    def __init__(self, message: str) -> None:
        super().__init__(redact_text(message))


class UnbrowseClient(ManagerClient):
    """Manager client whose claim endpoint is hard-bound to Unbrowse."""

    def claim(self) -> dict[str, Any] | None:
        path = f"/internal/task-runs/claim?{urlencode({'harness': HARNESS})}"
        response = self.request("POST", path)
        if response.status_code == 204:
            return None
        return response.json()


def select_task_target(claim: dict[str, Any]) -> str:
    """Return the first explicit task URL whose origin is run-allowlisted."""
    allowed = {
        normalized_origin(str(item))
        for item in claim.get("allowed_origins") or []
    }
    if not allowed:
        raise ValueError("Unbrowse run requires an allowed origin")
    urls = [item.rstrip(".,);]") for item in URL_RE.findall(str(claim.get("task") or ""))]
    if not urls:
        raise ValueError("Unbrowse task requires an explicit URL")
    for url in urls:
        if normalized_origin(url) in allowed:
            return url
    raise ValueError("Unbrowse task URL is outside the allowed origin")


def _snapshot_title(snapshot: dict[str, Any]) -> str:
    candidates = [
        snapshot.get("page_title"),
        snapshot.get("title"),
        (snapshot.get("page") or {}).get("title")
        if isinstance(snapshot.get("page"), dict)
        else None,
        (snapshot.get("data") or {}).get("title")
        if isinstance(snapshot.get("data"), dict)
        else None,
    ]
    for value in candidates:
        text = redact_text(str(value or "")).strip()
        if text:
            return text[:200]
    return "Unbrowse opened the managed page"


def _http_discovery_url(browser_ws: str) -> str:
    parsed = urlsplit(browser_ws)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError("Unbrowse browser relay must be an absolute WebSocket URL")
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


async def read_cdp_title(browser_ws: str, expected_url: str) -> str:
    discovery = f"{_http_discovery_url(browser_ws).rstrip('/')}/json/list"
    async with ClientSession(timeout=ClientTimeout(total=5)) as session:
        async with session.get(discovery) as response:
            if response.status != 200:
                return ""
            raw = await response.read()
    if len(raw) > MAX_MCP_LINE_BYTES:
        return ""
    try:
        targets = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(targets, list):
        return ""
    expected_origin = normalized_origin(expected_url)
    for target in reversed(targets):
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        try:
            if normalized_origin(str(target.get("url") or "")) != expected_origin:
                continue
        except ValueError:
            continue
        title = redact_text(str(target.get("title") or "")).strip()
        if title:
            return title[:200]
    return ""


def decode_mcp_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Decode structuredContent or a JSON text block from a tools/call result."""
    if result.get("isError") is True:
        detail = "Unbrowse MCP tool failed"
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                detail = str(item["text"])
                break
        raise UnbrowseMCPError(detail)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


class UnbrowseMCPClient:
    """Minimal newline-framed MCP client for one persistent Unbrowse process."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._next_id = 0

    @classmethod
    async def start(cls, *, binary: str, browser_ws: str) -> "UnbrowseMCPClient":
        discovery_url = _http_discovery_url(browser_ws)
        env = dict(os.environ)
        env.update(
            {
                "KURI_ATTACH_EXISTING_CHROME": "1",
                "KURI_DISABLE_CDP_ATTACH": "0",
                "PUPPETEER_BROWSER_WS_ENDPOINT": browser_ws,
                "CHROME_DEBUG_URL": discovery_url,
                "PLAYWRIGHT_CHROMIUM_REMOTE_DEBUGGING_URL": discovery_url,
                "UNBROWSE_NON_INTERACTIVE": "1",
                "UNBROWSE_SKIP_TOS_CHECK": "1",
                "UNBROWSE_SKIP_DAEMON_PROBE": "1",
                "UNBROWSE_NO_AUTO_UPDATE": "1",
                "UNBROWSE_DISABLE_AUTH_FALLBACK": "1",
                "UNBROWSE_NO_VISIBLE_FALLBACK": "1",
            }
        )
        process = await asyncio.create_subprocess_exec(
            binary,
            "mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=MAX_MCP_LINE_BYTES + 1,
        )
        client = cls(process)
        response = await client._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cloakbrowser-unbrowse-worker", "version": "1"},
            },
            timeout=15,
        )
        negotiated = str((response.get("result") or {}).get("protocolVersion") or "")
        if negotiated != MCP_PROTOCOL_VERSION:
            await client.close()
            raise UnbrowseMCPError("Unbrowse MCP protocol version mismatch")
        await client._notify("notifications/initialized", {})
        return client

    async def _write(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.returncode is not None:
            raise UnbrowseMCPError("Unbrowse MCP process is not running")
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(line) > MAX_MCP_LINE_BYTES:
            raise UnbrowseMCPError("Unbrowse MCP request exceeds size bound")
        self.process.stdin.write(line + b"\n")
        await self.process.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        if self.process.stdout is None:
            raise UnbrowseMCPError("Unbrowse MCP stdout is unavailable")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise UnbrowseMCPError("Unbrowse MCP request timed out")
            try:
                raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise UnbrowseMCPError("Unbrowse MCP request timed out") from exc
            if not raw:
                raise UnbrowseMCPError("Unbrowse MCP process closed stdout")
            line = raw.rstrip(b"\r\n")
            if len(line) > MAX_MCP_LINE_BYTES:
                raise UnbrowseMCPError("Unbrowse MCP response exceeds size bound")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("error"):
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise UnbrowseMCPError(f"Unbrowse MCP error: {detail}")
            return message

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        response = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise UnbrowseMCPError("Unbrowse MCP tool result is malformed")
        return decode_mcp_tool_result(result)

    async def navigate(self, url: str) -> dict[str, Any]:
        return await self._call_tool(
            "unbrowse_breath_navigate",
            {"url": url},
            timeout=90,
        )

    async def snap(self, session_id: str) -> dict[str, Any]:
        return await self._call_tool(
            "unbrowse_eval_snap",
            {"session_id": session_id, "detail_level": "summary"},
            timeout=60,
        )

    async def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


async def start_unbrowse_mcp(*, binary: str, browser_ws: str) -> UnbrowseMCPClient:
    return await UnbrowseMCPClient.start(binary=binary, browser_ws=browser_ws)


@dataclass
class UnbrowseWorker:
    client: Any
    unbrowse_bin: str = "unbrowse"
    poll_interval_seconds: float = 2.0
    mcp_factory: MCPFactory = field(default=start_unbrowse_mcp, repr=False)
    gateway_factory: GatewayFactory = field(default=start_cdp_gateway, repr=False)
    title_reader: TitleReader = field(default=read_cdp_title, repr=False)

    async def _heartbeat(self, run_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                body = await asyncio.to_thread(self.client.heartbeat, run_id)
            except ManagerHTTPError as exc:
                if exc.status_code in {404, 409, 410}:
                    stop.set()
                    return
                raise
            if body.get("cancel_requested"):
                stop.set()
                return
            delay = max(0.2, min(float(body.get("heartbeat_interval_seconds") or 5), 15.0))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _await_guarded(
        self,
        awaitable: Awaitable[dict[str, Any]],
        *,
        cancelled: asyncio.Event,
    ) -> dict[str, Any]:
        operation = asyncio.create_task(awaitable)
        cancel_task = asyncio.create_task(cancelled.wait())
        done, _pending = await asyncio.wait(
            {operation, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done and cancelled.is_set():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise RunCancelled("run cancelled")
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)
        return await operation

    async def execute_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        run_id = str(claim.get("id") or "")
        if not run_id or claim.get("harness") != HARNESS:
            return {"status": "failed", "error_code": "internal_error"}

        capability_issued = False
        cancelled = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        gateway_runner: Any | None = None
        mcp: Any | None = None
        try:
            target = select_task_target(claim)
            capability = await asyncio.to_thread(self.client.issue_capability, run_id)
            capability_issued = True
            upstream_http = urljoin(
                self.client.base_url.rstrip("/") + "/",
                str(capability.get("cdp_url") or ""),
            )
            headers = {
                str(key): str(value)
                for key, value in dict(capability.get("headers") or {}).items()
            }
            heartbeat = asyncio.create_task(self._heartbeat(run_id, cancelled))
            gateway_runner, local_ws = await self.gateway_factory(
                upstream_http=upstream_http, headers=headers
            )
            mcp = await self.mcp_factory(binary=self.unbrowse_bin, browser_ws=local_ws)
            opened = await self._await_guarded(mcp.navigate(target), cancelled=cancelled)
            session_id = str(opened.get("session_id") or opened.get("sessionId") or "")
            if not session_id:
                raise RuntimeError("Unbrowse did not return a session")
            safe_url = normalized_navigation_url(target)
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="action",
                summary="navigate",
                payload={"name": "navigate", "url": safe_url, "step": 1},
                idempotency_key=f"unbrowse:{run_id}:navigate",
            )
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="status",
                summary="Unbrowse attached to the managed profile",
                payload={"status": "running", "progress": 40},
                idempotency_key=f"unbrowse:{run_id}:attached",
            )
            snapshot = await self._await_guarded(
                mcp.snap(session_id), cancelled=cancelled
            )
            title = _snapshot_title(snapshot)
            if title == "Unbrowse opened the managed page":
                observed_title = await self.title_reader(local_ws, safe_url)
                title = observed_title or title
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="observation",
                summary=title,
                payload={"title": title, "url": safe_url},
                idempotency_key=f"unbrowse:{run_id}:snapshot",
            )
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="summary",
                summary=title,
                payload={"status": "succeeded", "text": title},
                idempotency_key=f"unbrowse:{run_id}:summary",
            )
            await asyncio.to_thread(self.client.complete, run_id)
            return {"status": "succeeded", "title": title}
        except RunCancelled:
            return {"status": "cancelled"}
        except Exception as exc:  # noqa: BLE001 — redacted Manager boundary
            try:
                await asyncio.to_thread(
                    self.client.fail,
                    run_id,
                    error_code="internal_error",
                    message=redact_text(str(exc)),
                )
            except Exception:
                logger.exception("failed to report Unbrowse run failure")
            return {"status": "failed", "error_code": "internal_error"}
        finally:
            cancelled.set()
            if heartbeat is not None:
                heartbeat.cancel()
            if mcp is not None:
                await mcp.close()
            if gateway_runner is not None:
                await gateway_runner.cleanup()
            if heartbeat is not None:
                await asyncio.gather(heartbeat, return_exceptions=True)
            if capability_issued:
                try:
                    await asyncio.to_thread(self.client.revoke_capability, run_id)
                except Exception:
                    pass

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        interval = max(0.05, float(self.poll_interval_seconds))
        while not stop.is_set():
            try:
                claim = self.client.claim()
            except Exception:
                claim = None
            if claim:
                try:
                    await self.execute_claim(claim)
                except Exception:
                    logger.exception("Unbrowse claim execution failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


def build_worker(argv: list[str] | None = None) -> UnbrowseWorker:
    parser = argparse.ArgumentParser(prog="unbrowse_worker")
    parser.add_argument("--manager-url", default=os.environ.get("CBM_MANAGER_URL") or "")
    parser.add_argument("--token-file", default=os.environ.get("CBM_WORKER_TOKEN_FILE") or "")
    parser.add_argument("--unbrowse-bin", default="unbrowse")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    manager_url = str(args.manager_url or "").strip().rstrip("/")
    token_file = str(args.token_file or "").strip()
    if not manager_url or not token_file:
        raise ValueError("manager URL and token file are required")
    binary = shutil.which(str(args.unbrowse_bin))
    if not binary:
        raise ValueError("Unbrowse binary is not installed")
    client = UnbrowseClient(manager_url, token_file=Path(token_file))
    return UnbrowseWorker(
        client=client,
        unbrowse_bin=binary,
        poll_interval_seconds=float(args.poll_interval),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        worker = build_worker(argv if argv is not None else sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        print(redact_text(str(exc)), file=sys.stderr)
        return 2
    stop = asyncio.Event()

    def handle_stop(*_args: Any) -> None:
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle_stop)
        except Exception:
            pass
    asyncio.run(worker.run_forever(stop_event=stop))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
