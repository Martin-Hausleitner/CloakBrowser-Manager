#!/usr/bin/env python3
"""Build fail-closed VCVM release manifests without exposing secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = "vcvm-release-v1"
MIN_RELEASE_FREE_GIB = 8
MIN_WORKTREE_FREE_GIB = 12
DEFAULT_REMOTE_PATH = "/home/coder/cloakbrowser-manager"
DEFAULT_SOURCE_REMOTE = "fork"
RELEASE_ID_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]{11,80}$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MISSING_APPLY_GATES = ("transaction_engine_re_review",)
DEFAULT_ARTIFACTS = (
    "Dockerfile",
    "docker-compose.vcvm.yml",
    "backend",
    "frontend",
    "scripts/deploy_vcvm.sh",
)
DATABASE_MIGRATION_RE = re.compile(r"""migration_version\s*=\s*["']([^"']+)["']""")
HASH_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
}
SECRET_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bcbm_(?:agent|worker)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"(?i)(?:token|secret|password|passwd|apikey|api_key)=([^&\s]{8,})"),
)


class ManifestError(RuntimeError):
    """A deterministic fail-closed manifest error."""


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def reject_secret_text(value: object, label: str) -> None:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ManifestError(f"{label} contains raw secret or credentialed URL")


def validate_release_id(release_id: str) -> str:
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ManifestError("release_id must be 12-81 safe characters: letters, numbers, dot, underscore or dash")
    if ".." in release_id:
        raise ManifestError("release_id must not contain traversal")
    reject_secret_text(release_id, "release id")
    return release_id


def safe_git_remote(root: Path, remote_name: str, expected_remote: str | None) -> str:
    if not REMOTE_NAME_RE.fullmatch(remote_name):
        raise ManifestError("git remote name contains unsafe characters")
    reject_secret_text(remote_name, "git remote name")
    try:
        remote = run_git(root, "remote", "get-url", remote_name)
    except subprocess.CalledProcessError:
        raise ManifestError(f"required source remote is missing: {remote_name}") from None
    if not remote:
        raise ManifestError(f"required source remote has no URL: {remote_name}")
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.username:
        raise ManifestError("source remote contains credentials")
    reject_secret_text(remote, "git remote")
    if expected_remote is not None and remote != expected_remote:
        raise ManifestError("source remote does not match expected fork remote")
    return remote


def git_metadata(
    root: Path,
    *,
    allow_dirty: bool,
    source_remote: str,
    expected_source_remote: str | None,
) -> dict[str, object]:
    commit = run_git(root, "rev-parse", "HEAD")
    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = run_git(root, "status", "--porcelain")
    if status and not allow_dirty:
        raise ManifestError("release source must be a clean git checkout")
    reject_secret_text(branch, "git branch")
    return {
        "commit": commit,
        "branch": branch,
        "remote_name": source_remote,
        "remote": safe_git_remote(root, source_remote, expected_source_remote),
        "dirty": bool(status),
    }


def measured_disk(
    path: Path,
    *,
    override_total_bytes: int | None,
    override_used_bytes: int | None,
    override_free_bytes: int | None,
    measurement_source: str,
) -> dict[str, object]:
    usage = shutil.disk_usage(path)
    has_override = any(value is not None for value in (override_total_bytes, override_used_bytes, override_free_bytes))
    if measurement_source == "local" and has_override:
        raise ManifestError("override disk values must use measurement_source=override or vcvm-remote")
    if measurement_source == "vcvm-remote" and not all(
        value is not None for value in (override_total_bytes, override_used_bytes, override_free_bytes)
    ):
        raise ManifestError("vcvm-remote disk measurement requires total, used and free byte values")
    free_bytes = override_free_bytes if override_free_bytes is not None else usage.free
    total_bytes = override_total_bytes if override_total_bytes is not None else usage.total
    used_bytes = override_used_bytes if override_used_bytes is not None else max(total_bytes - free_bytes, 0)
    if min(total_bytes, used_bytes, free_bytes) < 0:
        raise ManifestError("disk byte values must be non-negative")
    if used_bytes > total_bytes or free_bytes > total_bytes or used_bytes + free_bytes > total_bytes:
        raise ManifestError("disk used/free byte values and their sum must not exceed total bytes")
    return {
        "path": str(path),
        "measurement_source": measurement_source,
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "free_gib": round(free_bytes / (1024**3), 3),
        "release_min_free_gib": MIN_RELEASE_FREE_GIB,
        "new_worktree_min_free_gib": MIN_WORKTREE_FREE_GIB,
    }


def ensure_disk_gate(disk: dict[str, object]) -> None:
    free_bytes = int(disk["free_bytes"])
    if free_bytes < MIN_RELEASE_FREE_GIB * 1024**3:
        raise ManifestError("refusing release: less than 8 GiB free on target volume")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_path(root: Path, relative: str) -> dict[str, object]:
    candidate = root / relative
    if candidate.is_symlink():
        raise ManifestError(f"artifact path must not be a symlink: {relative}")
    path = candidate.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ManifestError(f"artifact path escapes source root: {relative}") from None
    if not path.exists():
        raise ManifestError(f"artifact path is missing: {relative}")
    if path.is_file():
        return {"path": relative, "type": "file", "sha256": hash_file(path)}

    files: list[dict[str, str]] = []
    for child in sorted(path.rglob("*")):
        child_rel = child.relative_to(root)
        if any(part in HASH_SKIP_DIRS for part in child_rel.parts):
            continue
        if child.is_symlink():
            raise ManifestError(f"artifact tree contains symlink: {child_rel}")
        if child.is_file():
            files.append({"path": child_rel.as_posix(), "sha256": hash_file(child)})
    digest = hashlib.sha256()
    for item in files:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\0")
    return {
        "path": relative,
        "type": "directory",
        "sha256": digest.hexdigest(),
        "file_count": len(files),
    }


def artifact_set_sha256(artifacts: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(artifacts, key=lambda entry: str(entry["path"])):
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["type"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def git_archive_sha256(root: Path, commit: str) -> str:
    with tempfile.NamedTemporaryFile(prefix=f"cbm-manifest-{commit[:12]}-", suffix=".tar", delete=True) as archive:
        subprocess.run(
            ("git", "archive", "--format=tar", "--prefix=source/", "-o", archive.name, commit),
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return hash_file(Path(archive.name))


def migration_set(root: Path) -> list[dict[str, str]]:
    candidates = [
        root / "backend" / "migrations",
        root / "backend" / "alembic" / "versions",
        root / "migrations",
    ]
    migrations: list[dict[str, str]] = []
    for directory in candidates:
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise ManifestError(f"migration directory must not be a symlink: {directory.relative_to(root)}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ManifestError(f"migration path must not be a symlink: {path.relative_to(root)}")
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                migrations.append({"source": rel, "identifier": rel, "sha256": hash_file(path)})
    if migrations:
        return migrations
    database_py = root / "backend" / "database.py"
    if database_py.exists() and not database_py.is_symlink():
        text = database_py.read_text(encoding="utf-8")
        database_hash = hash_file(database_py)
        for identifier in sorted(set(DATABASE_MIGRATION_RE.findall(text))):
            reject_secret_text(identifier, "migration identifier")
            migrations.append(
                {
                    "source": "backend/database.py:migration_version",
                    "identifier": identifier,
                    "sha256": database_hash,
                }
            )
    return migrations


def build_manifest(args: argparse.Namespace) -> dict[str, object]:
    root = args.source_root.resolve()
    if not root.exists():
        raise ManifestError("source root does not exist")
    expected_source_remote = args.expected_source_remote
    if expected_source_remote is not None:
        reject_secret_text(expected_source_remote, "expected source remote")
    git = git_metadata(
        root,
        allow_dirty=args.allow_dirty,
        source_remote=args.source_remote,
        expected_source_remote=expected_source_remote,
    )
    disk = measured_disk(
        args.disk_path.resolve(),
        override_total_bytes=args.disk_total_bytes,
        override_used_bytes=args.disk_used_bytes,
        override_free_bytes=args.disk_free_bytes,
        measurement_source=args.measurement_source,
    )
    ensure_disk_gate(disk)

    created_at = args.created_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    release_id = args.release_id or f"{created_at.replace(':', '').replace('-', '').replace('Z', 'Z')}-{str(git['commit'])[:12]}"
    release_id = validate_release_id(release_id)
    artifacts = [hash_path(root, item) for item in args.artifact]
    archive_sha256 = git_archive_sha256(root, str(git["commit"]))
    migrations = migration_set(root)
    if not migrations:
        raise ManifestError("release manifest requires a non-empty migration set")
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "created_at": created_at,
        "source": {
            "kind": "git",
            "branch": git["branch"],
            "commit": git["commit"],
            "remote_name": git["remote_name"],
            "remote": git["remote"],
            "dirty": git["dirty"],
            "archive_sha256": archive_sha256,
        },
        "target": {
            "host": args.host,
            "remote_path": args.remote_path,
            "release_dir": f"{args.remote_path}/releases/{release_id}",
            "current_symlink": f"{args.remote_path}/current",
        },
        "disk": disk,
        "artifacts": artifacts,
        "artifact_set_sha256": artifact_set_sha256(artifacts),
        "migrations": migrations,
        "policy": {
            "dry_run_default": True,
            "release_requires_clean_commit": not args.allow_dirty,
            "apply_available": False,
            "missing_apply_gates": list(MISSING_APPLY_GATES),
            "never_prune_shared_host_resources": True,
        },
    }
    serialized = json.dumps(manifest, sort_keys=True)
    reject_secret_text(serialized, "manifest")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--disk-path", type=Path, default=Path.cwd())
    parser.add_argument("--disk-total-bytes", type=int)
    parser.add_argument("--disk-used-bytes", type=int)
    parser.add_argument("--disk-free-bytes", type=int)
    parser.add_argument("--measurement-source", choices=("local", "override", "vcvm-remote"))
    parser.add_argument("--host", default="vcvm")
    parser.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    parser.add_argument("--source-remote", default=DEFAULT_SOURCE_REMOTE)
    parser.add_argument("--expected-source-remote")
    parser.add_argument("--release-id")
    parser.add_argument("--created-at")
    parser.add_argument("--artifact", action="append", default=list(DEFAULT_ARTIFACTS))
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--require-migrations",
        action="store_true",
        help="Compatibility flag; non-empty migrations are always required",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.measurement_source is None:
        args.measurement_source = (
            "override"
            if any(value is not None for value in (args.disk_total_bytes, args.disk_used_bytes, args.disk_free_bytes))
            else "local"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_manifest(args)
    except (ManifestError, subprocess.CalledProcessError, OSError) as exc:
        print(f"release manifest refused: {exc}", file=sys.stderr)
        return 75
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
