"""Strict ProjectStateV1 receipt generation for agent handoffs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PROJECT_STATE_VERSION = "project-state-v1"
PROJECT_STATE_FILENAME = "project-state-v1.json"
PROJECT_STATE_FIELDS = (
    "repo",
    "branch",
    "worktree",
    "mode",
    "owner",
    "active_ticket",
    "completed_receipts",
    "unmerged_files",
    "next_safe_step",
    "forbidden_actions",
    "stop_condition",
)
PROJECT_STATE_MODES = ("hot_reload", "staging", "vcvm_release")

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|cookie|auth|authorization|password|passwd|secret)\b\s*[:=]\s*[^\s,;]+"
)
_AUTH_SECRET_PHRASE_RE = re.compile(
    r"(?i)\bauth(?:orization)?\s+(?:bearer\s+)?(?:token|secret|password)\s*[:=]\s*[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PROXY_URL_RE = re.compile(
    r"(?i)\b(?:https?|socks5h?|socks4)://[^/\s:@]+:[^/\s:@]+@[^/\s]+"
)


class ProjectStateError(ValueError):
    """Raised when a ProjectStateV1 receipt is invalid or unsafe to emit."""


def _reject_raw_proxy_urls(value: str) -> None:
    if _PROXY_URL_RE.search(value):
        raise ProjectStateError("ProjectStateV1 must not contain raw proxy URLs")


def _redact_url_userinfo(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc or "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"[REDACTED]@{host}", parts.path, parts.query, parts.fragment))


def redact_project_state_value(value: str) -> str:
    """Redact secret-like fragments while preserving useful receipt context."""

    _reject_raw_proxy_urls(value)
    redacted = _redact_url_userinfo(value)
    redacted = _AUTH_SECRET_PHRASE_RE.sub("auth [REDACTED]", redacted)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    return redacted


def _redact_list(values: list[str]) -> list[str]:
    return [redact_project_state_value(value) for value in values]


def build_project_state(
    *,
    repo: str,
    branch: str,
    worktree: str,
    mode: str,
    owner: str,
    active_ticket: str,
    completed_receipts: list[str] | None = None,
    unmerged_files: list[str] | None = None,
    next_safe_step: str,
    forbidden_actions: list[str] | None = None,
    stop_condition: str,
) -> dict[str, Any]:
    state = {
        "repo": redact_project_state_value(repo),
        "branch": redact_project_state_value(branch),
        "worktree": redact_project_state_value(worktree),
        "mode": mode,
        "owner": redact_project_state_value(owner),
        "active_ticket": redact_project_state_value(active_ticket),
        "completed_receipts": _redact_list(completed_receipts or []),
        "unmerged_files": _redact_list(unmerged_files or []),
        "next_safe_step": redact_project_state_value(next_safe_step),
        "forbidden_actions": _redact_list(forbidden_actions or []),
        "stop_condition": redact_project_state_value(stop_condition),
    }
    return validate_project_state(state)


def validate_project_state(payload: dict[str, Any]) -> dict[str, Any]:
    keys = set(payload)
    expected = set(PROJECT_STATE_FIELDS)
    missing = sorted(expected - keys)
    if missing:
        raise ProjectStateError(f"Missing required field(s): {', '.join(missing)}")
    unknown = sorted(keys - expected)
    if unknown:
        raise ProjectStateError(f"Unknown field(s): {', '.join(unknown)}")

    for field in (
        "repo",
        "branch",
        "worktree",
        "mode",
        "owner",
        "active_ticket",
        "next_safe_step",
        "stop_condition",
    ):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise ProjectStateError(f"{field} must be a non-empty string")
        _reject_raw_proxy_urls(value)
        if redact_project_state_value(value) != value:
            raise ProjectStateError(f"{field} contains unredacted secret-like content")

    if payload["mode"] not in PROJECT_STATE_MODES:
        raise ProjectStateError(
            f"Invalid mode {payload['mode']!r}; expected one of {', '.join(PROJECT_STATE_MODES)}"
        )

    for field in ("completed_receipts", "unmerged_files", "forbidden_actions"):
        value = payload[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ProjectStateError(f"{field} must be a list of strings")
        for item in value:
            _reject_raw_proxy_urls(item)
            if redact_project_state_value(item) != item:
                raise ProjectStateError(
                    f"{field} contains unredacted secret-like content"
                )

    return {field: payload[field] for field in PROJECT_STATE_FIELDS}


def default_project_state_path(worktree: str | Path) -> Path:
    return Path(worktree) / ".cbm" / "state" / PROJECT_STATE_FILENAME


def atomic_write_project_state(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state = validate_project_state(payload)
    encoded = json.dumps(state, indent=2, sort_keys=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{PROJECT_STATE_FILENAME}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
