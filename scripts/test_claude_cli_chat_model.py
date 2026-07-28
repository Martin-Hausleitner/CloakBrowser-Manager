from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from scripts.claude_cli_chat_model import ClaudeCLIChatModel
from scripts.cursor_chat_model import CursorAgentError, CursorAgentTimeout


class Answer(BaseModel):
    answer: str


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["claude"], returncode, stdout, "")


def test_claude_cli_uses_argv_only_json_schema_and_stdin_prompt(tmp_path: Path):
    calls: list[tuple[list[str], str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append((list(argv), input_text or ""))
        return completed(json.dumps({"structured_output": {"answer": "ok"}}))

    model = ClaudeCLIChatModel(model_alias="sonnet-safe", timeout_seconds=3, runner=runner)

    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )

    assert result == Answer(answer="ok")
    argv, prompt = calls[0]
    assert argv[:5] == ["claude", "-p", "--output-format", "json", "--json-schema"]
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["answer"]["type"] == "string"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in argv
    assert "--safe-mode" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet-safe"
    assert "hello" in prompt
    assert "--workspace" not in argv
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_default_model_omits_claude_model_flag(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append(list(argv))
        return completed(json.dumps({"structured_output": {"answer": "ok"}}))

    for alias in (None, "", "default"):
        calls.clear()
        model = ClaudeCLIChatModel(model_alias=alias, timeout_seconds=3, runner=runner)
        result = asyncio.run(
            model.invoke_structured([{"role": "user", "content": "hi"}], Answer, tmp_path)
        )
        assert result.answer == "ok"
        assert "--model" not in calls[0]


def test_claude_cli_parses_string_structured_output(tmp_path: Path):
    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(json.dumps({"structured_output": json.dumps({"answer": "from-string"})}))

    model = ClaudeCLIChatModel(timeout_seconds=3, runner=runner)

    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )

    assert result.answer == "from-string"


def test_claude_cli_rejects_invalid_and_error_envelopes(tmp_path: Path):
    outputs = iter(
        [
            "not json",
            json.dumps({"error": {"message": "Bearer cbm_worker_secret failed"}}),
            json.dumps({"structured_output": {"answer": "fixed"}}),
        ]
    )

    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(next(outputs))

    model = ClaudeCLIChatModel(timeout_seconds=3, runner=runner)

    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )

    assert result.answer == "fixed"


def test_claude_cli_raises_redacted_error_after_retries(tmp_path: Path):
    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(json.dumps({"error": {"message": "Bearer cbm_worker_secret failed"}}))

    model = ClaudeCLIChatModel(timeout_seconds=3, runner=runner)

    with pytest.raises(CursorAgentError) as exc:
        asyncio.run(
            model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
        )

    assert "cbm_worker_secret" not in str(exc.value)
    assert "structured output validation failed" in str(exc.value)


def test_claude_cli_timeout_uses_process_group_and_redacts(tmp_path: Path):
    kills: list[tuple[int, int]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        kills.append((98765, signal.SIGKILL))
        raise CursorAgentTimeout("claude-cli timed out with Bearer cbm_worker_secret", pid=123)

    model = ClaudeCLIChatModel(
        timeout_seconds=0.01,
        runner=runner,
        killpg=lambda pid, sig: kills.append((pid, sig)),
    )

    with pytest.raises(CursorAgentTimeout) as exc:
        asyncio.run(
            model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
        )

    assert "cbm_worker_secret" not in str(exc.value)
    assert kills == [(98765, signal.SIGKILL)]


def test_claude_cli_cancel_signals_current_invoke_only_then_second_succeeds(tmp_path: Path):
    model = ClaudeCLIChatModel(timeout_seconds=30)
    seen_events: list[asyncio.Event] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        if cancel_event is not None:
            seen_events.append(cancel_event)
        if len(seen_events) == 1:
            deadline = time.time() + 5
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise CursorAgentError("claude-cli cancelled")
                time.sleep(0.05)
            raise CursorAgentError("first invoke never cancelled")
        return completed(json.dumps({"structured_output": {"answer": "second"}}))

    model._runner = runner

    async def drive() -> None:
        task = asyncio.create_task(
            model.ainvoke(
                [{"role": "user", "content": "hello"}],
                output_format=Answer,
                tmp_dir=tmp_path,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        second = await model.ainvoke(
            [{"role": "user", "content": "again"}],
            output_format=Answer,
            tmp_dir=tmp_path,
        )
        assert second.completion == Answer(answer="second")

    asyncio.run(drive())
    assert len(seen_events) >= 2
    assert seen_events[0] is not seen_events[1]
    assert seen_events[0].is_set()
    assert not seen_events[1].is_set()


def test_claude_cli_rejects_is_error_with_structured_output_and_redacts(tmp_path: Path):
    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(
            json.dumps(
                {
                    "is_error": True,
                    "result": "Bearer cbm_worker_secret failed",
                    "structured_output": {"answer": "do-not-trust"},
                }
            )
        )

    model = ClaudeCLIChatModel(timeout_seconds=3, runner=runner)

    with pytest.raises(CursorAgentError) as exc:
        asyncio.run(
            model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
        )

    message = str(exc.value)
    assert "cbm_worker_secret" not in message
    assert "do-not-trust" not in message
    assert "structured output validation failed" in message


def test_claude_cli_rejects_is_error_error_text_and_redacts(tmp_path: Path):
    outputs = iter(
        [
            json.dumps(
                {
                    "is_error": True,
                    "error": "Bearer cbm_worker_secret failed",
                    "structured_output": {"answer": "bad"},
                }
            ),
            json.dumps({"structured_output": {"answer": "fixed"}}),
        ]
    )

    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(next(outputs))

    model = ClaudeCLIChatModel(timeout_seconds=3, runner=runner)

    result = asyncio.run(
        model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)
    )

    assert result.answer == "fixed"
