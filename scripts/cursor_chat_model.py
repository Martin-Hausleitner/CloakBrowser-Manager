#!/usr/bin/env python3
"""Browser-Use BaseChatModel adapter backed by argv-only cursor-agent.

Spawns ``cursor-agent --print --mode ask --output-format json --workspace
<tmp> --trust``. An explicit non-default model alias may be passed as
``--model``; otherwise the flag is omitted. Prompts travel on stdin.
Data-URL images are extracted to ``0600`` temp files under the per-invoke
workspace (mode ``0700``) and cleaned up after invoke. Do not pass
``--sandbox`` (unavailable on some hosts).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

SAFE_MODEL_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DEFAULT_MODEL_ALIAS = "default"
MAX_STRUCTURED_ATTEMPTS = 3
MAX_DATA_URL_BYTES = 512_000

_REDACT = "[REDACTED]"

_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)data:image\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_-])(?:cbm_agent_|cbm_run_|cbm_worker_|cbm_lease_|cbm_session_)[A-Za-z0-9_-]+"
    ),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+=/]{8,}"),
    re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|secret|password|passwd|token)\s*[:=]\s*[^\s\"']+"
    ),
    re.compile(r"(?i)(cookie\s*:\s*)([^\r\n]+)"),
    re.compile(r"(?i)\b(?:session|sid|auth)=[^\s;,&]+"),
    re.compile(r"(?i)(https?://)([^:@\s/]+):([^@\s/]+)@"),
    re.compile(
        r"(?<![\w:])/(?:tmp|home|Users|private|var/folders|workspace|workspaces|Volumes)/[^\s,;|)\"']+"
    ),
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\s,;|)\"']+"),
)


Runner = Callable[..., subprocess.CompletedProcess[str]]
KillPg = Callable[[int, int], None]


class CursorAgentError(Exception):
    """Non-timeout cursor-agent failure (always redacted)."""

    def __init__(self, message: str = "") -> None:
        super().__init__(redact_text(str(message)))


class CursorAgentTimeout(CursorAgentError):
    """cursor-agent exceeded timeout or was cancelled via process-group kill."""

    def __init__(self, message: str = "", *, pid: int | None = None) -> None:
        super().__init__(message)
        self.pid = pid


def redact_text(value: str | None) -> str:
    """Strip tokens, cookies, data-URLs, and local paths from free-form text."""
    text = value or ""
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(_REDACT, text)
    return text


def cleanup_temp_paths(paths: list[Path] | tuple[Path, ...] | set[Path]) -> None:
    """Best-effort delete of temp image files created during serialization."""
    for path in list(paths):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            continue


def _normalize_model_alias(alias: str | None) -> str | None:
    if alias is None:
        return None
    cleaned = str(alias).strip()
    if not cleaned or cleaned == DEFAULT_MODEL_ALIAS:
        return None
    if not SAFE_MODEL_ALIAS_RE.fullmatch(cleaned):
        raise CursorAgentError("unsafe model alias rejected")
    return cleaned


def _to_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if isinstance(value, BaseModel):
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return _to_plain(dump(mode="python"))
        legacy = getattr(value, "dict", None)
        if callable(legacy):
            return _to_plain(legacy())
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _to_plain(dump(mode="python"))
        except TypeError:
            return _to_plain(dump())
    legacy = getattr(value, "dict", None)
    if callable(legacy):
        return _to_plain(legacy())
    if hasattr(value, "__dict__"):
        public = {
            k: v
            for k, v in vars(value).items()
            if not k.startswith("_") and not callable(v)
        }
        if public:
            return _to_plain(public)
    return str(value)


def _decode_data_url(url: str) -> tuple[bytes, str]:
    match = re.match(
        r"^data:(image/[a-z0-9.+-]+);base64,(.+)$",
        url,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise CursorAgentError("unsupported image data URL")
    media_type = match.group(1).lower()
    b64 = re.sub(r"\s+", "", match.group(2))
    if len(b64) > MAX_DATA_URL_BYTES:
        raise CursorAgentError("image data URL exceeds size bound")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:  # noqa: BLE001 — strict base64 boundary
        raise CursorAgentError("image data URL is not valid base64") from exc
    if not raw:
        raise CursorAgentError("image data URL decoded empty")
    return raw, media_type


def _write_image_temp(data: bytes, media_type: str, directory: Path) -> Path:
    suffix = ".jpg" if media_type in {"image/jpeg", "image/jpg"} else ".png"
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="cbm-shot-", suffix=suffix, dir=str(directory))
    path = Path(name)
    try:
        os.write(fd, data)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    path.chmod(0o600)
    return path


def serialize_messages(
    messages: list[Any], tmp_dir: Path | str
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Serialize chat messages; extract data-URL images to 0600 temp files."""
    root = Path(tmp_dir)
    out: list[dict[str, Any]] = []
    temps: list[Path] = []
    for message in messages:
        item = _to_plain(message)
        if not isinstance(item, dict):
            item = {"role": "user", "content": item}
        content = item.get("content")
        if isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                part_out = _to_plain(part)
                if not isinstance(part_out, dict):
                    parts.append(part_out)
                    continue
                image = part_out.get("image_url")
                url = None
                if isinstance(image, dict):
                    url = image.get("url")
                elif isinstance(image, str):
                    url = image
                if isinstance(url, str) and url.startswith("data:image"):
                    blob, media_type = _decode_data_url(url)
                    image_path = _write_image_temp(blob, media_type, root)
                    temps.append(image_path)
                    part_out.pop("image_url", None)
                    part_out["type"] = part_out.get("type") or "image_path"
                    part_out["image_path"] = str(image_path)
                parts.append(part_out)
            item["content"] = parts
        elif isinstance(content, str) and "data:image" in content:
            item["content"] = redact_text(content)
        out.append(item)
    return out, temps


