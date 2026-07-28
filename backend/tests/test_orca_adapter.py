"""Unit tests for the bounded Orca adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend import orca_adapter as oa

SYNTH_KEY = "cbm_agent_synth_test_key_01"


def _ok(result: dict[str, Any]) -> str:
    return json.dumps({"id": "test", "ok": True, "result": result})


def _write_key(path: Path, content: str = SYNTH_KEY) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.responses: list[subprocess.CompletedProcess[str]] = []
        self.timeouts: set[int] = set()

    def queue(self, stdout: str, *, returncode: int = 0) -> None:
        self.responses.append(
            subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
        )

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        idx = len(self.calls)
        self.calls.append((list(argv), timeout))
        if idx in self.timeouts:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        if not self.responses:
            raise AssertionError(f"unexpected invoke: {argv}")
        return self.responses.pop(0)


def test_build_argv_rejects_unknown_operation():
    with pytest.raises(oa.OrcaOperationNotAllowed):
        oa.build_argv("terminal.pause", orca_bin="/bin/orca")


def test_build_argv_never_uses_shell_string():
    argv = oa.build_argv(
        "terminal.create",
        orca_bin="/home/coder/.local/bin/orca-ide",
        worktree="path:/repo",
        command="codex",
        title="cbm-test",
    )
    assert argv[0] == "/home/coder/.local/bin/orca-ide"
    assert "shell" not in " ".join(argv).lower()
    assert argv == [
        "/home/coder/.local/bin/orca-ide",
        "terminal",
        "create",
        "--json",
        "--worktree",
        "path:/repo",
        "--title",
        "cbm-test",
        "--command",
        "codex",
    ]


def test_agy_commits_multiline_profile_context_after_the_tui_paste():
    runner = FakeRunner()
    runner.queue(_ok({"terminal": {"handle": "term_owned-agy-ready"}}))
    runner.queue(_ok({"ok": True}))
    runner.queue(_ok({"ok": True}))
    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector="path:/repo",
    )

    session = adapter.start_session(
        profile_id="profile-agy",
        sandbox_id="alpha",
        agent="agy",
        owner_key="agent:ops",
    )

    assert session.status == "running"
    assert runner.calls[1][0][1:4] == ["terminal", "send", "--json"]
    assert runner.calls[2][0][1:4] == ["terminal", "send", "--json"]
    submit_argv = runner.calls[2][0]
    assert submit_argv[submit_argv.index("--text") + 1] == " "
    assert submit_argv[-1] == "--enter"


def test_agy_restarts_fixed_wrapper_when_orca_opens_only_a_shell():
    runner = FakeRunner()
    runner.queue(_ok({"terminal": {"handle": "term_owned-agy-fallback"}}))
    runner.queue(_ok({"output": "coder@vcvm:~/repo$"}))
    runner.queue(_ok({"ok": True}))
    runner.queue(_ok({"output": "Antigravity CLI 1.1.8\n>"}))
    runner.queue(_ok({"ok": True}))
    runner.queue(_ok({"ok": True}))
    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector="path:/repo",
        probe_runtime=True,
        sleeper=lambda _seconds: None,
    )

    session = adapter.start_session(
        profile_id="profile-agy",
        sandbox_id="alpha",
        agent="agy",
        owner_key="agent:ops",
    )

    assert session.status == "running"
    fallback_argv = runner.calls[2][0]
    assert fallback_argv[1:4] == ["terminal", "send", "--json"]
    assert fallback_argv[fallback_argv.index("--text") + 1].endswith(
        "scripts/orca_agent_cli.sh agy"
    )
    assert runner.calls[4][0][1:4] == ["terminal", "send", "--json"]
    assert runner.calls[5][0][1:4] == ["terminal", "send", "--json"]


@pytest.mark.parametrize("agent", ["bash", "sh", "python", "cursor", "opencode"])
def test_validate_agent_cli_rejects_non_allowlisted(agent: str):
    with pytest.raises(oa.OrcaAdapterError) as exc:
        oa.validate_agent_cli(agent)
    assert exc.value.code == "agent_not_allowed"


@pytest.mark.parametrize("agent", ["cursor-agent", "grok", "agy", "codex"])
def test_validate_agent_cli_allows_supported(agent: str):
    assert oa.validate_agent_cli(agent) == agent


def test_redact_secrets_strips_tokens_and_proxy_creds():
    raw = (
        "Authorization: Bearer cbm_agent_SECRETAGENTKEY123\n"
        "proxy=http://user:supersecret@proxy.example:8080\n"
        "CURSOR_API_KEY=sk-live-abc\n"
        "use cbm_run_deadbeefcafebabe01\n"
    )
    cleaned = oa.redact_secrets(raw)
    assert "SECRETAGENTKEY123" not in cleaned
    assert "supersecret" not in cleaned
    assert "sk-live-abc" not in cleaned
    assert "deadbeefcafebabe01" not in cleaned
    assert "[redacted]" in cleaned


def test_assert_owned_handle_rejects_foreign_terminals():
    runner = FakeRunner()
    adapter = oa.OrcaAdapter(orca_bin="/bin/fake-orca", runner=runner)
    with pytest.raises(oa.OrcaHandleInvalid):
        adapter.assert_owned_handle("term_foreign-handle-0001")


def test_read_output_extracts_nested_terminal_tail_and_string_cursor():
    runner = FakeRunner()
    runner.queue(
        _ok(
            {
                "terminal": {
                    "handle": "term_owned-nested-tail",
                    "title": "cbm-codex-profile",
                }
            }
        )
    )
    runner.queue(_ok({"ok": True}))  # initial context send
    runner.queue(
        _ok(
            {
                "terminal": {
                    "tail": [
                        "first line",
                        "Authorization: Bearer cbm_agent_NESTEDLEAK999",
                        "third line",
                    ],
                    "oldestCursor": "17",
                    "nextCursor": "42",
                    "latestCursor": "41",
                    "hasMoreBefore": False,
                    "hasMoreAfter": False,
                }
            }
        )
    )

    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector="path:/repo",
        base_url_hint="http://127.0.0.1:18115",
    )
    session = adapter.start_session(
        profile_id="profile-1",
        sandbox_id="alpha",
        agent="codex",
        owner_key="agent:ops",
    )

    output = adapter.read_output(session.id, owner_key="agent:ops")

    assert output["output"] == "first line\nAuthorization: Bearer [redacted]\nthird line"
    assert "NESTEDLEAK999" not in output["output"]
    assert output["next_cursor"] == 42


def test_start_read_send_close_happy_path_and_ownership():
    runner = FakeRunner()
    runner.queue(
        _ok(
            {
                "terminal": {
                    "handle": "term_owned-aaaa-bbbb-cccc",
                    "title": "cbm-codex-profile",
                }
            }
        )
    )
    runner.queue(_ok({"ok": True}))  # initial context send
    runner.queue(
        _ok(
            {
                "output": "hello Authorization: Bearer cbm_agent_LEAKEDTOKEN999",
                "nextCursor": 12,
            }
        )
    )
    runner.queue(_ok({"ok": True}))  # user send
    runner.queue(_ok({"ok": True}))  # close

    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector="path:/repo",
        base_url_hint="http://127.0.0.1:18115",
    )
    session = adapter.start_session(
        profile_id="profile-1",
        sandbox_id="alpha",
        agent="codex",
        owner_key="agent:ops",
        prompt="Open the selected profile live view",
    )
    assert session.terminal_handle == "term_owned-aaaa-bbbb-cccc"
    assert session.capabilities["pause"] is False
    assert session.capabilities["resume"] is False

    create_argv = runner.calls[0][0]
    assert create_argv[1:4] == ["terminal", "create", "--json"]
    assert "--command" in create_argv
    command = create_argv[create_argv.index("--command") + 1]
    assert command.endswith("scripts/orca_agent_cli.sh codex")
    assert command != "codex"
    assert "cbm_agent_" not in command

    send_argv = runner.calls[1][0]
    assert "--text" in send_argv
    preamble = send_argv[send_argv.index("--text") + 1]
    assert "profile_id=profile-1" in preamble
    assert "cloakbrowser-orca-control" in preamble
    assert "scripts/cbm_browser_ctl.py" in preamble
    assert "prefer scripts/cbm_browser_ctl.py" in preamble
    assert "inspect/navigate/click/fill/text/screenshot" in preamble
    assert "scripts/cbm_agent_ctl.py" in preamble
    assert "/api/task-sessions" in preamble
    assert "worker claim" in preamble
    assert "never print key contents" in preamble
    assert "Browser-Use Cloud" in preamble
    assert "Do not guess shell browser launches" in preamble
    assert "do not invent CDP ports" in preamble

    output = adapter.read_output(session.id, owner_key="agent:ops")
    assert "LEAKEDTOKEN999" not in output["output"]
    assert "[redacted]" in output["output"]
    assert output["next_cursor"] == 12

    sent = adapter.send_input(session.id, owner_key="agent:ops", text="continue")
    assert sent["ok"] is True

    with pytest.raises(oa.OrcaSessionNotFound):
        adapter.read_output(session.id, owner_key="agent:other")

    closed = adapter.close_session(session.id, owner_key="agent:ops")
    assert closed.status == "closed"
    with pytest.raises(oa.OrcaHandleInvalid):
        adapter.assert_owned_handle("term_owned-aaaa-bbbb-cccc")


def test_build_initial_context_prefers_browser_ctl_for_immediate_actions():
    text = oa.build_initial_context(
        profile_id="prof-abc",
        agent="codex",
        base_url_hint="http://127.0.0.1:18115",
    )
    assert "Selected profile_id=prof-abc." in text
    assert "Agent CLI=codex." in text
    assert ".agents/skills/cloakbrowser-orca-control/SKILL.md" in text
    assert "For immediate inspect/navigate/click/fill/text/screenshot" in text
    assert "prefer scripts/cbm_browser_ctl.py" in text
    assert "For longer Browser-Use runs, use scripts/cbm_agent_ctl.py task/run" in text
    assert "require a VCVM Browser-Use worker claim" in text
    assert "queuing a run alone is not execution proof" in text
    assert "Do not guess shell browser launches" in text
    assert "do not call Browser-Use Cloud" in text
    assert "do not invent CDP ports" in text
    assert "never print key contents" in text
    assert "Base URL: http://127.0.0.1:18115" in text
    # Immediate path must not be framed as agent_ctl-only.
    assert text.index("scripts/cbm_browser_ctl.py") < text.index("scripts/cbm_agent_ctl.py")


def test_invoke_timeout_is_bounded():
    runner = FakeRunner()
    runner.timeouts.add(0)
    adapter = oa.OrcaAdapter(orca_bin="/bin/fake-orca", runner=runner)
    with pytest.raises(oa.OrcaTimeoutError) as exc:
        adapter.invoke("status", timeout=0.01)
    assert exc.value.status_code == 504


def test_capabilities_report_no_pause_resume_when_ready():
    caps = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=FakeRunner(),
        probe_runtime=False,
    ).capabilities()
    assert caps["available"] is True
    assert caps["actions"]["start"] is True
    assert caps["actions"]["close"] is True
    assert caps["actions"]["pause"] is False
    assert caps["actions"]["resume"] is False
    assert "cursor-agent" in caps["agents"]
    assert "agy" in caps["agents"]
    assert any("owner-only" in note for note in caps["notes"])
    assert any("vcvm_orca_preflight" in note for note in caps["notes"])
    assert any("not mounted into the Manager container" in note for note in caps["notes"])


def test_capabilities_available_without_container_local_wrapper_or_key(tmp_path: Path):
    """Host wrapper/key are not mounted; missing local paths must not disable Launch."""
    missing_wrapper = tmp_path / "missing" / "orca_agent_cli.sh"
    missing_key = tmp_path / "missing" / "orca-agent-key"
    caps = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=FakeRunner(),
        agent_wrapper=str(missing_wrapper),
        agent_key_file=str(missing_key),
        probe_runtime=False,
    ).capabilities()
    assert caps["available"] is True
    assert not any("agent key file" in note for note in caps["notes"])
    assert not any("agent wrapper is missing" in note for note in caps["notes"])
    # start_session still uses the configured host wrapper path string.
    command = oa.build_agent_launch_command("codex", wrapper=str(tmp_path / "scripts" / "orca_agent_cli.sh"))
    assert command.endswith("scripts/orca_agent_cli.sh codex")


def test_capabilities_probe_runtime_status_and_worktree():
    runner = FakeRunner()
    runner.queue(_ok({"runtime": {"reachable": True, "state": "ready"}}))
    runner.queue(
        _ok(
            {
                "worktree": {
                    "path": "/home/coder/vk-repos/CloakBrowser-Manager-browser-use",
                }
            }
        )
    )
    adapter = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector=oa.DEFAULT_WORKTREE_SELECTOR,
        probe_runtime=True,
    )
    caps = adapter.capabilities()
    assert caps["available"] is True
    assert runner.calls[0][0][1:3] == ["status", "--json"]
    assert runner.calls[1][0][1:4] == ["worktree", "show", "--json"]


def test_capabilities_probe_marks_unavailable_on_bad_runtime():
    runner = FakeRunner()
    runner.queue(_ok({"runtime": {"reachable": False, "state": "starting"}}))
    runner.queue(
        _ok(
            {
                "worktree": {
                    "path": "/home/coder/vk-repos/CloakBrowser-Manager-browser-use",
                }
            }
        )
    )
    caps = oa.OrcaAdapter(
        orca_bin="/bin/fake-orca",
        runner=runner,
        worktree_selector=oa.DEFAULT_WORKTREE_SELECTOR,
        probe_runtime=True,
    ).capabilities()
    assert caps["available"] is False
    assert any("not reachable" in note for note in caps["notes"])
    assert any("not ready" in note for note in caps["notes"])


def test_build_agent_launch_command_uses_fixed_wrapper_only():
    command = oa.build_agent_launch_command(
        "grok",
        wrapper="/repo/scripts/orca_agent_cli.sh",
    )
    assert command == "/repo/scripts/orca_agent_cli.sh grok"
    assert oa.build_agent_launch_command(
        "agy",
        wrapper="/repo/scripts/orca_agent_cli.sh",
    ) == "/repo/scripts/orca_agent_cli.sh agy"
    with pytest.raises(oa.OrcaAdapterError) as exc:
        oa.build_agent_launch_command("codex", wrapper="/tmp/evil.sh")
    assert exc.value.code == "wrapper_not_allowed"
    with pytest.raises(oa.OrcaAdapterError):
        oa.build_agent_launch_command("bash", wrapper="/repo/scripts/orca_agent_cli.sh")


def test_default_runner_uses_shell_false(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"ok":true,"result":{}}',
            stderr="",
        )

    monkeypatch.setattr(oa.subprocess, "run", fake_run)
    result = oa.default_runner(["/bin/fake-orca", "status", "--json"], 5.0)
    assert result.returncode == 0
    assert seen["kwargs"]["shell"] is False
    assert isinstance(seen["args"][0], list)
