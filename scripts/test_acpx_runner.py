from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.models import TaskOutputCreate
from scripts.acpx_runner import (
    ACPX_VERSION,
    build_close_command,
    build_ensure_command,
    build_preflight_close_command,
    build_preflight_ensure_command,
    build_prompt_command,
    classify_acpx_control_failure,
    derive_session_name,
    map_acpx_event,
    map_acpx_jsonrpc_update,
    parse_acpx_event,
    parse_acpx_frame,
    validate_acpx_version,
    validate_mcp_config,
    validate_permission_policy,
    validate_preflight_mcp_config,
)


def test_build_close_command_is_bounded(tmp_path: Path):
    session_name = derive_session_name("doctor-test")
    command = build_close_command(
        executable="acpx",
        cwd=tmp_path,
        agent="cursor",
        session_name=session_name,
    )
    assert command == [
        "acpx",
        "--cwd",
        str(tmp_path),
        "--format",
        "json",
        "--json-strict",
        "cursor",
        "sessions",
        "close",
        session_name,
    ]


def test_classify_acpx_control_failure_redacts_auth_and_mcp():
    auth = (
        b'{"jsonrpc":"2.0","id":null,"error":{"message":"no matching credentials found",'
        b'"data":{"detailCode":"AUTH_REQUIRED"}}}'
    )
    mcp = (
        b'{"jsonrpc":"2.0","id":null,"error":{"message":"MCP server failed",'
        b'"data":{"detailCode":"RUNTIME"}}}'
    )
    assert classify_acpx_control_failure(auth) == "auth_required"
    assert classify_acpx_control_failure(mcp) == "mcp_unavailable"
    assert classify_acpx_control_failure(b"not json") == "protocol_error"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            b'{"jsonrpc":"2.0","id":null,"error":{"message":"adapter binary missing",'
            b'"data":{"detailCode":"COMMAND_NOT_FOUND"}}}',
            "adapter_unavailable",
        ),
        (
            b'{"jsonrpc":"2.0","id":null,"error":{"message":"requires acpx 0.12.1; found 9.9.9",'
            b'"data":{"detailCode":"VERSION_MISMATCH"}}}',
            "version_mismatch",
        ),
        (
            b'{"jsonrpc":"2.0","id":null,"error":{"message":"unexpected frame",'
            b'"data":{"detailCode":"PROTOCOL_ERROR"}}}',
            "protocol_error",
        ),
    ],
)
def test_classify_acpx_control_failure_distinguishes_adapter_version_and_protocol(
    raw: bytes, expected: str
):
    assert classify_acpx_control_failure(raw) == expected


def test_version_is_pinned_and_rejects_drift():
    assert ACPX_VERSION == "0.12.1"
    assert validate_acpx_version("acpx 0.12.1\n") == ACPX_VERSION
    with pytest.raises(ValueError, match="requires acpx 0.12.1"):
        validate_acpx_version("acpx 0.13.0")


def test_session_name_is_opaque_stable_and_safe():
    first = derive_session_name("task-session/customer-visible-id")
    second = derive_session_name("task-session/customer-visible-id")
    other = derive_session_name("task-session/other")

    assert first == second
    assert first != other
    assert first.startswith("cbm-")
    assert len(first) == 36
    assert "customer" not in first
    assert set(first) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


def test_ensure_command_is_strict_and_repo_scoped(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)
    command = build_ensure_command(
        executable="acpx",
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        permission_policy=policy,
        mcp_config=mcp,
    )

    assert command[:7] == [
        "acpx", "--cwd", str(tmp_path.resolve()), "--format", "json", "--json-strict",
        "--suppress-reads",
    ]
    assert "--auth-policy" in command and command[command.index("--auth-policy") + 1] == "fail"
    assert "--no-terminal" in command
    assert command[command.index("--permission-policy") + 1] == str(policy.resolve())
    assert command[command.index("--mcp-config") + 1] == str(mcp.resolve())
    assert command[-5:] == [
        "codex", "sessions", "ensure", "--name", "cbm-0123456789abcdef0123456789abcdef",
    ]


