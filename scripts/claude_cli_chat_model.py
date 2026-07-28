"""Browser-Use BaseChatModel adapter backed by argv-only Claude CLI.

Runs ``claude -p --output-format json --json-schema <schema> --tools ''
--permission-mode dontAsk --no-session-persistence --safe-mode``. Prompts are
sent on stdin and the response is read from the Claude JSON envelope's
``structured_output`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scripts.cursor_chat_model import (
    CursorAgentChatModel,
    CursorAgentError,
    redact_text,
)


_CLAUDE_SCHEMA_KEY_ALIASES = {"min_items": "minItems"}
_SCHEMA_IDENTIFIER_MAPS = frozenset({"$defs", "definitions", "properties"})


def _normalize_claude_json_schema(value: Any) -> Any:
    """Translate legacy Browser-Use schema keywords without renaming fields."""
    if isinstance(value, list):
        return [_normalize_claude_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in _SCHEMA_IDENTIFIER_MAPS and isinstance(item, dict):
            normalized[key] = {
                identifier: _normalize_claude_json_schema(schema)
                for identifier, schema in item.items()
            }
            continue
        normalized[_CLAUDE_SCHEMA_KEY_ALIASES.get(key, key)] = (
            _normalize_claude_json_schema(item)
        )
    return normalized


class ClaudeCLIChatModel(CursorAgentChatModel):
    """Minimal Browser-Use-compatible chat model using Claude CLI."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("provider", "claude-cli")
        super().__init__(**kwargs)

    def _argv(self, workspace: Path, *, schema: type[BaseModel] | None = None) -> list[str]:
        if schema is None:
            raise CursorAgentError("claude-cli requires a JSON schema")
        provider_schema = _normalize_claude_json_schema(schema.model_json_schema())
        schema_json = json.dumps(provider_schema, ensure_ascii=False, separators=(",", ":"))
        argv = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--safe-mode",
        ]
        if self._model_alias:
            argv.extend(["--model", self._model_alias])
        return argv

    def _extract_stdout_payload(self, stdout: str) -> Any:
        text = (stdout or "").strip()
        if not text:
            raise CursorAgentError("empty claude-cli output")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CursorAgentError("claude-cli output is not JSON") from exc

        if not isinstance(envelope, dict):
            raise CursorAgentError("claude-cli output envelope is not an object")

        if envelope.get("is_error") is True:
            messages: list[str] = []
            for key in ("error", "result"):
                value = envelope.get(key)
                if value is None:
                    continue
                if isinstance(value, dict):
                    value = value.get("message") or value.get("error") or value
                messages.append(redact_text(str(value)))
            detail = "; ".join(item for item in messages if item) or "is_error true"
            raise CursorAgentError(f"claude-cli error: {detail}")

        if "error" in envelope:
            error = envelope.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("error") or error
            else:
                message = error
            raise CursorAgentError(f"claude-cli error: {redact_text(str(message))}")

        if "structured_output" not in envelope:
            raise CursorAgentError("claude-cli output missing structured_output")

        structured = envelope["structured_output"]
        if isinstance(structured, str):
            try:
                return json.loads(structured)
            except json.JSONDecodeError as exc:
                raise CursorAgentError("claude-cli structured_output is not JSON") from exc
        return structured
