"""Unit tests for agent key file validation (never echo secrets)."""

from __future__ import annotations

from pathlib import Path

from backend.orca_agent_key import check_agent_key_file

SYNTH = "cbm_agent_synth_test_key_01"


def _write(path: Path, content: str = SYNTH, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_missing_key_file():
    errors = check_agent_key_file("/tmp/cbm-agent-key-does-not-exist-xyz")
    assert errors == ["agent key file is missing"]


def test_wrong_mode_and_empty_and_syntax(tmp_path: Path):
    wrong = _write(tmp_path / "k1", mode=0o644)
    assert any("0600" in e for e in check_agent_key_file(wrong))

    empty = tmp_path / "k2"
    empty.write_text("\n", encoding="utf-8")
    empty.chmod(0o600)
    assert check_agent_key_file(empty) == ["agent key file is empty"]

    bad = _write(tmp_path / "k3", content="cbm_agent_short")
    errors = check_agent_key_file(bad)
    assert any("syntactic" in e for e in errors)
    assert "cbm_agent_short" not in " ".join(errors)


def test_valid_key_passes(tmp_path: Path):
    good = _write(tmp_path / "k4")
    assert check_agent_key_file(good) == []
