#!/usr/bin/env python3
"""Host worker for Manager internal Browser-Use claim/run lifecycle.

Claims runs from ``/internal/task-runs/*``, attaches via a short-lived CDP
capability, drives Browser-Use 0.13.x with ``enable_signal_handler=False``,
heartbeats concurrently, streams typed outputs, and uploads a final screenshot.
Prefer a worker token file over env. Never logs secrets. ``browser_use`` is
imported lazily so unit tests can stub it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlparse

from scripts.claude_cli_chat_model import ClaudeCLIChatModel
from scripts.cursor_chat_model import CursorAgentChatModel, redact_text

logger = logging.getLogger(__name__)

SUPPORTED_HARNESS = "browser-use"
CLAIM_PATH = "/internal/task-runs/claim"
CLAIM_QUERY = urlencode({"harness": SUPPORTED_HARNESS})
STERILE_FAIL_MESSAGE = "run failed"
_BANNED_MESSAGE_NEEDLES = ("bearer ", "authorization")
_BANNED_WORD_RE = re.compile(r"(?i)\b(authorization|bearer)\b")
_BEARER_PREFIX_RE = re.compile(r"(?i)\bbearer\s+")
_AUTHORIZATION_RE = re.compile(r"(?i)authorization")
ALLOWLISTED_FAIL_CODES = frozenset(
    {
        "worker_lost",
        "worker_unavailable",
        "navigation_blocked",
        "model_timeout",
        "model_rate_limit",
        "max_steps",
        "health_blocked",
        "capability_revoked",
        "internal_error",
    }
)
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
MAX_HEARTBEAT_TRANSIENT_FAILURES = 3
ACTION_PAYLOAD_KEYS = frozenset({"name", "url", "selector", "text", "step", "target"})
DEFAULT_LLM_PROVIDER = "cursor-agent"
SUPPORTED_LLM_PROVIDERS = frozenset({DEFAULT_LLM_PROVIDER, "claude-cli"})

# Browser Use per-call LLM wait must leave a cleanup margin under the run budget.
# Formula: llm_timeout = run_timeout - clamp(run_timeout // 6, 15, 60),
# then clamped to [1, run_timeout - 1]. For timeout_seconds=180 → margin 30 → 150.
LLM_TIMEOUT_MARGIN_MIN = 15
LLM_TIMEOUT_MARGIN_MAX = 60


def select_llm_provider(provider: str | None) -> str:
    """Normalize worker LLM provider, preserving cursor-agent as default."""
    value = str(provider or "").strip()
    if not value or value == "default":
        return DEFAULT_LLM_PROVIDER
    if value not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError("LLM provider must be cursor-agent or claude-cli")
    return value


def derive_browser_use_llm_timeout(run_timeout_seconds: float) -> int:
    """Derive Browser Use ``llm_timeout`` from the Manager run budget.

    CursorAgentChatModel keeps the full run/subprocess timeout. Browser Use's
    generic default is 75s when ``llm_timeout`` is omitted, which starved the
    live 180s run (af3a1709). This helper always grants a value strictly less
    than the run budget so heartbeat/screenshot/complete/cleanup can finish
    before the Manager deadline:

        margin = clamp(run_timeout // 6, 15, 60)
        llm_timeout = clamp(run_timeout - margin, 1, run_timeout - 1)
    """
    run = max(1, int(run_timeout_seconds))
    if run <= 1:
        return 1
    margin = max(LLM_TIMEOUT_MARGIN_MIN, min(LLM_TIMEOUT_MARGIN_MAX, run // 6))
    llm = run - margin
    return max(1, min(llm, run - 1))


def validate_worker_token(token: str | None) -> str:
    """Require a non-empty printable token with no whitespace/newlines."""
    if token is None:
        raise ValueError("worker token is required")
    value = str(token)
    if not value or any(ch.isspace() for ch in value) or not value.isprintable():
        raise ValueError("worker token must be non-empty printable without whitespace")
    return value


def sanitize_manager_error_message(message: str | None) -> str:
    """Redact secrets then remove Manager-banned fail-message needles."""
    text = redact_text(message or "")
    # Consume banned needles and any immediate credential-looking values.
    text = re.sub(r"(?i)\bbearer\s+\S*", "[REDACTED]", text)
    text = re.sub(r"(?i)authorization\s*[:=]?\s*\S*", "[REDACTED]", text)
    text = _BANNED_WORD_RE.sub("[REDACTED]", text)
    text = re.sub(r"(?i)\[REDACTED\]\s*[:=]\s*\S+", "[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip(" :,-")
    text = text.strip()
    lowered = text.lower()
    if not text or any(needle in lowered for needle in _BANNED_MESSAGE_NEEDLES):
        return STERILE_FAIL_MESSAGE
    # Guard against leftover token-like fragments after needle stripping.
    if "cbm_run_" in lowered or "cbm_worker_" in lowered or "cbm_agent_" in lowered:
        return STERILE_FAIL_MESSAGE
    return text[:500]


def sanitize_output_payload(value: Any) -> Any:
    """Recursively redact/sanitize string values in typed output payloads."""
    if isinstance(value, dict):
        return {str(k): sanitize_output_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_output_payload(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_output_payload(v) for v in value]
    if isinstance(value, str):
        return sanitize_manager_error_message(value)
    return value


def normalize_fail_code(code: str | None) -> str:
    cleaned = (code or "internal_error").strip()
    if cleaned not in ALLOWLISTED_FAIL_CODES:
        return "internal_error"
    return cleaned


def flatten_action_payload(action: Any) -> dict[str, Any]:
    """Flatten Browser-Use ActionModel dumps like ``{navigate: {url: ...}}``."""
    data: Any
    if action is None:
        return {}
    dump = getattr(action, "model_dump", None)
    if callable(dump):
        try:
            data = dump(mode="python", exclude_none=True)
        except TypeError:
            try:
                data = dump(exclude_none=True)
            except TypeError:
                data = dump()
    elif isinstance(action, dict):
        data = action
    else:
        name = getattr(action, "name", None) or action.__class__.__name__
        return {"name": str(name)}

    if not isinstance(data, dict):
        return {"name": str(data)}

    # Already flat
    if "name" in data and not any(isinstance(v, dict) for v in data.values()):
        return {k: v for k, v in data.items() if k in ACTION_PAYLOAD_KEYS or k == "name"}

    nested_items = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    if len(nested_items) == 1:
        name, fields = nested_items[0]
        out: dict[str, Any] = {"name": str(name)}
        for key, value in fields.items():
            if key in ACTION_PAYLOAD_KEYS and key != "name":
                out[key] = value
            elif key == "url":
                out["url"] = value
            elif key in {"text", "selector", "target", "step"}:
                out[key] = value
        # Keep useful scalar fields even if not in allowlist names by mapping known ones
        for key, value in fields.items():
            if key not in out and key in ACTION_PAYLOAD_KEYS:
                out[key] = value
        if "url" in fields:
            out["url"] = fields["url"]
        return out

    if data:
        # Multiple keys — use first non-dict as name fallback
        return {"name": next(iter(data.keys()))}
    return {}


def decode_screenshot_payload(payload: Any, *, max_bytes: int = MAX_SCREENSHOT_BYTES) -> bytes:
    """Decode BrowserSession.take_screenshot return value (bytes or raw base64)."""
    if payload is None:
        raise ValueError("empty screenshot")
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
        if len(raw) > max_bytes:
            raise ValueError("screenshot exceeds size bound")
        return raw
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("data:image"):
            _, _, b64 = text.partition(",")
            text = b64
        if len(text) > max_bytes * 2:
            raise ValueError("screenshot exceeds size bound")
        try:
            raw = base64.b64decode(text, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("screenshot is not valid base64") from exc
        if len(raw) > max_bytes:
            raise ValueError("screenshot exceeds size bound")
        if not raw:
            raise ValueError("empty screenshot")
        return raw
    raise ValueError("unsupported screenshot payload type")


def detect_image_media_type(data: bytes) -> str | None:
    """Return image/png or image/jpeg from magic bytes; never guess JPEG as PNG."""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _call_history_list(fn: Any, **kwargs: Any) -> Any:
    if not callable(fn):
        return None
    try:
        return fn(**kwargs)
    except TypeError:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def _read_screenshot_file(path: Path, *, max_bytes: int = MAX_SCREENSHOT_BYTES) -> bytes | None:
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > max_bytes:
            return None
        raw = path.read_bytes()
        if not raw or len(raw) > max_bytes:
            return None
        return raw
    except OSError:
        return None


def extract_screenshot_from_history(
    history: Any, *, max_bytes: int = MAX_SCREENSHOT_BYTES
) -> tuple[bytes, str] | None:
    """Prefer newest existing history screenshot_path; else newest screenshots() b64."""
    if history is None:
        return None
    try:
        paths = _call_history_list(
            getattr(history, "screenshot_paths", None),
            return_none_if_not_screenshot=False,
        )
        if isinstance(paths, list):
            for item in reversed(paths):
                if not item:
                    continue
                raw = _read_screenshot_file(Path(str(item)), max_bytes=max_bytes)
                if raw is None:
                    continue
                media_type = detect_image_media_type(raw)
                if media_type is None:
                    continue
                return raw, media_type

        shots = _call_history_list(
            getattr(history, "screenshots", None),
            return_none_if_not_screenshot=False,
        )
        if isinstance(shots, list):
            for item in reversed(shots):
                if not item:
                    continue
                try:
                    raw = decode_screenshot_payload(item, max_bytes=max_bytes)
                except ValueError:
                    continue
                media_type = detect_image_media_type(raw)
                if media_type is None:
                    continue
                return raw, media_type
    except Exception:  # noqa: BLE001 — screenshot is best-effort
        return None
    return None


async def resolve_final_screenshot(
    history: Any, session: Any, *, max_bytes: int = MAX_SCREENSHOT_BYTES
) -> tuple[bytes, str] | None:
    """History screenshot first, then live session capture. Fail soft."""
    try:
        from_history = extract_screenshot_from_history(history, max_bytes=max_bytes)
        if from_history is not None:
            return from_history
        raw = await _capture_screenshot_bytes(session)
        if raw is None:
            return None
        if len(raw) > max_bytes:
            return None
        media_type = detect_image_media_type(raw)
        if media_type is None:
            return None
        return raw, media_type
    except Exception:  # noqa: BLE001 — screenshot is best-effort
        return None


@dataclass(frozen=True)
class WorkerConfig:
    manager_url: str
    worker_id: str
    poll_interval_seconds: float = 2.0
    default_timeout_seconds: float = 300.0
    default_max_steps: int = 20
    token: str | None = None
    token_file: str | None = None
    llm_provider: str = DEFAULT_LLM_PROVIDER


class ManagerHTTPError(RuntimeError):
    """HTTP error from Manager with redacted message and status code."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(redact_text(str(message)))
        self.status_code = int(status_code)


class _UrllibResponse:
    def __init__(self, status_code: int, data: Any = None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._data = data
        self.content = content
        if data is not None and not content:
            self.text = json.dumps(data)
            self.content = self.text.encode("utf-8")
        else:
            self.text = content.decode("utf-8", errors="replace") if content else ""

    def json(self) -> Any:
        if self._data is not None:
            return self._data
        if not self.text:
            return {}
        return json.loads(self.text)


class _UrllibHTTP:
    """Minimal ``.request(method, url, **kwargs)`` client (no extra deps)."""

    def request(self, method: str, url: str, **kwargs: Any) -> _UrllibResponse:
        headers = dict(kwargs.get("headers") or {})
        body: bytes | None = None
        if "json" in kwargs and kwargs["json"] is not None:
            body = json.dumps(kwargs["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif kwargs.get("content") is not None:
            body = kwargs["content"]
            if isinstance(body, str):
                body = body.encode("utf-8")
        elif kwargs.get("data") is not None:
            data = kwargs["data"]
            body = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=float(kwargs.get("timeout") or 60)) as resp:
                raw = resp.read()
                status = getattr(resp, "status", None) or resp.getcode()
                if not raw:
                    return _UrllibResponse(int(status), data=None, content=b"")
                try:
                    return _UrllibResponse(
                        int(status), data=json.loads(raw.decode("utf-8")), content=raw
                    )
                except json.JSONDecodeError:
                    return _UrllibResponse(int(status), data=None, content=raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            data = None
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except json.JSONDecodeError:
                data = None
            return _UrllibResponse(int(exc.code), data=data, content=raw or b"")


class ManagerClient:
    """Authenticated client for Manager ``/internal/task-runs`` routes."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        token_file: str | Path | None = None,
        http: Any | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self._token = token
        self._token_file = Path(token_file) if token_file else None
        self.http = http or _UrllibHTTP()

    @property
    def token(self) -> str:
        if self._token_file is not None:
            raw = self._token_file.read_text(encoding="utf-8")
            value = raw.strip()
            if value:
                return validate_worker_token(value)
        if self._token:
            return validate_worker_token(str(self._token).strip())
        env = (os.environ.get("CBM_WORKER_TOKEN") or "").strip()
        return validate_worker_token(env)

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = self._auth_headers(kwargs.pop("headers", None))
        url = self._url(path)
        try:
            resp = self.http.request(method.upper(), url, headers=headers, **kwargs)
        except Exception as exc:  # noqa: BLE001 — redacted transport boundary
            raise RuntimeError(redact_text(f"manager request failed: {exc}")) from None
        if resp.status_code >= 400:
            detail = getattr(resp, "text", "") or ""
            raise ManagerHTTPError(
                f"manager HTTP {resp.status_code}: {detail}",
                status_code=int(resp.status_code),
            )
        return resp

    def post(self, path: str, **kwargs: Any) -> Any:
        resp = self.request("POST", path, **kwargs)
        if resp.status_code == 204:
            return None
        if not getattr(resp, "text", None):
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    def put(self, path: str, **kwargs: Any) -> Any:
        resp = self.request("PUT", path, **kwargs)
        if resp.status_code == 204:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self.request("DELETE", path, **kwargs)
        if resp.status_code == 204:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    def claim(self) -> dict[str, Any] | None:
        # Always use the harness-filtered claim endpoint; never the unfiltered path.
        path = f"{CLAIM_PATH}?{CLAIM_QUERY}"
        resp = self.request("POST", path)
        if resp.status_code == 204:
            return None
        return resp.json()

    def heartbeat(self, run_id: str) -> dict[str, Any]:
        return self.post(f"/internal/task-runs/{run_id}/heartbeat") or {}

    def issue_capability(self, run_id: str) -> dict[str, Any]:
        return self.post(f"/internal/task-runs/{run_id}/capability") or {}

    def revoke_capability(self, run_id: str) -> None:
        self.delete(f"/internal/task-runs/{run_id}/capability")

    def output(
        self,
        run_id: str,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        body = {
            "idempotency_key": idempotency_key,
            "kind": kind,
            "summary": summary,
            "payload": payload,
        }
        return self.post(f"/internal/task-runs/{run_id}/outputs", json=body) or {}

    def upload_screenshot(
        self,
        run_id: str,
        output_id: str,
        body: bytes,
        media_type: str,
    ) -> None:
        digest = hashlib.sha256(body).hexdigest()
        self.put(
            f"/internal/task-runs/{run_id}/screenshots/{output_id}",
            content=body,
            headers={
                "Content-Type": media_type,
                "X-CBM-Screenshot-SHA256": digest,
            },
        )

    def complete(self, run_id: str) -> dict[str, Any]:
        return self.post(f"/internal/task-runs/{run_id}/complete") or {}

    def fail(self, run_id: str, *, error_code: str, message: str) -> dict[str, Any]:
        code = normalize_fail_code(error_code)
        clean = sanitize_manager_error_message(message)
        body = {"error_code": code, "message": clean}
        try:
            return self.post(f"/internal/task-runs/{run_id}/fail", json=body) or {}
        except ManagerHTTPError as exc:
            # Retry once with a fixed sterile message when Manager rejects validation.
            if exc.status_code == 422 and clean != STERILE_FAIL_MESSAGE:
                return (
                    self.post(
                        f"/internal/task-runs/{run_id}/fail",
                        json={
                            "error_code": code,
                            "message": STERILE_FAIL_MESSAGE,
                        },
                    )
                    or {}
                )
            raise


def _absolute_cdp_url(manager_url: str, cdp_url: str) -> str:
    raw = (cdp_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("ws"):
        return raw
    return urljoin(manager_url.rstrip("/") + "/", raw)


def _import_browser_use() -> Any:
    import browser_use  # noqa: WPS433 — intentional lazy import

    return browser_use


def _call_maybe(value: Any) -> Any:
    return value() if callable(value) else value


def _history_is_done(history: Any) -> bool | None:
    if history is None:
        return None
    fn = getattr(history, "is_done", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return None
    # Compatibility for simple fakes that only expose final_result
    if hasattr(history, "final_result"):
        return True
    return None


def _history_is_successful(history: Any) -> bool | None:
    fn = getattr(history, "is_successful", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    return True


def _history_error_message(history: Any) -> str:
    errors_fn = getattr(history, "errors", None)
    if callable(errors_fn):
        try:
            errors = errors_fn() or []
            for item in errors:
                if item:
                    return str(item)
        except Exception:  # noqa: BLE001
            pass
    return "agent reported failure"


async def _capture_screenshot_bytes(session: Any) -> bytes | None:
    if session is None:
        return None
    for name in ("take_screenshot", "screenshot"):
        fn = getattr(session, name, None)
        if not callable(fn):
            continue
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            return decode_screenshot_payload(result)
        except Exception:  # noqa: BLE001 — screenshot is best-effort
            continue
    return None


def _llm_factory_for_provider(provider: str) -> Callable[..., Any]:
    if select_llm_provider(provider) == "claude-cli":
        return ClaudeCLIChatModel
    return CursorAgentChatModel


async def _disconnect_session(session: Any) -> None:
    """Disconnect CDP client without killing the Manager-owned browser."""
    if session is None:
        return
    for name in ("stop", "close"):
        fn = getattr(session, name, None)
        if not callable(fn):
            continue
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                await result
            return
        except Exception:  # noqa: BLE001 — best-effort disconnect
            continue


@dataclass
class BrowserUseWorker:
    """Execute claimed Browser-Use runs against Manager-owned profiles."""

    client: Any
    config: WorkerConfig
    _output_keys: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)
    _completed: set[str] = field(default_factory=set, init=False, repr=False)
    _failed: set[str] = field(default_factory=set, init=False, repr=False)
    _cleaned: set[str] = field(default_factory=set, init=False, repr=False)
    _llm_factory: Callable[..., Any] = field(
        default=CursorAgentChatModel, init=False, repr=False
    )
    _obs_emitted: set[tuple[str, int, int]] = field(
        default_factory=set, init=False, repr=False
    )

    async def emit_output(
        self,
        run_id: str,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        key = (run_id, idempotency_key)
        if key in self._output_keys:
            return None
        safe_payload = sanitize_output_payload(payload)
        if not isinstance(safe_payload, dict):
            safe_payload = {}
        result = self.client.output(
            run_id,
            kind=kind,
            summary=sanitize_manager_error_message(summary)[:500],
            payload=safe_payload,
            idempotency_key=idempotency_key,
        )
        self._output_keys.add(key)
        return result

    async def emit_screenshot(
        self,
        run_id: str,
        body: bytes,
        *,
        media_type: str = "image/png",
        idempotency_key: str,
        summary: str = "Screenshot",
    ) -> None:
        key = (run_id, idempotency_key)
        if key in self._output_keys:
            return
        created = self.client.output(
            run_id,
            kind="screenshot",
            summary=summary,
            payload={},
            idempotency_key=idempotency_key,
        )
        self._output_keys.add(key)
        output_id = (created or {}).get("id")
        if not output_id:
            raise RuntimeError("screenshot output missing id")
        self.client.upload_screenshot(run_id, output_id, body, media_type)

    async def complete(self, run_id: str, *, summary: str | None = None) -> dict[str, Any]:
        if run_id in self._completed:
            return {"status": "succeeded"}
        if summary:
            text = sanitize_manager_error_message(summary)[:500]
            await self.emit_output(
                run_id,
                "summary",
                text,
                {"text": text},
                f"summary:{run_id}",
            )
        result = self.client.complete(run_id) or {"status": "succeeded"}
        self._completed.add(run_id)
        return result

    async def fail(
        self,
        run_id: str,
        *,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        if run_id in self._failed:
            return {"status": "failed"}
        clean = sanitize_manager_error_message(message)
        code = normalize_fail_code(error_code)
        try:
            result = self.client.fail(run_id, error_code=code, message=clean) or {
                "status": "failed"
            }
        except ManagerHTTPError as exc:
            if exc.status_code == 422 and clean != STERILE_FAIL_MESSAGE:
                result = self.client.fail(
                    run_id, error_code=code, message=STERILE_FAIL_MESSAGE
                ) or {"status": "failed"}
            else:
                raise
        self._failed.add(run_id)
        return result

    def cleanup(self, run_id: str) -> None:
        if run_id in self._cleaned:
            return
        try:
            self.client.revoke_capability(run_id)
        except Exception:  # noqa: BLE001 — best-effort capability cleanup
            logger.warning("capability cleanup failed for run %s", run_id)
        self._cleaned.add(run_id)

    def _stop_agent_and_llm(self, agent: Any) -> None:
        stop = getattr(agent, "stop", None)
        if callable(stop):
            stop()
        llm = getattr(agent, "_llm", None) or getattr(agent, "llm", None)
        cancel = getattr(llm, "cancel", None)
        if callable(cancel):
            cancel()

    async def heartbeat_once(self, run_id: str, agent: Any) -> bool:
        body = await asyncio.to_thread(self.client.heartbeat, run_id) or {}
        if body.get("cancel_requested"):
            self._stop_agent_and_llm(agent)
            return True
        return False

    async def _heartbeat_loop(
        self,
        run_id: str,
        agent: Any,
        stop_event: asyncio.Event,
        cancel_requested: asyncio.Event,
        claim_lost: asyncio.Event,
        *,
        interval: float = 0.5,
    ) -> None:
        transient = 0
        while not stop_event.is_set() and not cancel_requested.is_set() and not claim_lost.is_set():
            try:
                body = await asyncio.to_thread(self.client.heartbeat, run_id) or {}
                transient = 0
            except ManagerHTTPError as exc:
                if exc.status_code in {404, 410}:
                    self._stop_agent_and_llm(agent)
                    claim_lost.set()
                    return
                transient += 1
                if transient >= MAX_HEARTBEAT_TRANSIENT_FAILURES:
                    self._stop_agent_and_llm(agent)
                    claim_lost.set()
                    return
                body = {}
            except Exception:  # noqa: BLE001 — count as transient
                transient += 1
                if transient >= MAX_HEARTBEAT_TRANSIENT_FAILURES:
                    self._stop_agent_and_llm(agent)
                    claim_lost.set()
                    return
                body = {}

            if body.get("cancel_requested"):
                self._stop_agent_and_llm(agent)
                cancel_requested.set()
                return

            sleep_for = float(body.get("heartbeat_interval_seconds") or interval)
            sleep_for = max(0.01, min(sleep_for, 15.0))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                continue

    async def _emit_actions_from_model_output(
        self, run_id: str, model_output: Any, step_num: int
    ) -> None:
        actions = getattr(model_output, "action", None) or []
        if not isinstance(actions, (list, tuple)):
            actions = [actions]
        for index, action in enumerate(actions):
            payload = flatten_action_payload(action)
            if "step" not in payload:
                payload["step"] = int(step_num)
            name = str(payload.get("name") or "action")
            await self.emit_output(
                run_id,
                "action",
                name,
                {k: v for k, v in payload.items() if k in ACTION_PAYLOAD_KEYS or k == "name"},
                f"action:{run_id}:{step_num}:{index}",
            )

    async def _emit_observations_from_agent(self, run_id: str, agent: Any) -> None:
        history = getattr(agent, "history", None)
        items = getattr(history, "history", None) if history is not None else None
        if not items:
            return
        for step_idx, item in enumerate(items):
            results = getattr(item, "result", None) or []
            if not isinstance(results, (list, tuple)):
                results = [results]
            for index, result in enumerate(results):
                key = (run_id, step_idx, index)
                if key in self._obs_emitted:
                    continue
                text = getattr(result, "extracted_content", None) or getattr(result, "error", None)
                if not text:
                    continue
                note = redact_text(str(text))[:500]
                await self.emit_output(
                    run_id,
                    "observation",
                    note[:120] or "observation",
                    {"text": note},
                    f"observation:{run_id}:{step_idx}:{index}",
                )
                self._obs_emitted.add(key)

    async def execute_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        run_id = str(claim.get("id") or "")
        harness = str(claim.get("harness") or "")
        if harness != SUPPORTED_HARNESS:
            await self.fail(
                run_id,
                error_code="internal_error",
                message=f"harness {harness!r} is not supported by this worker",
            )
            return {"status": "failed", "error_code": "internal_error"}

        capability_issued = False
        stop_event = asyncio.Event()
        cancel_requested = asyncio.Event()
        claim_lost = asyncio.Event()
        agent: Any = None
        llm: Any = None
        session: Any = None
        try:
            cap = self.client.issue_capability(run_id)
            capability_issued = True
            cdp_url = _absolute_cdp_url(self.config.manager_url, str(cap.get("cdp_url") or ""))
            headers = dict(cap.get("headers") or {})
            origins = list(claim.get("allowed_origins") or [])
            allowed_domains = origins if origins else None
            timeout_seconds = float(
                claim.get("timeout_seconds") or self.config.default_timeout_seconds
            )
            max_steps = int(claim.get("max_steps") or self.config.default_max_steps)
            raw_alias = claim.get("model_alias")
            model_alias = None if raw_alias in (None, "", "default") else str(raw_alias)
            llm_provider = select_llm_provider(claim.get("llm_provider") or self.config.llm_provider)

            bu = _import_browser_use()
            session = bu.BrowserSession(
                cdp_url=cdp_url,
                headers=headers,
                allowed_domains=allowed_domains,
                keep_alive=True,
            )
            llm_factory = self._llm_factory
            if llm_factory is CursorAgentChatModel:
                llm_factory = _llm_factory_for_provider(llm_provider)

            llm = llm_factory(
                model_alias=model_alias,
                timeout_seconds=timeout_seconds,
            )
            llm_timeout = derive_browser_use_llm_timeout(timeout_seconds)

            async def on_step(browser_state: Any, model_output: Any, step_num: int) -> None:
                await self._emit_actions_from_model_output(run_id, model_output, step_num)

            async def on_step_end(agent_obj: Any) -> None:
                await self._emit_observations_from_agent(run_id, agent_obj)

            agent = bu.Agent(
                task=str(claim.get("task") or ""),
                llm=llm,
                browser_session=session,
                enable_signal_handler=False,
                register_new_step_callback=on_step,
                llm_timeout=llm_timeout,
                flash_mode=True,
                use_judge=False,
                max_clickable_elements_length=10000,
                llm_screenshot_size=(640, 480),
            )

            hb_task = asyncio.create_task(
                self._heartbeat_loop(
                    run_id,
                    agent,
                    stop_event,
                    cancel_requested,
                    claim_lost,
                    interval=0.05,
                )
            )
            try:
                history = await asyncio.wait_for(
                    agent.run(max_steps=max_steps, on_step_end=on_step_end),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._stop_agent_and_llm(agent)
                if not claim_lost.is_set():
                    await self.fail(
                        run_id,
                        error_code="model_timeout",
                        message="run exceeded timeout_seconds",
                    )
                return {"status": "failed", "error_code": "model_timeout"}
            finally:
                stop_event.set()
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

            if claim_lost.is_set():
                # Manager already terminal (public cancel / revoked claim).
                return {"status": "cancelled"}

            if cancel_requested.is_set():
                # Explicit cancel_requested from heartbeat while claim still valid.
                # Public cancel path uses 404; this branch is rare. Do not POST fail
                # if Manager is racing to terminal — prefer abort without fail.
                return {"status": "cancelled"}

            done = _history_is_done(history)
            successful = _history_is_successful(history)

            if done is False:
                await self.fail(
                    run_id,
                    error_code="max_steps",
                    message="agent did not finish within max_steps",
                )
                return {"status": "failed", "error_code": "max_steps"}

            if successful is False:
                await self.fail(
                    run_id,
                    error_code="internal_error",
                    message=_history_error_message(history),
                )
                return {"status": "failed", "error_code": "internal_error"}

            final = None
            if history is not None:
                final = _call_maybe(getattr(history, "final_result", None))
            summary = redact_text(str(final or "succeeded"))[:500]

            shot = await resolve_final_screenshot(
                history, getattr(agent, "browser_session", None) or session
            )
            if shot:
                body, media_type = shot
                await self.emit_screenshot(
                    run_id,
                    body,
                    media_type=media_type,
                    idempotency_key=f"final-shot:{run_id}",
                    summary="Final screenshot",
                )

            result = await self.complete(run_id, summary=summary)
            return {"status": result.get("status") or "succeeded"}
        except Exception as exc:  # noqa: BLE001 — map to fail without leaking secrets
            if claim_lost.is_set():
                return {"status": "cancelled"}
            message = str(exc)
            if run_id and run_id not in self._failed and run_id not in self._completed:
                await self.fail(run_id, error_code="internal_error", message=message)
            return {"status": "failed", "error_code": "internal_error"}
        finally:
            stop_event.set()
            if llm is not None:
                cancel = getattr(llm, "cancel", None)
                if callable(cancel):
                    cancel()
            await _disconnect_session(
                getattr(agent, "browser_session", None) if agent is not None else session
            )
            if capability_issued and run_id:
                self.cleanup(run_id)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Poll for claims until stop_event is set."""
        stop = stop_event or asyncio.Event()
        interval = max(0.01, float(self.config.poll_interval_seconds))
        while not stop.is_set():
            try:
                claim = self.client.claim()
            except Exception:  # noqa: BLE001 — continue polling after transient errors
                claim = None
            if claim:
                try:
                    await self.execute_claim(claim)
                except Exception:  # noqa: BLE001 — never kill the poll loop
                    logger.exception("claim execution failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


def build_worker_config(argv: list[str] | None = None) -> WorkerConfig:
    """Parse CLI/env into a validated WorkerConfig (never prints secrets)."""
    parser = argparse.ArgumentParser(prog="browser_use_worker")
    parser.add_argument("--manager-url", default=os.environ.get("CBM_MANAGER_URL") or "")
    parser.add_argument("--worker-id", default=os.environ.get("CBM_WORKER_ID") or "browser-use-worker")
    parser.add_argument("--token", default=os.environ.get("CBM_WORKER_TOKEN") or "")
    parser.add_argument(
        "--token-file",
        default=os.environ.get("CBM_WORKER_TOKEN_FILE") or "",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("CBM_WORKER_POLL_INTERVAL") or 2.0),
    )
    parser.add_argument(
        "--llm-provider",
        choices=sorted(SUPPORTED_LLM_PROVIDERS),
        default=os.environ.get("CBM_BROWSER_USE_LLM_PROVIDER") or DEFAULT_LLM_PROVIDER,
    )
    args = parser.parse_args(argv)
    manager_url = str(args.manager_url or "").strip().rstrip("/")
    if not manager_url:
        raise ValueError("manager URL is required")
    token_file = str(args.token_file or "").strip() or None
    token = str(args.token or "").strip() or None
    if token_file:
        raw = Path(token_file).read_text(encoding="utf-8").strip()
        token = validate_worker_token(raw)
    elif token:
        token = validate_worker_token(token)
    else:
        raise ValueError("worker token or token file is required")
    return WorkerConfig(
        manager_url=manager_url,
        worker_id=str(args.worker_id or "browser-use-worker"),
        poll_interval_seconds=float(args.poll_interval),
        token=token,
        token_file=token_file,
        llm_provider=select_llm_provider(args.llm_provider),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the host Browser-Use worker."""
    try:
        config = build_worker_config(argv if argv is not None else sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 — redacted CLI errors only
        print(redact_text(str(exc)), file=sys.stderr)
        return 2

    client = ManagerClient(
        config.manager_url,
        token=config.token,
        token_file=config.token_file,
    )
    worker = BrowserUseWorker(client=client, config=config)
    stop = asyncio.Event()

    def _handle_stop(*_args: Any) -> None:
        stop.set()

    try:
        import signal

        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
    except Exception:  # noqa: BLE001
        pass

    try:
        asyncio.run(worker.run_forever(stop_event=stop))
    except KeyboardInterrupt:
        stop.set()
    return 0


def resolve_manager_base(url: str) -> str:
    """Return scheme://host[:port] for joining absolute Manager paths."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


if __name__ == "__main__":
    raise SystemExit(main())
