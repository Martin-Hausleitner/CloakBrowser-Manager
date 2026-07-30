from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from scripts.cursor_chat_model import (
    MAX_DATA_URL_BYTES,
    CursorAgentChatModel,
    CursorAgentError,
    CursorAgentTimeout,
    cleanup_temp_paths,
    make_chat_invoke_completion,
    redact_text,
    serialize_messages,
)


class Answer(BaseModel):
    answer: str


class ImageUrl(BaseModel):
    url: str


class ContentPart(BaseModel):
    type: str = "image_url"
    image_url: ImageUrl


class UserMessage(BaseModel):
    role: str = "user"
    content: list[ContentPart]


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["cursor-agent"], returncode, stdout, "")


def test_redacts_tokens_cookies_base64_and_paths_from_text():
    text = (
        "Authorization: Bearer cbm_run_deadbeef token=secret "
        "Cookie: session=abc data:image/png;base64," + ("A" * 80) + " /tmp/run/shot.png"
    )

    redacted = redact_text(text)

    assert "cbm_run_deadbeef" not in redacted
    assert "session=abc" not in redacted
    assert "data:image" not in redacted
    assert "/tmp/run/shot.png" not in redacted
    assert "[REDACTED]" in redacted


def test_serializes_messages_without_raw_data_url_payload(tmp_path: Path):
    payload = "data:image/png;base64," + base64.b64encode(b"PNGFAKE" + b"A" * 32).decode("ascii")
    out, temps = serialize_messages(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": payload}}]}],
        tmp_path,
    )

    rendered = json.dumps(out)
    assert payload not in rendered
    assert out[0]["content"][0]["image_path"].startswith(str(tmp_path))
    image_path = Path(out[0]["content"][0]["image_path"])
    assert image_path.exists()
    assert image_path.stat().st_mode & 0o777 == 0o600
    assert image_path in temps
    cleanup_temp_paths(temps)
    assert not image_path.exists()


def test_serializes_pydantic_browser_use_like_messages(tmp_path: Path):
    raw = base64.b64encode(b"hello-image").decode("ascii")
    msg = UserMessage(content=[ContentPart(image_url=ImageUrl(url=f"data:image/png;base64,{raw}"))])

    out, temps = serialize_messages([msg], tmp_path)

    assert "data:image" not in json.dumps(out)
    assert Path(out[0]["content"][0]["image_path"]).exists()
    cleanup_temp_paths(temps)


def test_rejects_oversized_and_invalid_base64_data_urls(tmp_path: Path):
    huge = "data:image/png;base64," + ("A" * (MAX_DATA_URL_BYTES + 8))
    with pytest.raises(CursorAgentError):
        serialize_messages(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": huge}}]}],
            tmp_path,
        )

    with pytest.raises(CursorAgentError):
        serialize_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,@@@not-base64@@@"},
                        }
                    ],
                }
            ],
            tmp_path,
        )


