#!/usr/bin/env python3
"""Fail-closed ACPX command and event adapter for CloakBrowser Manager.

The Manager remains the policy and lifecycle authority. This module only
builds pinned ACPX invocations and translates ACP events into the Manager's
existing typed task-output contract. Prompts are intentionally passed through
stdin by the caller and never embedded in command arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ACPX_VERSION = "0.12.1"
ACP_SDK_CONTRACT_BASELINE = "1.2.1"
EVENT_VERSION = 1
MAX_EVENT_BYTES = 1_048_576
LEGACY_SUPPORTED_AGENTS = frozenset(
    {"codex", "claude", "cursor", "grok-build", "opencode"}
)
ACPX_META_COMMANDS = frozenset(
    {
        "prompt",
        "exec",
        "cancel",
        "set-mode",
        "set",
        "status",
        "sessions",
        "config",
        "compare",
        "flow",
    }
)
_SAFE_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_SESSION_RE = re.compile(r"^cbm-[a-f0-9]{32}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)(?:password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;]+"
    ),
)
_SENSITIVE_ARG_RE = re.compile(
    r"(?i)(?:authorization|bearer|password|secret|api[_-]?key|token|cookie|credential)"
)


def parse_acpx_configured_agent_ids(payload: Any) -> tuple[str, ...]:
    """Extract only safe configured agent names from `acpx config show` JSON."""
    if not isinstance(payload, dict):
        raise ValueError("ACPX config JSON must be an object")
    raw_agents = payload.get("agents", {})
    names: list[str] = []
    if raw_agents in (None, {}):
        return ()
    if isinstance(raw_agents, dict):
        names = [str(name) for name in raw_agents]
    elif isinstance(raw_agents, list):
        for item in raw_agents:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("ACPX config agents must expose safe names")
            names.append(str(item["name"]))
    else:
        raise ValueError("ACPX config agents must be an object or array")
    return tuple(_unique_sorted(validate_acpx_agent_id(name) for name in names))


def parse_acpx_advertised_agent_ids(help_text: str) -> tuple[str, ...]:
    """Parse safe ACP subcommand names advertised by ACPX help output."""
    names: list[str] = []
    in_commands = False
    for line in str(help_text or "").splitlines():
        if not in_commands:
            if line == "Commands:":
                in_commands = True
            continue
        if line and not line[0].isspace():
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        match = re.match(r"^([a-z0-9][a-z0-9._-]{0,63})(?:\s|$)", stripped)
        if match is None:
            continue
        name = match.group(1)
        if name in ACPX_META_COMMANDS:
            continue
        names.append(name)
    return tuple(_unique_sorted(names))


def discover_acpx_agent_ids(*, help_text: str, config_payload: Any) -> tuple[str, ...]:
    """Merge advertised ACP agents with safe operator-configured agent names."""
    discovered = set(parse_acpx_advertised_agent_ids(help_text))
    discovered.update(parse_acpx_configured_agent_ids(config_payload))
    if not discovered:
        raise ValueError("ACPX discovery found no safe ACP agents")
    return tuple(_unique_sorted(discovered))


def _unique_sorted(values: Any) -> list[str]:
    return sorted(dict.fromkeys(str(value) for value in values))

def validate_acpx_version(output: str) -> str:
    """Accept only the reviewed ACPX runtime version."""
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", output or "")
    actual = match.group(1) if match else "unknown"
    if actual != ACPX_VERSION:
        raise ValueError(f"CloakBrowser requires acpx {ACPX_VERSION}; found {actual}")
    return actual


def derive_session_name(task_session_id: str) -> str:
    """Derive an opaque repo-safe ACPX session name without leaking user IDs."""
    value = str(task_session_id or "").strip()
    if not value:
        raise ValueError("task session id is required")
    return f"cbm-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def validate_acpx_agent_id(agent: str) -> str:
    """Accept one safe ACPX ACP subcommand token; reject ACPX meta commands."""
    value = str(agent or "").strip()
    if _SAFE_AGENT_RE.fullmatch(value) is None:
        raise ValueError(f"unsafe ACP agent id: {value or 'empty'}")
    if value in ACPX_META_COMMANDS:
        raise ValueError(f"unsupported ACP agent: {value}")
    return value


def _validate_agent(agent: str) -> str:
    return validate_acpx_agent_id(agent)


def _validate_cwd(cwd: Path) -> Path:
    path = Path(cwd)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("ACPX cwd must be an existing absolute directory")
    return path.resolve()


def _validate_session_name(session_name: str) -> str:
    value = str(session_name or "")
    if _SESSION_RE.fullmatch(value) is None:
        raise ValueError("ACPX session name must be an opaque cbm session id")
    return value


def _validate_private_file(path: Path, *, label: str) -> Path:
    value = Path(path)
    if not value.is_absolute():
        value = value.resolve()
    if not value.is_file():
        raise ValueError(f"{label} must be an existing file")
    if value.stat().st_mode & 0o077:
        raise ValueError(f"{label} must use mode 0600")
    return value.resolve()


def validate_mcp_config(path: Path) -> Path:
    """Require the single bounded CloakBrowser MCP stdio descriptor."""
    value = _validate_private_file(path, label="MCP config")
    try:
        parsed = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("MCP config must be valid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"mcpServers"}:
        raise ValueError("MCP config may contain only mcpServers")
    if not isinstance(parsed["mcpServers"], list):
        raise ValueError("MCP config mcpServers must be an array")
    servers = parsed["mcpServers"]
    if (
        len(servers) != 1
        or not isinstance(servers[0], dict)
        or servers[0].get("name") != "cloakbrowser"
    ):
        raise ValueError("MCP config must contain exactly the cloakbrowser server")
    descriptor = servers[0]
    if not set(descriptor) <= {"name", "command", "args"}:
        raise ValueError("cloakbrowser MCP descriptor allows name, command and optional args only")
    command = descriptor.get("command")
    if not isinstance(command, str) or Path(command).name != "cbm-mcp":
        raise ValueError("cloakbrowser MCP command must be cbm-mcp")
    args = descriptor.get("args", [])
    if not isinstance(args, list) or any(
        not isinstance(item, str) or not item or len(item) > 256 for item in args
    ):
        raise ValueError("cloakbrowser MCP args must be bounded non-empty strings")
    if any(_SENSITIVE_ARG_RE.search(item) for item in args):
        raise ValueError("cloakbrowser MCP args must not carry credentials")
    return value


def validate_preflight_mcp_config(path: Path) -> Path:
    """Require the sterile preflight descriptor that disables all MCP servers."""
    value = _validate_private_file(path, label="preflight MCP config")
    try:
        parsed = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("preflight MCP config must be valid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"mcpServers"}:
        raise ValueError("preflight MCP config may contain only mcpServers")
    if parsed["mcpServers"] != []:
        raise ValueError("preflight MCP config must contain empty mcpServers")
    return value


def validate_permission_policy(path: Path) -> Path:
    """Accept only a private policy with no automatic approvals."""
    value = _validate_private_file(path, label="permission policy")
    try:
        parsed = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("permission policy must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("permission policy must be an object")
    allowed_keys = {"autoApprove", "autoDeny", "escalate", "defaultAction"}
    if not set(parsed) <= allowed_keys:
        raise ValueError("permission policy contains unsupported keys")
    auto_approve = parsed.get("autoApprove", [])
    if auto_approve not in (None, []):
        raise ValueError("permission policy must not auto-approve tools")
    for key in ("autoDeny", "escalate"):
        rules = parsed.get(key, [])
        if not isinstance(rules, list) or any(
            not isinstance(rule, str) or not rule or len(rule) > 128 for rule in rules
        ):
            raise ValueError(f"permission policy {key} must contain bounded rules")
    if parsed.get("defaultAction") not in {"deny", "escalate"}:
        raise ValueError("permission policy defaultAction must be deny or escalate")
    return value


def build_ensure_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
    permission_policy: Path,
    mcp_config: Path,
) -> list[str]:
    """Build the deterministic command that creates or resumes one ACP session."""
    safe_cwd = _validate_cwd(cwd)
    safe_agent = _validate_agent(agent)
    safe_session = _validate_session_name(session_name)
    policy = validate_permission_policy(permission_policy)
    mcp = validate_mcp_config(mcp_config)
    return [
        str(executable),
        "--cwd",
        str(safe_cwd),
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--auth-policy",
        "fail",
        "--no-terminal",
        "--non-interactive-permissions",
        "fail",
        "--permission-policy",
        str(policy),
        "--mcp-config",
        str(mcp),
        safe_agent,
        "sessions",
        "ensure",
        "--name",
        safe_session,
    ]


def build_preflight_ensure_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
    permission_policy: Path,
    mcp_config: Path,
) -> list[str]:
    """Build an ACP session readiness probe with ambient MCP config disabled."""
    safe_cwd = _validate_cwd(cwd)
    safe_agent = _validate_agent(agent)
    safe_session = _validate_session_name(session_name)
    policy = validate_permission_policy(permission_policy)
    mcp = validate_preflight_mcp_config(mcp_config)
    return [
        str(executable),
        "--cwd",
        str(safe_cwd),
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--auth-policy",
        "fail",
        "--no-terminal",
        "--non-interactive-permissions",
        "fail",
        "--permission-policy",
        str(policy),
        "--mcp-config",
        str(mcp),
        safe_agent,
        "sessions",
        "ensure",
        "--name",
        safe_session,
    ]


def build_prompt_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
    permission_policy: Path,
    mcp_config: Path,
    timeout_seconds: int | None = None,
) -> list[str]:
    """Build a prompt invocation that reads the prompt from stdin and fails closed."""
    safe_cwd = _validate_cwd(cwd)
    safe_agent = _validate_agent(agent)
    safe_session = _validate_session_name(session_name)
    policy = validate_permission_policy(permission_policy)
    mcp = validate_mcp_config(mcp_config)
    command = [
        str(executable),
        "--cwd",
        str(safe_cwd),
        "--format",
        "json",
        "--json-strict",
        "--auth-policy",
        "fail",
        "--no-terminal",
        "--suppress-reads",
        "--non-interactive-permissions",
        "fail",
        "--permission-policy",
        str(policy),
        "--mcp-config",
        str(mcp),
    ]
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise ValueError("ACPX timeout must be a positive integer")
        command.extend(["--timeout", str(timeout_seconds)])
    command.extend(
        [
        safe_agent,
        "-s",
        safe_session,
        "--file",
        "-",
        ]
    )
    return command


def build_close_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
) -> list[str]:
    """Build deterministic cleanup for a short-lived ACP session."""
    return [
        str(executable),
        "--cwd",
        str(_validate_cwd(cwd)),
        "--format",
        "json",
        "--json-strict",
        _validate_agent(agent),
        "sessions",
        "close",
        _validate_session_name(session_name),
    ]


def build_preflight_close_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
    mcp_config: Path,
) -> list[str]:
    """Build cleanup for a sterile preflight session without ambient MCP config."""
    return [
        str(executable),
        "--cwd",
        str(_validate_cwd(cwd)),
        "--format",
        "json",
        "--json-strict",
        "--mcp-config",
        str(validate_preflight_mcp_config(mcp_config)),
        _validate_agent(agent),
        "sessions",
        "close",
        _validate_session_name(session_name),
    ]


def classify_acpx_control_failure(raw: bytes) -> str:
    """Map raw JSON-RPC control failures to a small non-secret reason code."""
    text = raw.decode("utf-8", errors="replace")
    detail_code = ""
    message = ""
    for line in text.splitlines():
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
            continue
        error = frame.get("error")
        if not isinstance(error, dict):
            continue
        message = str(error.get("message") or "").lower()
        data = error.get("data")
        if isinstance(data, dict):
            detail_code = str(data.get("detailCode") or "").upper()
        break
    if detail_code == "AUTH_REQUIRED" or "credential" in message or "auth" in message:
        return "auth_required"
    if "MCP" in detail_code or "mcp" in message:
        return "mcp_unavailable"
    if detail_code == "VERSION_MISMATCH" or "version mismatch" in message:
        return "version_mismatch"
    if "requires acpx" in message and "found" in message:
        return "version_mismatch"
    if detail_code in {"AGENT_NOT_FOUND", "SPAWN_FAILED", "COMMAND_NOT_FOUND"}:
        return "adapter_unavailable"
    if "not found" in message or "spawn" in message or "executable" in message:
        return "adapter_unavailable"
    return "protocol_error"


def parse_acpx_event(line: str) -> dict[str, Any]:
    """Parse one bounded, versioned ACPX NDJSON envelope."""
    encoded = (line or "").encode("utf-8")
    if len(encoded) > MAX_EVENT_BYTES:
        raise ValueError("ACPX event is too large")
    try:
        event = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("ACPX event is not valid JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("ACPX event must be an object")
    if event.get("eventVersion") != EVENT_VERSION:
        raise ValueError(f"unsupported ACPX eventVersion: {event.get('eventVersion')}")
    if not isinstance(event.get("type"), str) or not event["type"]:
        raise ValueError("ACPX event type is required")
    return event


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in ("password", "secret", "token", "authorization", "cookie")):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _event_text(event: dict[str, Any], fallback: str) -> str:
    for key in ("text", "message", "summary", "detail"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return str(_redact(value.strip()))[:500]
    return fallback


def parse_acpx_frame(line: str) -> dict[str, Any]:
    """Parse one bounded ACPX line, accepting legacy envelopes and JSON-RPC frames."""
    encoded = (line or "").encode("utf-8")
    if len(encoded) > MAX_EVENT_BYTES:
        raise ValueError("ACPX frame is too large")
    try:
        frame = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("ACPX frame is not valid JSON") from exc
    if not isinstance(frame, dict):
        raise ValueError("ACPX frame must be an object")
    if frame.get("eventVersion") == EVENT_VERSION:
        return parse_acpx_event(line)
    if frame.get("jsonrpc") != "2.0":
        raise ValueError("ACPX frame is not a supported envelope")
    return frame


def _jsonrpc_update(frame: dict[str, Any]) -> dict[str, Any] | None:
    if frame.get("jsonrpc") != "2.0" or frame.get("method") != "session/update":
        return None
    params = frame.get("params")
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    if not isinstance(update, dict):
        return None
    if not isinstance(update.get("sessionUpdate"), str):
        return None
    return update


def _jsonrpc_seq(frame: dict[str, Any]) -> int:
    seq = frame.get("_seq")
    if isinstance(seq, int) and seq >= 0:
        return seq
    params = frame.get("params")
    if isinstance(params, dict):
        update = params.get("update")
        if isinstance(update, dict) and isinstance(update.get("seq"), int) and update["seq"] >= 0:
            return update["seq"]
    return 0


def _content_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return str(_redact(value["text"].strip()))[:500]
        if isinstance(value.get("text"), str):
            return str(_redact(value["text"].strip()))[:500]
    if isinstance(value, list):
        parts = [_content_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()[:500]
    if isinstance(value, str):
        return str(_redact(value.strip()))[:500]
    return ""


def map_acpx_jsonrpc_update(frame: dict[str, Any]) -> dict[str, Any] | None:
    """Map ACPX 0.12 JSON-RPC session/update notifications to typed outputs."""
    update = _jsonrpc_update(frame)
    if update is None:
        return None
    update_type = str(update["sessionUpdate"])
    seq = _jsonrpc_seq(frame)
    base = {"idempotency_key": f"acpx-jsonrpc-{seq}", "summary": update_type.replace("_", " ")}

    if update_type == "agent_message_chunk":
        text = _content_text(update.get("content")) or "Assistant message chunk"
        return {**base, "summary": text, "kind": "status", "payload": {"status": "running", "detail": text}}
    if update_type == "agent_thought_chunk":
        text = _content_text(update.get("content")) or "Agent thought"
        return {**base, "summary": text, "kind": "observation", "payload": {"text": text}}
    if update_type == "tool_call":
        name = str(_redact(update.get("title") or update.get("kind") or "ACP tool"))[:200]
        # Keep ACP-internal correlation fields outside the public action contract.
        return {**base, "summary": name, "kind": "action", "payload": {"name": name}}
    if update_type == "tool_call_update":
        text = _content_text(update.get("content")) or str(_redact(update.get("status") or "Tool call update"))[:500]
        return {**base, "summary": text, "kind": "observation", "payload": {"text": text}}
    if update_type == "usage_update":
        usage = update.get("usage") if isinstance(update.get("usage"), dict) else update
        used = usage.get("used", usage.get("promptTokens", 0)) if isinstance(usage, dict) else 0
        size = usage.get("size", usage.get("completionTokens", 0)) if isinstance(usage, dict) else 0
        return {
            **base,
            "summary": "usage",
            "kind": "metric",
            "payload": {"name": "usage", "value": used, "unit": "tokens"},
        }
    return {**base, "kind": "status", "payload": {"status": "running", "detail": base["summary"]}}


def map_acpx_event(event: dict[str, Any]) -> dict[str, Any]:
    """Map an ACPX event onto the Manager's allowlisted typed-output shapes."""
    if event.get("jsonrpc") == "2.0":
        mapped = map_acpx_jsonrpc_update(event)
        if mapped is not None:
            return mapped
        raise ValueError("unsupported ACPX JSON-RPC frame")
    if event.get("eventVersion") != EVENT_VERSION:
        raise ValueError("unsupported ACPX eventVersion")
    seq = event.get("seq")
    if not isinstance(seq, int) or seq < 0:
        raise ValueError("ACPX event sequence must be a non-negative integer")
    event_type = str(event.get("type") or "")
    text = _event_text(event, event_type.replace("_", " ").strip() or "ACP event")
    base = {"idempotency_key": f"acpx-{seq}", "summary": text}

    if event_type in {"assistant_message", "final", "result"}:
        return {**base, "kind": "summary", "payload": {"text": text}}
    if event_type in {"thinking", "tool_result", "observation", "progress"}:
        return {**base, "kind": "observation", "payload": {"text": text}}
    if event_type in {"tool_call", "tool_call_start", "tool_call_complete"}:
        name = str(_redact(event.get("toolName") or event.get("name") or "ACP tool"))[:200]
        return {**base, "kind": "action", "payload": {"name": name}}
    if event_type in {"permission_request", "approval"}:
        return {
            **base,
            "kind": "approval",
            "payload": {"prompt": text, "required": True},
        }
    if event_type in {"error", "failed"}:
        return {
            **base,
            "kind": "error",
            "payload": {"code": "acp_error", "message": text, "retryable": False},
        }
    if event_type in {"usage", "metric"}:
        if isinstance(event.get("latencyMs"), (int, float)):
            name, value, unit = "latency", event["latencyMs"], "ms"
        else:
            name = str(event.get("name") or "usage")[:80]
            value = event.get("value", 0)
            unit = str(event.get("unit") or "count")[:32]
        return {
            **base,
            "kind": "metric",
            "payload": {"name": name, "value": value, "unit": unit},
        }
    if event_type in {"diff", "artifact"}:
        return {
            **base,
            "kind": "extracted_data",
            "payload": {"label": "ACP artifact", "data": text},
        }
    return {
        **base,
        "kind": "status",
        "payload": {"status": "running", "detail": text},
    }


__all__ = [
    "ACPX_VERSION",
    "ACP_SDK_CONTRACT_BASELINE",
    "ACPX_META_COMMANDS",
    "LEGACY_SUPPORTED_AGENTS",
    "discover_acpx_agent_ids",
    "parse_acpx_advertised_agent_ids",
    "parse_acpx_configured_agent_ids",
    "validate_acpx_agent_id",
    "build_close_command",
    "build_ensure_command",
    "build_preflight_close_command",
    "build_preflight_ensure_command",
    "build_prompt_command",
    "classify_acpx_control_failure",
    "derive_session_name",
    "map_acpx_event",
    "map_acpx_jsonrpc_update",
    "parse_acpx_event",
    "parse_acpx_frame",
    "validate_acpx_version",
    "validate_mcp_config",
    "validate_permission_policy",
    "validate_preflight_mcp_config",
]
