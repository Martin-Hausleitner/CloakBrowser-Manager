"""CLI tests for scripts/cbm_trace_ctl.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "cbm_trace_ctl.py"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "observability"
    / "tokscale_aggregate_4_7_0.json"
)


def _run(args: list[str], *, env_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CLI), *args]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_config_and_benchmark(tmp_path: Path):
    r = _run(["config"])
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["content_capture"] is False

    r2 = _run(
        [
            "benchmark",
            "--spans",
            "50",
            "--usage",
            "20",
            "--workdir",
            str(tmp_path / "bench"),
        ]
    )
    assert r2.returncode == 0, r2.stderr
    bench = json.loads(r2.stdout)
    assert "sqlite" in bench["backends"]
    assert "jsonl" in bench["backends"]
    assert bench["backends"]["sqlite"]["span_count"] >= 50


def test_cli_record_import_export(tmp_path: Path):
    db = tmp_path / "traces.db"
    out = tmp_path / "export.jsonl"
    r = _run(
        [
            "--backend",
            "sqlite",
            "--path",
            str(db),
            "record-span",
            "--name",
            "acpx.run",
            "--run-id",
            "run-cli",
            "--harness",
            "acpx",
            "--finish",
        ]
    )
    assert r.returncode == 0, r.stderr
    span = json.loads(r.stdout)
    assert span["name"] == "acpx.run"
    assert span["content_capture"] is False

    r2 = _run(
        [
            "--backend",
            "sqlite",
            "--path",
            str(db),
            "import-tokscale",
            "--input",
            str(FIXTURE),
            "--run-id",
            "run-cli",
            "--require-version",
        ]
    )
    assert r2.returncode == 0, r2.stderr
    imported = json.loads(r2.stdout)
    assert imported["imported"] == 3

    r3 = _run(
        [
            "--backend",
            "sqlite",
            "--path",
            str(db),
            "export",
            "--format",
            "jsonl",
            "--out",
            str(out),
        ]
    )
    assert r3.returncode == 0, r3.stderr
    assert out.exists()
    lines = out.read_text().splitlines()
    assert len(lines) >= 4
    for line in lines:
        assert '"prompt"' not in line
        assert '"cookie"' not in line
