"""Start/stop the local extension control bridge for E2E."""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from scripts.cbm_extension_bridge import ControlTokenFile, ExtensionBridgeServer


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


@dataclass
class LocalExtensionBridge:
    host: str
    port: int
    token_path: Path
    control_token: str
    server: ExtensionBridgeServer
    thread: threading.Thread
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __repr__(self) -> str:
        return (
            f"LocalExtensionBridge(host={self.host!r}, port={self.port!r}, "
            f"token_path={self.token_path!r})"
        )

    def wait_until_healthy(self, timeout_seconds: float = 10.0) -> dict[str, Any]:
        deadline = time.time() + max(1.0, timeout_seconds)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urlopen(f"{self.base_url}/health", timeout=1) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("ok") is True:
                    return payload
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.1)
        raise TimeoutError(f"extension bridge not healthy: {last_error!r}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            self.thread.join(timeout=5)

    def __enter__(self) -> LocalExtensionBridge:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def start_local_extension_bridge(
    *,
    token_path: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> LocalExtensionBridge:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("bridge host must be loopback")
    path = Path(token_path)
    token = ControlTokenFile(path).load_or_create()
    bind_port = int(port) if port is not None else _free_port()
    server = ExtensionBridgeServer((host, bind_port), control_token=token)
    thread = threading.Thread(
        target=server.serve_forever,
        name="mv3-extension-bridge",
        daemon=True,
    )
    thread.start()
    bridge = LocalExtensionBridge(
        host=host,
        port=bind_port,
        token_path=path,
        control_token=token,
        server=server,
        thread=thread,
    )
    try:
        bridge.wait_until_healthy()
    except Exception:
        bridge.close()
        raise
    return bridge
