#!/usr/bin/env python3
"""Idempotent, secret-safe provisioner for the host Browser-Use worker.

Creates or reuses a strong ``cbm_worker_`` key file, writes a dedicated
``.env.worker.vcvm`` (only ``CBM_WORKER_ID`` / ``CBM_WORKER_TOKEN``), and
renders a systemd user unit. Never prints, logs, or returns the token.
All filesystem paths must be supplied explicitly on the CLI.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WORKER_KEY_PREFIX = "cbm_worker_"
WORKER_KEY_HEX_LEN = 64
DEFAULT_WORKER_ID = "browser-use-worker"
TOKEN_PATTERN = re.compile(rf"^{re.escape(WORKER_KEY_PREFIX)}[0-9a-f]{{{WORKER_KEY_HEX_LEN}}}$")
WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SECURE_MODE = 0o600
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def generate_worker_token() -> str:
    """Return ``cbm_worker_`` + 64 lowercase hex (32 random bytes)."""
    return WORKER_KEY_PREFIX + secrets.token_bytes(WORKER_KEY_HEX_LEN // 2).hex()


def is_valid_worker_token(token: str | None) -> bool:
    """True only for exact Manager format: cbm_worker_ + 64 lowercase hex."""
    if not isinstance(token, str):
        return False
    return TOKEN_PATTERN.fullmatch(token) is not None


def validate_worker_id(worker_id: str | None) -> str:
    """Accept only ``^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`` (no whitespace/=/newlines)."""
    if not isinstance(worker_id, str) or not WORKER_ID_PATTERN.fullmatch(worker_id):
        raise ValueError("invalid worker id")
    if any(ch.isspace() for ch in worker_id) or "=" in worker_id:
        raise ValueError("invalid worker id")
    return worker_id


def systemd_quote(value: str) -> str:
    """Quote a value for systemd unit argv / path / Environment contexts.

    Escapes ``\\``, ``"``, and ``%`` (as ``%%``). Wraps in double quotes when the
    value is empty or contains whitespace/metacharacters. No shell interpolation.
    """
    text = str(value)
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
    )
    if text == "" or re.search(r'[\s"\\\'`]', text) or "%" in text:
        return f'"{escaped}"'
    return escaped


def _chmod_0600(path: Path) -> None:
    os.chmod(path, SECURE_MODE)


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")


def require_loopback_manager_url(manager_url: str) -> str:
    """Require a loopback http(s) origin only (no credentials/query/fragment/path)."""
    raw = str(manager_url or "").strip().rstrip("/")
    if not raw:
        raise ValueError("manager URL is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("manager URL must be an absolute http(s) URL")
    if not parsed.netloc:
        raise ValueError("manager URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("manager URL must not include credentials")
    if "@" in parsed.netloc:
        raise ValueError("manager URL must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("manager URL must not include query or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("manager URL must be a loopback origin only")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("manager URL must target loopback")
    if parsed.port is not None and not (1 <= int(parsed.port) <= 65535):
        raise ValueError("manager URL port is invalid")

    if host == "::1":
        host_part = "[::1]"
    else:
        host_part = host
    if parsed.port:
        return f"{parsed.scheme}://{host_part}:{parsed.port}"
    return f"{parsed.scheme}://{host_part}"


# Keep private alias used by older call sites during transition.
_require_loopback_manager_url = require_loopback_manager_url


def _atomic_write_text(path: Path, text: str, *, label: str) -> None:
    """Write text via exclusive same-dir tempfile, fsync, and os.replace."""
    _reject_symlink(path, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path.parent, f"{label} parent")
    fd: int | None = None
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        if _O_NOFOLLOW:
            # Re-open check: tempfile is a new inode; ensure final path is not a symlink.
            _reject_symlink(path, label)
        os.fchmod(fd, SECURE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    _chmod_0600(path)


def _create_unique_backup(src: Path) -> Path:
    """Copy ``src`` to ``src.bak.<stamp>`` with O_EXCL|O_NOFOLLOW and mode 0600."""
    _reject_symlink(src, "worker env file")
    last_error: Exception | None = None
    for _ in range(16):
        stamp = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
        bak = src.with_name(f"{src.name}.bak.{stamp}")
        if bak.exists() or bak.is_symlink():
            continue
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
        try:
            fd = os.open(str(bak), flags, SECURE_MODE)
        except FileExistsError as exc:
            last_error = exc
            continue
        try:
            with os.fdopen(fd, "wb") as out_handle:
                data = src.read_bytes()
                out_handle.write(data)
                out_handle.flush()
                os.fsync(out_handle.fileno())
            _chmod_0600(bak)
            return bak
        except Exception:
            try:
                bak.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    raise RuntimeError(f"unable to create unique backup: {last_error}")


def ensure_worker_key_file(path: Path | str) -> None:
    """Create a strong worker key if absent; validate and chmod if present.

    Never returns the token. Raises ValueError when an existing file is invalid
    or the path is a symlink.
    """
    key_path = Path(path).expanduser()
    _reject_symlink(key_path, "worker key file")
    if key_path.exists():
        if not key_path.is_file():
            raise ValueError("worker key path exists but is not a regular file")
        token = key_path.read_text(encoding="utf-8").strip()
        if not is_valid_worker_token(token):
            raise ValueError("invalid existing worker key file")
        _chmod_0600(key_path)
        return

    key_path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(key_path.parent, "worker key parent")
    token = generate_worker_token()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
    fd = os.open(str(key_path), flags, SECURE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            key_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _chmod_0600(key_path)


def _read_worker_token(path: Path) -> str:
    _reject_symlink(path, "worker key file")
    token = path.read_text(encoding="utf-8").strip()
    if not is_valid_worker_token(token):
        raise ValueError("invalid existing worker key file")
    return token


def write_worker_env_file(
    path: Path | str,
    *,
    worker_id: str,
    worker_token: str,
) -> None:
    """Write a dedicated worker env file with only CBM_WORKER_ID / CBM_WORKER_TOKEN.

    Validates ``worker_id`` before any write. Creates a unique same-directory
    ``*.bak.<stamp>`` backup only when content actually changes. Mode 0600.
    Never prints the token.
    """
    safe_id = validate_worker_id(worker_id)
    if not is_valid_worker_token(worker_token):
        raise ValueError("invalid worker token for worker env file")
    env_path = Path(path).expanduser()
    _reject_symlink(env_path, "worker env file")
    new_text = f"CBM_WORKER_ID={safe_id}\nCBM_WORKER_TOKEN={worker_token}\n"

    if env_path.exists():
        if not env_path.is_file():
            raise ValueError("worker env path exists but is not a regular file")
        existing = env_path.read_text(encoding="utf-8")
        if existing == new_text:
            _chmod_0600(env_path)
            return
        _create_unique_backup(env_path)
    else:
        env_path.parent.mkdir(parents=True, exist_ok=True)

    _atomic_write_text(env_path, new_text, label="worker env file")


def default_template_path(repo: Path) -> Path:
    return repo / "deploy" / "systemd" / "cloakbrowser-browser-use-worker.service.template"


def render_systemd_unit(
    *,
    repo: Path | str,
    venv: Path | str,
    manager_url: str,
    worker_id: str,
    worker_key_file: Path | str,
    template_path: Path | str | None = None,
    home: Path | str | None = None,
) -> str:
    """Render the systemd user unit from the checked-in template."""
    safe_id = validate_worker_id(worker_id)
    repo_path = Path(repo).expanduser().resolve()
    venv_path = Path(venv).expanduser().resolve()
    key_path = Path(worker_key_file).expanduser().resolve()
    url = require_loopback_manager_url(manager_url)
    tpl = Path(template_path) if template_path else default_template_path(repo_path)
    tpl = tpl.expanduser().resolve()
    if not tpl.is_file():
        raise FileNotFoundError(f"systemd template missing: {tpl}")

    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    venv_python = venv_path / "bin" / "python"
    worker_script = repo_path / "scripts" / "browser_use_worker.py"
    documentation = f"file://{repo_path}/docs/BROWSER_USE_WORKER.md"
    path_assignment = f"PATH={home_path}/.local/bin:/usr/local/bin:/usr/bin:/bin"

    text = tpl.read_text(encoding="utf-8")
    replacements = {
        "@WORKING_DIRECTORY@": systemd_quote(str(repo_path)),
        "@DOCUMENTATION@": systemd_quote(documentation),
        "@VENV_PYTHON@": systemd_quote(str(venv_python)),
        "@WORKER_SCRIPT@": systemd_quote(str(worker_script)),
        "@MANAGER_URL@": systemd_quote(url),
        "@WORKER_ID@": systemd_quote(safe_id),
        "@TOKEN_FILE@": systemd_quote(str(key_path)),
        "@PATH_ENVIRONMENT@": systemd_quote(path_assignment),
        # Legacy single-token placeholders (if present in older templates).
        "@REPO@": systemd_quote(str(repo_path)),
        "@HOME@": systemd_quote(str(home_path)),
    }
    rendered = text
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    if WORKER_KEY_PREFIX in rendered and TOKEN_PATTERN.search(rendered):
        raise RuntimeError("rendered unit must not contain a worker token")
    return rendered


def provision(
    *,
    repo: Path | str,
    manager_url: str,
    worker_env_file: Path | str,
    worker_key_file: Path | str,
    venv: Path | str,
    unit_output: Path | str,
    worker_id: str = DEFAULT_WORKER_ID,
    template_path: Path | str | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Provision key file, worker env file, and systemd unit (no secrets)."""
    safe_id = validate_worker_id(worker_id)
    repo_path = Path(repo).expanduser().resolve()
    env_path = Path(worker_env_file).expanduser()
    key_path = Path(worker_key_file).expanduser()
    venv_path = Path(venv).expanduser().resolve()
    unit_path = Path(unit_output).expanduser()
    url = require_loopback_manager_url(manager_url)

    _reject_symlink(env_path, "worker env file")
    _reject_symlink(key_path, "worker key file")
    _reject_symlink(unit_path, "unit output")

    worker_script = repo_path / "scripts" / "browser_use_worker.py"
    if not worker_script.is_file():
        raise FileNotFoundError("scripts/browser_use_worker.py missing under --repo")

    ensure_worker_key_file(key_path)
    token = _read_worker_token(key_path)
    try:
        write_worker_env_file(env_path, worker_id=safe_id, worker_token=token)
        unit_text = render_systemd_unit(
            repo=repo_path,
            venv=venv_path,
            manager_url=url,
            worker_id=safe_id,
            worker_key_file=key_path,
            template_path=template_path,
            home=home,
        )
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(unit_path, unit_text, label="unit output")
    finally:
        del token

    return {
        "repo": str(repo_path),
        "manager_url": url,
        "worker_env_file": str(env_path),
        "worker_key_file": str(key_path),
        "venv": str(venv_path),
        "unit_output": str(unit_path),
        "worker_id": safe_id,
        "key_mode": oct(stat.S_IMODE(key_path.stat().st_mode)),
        "env_mode": oct(stat.S_IMODE(env_path.stat().st_mode)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision_browser_use_worker",
        description=(
            "Idempotently provision Browser-Use worker secrets and a systemd "
            "user unit. Never prints the worker token."
        ),
    )
    parser.add_argument("--repo", required=True, help="Absolute path to the CloakBrowser Manager repo")
    parser.add_argument(
        "--manager-url",
        required=True,
        help="Loopback Manager origin URL (e.g. http://127.0.0.1:18115)",
    )
    parser.add_argument(
        "--worker-env-file",
        required=True,
        help="Dedicated worker env file (recommended: .env.worker.vcvm); only CBM_WORKER_ID/TOKEN",
    )
    parser.add_argument(
        "--worker-key-file",
        required=True,
        help="Host path for the worker token file (created only if absent)",
    )
    parser.add_argument(
        "--venv",
        required=True,
        help="Dedicated worker virtualenv path (bin/python used in the unit)",
    )
    parser.add_argument(
        "--unit-output",
        required=True,
        help="Path to write the rendered systemd user unit",
    )
    parser.add_argument(
        "--worker-id",
        default=DEFAULT_WORKER_ID,
        help=f"Worker identity written to env and unit (default: {DEFAULT_WORKER_ID})",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Optional override for the systemd unit template path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = provision(
            repo=args.repo,
            manager_url=args.manager_url,
            worker_env_file=args.worker_env_file,
            worker_key_file=args.worker_key_file,
            venv=args.venv,
            unit_output=args.unit_output,
            worker_id=args.worker_id,
            template_path=args.template,
        )
    except Exception as exc:  # noqa: BLE001 — keep errors free of secrets
        print(f"provision failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1

    print("provisioned browser-use worker")
    for key in (
        "repo",
        "manager_url",
        "worker_env_file",
        "worker_key_file",
        "venv",
        "unit_output",
        "worker_id",
        "key_mode",
        "env_mode",
    ):
        print(f"{key}={result[key]}")
    print("note=restart Manager container to load CBM_WORKER_* from .env.worker.vcvm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
