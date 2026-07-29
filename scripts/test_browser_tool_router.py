from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from scripts.browser_tool_router import (
    BrowserToolResult,
    MAX_PUBLIC_RESULT_BYTES,
    RunScopedBrowserContext,
    route_browser_action,
    routing_contract_from_claim,
)


def run_context(tmp_path: Path) -> RunScopedBrowserContext:
    capability = tmp_path / "capability"
    capability.write_text("cbm_run_private_capability", encoding="utf-8")
    return RunScopedBrowserContext(
        manager_url="https://manager.local",
        profile_id="profile-1",
        task_run_id="run-1",
        allowed_origins=("https://example.com",),
        capability_file=capability,
        capability_token="cbm_run_private_capability",
        lease_id="lease-1",
    )


def normalized_claim(**overrides):
    body = {
        "id": "run-1",
        "profile_id": "profile-1",
        "provider": {"id": "grok", "transport": "acp"},
        "browser_tools": [
            {"id": "unbrowse", "enabled": True},
            {"id": "stagehand", "enabled": True},
            {"id": "browser-harness", "enabled": True},
        ],
        "routing_policy": {
            "mode": "ordered-fallback",
            "allow_second_browser": False,
            "max_tool_attempts": 2,
        },
    }
    body.update(overrides)
    return body


def partial_claim(**overrides):
    body = {
        "id": "run-1",
        "profile_id": "profile-1",
        "provider": None,
        "browser_tools": [],
        "routing_policy": None,
    }
    body.update(overrides)
    return body


def test_routing_contract_is_absent_for_legacy_claims():
    assert routing_contract_from_claim(partial_claim()) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": {"id": "grok", "transport": "acp"}},
        {"browser_tools": [{"id": "unbrowse", "enabled": True}]},
        {"routing_policy": {"mode": "ordered-fallback"}},
        {
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand", "enabled": True},
                {"id": "browser-harness", "enabled": True},
            ]
        },
        {
            "routing_policy": {
                "mode": "ordered-fallback",
                "allow_second_browser": False,
            }
        },
    ],
)
def test_routing_contract_rejects_partial_or_implicit_contract_shapes(overrides):
    with pytest.raises(ValueError):
        routing_contract_from_claim(partial_claim(**overrides))


def test_router_fails_over_only_for_allowed_classifications_and_emits_redacted_telemetry(
    tmp_path: Path,
):
    context = run_context(tmp_path)
    seen_contexts = []

    async def unbrowse(request):
        seen_contexts.append(request.context)
        return BrowserToolResult(
            outcome="failed",
            classification="route_miss",
            message="Bearer cbm_run_private_capability missed https://u:p@example.com?token=secret",
        )

    async def stagehand(request):
        seen_contexts.append(request.context)
        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload={"url": "https://u:p@example.com?token=secret"},
        )

    async def browser_harness(_request):
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=context,
            action="inspect",
            arguments={},
            adapters={
                "unbrowse": unbrowse,
                "stagehand": stagehand,
                "browser-harness": browser_harness,
            },
        )
    )

    assert result.outcome == "succeeded"
    assert result.tool_id == "stagehand"
    assert seen_contexts == [context, context]
    assert [attempt["tool_id"] for attempt in result.telemetry] == ["unbrowse", "stagehand"]
    assert set(result.telemetry[0]) == {
        "tool_id",
        "action_class",
        "duration_ms",
        "result_class",
        "fallback_reason",
    }
    assert result.telemetry[0]["action_class"] == "inspect"
    assert result.telemetry[0]["result_class"] == "route_miss"
    assert result.telemetry[0]["fallback_reason"] == "route_miss"
    assert result.telemetry[1]["result_class"] == "ok"
    assert result.telemetry[1]["fallback_reason"] is None
    serialized = json.dumps(result.telemetry)
    assert "cbm_run_private_capability" not in serialized
    assert "u:p" not in serialized
    assert "secret" not in serialized
    public = json.dumps(result.public_json())
    assert "u:p" not in public
    assert "secret" not in public
    assert "manager_url" not in public
    assert "capability_file" not in public
    assert "allowed_origins" not in public
    assert "lease-1" not in public


def test_public_result_redacts_secret_keys_and_caps_nested_payload(tmp_path: Path):
    huge_payload = {
        "https://u:p@example.com/account?token=secret": [
            {
                f"item-{index}": {
                    "nested": ["x" * 5000, {"password=secret": "cbm_run_private_capability"}]
                }
            }
            for index in range(150)
        ]
    }

    async def unbrowse(_request):
        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload=huge_payload,
        )

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unbrowse},
        )
    ).public_json()

    serialized = json.dumps(result)
    assert len(serialized.encode("utf-8")) <= MAX_PUBLIC_RESULT_BYTES
    assert "u:p" not in serialized
    assert "secret" not in serialized
    assert "cbm_run_private_capability" not in serialized
    assert "[redacted:truncated]" in serialized


