#!/usr/bin/env python3
"""Run-scoped Unbrowse router adapter for Manager-owned browser profiles."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, unquote, urlparse

from backend.origin_policy import is_top_level_origin_allowed, normalize_origin
from scripts.browser_tool_router import BrowserToolRequest, BrowserToolResult
from scripts.cbm_browser_ctl import (
    BrowserCtlError,
    redact_error_message,
    redact_url,
    validate_http_url,
)
from scripts.unbrowse_managed_helper import start_cdp_gateway


MAX_OUTPUT_BYTES = 65_536
MAX_STDERR_BYTES = 16_384
MAX_STDERR_CHARS = 700
READ_CHUNK_BYTES = 8_192
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_MS = 120_000
SUPPORTED_ACTION = "navigate"
SUPPORTED_VERSION = "11.2.0-preview.3"
SUPPORTED_BUILD_SHA = "0c552cf6f8b0"
SUCCESS_SUBCOMMAND = "act go"
SUCCESS_OP_KIND = "go"
MAX_SESSION_ID_CHARS = 128
MAX_TARGET_ID_CHARS = 256
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
UNBROWSE_NON_SECRET_PATH_ENV_KEYS = ("UNBROWSE_CONFIG",)
SENSITIVE_URL_TOKENS = (
    "access_token",
    "api_key",
    "auth",
    "code",
    "id_token",
    "password",
    "secret",
    "session",
    "state",
    "token",
)

GatewayStarter = Callable[..., Awaitable[tuple[Any, str]]]
ExecutableResolver = Callable[[str], str | None]
ProcessLauncher = Callable[..., Awaitable[Any]]


class UnbrowseRouterAdapter:
    """Attach Unbrowse to the exact Manager profile through a nonce CDP relay."""

    def __init__(
        self,
        *,
        executable: str = "unbrowse",
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
        self._preflight_succeeded = False

    async def __call__(self, request: BrowserToolRequest) -> BrowserToolResult:
        try:
            safe_url = _validated_navigation_url(request)
        except _SecretBoundaryViolation as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="secret_boundary_violation",
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

        binary = self.executable_resolver(self.executable)
        if not binary:
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message="unbrowse executable is unavailable",
            )
        if not self._preflight_succeeded:
            preflight_failure = await self._preflight(binary)
            if preflight_failure is not None:
                return preflight_failure
            self._preflight_succeeded = True

        gateway_runner: Any | None = None
        process: Any | None = None
        try:
            gateway_runner, local_ws = await self.gateway_starter(
                upstream_http=_manager_cdp_endpoint(request),
                headers={"Authorization": f"Bearer {request.context.capability_token}"},
            )
            timeout_ms = _bounded_timeout_ms(self.timeout_seconds)
            process = await self.process_launcher(
                binary,
                "breath",
                "go",
                safe_url,
                "--ws",
                local_ws,
                "--timeout",
                str(timeout_ms),
                "--json",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_unbrowse_env(),
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES + 1,
            )
            stdout, stderr, returncode = await _run_process_bounded(
                process,
                timeout_seconds=self.timeout_seconds,
            )
            if returncode != 0:
                return _failed_command_result(stdout, stderr)
            return _result_from_stdout(
                stdout,
                request=request,
                requested_url=safe_url,
                local_ws=local_ws,
            )
        except _OutputTooLarge as exc:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied" if self._preflight_succeeded else "tool_unavailable",
                message=str(exc),
            )
        except asyncio.TimeoutError:
            await _kill_process(process)
            return BrowserToolResult(
                outcome="failed",
                classification="transient_timeout",
                message="unbrowse timed out",
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
        except Exception as exc:  # noqa: BLE001 - adapter boundary is terminal-safe
            return BrowserToolResult(
                outcome="failed",
                classification="policy_denied" if self._preflight_succeeded else "tool_unavailable",
                message=redact_error_message(str(exc))[:MAX_STDERR_CHARS],
            )
        finally:
            if gateway_runner is not None:
                await _cleanup_gateway(gateway_runner)

    async def _preflight(self, binary: str) -> BrowserToolResult | None:
        try:
            version_payload = await self._run_preflight_probe(
                binary,
                ("eval", "version", "--json"),
                accepted_returncodes={0},
                fallback_error="unbrowse version preflight failed",
            )
            _validate_version_preflight_payload(version_payload)
            help_payload = await self._run_preflight_probe(
                binary,
                ("breath", "go", "--help"),
                accepted_returncodes={0, 64},
                fallback_error="unbrowse command preflight failed",
            )
            _validate_help_preflight_payload(help_payload)
            return None
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, _OutputTooLarge, ValueError, _PolicyDenied) as exc:
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message=redact_error_message(str(exc))[:MAX_STDERR_CHARS],
            )
        except Exception as exc:  # noqa: BLE001 - preflight fails closed
            return BrowserToolResult(
                outcome="failed",
                classification="tool_unavailable",
                message=redact_error_message(str(exc))[:MAX_STDERR_CHARS],
            )

    async def _run_preflight_probe(
        self,
        binary: str,
        args: tuple[str, ...],
        *,
        accepted_returncodes: set[int],
        fallback_error: str,
    ) -> dict[str, Any]:
        process: Any | None = None
        try:
            process = await self.process_launcher(
                binary,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_unbrowse_env(),
                start_new_session=True,
                limit=MAX_OUTPUT_BYTES + 1,
            )
            stdout, stderr, returncode = await _run_process_bounded(
                process,
                timeout_seconds=min(self.timeout_seconds, 10.0),
            )
            if returncode not in accepted_returncodes:
                raise _PolicyDenied(_bounded_error(stderr, fallback_error))
            return _parse_json_output(stdout)
        except asyncio.CancelledError:
            await _kill_process(process)
            raise
        except Exception:
            await _kill_process(process)
            raise


async def unbrowse_router_adapter(request: BrowserToolRequest) -> BrowserToolResult:
    return await UnbrowseRouterAdapter()(request)


class _UnsupportedAction(ValueError):
    pass


class _SecretBoundaryViolation(ValueError):
    pass


class _OriginDenied(ValueError):
    pass


class _PolicyDenied(ValueError):
    pass


class _OutputTooLarge(RuntimeError):
    pass


def _validated_navigation_url(request: BrowserToolRequest) -> str:
    action = str(request.action or "").strip().lower()
    if action != SUPPORTED_ACTION:
        raise _UnsupportedAction("unsupported unbrowse action")
    raw_args = request.arguments if isinstance(request.arguments, dict) else {}
    raw_url = str(raw_args.get("url") or "")
    if _url_contains_sensitive_argv_material(raw_url):
        raise _SecretBoundaryViolation("sensitive navigation URL requires a non-argv transport")
    safe_url = validate_http_url(raw_url)
    _require_allowed_url(safe_url, request)
    return safe_url


def _url_contains_sensitive_argv_material(url: str) -> bool:
    raw = str(url or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.fragment:
        return True
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_text = unquote(str(key or "")).lower()
        value_text = unquote(str(value or "")).lower()
        if any(token in key_text or token in value_text for token in SENSITIVE_URL_TOKENS):
            return True
    return False


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


def _bounded_timeout_ms(timeout_seconds: float) -> int:
    if timeout_seconds <= 0:
        return 1
    return max(1, min(MAX_TIMEOUT_MS, int(timeout_seconds * 1000)))


def _unbrowse_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in OPERATIONAL_ENV_KEYS if key in os.environ}
    for key in UNBROWSE_NON_SECRET_PATH_ENV_KEYS:
        value = os.environ.get(key)
        if value and _is_non_secret_path_value(value):
            env[key] = value
    return env


def _is_non_secret_path_value(value: str) -> bool:
    text = value.strip()
    if not text or any(ch in text for ch in "\r\n\0"):
        return False
    return text.startswith(("/", "./", "../", "~"))


def _validate_version_preflight_payload(payload: dict[str, Any]) -> None:
    if payload.get("ok") is not True:
        raise _PolicyDenied("unbrowse version preflight did not report ok=true")
    if payload.get("subcommand") != "eval version":
        raise _PolicyDenied("unbrowse version preflight subcommand is incompatible")
    if payload.get("op_kind") != "eval:version":
        raise _PolicyDenied("unbrowse version preflight op_kind is incompatible")
    if payload.get("version") != SUPPORTED_VERSION:
        raise _PolicyDenied("unbrowse version is incompatible")
    if payload.get("buildSha") != SUPPORTED_BUILD_SHA:
        raise _PolicyDenied("unbrowse build is incompatible")


def _validate_help_preflight_payload(payload: dict[str, Any]) -> None:
    if payload.get("help") is not True:
        raise _PolicyDenied("unbrowse command preflight did not report help=true")
    if payload.get("subcommand") != "breath go":
        raise _PolicyDenied("unbrowse command preflight subcommand is incompatible")
    if payload.get("op_kind") != "breath:navigate":
        raise _PolicyDenied("unbrowse command preflight op_kind is incompatible")
    flags = payload.get("flags")
    if not isinstance(flags, list):
        raise _PolicyDenied("unbrowse command preflight flags are unavailable")
    ws_flags = [
        item
        for item in flags
        if isinstance(item, dict) and item.get("name") == "--ws"
    ]
    if len(ws_flags) != 1 or ws_flags[0].get("value_expected") is not True:
        raise _PolicyDenied("unbrowse command contract lacks exactly one breath go --ws value flag")


async def _run_process_bounded(
    process: Any,
    *,
    timeout_seconds: float,
) -> tuple[bytes, bytes, int]:
    async def run() -> tuple[bytes, bytes, int]:
        stdout_reader = getattr(process, "stdout", None)
        stderr_reader = getattr(process, "stderr", None)
        if stdout_reader is None or stderr_reader is None:
            raise RuntimeError("unbrowse streams are unavailable")
        stdout_task = asyncio.create_task(
            _read_limited(stdout_reader, MAX_OUTPUT_BYTES, "unbrowse output")
        )
        stderr_task = asyncio.create_task(
            _read_limited(stderr_reader, MAX_STDERR_BYTES, "unbrowse stderr")
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


def _failed_command_result(stdout: bytes, stderr: bytes) -> BrowserToolResult:
    try:
        payload = _parse_json_output(stdout)
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("ok") is False:
        return _error_payload_result(payload)
    message = _bounded_error(stderr, "unbrowse action failed")
    return BrowserToolResult(
        outcome="failed",
        classification="policy_denied",
        message=message,
    )


def _result_from_stdout(
    stdout: bytes,
    *,
    request: BrowserToolRequest,
    requested_url: str,
    local_ws: str,
) -> BrowserToolResult:
    if len(stdout) > MAX_OUTPUT_BYTES:
        return BrowserToolResult(
            outcome="failed",
            classification="policy_denied",
            message="unbrowse output exceeds size bound",
        )
    payload = _parse_json_output(stdout)
    if _signals_second_browser(payload):
        return BrowserToolResult(
            outcome="failed",
            classification="second_browser_attempt",
            message="unbrowse signaled disallowed browser launch",
        )
    if payload.get("ok") is False:
        return _error_payload_result(payload)
    try:
        safe = _safe_payload(
            payload,
            request=request,
            requested_url=requested_url,
            local_ws=local_ws,
        )
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
    lines = [item for item in text.splitlines() if item.strip()]
    if not lines:
        raise ValueError("unbrowse did not return bounded JSON")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("unbrowse final output line is not JSON") from exc
    if isinstance(value, dict):
        return value
    raise ValueError("unbrowse did not return bounded JSON")


def _signals_second_browser(payload: dict[str, Any]) -> bool:
    for key in (
        "opened_second_browser",
        "second_browser",
        "secondBrowser",
        "spawned_browser",
        "launched_browser",
    ):
        if payload.get(key) is True:
            return True
    code = str(payload.get("code") or payload.get("classification") or "").lower()
    return code in {"second_browser_attempt", "second-browser-attempt"}


def _error_payload_result(payload: dict[str, Any]) -> BrowserToolResult:
    raw_code = _exact_error_code(payload)
    if raw_code == "AUTH_REQUIRED":
        classification = "auth_required"
    elif raw_code == "SECOND_BROWSER_ATTEMPT":
        classification = "second_browser_attempt"
    elif raw_code == "TRANSIENT_TIMEOUT":
        classification = "transient_timeout"
    else:
        classification = "policy_denied"
    return BrowserToolResult(
        outcome="failed",
        classification=classification,
        message=redact_error_message(str(payload.get("error") or payload.get("message") or classification)),
    )


def _exact_error_code(payload: dict[str, Any]) -> str:
    for key in ("code", "name"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _safe_payload(
    payload: dict[str, Any],
    *,
    request: BrowserToolRequest,
    requested_url: str,
    local_ws: str,
) -> dict[str, Any]:
    if payload.get("ok") is not True:
        raise _PolicyDenied("unbrowse result did not report ok=true")
    if payload.get("subcommand") != SUCCESS_SUBCOMMAND:
        raise _PolicyDenied("unbrowse result subcommand is not recognized")
    if payload.get("op_kind") != SUCCESS_OP_KIND:
        raise _PolicyDenied("unbrowse result op_kind is not recognized")
    session_id = _bounded_nonempty_top_level_string(
        payload,
        "session_id",
        MAX_SESSION_ID_CHARS,
    )
    target_id = _bounded_nonempty_top_level_string(
        payload,
        "target_id",
        MAX_TARGET_ID_CHARS,
    )
    if not session_id or not target_id:
        raise _PolicyDenied("unbrowse result missing attachment session evidence")
    if payload.get("chrome_ws_url") != local_ws:
        raise _PolicyDenied("unbrowse result missing exact websocket attachment evidence")
    final_url = _bounded_nonempty_top_level_string(payload, "url", 2_048)
    if not final_url:
        raise _PolicyDenied("unbrowse result missing final URL")
    _require_allowed_url(final_url, request)
    if _normalized_origin(final_url) != _normalized_origin(requested_url):
        raise _PolicyDenied("unbrowse result URL origin changed")
    return {
        "url": redact_url(final_url),
        "attached": True,
    }


def _bounded_nonempty_top_level_string(
    payload: dict[str, Any],
    key: str,
    max_chars: int,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if not stripped or len(stripped) > max_chars:
        return ""
    return stripped


def _origin(value: str) -> str:
    parsed = urlparse(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalized_origin(value: str) -> str:
    parsed = urlparse(value)
    return normalize_origin(f"{parsed.scheme}://{parsed.netloc}")


def _bounded_error(stderr: bytes, fallback: str) -> str:
    raw = stderr.decode("utf-8", "replace").strip() or fallback
    return redact_error_message(raw)[:MAX_STDERR_CHARS]


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
    if isinstance(pid, int) and pid > 0:
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