def test_cursor_wrapper_invokes_argv_only_json_and_validates_schema(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append(list(argv))
        return completed(json.dumps({"result": json.dumps({"answer": "ok"})}))

    model = CursorAgentChatModel(model_alias="safe-model", timeout_seconds=3, runner=runner)

    result = asyncio.run(model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path))

    assert result == Answer(answer="ok")
    argv = calls[0]
    assert argv[:6] == ["cursor-agent", "--print", "--mode", "ask", "--output-format", "json"]
    assert "--workspace" in argv
    assert argv[argv.index("--workspace") + 1] == str(tmp_path)
    assert "--trust" in argv
    assert "--sandbox" not in argv
    assert "--model" in argv and "safe-model" in argv
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_default_model_omits_model_flag(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append(list(argv))
        return completed(json.dumps({"result": json.dumps({"answer": "ok"})}))

    for alias in (None, "", "default"):
        calls.clear()
        model = CursorAgentChatModel(model_alias=alias, timeout_seconds=3, runner=runner)
        asyncio.run(model.invoke_structured([{"role": "user", "content": "hi"}], Answer, tmp_path))
        argv = calls[0]
        assert argv[:6] == ["cursor-agent", "--print", "--mode", "ask", "--output-format", "json"]
        assert "--workspace" in argv
        assert "--trust" in argv
        assert "--sandbox" not in argv
        assert "--model" not in argv
        assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_workspace_is_0700_and_images_stay_inside(tmp_path: Path):
    calls: list[list[str]] = []
    raw = base64.b64encode(b"PNGIMG").decode("ascii")
    payload = f"data:image/png;base64,{raw}"

    def runner(argv, timeout, cancel_event=None, input_text=None):
        calls.append(list(argv))
        return completed(json.dumps({"result": json.dumps({"answer": "ok"})}))

    model = CursorAgentChatModel(model_alias="safe-model", timeout_seconds=3, runner=runner)
    asyncio.run(
        model.invoke_structured(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": payload}}]}],
            Answer,
            tmp_path,
        )
    )
    argv = calls[0]
    workspace = Path(argv[argv.index("--workspace") + 1])
    assert workspace == tmp_path
    assert workspace.stat().st_mode & 0o777 == 0o700
    assert "--trust" in argv
    assert "--sandbox" not in argv
    # Image temps are cleaned after invoke, but during serialize they lived under workspace.
    assert not any(workspace.glob("cbm-shot-*")) or all(
        p.is_relative_to(workspace) for p in workspace.glob("cbm-shot-*")
    )

def test_cursor_wrapper_retries_invalid_structured_output_twice(tmp_path: Path):
    attempts = iter(
        [
            completed(json.dumps({"result": "not json"})),
            completed(json.dumps({"result": json.dumps({"missing": "field"})})),
            completed(json.dumps({"result": json.dumps({"answer": "fixed"})})),
        ]
    )

    model = CursorAgentChatModel(timeout_seconds=3, runner=lambda *_args, **_kw: next(attempts))

    assert asyncio.run(model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)).answer == "fixed"


def test_retry_prompt_includes_validation_error(tmp_path: Path):
    prompts: list[str] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        prompts.append(input_text or "")
        if len(prompts) == 1:
            return completed(json.dumps({"result": json.dumps({"missing": "field"})}))
        return completed(json.dumps({"result": json.dumps({"answer": "ok"})}))

    model = CursorAgentChatModel(model_alias="safe-model", timeout_seconds=3, runner=runner)
    assert asyncio.run(model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path)).answer == "ok"
    assert len(prompts) >= 2
    assert "answer" in prompts[1].lower() or "validation" in prompts[1].lower() or "missing" in prompts[1].lower()


def test_cursor_wrapper_timeout_cancels_process_group_and_redacts_error(tmp_path: Path):
    killed: list[int] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        raise CursorAgentTimeout("timed out with Bearer cbm_worker_secret", pid=12345)

    model = CursorAgentChatModel(timeout_seconds=0.01, runner=runner, killpg=lambda pid, sig: killed.append(pid))

    with pytest.raises(CursorAgentTimeout) as exc:
        asyncio.run(model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path))

    assert "cbm_worker_secret" not in str(exc.value)
    # invoke_structured must not re-kill a raw PID; run_cursor already killed the group.
    assert killed == []


def test_invoke_structured_does_not_second_kill_raw_pid_after_timeout(tmp_path: Path):
    kills: list[tuple[int, int]] = []

    def runner(argv, timeout, cancel_event=None, input_text=None):
        # Simulate run_cursor already killing via getpgid, then raising with raw pid.
        kills.append((99901, signal.SIGKILL))
        raise CursorAgentTimeout("cursor-agent timed out", pid=4242)

    model = CursorAgentChatModel(
        timeout_seconds=0.01,
        runner=runner,
        killpg=lambda pid, sig: kills.append((pid, sig)),
    )
    with pytest.raises(CursorAgentTimeout):
        asyncio.run(model.invoke_structured([{"role": "user", "content": "hello"}], Answer, tmp_path))

    assert kills == [(99901, signal.SIGKILL)]
    assert 4242 not in [pid for pid, _sig in kills]