def test_router_stops_on_non_failover_classification_without_trying_next_tool(
    tmp_path: Path,
):
    calls = []

    async def unbrowse(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="failed", classification="auth_required")

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unbrowse, "stagehand": stagehand},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "auth_required"
    assert calls == ["unbrowse"]


def test_model_required_is_terminal_and_never_falls_back_to_prompted_tools(
    tmp_path: Path,
):
    calls = []

    async def stagehand(request):
        calls.append((request.tool_id, dict(request.arguments)))
        return BrowserToolResult(
            outcome="failed",
            classification="model_required",
            message="Stagehand semantic actions require an explicit model",
        )

    async def browser_harness(request):
        calls.append((request.tool_id, dict(request.arguments)))
        raise AssertionError("semantic Stagehand prompt must not fall back")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(
                normalized_claim(
                    browser_tools=[
                        {"id": "unbrowse", "enabled": False},
                        {"id": "stagehand", "enabled": True},
                        {"id": "browser-harness", "enabled": True},
                    ],
                    routing_policy={
                        "mode": "ordered-fallback",
                        "allow_second_browser": False,
                        "max_tool_attempts": 3,
                    },
                )
            ),
            context=run_context(tmp_path),
            action="act",
            arguments={"prompt": "click the login button"},
            adapters={"stagehand": stagehand, "browser-harness": browser_harness},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "model_required"
    assert result.tool_id is None
    assert result.telemetry == ()
    assert calls == []


@pytest.mark.parametrize("action", ["act", "extract", "observe", "agent"])
def test_semantic_stagehand_actions_stop_before_tool_selection_even_when_stagehand_unavailable(
    tmp_path: Path,
    action: str,
):
    calls = []

    async def unbrowse(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="failed", classification="tool_unavailable")

    async def browser_harness(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    contract = routing_contract_from_claim(
        normalized_claim(
            browser_tools=[
                {"id": "unbrowse", "enabled": True},
                {"id": "stagehand", "enabled": False},
                {"id": "browser-harness", "enabled": True},
            ],
            routing_policy={
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 1,
            },
        )
    )

    result = asyncio.run(
        route_browser_action(
            contract=contract,
            context=run_context(tmp_path),
            action=action,
            arguments={"prompt": "click the login button"},
            adapters={"unbrowse": unbrowse, "browser-harness": browser_harness},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "model_required"
    assert result.tool_id is None
    assert result.telemetry == ()
    assert calls == []


def test_unexpected_adapter_exception_is_terminal_redacted_policy_denied(
    tmp_path: Path,
):
    calls = []

    async def unbrowse(request):
        calls.append(request.tool_id)
        raise RuntimeError(
            "boom cbm_run_private_capability https://u:p@example.com?token=secret"
        )

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unbrowse, "stagehand": stagehand},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert result.tool_id == "unbrowse"
    assert calls == ["unbrowse"]
    assert result.telemetry[0]["result_class"] == "policy_denied"
    assert result.telemetry[0]["fallback_reason"] is None
    serialized = json.dumps(result.public_json())
    assert "cbm_run_private_capability" not in serialized
    assert "u:p" not in serialized
    assert "secret" not in serialized
    assert "boom" not in serialized
    assert "RuntimeError" not in serialized
    assert "cbm_run_private_capability" not in result.message
    assert "secret" not in result.message
    assert "boom" not in result.message


def test_invalid_adapter_return_is_terminal_redacted_policy_denied(tmp_path: Path):
    calls = []

    async def unbrowse(request):
        calls.append(request.tool_id)
        return {
            "classification": "tool_unavailable",
            "secret": "cbm_run_private_capability",
            "url": "https://u:p@example.com?token=secret",
        }

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unbrowse, "stagehand": stagehand},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert result.tool_id == "unbrowse"
    assert calls == ["unbrowse"]
    assert result.telemetry[0]["result_class"] == "policy_denied"
    assert result.telemetry[0]["fallback_reason"] is None
    serialized = json.dumps(result.public_json())
    assert "cbm_run_private_capability" not in serialized
    assert "u:p" not in serialized
    assert "secret" not in serialized
    assert "tool_unavailable" not in result.message


def test_explicit_tool_unavailable_result_still_falls_back(tmp_path: Path):
    calls = []

    async def unbrowse(request):
        calls.append(request.tool_id)
        return BrowserToolResult(
            outcome="failed",
            classification="tool_unavailable",
            message="unbrowse binary missing",
        )

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload={"title": "Recovered"},
        )

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unbrowse, "stagehand": stagehand},
        )
    )

    assert result.outcome == "succeeded"
    assert result.tool_id == "stagehand"
    assert calls == ["unbrowse", "stagehand"]
    assert result.telemetry[0]["fallback_reason"] == "tool_unavailable"


