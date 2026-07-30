#!/usr/bin/env python3
"""Managed Stagehand worker for an existing CloakBrowser profile.

The worker claims only ``stagehand`` runs. It exchanges a Manager lease for a
short-lived CDP capability, hides that capability behind an ephemeral loopback
nonce relay, and gives the resulting browser endpoint to a small Node runner
over stdin. Stagehand therefore attaches to the selected Manager browser and
never receives a persistent credential or launches another profile.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urljoin

from scripts.browser_use_worker import ManagerClient, ManagerHTTPError, redact_text
from scripts.unbrowse_managed_helper import normalized_navigation_url, start_cdp_gateway
from scripts.unbrowse_worker import read_browser_endpoint, read_cdp_page, select_task_target


logger = logging.getLogger(__name__)
HARNESS = "stagehand"
MAX_RUNNER_LINE_BYTES = 1_048_576

GatewayFactory = Callable[..., Awaitable[tuple[Any, str]]]
EndpointReader = Callable[[str], Awaitable[str]]
RunnerFactory = Callable[..., Awaitable[Any]]
PageReader = Callable[[str, str], Awaitable[dict[str, str]]]


class RunCancelled(RuntimeError):
    """The Manager asked the worker to release the active browser lease."""


class StagehandRunnerError(RuntimeError):
    """Sterile Stagehand runtime failure safe for the Manager boundary."""


class StagehandClient(ManagerClient):
    """Manager client whose claim endpoint is hard-bound to Stagehand."""

    def claim(self) -> dict[str, Any] | None:
        response = self.request(
            "POST",
            f"/internal/task-runs/claim?{urlencode({'harness': HARNESS})}",
        )
        if response.status_code == 204:
            return None
        return response.json()


class StagehandRunner:
    """One-shot JSON runner whose private CDP endpoint is delivered via stdin."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        browser_ws: str,
        target_url: str,
    ) -> None:
        self.process = process
        self.browser_ws = browser_ws
        self.target_url = target_url

    @classmethod
    async def start(
        cls,
        *,
        node_bin: str,
        script_path: str,
        browser_ws: str,
        target_url: str,
    ) -> "StagehandRunner":
        process = await asyncio.create_subprocess_exec(
            node_bin,
            script_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=MAX_RUNNER_LINE_BYTES + 1,
        )
        return cls(process, browser_ws=browser_ws, target_url=target_url)

    async def run(self) -> dict[str, str]:
        if self.process.stdin is None or self.process.stdout is None:
            raise StagehandRunnerError("Stagehand runtime is unavailable")
        request = json.dumps(
            {"cdpUrl": self.browser_ws, "targetUrl": self.target_url},
            separators=(",", ":"),
        ).encode("utf-8")
        self.process.stdin.write(request)
        await self.process.stdin.drain()
        self.process.stdin.close()
        while True:
            try:
                raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=90)
            except asyncio.TimeoutError as exc:
                raise StagehandRunnerError("Stagehand runtime timed out") from exc
            if not raw:
                raise StagehandRunnerError("Stagehand runtime ended without a result")
            line = raw.rstrip(b"\r\n")
            if len(line) > MAX_RUNNER_LINE_BYTES:
                raise StagehandRunnerError("Stagehand runtime result exceeds size bound")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or "ok" not in payload:
                continue
            if payload.get("ok") is not True:
                raise StagehandRunnerError("Stagehand runtime failed")
            url = normalized_navigation_url(str(payload.get("url") or self.target_url))
            title = redact_text(str(payload.get("title") or "")).strip()[:200]
            return {"url": url, "title": title}

    async def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.is_closing():
            try:
                self.process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if self.process.returncode is None:
            try:
                if os.name == "posix":
                    os.killpg(self.process.pid, signal.SIGTERM)
                else:
                    self.process.terminate()
            except ProcessLookupError:
                await self.process.wait()
                return
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    if os.name == "posix":
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                except ProcessLookupError:
                    pass
                await self.process.wait()


async def start_stagehand_runner(**kwargs: Any) -> StagehandRunner:
    return await StagehandRunner.start(**kwargs)