def test_ainvoke_cancellation_signals_current_invoke_only_then_second_succeeds(tmp_path: Path):
    model = CursorAgentChatModel(timeout_seconds=30)
    seen_events: list[asyncio.Event] = []
    cancel_observed = []

    def hanging_then_ok_runner(argv, timeout, cancel_event=None, input_text=None):
        if cancel_event is not None:
            seen_events.append(cancel_event)
        # First call blocks until its own cancel_event is set.
        if len(seen_events) == 1:
            deadline = time.time() + 5
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    cancel_observed.append("first-saw-cancel")
                    raise CursorAgentError("cursor-agent cancelled")
                time.sleep(0.05)
            raise CursorAgentError("first invoke never cancelled")
        return completed(json.dumps({"result": json.dumps({"answer": "second"})}))

    model._runner = hanging_then_ok_runner

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
        # Same model instance must accept a fresh invoke after cancellation.
        second = await model.ainvoke(
            [{"role": "user", "content": "again"}],
            output_format=Answer,
            tmp_dir=tmp_path,
        )
        assert second.completion == Answer(answer="second")

    asyncio.run(drive())
    assert cancel_observed == ["first-saw-cancel"]
    assert len(seen_events) >= 2
    # First invoke's event stayed set for that subprocess; second used a distinct event.
    assert seen_events[0] is not seen_events[1]
    assert seen_events[0].is_set()
    assert not seen_events[1].is_set()


def test_ainvoke_cancel_joins_runner_before_workspace_cleanup(tmp_path: Path):
    """CancelledError must not unwind/cleanup until runner observes cancel and exits."""
    import threading

    workspace = tmp_path / "ws"
    workspace.mkdir()
    os.chmod(workspace, 0o700)
    order: list[object] = []
    entered = threading.Event()

    def slow_cancel_runner(argv, timeout, cancel_event=None, input_text=None):
        order.append("runner-entered")
        entered.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                order.append(
                    (
                        "runner-saw-cancel",
                        workspace.exists(),
                        workspace.is_dir(),
                        not workspace.is_symlink(),
                    )
                )
                # Hold briefly so premature cleanup would be visible.
                time.sleep(0.2)
                order.append(("runner-finished", workspace.exists()))
                raise CursorAgentError("cursor-agent cancelled")
            time.sleep(0.02)
        raise CursorAgentError("never cancelled")

    model = CursorAgentChatModel(timeout_seconds=30, runner=slow_cancel_runner)

    async def drive() -> None:
        task = asyncio.create_task(
            model.ainvoke(
                [{"role": "user", "content": "hello"}],
                output_format=Answer,
                tmp_dir=workspace,
            )
        )
        assert await asyncio.to_thread(entered.wait, 2.0)
        await asyncio.sleep(0.05)
        order.append("cancel-requested")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        order.append("ainvoke-exited")

    asyncio.run(drive())

    saw = next(x for x in order if isinstance(x, tuple) and x[0] == "runner-saw-cancel")
    finished = next(x for x in order if isinstance(x, tuple) and x[0] == "runner-finished")
    assert saw == ("runner-saw-cancel", True, True, True)
    assert finished == ("runner-finished", True)
    assert order.index(saw) < order.index("ainvoke-exited")
    assert order.index(finished) < order.index("ainvoke-exited")
    assert order.index("cancel-requested") < order.index(saw)


def test_ensure_workspace_rejects_symlink_without_changing_target_mode(tmp_path: Path):
    target = tmp_path / "real-ws"
    target.mkdir()
    os.chmod(target, 0o755)
    before_mode = target.stat().st_mode & 0o777
    assert before_mode == 0o755

    link = tmp_path / "ws-link"
    link.symlink_to(target)

    model = CursorAgentChatModel(
        timeout_seconds=3,
        runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("runner must not start")),
    )
    with pytest.raises(CursorAgentError, match="symlink"):
        asyncio.run(
            model.invoke_structured(
                [{"role": "user", "content": "hello"}],
                Answer,
                link,
            )
        )
    assert target.stat().st_mode & 0o777 == before_mode
    assert (target.stat().st_mode & 0o777) != 0o700


