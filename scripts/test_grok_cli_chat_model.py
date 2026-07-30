from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from scripts.cursor_chat_model import CursorAgentError
from scripts.grok_cli_chat_model import GrokCLIChatModel


class Answer(BaseModel):
    answer: str


class BrowserUseLikeOutput(BaseModel):
    min_items: int = 0
    action: list[Answer] = Field(..., json_schema_extra={"min_items": 1})


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["grok"], returncode, stdout, "")


def test_grok_cli_uses_stdin_json_schema_and_no_agent_tools(tmp_path: Path):
    calls: list[tuple[list[str], str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append((list(argv), input_text or ""))
        return completed(json.dumps({"structuredOutput": {"answer": "ok"}}))

    model = GrokCLIChatModel(model_alias="grok-4.5-build", timeout_seconds=3, runner=runner)
    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )

    assert result == Answer(answer="ok")
    argv, prompt = calls[0]
    assert argv[:3] == ["grok", "--prompt-file", "/dev/stdin"]
    assert argv[argv.index("--output-format") + 1] == "json"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["answer"]["type"] == "string"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--no-subagents" in argv
    assert "--disable-web-search" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[argv.index("--model") + 1] == "grok-4.5-build"
    assert "hello" in prompt
    assert "hello" not in argv


def test_grok_cli_normalizes_browser_use_schema_keywords(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append(list(argv))
        return completed(
            json.dumps(
                {
                    "structuredOutput": {
                        "min_items": 0,
                        "action": [{"answer": "ok"}],
                    }
                }
            )
        )

    model = GrokCLIChatModel(timeout_seconds=3, runner=runner)
    result = asyncio.run(
        model.invoke_structured(
            [{"role": "user", "content": "hello"}],
            BrowserUseLikeOutput,
            tmp_path,
        )
    )

    assert result.action == [Answer(answer="ok")]
    schema = json.loads(calls[0][calls[0].index("--json-schema") + 1])
    assert schema["properties"]["action"]["minItems"] == 1
    assert "min_items" not in schema["properties"]["action"]
    assert "min_items" in schema["properties"]


def test_grok_cli_retries_invalid_envelopes_and_redacts_errors(tmp_path: Path):
    outputs = iter(
        [
            "not json",
            json.dumps({"error": {"message": "Bearer cbm_worker_secret failed"}}),
            json.dumps({"structuredOutput": {"answer": "fixed"}}),
        ]
    )

    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(next(outputs))

    model = GrokCLIChatModel(timeout_seconds=3, runner=runner)
    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )
    assert result == Answer(answer="fixed")


def test_grok_cli_raises_after_missing_structured_output(tmp_path: Path):
    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(json.dumps({"text": "Bearer cbm_worker_secret"}))

    model = GrokCLIChatModel(timeout_seconds=3, runner=runner)
    with pytest.raises(CursorAgentError) as exc:
        asyncio.run(
            model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
        )

    assert "cbm_worker_secret" not in str(exc.value)
    assert "structured output validation failed" in str(exc.value)
