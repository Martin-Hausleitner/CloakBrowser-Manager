#!/usr/bin/env python3
"""Idempotent, secret-safe local provisioner for the host ACPX worker.

Validates all supplied local paths, creates or reuses a strong worker token
file, and renders the checked-in systemd user unit. The token is never returned,
printed, placed in argv, or embedded in the rendered unit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.acpx_runner import ACPX_VERSION, validate_mcp_config, validate_permission_policy  # noqa: E402
from scripts.provision_browser_use_worker import (  # noqa: E402
    _atomic_write_text,
    _read_worker_token,
    _reject_symlink,
    ensure_worker_key_file,
    require_loopback_manager_url,
    is_valid_worker_token,
    systemd_quote,
    validate_worker_id,
)

DEFAULT_WORKER_ID = "acpx-worker"
SECURE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
SECRET_TOKEN_RE = re.compile(r"cbm_worker_[0-9a-fA-F]{16,}")
PLACEHOLDER_RE = re.compile(r"@[A-Z_]+@")


def default_template_path(repo: Path) -> Path:
    return repo / "deploy" / "systemd" / "cloakbrowser-acpx-worker.service.template"


def _require_absolute_path(path: Path | str, *, label: str) -> Path:
    value = Path(path).expanduser()
    if SECRET_TOKEN_RE.search(str(value)):
        raise ValueError(f"{label} must not contain secret-like token material")
    if not value.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return value


def _require_no_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")


def _resolve_existing_file(path: Path | str, *, label: str, private: bool = False) -> Path:
    value = _require_absolute_path(path, label=label)
    _require_no_symlink(value, label=label)
    if not value.is_file():
        raise FileNotFoundError(f"{label} must be an existing file")
    if private and stat.S_IMODE(value.stat().st_mode) != SECURE_FILE_MODE:
        raise ValueError(f"{label} must use mode 0600")
    return value.resolve()


def _resolve_existing_directory(path: Path | str, *, label: str, private: bool = False) -> Path:
    value = _require_absolute_path(path, label=label)
    _require_no_symlink(value, label=label)
    if not value.is_dir():
        raise FileNotFoundError(f"{label} must be an existing directory")
    if private and stat.S_IMODE(value.stat().st_mode) != PRIVATE_DIR_MODE:
        raise ValueError(f"{label} must use mode 0700")
    return value.resolve()


def _resolve_output_file(path: Path | str, *, label: str) -> Path:
    value = _require_absolute_path(path, label=label)
    _require_no_symlink(value, label=label)
    if value.exists() and not value.is_file():
        raise ValueError(f"{label} path exists but is not a regular file")
    return value


def _validate_output_parent(path: Path, *, label: str) -> None:
    parent = path.parent
    cursor = parent
    while not cursor.exists():
        next_cursor = cursor.parent
        if next_cursor == cursor:
            raise ValueError(f"{label} parent is not createable")
        cursor = next_cursor
    if cursor.is_symlink():
        raise ValueError(f"{label} parent must not be a symlink")
    if not cursor.is_dir():
        raise ValueError(f"{label} parent must be an existing directory")
    mode = stat.S_IMODE(cursor.stat().st_mode)
    if mode & 0o022:
        raise ValueError(f"{label} parent is unsafe")
    if not os.access(cursor, os.W_OK | os.X_OK):
        raise ValueError(f"{label} parent is not createable")


def _validate_existing_key_if_present(path: Path) -> None:
    if not path.exists():
        return
    if stat.S_IMODE(path.stat().st_mode) != SECURE_FILE_MODE:
        raise ValueError("worker key file must use mode 0600")
    if not is_valid_worker_token(path.read_text(encoding="utf-8").strip()):
        raise ValueError("invalid existing worker key file")


def _validate_repo(repo: Path | str) -> Path:
    repo_path = _resolve_existing_directory(repo, label="repo")
    git_marker = repo_path / ".git"
    if not git_marker.exists():
        raise FileNotFoundError("repo marker .git missing under --repo")
    worker_script = repo_path / "scripts" / "acpx_worker.py"
    if not worker_script.is_file():
        raise FileNotFoundError("scripts/acpx_worker.py missing under --repo")
    return repo_path


def _validate_venv(venv: Path | str) -> Path:
    venv_path = _resolve_existing_directory(venv, label="venv")
    python = venv_path / "bin" / "python"
    if python.is_symlink():
        raise ValueError("venv python must not be a symlink")
    if not python.is_file():
        raise FileNotFoundError("venv/bin/python missing under --venv")
    return venv_path


def _validate_acpx(acpx: Path | str) -> Path:
    executable = _resolve_existing_file(acpx, label="ACPX executable")
    if not os.access(executable, os.X_OK):
        raise ValueError("ACPX executable must be executable")
    return executable


def verify_acpx_version(acpx: Path | str) -> str:
    """Run ``acpx --version`` and require exactly ``0.12.1`` on stdout."""
    executable = _validate_acpx(acpx)
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("ACPX version check failed") from exc
    actual = (completed.stdout or "").strip()
    if completed.returncode != 0 or actual != ACPX_VERSION:
        raise ValueError(f"CloakBrowser requires acpx {ACPX_VERSION}")
    return actual


def _validate_policy_and_mcp(permission_policy: Path | str, mcp_config: Path | str) -> tuple[Path, Path]:
    policy_path = _resolve_existing_file(
        permission_policy,
        label="permission policy",
        private=True,
    )
    mcp_path = _resolve_existing_file(mcp_config, label="MCP config", private=True)
    return validate_permission_policy(policy_path), validate_mcp_config(mcp_path)


def _validate_inputs(
    *,
    repo: Path | str,
    manager_url: str,
    worker_key_file: Path | str,
    venv: Path | str,
    unit_output: Path | str | None,
    worker_id: str,
    permission_policy: Path | str,
    mcp_config: Path | str,
    capability_dir: Path | str,
    acpx: Path | str,
    template_path: Path | str | None,
) -> dict[str, Any]:
    repo_path = _validate_repo(repo)
    venv_path = _validate_venv(venv)
    key_path = _resolve_output_file(worker_key_file, label="worker key file")
    _validate_output_parent(key_path, label="worker key file")
    _validate_existing_key_if_present(key_path)
    unit_path = None
    if unit_output is not None:
        unit_path = _resolve_output_file(unit_output, label="unit output")
        _validate_output_parent(unit_path, label="unit output")
    policy_path, mcp_path = _validate_policy_and_mcp(permission_policy, mcp_config)
    capability_path = _resolve_existing_directory(
        capability_dir,
        label="capability directory",
        private=True,
    )
    acpx_path = _validate_acpx(acpx)
    tpl = (
        _resolve_existing_file(template_path, label="systemd template")
        if template_path
        else _resolve_existing_file(default_template_path(repo_path), label="systemd template")
    )
    return {
        "repo": repo_path,
        "manager_url": require_loopback_manager_url(manager_url),
        "worker_key_file": key_path,
        "venv": venv_path,
        "unit_output": unit_path,
        "worker_id": validate_worker_id(worker_id),
        "permission_policy": policy_path,
        "mcp_config": mcp_path,
        "capability_dir": capability_path,
        "acpx": acpx_path,
        "template_path": tpl,
    }


def render_systemd_unit(
    *,
    repo: Path | str,
    venv: Path | str,
    manager_url: str,
    worker_id: str,
    worker_key_file: Path | str,
    permission_policy: Path | str,
    mcp_config: Path | str,
    capability_dir: Path | str,
    acpx: Path | str,
    template_path: Path | str | None = None,
    home: Path | str | None = None,
) -> str:
    """Render the ACPX worker unit from the checked-in template."""
    values = _validate_inputs(
        repo=repo,
        manager_url=manager_url,
        worker_key_file=worker_key_file,
        venv=venv,
        unit_output=None,
        worker_id=worker_id,
        permission_policy=permission_policy,
        mcp_config=mcp_config,
        capability_dir=capability_dir,
        acpx=acpx,
        template_path=template_path,
    )
    key_path = _require_absolute_path(worker_key_file, label="worker key file").resolve()
    home_path = Path(home).expanduser().resolve() if home else Path.home().resolve()
    path_assignment = f"PATH={home_path}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    documentation = f"file://{values['repo']}/docs/ACPX_WORKER.md"
    replacements = {
        "@WORKING_DIRECTORY@": systemd_quote(str(values["repo"])),
        "@DOCUMENTATION@": systemd_quote(documentation),
        "@VENV_PYTHON@": systemd_quote(str(values["venv"] / "bin" / "python")),
        "@MANAGER_URL@": systemd_quote(values["manager_url"]),
        "@WORKER_ID@": systemd_quote(values["worker_id"]),
        "@TOKEN_FILE@": systemd_quote(str(key_path)),
        "@ACPX_EXECUTABLE@": systemd_quote(str(values["acpx"])),
        "@PERMISSION_POLICY@": systemd_quote(str(values["permission_policy"])),
        "@MCP_CONFIG@": systemd_quote(str(values["mcp_config"])),
        "@CAPABILITY_DIR@": systemd_quote(str(values["capability_dir"])),
        "@PATH_ENVIRONMENT@": systemd_quote(path_assignment),
    }
    rendered = values["template_path"].read_text(encoding="utf-8")
    for needle, replacement in replacements.items():
        rendered = rendered.replace(needle, replacement)
    if PLACEHOLDER_RE.search(rendered):
        raise RuntimeError("rendered ACPX unit contains unresolved placeholders")
    exec_line = next((line for line in rendered.splitlines() if line.startswith("ExecStart=")), "")
    if "-m scripts.acpx_worker" not in rendered:
        raise RuntimeError("ExecStart must use python -m scripts.acpx_worker")
    if "scripts/acpx_worker.py" in exec_line:
        raise RuntimeError("ExecStart must invoke -m scripts.acpx_worker, not a .py path")
    if "--token " in rendered.replace("--token-file", ""):
        raise RuntimeError("rendered ACPX unit must use --token-file only")
    if SECRET_TOKEN_RE.search(rendered):
        raise RuntimeError("rendered ACPX unit must not contain a worker token")
    return rendered


def _receipt(values: dict[str, Any], *, dry_run: bool, version: str) -> dict[str, Any]:
    key_path = Path(values["worker_key_file"])
    unit_path = Path(values["unit_output"])
    receipt: dict[str, Any] = {
        "status": "ok",
        "error_code": None,
        "message": "validated dry run" if dry_run else "provisioned ACPX worker",
        "dry_run": dry_run,
        "repo": str(values["repo"]),
        "manager_url": values["manager_url"],
        "worker_key_file": str(key_path),
        "venv": str(values["venv"]),
        "unit_output": str(unit_path),
        "worker_id": values["worker_id"],
        "permission_policy": str(values["permission_policy"]),
        "mcp_config": str(values["mcp_config"]),
        "capability_dir": str(values["capability_dir"]),
        "acpx": str(values["acpx"]),
        "version": version,
        "would_create_key": not key_path.exists(),
        "would_write_unit": True,
    }
    if key_path.exists():
        receipt["key_mode"] = oct(stat.S_IMODE(key_path.stat().st_mode))
    if unit_path.exists():
        receipt["unit_mode"] = oct(stat.S_IMODE(unit_path.stat().st_mode))
    return receipt


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid arguments")


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "validation_error"
    if isinstance(exc, FileNotFoundError):
        return "missing_path"
    return "provision_error"


def _error_payload(exc: Exception) -> dict[str, str]:
    return {
        "status": "error",
        "error_code": _error_code(exc),
        "message": "provision failed",
    }


def provision(
    *,
    repo: Path | str,
    manager_url: str,
    worker_key_file: Path | str,
    venv: Path | str,
    unit_output: Path | str,
    worker_id: str = DEFAULT_WORKER_ID,
    permission_policy: Path | str,
    mcp_config: Path | str,
    capability_dir: Path | str,
    acpx: Path | str,
    template_path: Path | str | None = None,
    home: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and optionally apply local ACPX worker provisioning."""
    values = _validate_inputs(
        repo=repo,
        manager_url=manager_url,
        worker_key_file=worker_key_file,
        venv=venv,
        unit_output=unit_output,
        worker_id=worker_id,
        permission_policy=permission_policy,
        mcp_config=mcp_config,
        capability_dir=capability_dir,
        acpx=acpx,
        template_path=template_path,
    )
    version = verify_acpx_version(values["acpx"])
    receipt = _receipt(values, dry_run=dry_run, version=version)
    if dry_run:
        return receipt

    key_path = Path(values["worker_key_file"])
    unit_path = Path(values["unit_output"])
    _reject_symlink(key_path, "worker key file")
    _reject_symlink(unit_path, "unit output")
    ensure_worker_key_file(key_path)
    token = _read_worker_token(key_path)
    try:
        unit_text = render_systemd_unit(
            repo=values["repo"],
            venv=values["venv"],
            manager_url=values["manager_url"],
            worker_id=values["worker_id"],
            worker_key_file=key_path,
            permission_policy=values["permission_policy"],
            mcp_config=values["mcp_config"],
            capability_dir=values["capability_dir"],
            acpx=values["acpx"],
            template_path=values["template_path"],
            home=home,
        )
        _atomic_write_text(unit_path, unit_text, label="unit output")
    finally:
        del token
    receipt = _receipt(values, dry_run=False, version=version)
    receipt["key_mode"] = oct(stat.S_IMODE(key_path.stat().st_mode))
    receipt["unit_mode"] = oct(stat.S_IMODE(unit_path.stat().st_mode))
    receipt["would_create_key"] = False
    receipt["would_write_unit"] = True
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="provision_acpx_worker",
        description=(
            "Validate and locally provision ACPX worker key material and a "
            "systemd user unit. Outputs secret-safe JSON."
        ),
    )
    parser.add_argument("--repo", required=True, help="Absolute path to the CloakBrowser Manager repo")
    parser.add_argument("--manager-url", required=True, help="Loopback Manager origin URL")
    parser.add_argument("--worker-key-file", required=True, help="Absolute path for the worker token file")
    parser.add_argument("--venv", required=True, help="Absolute worker venv path; bin/python must exist")
    parser.add_argument("--unit-output", required=True, help="Absolute path for the rendered systemd unit")
    parser.add_argument("--worker-id", default=DEFAULT_WORKER_ID, help=f"Worker ID (default: {DEFAULT_WORKER_ID})")
    parser.add_argument("--permission-policy", required=True, help="Absolute private ACPX permission policy JSON")
    parser.add_argument("--mcp-config", required=True, help="Absolute private ACPX MCP config JSON")
    parser.add_argument("--capability-dir", required=True, help="Absolute private capability directory")
    parser.add_argument("--acpx", required=True, help="Absolute ACPX executable path pinned to 0.12.1")
    parser.add_argument("--template", default=None, help="Optional absolute systemd template path")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print planned paths without writes")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        result = provision(
            repo=args.repo,
            manager_url=args.manager_url,
            worker_key_file=args.worker_key_file,
            venv=args.venv,
            unit_output=args.unit_output,
            worker_id=args.worker_id,
            permission_policy=args.permission_policy,
            mcp_config=args.mcp_config,
            capability_dir=args.capability_dir,
            acpx=args.acpx,
            template_path=args.template,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - keep CLI errors secret-safe
        print(json.dumps(_error_payload(exc), sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
