"""Live real-Chromium MV3 DevTools E2E for cloak-profile-sync."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.e2e.mv3_devtools import EXTENSION_ID
from scripts.e2e.mv3_devtools.chrome_launcher import resolve_chromium_binary
from scripts.e2e.mv3_devtools.runner import run_mv3_devtools_e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION_DIR = REPO_ROOT / "extensions" / "cloak-profile-sync"


def _chromium_available() -> bool:
    try:
        resolve_chromium_binary()
        return True
    except FileNotFoundError:
        return False


requires_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="Chromium binary unavailable for MV3 DevTools E2E",
)


@requires_chromium
def test_mv3_devtools_real_chromium_e2e(tmp_path: Path):
    """Strict E2E: disposable Chromium + bridge + SW CDP + CLI/MCP surface."""
    assert (EXTENSION_DIR / "manifest.json").is_file()
    assert (EXTENSION_DIR / "background" / "service-worker.js").is_file()

    artifact_dir = tmp_path / "artifacts"
    # Prefer a non-conflicting display range under pytest concurrency.
    os.environ.setdefault("CBM_MV3_E2E", "1")

    summary = run_mv3_devtools_e2e(
        artifact_dir=artifact_dir,
        command_timeout_seconds=20.0,
    )

    summary_path = artifact_dir / "summary.json"
    assert summary_path.is_file()
    on_disk = json.loads(summary_path.read_text(encoding="utf-8"))
    text = summary_path.read_text(encoding="utf-8")

    # Core DevTools claims.
    assert summary["extension_id"] == EXTENSION_ID
    assert summary["steps"]["bridge"]["ok"] is True
    assert summary["steps"]["chromium"]["ok"] is True
    assert summary["steps"]["service_worker"]["ok"] is True
    assert EXTENSION_ID in (summary["steps"]["service_worker"].get("url") or "")
    assert summary["ok"] is True

    # Safety: no home path dump, no bearer tokens, no password material.
    home = str(Path.home())
    assert home not in text
    assert "authorization: bearer" not in text.lower()
    assert "raw-password" not in text.lower()
    assert "cbm_agent_" not in text

    # CLI surface exercised; individual ops may be blocked by permissions.
    assert set(summary["steps"]["cli"]) >= {"status", "start", "stop", "export", "compile"}
    assert "mcp" in summary["steps"]

    # Blockers are explicit and redacted when present.
    assert isinstance(on_disk.get("blockers"), list)
    for blocker in on_disk["blockers"]:
        assert "code" in blocker
        assert "message" in blocker