def _extract_result_payload(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        raise CursorAgentError("empty cursor-agent output")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CursorAgentError("cursor-agent output is not JSON") from exc
    if isinstance(envelope, dict) and "result" in envelope:
        result = envelope["result"]
    else:
        result = envelope
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError as exc:
            raise CursorAgentError("cursor-agent result is not JSON") from exc
    return result


def _build_structured_prompt(
    serialized: list[dict[str, Any]],
    schema: type[BaseModel],
    *,
    prior_error: str | None = None,
) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    messages_json = json.dumps(serialized, ensure_ascii=False)
    parts = [
        "Return ONLY a JSON object that validates against this schema.\n",
        f"SCHEMA:\n{schema_json}\n\n",
        f"MESSAGES:\n{messages_json}\n",
    ]
    if prior_error:
        parts.append(
            "\nPREVIOUS_VALIDATION_ERROR:\n"
            f"{redact_text(prior_error)}\n"
            "Fix the JSON so it satisfies the schema.\n"
        )
    return "".join(parts)


class _FallbackChatInvokeCompletion:
    """Safe stand-in when browser_use is not installed."""

    def __init__(self, *, completion: Any, usage: Any = None) -> None:
        self.completion = completion
        self.usage = usage


def make_chat_invoke_completion(*, completion: Any, usage: Any = None) -> Any:
    """Prefer Browser-Use ChatInvokeCompletion when installed."""
    try:
        from browser_use.llm.views import ChatInvokeCompletion  # lazy

        try:
            return ChatInvokeCompletion(completion=completion, usage=usage)
        except TypeError:
            return ChatInvokeCompletion(completion=completion)
    except Exception:
        return _FallbackChatInvokeCompletion(completion=completion, usage=usage)


class CursorAgentChatModel:
    """Minimal Browser-Use-compatible chat model using cursor-agent CLI."""

    def __init__(
        self,
        *,
        model_alias: str | None = None,
        timeout_seconds: float = 120,
        runner: Runner | None = None,
        killpg: KillPg | None = None,
        provider: str = "cursor-agent",
    ) -> None:
        self._model_alias = _normalize_model_alias(model_alias)
        self.model = self._model_alias or DEFAULT_MODEL_ALIAS
        self.model_alias = self._model_alias
        self.timeout_seconds = float(timeout_seconds)
        self.provider = provider
        self._runner = runner
        self._killpg = killpg or os.killpg
        # Per-invoke cancel events (not latched). cancel() sets all active ones.
        self._active_cancels: set[asyncio.Event] = set()
        self._active_lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    def cancel(self) -> None:
        """Signal all currently active cursor-agent invocations."""
        with self._active_lock:
            active = list(self._active_cancels)
        for ev in active:
            ev.set()

    def _register_invoke_cancel(self) -> asyncio.Event:
        ev = asyncio.Event()
        with self._active_lock:
            self._active_cancels.add(ev)
        return ev

    def _unregister_invoke_cancel(self, ev: asyncio.Event) -> None:
        with self._active_lock:
            self._active_cancels.discard(ev)

    @staticmethod
    def _ensure_workspace(tmp_dir: Path | str) -> Path:
        """Create/normalize a real directory workspace (mode 0700).

        Rejects symlink paths and non-directory targets before any chmod/write
        so we never follow a link and mutate another tree's permissions.
        """
        root = Path(tmp_dir)
        if root.is_symlink():
            raise CursorAgentError("workspace must not be a symlink")
        try:
            st = root.lstat()
        except FileNotFoundError:
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink():
                raise CursorAgentError("workspace must not be a symlink")
            st = root.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise CursorAgentError("workspace must not be a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise CursorAgentError("workspace must be a directory")
        os.chmod(root, 0o700, follow_symlinks=False)
        if root.is_symlink():
            raise CursorAgentError("workspace must not be a symlink")
        return root

    def _argv(self, workspace: Path) -> list[str]:
        argv = [
            "cursor-agent",
            "--print",
            "--mode",
            "ask",
            "--output-format",
            "json",
            "--workspace",
            str(workspace),
            "--trust",
        ]
        if self._model_alias:
            argv.extend(["--model", self._model_alias])
        return argv

    def _drain_runner_done(self, done: threading.Event) -> None:
        """Block until runner thread finishes (cancel-safe, bounded).

        A cancelled Task re-raises on every ``await``, and cancelling the
        asyncio wrapper Future can mark it done while the executor thread is
        still running — so we join via a threading.Event set in the worker
        ``finally``. Timeout avoids deadlock if a runner ignores cancel.
        """
        done.wait(timeout=max(5.0, float(self.timeout_seconds) + 5.0))

    def run_cursor(
        self,
        argv: list[str],
        timeout: float,
        cancel_event: asyncio.Event | None = None,
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run argv in a new process group; honor timeout and cancel_event.

        stdin is owned exclusively by ``communicate(input=...)`` so we never
        close the pipe ourselves (that caused 'I/O operation on closed file').
        """
        events = [cancel_event] if cancel_event is not None else []
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise CursorAgentError(f"failed to start cursor-agent: {exc}") from exc

        box: dict[str, Any] = {}

        def _communicate() -> None:
            try:
                stdout, stderr = proc.communicate(input=input_text)
                box["stdout"] = stdout or ""
                box["stderr"] = stderr or ""
                box["returncode"] = int(proc.returncode or 0)
            except Exception as exc:  # noqa: BLE001 — surfaced below
                box["error"] = exc

        thread = threading.Thread(target=_communicate, name="cursor-agent-io", daemon=True)
        thread.start()

        deadline = time.monotonic() + max(float(timeout), 0)
        timed_out = False
        cancelled = False
        while thread.is_alive():
            if any(ev.is_set() for ev in events):
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            thread.join(0.05)

        if timed_out or cancelled:
            pid = proc.pid
            try:
                self._killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
            thread.join(2)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            if timed_out:
                raise CursorAgentTimeout("cursor-agent timed out", pid=pid)
            raise CursorAgentError("cursor-agent cancelled")

        thread.join(2)
        if "error" in box:
            raise CursorAgentError(f"cursor-agent I/O failed: {box['error']}")
        completed = subprocess.CompletedProcess(
            args=list(argv),
            returncode=int(box.get("returncode") or 0),
            stdout=str(box.get("stdout") or ""),
            stderr=str(box.get("stderr") or ""),
        )
        if completed.returncode != 0:
            raise CursorAgentError(
                f"cursor-agent exited {completed.returncode}: {completed.stderr or completed.stdout}"
            )
        return completed

    def _call_runner(
        self,
        argv: list[str],
        timeout: float,
        cancel_event: asyncio.Event | None = None,
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            try:
                return self._runner(
                    argv, timeout, cancel_event=cancel_event, input_text=input_text
                )
            except TypeError:
                return self._runner(argv, timeout, cancel_event=cancel_event)
        return self.run_cursor(
            argv, timeout, cancel_event=cancel_event, input_text=input_text
        )

    async def invoke_structured(
        self,
        messages: list[Any],
        schema: type[T],
        tmp_dir: Path | str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> T:
        """Invoke cursor-agent and validate structured output with retries."""
        temps: list[Path] = []
        workspace = self._ensure_workspace(tmp_dir)
        invoke_cancel = self._register_invoke_cancel()
        link_task: asyncio.Task[None] | None = None
        if cancel_event is not None:

            async def _forward_external_cancel() -> None:
                await cancel_event.wait()
                invoke_cancel.set()

            link_task = asyncio.create_task(_forward_external_cancel())
        try:
            try:
                serialized, temps = serialize_messages(messages, workspace)
                argv = self._argv(workspace)
                last_error: Exception | None = None
                prior_error: str | None = None
                loop = asyncio.get_running_loop()

                for _attempt in range(MAX_STRUCTURED_ATTEMPTS):
                    prompt = _build_structured_prompt(
                        serialized, schema, prior_error=prior_error
                    )
                    runner_done = threading.Event()

                    def _run_invoke(p: str = prompt) -> Any:
                        try:
                            return self._call_runner(
                                argv,
                                self.timeout_seconds,
                                invoke_cancel,
                                input_text=p,
                            )
                        finally:
                            runner_done.set()

                    runner_fut: asyncio.Future[Any] = loop.run_in_executor(
                        None, _run_invoke
                    )
                    try:
                        try:
                            completed = await runner_fut
                        except asyncio.CancelledError:
                            # Signal current invoke, then join runner before unwind
                            # so temp/workspace cleanup cannot race the subprocess.
                            invoke_cancel.set()
                            self._drain_runner_done(runner_done)
                            raise
                        payload = _extract_result_payload(completed.stdout)
                        return schema.model_validate(payload)
                    except CursorAgentTimeout as exc:
                        # run_cursor already killed the process group via getpgid.
                        raise CursorAgentTimeout(str(exc), pid=exc.pid) from None
                    except CursorAgentError as exc:
                        # Cancellation must not be retried; keep the event set so
                        # the subprocess observer still sees its own signal.
                        if invoke_cancel.is_set():
                            raise
                        last_error = exc
                        prior_error = str(exc)
                        continue
                    except (ValidationError, TypeError, ValueError) as exc:
                        last_error = exc
                        prior_error = str(exc)
                        continue

                raise CursorAgentError(
                    f"structured output validation failed after retries: {last_error}"
                )
            except asyncio.CancelledError:
                # Signal only this invoke's event if cancel beat the runner await.
                invoke_cancel.set()
                raise
        finally:
            if link_task is not None:
                link_task.cancel()
            self._unregister_invoke_cancel(invoke_cancel)
            cleanup_temp_paths(temps)

    async def ainvoke(
        self,
        messages: list[Any],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Browser-Use BaseChatModel-compatible entrypoint."""
        tmp_dir = kwargs.get("tmp_dir") or kwargs.get("temp_dir")
        created_tmp = False
        if tmp_dir is None:
            tmp_dir = tempfile.mkdtemp(prefix="cbm-cursor-")
            created_tmp = True
            os.chmod(tmp_dir, 0o700)
        cancel_event = kwargs.get("cancel_event")
        try:
            try:
                if output_format is not None:
                    result = await self.invoke_structured(
                        messages, output_format, tmp_dir, cancel_event=cancel_event
                    )
                    return make_chat_invoke_completion(completion=result)

                class _Text(BaseModel):
                    result: str

                structured = await self.invoke_structured(
                    messages, _Text, tmp_dir, cancel_event=cancel_event
                )
                return make_chat_invoke_completion(completion=structured.result)
            except asyncio.CancelledError:
                # invoke_structured already signaled the active per-invoke event.
                raise
        finally:
            if created_tmp:
                try:
                    for child in Path(tmp_dir).glob("cbm-shot-*"):
                        child.unlink(missing_ok=True)
                    Path(tmp_dir).rmdir()
                except OSError:
                    pass
