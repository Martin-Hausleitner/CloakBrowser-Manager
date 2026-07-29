#!/usr/bin/env python3
"""Host worker that executes Manager task runs through ACPX/ACP.

The Manager owns identity, policy, browser leases, task state, and audit. This
worker claims only ``harness=acpx`` runs, keeps the claim alive, executes one
pinned ACPX named session, streams bounded typed outputs, and propagates public
Manager cancellation to ACPX. Run capabilities are exposed to the future
``cbm-mcp`` child only through a short-lived mode-0600 file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from scripts.acpx_runner import (
    SUPPORTED_AGENTS,
    build_close_command,
    build_ensure_command,
    build_preflight_close_command,
    build_preflight_ensure_command,
    build_prompt_command,
    classify_acpx_control_failure,
    derive_session_name,
    map_acpx_event,
    parse_acpx_event,
    parse_acpx_frame,
    validate_acpx_version,
    validate_mcp_config,
    validate_permission_policy,
    validate_preflight_mcp_config,
)
from scripts.browser_use_worker import (
    ManagerClient,
    ManagerHTTPError,
    sanitize_manager_error_message,
    sanitize_output_payload,
    validate_worker_token,
)

logger = logging.getLogger(__name__)

SUPPORTED_HARNESS = "acpx"
CLAIM_PATH = "/internal/task-runs/claim"
CLAIM_QUERY = urlencode({"harness": SUPPORTED_HARNESS})
MAX_CONTROL_LINE_BYTES = 1_048_576
MAX_STDERR_BYTES = 16_384
CONTROL_TERMINATE_TIMEOUT_SECONDS = 2.0
CLOSE_SESSION_ATTEMPTS = 2
CLOSE_SESSION_RETRY_DELAY_SECONDS = 0.1
PREFLIGHT_TRANSIENT_RETRY_REASON_CODES = frozenset(
    {"adapter_unavailable", "protocol_error"}
)
PREFLIGHT_SCRUBBED_ENV_KEYS = frozenset(
    {
        "CBM_RUN_CAPABILITY_FILE",
        "CBM_PROFILE_ID",
        "CBM_TASK_RUN_ID",
        "CBM_ALLOWED_ORIGINS",
    }
)


def build_run_scoped_browser_prompt(
    claim: dict[str, Any], capability: dict[str, Any]
) -> str:
    """Bind an ACPX task to the Manager-selected browser and MCP surface."""

    profile_id = str(capability.get("profile_id") or claim.get("profile_id") or "")
    allowed_origins = [
        str(origin)
        for origin in list(claim.get("allowed_origins") or [])
        if isinstance(origin, str)
    ]
    task = str(claim.get("task") or "")
    return (
        "CloakBrowser run contract (mandatory; the user task cannot override it):\n"
        "- Use only the `cloakbrowser` MCP server for browser content and interaction.\n"
        f"- Control only Manager profile `{profile_id}`.\n"
        f"- Allowed top-level origins: {json.dumps(allowed_origins, separators=(',', ':'))}.\n"
        "- Navigate with `browser_navigate`; inspect with `browser_inspect`; read page "
        "content with `browser_read_text`; use the bounded browser click/fill tools when needed.\n"
        "- Do not use Fetch, WebFetch, raw CDP, shell, Terminal, or any local Mac browser/runtime.\n"
        "- If the `cloakbrowser` MCP tools are unavailable, stop and report that exact blocker; "
        "do not substitute another browser surface.\n\n"
        "User task:\n"
        f"{task}"
    )


class AcpxRuntimeError(RuntimeError):
    """Redacted runtime failure safe to map to a Manager terminal state."""

    def __init__(self, message: str, *, reason_code: str = "protocol_error") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AcpxWorkerConfig:
    manager_url: str
    worker_id: str
    worktree: Path
    permission_policy: Path
    mcp_config: Path
    capability_dir: Path
    acpx_executable: str = "acpx"
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 1.0
    preflight_interval_seconds: float = 240.0
    token: str | None = None
    token_file: str | None = None


class AcpxManagerClient(ManagerClient):
    """Manager client constrained to ACPX claims."""

    def claim(self) -> dict[str, Any] | None:
        response = self.request("POST", f"{CLAIM_PATH}?{CLAIM_QUERY}")
        if response.status_code == 204:
            return None
        return response.json()

    def report_preflight(self, *, agent: str, ready: bool, reason_code: str) -> None:
        self.request(
            "POST",
            "/internal/task-harnesses/acpx/preflights",
            json={"agent": agent, "ready": ready, "reason_code": reason_code},
        )


class AcpxRuntime:
    """Pinned ACPX subprocess boundary with strict NDJSON output."""

    def __init__(self, config: AcpxWorkerConfig) -> None:
        self.config = config

    async def _run_control(
        self,
        command: list[str],
        *,
        timeout: float = 30.0,
        environment: dict[str, str] | None = None,
        scrub_environment_keys: frozenset[str] | None = None,
    ) -> bytes:
        child_env = os.environ.copy()
        if environment:
            child_env.update(environment)
        for key in scrub_environment_keys or frozenset():
            child_env.pop(key, None)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise AcpxRuntimeError(
                "ACPX adapter unavailable",
                reason_code="adapter_unavailable",
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await _terminate_process(process)
            raise AcpxRuntimeError("ACPX control command timed out") from exc
        except asyncio.CancelledError:
            await _terminate_process(process)
            raise
        if process.returncode != 0:
            raw = stdout + b"\n" + stderr
            message = _safe_process_error(stderr or stdout)
            raise AcpxRuntimeError(
                message or "ACPX control command failed",
                reason_code=classify_acpx_control_failure(raw),
            )
        return stdout

    async def validate_version(self) -> None:
        stdout = await self._run_control([self.config.acpx_executable, "--version"])
        try:
            validate_acpx_version(stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise AcpxRuntimeError(str(exc), reason_code="version_mismatch") from exc

    async def ensure_session(
        self,
        *,
        cwd: Path,
        agent: str,
        session_name: str,
        environment: dict[str, str],
    ) -> None:
        command = build_ensure_command(
            executable=self.config.acpx_executable,
            cwd=cwd,
            agent=agent,
            session_name=session_name,
            permission_policy=self.config.permission_policy,
            mcp_config=self.config.mcp_config,
        )
        await self._run_control(command, timeout=90.0, environment=environment)

    async def close_session(
        self,
        *,
        cwd: Path,
        agent: str,
        session_name: str,
    ) -> None:
        command = build_close_command(
            executable=self.config.acpx_executable,
            cwd=cwd,
            agent=agent,
            session_name=session_name,
        )
        last_error: AcpxRuntimeError | None = None
        for attempt in range(CLOSE_SESSION_ATTEMPTS):
            try:
                await self._run_control(command, timeout=10.0)
                return
            except AcpxRuntimeError as exc:
                last_error = exc
                if attempt + 1 >= CLOSE_SESSION_ATTEMPTS:
                    break
                await asyncio.sleep(CLOSE_SESSION_RETRY_DELAY_SECONDS)
        raise last_error or AcpxRuntimeError("ACPX close command failed")

    async def preflight_agent(
        self,
        *,
        cwd: Path,
        agent: str,
        session_name: str,
        environment: dict[str, str],
        mcp_config: Path,
    ) -> dict[str, Any]:
        ensure_command = build_preflight_ensure_command(
            executable=self.config.acpx_executable,
            cwd=cwd,
            agent=agent,
            session_name=session_name,
            permission_policy=self.config.permission_policy,
            mcp_config=mcp_config,
        )
        try:
            await self._run_control(
                ensure_command,
                environment=environment,
                scrub_environment_keys=PREFLIGHT_SCRUBBED_ENV_KEYS,
            )
        except AcpxRuntimeError as exc:
            return {"ready": False, "reason_code": exc.reason_code}
        close_command = build_preflight_close_command(
            executable=self.config.acpx_executable,
            cwd=cwd,
            agent=agent,
            session_name=session_name,
            mcp_config=mcp_config,
        )
        for attempt in range(CLOSE_SESSION_ATTEMPTS):
            try:
                await self._run_control(
                    close_command,
                    timeout=10.0,
                    environment=environment,
                    scrub_environment_keys=PREFLIGHT_SCRUBBED_ENV_KEYS,
                )
                return {"ready": True, "reason_code": "ok"}
            except AcpxRuntimeError:
                if attempt + 1 >= CLOSE_SESSION_ATTEMPTS:
                    break
                await asyncio.sleep(CLOSE_SESSION_RETRY_DELAY_SECONDS)
        return {"ready": False, "reason_code": "protocol_error"}

    async def cancel(self, *, cwd: Path, agent: str, session_name: str) -> None:
        command = [
            self.config.acpx_executable,
            "--cwd",
            str(cwd),
            "--format",
            "json",
            "--json-strict",
            agent,
            "cancel",
            "-s",
            session_name,
        ]
        try:
            await self._run_control(command, timeout=10.0)
        except Exception:  # noqa: BLE001 - cancellation remains best effort
            logger.warning("ACPX cancellation command failed")

    async def run_prompt(
        self,
        *,
        cwd: Path,
        agent: str,
        session_name: str,
        prompt: str,
        timeout_seconds: float,
        environment: dict[str, str],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        cancel_event: asyncio.Event,
    ) -> str | None:
        command = build_prompt_command(
            executable=self.config.acpx_executable,
            cwd=cwd,
            agent=agent,
            session_name=session_name,
            permission_policy=self.config.permission_policy,
            mcp_config=self.config.mcp_config,
            timeout_seconds=max(1, int(timeout_seconds)),
        )
        child_env = os.environ.copy()
        child_env.update(environment)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
            limit=MAX_CONTROL_LINE_BYTES + 1,
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            await process.wait()
            raise AcpxRuntimeError("ACPX subprocess pipes unavailable")
        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        last_summary: str | None = None
        protocol_error: str | None = None
        prompt_request_id: str | None = None
        assistant_chunks: list[str] = []
        jsonrpc_seq = 0

        async def drain_stderr() -> bytes:
            if process.stderr is None:
                return b""
            retained = bytearray()
            while True:
                chunk = await process.stderr.read(4_096)
                if not chunk:
                    break
                remaining = MAX_STDERR_BYTES - len(retained)
                if remaining > 0:
                    retained.extend(chunk[:remaining])
            return bytes(retained)

        async def consume_stdout() -> None:
            nonlocal jsonrpc_seq, last_summary, prompt_request_id, protocol_error
            async for raw_line in process.stdout:
                frame_bytes = raw_line[:-1] if raw_line.endswith(b"\n") else raw_line
                if frame_bytes.endswith(b"\r"):
                    frame_bytes = frame_bytes[:-1]
                if len(frame_bytes) > MAX_CONTROL_LINE_BYTES:
                    raise AcpxRuntimeError("ACPX output line exceeds size bound")
                line = frame_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    raw = parse_acpx_frame(line)
                except ValueError as exc:
                    raise AcpxRuntimeError(str(exc)) from exc
                if raw.get("eventVersion") == 1:
                    event = parse_acpx_event(line)
                    mapped = map_acpx_event(event)
                    await emit(mapped)
                    if mapped.get("kind") == "summary":
                        last_summary = str(mapped.get("summary") or "") or last_summary
                    continue
                if raw.get("jsonrpc") != "2.0":
                    raise AcpxRuntimeError("ACPX emitted unsupported JSON envelope")

                error = raw.get("error")
                if isinstance(error, dict):
                    protocol_error = sanitize_manager_error_message(
                        str(error.get("message") or "ACPX protocol error")
                    )
                    continue

                if raw.get("method") == "session/prompt" and "id" in raw:
                    prompt_request_id = str(raw.get("id"))
                    continue

                if raw.get("method") == "session/update":
                    jsonrpc_seq += 1
                    params = raw.get("params")
                    update = params.get("update") if isinstance(params, dict) else None
                    update_type = update.get("sessionUpdate") if isinstance(update, dict) else None
                    if update_type == "agent_message_chunk":
                        chunk = _jsonrpc_content_text(update.get("content"))
                        if chunk:
                            assistant_chunks.append(chunk)
                        continue
                    if update_type == "agent_thought_chunk":
                        # ACP thought streams are private, token-sized reasoning fragments.
                        # They are neither stable operator output nor safe standalone payloads.
                        continue
                    raw["_seq"] = jsonrpc_seq
                    mapped = map_acpx_event(raw)
                    await emit(mapped)
                    continue

                if _is_matching_prompt_result(raw, prompt_request_id):
                    text = "".join(assistant_chunks).strip()
                    if text:
                        jsonrpc_seq += 1
                        mapped = {
                            "idempotency_key": f"acpx-jsonrpc-{jsonrpc_seq}",
                            "kind": "summary",
                            "summary": text[:500],
                            "payload": {"text": text[:500]},
                        }
                        await emit(mapped)
                        last_summary = mapped["summary"]
                    continue

        stream_task = asyncio.create_task(consume_stdout())
        stderr_task = asyncio.create_task(drain_stderr())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {stream_task, cancel_task},
                timeout=max(1.0, float(timeout_seconds)),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await _terminate_process(process)
                raise AcpxRuntimeError("ACPX prompt timed out")
            if cancel_task in done and cancel_event.is_set() and not stream_task.done():
                try:
                    await asyncio.wait_for(stream_task, timeout=5.0)
                except asyncio.TimeoutError:
                    await _terminate_process(process)
                return None
            await stream_task
            return_code = await asyncio.wait_for(process.wait(), timeout=5.0)
            stderr = await stderr_task
            if protocol_error:
                raise AcpxRuntimeError(protocol_error)
            if return_code != 0:
                raise AcpxRuntimeError(_safe_process_error(stderr) or "ACPX prompt failed")
            if not last_summary:
                raise AcpxRuntimeError("ACPX prompt produced no terminal assistant output")
            return last_summary
        finally:
            cancel_task.cancel()
            if not stream_task.done():
                stream_task.cancel()
            if not stderr_task.done():
                stderr_task.cancel()
            if process.returncode is None:
                await _terminate_process(process)
            if not stderr_task.done():
                try:
                    await stderr_task
                except asyncio.CancelledError:
                    pass


def _jsonrpc_content_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return str(sanitize_output_payload(value["text"]))
        if isinstance(value.get("text"), str):
            return str(sanitize_output_payload(value["text"]))
    if isinstance(value, list):
        return "".join(_jsonrpc_content_text(item) for item in value)
    if isinstance(value, str):
        return str(sanitize_output_payload(value))
    return ""


def _is_matching_prompt_result(frame: dict[str, Any], prompt_request_id: str | None) -> bool:
    if frame.get("jsonrpc") != "2.0" or prompt_request_id is None:
        return False
    if str(frame.get("id")) != prompt_request_id:
        return False
    result = frame.get("result")
    return isinstance(result, dict) and isinstance(result.get("stopReason"), str)


def _safe_process_error(raw: bytes) -> str:
    text = raw[:MAX_STDERR_BYTES].decode("utf-8", errors="replace")
    clean = sanitize_manager_error_message(text)
    return clean or "ACPX runtime failed"


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError):
        return
    except OSError:
        try:
            process.terminate()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=CONTROL_TERMINATE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError):
            return
        except OSError:
            try:
                process.kill()
            except ProcessLookupError:
                return
        await process.wait()


class AcpxWorker:
    """Execute ACPX claims and mirror their lifecycle into Manager state."""

    def __init__(
        self,
        client: Any,
        config: AcpxWorkerConfig,
        *,
        runtime: Any | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.runtime = runtime or AcpxRuntime(config)

    async def _emit(self, run_id: str, output: dict[str, Any]) -> None:
        idempotency_key = str(output["idempotency_key"])
        try:
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind=str(output["kind"]),
                summary=str(output["summary"]),
                payload=dict(output.get("payload") or {}),
                idempotency_key=idempotency_key,
            )
        except ManagerHTTPError as exc:
            if exc.status_code != 422:
                raise
            logger.warning("Manager rejected one ACPX adapter output; emitting safe status")
            await asyncio.to_thread(
                self.client.output,
                run_id,
                kind="status",
                summary="ACP output omitted",
                payload={"status": "running", "detail": "ACP output omitted"},
                idempotency_key=idempotency_key,
            )

    async def _heartbeat_loop(
        self,
        *,
        run_id: str,
        cwd: Path,
        agent: str,
        session_name: str,
        stop_event: asyncio.Event,
        cancel_event: asyncio.Event,
        claim_lost: asyncio.Event,
    ) -> None:
        while not stop_event.is_set() and not cancel_event.is_set():
            try:
                body = await asyncio.to_thread(self.client.heartbeat, run_id) or {}
            except ManagerHTTPError as exc:
                if exc.status_code in {404, 410}:
                    claim_lost.set()
                    cancel_event.set()
                    await self.runtime.cancel(cwd=cwd, agent=agent, session_name=session_name)
                    return
                body = {}
            except Exception:  # noqa: BLE001 - retry transient heartbeat failures
                body = {}
            if body.get("cancel_requested"):
                cancel_event.set()
                await self.runtime.cancel(cwd=cwd, agent=agent, session_name=session_name)
                return
            interval = float(
                body.get("heartbeat_interval_seconds")
                or self.config.heartbeat_interval_seconds
            )
            interval = max(0.01, min(interval, 15.0))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    def _write_capability_file(self, token: str) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="cbm-run-", dir=self.config.capability_dir)
        path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(token)
                handle.flush()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            path.unlink(missing_ok=True)
            raise
        return path

    def _write_preflight_mcp_config(self) -> Path:
        fd, raw_path = tempfile.mkstemp(
            prefix="cbm-preflight-mcp-", suffix=".json", dir=self.config.capability_dir
        )
        path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
                json.dump({"mcpServers": []}, handle, separators=(",", ":"))
                handle.flush()
            return validate_preflight_mcp_config(path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            path.unlink(missing_ok=True)
            raise

    async def _preflight_agent_once(
        self,
        *,
        agent: str,
        session_name: str,
        environment: dict[str, str],
        mcp_config: Path,
    ) -> tuple[bool, str]:
        try:
            result = await self.runtime.preflight_agent(
                cwd=self.config.worktree,
                agent=agent,
                session_name=session_name,
                environment=environment,
                mcp_config=mcp_config,
            )
            ready = bool(result.get("ready"))
            reason_code = str(result.get("reason_code") or "protocol_error")
            return ready, reason_code
        except Exception:  # noqa: BLE001 - fail closed per adapter
            return False, "protocol_error"

    async def _preflight_agent_with_transient_retry(
        self,
        *,
        agent: str,
        session_name: str,
        environment: dict[str, str],
        mcp_config: Path,
    ) -> tuple[bool, str]:
        ready, reason_code = await self._preflight_agent_once(
            agent=agent,
            session_name=session_name,
            environment=environment,
            mcp_config=mcp_config,
        )
        if ready or reason_code not in PREFLIGHT_TRANSIENT_RETRY_REASON_CODES:
            return ready, reason_code
        retry_session_name = derive_session_name(f"retry:{session_name}")
        return await self._preflight_agent_once(
            agent=agent,
            session_name=retry_session_name,
            environment=environment,
            mcp_config=mcp_config,
        )

    async def refresh_preflights(self) -> None:
        """Probe every supported ACP adapter without exposing credentials."""
        mcp_config = self._write_preflight_mcp_config()
        environment = {
            "CBM_MANAGER_URL": self.config.manager_url,
        }
        try:
            try:
                await self.runtime.validate_version()
            except AcpxRuntimeError as exc:
                for agent in sorted(SUPPORTED_AGENTS):
                    await asyncio.to_thread(
                        self.client.report_preflight,
                        agent=agent,
                        ready=False,
                        reason_code=exc.reason_code,
                    )
                return
            except Exception:  # noqa: BLE001 - publish only redacted reason codes
                for agent in sorted(SUPPORTED_AGENTS):
                    await asyncio.to_thread(
                        self.client.report_preflight,
                        agent=agent,
                        ready=False,
                        reason_code="protocol_error",
                    )
                return

            for agent in sorted(SUPPORTED_AGENTS):
                session_name = derive_session_name(
                    f"preflight-v2:{self.config.worker_id}:{self.config.worktree}:{agent}"
                )
                ready, reason_code = await self._preflight_agent_with_transient_retry(
                    agent=agent,
                    session_name=session_name,
                    environment=environment,
                    mcp_config=mcp_config,
                )
                await asyncio.to_thread(
                    self.client.report_preflight,
                    agent=agent,
                    ready=ready,
                    reason_code=reason_code,
                )
        finally:
            mcp_config.unlink(missing_ok=True)

    async def execute_claim(self, claim: dict[str, Any]) -> dict[str, str]:
        run_id = str(claim.get("id") or "")
        agent = claim.get("agent")
        if (
            not run_id
            or claim.get("harness") != SUPPORTED_HARNESS
            or agent not in SUPPORTED_AGENTS
            or not claim.get("task_session_id")
        ):
            if run_id:
                await asyncio.to_thread(
                    self.client.fail,
                    run_id,
                    error_code="internal_error",
                    message="invalid ACPX claim",
                )
            return {"status": "failed"}

        session_name = derive_session_name(
            f"{claim['task_session_id']}:{run_id}"
        )
        stop_event = asyncio.Event()
        cancel_event = asyncio.Event()
        claim_lost = asyncio.Event()
        capability_file: Path | None = None
        capability_issued = False
        session_ensured = False
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            capability = await asyncio.to_thread(self.client.issue_capability, run_id)
            capability_issued = True
            token = validate_worker_token(str(capability.get("token") or ""))
            capability_file = self._write_capability_file(token)
            run_environment = {
                "CBM_MANAGER_URL": self.config.manager_url,
                "CBM_RUN_CAPABILITY_FILE": str(capability_file),
                "CBM_PROFILE_ID": str(capability.get("profile_id") or ""),
                "CBM_TASK_RUN_ID": run_id,
                "CBM_ALLOWED_ORIGINS": json.dumps(
                    list(claim.get("allowed_origins") or []), separators=(",", ":")
                ),
            }
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(
                    run_id=run_id,
                    cwd=self.config.worktree,
                    agent=str(agent),
                    session_name=session_name,
                    stop_event=stop_event,
                    cancel_event=cancel_event,
                    claim_lost=claim_lost,
                )
            )
            await self.runtime.validate_version()
            await self.runtime.ensure_session(
                cwd=self.config.worktree,
                agent=str(agent),
                session_name=session_name,
                environment=run_environment,
            )
            session_ensured = True
            summary = await self.runtime.run_prompt(
                cwd=self.config.worktree,
                agent=str(agent),
                session_name=session_name,
                prompt=build_run_scoped_browser_prompt(claim, capability),
                timeout_seconds=float(claim.get("timeout_seconds") or 300),
                environment=run_environment,
                emit=lambda output: self._emit(run_id, output),
                cancel_event=cancel_event,
            )
            if cancel_event.is_set() or claim_lost.is_set():
                return {"status": "cancelled"}
            if not summary:
                raise AcpxRuntimeError("ACPX prompt produced no terminal assistant output")
            # The ACP event stream already persisted the final summary.
            result = await asyncio.to_thread(self.client.complete, run_id)
            return {"status": str(result.get("status") or "succeeded")}
        except Exception as exc:  # noqa: BLE001 - sanitized terminal boundary
            if cancel_event.is_set() or claim_lost.is_set():
                return {"status": "cancelled"}
            message = sanitize_manager_error_message(str(exc)) or "ACPX run failed"
            await asyncio.to_thread(
                self.client.fail,
                run_id,
                error_code="internal_error",
                message=message,
            )
            return {"status": "failed"}
        finally:
            stop_event.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            if session_ensured:
                try:
                    await self.runtime.close_session(
                        cwd=self.config.worktree,
                        agent=str(agent),
                        session_name=session_name,
                    )
                except Exception:  # noqa: BLE001 - best-effort terminal cleanup
                    logger.warning("ACPX session cleanup failed for run %s", run_id)
            if capability_file is not None:
                capability_file.unlink(missing_ok=True)
            if capability_issued:
                try:
                    await asyncio.to_thread(self.client.revoke_capability, run_id)
                except Exception:  # noqa: BLE001 - terminal cleanup is idempotent
                    logger.warning("Manager capability cleanup failed for run %s", run_id)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        preflight_task: asyncio.Task[None] | None = None
        next_preflight_at = 0.0
        loop = asyncio.get_running_loop()
        try:
            while not stop.is_set():
                if (
                    loop.time() >= next_preflight_at
                    and (preflight_task is None or preflight_task.done())
                ):
                    if preflight_task is not None:
                        try:
                            await preflight_task
                        except Exception:  # noqa: BLE001 - retry on next interval
                            logger.warning("ACPX preflight refresh failed")
                    preflight_task = asyncio.create_task(self.refresh_preflights())
                    next_preflight_at = loop.time() + max(
                        30.0, self.config.preflight_interval_seconds
                    )
                try:
                    claim = await asyncio.to_thread(self.client.claim)
                except Exception:  # noqa: BLE001 - continue after transient claim errors
                    claim = None
                if claim:
                    if preflight_task is not None and not preflight_task.done():
                        preflight_task.cancel()
                        try:
                            await preflight_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:  # noqa: BLE001 - real work takes priority
                            logger.warning("ACPX preflight refresh failed before claim execution")
                        preflight_task = None
                    await self.execute_claim(claim)
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=max(0.01, self.config.poll_interval_seconds)
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            if preflight_task is not None:
                if not preflight_task.done():
                    preflight_task.cancel()
                try:
                    await preflight_task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - shutdown remains best effort
                    logger.warning("ACPX preflight refresh failed during shutdown")


def _absolute_directory(raw: str, *, label: str, private: bool = False) -> Path:
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"{label} must be an existing absolute directory")
    resolved = path.resolve()
    if private and resolved.stat().st_mode & 0o077:
        raise ValueError(f"{label} must use mode 0700")
    return resolved


def _read_private_worker_token_file(raw: str) -> tuple[str, str]:
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("worker token file must be an existing absolute regular file")
    if path.is_symlink():
        raise ValueError("worker token file must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(str(path), flags)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("worker token file must be an existing absolute regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise ValueError("worker token file must use mode 0600")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            token = validate_worker_token(handle.read().strip())
    except OSError as exc:
        raise ValueError("worker token file must be an existing absolute regular file") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    return token, str(path)


class _ArgumentParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("argument parser exit")
        self.status = int(status)


class _SecretSafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentParserExit(2)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise _ArgumentParserExit(status)


def build_worker_config(argv: list[str] | None = None) -> AcpxWorkerConfig:
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    if any(arg == "--token" or arg.startswith("--token=") for arg in raw_argv):
        raise ValueError("worker token file is required")
    parser = _SecretSafeArgumentParser(prog="acpx_worker")
    parser.add_argument("--manager-url", default=os.environ.get("CBM_MANAGER_URL") or "")
    parser.add_argument("--worker-id", default=os.environ.get("CBM_WORKER_ID") or "acpx-worker")
    parser.add_argument("--token-file", default=os.environ.get("CBM_WORKER_TOKEN_FILE") or "")
    parser.add_argument("--worktree", default=os.environ.get("CBM_ACPX_WORKTREE") or "")
    parser.add_argument(
        "--permission-policy",
        default=os.environ.get("CBM_ACPX_PERMISSION_POLICY") or "",
    )
    parser.add_argument("--mcp-config", default=os.environ.get("CBM_ACPX_MCP_CONFIG") or "")
    parser.add_argument(
        "--capability-dir",
        default=os.environ.get("CBM_ACPX_CAPABILITY_DIR") or "",
    )
    parser.add_argument("--acpx", default=os.environ.get("CBM_ACPX_EXECUTABLE") or "acpx")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--preflight-interval", type=float, default=240.0)
    args = parser.parse_args(raw_argv)

    manager_url = str(args.manager_url or "").strip().rstrip("/")
    if not manager_url:
        raise ValueError("manager URL is required")
    token_file = str(args.token_file or "").strip() or None
    if not token_file:
        raise ValueError("worker token file is required")
    token, token_file = _read_private_worker_token_file(token_file)

    worktree = _absolute_directory(str(args.worktree or ""), label="worktree")
    capability_dir = _absolute_directory(
        str(args.capability_dir or ""), label="capability directory", private=True
    )
    permission_policy = validate_permission_policy(Path(str(args.permission_policy or "")))
    mcp_config = validate_mcp_config(Path(str(args.mcp_config or "")))
    executable = str(args.acpx or "").strip()
    if not executable or any(ch.isspace() for ch in executable):
        raise ValueError("ACPX executable must be one command name or path")
    return AcpxWorkerConfig(
        manager_url=manager_url,
        worker_id=str(args.worker_id or "acpx-worker"),
        worktree=worktree,
        permission_policy=permission_policy,
        mcp_config=mcp_config,
        capability_dir=capability_dir,
        acpx_executable=executable,
        poll_interval_seconds=max(0.01, float(args.poll_interval)),
        preflight_interval_seconds=max(30.0, float(args.preflight_interval)),
        token=token,
        token_file=token_file,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = build_worker_config(argv if argv is not None else sys.argv[1:])
    except _ArgumentParserExit as exc:
        if exc.status == 0:
            return 0
        print(sanitize_manager_error_message("invalid arguments"), file=sys.stderr)
        return exc.status or 2
    except Exception as exc:  # noqa: BLE001 - sanitized CLI error
        print(sanitize_manager_error_message(str(exc)), file=sys.stderr)
        return 2
    client = AcpxManagerClient(
        config.manager_url,
        token=config.token,
        token_file=config.token_file,
    )
    worker = AcpxWorker(client, config)
    stop = asyncio.Event()

    def request_stop(*_args: Any) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
    except Exception:  # noqa: BLE001
        pass
    try:
        asyncio.run(worker.run_forever(stop_event=stop))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
