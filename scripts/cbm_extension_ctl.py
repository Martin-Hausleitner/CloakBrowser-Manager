#!/usr/bin/env python3
"""Local CLI for the bounded CloakBrowser extension recorder bridge."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from scripts.cbm_extension_bridge import ALLOWED_OPERATIONS, DEFAULT_PORT, DEFAULT_TOKEN_FILE
from scripts.secure_recording_compiler import compile_recording


DEFAULT_BRIDGE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"


def validate_bridge_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("extension bridge must use loopback HTTP")
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("extension bridge URL must contain only loopback host and port")
    if parsed.port is None or parsed.port < 1024 or parsed.port > 65535:
        raise ValueError("extension bridge port must be explicit and unprivileged")
    return value


def _load_private_token(path: Path) -> str:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("control token must be a regular private file")
    if path.stat().st_mode & 0o077:
        raise ValueError("control token file must use mode 0600")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 43 or any(ch.isspace() for ch in token):
        raise ValueError("control token file is invalid")
    return token


def _http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"extension bridge rejected request ({exc.code})") from exc
    except URLError as exc:
        raise RuntimeError("extension bridge is unavailable") from exc
    result = json.loads(raw or "{}")
    if not isinstance(result, dict):
        raise RuntimeError("extension bridge returned invalid JSON")
    return result


class ExtensionControlClient:
    def __init__(
        self,
        bridge_url: str = DEFAULT_BRIDGE_URL,
        *,
        token_path: Path = DEFAULT_TOKEN_FILE,
        transport: Callable[..., dict[str, Any]] = _http_transport,
    ) -> None:
        self.bridge_url = validate_bridge_url(bridge_url)
        self.token_path = Path(token_path)
        self._transport = transport

    def __repr__(self) -> str:
        return f"ExtensionControlClient(bridge_url={self.bridge_url!r}, token_path={self.token_path!r})"

    def execute(self, operation: str, *, timeout_seconds: float = 30) -> Any:
        requested = str(operation or "").strip().lower()
        wire_operation = "export" if requested == "compile" else requested
        if wire_operation not in ALLOWED_OPERATIONS:
            raise ValueError("unsupported extension operation")
        timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        token = _load_private_token(self.token_path)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        created = self._transport(
            "POST",
            f"{self.bridge_url}/v1/control/commands",
            headers,
            {"operation": wire_operation},
            min(timeout_seconds, 10.0),
        )
        command_id = str(created.get("command", {}).get("id") or "")
        if not command_id.startswith("cmd-"):
            raise RuntimeError("extension bridge returned an invalid command id")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self._transport(
                "GET",
                f"{self.bridge_url}/v1/control/commands/{command_id}",
                headers,
                None,
                min(max(deadline - time.monotonic(), 0.1), 10.0),
            )
            state = status.get("state")
            if state == "completed":
                result = status.get("result")
                return compile_recording(result) if requested == "compile" else result
            if state in {"expired", "missing"}:
                raise RuntimeError(f"extension command {state}")
            time.sleep(0.1)
        raise TimeoutError("extension command timed out")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cbm-extension-ctl")
    parser.add_argument("--bridge-url", default=os.environ.get("CBM_EXTENSION_BRIDGE_URL", DEFAULT_BRIDGE_URL))
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(os.environ.get("CBM_EXTENSION_CONTROL_TOKEN_FILE", DEFAULT_TOKEN_FILE)),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("operation", choices=sorted(ALLOWED_OPERATIONS | {"compile"}))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = ExtensionControlClient(
            args.bridge_url, token_path=args.token_file
        ).execute(args.operation, timeout_seconds=args.timeout)
    except Exception as exc:  # noqa: BLE001 - sanitized public failure only.
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "operation": args.operation, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
