from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.project_state import (
    PROJECT_STATE_FIELDS,
    ProjectStateError,
    atomic_write_project_state,
    build_project_state,
    validate_project_state,
)


def _valid_state() -> dict:
    return {
        "repo": "git@github.com:Martin-Hausleitner/CloakBrowser-Manager.git",
        "branch": "feature/project-state",
        "worktree": "/tmp/cloakbrowser-manager",
        "mode": "hot_reload",
        "owner": "agent-codex",
        "active_ticket": "CBM-001",
        "completed_receipts": ["tests: pending"],
        "unmerged_files": [],
        "next_safe_step": "Run targeted tests",
        "forbidden_actions": ["Do not commit or push"],
        "stop_condition": "Project state receipt written and tested",
    }


def test_validate_project_state_rejects_missing_and_unknown_fields():
    missing = _valid_state()
    missing.pop("owner")
    with pytest.raises(ProjectStateError, match="Missing required field"):
        validate_project_state(missing)

    unknown = _valid_state()
    unknown["extra"] = "not allowed"
    with pytest.raises(ProjectStateError, match="Unknown field"):
        validate_project_state(unknown)


def test_validate_project_state_rejects_invalid_mode_and_list_types():
    invalid_mode = _valid_state()
    invalid_mode["mode"] = "production"
    with pytest.raises(ProjectStateError, match="Invalid mode"):
        validate_project_state(invalid_mode)

    invalid_list = _valid_state()
    invalid_list["completed_receipts"] = ["ok", 42]
    with pytest.raises(ProjectStateError, match="completed_receipts"):
        validate_project_state(invalid_list)


def test_build_project_state_redacts_secret_like_values():
    state = build_project_state(
        repo="https://ghp_example@github.com/acme/private.git",
        branch="feature/token=abc123",
        worktree="/tmp/worktree",
        mode="staging",
        owner="cookie=sessionid",
        active_ticket="CBM-001",
        completed_receipts=["auth bearer token: secret-value"],
        unmerged_files=["backend/main.py"],
        next_safe_step="Use password=hunter2 in env only",
        forbidden_actions=["Never print raw cookies"],
        stop_condition="Done",
    )

    assert state["repo"] == "https://[REDACTED]@github.com/acme/private.git"
    assert "[REDACTED]" in state["branch"]
    assert state["owner"] == "cookie=[REDACTED]"
    assert state["completed_receipts"] == ["auth [REDACTED]"]
    assert state["next_safe_step"] == "Use password=[REDACTED] in env only"
    assert list(state) == list(PROJECT_STATE_FIELDS)


def test_build_project_state_rejects_raw_proxy_urls():
    with pytest.raises(ProjectStateError, match="raw proxy URL"):
        build_project_state(
            repo="git@github.com:Martin-Hausleitner/CloakBrowser-Manager.git",
            branch="feature/project-state",
            worktree="/tmp/cloakbrowser-manager",
            mode="vcvm_release",
            owner="agent-codex",
            active_ticket="CBM-001",
            completed_receipts=[],
            unmerged_files=[],
            next_safe_step="Do not use http://user:pass@proxy.example:8080",
            forbidden_actions=[],
            stop_condition="Done",
        )


def test_atomic_write_project_state_writes_json_without_temp_leftovers(tmp_path: Path):
    state = _valid_state()
    output_path = tmp_path / ".cbm" / "state" / "project-state-v1.json"

    atomic_write_project_state(output_path, state)

    assert json.loads(output_path.read_text(encoding="utf-8")) == state
    assert not list(output_path.parent.glob("*.tmp"))


def test_atomic_write_project_state_rejects_unredacted_secret(tmp_path: Path):
    state = _valid_state()
    state["next_safe_step"] = "Use token=raw-value"

    with pytest.raises(ProjectStateError, match="unredacted secret-like content"):
        atomic_write_project_state(
            tmp_path / ".cbm" / "state" / "project-state-v1.json",
            state,
        )
