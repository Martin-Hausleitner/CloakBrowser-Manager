#!/usr/bin/env python3
"""Run-scoped Stagehand router adapter for Manager-owned browser profiles."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import tempfile
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
from scripts.unbrowse_worker import read_browser_endpoint


MAX_REQUEST_BYTES = 16_384
MAX_OUTPUT_BYTES = 65_536
MAX_STDERR_BYTES = 16_384
MAX_STDERR_CHARS = 700
READ_CHUNK_BYTES = 8_192
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_CONNECT_TIMEOUT_MS = 60_000
SUPPORTED_STAGEHAND_VERSION = "3.7.1"
DETERMINISTIC_ACTIONS = {"inspect", "navigate", "click", "fill", "read_text"}
SEMANTIC_ACTIONS = {"act", "extract", "observe", "agent"}
REQUEST_FILE_ENV = "CBM_STAGEHAND_REQUEST_FILE"
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
EndpointReader = Callable[[str], Awaitable[str]]
ExecutableResolver = Callable[[str], str | None]
ProcessLauncher = Callable[..., Awaitable[Any]]


class StagehandRouterAdapter:
    """Attach Stagehand v3.7.1 to the exact Manager profile via nonce CDP."""

    def __init__(
        self,
        *,
        node_bin: str = "node",
        runner_path: Path | str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        connect_timeout_ms: int = 15_000,
        node_resolver: ExecutableResolver | None = None,
        gateway_starter: GatewayStarter = start_cdp_gateway,
        endpoint_reader: EndpointReader = read_browser_endpoint,
        process_launcher: ProcessLauncher | None = None,
    ) -> None:
        self.node_bin = node_bin
        self.runner_path = Path(runner_path) if runner_path is not None else (
            Path(__file__).with_name("stagehand_runtime") / "router-runner.mjs"
        )
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_ms = max(1, min(MAX_CONNECT_TIMEOUT_MS, int(connect_timeout_ms)))
        self.node_resolver = node_resolver or shutil.which
        self.gateway_starter = gateway_starter
        self.endpoint_reader = endpoint_reader
        self.process_launcher = process_launcher or asyncio.create_subprocess_exec
        self._preflight_succeeded = False

    async def preflight(self) -> dict[str, object]:
        node = self.node_resolver(self.node_bin)
        runner = self.runner_path.resolve()
        if not node:
            return {"ready": False, "reason_code": "executable_missing"}
        if not runner.is_file():
            return {"ready": False, "reason_code": "runtime_missing"}
        return await self._preflight_status(node, runner)

    async def __call__(self, request: BrowserToolRequest) -> BrowserToolResult:
        try:
            action, arguments = _validated_action(request)
        except _ModelRequired as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="model_required",
                message=str(exc),
            )
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

        node = self.node_resolver(self.node_bin)
        runner = self.runner_path.resolve()
        if not node or not runner.is_file():
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message="Stagehand router runtime is unavailable",
            )
        if not self._preflight_succeeded:
            preflight_failure = await self._preflight(node, runner)
            if preflight_failure is not None:
                return preflight_failure
            self._preflight_succeeded = True

        gateway_runner: Any | None = None
        request_path: Path | None = None
        process: Any | None = None
        try:
            gateway_runner, local_ws = await self.gateway_starter(
                upstream_http=_manager_cdp_endpoint(request),
                headers={"Authorization": f"Bearer {request.context.capability_token}"},
            )
            browser_ws = _validated_browser_endpoint(
                await self.endpoint_reader(local_ws),
                local_ws=local_ws,
            )
            request_path = _write_request_file(
                action,
                arguments,
                request=request,
                cdp_url=browser_ws,
                connect_timeout_ms=self.connect_timeout_ms,
            )
            process = await self.process_launcher(
                node,
                str(runner),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_stagehand_env(request_path),
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES + 1,
            )
            stdout, stderr, returncode = await _run_process_bounded(
                process,
                timeout_seconds=self.timeout_seconds,
            )
            if returncode != 0:
                return _failed_command_result(stdout, stderr)
            return _result_from_stdout(stdout, request=request, expected_action=action)
        except _OutputTooLarge as exc:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied",
                message=str(exc),
            )
        except asyncio.TimeoutError:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="transient_timeout",
                message="Stagehand router timed out",
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
        except Exception as exc:  # noqa: BLE001 - adapter boundary fails closed
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied",
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

    async def _preflight(self, node: str, runner: Path) -> BrowserToolResult | None:
        status = await self._preflight_status(node, runner)
        if status["ready"] is True:
            return None
        return BrowserToolResult(
            outcome="failed",
            classification="tool_unavailable",
            message=f"Stagehand preflight failed: {status['reason_code']}",
        )

    async def _preflight_status(self, node: str, runner: Path) -> dict[str, object]:
        process: Any | None = None
        try:
            process = await self.process_launcher(
                node,
                str(runner),
                "--preflight",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_stagehand_env(None),
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES + 1,
            )
            stdout, stderr, returncode = await _run_process_bounded(
                process,
                timeout_seconds=min(self.timeout_seconds, 10.0),
            )
            if returncode != 0:
                raise _PolicyDenied(_bounded_error(stderr, "Stagehand preflight failed"))
            payload = _parse_exact_json_object(stdout)
            _validate_preflight_payload(payload)
            return {"ready": True, "reason_code": "ready"}
        except asyncio.CancelledError:
            await _kill_process(process)
            raise
        except asyncio.TimeoutError:
            await _kill_process(process)
            return {"ready": False, "reason_code": "timeout"}
        except _PolicyDenied as exc:
            await _kill_process(process)
            return {"ready": False, "reason_code": _stagehand_preflight_reason_code(str(exc))}
        except ValueError:
            await _kill_process(process)
            return {"ready": False, "reason_code": "malformed_output"}
        except _OutputTooLarge:
            await _kill_process(process)
            return {"ready": False, "reason_code": "malformed_output"}
        except Exception:  # noqa: BLE001 - public readiness fails closed
            await _kill_process(process)
            return {"ready": False, "reason_code": "probe_error"}


def _stagehand_preflight_reason_code(message: str) -> str:
    lowered = message.lower()
    if "incompatible" in lowered or "version" in lowered or "node" in lowered or "cdpurl" in lowered:
        return "incompatible_runtime"
    return "command_failed"


async def stagehand_router_adapter(request: BrowserToolRequest) -> BrowserToolResult:
    return await StagehandRouterAdapter()(request)


class _UnsupportedAction(ValueError):
    pass


class _ModelRequired(ValueError):
    pass


class _OriginDenied(ValueError):
    pass


class _PolicyDenied(ValueError):
    pass


class _OutputTooLarge(RuntimeError):
    pass


def _validated_action(request: BrowserToolRequest) -> tuple[str, dict[str, Any]]:
    action = str(request.action or "").strip().lower()
    if action in SEMANTIC_ACTIONS:
        raise _ModelRequired("Stagehand semantic actions require explicit model configuration")
    if action not in DETERMINISTIC_ACTIONS:
        raise _UnsupportedAction("unsupported Stagehand action")
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


def _validated_browser_endpoint(browser_ws: str, *, local_ws: str) -> str:
    endpoint = urlparse(str(browser_ws or ""))
    base = urlparse(str(local_ws or ""))
    nonce_path = base.path.rstrip("/")
    if (
        endpoint.scheme != "ws"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.netloc != base.netloc
        or not nonce_path
        or not endpoint.path.startswith(f"{nonce_path}/")
        or endpoint.params
        or endpoint.query
        or endpoint.fragment
    ):
        raise _PolicyDenied("Stagehand browser endpoint is outside the nonce gateway")
    return browser_ws


def _write_request_file(
    action: str,
    arguments: dict[str, Any],
    *,
    request: BrowserToolRequest,
    cdp_url: str,
    connect_timeout_ms: int,
) -> Path:
    payload = json.dumps(
        {
            "action": action,
            "arguments": arguments,
            "allowed_origins": list(request.context.allowed_origins),
            "cdpUrl": cdp_url,
            "connectTimeoutMs": connect_timeout_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise BrowserCtlError("request_too_large", "Stagehand request is too large")
    fd, raw_path = tempfile.mkstemp(prefix="cbm-stagehand-router-", suffix=".json")
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


def _stagehand_env(request_path: Path | None) -> dict[str, str]:
    env = {key: os.environ[key] for key in OPERATIONAL_ENV_KEYS if key in os.environ}
    if request_path is not None:
        env[REQUEST_FILE_ENV] = str(request_path)
    return env


def _validate_preflight_payload(payload: dict[str, Any]) -> None:
    if payload.get("ok") is not True:
        raise _PolicyDenied("Stagehand preflight did not report ok=true")
    if payload.get("mode") != "preflight":
        raise _PolicyDenied("Stagehand preflight mode is incompatible")
    if payload.get("stagehandVersion") != SUPPORTED_STAGEHAND_VERSION:
        raise _PolicyDenied("Stagehand package version is incompatible")
    if payload.get("supportsCdpUrl") is not True:
        raise _PolicyDenied("Stagehand runtime does not expose cdpUrl support")
    node = str(payload.get("node") or "")
    if not _node_version_is_compatible(node):
        raise _PolicyDenied("Node version is incompatible")


def _node_version_is_compatible(version: str) -> bool:
    parts = str(version or "").strip().split(".")
    if len(parts) < 3 or not all(part.isdigit() for part in parts[:3]):
        return False
    major, minor, patch = (int(part) for part in parts[:3])
    if major == 20:
        return (minor, patch) >= (19, 0)
    if major == 21:
        return False
    if major == 22:
        return (minor, patch) >= (12, 0)
    return major >= 23


async def _run_process_bounded(
    process: Any,
    *,
    timeout_seconds: float,
) -> tuple[bytes, bytes, int]:
    async def run() -> tuple[bytes, bytes, int]:
        stdout_reader = getattr(process, "stdout", None)
        stderr_reader = getattr(process, "stderr", None)
        if stdout_reader is None or stderr_reader is None:
            raise RuntimeError("Stagehand streams are unavailable")
        stdout_task = asyncio.create_task(
            _read_limited(stdout_reader, MAX_OUTPUT_BYTES, "Stagehand output")
        )
        stderr_task = asyncio.create_task(
            _read_limited(stderr_reader, MAX_STDERR_BYTES, "Stagehand stderr")
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
    if getattr(process, "_cbm_kill_requested", False):
        wait = getattr(process, "wait", None)
        if callable(wait):
            try:
                await wait()
            except Exception:
                pass
        return
    if getattr(process, "returncode", None) is not None and getattr(process, "killed", False):
        return
    killed = False
    pid = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            setattr(process, "_cbm_kill_requested", True)
            killed = True
        except ProcessLookupError:
            setattr(process, "_cbm_kill_requested", True)
            killed = True
        except Exception:
            killed = False
    if not killed:
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
                setattr(process, "_cbm_kill_requested", True)
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


def _failed_command_result(stdout: bytes, stderr: bytes) -> BrowserToolResult:
    try:
        payload = _parse_json_output(stdout)
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("ok") is False:
        return _error_payload_result(payload)
    return BrowserToolResult(
        outcome="failed",
        classification="policy_denied",
        message=_bounded_error(stderr, "Stagehand action failed"),
    )


def _result_from_stdout(
    stdout: bytes,
    *,
    request: BrowserToolRequest,
    expected_action: str,
) -> BrowserToolResult:
    payload = _parse_json_output(stdout)
    if payload.get("ok") is False:
        return _error_payload_result(payload)
    try:
        safe = _safe_payload(payload, request=request, expected_action=expected_action)
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
    except _SecondBrowserAttempt as exc:
        return BrowserToolResult(
            outcome="failed",
            classification="second_browser_attempt",
            message=str(exc),
        )
    return BrowserToolResult(outcome="succeeded", classification="ok", payload=safe)


class _SecondBrowserAttempt(ValueError):
    pass


def _parse_json_output(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", "replace")
    lines = [item for item in text.splitlines() if item.strip()]
    if not lines:
        raise ValueError("Stagehand did not return bounded JSON")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("Stagehand final output line is not JSON") from exc
    if isinstance(value, dict):
        return value
    raise ValueError("Stagehand did not return bounded JSON")


def _parse_exact_json_object(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", "replace")
    stripped = text.strip()
    if not stripped:
        raise ValueError("Stagehand preflight did not return JSON")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("Stagehand preflight output is not JSON") from exc
    if stripped[end:].strip():
        raise ValueError("Stagehand preflight returned multiple records")
    if isinstance(value, dict):
        return value
    raise ValueError("Stagehand preflight did not return one JSON object")


def _error_payload_result(payload: dict[str, Any]) -> BrowserToolResult:
    classification = str(payload.get("classification") or "").strip()
    if classification == "origin_denied":
        mapped = "origin_denied"
    elif classification == "transient_timeout":
        mapped = "transient_timeout"
    elif classification == "second_browser_attempt":
        mapped = "second_browser_attempt"
    elif classification == "model_required":
        mapped = "model_required"
    else:
        mapped = "policy_denied"
    return BrowserToolResult(
        outcome="failed",
        classification=mapped,
        message=redact_error_message(str(payload.get("error") or mapped))[:MAX_STDERR_CHARS],
    )


def _safe_payload(
    payload: dict[str, Any],
    *,
    request: BrowserToolRequest,
    expected_action: str,
) -> dict[str, Any]:
    if payload.get("ok") is not True:
        raise _PolicyDenied("Stagehand result did not report ok=true")
    if payload.get("connection_mode") != "existing-cdp":
        raise _SecondBrowserAttempt("Stagehand connection mode drifted from existing CDP")
    if payload.get("used_model") is not False:
        raise _PolicyDenied("Stagehand result used a model")
    if payload.get("action") != expected_action:
        raise _PolicyDenied("Stagehand result action mismatch")
    final_url = str(payload.get("url") or "").strip()
    if not final_url:
        raise _PolicyDenied("Stagehand result missing final URL")
    _require_allowed_url(final_url, request)
    safe: dict[str, Any] = {
        "action": expected_action,
        "connection_mode": "existing-cdp",
        "used_model": False,
        "url": redact_url(final_url),
    }
    if "selector" in payload:
        selector = payload.get("selector")
        safe["selector"] = validate_selector(str(selector)) if selector is not None else None
    if "title" in payload and expected_action == "inspect":
        safe["title"] = redact_text(str(payload.get("title") or ""))[:200]
    if "text" in payload:
        safe["text"] = redact_text(str(payload.get("text") or ""))[:MAX_TEXT_CHARS]
    return safe


def _bounded_error(stderr: bytes, fallback: str) -> str:
    raw = stderr.decode("utf-8", "replace").strip() or fallback
    return redact_error_message(raw)[:MAX_STDERR_CHARS]
