"""Pure bounded OpenAI-compatible browser-action tool loop.

The provider sees only a compact chat-completions transcript and one fixed
function tool. Browser routing remains behind an injectable async callable.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8317"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
DEFAULT_MODEL_ALIAS = "grok-build-0.1"
TOOL_NAME = "cbm_route_browser_action"
ALLOWED_ACTIONS = ("inspect", "navigate", "click", "fill", "read_text")
TERMINAL_CLASSIFICATIONS = {
    "auth_required",
    "origin_denied",
    "capability_invalid",
    "profile_lease_lost",
    "policy_denied",
    "secret_boundary_violation",
    "second_browser_attempt",
    "model_required",
}
SECRET_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "headers",
    "password",
    "profile",
    "proxy",
    "secret",
    "token",
}
SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "cookie",
    "auth",
    "header",
    "key",
    "capability",
    "cdp",
    "routing",
    "profile",
    "proxy",
    "session",
    "lease",
)
MAX_TURNS = 8
MAX_RUN_TIMEOUT_SECONDS = 120.0
MAX_TOTAL_TOOL_CALLS = 8
MAX_TOOL_CALLS_PER_MESSAGE = 4
MAX_TOOL_CALL_ID_CHARS = 128
TOOL_CALL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_USER_CONTENT_CHARS = 16_000
MAX_ASSISTANT_CONTENT_CHARS = 8_000
MAX_ARGUMENTS_CHARS = 8_000
MAX_TOOL_RESULT_CHARS = 4_000
MAX_STRING_CHARS = 1_000
MAX_LIST_ITEMS = 24
MAX_DICT_ITEMS = 48
HTTP_TIMEOUT_SECONDS = 30.0

RouterCallable = Callable[[str, dict[str, Any]], Any]


class ProviderClient(Protocol):
    async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Return a decoded OpenAI-compatible chat-completions response."""


@dataclass(frozen=True)
class ToolLoopResult:
    final_text: str
    model: str
    turns: int
    tool_calls: int


@dataclass
class ToolLoopError(Exception):
    code: str
    reason: str
    classification: str | None = None
    tool_id: str | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.code}: {self.reason}")


