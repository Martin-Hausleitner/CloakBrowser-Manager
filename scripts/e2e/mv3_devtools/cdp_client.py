"""Minimal CDP client for MV3 service-worker verification."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.request import urlopen

import websocket

from scripts.e2e.mv3_devtools import EXTENSION_ID


@dataclass
class CdpClient:
    browser_ws_url: str
    _ws: Any = field(default=None, init=False, repr=False)
    _next_id: int = field(default=0, init=False, repr=False)

    def connect(self, timeout_seconds: float = 5.0) -> None:
        self._ws = websocket.create_connection(
            self.browser_ws_url,
            timeout=timeout_seconds,
            suppress_origin=True,
        )
        self._ws.settimeout(timeout_seconds)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def __enter__(self) -> CdpClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("CDP client is not connected")
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        if session_id:
            payload["sessionId"] = session_id
        self._ws.send(json.dumps(payload))
        deadline = time.time() + max(0.5, timeout_seconds)
        while time.time() < deadline:
            message = json.loads(self._ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                return message.get("result") or {}
        raise TimeoutError(f"CDP {method} timed out")

    def get_targets(self) -> list[dict[str, Any]]:
        result = self.call("Target.getTargets")
        infos = result.get("targetInfos") or []
        if not isinstance(infos, list):
            return []
        return [item for item in infos if isinstance(item, dict)]

    def find_extension_service_worker(
        self,
        extension_id: str = EXTENSION_ID,
    ) -> dict[str, Any] | None:
        needle = f"chrome-extension://{extension_id}/"
        for target in self.get_targets():
            url = str(target.get("url") or "")
            if target.get("type") == "service_worker" and needle in url:
                return target
        return None

    def ensure_extension_page(
        self,
        path: str = "popup/popup.html",
        *,
        extension_id: str = EXTENSION_ID,
    ) -> dict[str, Any]:
        url = f"chrome-extension://{extension_id}/{path.lstrip('/')}"
        for target in self.get_targets():
            if target.get("type") == "page" and str(target.get("url") or "").startswith(url):
                return target
        created = self.call("Target.createTarget", {"url": url})
        target_id = created.get("targetId")
        deadline = time.time() + 5
        while time.time() < deadline:
            for target in self.get_targets():
                if target.get("targetId") == target_id or (
                    target.get("type") == "page"
                    and str(target.get("url") or "").startswith(url)
                ):
                    return target
            time.sleep(0.1)
        raise TimeoutError(f"extension page not created: {url}")

    def evaluate_in_target(
        self,
        target_id: str,
        expression: str,
        *,
        await_promise: bool = False,
        timeout_seconds: float = 8.0,
    ) -> Any:
        attached = self.call(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
            timeout_seconds=timeout_seconds,
        )
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("Target.attachToTarget returned no sessionId")
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": bool(await_promise),
            },
            session_id=session_id,
            timeout_seconds=timeout_seconds,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text") or details.get("exception", {}).get("description")
            raise RuntimeError(f"Runtime.evaluate failed: {text}")
        return (result.get("result") or {}).get("value")

    def verify_service_worker(
        self,
        *,
        extension_id: str = EXTENSION_ID,
        wake: bool = True,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        deadline = time.time() + max(1.0, timeout_seconds)
        page = None
        if wake:
            page = self.ensure_extension_page()
        last_targets: list[dict[str, Any]] = []
        while time.time() < deadline:
            self.call("Target.setDiscoverTargets", {"discover": True})
            last_targets = self.get_targets()
            worker = self.find_extension_service_worker(extension_id)
            if worker:
                runtime_probe = None
                if page and page.get("targetId"):
                    try:
                        runtime_probe = self.evaluate_in_target(
                            str(page["targetId"]),
                            "({id: chrome.runtime.id, version: chrome.runtime.getManifest().version, "
                            "bg: chrome.runtime.getManifest().background})",
                            timeout_seconds=5.0,
                        )
                    except Exception as exc:  # noqa: BLE001
                        runtime_probe = {"ok": False, "error": str(exc)[:200]}
                return {
                    "ok": True,
                    "service_worker": {
                        "type": worker.get("type"),
                        "url": worker.get("url"),
                        "title": worker.get("title"),
                        "targetId": worker.get("targetId"),
                    },
                    "runtime": runtime_probe,
                    "target_count": len(last_targets),
                }
            time.sleep(0.25)
        return {
            "ok": False,
            "error": "service_worker_not_observed",
            "target_count": len(last_targets),
            "extension_targets": [
                {
                    "type": item.get("type"),
                    "url": item.get("url"),
                }
                for item in last_targets
                if extension_id in str(item.get("url") or "")
            ],
        }


def discover_browser_ws_url(debugging_port: int) -> str:
    with urlopen(f"http://127.0.0.1:{debugging_port}/json/version", timeout=3) as resp:
        payload = json.load(resp)
    url = payload.get("webSocketDebuggerUrl")
    if not isinstance(url, str) or not url.startswith("ws://127.0.0.1:"):
        raise RuntimeError("invalid browser webSocketDebuggerUrl")
    return url
