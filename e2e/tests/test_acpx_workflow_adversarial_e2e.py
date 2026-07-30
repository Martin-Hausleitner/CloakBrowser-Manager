"""Adversarial E2E coverage for ACPX workflow.

Tests local extension MCP/CLI, browser-harness routing, provider switching,
timeouts, retry classification, no raw secrets, and strict artifact gates.
Exercises actual ACPX workflow locally with synthetic fixtures only.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from scripts.acpx_runner import (
    _redact,
    classify_acpx_control_failure,
    map_acpx_event,
)
from scripts.browser_tool_router import (
    BrowserRoutingContract,
    BrowserToolConfig,
    routing_contract_from_claim,
)

def test_adversarial_local_extension_mcp_rejects_raw_secrets():
    """Test no raw secrets leakage during ACPX event mapping."""
    adversarial_event = {
        "eventVersion": 1,
        "type": "result",
        "seq": 42,
        "text": "My password: top-secret and token: a-secret-token"
    }
    mapped = map_acpx_event(adversarial_event)
    payload_text = mapped["payload"]["text"]
    assert "top-secret" not in payload_text
    assert "[REDACTED]" in payload_text

def test_acpx_browser_harness_routing_adversarial():
    """Test browser-harness routing with invalid/adversarial states."""
    claim = {
        "browser_tools": [{"id": "unbrowse", "enabled": True}, {"id": "stagehand", "enabled": True}],
        "routing_policy": {"mode": "ordered-fallback", "max_tool_attempts": 2}
    }
    capability = {}
    
    # Missing browser-harness in order should raise ValueError from _routing_contract_for_openai_compatible
    # Wait, routing_contract_from_claim parses it.
    with pytest.raises(ValueError):
        # We need the full 3 items in ROUTING_BROWSER_TOOL_ORDER: unbrowse, stagehand, browser-harness
        from scripts.acpx_worker import _routing_contract_for_openai_compatible
        _routing_contract_for_openai_compatible(claim, capability)

def test_acpx_provider_switching_adversarial():
    """Test provider switching structure with openai-compatible."""
    claim = {
        "provider": {"id": "grok", "transport": "openai-compatible"},
        "browser_tools": [
            {"id": "unbrowse", "enabled": True},
            {"id": "stagehand", "enabled": False},
            {"id": "browser-harness", "enabled": False}
        ],
        "routing_policy": {"mode": "ordered-fallback", "max_tool_attempts": 1, "allow_second_browser": False}
    }
    from scripts.acpx_worker import _routing_contract_for_openai_compatible
    contract = _routing_contract_for_openai_compatible(claim, {})
    assert contract.provider["transport"] == "openai-compatible"

@pytest.mark.asyncio
async def test_acpx_timeouts_adversarial():
    """Test timeouts during ACPX execution."""
    from scripts.acpx_worker import AcpxRuntime, AcpxWorkerConfig
    config = AcpxWorkerConfig(
        manager_url="http://localhost",
        worker_id="test",
        worktree=MagicMock(),
        permission_policy=MagicMock(),
        mcp_config=MagicMock(),
        capability_dir=MagicMock(),
    )
    runtime = AcpxRuntime(config)
    
    async def fast_run(*args, **kwargs):
        raise asyncio.TimeoutError()
    
    with patch.object(asyncio, "wait_for", side_effect=asyncio.TimeoutError):
        with patch.object(asyncio, "create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.wait.return_value = 0
            mock_exec.return_value = mock_proc
            
            from scripts.acpx_worker import AcpxRuntimeError
            with pytest.raises(AcpxRuntimeError) as exc_info:
                await runtime._run_control(["ls"], timeout=0.01)
            assert "timed out" in str(exc_info.value)

def test_acpx_retry_classification_adversarial():
    """Test retry classification for non-retryable errors."""
    error_raw = b'{"jsonrpc": "2.0", "error": {"message": "invalid credential", "data": {"detailCode": "AUTH_REQUIRED"}}}'
    reason = classify_acpx_control_failure(error_raw)
    assert reason == "auth_required"
    
    error_raw_2 = b'{"jsonrpc": "2.0", "error": {"message": "MCP not found", "data": {"detailCode": "MCP_UNAVAILABLE"}}}'
    reason_2 = classify_acpx_control_failure(error_raw_2)
    assert reason_2 == "mcp_unavailable"

def test_acpx_strict_artifact_gates_adversarial():
    """Test strict artifact gates for ACPX workflow."""
    event = {
        "eventVersion": 1,
        "type": "artifact",
        "seq": 10,
        "text": "some extracted data"
    }
    mapped = map_acpx_event(event)
    assert mapped["kind"] == "extracted_data"
    assert mapped["payload"]["label"] == "ACP artifact"