class OpenAICompatibleHTTPClient:
    """Cancellation-safe httpx loopback transport for chat completions."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = _validate_loopback_base_url(base_url)

    async def complete_chat(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        bounded_timeout = min(float(timeout), HTTP_TIMEOUT_SECONDS)
        timeout_config = httpx.Timeout(
            bounded_timeout,
            connect=bounded_timeout,
            read=bounded_timeout,
            write=bounded_timeout,
            pool=bounded_timeout,
        )
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=0)
        url = self.base_url + CHAT_COMPLETIONS_PATH
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        async with httpx.AsyncClient(
            timeout=timeout_config,
            trust_env=False,
            follow_redirects=False,
            limits=limits,
        ) as client:
            async with client.stream(
                "POST",
                url,
                content=body,
                headers={"content-type": "application/json", "accept": "application/json"},
            ) as response:
                data = b""
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ToolLoopError("protocol_error", "response body too large")
                if 300 <= response.status_code <= 399:
                    raise ToolLoopError("protocol_error", "provider redirect is not allowed")
                if response.status_code in {401, 403}:
                    raise ToolLoopError("auth_required", "provider authentication failed")
                if 500 <= response.status_code <= 599:
                    raise ToolLoopError("proxy_unavailable", "provider proxy unavailable")
                if response.status_code >= 400:
                    raise ToolLoopError("protocol_error", f"provider HTTP error {response.status_code}")
        if len(data) > MAX_RESPONSE_BYTES:
            raise ToolLoopError("protocol_error", "response body too large")
        try:
            decoded = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolLoopError("protocol_error", "provider response is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ToolLoopError("protocol_error", "provider response is not an object")
        return decoded


async def run_openai_compatible_tool_loop(
    user_task: str,
    router: RouterCallable,
    *,
    model_alias: str = DEFAULT_MODEL_ALIAS,
    client: ProviderClient | None = None,
    base_url: str = DEFAULT_BASE_URL,
    run_timeout_seconds: float = 60.0,
) -> ToolLoopResult:
    if not isinstance(user_task, str) or not user_task.strip():
        raise ToolLoopError("invalid_request", "user task is required")
    if len(user_task) > MAX_USER_CONTENT_CHARS:
        raise ToolLoopError("invalid_request", "user task is too large")
    if run_timeout_seconds <= 0:
        raise ToolLoopError("invalid_request", "run timeout must be positive")

    provider = client or OpenAICompatibleHTTPClient(base_url)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Route browser action requests through the provided browser action tool. "
                "Return final concise text when finished."
            ),
        },
        {"role": "user", "content": user_task},
    ]
    seen_tool_ids: set[str] = set()
    tool_call_count = 0
    loop = asyncio.get_running_loop()
    total_timeout = min(float(run_timeout_seconds), MAX_RUN_TIMEOUT_SECONDS)
    deadline = loop.time() + total_timeout

    try:
        async with asyncio.timeout(total_timeout):
            for turn in range(1, MAX_TURNS + 1):
                remaining = max(0.001, deadline - loop.time())
                response = await _complete(provider, _request_payload(model_alias, messages), remaining)
                message = _extract_message(response)
                if "tool_calls" in message and message["tool_calls"] is not None:
                    tool_calls = message["tool_calls"]
                    parsed_calls = _validate_tool_calls(tool_calls, seen_tool_ids)
                    if tool_call_count + len(parsed_calls) > MAX_TOTAL_TOOL_CALLS:
                        raise ToolLoopError("protocol_error", "too many tool calls total")
                    messages.append(_assistant_tool_message(message, parsed_calls))
                    for call in parsed_calls:
                        tool_call_count += 1
                        router_result = await _call_router(router, call["action"], call["arguments"])
                        tool_payload = _provider_safe_tool_payload(router_result, call["id"])
                        classification = str(tool_payload.get("classification") or "")
                        if classification in TERMINAL_CLASSIFICATIONS:
                            reason = str(tool_payload.get("message") or classification)
                            raise ToolLoopError(
                                classification,
                                _redact_text(reason),
                                classification=classification,
                                tool_id=call["id"],
                            )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": _bounded_json(tool_payload, MAX_TOOL_RESULT_CHARS),
                            }
                        )
                    continue

                content = message.get("content")
                if not isinstance(content, str):
                    raise ToolLoopError("protocol_error", "final assistant content is missing")
                content = content.strip()
                if not content:
                    raise ToolLoopError("protocol_error", "final assistant content is empty")
                if len(content) > MAX_ASSISTANT_CONTENT_CHARS:
                    raise ToolLoopError("protocol_error", "final assistant content is too large")
                return ToolLoopResult(
                    final_text=content,
                    model=model_alias,
                    turns=turn,
                    tool_calls=tool_call_count,
                )
    except TimeoutError as exc:
        raise ToolLoopError("timeout", "tool loop deadline exceeded") from exc

    raise ToolLoopError("protocol_error", "maximum tool loop turns exceeded")


async def _complete(
    provider: ProviderClient, payload: dict[str, Any], remaining_seconds: float
) -> dict[str, Any]:
    try:
        return await provider.complete_chat(
            payload,
            timeout=min(HTTP_TIMEOUT_SECONDS, max(0.001, remaining_seconds)),
        )
    except asyncio.CancelledError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ToolLoopError("auth_required", "provider authentication failed") from exc
        if 500 <= exc.code <= 599:
            raise ToolLoopError("proxy_unavailable", "provider proxy unavailable") from exc
        raise ToolLoopError("protocol_error", f"provider HTTP error {exc.code}") from exc
    except ToolLoopError:
        raise
    except httpx.TimeoutException as exc:
        raise ToolLoopError("timeout", "provider request timed out") from exc
    except httpx.TransportError as exc:
        raise ToolLoopError("proxy_unavailable", "provider proxy unavailable") from exc
    except TimeoutError as exc:
        raise ToolLoopError("timeout", "provider request timed out") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ToolLoopError("proxy_unavailable", "provider proxy unavailable") from exc
    except Exception as exc:
        raise ToolLoopError("protocol_error", "provider client failed") from exc


def _request_payload(model_alias: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": model_alias,
        "messages": messages,
        "tools": [_tool_schema()],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Route one bounded browser action.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "arguments"],
                "properties": {
                    "action": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                        "maxProperties": 24,
                    },
                },
            },
        },
    }


def _extract_message(response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ToolLoopError("protocol_error", "provider response is not an object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ToolLoopError("protocol_error", "provider response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ToolLoopError("protocol_error", "provider choice is malformed")
    message = first.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ToolLoopError("protocol_error", "provider assistant message is malformed")
    content = message.get("content")
    if content is not None and (not isinstance(content, str) or len(content) > MAX_ASSISTANT_CONTENT_CHARS):
        raise ToolLoopError("protocol_error", "assistant content is oversized or malformed")
    return message


def _validate_tool_calls(tool_calls: Any, seen_tool_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise ToolLoopError("protocol_error", "tool_calls is malformed")
    if len(tool_calls) > MAX_TOOL_CALLS_PER_MESSAGE:
        raise ToolLoopError("protocol_error", "too many tool calls in assistant message")
    if len(tool_calls) > 1:
        raise ToolLoopError("protocol_error", "parallel tool calls are not allowed")
    parsed: list[dict[str, Any]] = []
    for raw_call in tool_calls:
        if not isinstance(raw_call, dict):
            raise ToolLoopError("protocol_error", "tool call is malformed")
        call_id = raw_call.get("id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or len(call_id) > MAX_TOOL_CALL_ID_CHARS
            or not TOOL_CALL_ID_RE.fullmatch(call_id)
        ):
            raise ToolLoopError("protocol_error", "tool call id is malformed")
        if call_id in seen_tool_ids:
            raise ToolLoopError("protocol_error", "duplicate tool call id")
        if raw_call.get("type") != "function":
            raise ToolLoopError("protocol_error", "unknown tool call type")
        function = raw_call.get("function")
        if not isinstance(function, dict) or function.get("name") != TOOL_NAME:
            raise ToolLoopError("protocol_error", "unknown tool call")
        raw_args = function.get("arguments")
        if not isinstance(raw_args, str) or len(raw_args) > MAX_ARGUMENTS_CHARS:
            raise ToolLoopError("protocol_error", "tool arguments are malformed or oversized")
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise ToolLoopError("protocol_error", "tool arguments are not valid JSON") from exc
        if not isinstance(args, dict):
            raise ToolLoopError("protocol_error", "tool arguments must be an object")
        action = args.get("action")
        arguments = args.get("arguments")
        if action not in ALLOWED_ACTIONS:
            raise ToolLoopError("protocol_error", "disallowed browser action")
        if not isinstance(arguments, dict):
            raise ToolLoopError("protocol_error", "browser action arguments must be an object")
        seen_tool_ids.add(call_id)
        parsed.append(
            {
                "id": call_id,
                "raw": _safe_tool_call_echo(call_id, raw_args),
                "action": action,
                "arguments": arguments,
            }
        )
    return parsed


def _assistant_tool_message(message: dict[str, Any], parsed_calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": [call["raw"] for call in parsed_calls],
    }


def _safe_tool_call_echo(call_id: str, raw_args: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "arguments": raw_args,
        },
    }


async def _call_router(router: RouterCallable, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = router(action, arguments)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ToolLoopError("protocol_error", "router result is not an object")
    return result


def _provider_safe_tool_payload(router_result: dict[str, Any], tool_id: str) -> dict[str, Any]:
    safe = _redact_value(router_result)
    if not isinstance(safe, dict):
        safe = {}
    payload = {
        "ok": bool(safe.get("ok")),
        "outcome": _string_or_default(safe.get("outcome"), ""),
        "classification": _string_or_default(safe.get("classification"), ""),
        "tool_id": tool_id,
        "result": safe.get("result") if isinstance(safe.get("result"), (dict, list, str, int, float, bool)) else {},
        "message": _string_or_default(safe.get("message"), ""),
    }
    return payload


def _string_or_default(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    return _redact_text(value)[:MAX_STRING_CHARS]


def _redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                redacted["truncated"] = True
                break
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            redacted[key_text[:128]] = _redact_value(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, str):
        return _redact_text(value)[:MAX_STRING_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))[:MAX_STRING_CHARS]


def _redact_text(text: str) -> str:
    text = _redact_urls(text)
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"cbm_[a-z0-9_:-]+", "[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=[^&\s]+", r"\1=[REDACTED]", text)
    return text


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_KEYS:
        return True
    normalized = re.sub(r"[^a-z0-9]", "", lowered)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _redact_urls(text: str) -> str:
    return re.sub(r"\b(?:https?|wss?)://[^\s\"'<>]+", _redact_url_match, text)


def _redact_url_match(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        port = parsed.port
    except ValueError:
        return "[REDACTED_URL]"
    if not parsed.scheme or not parsed.netloc:
        return raw_url
    hostname = parsed.hostname or ""
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_pairs = [(key, "[REDACTED]") for key, _value in query_pairs]
    query = urllib.parse.urlencode(safe_pairs)
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


def _bounded_json(payload: dict[str, Any], limit: int) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= limit:
        return text
    clipped = dict(payload)
    clipped["result"] = "[truncated]"
    text = json.dumps(clipped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= limit:
        return text
    fallback = {"ok": bool(payload.get("ok")), "error": "truncated", "tool_id": payload.get("tool_id")}
    text = json.dumps(fallback, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= limit:
        return text
    return json.dumps({"error": "truncated"}, separators=(",", ":"))


def _validate_loopback_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ToolLoopError("configuration_error", "base URL must be loopback http://127.0.0.1")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ToolLoopError("configuration_error", "base URL must not include path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolLoopError("configuration_error", "base URL port is invalid") from exc
    if not port:
        raise ToolLoopError("configuration_error", "base URL must include a port")
    return f"http://127.0.0.1:{port}"