@dataclass
class StagehandWorker:
    client: Any
    node_bin: str = "node"
    script_path: str = str(Path(__file__).with_name("stagehand_runtime") / "runner.mjs")
    poll_interval_seconds: float = 2.0
    gateway_factory: GatewayFactory = field(default=start_cdp_gateway, repr=False)
    endpoint_reader: EndpointReader = field(default=read_browser_endpoint, repr=False)
    runner_factory: RunnerFactory = field(default=start_stagehand_runner, repr=False)
    page_reader: PageReader = field(default=read_cdp_page, repr=False)

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

    async def _read_observed_page(
        self,
        browser_ws: str,
        expected_url: str,
        *,
        cancelled: asyncio.Event,
    ) -> dict[str, str]:
        observed: dict[str, str] = {}
        for attempt in range(6):
            observed = await self._await_guarded(
                self.page_reader(browser_ws, expected_url),
                cancelled=cancelled,
            )
            if observed.get("title"):
                return observed
            if attempt < 5:
                try:
                    await asyncio.wait_for(cancelled.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                raise RunCancelled("run cancelled")
        return observed

    async def execute_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        run_id = str(claim.get("id") or "")
        if not run_id or claim.get("harness") != HARNESS:
            return {"status": "failed", "error_code": "internal_error"}

        capability_issued = False
        cancelled = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        gateway_runner: Any | None = None
        runner: Any | None = None
        try:
            safe_url = normalized_navigation_url(select_task_target(claim))
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
                upstream_http=upstream_http,
                headers=headers,
            )
            browser_endpoint = await self.endpoint_reader(local_ws)
            runner = await self.runner_factory(
                node_bin=self.node_bin,
                script_path=self.script_path,
                browser_ws=browser_endpoint,
                target_url=safe_url,
            )
            result = await self._await_guarded(runner.run(), cancelled=cancelled)
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="action",
                summary="navigate",
                payload={"name": "navigate", "url": safe_url, "step": 1},
                idempotency_key=f"stagehand:{run_id}:navigate",
            )
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="status",
                summary="Stagehand attached to the managed profile",
                payload={"status": "running", "progress": 60},
                idempotency_key=f"stagehand:{run_id}:attached",
            )
            observed = await self._read_observed_page(
                local_ws,
                safe_url,
                cancelled=cancelled,
            )
            if not observed:
                raise StagehandRunnerError("Stagehand did not navigate the managed profile")
            title = observed.get("title") or str(result.get("title") or safe_url)
            title = redact_text(title).strip()[:200] or safe_url
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="observation",
                summary=title,
                payload={"title": title, "url": safe_url},
                idempotency_key=f"stagehand:{run_id}:observation",
            )
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="summary",
                summary=title,
                payload={"status": "succeeded", "text": title},
                idempotency_key=f"stagehand:{run_id}:summary",
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
                logger.exception("failed to report Stagehand run failure")
            return {"status": "failed", "error_code": "internal_error"}
        finally:
            cancelled.set()
            if heartbeat is not None:
                heartbeat.cancel()
            if runner is not None:
                try:
                    await runner.close()
                except Exception:
                    logger.warning("Stagehand runner cleanup failed")
            if gateway_runner is not None:
                try:
                    await gateway_runner.cleanup()
                except Exception:
                    logger.warning("Stagehand CDP gateway cleanup failed")
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
                    logger.exception("Stagehand claim execution failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


def build_worker(argv: list[str] | None = None) -> StagehandWorker:
    parser = argparse.ArgumentParser(prog="stagehand_worker")
    parser.add_argument("--manager-url", default=os.environ.get("CBM_MANAGER_URL") or "")
    parser.add_argument("--token-file", default=os.environ.get("CBM_WORKER_TOKEN_FILE") or "")
    parser.add_argument("--node-bin", default="node")
    parser.add_argument(
        "--script-path",
        default=str(Path(__file__).with_name("stagehand_runtime") / "runner.mjs"),
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    manager_url = str(args.manager_url or "").strip().rstrip("/")
    token_file = str(args.token_file or "").strip()
    node_bin = shutil.which(str(args.node_bin))
    script_path = Path(str(args.script_path)).resolve()
    if not manager_url or not token_file:
        raise ValueError("manager URL and token file are required")
    if not node_bin:
        raise ValueError("Node.js is not installed")
    if not script_path.is_file():
        raise ValueError("Stagehand runtime is not installed")
    client = StagehandClient(manager_url, token_file=Path(token_file))
    return StagehandWorker(
        client=client,
        node_bin=node_bin,
        script_path=str(script_path),
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
