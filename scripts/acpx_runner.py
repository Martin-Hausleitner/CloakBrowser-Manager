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
MAX_EVENT_BYTES = 65_536
SUPPORTED_AGENTS = frozenset(
    {"codex", "claude", "cursor", "grok-build", "opencode"}
)

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


def _validate_agent(agent: str) -> str:
    value = str(agent or "").strip()
    if value not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported ACP agent: {value or 'empty'}")
    return value


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
    *, executable: str, cwd: Path, agent: str, session_name: str
) -> list[str]:
    """Build the deterministic command that creates or resumes one ACP session."""
    return [
        str(executable),
        "--cwd",
        str(_validate_cwd(cwd)),
        "--format",
        "json",
        "--json-strict",
        _validate_agent(agent),
        "sessions",
        "ensure",
        "--name",
        _validate_session_name(session_name),
    ]


def build_prompt_command(
    *,
    executable: str,
    cwd: Path,
    agent: str,
    session_name: str,
    permission_policy: Path,
    mcp_config: Path,
) -> list[str]:
    """Build a prompt invocation that reads the prompt from stdin and fails closed."""
    policy = validate_permission_policy(permission_policy)
    mcp = validate_mcp_config(mcp_config)
    return [
        str(executable),
        "--cwd",
        str(_validate_cwd(cwd)),
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--non-interactive-permissions",
        "fail",
        "--permission-policy",
        str(policy),
        "--mcp-config",
        str(mcp),
        _validate_agent(agent),
        "-s",
        _validate_session_name(session_name),
        "--file",
        "-",
    ]


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


def map_acpx_event(event: dict[str, Any]) -> dict[str, Any]:
    """Map an ACPX event onto the Manager's allowlisted typed-output shapes."""
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
    "SUPPORTED_AGENTS",
    "build_ensure_command",
    "build_prompt_command",
    "derive_session_name",
    "map_acpx_event",
    "parse_acpx_event",
    "validate_acpx_version",
    "validate_mcp_config",
    "validate_permission_policy",
]