def test_ensure_workspace_rejects_non_directory(tmp_path: Path):
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    model = CursorAgentChatModel(timeout_seconds=3, runner=lambda *a, **k: completed("{}"))
    with pytest.raises(CursorAgentError, match="directory"):
        asyncio.run(
            model.invoke_structured(
                [{"role": "user", "content": "hello"}],
                Answer,
                file_path,
            )
        )


def test_model_cancel_signals_all_active_invokes(tmp_path: Path):
    model = CursorAgentChatModel(timeout_seconds=30)
    releases = []

    def blocking_runner(argv, timeout, cancel_event=None, input_text=None):
        deadline = time.time() + 5
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                releases.append(id(cancel_event))
                raise CursorAgentError("cursor-agent cancelled")
            time.sleep(0.05)
        raise CursorAgentError("never cancelled")

    model._runner = blocking_runner

    async def drive() -> None:
        t1 = asyncio.create_task(
            model.ainvoke([{"role": "user", "content": "a"}], output_format=Answer, tmp_dir=tmp_path)
        )
        t2 = asyncio.create_task(
            model.ainvoke([{"role": "user", "content": "b"}], output_format=Answer, tmp_dir=tmp_path)
        )
        await asyncio.sleep(0.15)
        model.cancel()
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        assert all(isinstance(r, CursorAgentError) for r in results)

    asyncio.run(drive())
    assert len(releases) == 2
    assert releases[0] != releases[1]

def test_default_runner_uses_process_group_and_honors_cancel(tmp_path: Path):
    script = tmp_path / "slow.py"
    script.write_text("import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    cancel = asyncio.Event()
    cancel.set()

    model = CursorAgentChatModel(timeout_seconds=5)

    with pytest.raises(CursorAgentError):
        model.run_cursor([sys.executable, str(script)], timeout=5, cancel_event=cancel)


def test_run_cursor_stdin_then_capture_stdout_without_closed_file():
    """Regression: closing stdin must not break stdout/stderr capture via communicate."""
    script = (
        "import sys, json\n"
        "payload = sys.stdin.read()\n"
        "assert 'hello' in payload\n"
        "print(json.dumps({'result': json.dumps({'answer': 'from-stdin'})}))\n"
    )
    model = CursorAgentChatModel(timeout_seconds=5)
    completed = model.run_cursor(
        [sys.executable, "-c", script],
        timeout=5,
        input_text="hello structured world",
    )
    assert completed.returncode == 0
    assert "from-stdin" in completed.stdout


def test_run_cursor_timeout_still_kills_process_group_after_stdin(tmp_path: Path):
    script = tmp_path / "slow_stdin.py"
    script.write_text(
        "import sys, time\n_ = sys.stdin.read()\nwhile True:\n    time.sleep(1)\n",
        encoding="utf-8",
    )
    killed: list[int] = []
    model = CursorAgentChatModel(
        timeout_seconds=0.2,
        killpg=lambda pid, sig: killed.append(pid),
    )
    with pytest.raises(CursorAgentTimeout):
        model.run_cursor(
            [sys.executable, str(script)],
            timeout=0.2,
            input_text="payload",
        )
    assert killed


def test_chat_invoke_completion_fallback_has_completion_attr():
    wrapped = make_chat_invoke_completion(completion=Answer(answer="x"))
    assert wrapped.completion == Answer(answer="x")


def test_invoke_structured_cleans_temp_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw = base64.b64encode(b"abc123").decode("ascii")
    payload = f"data:image/png;base64,{raw}"
    created: list[Path] = []

    import scripts.cursor_chat_model as mod

    real_serialize = mod.serialize_messages

    def tracking_serialize(messages, tmp_dir):
        out, temps = real_serialize(messages, tmp_dir)
        created.extend(temps)
        return out, temps

    def runner(argv, timeout, cancel_event=None, input_text=None):
        return completed(json.dumps({"result": json.dumps({"answer": "ok"})}))

    monkeypatch.setattr(mod, "serialize_messages", tracking_serialize)
    model = CursorAgentChatModel(model_alias="safe-model", timeout_seconds=3, runner=runner)
    asyncio.run(
        model.invoke_structured(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": payload}}]}],
            Answer,
            tmp_path,
        )
    )

    assert created
    assert all(not path.exists() for path in created)