def test_preflight_ensure_command_requires_exact_empty_mcp_config(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "preflight-mcp.json"
    mcp.write_text('{"mcpServers":[]}', encoding="utf-8")
    os.chmod(mcp, 0o600)

    command = build_preflight_ensure_command(
        executable="acpx",
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        permission_policy=policy,
        mcp_config=mcp,
    )

    assert validate_preflight_mcp_config(mcp) == mcp.resolve()
    assert command[command.index("--mcp-config") + 1] == str(mcp.resolve())
    assert command[-5:] == [
        "codex", "sessions", "ensure", "--name", "cbm-0123456789abcdef0123456789abcdef",
    ]


def test_preflight_close_command_requires_same_empty_mcp_config(tmp_path: Path):
    mcp = tmp_path / "preflight-mcp.json"
    mcp.write_text('{"mcpServers":[]}', encoding="utf-8")
    os.chmod(mcp, 0o600)

    command = build_preflight_close_command(
        executable="acpx",
        cwd=tmp_path,
        agent="codex",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        mcp_config=mcp,
    )

    assert command[command.index("--mcp-config") + 1] == str(mcp.resolve())
    assert command[-2:] == ["close", "cbm-0123456789abcdef0123456789abcdef"]


def test_prompt_command_uses_stdin_and_fail_closed_permissions(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)

    command = build_prompt_command(
        executable="acpx",
        cwd=tmp_path,
        agent="cursor",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        permission_policy=policy,
        mcp_config=mcp,
    )

    assert "--approve-all" not in command
    assert command[-2:] == ["--file", "-"]
    assert command[command.index("--non-interactive-permissions") + 1] == "fail"
    assert command[command.index("--permission-policy") + 1] == str(policy.resolve())
    assert command[command.index("--mcp-config") + 1] == str(mcp.resolve())
    assert "--suppress-reads" in command
    assert "cursor" in command


def test_commands_reject_unsupported_agent_and_relative_worktree(tmp_path: Path):
    with pytest.raises(ValueError, match="unsupported ACP agent"):
        build_ensure_command(
            executable="acpx",
            cwd=tmp_path,
            agent="shell",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            permission_policy=tmp_path / "missing-policy",
            mcp_config=tmp_path / "missing-mcp",
        )
    with pytest.raises(ValueError, match="absolute directory"):
        build_ensure_command(
            executable="acpx",
            cwd=Path("relative"),
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            permission_policy=tmp_path / "missing-policy",
            mcp_config=tmp_path / "missing-mcp",
        )


def test_mcp_config_must_be_private_and_only_contain_mcp_servers(tmp_path: Path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(config, 0o600)
    assert validate_mcp_config(config) == config.resolve()

    os.chmod(config, 0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        validate_mcp_config(config)

    os.chmod(config, 0o600)
    config.write_text(json.dumps({"mcpServers": {}, "authorization": "secret"}), encoding="utf-8")
    with pytest.raises(ValueError, match="only mcpServers"):
        validate_mcp_config(config)


def test_real_run_builder_rejects_empty_preflight_mcp_config(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    empty = tmp_path / "empty-mcp.json"
    empty.write_text('{"mcpServers":[]}', encoding="utf-8")
    os.chmod(empty, 0o600)

    with pytest.raises(ValueError, match="exactly the cloakbrowser server"):
        build_ensure_command(
            executable="acpx",
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            permission_policy=policy,
            mcp_config=empty,
        )


def test_preflight_builder_rejects_cloakbrowser_mcp_and_weak_files(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    cloakbrowser = tmp_path / "cloakbrowser-mcp.json"
    cloakbrowser.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(cloakbrowser, 0o600)

    with pytest.raises(ValueError, match="empty mcpServers"):
        build_preflight_ensure_command(
            executable="acpx",
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            permission_policy=policy,
            mcp_config=cloakbrowser,
        )

    loose = tmp_path / "loose-mcp.json"
    loose.write_text('{"mcpServers":[]}', encoding="utf-8")
    os.chmod(loose, 0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        validate_preflight_mcp_config(loose)

    bad_json = tmp_path / "bad-mcp.json"
    bad_json.write_text('{"mcpServers":{}', encoding="utf-8")
    os.chmod(bad_json, 0o600)
    with pytest.raises(ValueError, match="valid JSON"):
        validate_preflight_mcp_config(bad_json)

    permissive = tmp_path / "permissive-policy.json"
    permissive.write_text('{"defaultAction":"approve"}', encoding="utf-8")
    os.chmod(permissive, 0o600)
    with pytest.raises(ValueError, match="deny or escalate"):
        build_preflight_ensure_command(
            executable="acpx",
            cwd=tmp_path,
            agent="codex",
            session_name="cbm-0123456789abcdef0123456789abcdef",
            permission_policy=permissive,
            mcp_config=bad_json,
        )


def test_mcp_config_rejects_arbitrary_servers_and_secret_env(tmp_path: Path):
    config = tmp_path / "mcp.json"
    os.chmod(tmp_path, 0o700)

    config.write_text(
        json.dumps({"mcpServers": [{"name": "shell", "command": "bash"}]}),
        encoding="utf-8",
    )
    os.chmod(config, 0o600)
    with pytest.raises(ValueError, match="exactly the cloakbrowser server"):
        validate_mcp_config(config)

    config.write_text(
        json.dumps(
            {
                "mcpServers": [
                    {
                        "name": "cloakbrowser",
                        "command": "cbm-mcp",
                        "env": {"CBM_AGENT_TOKEN": "secret"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="command and optional args"):
        validate_mcp_config(config)

    config.write_text(
        json.dumps(
            {
                "mcpServers": [
                    {
                        "name": "cloakbrowser",
                        "command": "cbm-mcp",
                        "args": ["--token", "top-secret"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not carry credentials"):
        validate_mcp_config(config)


def test_permission_policy_rejects_auto_approval_and_default_allow(tmp_path: Path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps({"autoApprove": ["read"], "defaultAction": "approve"}),
        encoding="utf-8",
    )
    os.chmod(policy, 0o600)
    with pytest.raises(ValueError, match="must not auto-approve"):
        validate_permission_policy(policy)

    policy.write_text(json.dumps({"defaultAction": "approve"}), encoding="utf-8")
    with pytest.raises(ValueError, match="deny or escalate"):
        validate_permission_policy(policy)

    policy.write_text(
        json.dumps(
            {
                "autoApprove": [],
                "autoDeny": ["execute", "write"],
                "escalate": ["read"],
                "defaultAction": "deny",
            }
        ),
        encoding="utf-8",
    )
    assert validate_permission_policy(policy) == policy.resolve()


def test_event_parser_enforces_envelope_version_and_size():
    event = parse_acpx_event(
        json.dumps(
            {
                "eventVersion": 1,
                "sessionId": "session-1",
                "requestId": "request-1",
                "seq": 4,
                "stream": "assistant",
                "type": "assistant_message",
                "text": "Finished",
            }
        )
    )
    assert event["seq"] == 4
    with pytest.raises(ValueError, match="eventVersion"):
        parse_acpx_event('{"eventVersion":2,"type":"assistant_message"}')
    large_event = json.dumps(
        {
            "eventVersion": 1,
            "sessionId": "session-1",
            "requestId": "request-1",
            "seq": 5,
            "stream": "assistant",
            "type": "assistant_message",
            "text": "x" * 100_000,
        }
    )
    assert parse_acpx_event(large_event)["seq"] == 5
    with pytest.raises(ValueError, match="too large"):
        parse_acpx_event(" " * 1_048_577)


@pytest.mark.parametrize(
    ("event", "expected_kind"),
    [
        ({"type": "assistant_message", "text": "Finished"}, "summary"),
        ({"type": "thinking", "text": "Inspecting"}, "observation"),
        ({"type": "tool_call", "toolName": "browser_navigate"}, "action"),
        ({"type": "tool_result", "text": "Page title"}, "observation"),
        ({"type": "permission_request", "text": "Approve write"}, "approval"),
        ({"type": "error", "message": "Provider failed"}, "error"),
        ({"type": "usage", "latencyMs": 42}, "metric"),
        ({"type": "diff", "text": "2 files changed"}, "extracted_data"),
    ],
)
def test_event_mapping_uses_existing_typed_output_contract(event: dict, expected_kind: str):
    mapped = map_acpx_event({"eventVersion": 1, "seq": 7, **event})
    assert mapped["kind"] == expected_kind
    assert mapped["idempotency_key"] == "acpx-7"
    assert mapped["summary"]


def test_event_mapping_redacts_common_secret_values():
    mapped = map_acpx_event(
        {
            "eventVersion": 1,
            "seq": 9,
            "type": "error",
            "message": "Authorization: Bearer top-secret-token password=hunter2",
        }
    )
    encoded = json.dumps(mapped)
    assert "top-secret-token" not in encoded
    assert "hunter2" not in encoded
    assert "[REDACTED]" in encoded
    TaskOutputCreate.model_validate(mapped)


@pytest.mark.parametrize(
    "message",
    [
        "Password reset flow passed",
        "Token count is 42",
        "Secret management documentation updated",
    ],
)
def test_event_mapping_preserves_benign_security_words(message: str):
    mapped = map_acpx_event(
        {"eventVersion": 1, "seq": 10, "type": "assistant_message", "text": message}
    )
    assert mapped["summary"] == message
    TaskOutputCreate.model_validate(mapped)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "assistant_message", "text": "Finished"},
        {"type": "thinking", "text": "Inspecting"},
        {"type": "tool_call", "toolName": "browser_navigate"},
        {"type": "permission_request", "text": "Approve write"},
        {"type": "error", "message": "Provider failed"},
        {"type": "usage", "latencyMs": 42},
        {"type": "diff", "text": "2 files changed"},
        {"type": "future_additive_event", "detail": "Still running"},
    ],
)
def test_every_mapped_event_validates_as_manager_task_output(event: dict):
    mapped = map_acpx_event({"eventVersion": 1, "seq": 11, **event})
    TaskOutputCreate.model_validate(mapped)


# =============================================================================
# ACP JSON-RPC session/update frame handling tests (acpx 0.12.1 contract)
# =============================================================================


def jsonrpc_update(update: dict, *, seq: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {"sessionId": "cbm-xxx", "update": update},
        "_seq": seq,
    }


def test_parse_acpx_frame_accepts_jsonrpc_and_legacy_event_version():
    legacy = {
        "eventVersion": 1,
        "sessionId": "session-1",
        "requestId": "request-1",
        "seq": 4,
        "stream": "assistant",
        "type": "assistant_message",
        "text": "Finished",
    }
    frame = {"jsonrpc": "2.0", "method": "session/prompt", "id": "req-1"}

    assert parse_acpx_frame(json.dumps(legacy))["eventVersion"] == 1
    assert parse_acpx_frame(json.dumps(frame))["method"] == "session/prompt"
    with pytest.raises(ValueError, match="supported envelope"):
        parse_acpx_frame(json.dumps({"eventVersion": 2, "type": "assistant_message"}))


def test_jsonrpc_agent_message_chunk_is_not_a_terminal_summary():
    mapped = map_acpx_jsonrpc_update(
        jsonrpc_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Hello from ACP"},
            }
        )
    )

    assert mapped is not None
    assert mapped["kind"] == "status"
    assert mapped["summary"] == "Hello from ACP"
    TaskOutputCreate.model_validate(mapped)


def test_jsonrpc_agent_thought_chunk_maps_to_observation_and_redacts():
    mapped = map_acpx_jsonrpc_update(
        jsonrpc_update(
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "Authorization: Bearer top-secret"},
            }
        )
    )

    assert mapped is not None
    assert mapped["kind"] == "observation"
    encoded = json.dumps(mapped)
    assert "top-secret" not in encoded
    assert "[REDACTED]" in encoded
    TaskOutputCreate.model_validate(mapped)


def test_jsonrpc_tool_call_maps_to_action_with_safe_fields():
    mapped = map_acpx_jsonrpc_update(
        jsonrpc_update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc1",
                "title": "Navigate browser",
                "status": "pending",
                "kind": "execute",
                "rawInput": {"authorization": "Bearer top-secret"},
            }
        )
    )

    assert mapped is not None
    assert mapped["kind"] == "action"
    assert mapped["payload"] == {"name": "Navigate browser"}
    assert "top-secret" not in json.dumps(mapped)
    TaskOutputCreate.model_validate(mapped)


def test_jsonrpc_tool_call_update_maps_to_observation():
    mapped = map_acpx_jsonrpc_update(
        jsonrpc_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc1",
                "status": "completed",
                "content": {"type": "text", "text": "Navigation OK"},
                "rawOutput": {"ok": True},
            }
        )
    )

    assert mapped is not None
    assert mapped["kind"] == "observation"
    assert mapped["summary"] == "Navigation OK"
    TaskOutputCreate.model_validate(mapped)


def test_jsonrpc_usage_update_maps_to_metric():
    mapped = map_acpx_jsonrpc_update(
        jsonrpc_update(
            {
                "sessionUpdate": "usage_update",
                "used": 123,
                "size": 400,
                "cost": 0.01,
                "_meta": {"model": "claude"},
            }
        )
    )

    assert mapped is not None
    assert mapped["kind"] == "metric"
    assert mapped["payload"] == {"name": "usage", "value": 123, "unit": "tokens"}
    TaskOutputCreate.model_validate(mapped)


def test_jsonrpc_prompt_result_does_not_invent_summary_without_accumulated_text():
    frame = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "result": {
            "stopReason": "end_turn",
            "usage": {"used": 150, "size": 300},
        },
    }

    with pytest.raises(ValueError, match="unsupported ACPX JSON-RPC frame"):
        map_acpx_event(frame)


def test_prompt_command_has_no_duplicate_cwd(tmp_path: Path):
    """build_prompt_command must not emit duplicate --cwd flags"""
    policy = tmp_path / "policy.json"
    policy.write_text('{"defaultAction":"deny"}', encoding="utf-8")
    os.chmod(policy, 0o600)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]}
        ),
        encoding="utf-8",
    )
    os.chmod(mcp, 0o600)

    command = build_prompt_command(
        executable="acpx",
        cwd=tmp_path,
        agent="cursor",
        session_name="cbm-0123456789abcdef0123456789abcdef",
        permission_policy=policy,
        mcp_config=mcp,
    )

    # Count --cwd occurrences - must be exactly 1
    cwd_count = command.count("--cwd")
    assert cwd_count == 1, f"Expected exactly one --cwd flag, found {cwd_count}: {command}"
