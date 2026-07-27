#!/usr/bin/env python3
"""Read-only git worktree audit receipts for CloakBrowser Manager agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BYTES_PER_GIB = 1024**3
DEFAULT_WARN_FREE_GIB = 16.0
DEFAULT_BLOCK_WORKTREE_FREE_GIB = 12.0
DEFAULT_BLOCK_RELEASE_FREE_GIB = 8.0
DEFAULT_RETENTION_DAYS = 7
DEFAULT_OVERSIZED_GIB = 32.0

SECRET_PATTERNS = [
    re.compile(r"(?i)(https?://)[^/@\s]+@"),
    re.compile(r"(?i)(token|key|secret|password)=([^&\s]+)"),
    re.compile(r"cbm_(?:agent|worker|lease)_[A-Za-z0-9._-]{16,}"),
]


class AuditError(RuntimeError):
    """Raised when a read-only audit cannot be completed."""


@dataclass(frozen=True)
class AuditConfig:
    root: Path
    target_ref: str | None = None
    retention_days: int = DEFAULT_RETENTION_DAYS
    oversized_gib: float = DEFAULT_OVERSIZED_GIB
    warn_free_gib: float = DEFAULT_WARN_FREE_GIB
    block_worktree_free_gib: float = DEFAULT_BLOCK_WORKTREE_FREE_GIB
    block_release_free_gib: float = DEFAULT_BLOCK_RELEASE_FREE_GIB
    now: datetime | None = None


def redact_text(value: str) -> str:
    redacted = value
    redacted = SECRET_PATTERNS[0].sub(r"\1[REDACTED]@", redacted)
    redacted = SECRET_PATTERNS[1].sub(r"\1=[REDACTED]", redacted)
    redacted = SECRET_PATTERNS[2].sub("[REDACTED_CBM_TOKEN]", redacted)
    home = str(Path.home())
    if home and redacted.startswith(home):
        redacted = "~" + redacted[len(home) :]
    temp_root = str(Path(tempfile.gettempdir()).resolve())
    private_temp_root = f"/private{temp_root}" if temp_root.startswith("/var/") else ""
    for root in (temp_root, private_temp_root, "/private/tmp", "/tmp"):
        if root and redacted.startswith(root):
            basename = Path(redacted).name
            redacted = f"{root}/[REDACTED_PATH]/{basename}"
    return redacted


def path_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AuditError(f"unable to run git {' '.join(args)}") from exc


def git_stdout(args: list[str], cwd: Path, *, required: bool = True) -> str:
    completed = run_git(args, cwd)
    if required and completed.returncode != 0:
        raise AuditError(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def parse_git_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_now(config: AuditConfig) -> datetime:
    return (config.now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def repo_root(path: Path) -> Path:
    raw = git_stdout(["rev-parse", "--show-toplevel"], path)
    return Path(raw).resolve()


def list_git_worktrees(root: Path) -> list[dict[str, str]]:
    raw = git_stdout(["worktree", "list", "--porcelain"], root)
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree" and current:
            worktrees.append(current)
            current = {}
        current[key] = value
    if current:
        worktrees.append(current)
    return worktrees


def parse_porcelain_paths(raw: str) -> list[str]:
    paths: list[str] = []
    for line in raw.splitlines():
        if len(line) < 3:
            continue
        path = line[2:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return paths


def module_for_path(path: str) -> str:
    clean = path.strip().lstrip("/")
    if not clean:
        return "."
    return clean.split("/", 1)[0]


def directory_size_bytes(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for filename in filenames:
            file_path = Path(dirpath) / filename
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def resolve_target_ref(root: Path, configured: str | None) -> str | None:
    candidates = [configured] if configured else ["origin/main", "main", "origin/master", "master"]
    for ref in candidates:
        if not ref:
            continue
        if run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root).returncode == 0:
            return ref
    return None


def is_ancestor(cwd: Path, older: str, newer: str) -> bool | None:
    completed = run_git(["merge-base", "--is-ancestor", older, newer], cwd)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def upstream_state(path: Path, target_ref: str | None) -> dict[str, Any]:
    upstream = git_stdout(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], path, required=False)
    if not upstream:
        ahead = None
        if target_ref:
            counts = git_stdout(["rev-list", "--count", f"{target_ref}..HEAD"], path, required=False)
            ahead = int(counts) if counts.isdigit() else None
        return {
            "upstream": None,
            "ahead": ahead,
            "behind": None,
            "unpushed": ahead is not None and ahead > 0,
        }
    counts = git_stdout(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], path, required=False)
    ahead: int | None = None
    behind: int | None = None
    parts = counts.split()
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        ahead, behind = int(parts[0]), int(parts[1])
    return {
        "upstream": redact_text(upstream),
        "ahead": ahead,
        "behind": behind,
        "unpushed": ahead is not None and ahead > 0,
    }


def branch_name(entry: dict[str, str], path: Path) -> str:
    branch = entry.get("branch") or git_stdout(["branch", "--show-current"], path, required=False)
    if branch.startswith("refs/heads/"):
        branch = branch.removeprefix("refs/heads/")
    return redact_text(branch or "(detached)")


def audit_one_worktree(entry: dict[str, str], config: AuditConfig, target_ref: str | None) -> dict[str, Any]:
    path = Path(entry["worktree"]).resolve()
    if not path.exists():
        return {
            "path": redact_text(str(path)),
            "path_hash": path_hash(path),
            "branch": redact_text(entry.get("branch", "(unknown)").removeprefix("refs/heads/")),
            "commit": redact_text((entry.get("HEAD") or "")[:12]),
            "modules": [],
            "activity": {
                "last_commit_at": None,
                "age_days": None,
                "stale": True,
                "retention_days": config.retention_days,
            },
            "size": {
                "bytes": 0,
                "gib": 0.0,
                "oversized": False,
                "oversized_gib": config.oversized_gib,
            },
            "merge_state": {
                "target_ref": redact_text(target_ref or ""),
                "merged": None,
                "upstream": None,
                "ahead": None,
                "behind": None,
                "unpushed": None,
            },
            "dirty": False,
            "changed_files": [],
            "overlap": {"modules": [], "with": []},
            "cleanup_candidate": False,
            "candidate_blockers": ["missing_path"],
        }
    status_raw = git_stdout(["status", "--porcelain=v1", "--untracked-files=all"], path, required=False)
    changed_paths = parse_porcelain_paths(status_raw)
    modules = sorted({module_for_path(path) for path in changed_paths})
    commit = git_stdout(["rev-parse", "--short=12", "HEAD"], path, required=False) or entry.get("HEAD", "")[:12]
    last_commit_raw = git_stdout(["log", "-1", "--format=%cI"], path, required=False)
    last_commit_at = parse_git_datetime(last_commit_raw)
    now = iso_now(config)
    age_days = None
    if last_commit_at:
        age_days = max(0, int((now - last_commit_at.astimezone(timezone.utc)).total_seconds() // 86400))
    stale = age_days is not None and age_days >= config.retention_days
    size_bytes = directory_size_bytes(path)
    size_gib = size_bytes / BYTES_PER_GIB
    upstream = upstream_state(path, target_ref)
    merged = None
    if target_ref:
        merged = is_ancestor(path, "HEAD", target_ref)
    dirty = bool(changed_paths)
    oversized = size_gib >= config.oversized_gib
    candidate = (
        not dirty
        and merged is True
        and stale
        and upstream.get("unpushed") is not True
        and not oversized
    )
    reasons: list[str] = []
    if dirty:
        reasons.append("dirty")
    if upstream.get("unpushed") is True:
        reasons.append("unpushed")
    if merged is not True:
        reasons.append("unmerged" if merged is False else "merge_unknown")
    if not stale:
        reasons.append("retention_not_met")
    if oversized:
        reasons.append("oversized")
    return {
        "path": redact_text(str(path)),
        "path_hash": path_hash(path),
        "branch": branch_name(entry, path),
        "commit": redact_text(commit),
        "modules": modules,
        "activity": {
            "last_commit_at": last_commit_raw or None,
            "age_days": age_days,
            "stale": stale,
            "retention_days": config.retention_days,
        },
        "size": {
            "bytes": size_bytes,
            "gib": round(size_gib, 3),
            "oversized": oversized,
            "oversized_gib": config.oversized_gib,
        },
        "merge_state": {
            "target_ref": redact_text(target_ref or ""),
            "merged": merged,
            **upstream,
        },
        "dirty": dirty,
        "changed_files": [redact_text(path) for path in changed_paths],
        "overlap": {"modules": [], "with": []},
        "cleanup_candidate": candidate,
        "candidate_blockers": reasons,
    }


def add_overlap(worktrees: list[dict[str, Any]]) -> None:
    dirty_by_module: dict[str, list[dict[str, Any]]] = {}
    for item in worktrees:
        if not item["dirty"]:
            continue
        for module in item["modules"]:
            dirty_by_module.setdefault(module, []).append(item)
    for item in worktrees:
        overlapping_modules = [
            module
            for module in item["modules"]
            if len(dirty_by_module.get(module, [])) > 1
        ]
        peers = sorted(
            {
                peer["path_hash"]
                for module in overlapping_modules
                for peer in dirty_by_module.get(module, [])
                if peer["path_hash"] != item["path_hash"]
            }
        )
        item["overlap"] = {"modules": sorted(overlapping_modules), "with": peers}
        if overlapping_modules and "overlap" not in item["candidate_blockers"]:
            item["candidate_blockers"].append("overlap")
            item["cleanup_candidate"] = False


def disk_policy(path: Path, config: AuditConfig) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    free_gib = usage.free / BYTES_PER_GIB
    status = "ok"
    actions: list[str] = []
    if free_gib < config.warn_free_gib:
        status = "warn"
        actions.append("warn")
    if free_gib < config.block_worktree_free_gib:
        status = "block_new_worktree"
        actions.append("block_new_worktree")
    if free_gib < config.block_release_free_gib:
        status = "block_release"
        actions.append("block_release")
    return {
        "free_gib": round(free_gib, 3),
        "thresholds_gib": {
            "warn": config.warn_free_gib,
            "block_new_worktree": config.block_worktree_free_gib,
            "block_release": config.block_release_free_gib,
        },
        "status": status,
        "actions": actions,
    }


def audit_repository(config: AuditConfig) -> dict[str, Any]:
    root = repo_root(config.root)
    target_ref = resolve_target_ref(root, config.target_ref)
    entries = list_git_worktrees(root)
    worktrees = [audit_one_worktree(entry, config, target_ref) for entry in entries]
    add_overlap(worktrees)
    return {
        "schema": "cbm.worktree_audit.v1",
        "generated_at": iso_now(config).isoformat().replace("+00:00", "Z"),
        "repo": {"root": redact_text(str(root)), "root_hash": path_hash(root)},
        "disk": disk_policy(root, config),
        "summary": {
            "worktrees": len(worktrees),
            "dirty": sum(1 for item in worktrees if item["dirty"]),
            "cleanup_candidates": sum(1 for item in worktrees if item["cleanup_candidate"]),
            "overlaps": sum(1 for item in worktrees if item["overlap"]["modules"]),
            "target_ref": redact_text(target_ref or ""),
        },
        "worktrees": worktrees,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Any path inside the git repository")
    parser.add_argument("--target-ref", help="Merge target; defaults to origin/main, main, origin/master, master")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--oversized-gib", type=float, default=DEFAULT_OVERSIZED_GIB)
    parser.add_argument("--warn-free-gib", type=float, default=DEFAULT_WARN_FREE_GIB)
    parser.add_argument("--block-worktree-free-gib", type=float, default=DEFAULT_BLOCK_WORKTREE_FREE_GIB)
    parser.add_argument("--block-release-free-gib", type=float, default=DEFAULT_BLOCK_RELEASE_FREE_GIB)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = audit_repository(
            AuditConfig(
                root=Path(args.root),
                target_ref=args.target_ref,
                retention_days=args.retention_days,
                oversized_gib=args.oversized_gib,
                warn_free_gib=args.warn_free_gib,
                block_worktree_free_gib=args.block_worktree_free_gib,
                block_release_free_gib=args.block_release_free_gib,
            )
        )
    except AuditError as exc:
        print(json.dumps({"schema": "cbm.worktree_audit.v1", "ok": False, "error": redact_text(str(exc))}))
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
