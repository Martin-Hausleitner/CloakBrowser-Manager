#!/usr/bin/env python3
"""Install acpx-agent-family skill to an explicit destination with hash verification.

Defaults to dry-run. Never follows symlinks. Rejects path escapes.
Copies only this skill directory (not the whole repo).

Symlink policy (fail-closed):
- lstat the original destination and every relevant existing ancestor
  *before* any resolve/abspath normalization that could hide links
- reject symlink source roots and symlink files/dirs under the skill tree
- reject symlink directories during walk before pruning
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

SKILL_NAME = "acpx-agent-family"
HASH_ALGO = "sha256"

# Files/dirs that must never be installed (local junk / secrets / bytecode).
SKIP_NAMES = frozenset(
    {
        ".DS_Store",
        "__pycache__",
        ".git",
        ".gitignore",
        ".env",
        ".env.local",
        "Thumbs.db",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

SKIP_SUFFIXES = frozenset({".pyc", ".pyo", ".swp", ".swo", ".pyd"})


class InstallError(ValueError):
    """Fail-closed install contract violation."""


@dataclass(frozen=True, slots=True)
class FileRecord:
    relative: str
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True, slots=True)
class InstallReport:
    skill: str
    mode: str  # dry-run | apply
    source: str
    destination: str
    files: list[FileRecord]
    would_copy: list[str]
    copied: list[str]
    verified: bool
    errors: list[str]
    passed: bool


def skill_root() -> Path:
    """Return the skill directory containing this script (…/acpx-agent-family)."""
    return Path(__file__).resolve().parents[1]


def _lstat_is_symlink(path: Path) -> bool:
    """True if path exists as a symlink (lstat; does not follow)."""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstallError(f"cannot lstat path {path}: {exc}") from exc


def _path_exists_lstat(path: Path) -> bool:
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstallError(f"cannot lstat path {path}: {exc}") from exc


_SYMLINK_HINT = (
    "Use a canonical destination (Path(dest).resolve()) so macOS system path "
    "aliases such as /var -> /private/var are not path components. Explicit "
    "user-created symlink parents remain rejected; this is not a broad allow."
)


def _reject_if_symlink(path: Path, *, label: str) -> None:
    if _lstat_is_symlink(path):
        raise InstallError(
            f"{label} must not be a symlink: {path}. {_SYMLINK_HINT}"
        )


def _absolute_no_follow(path: Path) -> Path:
    """Absolute path without resolving symlinks (normalizes . and .. only)."""
    if not path.is_absolute():
        path = Path.cwd() / path
    # os.path.abspath does not resolve symlinks (unlike Path.resolve).
    return Path(os.path.abspath(path))


def reject_symlinks_in_path(path: Path, *, label: str) -> Path:
    """lstat original path and every existing ancestor before resolve.

    Returns the absolute (non-followed) path. Raises InstallError if any
    existing component is a symlink.

    Security note: system path aliases (e.g. macOS /var) and explicit user
    symlink parents are both rejected. Callers that need a temp install target
    should create it under Path(tempfile.gettempdir()).resolve() so the
    destination string has no symlink ancestors — do not weaken this check.
    """
    raw = Path(path)
    # Check the caller-supplied path string components as given (relative OK).
    if _path_exists_lstat(raw):
        _reject_if_symlink(raw, label=label)

    abs_path = _absolute_no_follow(raw)

    # Walk every ancestor from root → leaf; lstat each existing node.
    parts = abs_path.parts
    if not parts:
        raise InstallError(f"{label} path is empty")

    current = Path(parts[0])  # '/' on POSIX
    if _path_exists_lstat(current):
        _reject_if_symlink(current, label=f"{label} ancestor")

    for part in parts[1:]:
        current = current / part
        if _path_exists_lstat(current):
            _reject_if_symlink(current, label=f"{label} path component")

    return abs_path


def _ensure_dest_explicit(dest: Path) -> Path:
    """Destination must be explicit; final component should be the skill name."""
    # Fail closed on symlink dest/ancestors using lstat *before* resolve.
    abs_dest = reject_symlinks_in_path(dest, label="destination")
    if abs_dest.name != SKILL_NAME:
        candidate = abs_dest / SKILL_NAME
        # Parent already checked; skill leaf may not exist yet.
        if _path_exists_lstat(candidate):
            _reject_if_symlink(candidate, label="destination")
        abs_dest = candidate
    return abs_dest


def _should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    for part in rel_parts:
        if part in SKIP_NAMES:
            return True
        if any(part.endswith(suf) for suf in SKIP_SUFFIXES):
            return True
    if path.suffix in SKIP_SUFFIXES:
        return True
    if path.name.endswith(".pyc") or path.name.endswith(".pyo"):
        return True
    return False


def _iter_skill_files(root: Path) -> list[Path]:
    _reject_if_symlink(root, label="skill root")
    if not root.is_dir():
        raise InstallError(f"skill root is not a directory: {root}")

    root_abs = reject_symlinks_in_path(root, label="skill root")
    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root_abs, followlinks=False):
        current = Path(dirpath)
        # Walk should never land on a symlink dir; still fail closed.
        if _lstat_is_symlink(current):
            raise InstallError(f"refusing to walk symlink directory: {current}")

        # Reject EVERY child directory symlink via lstat *before* SKIP_NAMES.
        # (A symlink named __pycache__ -> outside must fail closed, not be skipped.)
        for d in list(dirnames):
            child = current / d
            if _lstat_is_symlink(child):
                raise InstallError(
                    f"refusing symlink directory under skill (before skip): {child}"
                )

        # Prune skip-names only after symlink rejection for all children.
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_NAMES)

        for name in sorted(filenames):
            path = current / name
            if _lstat_is_symlink(path):
                raise InstallError(f"refusing symlink file under skill: {path}")
            if _should_skip(path, root_abs):
                continue
            if not path.is_file():
                continue
            # Path escape: resolved real path must stay under root real path.
            # Only resolve after confirming this leaf is not a symlink.
            try:
                resolved = path.resolve(strict=True)
                root_resolved = root_abs.resolve(strict=True)
                resolved.relative_to(root_resolved)
            except ValueError as exc:
                raise InstallError(
                    f"path escape detected for {path} -> {path.resolve()}"
                ) from exc
            except OSError as exc:
                raise InstallError(f"cannot resolve skill file {path}: {exc}") from exc
            files.append(path)
    return files


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in _iter_skill_files(root):
        rel = path.relative_to(
            reject_symlinks_in_path(root, label="skill root")
        ).as_posix()
        mode = path.stat().st_mode & 0o777
        records.append(
            FileRecord(
                relative=rel,
                sha256=file_sha256(path),
                size=path.stat().st_size,
                mode=mode,
            )
        )
    if not records:
        raise InstallError("skill has no installable files")
    if not any(r.relative == "SKILL.md" for r in records):
        raise InstallError("SKILL.md missing from skill root")
    # Bytecode must never appear in the install set.
    for r in records:
        if "__pycache__" in r.relative.split("/") or r.relative.endswith(
            (".pyc", ".pyo")
        ):
            raise InstallError(f"bytecode must not be installed: {r.relative}")
    return records


def _copy_file(src: Path, dest: Path, *, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _lstat_is_symlink(dest) or (
        dest.parent.exists() and _lstat_is_symlink(dest.parent)
    ):
        raise InstallError(f"destination path is a symlink: {dest}")
    shutil.copy2(src, dest, follow_symlinks=False)
    if _lstat_is_symlink(dest):
        raise InstallError(f"copy produced symlink at destination: {dest}")
    dest.chmod(mode)


def verify_destination(dest: Path, manifest: Sequence[FileRecord]) -> list[str]:
    errors: list[str] = []
    for record in manifest:
        target = dest / record.relative
        if not _path_exists_lstat(target):
            errors.append(f"missing after install: {record.relative}")
            continue
        if _lstat_is_symlink(target):
            errors.append(f"symlink at destination: {record.relative}")
            continue
        digest = file_sha256(target)
        if digest != record.sha256:
            errors.append(
                f"hash mismatch for {record.relative}: "
                f"expected {record.sha256}, got {digest}"
            )
        if target.stat().st_size != record.size:
            errors.append(f"size mismatch for {record.relative}")
    return errors


def install_skill(
    *,
    destination: Path | str,
    source: Path | None = None,
    apply: bool = False,
) -> InstallReport:
    """Install skill. apply=False (default) is dry-run only."""
    if source is None:
        src_raw = skill_root()
    else:
        src_raw = Path(source)

    # Source: lstat original + ancestors before resolve.
    reject_symlinks_in_path(src_raw, label="source")
    if not src_raw.exists():
        raise InstallError(f"source does not exist: {src_raw}")
    if not src_raw.is_dir():
        raise InstallError(f"source is not a directory: {src_raw}")

    src = reject_symlinks_in_path(src_raw, label="source")
    # Name check on the non-followed absolute path / real name.
    if src.name != SKILL_NAME and src_raw.name != SKILL_NAME:
        # After abspath, name should still be skill name for a real skill root.
        if Path(src).name != SKILL_NAME:
            raise InstallError(
                f"source directory name must be {SKILL_NAME!r}, got {src.name!r}"
            )

    # Resolve only after symlink-free confirmation for comparison identity.
    src_real = src.resolve(strict=True)
    if src_real.name != SKILL_NAME:
        raise InstallError(
            f"source directory name must be {SKILL_NAME!r}, got {src_real.name!r}"
        )

    dest = _ensure_dest_explicit(Path(destination))

    if dest == src or dest.resolve() == src_real:
        raise InstallError("destination must differ from source skill root")

    # Re-check destination parent chain (covers skill leaf append).
    reject_symlinks_in_path(dest.parent, label="destination parent")
    if _path_exists_lstat(dest):
        _reject_if_symlink(dest, label="destination")

    manifest = build_manifest(src_real)
    would_copy = [r.relative for r in manifest]
    mode = "apply" if apply else "dry-run"
    copied: list[str] = []
    errors: list[str] = []

    if apply:
        dest.mkdir(parents=True, exist_ok=True)
        _reject_if_symlink(dest, label="destination")
        reject_symlinks_in_path(dest, label="destination")
        for record in manifest:
            src_file = src_real / record.relative
            dest_file = dest / record.relative
            if _lstat_is_symlink(src_file):
                errors.append(f"source became symlink: {record.relative}")
                continue
            try:
                dest_parent = dest_file.parent
                reject_symlinks_in_path(dest_parent, label="destination file parent")
                dest_resolved = Path(os.path.abspath(dest_file))
                dest_root = Path(os.path.abspath(dest))
                dest_resolved.relative_to(dest_root)
            except (ValueError, InstallError) as exc:
                errors.append(
                    f"path escape for destination file: {record.relative} ({exc})"
                )
                continue
            try:
                _copy_file(src_file, dest_file, mode=record.mode or 0o644)
                copied.append(record.relative)
            except (OSError, InstallError) as exc:
                errors.append(f"copy failed for {record.relative}: {exc}")

        verify_errors = verify_destination(dest, manifest)
        errors.extend(verify_errors)
        verified = not verify_errors and not errors
    else:
        verified = True  # dry-run validates source only

    passed = not errors and (verified if apply else True)
    return InstallReport(
        skill=SKILL_NAME,
        mode=mode,
        source=str(src_real),
        destination=str(dest),
        files=list(manifest),
        would_copy=would_copy,
        copied=copied,
        verified=verified if apply else False,
        errors=errors,
        passed=passed,
    )


def report_to_dict(report: InstallReport) -> dict:
    payload = asdict(report)
    payload["files"] = [asdict(f) for f in report.files]
    payload["hash_algo"] = HASH_ALGO
    payload["file_count"] = len(report.files)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install acpx-agent-family skill to an explicit destination. "
            "Default mode is dry-run (no writes)."
        )
    )
    parser.add_argument(
        "--dest",
        required=True,
        help=(
            "Explicit install destination. If the final path segment is not "
            f"{SKILL_NAME}, it is appended."
        ),
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Optional skill source root (defaults to this skill directory).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually copy files. Without this flag, only dry-run planning runs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = install_skill(
            destination=args.dest,
            source=Path(args.source) if args.source else None,
            apply=bool(args.apply),
        )
    except InstallError as exc:
        err = {"passed": False, "error": str(exc), "skill": SKILL_NAME}
        if args.json:
            print(json.dumps(err, indent=2, sort_keys=True))
        else:
            print(f"INSTALL_ERROR: {exc}", file=sys.stderr)
        return 2

    payload = report_to_dict(report)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"skill={report.skill} mode={report.mode} passed={report.passed}")
        print(f"source={report.source}")
        print(f"destination={report.destination}")
        print(f"files={len(report.files)}")
        for rel in report.would_copy:
            action = "copied" if rel in report.copied else "would_copy"
            print(f"  {action}: {rel}")
        if report.errors:
            print("errors:")
            for e in report.errors:
                print(f"  - {e}")
        if report.mode == "dry-run":
            print("dry-run only; re-run with --apply to install")
        elif report.verified:
            print("hashes verified OK")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
