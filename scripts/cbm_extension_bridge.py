#!/usr/bin/env python3
"""Loopback-only command bridge for the CloakBrowser recorder extension.

The browser extension receives only a short-lived, origin-bound session. Local
CLI/MCP clients authenticate with a private token file. The bridge never
accepts arbitrary browser actions or raw credential fields.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18766
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "cloakbrowser" / "extension-control-token"
EXTENSION_ID = "fjcjfaeimhopmpnoemigapegahhjnbkl"
ALLOWED_EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
ALLOWED_OPERATIONS = frozenset({"status", "start", "stop", "export", "clear"})
MAX_BODY_BYTES = 1_048_576
FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "authorization", "cookie", "cookies", "password", "passwd", "raw_value",
        "token", "otp", "totp", "passkey", "cvc", "cvv",
    }
)


class UnsafeRecorderResult(ValueError):
    """Recorder result failed the reference-only boundary."""


class ControlTokenFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"ControlTokenFile(path={self.path!r})"

    def load_or_create(self) -> str:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            token = secrets.token_urlsafe(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                os.write(fd, (token + "\n").encode("ascii"))
            finally:
                os.close(fd)
            return token
        if not stat.S_ISREG(info.st_mode) or self.path.is_symlink():
            raise ValueError("control token must be a regular private file")
        if info.st_mode & 0o077:
            raise ValueError("control token file must use mode 0600")
        token = self.path.read_text(encoding="ascii").strip()
        if len(token) < 43 or any(ch.isspace() for ch in token) or not token.isprintable():
            raise ValueError("control token file is invalid")
        return token


class ExtensionSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        self._now = now
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self, origin: str) -> str:
        if origin != ALLOWED_EXTENSION_ORIGIN:
            raise PermissionError("request is not from the allowed extension origin")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked()
            self._tokens[token] = self._now() + self.ttl_seconds
        return token

    def verify(self, token: str) -> bool:
        with self._lock:
            self._purge_locked()
            expires_at = self._tokens.get(str(token or ""))
            return bool(expires_at and expires_at > self._now())

    def _purge_locked(self) -> None:
        now = self._now()
        for token, expires_at in tuple(self._tokens.items()):
            if expires_at <= now:
                self._tokens.pop(token, None)


def _validate_reference_only(value: Any, *, depth: int = 0) -> None:
    if depth > 16:
        raise UnsafeRecorderResult("recorder result nesting is too deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_RESULT_KEYS:
                raise UnsafeRecorderResult("recorder result contains a forbidden raw field")
            _validate_reference_only(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 1000:
            raise UnsafeRecorderResult("recorder result list is too large")
        for item in value:
            _validate_reference_only(item, depth=depth + 1)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if "authorization: bearer " in lowered or "cookie:" in lowered:
            raise UnsafeRecorderResult("recorder result contains secret-like text")


class CommandBroker:
    def __init__(
        self,
        *,
        ttl_seconds: int = 60,
        max_pending: int = 64,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = max(5, min(int(ttl_seconds), 300))
        self.max_pending = max(1, min(int(max_pending), 256))
        self._now = now
        self._commands: dict[str, dict[str, Any]] = {}
        self._queue: deque[str] = deque()
        self._condition = threading.Condition()

    def enqueue(self, operation: str) -> dict[str, Any]:
        operation = str(operation or "").strip().lower()
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError("unsupported extension operation")
        with self._condition:
            self._expire_locked()
            outstanding = sum(
                1 for item in self._commands.values() if item["state"] in {"queued", "delivered"}
            )
            if outstanding >= self.max_pending:
                raise OverflowError("extension command queue is full")
            command_id = "cmd-" + secrets.token_urlsafe(18)
            created_at = self._now()
            command = {
                "id": command_id,
                "operation": operation,
                "state": "queued",
                "created_at": created_at,
                "expires_at": created_at + self.ttl_seconds,
            }
            self._commands[command_id] = command
            self._queue.append(command_id)
            self._condition.notify_all()
            return self._public_command(command)

    def next_command(self, *, wait_seconds: float = 15) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.0, min(float(wait_seconds), 20.0))
        with self._condition:
            while True:
                self._expire_locked()
                while self._queue:
                    command_id = self._queue.popleft()
                    command = self._commands.get(command_id)
                    if command and command["state"] == "queued":
                        command["state"] = "delivered"
                        command["delivered_at"] = self._now()
                        return self._public_command(command)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    def complete(self, command_id: str, result: Any) -> None:
        encoded = json.dumps(result, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise UnsafeRecorderResult("recorder result is too large")
        _validate_reference_only(result)
        with self._condition:
            self._expire_locked()
            command = self._commands.get(str(command_id or ""))
            if not command or command["state"] != "delivered":
                raise KeyError("extension command is not pending")
            command["state"] = "completed"
            command["completed_at"] = self._now()
            command["result"] = result
            self._condition.notify_all()

    def result(self, command_id: str) -> dict[str, Any]:
        with self._condition:
            self._expire_locked()
            command = self._commands.get(str(command_id or ""))
            if not command:
                return {"state": "missing"}
            payload = {
                "id": command["id"],
                "operation": command["operation"],
                "state": command["state"],
            }
            if command["state"] == "completed":
                payload["result"] = command["result"]
            return payload

    def _expire_locked(self) -> None:
        now = self._now()
        for command in self._commands.values():
            if command["state"] in {"queued", "delivered"} and command["expires_at"] <= now:
                command["state"] = "expired"
        if len(self._commands) > self.max_pending * 4:
            removable = [
                key for key, item in self._commands.items() if item["state"] in {"completed", "expired"}
            ]
            for key in removable[: len(self._commands) - self.max_pending * 4]:
                self._commands.pop(key, None)

    @staticmethod
    def _public_command(command: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": command["id"],
            "operation": command["operation"],
            "createdAt": int(command["created_at"] * 1000),
            "expiresAt": int(command["expires_at"] * 1000),
        }


class ExtensionBridgeHandler(BaseHTTPRequestHandler):
    server_version = "CloakExtensionControl/0.1"

    @property
    def broker(self) -> CommandBroker:
        return self.server.broker  # type: ignore[attr-defined]

    @property
    def sessions(self) -> ExtensionSessionStore:
        return self.server.sessions  # type: ignore[attr-defined]

    @property
    def control_token(self) -> str:
        return self.server.control_token  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self.headers.get("Origin") != ALLOWED_EXTENSION_ORIGIN:
            self._json(403, {"ok": False, "detail": "origin denied"})
            return
        self._json(204, None, extension_cors=True)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "cloak-extension-control", "version": 1})
            return
        if parsed.path == "/v1/extension/commands/next":
            if not self._authorized_extension():
                self._json(401, {"ok": False, "detail": "unauthorized"}, extension_cors=True)
                return
            wait = parse_qs(parsed.query).get("wait", ["15"])[0]
            try:
                command = self.broker.next_command(wait_seconds=float(wait))
            except ValueError:
                self._json(400, {"ok": False, "detail": "invalid wait"}, extension_cors=True)
                return
            self._json(200, {"ok": True, "command": command}, extension_cors=True)
            return
        prefix = "/v1/control/commands/"
        if parsed.path.startswith(prefix) and parsed.query == "":
            if not self._authorized_control():
                self._json(401, {"ok": False, "detail": "unauthorized"})
                return
            self._json(200, {"ok": True, **self.broker.result(parsed.path[len(prefix) :])})
            return
        self._json(404, {"ok": False, "detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.query:
            self._json(404, {"ok": False, "detail": "not found"})
            return
        if parsed.path == "/v1/extension/session":
            origin = self.headers.get("Origin") or ""
            claimed = self.headers.get("X-CBM-Extension-Id") or ""
            if claimed != EXTENSION_ID:
                self._json(403, {"ok": False, "detail": "extension denied"}, extension_cors=True)
                return
            try:
                token = self.sessions.issue(origin)
            except PermissionError:
                self._json(403, {"ok": False, "detail": "origin denied"})
                return
            self._json(
                200,
                {"ok": True, "sessionToken": token, "expiresIn": self.sessions.ttl_seconds},
                extension_cors=True,
            )
            return
        if parsed.path == "/v1/control/commands":
            if not self._authorized_control():
                self._json(401, {"ok": False, "detail": "unauthorized"})
                return
            try:
                command = self.broker.enqueue(str(self._body().get("operation") or ""))
            except (ValueError, OverflowError) as exc:
                self._json(400, {"ok": False, "detail": str(exc)})
                return
            self._json(202, {"ok": True, "command": command})
            return
        result_prefix = "/v1/extension/commands/"
        result_suffix = "/result"
        if parsed.path.startswith(result_prefix) and parsed.path.endswith(result_suffix):
            if not self._authorized_extension():
                self._json(401, {"ok": False, "detail": "unauthorized"}, extension_cors=True)
                return
            command_id = parsed.path[len(result_prefix) : -len(result_suffix)]
            try:
                self.broker.complete(command_id, self._body().get("result"))
            except (KeyError, UnsafeRecorderResult, ValueError) as exc:
                self._json(400, {"ok": False, "detail": str(exc)}, extension_cors=True)
                return
            self._json(200, {"ok": True}, extension_cors=True)
            return
        self._json(404, {"ok": False, "detail": "not found"})

    def _authorized_control(self) -> bool:
        supplied = self._bearer()
        return bool(supplied and secrets.compare_digest(supplied, self.control_token))

    def _authorized_extension(self) -> bool:
        return self.headers.get("Origin") == ALLOWED_EXTENSION_ORIGIN and self.sessions.verify(
            self._bearer()
        )

    def _bearer(self) -> str:
        raw = self.headers.get("Authorization") or ""
        return raw[7:] if raw.startswith("Bearer ") else ""

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _json(
        self,
        status: int,
        payload: dict[str, Any] | None,
        *,
        extension_cors: bool = False,
    ) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if extension_cors and self.headers.get("Origin") == ALLOWED_EXTENSION_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_EXTENSION_ORIGIN)
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-CBM-Extension-Id",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")
        self.end_headers()
        if body:
            self.wfile.write(body)


class ExtensionBridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        control_token: str,
        broker: CommandBroker | None = None,
        sessions: ExtensionSessionStore | None = None,
    ) -> None:
        self.control_token = control_token
        self.broker = broker or CommandBroker()
        self.sessions = sessions or ExtensionSessionStore()
        super().__init__(address, ExtensionBridgeHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cbm_extension_bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("bridge host must be loopback")
    token = ControlTokenFile(args.token_file).load_or_create()
    server = ExtensionBridgeServer((args.host, args.port), control_token=token)
    print(f"CloakBrowser extension control on http://{args.host}:{args.port}", flush=True)
    print(f"Control token file: {args.token_file}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
