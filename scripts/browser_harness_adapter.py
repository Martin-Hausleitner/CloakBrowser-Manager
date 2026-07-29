#!/usr/bin/env python3
"""Run-scoped Browser Harness adapter for the normalized browser-tool router."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from backend.origin_policy import is_top_level_origin_allowed
from scripts.browser_tool_router import BrowserToolRequest, BrowserToolResult
from scripts.cbm_browser_ctl import (
    MAX_TEXT_CHARS,
    BrowserCtlError,
    redact_error_message,
    redact_text,
    redact_url,
    validate_http_url,
    validate_selector,
    validate_text,
)
from scripts.unbrowse_managed_helper import start_cdp_gateway


MAX_REQUEST_BYTES = 16_384
MAX_OUTPUT_BYTES = 65_536
MAX_STDERR_BYTES = 16_384
MAX_STDERR_CHARS = 700
DEFAULT_TIMEOUT_SECONDS = 30.0
SUPPORTED_ACTIONS = {"inspect", "navigate", "click", "fill", "read_text"}
REQUEST_FILE_ENV = "CBM_BROWSER_HARNESS_REQUEST_FILE"
READ_CHUNK_BYTES = 8_192
OPERATIONAL_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_RUNTIME_DIR",
)

GatewayStarter = Callable[..., Awaitable[tuple[Any, str]]]
ExecutableResolver = Callable[[str], str | None]
ProcessLauncher = Callable[..., Awaitable[Any]]


STATIC_BROWSER_HARNESS_PROGRAM = r'''
import json
import os
from urllib.parse import urlsplit

with open(os.environ["CBM_BROWSER_HARNESS_REQUEST_FILE"], "r", encoding="utf-8") as f:
    request = json.load(f)

action = request["action"]
arguments = request.get("arguments") or {}
allowed_origins = request.get("allowed_origins") or []

def normalized_origin(value):
    parts = urlsplit(str(value or ""))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    default_port = 80 if parts.scheme == "http" else 443
    port = parts.port
    host = parts.hostname.lower()
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parts.scheme.lower()}://{netloc}"

def current_origin_allowed(current_url):
    if not allowed_origins:
        return True
    current = normalized_origin(current_url)
    allowed = {item for item in (normalized_origin(value) for value in allowed_origins) if item}
    return bool(current and current in allowed)

def origin_denied():
    return {
        "ok": False,
        "classification": "origin_denied",
        "error": "current page origin is outside the run allowed origin set",
    }

def policy_denied(message):
    return {"ok": False, "classification": "policy_denied", "error": message}

def final_result(payload):
    final = page_info()
    final_url = str(final.get("url") or "")
    if not final_url:
        return policy_denied("browser-harness final page URL is missing")
    result = dict(payload)
    result["url"] = final_url
    if "title" in final:
        result["title"] = final.get("title")
    return result

if action in {"inspect", "click", "fill", "read_text"}:
    current = page_info()
    if not current_origin_allowed(current.get("url")):
        result = origin_denied()
    elif action == "inspect":
        result = final_result({"ok": True, "command": "page_info"})
    elif action == "click":
        selector = arguments["selector"]
        clicked = js(
            "(()=>{const e=document.querySelector("
            + json.dumps(selector)
            + ");if(!e)return false;e.click();return true})()"
        )
        if not clicked:
            result = policy_denied("selector was not found")
        else:
            result = final_result({"ok": True, "command": "click", "selector": selector})
    elif action == "fill":
        fill_input(arguments["selector"], arguments["text"])
        result = final_result(
            {
                "ok": True,
                "command": "fill",
                "selector": arguments["selector"],
                "text_length": len(arguments["text"]),
            }
        )
    else:
        selector = arguments.get("selector")
        query = selector or "body"
        text = js(
            "(()=>{const e=document.querySelector("
            + json.dumps(query)
            + ");return e ? e.innerText : null})()"
        )
        if text is None:
            result = policy_denied("selector was not found")
        else:
            result = final_result(
                {
                    "ok": True,
                    "command": "read_text",
                    "selector": selector,
                    "text": str(text),
                }
            )
elif action == "navigate":
    goto_url(arguments["url"])
    result = final_result({"ok": True, "command": "navigate"})
else:
    result = {"ok": False, "error": "unsupported_action"}

print(json.dumps(result, separators=(",", ":"), sort_keys=True))
'''


class BrowserHarnessAdapter:
    """Attach Browser Harness to one Manager-owned profile through a nonce relay."""

    def __init__(
        self,
        *,
        executable: str = "browser-harness",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        executable_resolver: ExecutableResolver | None = None,
        gateway_starter: GatewayStarter = start_cdp_gateway,
        process_launcher: ProcessLauncher | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.executable_resolver = executable_resolver or shutil.which
        self.gateway_starter = gateway_starter
        self.process_launcher = process_launcher or asyncio.create_subprocess_exec

    async def __call__(self, request: BrowserToolRequest) -> BrowserToolResult:
        try:
            action, arguments = _validated_action(request)
        except _UnsupportedAction as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="unsupported_action",
                message=str(exc),
            )
        except _OriginDenied as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="origin_denied",
                message=str(exc),
            )
        except BrowserCtlError as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied",
                message=redact_error_message(exc.message),
            )
        except ValueError as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied",
                message=redact_error_message(str(exc)),
            )

        binary = self.executable_resolver(self.executable)
        if not binary:
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message="browser-harness executable is unavailable",
            )

        gateway_runner: Any | None = None
        request_path: Path | None = None
        process: Any | None = None
        try:
            gateway_runner, local_ws = await self.gateway_starter(
                upstream_http=_manager_cdp_endpoint(request),
                headers={"Authorization": f"Bearer {request.context.capability_token}"},
            )
            request_path = _write_request_file(action, arguments, request=request)
            env = _harness_env(
                local_ws=local_ws,
                request_path=request_path,
                request=request,
            )
            process = await self.process_launcher(
                binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES + 1,
            )
            stdout, stderr, returncode = await _run_process_bounded(
                process,
                STATIC_BROWSER_HARNESS_PROGRAM.encode("utf-8"),
                timeout_seconds=self.timeout_seconds,
            )
            if returncode != 0:
                return BrowserToolResult(
                    outcome="failed",
                    classification="tool_unavailable",
                    message=_bounded_error(stderr, "browser-harness command failed"),
                )
            return _result_from_stdout(stdout, request=request)
        except _OutputTooLarge as exc:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message=str(exc),
            )
        except asyncio.TimeoutError:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="transient_timeout",
                message="browser-harness timed out",
            )
        except asyncio.CancelledError:
            await _kill_process(process)
            raise
        except _OriginDenied as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="origin_denied",
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary must be terminal-safe
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message=redact_error_message(str(exc))[:MAX_STDERR_CHARS],
            )
        finally:
            if request_path is not None:
                try:
                    request_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if gateway_runner is not None:
                await _cleanup_gateway(gateway_runner)


async def browser_harness_adapter(request: BrowserToolRequest) -> BrowserToolResult:
    return await BrowserHarnessAdapter()(request)


class _UnsupportedAction(ValueError):
    pass


class _OriginDenied(ValueError):
    pass


class _PolicyDenied(ValueError):
    pass


class _OutputTooLarge(RuntimeError):
    pass


def _validated_action(request: BrowserToolRequest) -> tuple[str, dict[str, Any]]:
    action = str(request.action or "").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise _UnsupportedAction("unsupported browser-harness action")
    raw_args = request.arguments if isinstance(request.arguments, dict) else {}
    if action == "inspect":
        return action, {}
    if action == "navigate":
        url = validate_http_url(str(raw_args.get("url") or ""))
        _require_allowed_url(url, request)
        return action, {"url": url}
    if action == "click":
        return action, {"selector": validate_selector(str(raw_args.get("selector") or ""))}
    if action == "fill":
        return action, {
            "selector": validate_selector(str(raw_args.get("selector") or "")),
            "text": validate_text(raw_args.get("text")),
        }
    selector = raw_args.get("selector")
    if selector is None or selector == "":
        return action, {}
    return action, {"selector": validate_selector(str(selector))}


def _require_allowed_url(url: str, request: BrowserToolRequest) -> None:
    allowed = request.context.allowed_origins
    if not allowed:
        return
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not is_top_level_origin_allowed(origin, allowed):
        raise _OriginDenied("URL is outside the run allowed origin set")


def _manager_cdp_endpoint(request: BrowserToolRequest) -> str:
    return (
        f"{request.context.manager_url.rstrip('/')}"
        f"/api/profiles/{request.context.profile_id}/cdp"
    )


def _write_request_file(
    action: str,
    arguments: dict[str, Any],
    *,
    request: BrowserToolRequest,
) -> Path:
    payload = json.dumps(
        {
            "action": action,
            "arguments": arguments,
            "allowed_origins": list(request.context.allowed_origins),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise BrowserCtlError("request_too_large", "Browser Harness request is too large")
    fd, raw_path = tempfile.mkstemp(prefix="cbm-browser-harness-", suffix=".json")
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return path
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _harness_env(
    *,
    local_ws: str,
    request_path: Path,
    request: BrowserToolRequest,
) -> dict[str, str]:
    env = {key: os.environ[key] for key in OPERATIONAL_ENV_KEYS if key in os.environ}
    env.update(
        {
            "BU_AUTOSPAWN": "0",
            "BU_CDP_WS": local_ws,
            "BU_CDP_URL": _http_from_ws(local_ws),
            "BU_NAME": _unique_harness_name(request),
            "BH_RECORD": "0",
            REQUEST_FILE_ENV: str(request_path),
        }
    )
    return env


def _http_from_ws(value: str) -> str:
    if value.startswith("ws://"):
        return "http://" + value[len("ws://") :]
    if value.startswith("wss://"):
        return "https://" + value[len("wss://") :]
    return value


def _unique_harness_name(request: BrowserToolRequest) -> str:
    base = f"cbm-{request.context.task_run_id}-{request.context.profile_id}"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")[:80] or "cbm-run"
    return f"{safe}-{uuid.uuid4().hex[:12]}"


def _result_from_stdout(stdout: bytes, *, request: BrowserToolRequest) -> BrowserToolResult:
    if len(stdout) > MAX_OUTPUT_BYTES:
        return BrowserToolResult(
            outcome="failed",
            classification="tool_unavailable",
            message="browser-harness output exceeds size bound",
        )
    payload = _parse_json_output(stdout)
    if payload.get("opened_second_browser") is True or payload.get("second_browser") is True:
        return BrowserToolResult(
            outcome="failed",
            classification="second_browser_attempt",
            message="browser-harness signaled a second browser attempt",
        )
    if payload.get("ok") is False:
        classification = str(payload.get("classification") or "").strip()
        if classification == "origin_denied":
            return BrowserToolResult(
                outcome="failed",
                classification="origin_denied",
                message=redact_error_message(
                    str(payload.get("error") or "current page origin denied")
                ),
            )
        if classification == "policy_denied":
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied",
                message=redact_error_message(str(payload.get("error") or "policy denied")),
            )
        return BrowserToolResult(
            outcome="failed",
            classification="tool_unavailable",
            message=redact_error_message(str(payload.get("error") or "browser-harness failed")),
        )
    try:
        safe = _safe_payload(payload, request=request)
    except _OriginDenied as exc:
        return BrowserToolResult(
            outcome="failed",
            classification="origin_denied",
            message=str(exc),
        )
    except _PolicyDenied as exc:
        return BrowserToolResult(
            outcome="failed",
            classification="policy_denied",
            message=str(exc),
        )
    return BrowserToolResult(outcome="succeeded", classification="ok", payload=safe)


def _parse_json_output(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", "replace")
    for line in reversed([item for item in text.splitlines() if item.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("browser-harness did not return bounded JSON")


def _safe_payload(payload: dict[str, Any], *, request: BrowserToolRequest) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in ("command", "selector"):
        if key in payload:
            safe[key] = payload[key]
    raw_url = str(payload.get("url") or "")
    if not raw_url:
        raise _PolicyDenied("browser-harness result missing final URL")
    _require_allowed_url(raw_url, request)
    safe["url"] = redact_url(raw_url)
    if "title" in payload:
        safe["title"] = redact_text(str(payload.get("title") or ""))[:200]
    if "text" in payload:
        safe["text"] = redact_text(str(payload.get("text") or ""))[:MAX_TEXT_CHARS]
    if "selector" in safe and safe["selector"] is not None:
        safe["selector"] = validate_selector(str(safe["selector"]))
    return safe


def _bounded_error(stderr: bytes, fallback: str) -> str:
    raw = stderr.decode("utf-8", "replace").strip() or fallback
    return redact_error_message(raw)[:MAX_STDERR_CHARS]


async def _run_process_bounded(
    process: Any,
    program: bytes,
    *,
    timeout_seconds: float,
) -> tuple[bytes, bytes, int]:
    async def run() -> tuple[bytes, bytes, int]:
        stdin = getattr(process, "stdin", None)
        if stdin is None:
            raise RuntimeError("browser-harness stdin is unavailable")
        stdin.write(program)
        drain = getattr(stdin, "drain", None)
        if callable(drain):
            await drain()
        close = getattr(stdin, "close", None)
        if callable(close):
            close()
        wait_closed = getattr(stdin, "wait_closed", None)
        if callable(wait_closed):
            await wait_closed()
        stdout_reader = getattr(process, "stdout", None)
        stderr_reader = getattr(process, "stderr", None)
        if stdout_reader is None or stderr_reader is None:
            raise RuntimeError("browser-harness streams are unavailable")
        stdout_task = asyncio.create_task(
            _read_limited(stdout_reader, MAX_OUTPUT_BYTES, "browser-harness output")
        )
        stderr_task = asyncio.create_task(
            _read_limited(stderr_reader, MAX_STDERR_BYTES, "browser-harness stderr")
        )
        wait_task = asyncio.create_task(process.wait())
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            stdout, stderr, returncode = await asyncio.gather(*tasks)
            return stdout, stderr, int(returncode if returncode is not None else 0)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    try:
        return await asyncio.wait_for(run(), timeout=timeout_seconds)
    except (asyncio.TimeoutError, asyncio.CancelledError, _OutputTooLarge):
        await _kill_process(process)
        raise


async def _read_limited(reader: Any, max_bytes: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        read_size = min(READ_CHUNK_BYTES, max_bytes - total + 1)
        data = await reader.read(read_size)
        if not data:
            return b"".join(chunks)
        total += len(data)
        if total > max_bytes:
            raise _OutputTooLarge(f"{label} exceeds size bound")
        chunks.append(data)


async def _kill_process(process: Any | None) -> None:
    if process is None:
        return
    if getattr(process, "returncode", None) is not None and getattr(process, "killed", False):
        return
    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except ProcessLookupError:
            pass
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            await wait()
        except Exception:
            pass


async def _cleanup_gateway(runner: Any) -> None:
    cleanup = getattr(runner, "cleanup", None)
    if callable(cleanup):
        result = cleanup()
        if hasattr(result, "__await__"):
            await result
