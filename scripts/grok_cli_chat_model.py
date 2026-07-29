"""Browser-Use BaseChatModel adapter backed by the VCVM Grok CLI.

Prompts are sent only through ``/dev/stdin``. Grok receives a bounded JSON
schema and runs without agent tools, web search, subagents, or cross-session
memory. The response is read from the documented ``structuredOutput`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scripts.cursor_chat_model import CursorAgentChatModel, CursorAgentError, redact_text
from scripts.json_schema_compat import normalize_cli_json_schema


class GrokCLIChatModel(CursorAgentChatModel):
    """Minimal Browser-Use-compatible chat model using Grok Build CLI."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("provider", "grok-cli")
        super().__init__(**kwargs)

    def _argv(self, workspace: Path, *, schema: type[BaseModel] | None = None) -> list[str]:
        if schema is None:
            raise CursorAgentError("grok-cli requires a JSON schema")
        provider_schema = normalize_cli_json_schema(schema.model_json_schema())
        schema_json = json.dumps(provider_schema, ensure_ascii=False, separators=(",", ":"))
        argv = [
            "grok",
            "--prompt-file",
            "/dev/stdin",
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--cwd",
            str(workspace),
            "--no-memory",
            "--no-subagents",
            "--disable-web-search",
            "--permission-mode",
            "plan",
            "--tools",
            "",
        ]
        if self._model_alias:
            argv.extend(["--model", self._model_alias])
        return argv

    def _extract_stdout_payload(self, stdout: str) -> Any:
        text = (stdout or "").strip()
        if not text:
            raise CursorAgentError("empty grok-cli output")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CursorAgentError("grok-cli output is not JSON") from exc
        if not isinstance(envelope, dict):
            raise CursorAgentError("grok-cli output envelope is not an object")
        if "error" in envelope:
            error = envelope.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error.get("error") or error
            raise CursorAgentError(f"grok-cli error: {redact_text(str(error))}")
        if "structuredOutput" not in envelope:
            stop_reason = redact_text(str(envelope.get("stopReason") or ""))
            detail = f" ({stop_reason})" if stop_reason else ""
            raise CursorAgentError(f"grok-cli output missing structuredOutput{detail}")
        structured = envelope["structuredOutput"]
        if isinstance(structured, str):
            try:
                return json.loads(structured)
            except json.JSONDecodeError as exc:
                raise CursorAgentError("grok-cli structuredOutput is not JSON") from exc
        return structured