def test_sync_adapter_is_never_invoked_and_fails_closed_promptly(tmp_path: Path):
    calls = []

    def blocking_sync(_request):
        calls.append("called")
        time.sleep(60)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    started = time.monotonic()
    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": blocking_sync},
            adapter_timeout_seconds=0.01,
        )
    )

    assert time.monotonic() - started < 1
    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert calls == []


def test_router_times_out_hanging_adapter_and_falls_back_within_bound(tmp_path: Path):
    calls = []

    async def hanging(request):
        calls.append(request.tool_id)
        await asyncio.sleep(60)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            payload={"title": "Recovered"},
        )

    started = time.monotonic()
    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": hanging, "stagehand": stagehand},
            adapter_timeout_seconds=0.01,
        )
    )

    assert time.monotonic() - started < 1
    assert result.outcome == "succeeded"
    assert result.tool_id == "stagehand"
    assert calls == ["unbrowse", "stagehand"]
    assert result.telemetry[0]["result_class"] == "transient_timeout"
    assert result.telemetry[0]["fallback_reason"] == "transient_timeout"


def test_disabled_tool_state_is_preserved_and_skipped_without_attempt_renumbering(
    tmp_path: Path,
):
    contract = routing_contract_from_claim(
        normalized_claim(
            browser_tools=[
                {"id": "unbrowse", "enabled": True},
                {"id": "stagehand", "enabled": False},
                {"id": "browser-harness", "enabled": True},
            ],
            routing_policy={
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 2,
            },
        )
    )
    assert contract is not None
    assert contract.public_json()["browser_tools"] == [
        {"id": "unbrowse", "enabled": True},
        {"id": "stagehand", "enabled": False},
        {"id": "browser-harness", "enabled": True},
    ]
    calls = []

    async def unbrowse(request):
        calls.append((request.tool_id, request.attempt_index))
        return BrowserToolResult(outcome="failed", classification="route_miss")

    async def stagehand(_request):
        raise AssertionError("disabled stagehand must not be invoked")

    async def browser_harness(request):
        calls.append((request.tool_id, request.attempt_index))
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=contract,
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={
                "unbrowse": unbrowse,
                "stagehand": stagehand,
                "browser-harness": browser_harness,
            },
        )
    )

    assert result.outcome == "succeeded"
    assert result.tool_id == "browser-harness"
    assert calls == [("unbrowse", 1), ("browser-harness", 2)]
    assert [item["tool_id"] for item in result.telemetry] == [
        "unbrowse",
        "browser-harness",
    ]


def test_unknown_adapter_classification_fails_closed_without_next_tool(tmp_path: Path):
    calls = []

    async def unknown(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="failed", classification="surprise")

    async def stagehand(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="succeeded", classification="ok")

    result = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unknown, "stagehand": stagehand},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "policy_denied"
    assert calls == ["unbrowse"]


def test_router_enforces_max_attempts_and_no_second_browser_invariant(tmp_path: Path):
    calls = []

    async def unavailable(request):
        calls.append(request.tool_id)
        return BrowserToolResult(outcome="failed", classification="tool_unavailable")

    contract = routing_contract_from_claim(
        normalized_claim(
            routing_policy={
                "mode": "ordered-fallback",
                "allow_second_browser": False,
                "max_tool_attempts": 1,
            }
        )
    )
    result = asyncio.run(
        route_browser_action(
            contract=contract,
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": unavailable, "stagehand": unavailable},
        )
    )

    assert result.outcome == "failed"
    assert result.classification == "tool_unavailable"
    assert calls == ["unbrowse"]

    async def second_browser(request):
        return BrowserToolResult(
            outcome="succeeded",
            classification="ok",
            opened_second_browser=True,
        )

    blocked = asyncio.run(
        route_browser_action(
            contract=routing_contract_from_claim(normalized_claim()),
            context=run_context(tmp_path),
            action="inspect",
            arguments={},
            adapters={"unbrowse": second_browser},
        )
    )

    assert blocked.outcome == "failed"
    assert blocked.classification == "second_browser_attempt"


@pytest.mark.parametrize("max_tool_attempts", [0, 4])
def test_routing_contract_rejects_max_attempts_outside_approved_bounds(
    max_tool_attempts: int,
):
    with pytest.raises(ValueError, match="max_tool_attempts"):
        routing_contract_from_claim(
            normalized_claim(routing_policy={"max_tool_attempts": max_tool_attempts})
        )
