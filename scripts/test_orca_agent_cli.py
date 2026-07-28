"""Contract tests for the interactive Orca agent wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "orca_agent_cli.sh"


def _run_with_fake_agent(tmp_path: Path, agent: str) -> list[str]:
    record = tmp_path / "argv.txt"
    fake = tmp_path / agent
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$CLI_ARGV_RECORD\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "CLI_ARGV_RECORD": str(record),
    }
    completed = subprocess.run(
        [str(WRAPPER), agent],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [
        line
        for line in record.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_agy_starts_as_interactive_cli_without_hidden_prompt(tmp_path: Path):
    assert _run_with_fake_agent(tmp_path, "agy") == []


def test_grok_starts_without_alt_screen_for_browser_terminal_mirroring(tmp_path: Path):
    assert _run_with_fake_agent(tmp_path, "grok") == ["--no-alt-screen"]


def test_wrapper_rejects_unknown_cli(tmp_path: Path):
    completed = subprocess.run(
        [str(WRAPPER), "bash"],
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 64
    assert "refusing unsupported agent CLI" in completed.stderr
