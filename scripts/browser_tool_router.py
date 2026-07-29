#!/usr/bin/env python3
"""Pure run-scoped browser tool router for normalized ACPX routing contracts."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from scripts.cbm_browser_ctl import redact_error_message

ROUTING_BROWSER_TOOL_ORDER = ("unbrowse", "stagehand", "browser-harness")
STAGEHAND_SEMANTIC_ACTIONS = frozenset({"act", "extract", "observe", "agent"})
ACP_PROVIDER_AGENTS = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok-build",
    "opencode": "opencode",
}
OPENAI_COMPATIBLE_PROVIDER_AGENTS = {"grok": "grok-build"}
MAX_PUBLIC_PAYLOAD_DEPTH = 6
MAX_PUBLIC_PAYLOAD_ITEMS = 64
MAX_PUBLIC_STRING_CHARS = 4_096
MAX_PUBLIC_RESULT_BYTES = 32_768
REDACTED_TRUNCATED = "[redacted:truncated]"
FAILOVER_CLASSIFICATIONS = frozenset(
    {"route_miss", "unsupported_action", "tool_unavailable", "transient_timeout"}
)
STOP_CLASSIFICATIONS = frozenset(
    {
        "auth_required",
        "origin_denied",
        "capability_invalid",
        "profile_lease_lost",
        "policy_denied",
        "model_required",
        "secret_boundary_violation",
        "second_browser_attempt",
    }
)
SUCCESS_CLASSIFICATION = "ok"


@dataclass(frozen=True)
class RunScopedBrowserContext:
    manager_url: str
    profile_id: str
    task_run_id: str
    allowed_origins: tuple[str, ...]
    capability_file: Path
    capability_token: str = field(repr=False, compare=False)
    lease_id: str | None = None


@dataclass(frozen=True)
class BrowserToolConfig:
    id: str
    enabled: bool


@dataclass(frozen=True)
class BrowserRoutingContract:
    provider: dict[str, Any]
    browser_tools: tuple[BrowserToolConfig, ...]
    max_tool_attempts: int
    allow_second_browser: bool = False
    mode: str = "ordered-fallback"

    def public_json(self) -> dict[str, Any]:
        return {
            "provider": dict(self.provider),
            "browser_tools": [
                {"id": tool.id, "enabled": tool.enabled} for tool in self.browser_tools
            ],
            "routing_policy": {
                "mode": self.mode,
                "allow_second_browser": self.allow_second_browser,
                "max_tool_attempts": self.max_tool_attempts,
            },
        }


@dataclass(frozen=True)
class BrowserToolRequest:
    tool_id: str
    attempt_index: int
    action: str
    arguments: dict[str, Any]
    context: RunScopedBrowserContext


@dataclass(frozen=True)
class BrowserToolResult:
    outcome: str
    classification: str = SUCCESS_CLASSIFICATION
    payload: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    opened_second_browser: bool = False


@dataclass(frozen=True)
class BrowserRouteResult:
    outcome: str
    classification: str
    tool_id: str | None
    payload: dict[str, Any]
    telemetry: tuple[dict[str, Any], ...]
    message: str = ""

    def public_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": self.outcome == "succeeded",
            "outcome": self.outcome,
            "classification": self.classification,
            "tool_id": self.tool_id,
            "result": _redact_payload(self.payload),
            "telemetry": list(self.telemetry),
        }
        if self.message:
            body["message"] = redact_error_message(self.message)
        return _cap_public_body(body)


class BrowserToolRouterError(RuntimeError):
    def __init__(self, classification: str, message: str = "") -> None:
        super().__init__(message or classification)
        self.classification = classification


BrowserToolAdapter = Callable[[BrowserToolRequest], Awaitable[BrowserToolResult]]


def routing_contract_from_claim(
    claim: dict[str, Any] | None,
    capability: dict[str, Any] | None = None,
) -> BrowserRoutingContract | None:
    """Parse the normalized Task1 contract when present; return None for legacy."""
    if claim is None and capability is None:
        return None
    source = _routing_source(claim or {}, capability or {})
    if not any(key in source for key in ("provider", "browser_tools", "routing_policy")):
        return None
    provider = source.get("provider")
    browser_tools = source.get("browser_tools")
    routing_policy = source.get("routing_policy")
    if provider is None and browser_tools == [] and routing_policy is None:
        return None
    if not isinstance(provider, dict) or not isinstance(browser_tools, list):
        raise ValueError("provider, browser_tools, and routing_policy are required")
    if not isinstance(routing_policy, dict):
        raise ValueError("provider, browser_tools, and routing_policy are required")
    provider_id = str(provider.get("id") or "")
    transport = str(provider.get("transport") or "")
    agent = _routing_agent(claim or {}, capability or {}, source, provider)
    expected_agent = _expected_provider_agent(provider_id, transport)
    if expected_agent is None:
        raise ValueError("routing contract provider transport is not supported")
    if agent is not None and agent != expected_agent:
        raise ValueError("routing contract provider and agent do not match")
    if len(browser_tools) != len(ROUTING_BROWSER_TOOL_ORDER):
        raise ValueError(
            "browser_tools must be ordered as unbrowse, stagehand, browser-harness"
        )
    for tool in browser_tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("enabled"), bool):
            raise ValueError("browser_tools entries require explicit boolean enabled")
    tool_configs = tuple(
        BrowserToolConfig(id=str(tool.get("id")), enabled=bool(tool.get("enabled")))
        for tool in browser_tools
    )
    declared_tool_ids = [
        str(tool.get("id")) for tool in browser_tools if isinstance(tool, dict)
    ]
    if tuple(declared_tool_ids) != ROUTING_BROWSER_TOOL_ORDER:
        raise ValueError(
            "browser_tools must be ordered as unbrowse, stagehand, browser-harness"
        )
    required_policy_keys = {"mode", "allow_second_browser", "max_tool_attempts"}
    if set(routing_policy) != required_policy_keys:
        raise ValueError(
            "routing_policy requires explicit mode, allow_second_browser, max_tool_attempts"
        )
    max_tool_attempts = routing_policy.get("max_tool_attempts")
    if not isinstance(max_tool_attempts, int) or isinstance(max_tool_attempts, bool):
        raise ValueError("max_tool_attempts must be an integer between 1 and 3")
    if max_tool_attempts < 1 or max_tool_attempts > 3:
        raise ValueError("max_tool_attempts must be between 1 and 3")
    allow_second_browser = routing_policy.get("allow_second_browser")
    if not isinstance(allow_second_browser, bool):
        raise ValueError("allow_second_browser must be explicit boolean false")
    if allow_second_browser:
        raise ValueError("allow_second_browser is not supported")
    mode = routing_policy.get("mode")
    if mode != "ordered-fallback":
        raise ValueError("routing mode must be ordered-fallback")
    return BrowserRoutingContract(
        provider={"id": provider_id, "transport": transport},
        browser_tools=tool_configs,
        max_tool_attempts=max_tool_attempts,
        allow_second_browser=False,
        mode=mode,
    )


async def route_browser_action(
    *,
    contract: BrowserRoutingContract | None,
    context: RunScopedBrowserContext,
    action: str,
    arguments: dict[str, Any] | None,
    adapters: dict[str, BrowserToolAdapter],
    adapter_timeout_seconds: float = 30.0,
) -> BrowserRouteResult:
    if adapter_timeout_seconds <= 0:
        raise ValueError("adapter_timeout_seconds must be positive")
    if _is_stagehand_semantic_action(action):
        return BrowserRouteResult(
            outcome="failed",
            classification="model_required",
            tool_id=None,
            payload={},
            telemetry=(),
            message="Stagehand semantic actions require explicit model configuration",
        )
    if contract is None:
        return BrowserRouteResult(
            outcome="failed",
            classification="tool_unavailable",
            tool_id=None,
            payload={},
            telemetry=(),
            message="routing contract absent",
        )
    attempts: list[dict[str, Any]] = []
    last_failure: BrowserRouteResult | None = None
    tools = [tool.id for tool in contract.browser_tools if tool.enabled][
        : contract.max_tool_attempts
    ]
    for index, tool_id in enumerate(tools, start=1):
        started = time.perf_counter()
        adapter = adapters.get(tool_id)
        classification = "tool_unavailable"
        outcome = "failed"
        message = ""
        payload: dict[str, Any] = {}
        try:
            if adapter is None:
                result = BrowserToolResult(
                    outcome="failed",
                    classification="tool_unavailable",
                )
            else:
                if not _is_async_adapter(adapter):
                    raise BrowserToolRouterError(
                        "policy_denied", "browser tool adapter must be async"
                    )
                request = BrowserToolRequest(
                    tool_id=tool_id,
                    attempt_index=index,
                    action=str(action),
                    arguments=dict(arguments or {}),
                    context=context,
                )
                result = await asyncio.wait_for(
                    adapter(request),
                    timeout=adapter_timeout_seconds,
                )
                if not isinstance(result, BrowserToolResult):
                    raise BrowserToolRouterError(
                        "policy_denied", "browser tool returned invalid result"
                    )
            if result.opened_second_browser or result.classification == "second_browser_attempt":
                classification = "second_browser_attempt"
                outcome = "failed"
                message = result.message or "browser tool attempted a second browser"
            else:
                classification = _normalize_classification(result.classification)
                outcome = (
                    "succeeded"
                    if classification == SUCCESS_CLASSIFICATION
                    and result.outcome == "succeeded"
                    else "failed"
                )
                payload = dict(result.payload or {}) if outcome == "succeeded" else {}
                message = result.message
        except BrowserToolRouterError as exc:
            classification = _normalize_classification(exc.classification)
            message = str(exc)
        except asyncio.TimeoutError as exc:
            classification = "transient_timeout"
            message = str(exc)
        except Exception:  # noqa: BLE001 - adapter boundary is terminal-safe
            classification = "policy_denied"
            message = "browser tool adapter failed"
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        will_fallback = (
            classification in FAILOVER_CLASSIFICATIONS
            and outcome != "succeeded"
            and index < len(tools)
        )
        attempts.append(
            {
                "tool_id": tool_id,
                "action_class": _action_class(action),
                "duration_ms": duration_ms,
                "result_class": classification,
                "fallback_reason": classification if will_fallback else None,
            }
        )
        route_result = BrowserRouteResult(
            outcome=outcome,
            classification=classification,
            tool_id=tool_id,
            payload=payload if outcome == "succeeded" else {},
            telemetry=tuple(attempts),
            message=message,
        )
        if outcome == "succeeded" and classification == SUCCESS_CLASSIFICATION:
            return route_result
        last_failure = route_result
        if classification not in FAILOVER_CLASSIFICATIONS:
            return route_result
    return last_failure or BrowserRouteResult(
        outcome="failed",
        classification="tool_unavailable",
        tool_id=None,
        payload={},
        telemetry=tuple(attempts),
        message="no browser tools configured",
    )


def _routing_source(
    claim: dict[str, Any],
    capability: dict[str, Any],
) -> dict[str, Any]:
    if (
        capability.get("provider") is not None
        or capability.get("browser_tools")
        or capability.get("routing_policy") is not None
    ):
        return capability
    return claim


def _expected_provider_agent(provider_id: str, transport: str) -> str | None:
    if transport == "acp":
        return ACP_PROVIDER_AGENTS.get(provider_id)
    if transport == "openai-compatible":
        return OPENAI_COMPATIBLE_PROVIDER_AGENTS.get(provider_id)
    return None


def _routing_agent(
    claim: dict[str, Any],
    capability: dict[str, Any],
    source: dict[str, Any],
    provider: dict[str, Any],
) -> str | None:
    containers: tuple[dict[str, Any], ...] = (source, provider, capability, claim)
    for container in containers:
        value = container.get("agent")
        if value is not None:
            return str(value)
    for envelope in (capability, claim):
        nested_provider = envelope.get("provider")
        if isinstance(nested_provider, dict):
            value = nested_provider.get("agent")
            if value is not None:
                return str(value)
    return None


def _normalize_classification(value: str) -> str:
    classification = str(value or "").strip() or "tool_unavailable"
    if classification == SUCCESS_CLASSIFICATION:
        return classification
    if classification in FAILOVER_CLASSIFICATIONS or classification in STOP_CLASSIFICATIONS:
        return classification
    return "policy_denied"


def _is_stagehand_semantic_action(value: str) -> bool:
    return str(value or "").strip().lower() in STAGEHAND_SEMANTIC_ACTIONS


def _is_async_adapter(adapter: BrowserToolAdapter) -> bool:
    if inspect.iscoroutinefunction(adapter):
        return True
    call = getattr(adapter, "__call__", None)
    return bool(call is not None and inspect.iscoroutinefunction(call))


def _action_class(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"inspect", "navigate", "click", "fill", "read_text"}:
        return text
    return "unsupported_action"


def _redact_payload(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_PUBLIC_PAYLOAD_DEPTH:
        return REDACTED_TRUNCATED
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_PUBLIC_PAYLOAD_ITEMS:
                redacted[REDACTED_TRUNCATED] = REDACTED_TRUNCATED
                break
            redacted[_redact_string(str(key))] = _redact_payload(
                item, depth=depth + 1
            )
        return redacted
    if isinstance(value, list):
        items = [
            _redact_payload(item, depth=depth + 1)
            for item in value[:MAX_PUBLIC_PAYLOAD_ITEMS]
        ]
        if len(value) > MAX_PUBLIC_PAYLOAD_ITEMS:
            items.append(REDACTED_TRUNCATED)
        return items
    if isinstance(value, tuple):
        items = [
            _redact_payload(item, depth=depth + 1)
            for item in value[:MAX_PUBLIC_PAYLOAD_ITEMS]
        ]
        if len(value) > MAX_PUBLIC_PAYLOAD_ITEMS:
            items.append(REDACTED_TRUNCATED)
        return items
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(value: str) -> str:
    redacted = redact_error_message(value)
    if len(redacted) > MAX_PUBLIC_STRING_CHARS:
        return redacted[:MAX_PUBLIC_STRING_CHARS] + REDACTED_TRUNCATED
    return redacted


def _cap_public_body(body: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(body, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode("utf-8")) <= MAX_PUBLIC_RESULT_BYTES:
        return body
    capped = dict(body)
    capped["result"] = REDACTED_TRUNCATED
    return capped
