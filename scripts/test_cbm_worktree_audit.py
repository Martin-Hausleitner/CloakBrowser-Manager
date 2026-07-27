"""Synthetic git-fixture tests for scripts/cbm_worktree_audit.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("cbm_worktree_audit.py")
SPEC = importlib.util.spec_from_file_location("cbm_worktree_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    return completed.stdout.strip()


def commit_file(repo: Path, path: str, body: str, message: str, date: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    git(repo, "add", path)
    env = {
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_AUTHOR_NAME": "CBM Test",
        "GIT_AUTHOR_EMAIL": "cbm@example.test",
        "GIT_COMMITTER_NAME": "CBM Test",
        "GIT_COMMITTER_EMAIL": "cbm@example.test",
    }
    git(repo, "commit", "-m", message, env=env)


def make_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    repo = tmp_path / "repo"
    git(tmp_path, "clone", str(origin), str(repo))
    git(repo, "config", "user.name", "CBM Test")
    git(repo, "config", "user.email", "cbm@example.test")
    commit_file(repo, "backend/main.py", "base\n", "base", "2026-07-01T12:00:00+00:00")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    return repo


def add_worktree(repo: Path, sibling: Path, branch: str, *, from_ref: str = "main") -> Path:
    git(repo, "worktree", "add", "-b", branch, str(sibling), from_ref)
    git(sibling, "config", "user.name", "CBM Test")
    git(sibling, "config", "user.email", "cbm@example.test")
    return sibling


def run_audit(repo: Path, **overrides):
    values = {
        "root": repo,
        "target_ref": "origin/main",
        "retention_days": 7,
        "oversized_gib": 32,
        "now": NOW,
        **overrides,
    }
    config = audit.AuditConfig(**values)
    return audit.audit_repository(config)


def by_branch(payload: dict) -> dict[str, dict]:
    return {item["branch"]: item for item in payload["worktrees"]}


def test_clean_merged_stale_worktree_is_cleanup_candidate(tmp_path: Path):
    repo = make_repo(tmp_path)
    stale = add_worktree(repo, tmp_path / "stale", "stale-clean")
    payload = run_audit(repo)

    item = by_branch(payload)["stale-clean"]
    assert item["dirty"] is False
    assert item["merge_state"]["merged"] is True
    assert item["activity"]["stale"] is True
    assert item["cleanup_candidate"] is True
    assert item["candidate_blockers"] == []


def test_dirty_worktree_reports_modules_and_is_not_candidate(tmp_path: Path):
    repo = make_repo(tmp_path)
    dirty = add_worktree(repo, tmp_path / "dirty", "dirty-work")
    (dirty / "backend/main.py").write_text("dirty\n", encoding="utf-8")
    (dirty / "docs").mkdir()
    (dirty / "docs/new.md").write_text("draft\n", encoding="utf-8")

    item = by_branch(run_audit(repo))["dirty-work"]
    assert item["dirty"] is True
    assert item["modules"] == ["backend", "docs"]
    assert item["cleanup_candidate"] is False
    assert "dirty" in item["candidate_blockers"]


def test_unpushed_worktree_is_reported_and_blocked(tmp_path: Path):
    repo = make_repo(tmp_path)
    unpushed = add_worktree(repo, tmp_path / "unpushed", "unpushed-work")
    commit_file(unpushed, "scripts/local.py", "print('local')\n", "local", "2026-07-26T12:00:00+00:00")

    item = by_branch(run_audit(repo))["unpushed-work"]
    assert item["merge_state"]["ahead"] == 1
    assert item["merge_state"]["unpushed"] is True
    assert "unpushed" in item["candidate_blockers"]
    assert item["cleanup_candidate"] is False


def test_unmerged_worktree_is_not_candidate(tmp_path: Path):
    repo = make_repo(tmp_path)
    unmerged = add_worktree(repo, tmp_path / "unmerged", "unmerged-work")
    commit_file(unmerged, "frontend/app.tsx", "export {}\n", "feature", "2026-07-01T12:00:00+00:00")
    git(unmerged, "push", "-u", "origin", "unmerged-work")

    item = by_branch(run_audit(repo))["unmerged-work"]
    assert item["merge_state"]["merged"] is False
    assert "unmerged" in item["candidate_blockers"]
    assert item["cleanup_candidate"] is False


def test_recent_clean_worktree_respects_retention(tmp_path: Path):
    repo = make_repo(tmp_path)
    recent = add_worktree(repo, tmp_path / "recent", "recent-clean")
    commit_file(repo, "docs/recent.md", "recent\n", "recent", "2026-07-26T12:00:00+00:00")
    git(repo, "push")
    git(recent, "fetch", "origin")
    git(recent, "reset", "--hard", "origin/main")

    item = by_branch(run_audit(repo))["recent-clean"]
    assert item["dirty"] is False
    assert item["merge_state"]["merged"] is True
    assert item["activity"]["stale"] is False
    assert "retention_not_met" in item["candidate_blockers"]
    assert item["cleanup_candidate"] is False


def test_missing_registered_worktree_is_reported_without_crashing(tmp_path: Path):
    repo = make_repo(tmp_path)
    missing = add_worktree(repo, tmp_path / "missing", "missing-path")
    git(repo, "worktree", "lock", str(missing))
    for path in sorted(missing.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    missing.rmdir()

    item = by_branch(run_audit(repo))["missing-path"]
    assert item["activity"]["stale"] is True
    assert item["cleanup_candidate"] is False
    assert item["candidate_blockers"] == ["missing_path"]


def test_dirty_module_overlap_is_reported_by_hash(tmp_path: Path):
    repo = make_repo(tmp_path)
    one = add_worktree(repo, tmp_path / "overlap-one", "overlap-one")
    two = add_worktree(repo, tmp_path / "overlap-two", "overlap-two")
    (one / "backend/main.py").write_text("one\n", encoding="utf-8")
    (two / "backend/other.py").write_text("two\n", encoding="utf-8")

    branches = by_branch(run_audit(repo))
    assert branches["overlap-one"]["overlap"]["modules"] == ["backend"]
    assert branches["overlap-two"]["path_hash"] in branches["overlap-one"]["overlap"]["with"]
    assert "overlap" in branches["overlap-one"]["candidate_blockers"]


def test_oversized_worktree_blocks_candidate_with_configurable_threshold(tmp_path: Path):
    repo = make_repo(tmp_path)
    oversized = add_worktree(repo, tmp_path / "oversized", "oversized-clean")
    (oversized / "large.bin").write_bytes(b"x" * 128)

    item = by_branch(run_audit(repo, oversized_gib=0.00000001))["oversized-clean"]
    assert item["size"]["oversized"] is True
    assert "oversized" in item["candidate_blockers"]
    assert item["cleanup_candidate"] is False


def test_disk_thresholds_warn_and_block_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path)

    class Usage:
        total = 100 * audit.BYTES_PER_GIB
        used = 91 * audit.BYTES_PER_GIB
        free = 9 * audit.BYTES_PER_GIB

    monkeypatch.setattr(audit.shutil, "disk_usage", lambda path: Usage)

    disk = run_audit(repo)["disk"]
    assert disk["status"] == "block_new_worktree"
    assert disk["actions"] == ["warn", "block_new_worktree"]
    assert disk["thresholds_gib"]["warn"] == 16.0
    assert disk["thresholds_gib"]["block_new_worktree"] == 12.0
    assert disk["thresholds_gib"]["block_release"] == 8.0


def test_disk_threshold_blocks_release_below_eight_gib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = make_repo(tmp_path)

    class Usage:
        total = 100 * audit.BYTES_PER_GIB
        used = 93 * audit.BYTES_PER_GIB
        free = 7 * audit.BYTES_PER_GIB

    monkeypatch.setattr(audit.shutil, "disk_usage", lambda path: Usage)

    assert run_audit(repo)["disk"]["status"] == "block_release"


def test_cli_outputs_json_only_and_redacts_secret_material(tmp_path: Path):
    repo = make_repo(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(repo),
            "--target-ref",
            "origin/main",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    combined = json.dumps(payload)
    assert payload["schema"] == "cbm.worktree_audit.v1"
    assert "token=" not in combined
    assert "cbm_agent_" not in combined


def test_redaction_preserves_cmb_filenames_and_redacts_temp_paths(tmp_path: Path):
    filename = "scripts/cbm_agent_ctl.py scripts/test_cbm_agent_ctl.py"
    assert audit.redact_text(filename) == filename
    assert audit.redact_text("cbm_agent_" + ("a" * 24)) == "[REDACTED_CBM_TOKEN]"
    redacted_temp = audit.redact_text(str(tmp_path / "repo"))
    assert "[REDACTED_PATH]" in redacted_temp
    assert redacted_temp.endswith("/repo")
