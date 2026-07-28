#!/usr/bin/env python3
"""Remote-side helper for VCVM release transactions.

The local transaction engine streams this checked-in file to `python3` over SSH
with one JSON request. The helper performs one bounded operation and prints one
JSON object on stdout. Errors are redacted and go to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


HELPER_VERSION = "vcvm-release-helper-v1"
REMOTE_PATH = Path("/home/coder/cloakbrowser-manager")
RELEASES_PATH = REMOTE_PATH / "releases"
CURRENT_LINK = REMOTE_PATH / "current"
STATE_FILE = REMOTE_PATH / ".vcvm-release-state.json"
MANAGED_MARKER = ".cloakbrowser-manager-vcvm-managed"
MANAGER_CONTAINER = "cloakbrowser-manager-vcvm"
MANAGER_VOLUME = "cloakbrowser-manager-vcvm-data"
BROWSER_USE_UNIT = "cloakbrowser-browser-use-worker.service"
BROWSER_USE_TOKEN_PATH = Path("/home/coder/.config/cloakbrowser/browser-use-worker-key")
ACPX_UNIT = "cloakbrowser-acpx.service"
MANAGER_CONTAINER_PORT = 8080
MANAGER_HOST_GATEWAY = "host.docker.internal:host-gateway"
PROXYCHECKER_HOST_HEALTH_URL = "http://172.17.0.1:18899/health"
MANAGED_LAYOUT_DIRS = ("releases", "backups", "receipts")
MANAGED_LAYOUT_MODE = 0o700
MANAGER_BIND_MOUNTS = (
    (Path("/home/coder/orca"), Path("/home/coder/orca"), "ro"),
    (Path("/home/coder/.local"), Path("/home/coder/.local"), "ro"),
    (Path("/home/coder/.config/orca"), Path("/home/coder/.config/orca"), "ro"),
)
MANAGER_RESTART_POLICY = "unless-stopped"
LIVE_PORT = 18115
CANDIDATE_PORT = 18116
CANDIDATE_READINESS_TIMEOUT_SECONDS = 180.0
CANDIDATE_READINESS_POLL_INTERVAL_SECONDS = 2.0
CANDIDATE_PROBE_TIMEOUT_SECONDS = 10.0
CANDIDATE_CURL_CONNECT_TIMEOUT_SECONDS = 2.0
ACPX_PROMOTED_READINESS_TIMEOUT_SECONDS = CANDIDATE_READINESS_TIMEOUT_SECONDS
RESTORE_MANAGER_VERIFY_TIMEOUT_SECONDS = 90.0
RESTORE_MANAGER_VERIFY_POLL_INTERVAL_SECONDS = CANDIDATE_READINESS_POLL_INTERVAL_SECONDS
ACPX_CANDIDATE_PREFLIGHT_INTERVAL_SECONDS = 30
ACPX_PRODUCTION_PREFLIGHT_INTERVAL_SECONDS = 240
ACPX_NODE_LOCK = "deploy/acpx-runtime/package-lock.json"
ACPX_PYTHON_LOCK = "scripts/requirements-acpx-worker.linux-x86_64.py312.txt"
ACPX_BOOTSTRAP_DIR = "acpx-bootstrap"
ACPX_DIRECT_CLI = Path("node_modules/acpx/dist/cli.js")
ACPX_SYSTEMD_PATH = "/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin"
UNIT_FRAGMENT_READ_MAX_BYTES = 1024 * 1024
ACPX_ABSENCE_COMPONENTS = ("binary", "unit", "key", "venv", "capability")
EXPECTED_ACPX_PREFLIGHT_AGENTS = ("codex", "claude", "cursor", "grok-build", "opencode")
RELEASE_ID_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]{11,80}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
BACKUP_RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,80}$")
IMMUTABLE_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACPX_VERSION_RE = re.compile(r"\b0\.12\.1\b")
REQUIRED_COMMANDS = (
    "docker",
    "systemctl",
    "python3",
    "tar",
    "sha256sum",
    "curl",
    "tailscale",
    "orca",
)
EXPECTED_MIGRATIONS = (
    "agent_workspace_v1",
    "task_runs_v1",
    "task_artifacts_v1",
    "worker_runtime_v1",
    "task_runs_acpx_v1",
    "worker_harness_presence_v1",
    "worker_harness_preflights_v1",
    "task_run_binding_v1",
)
OPERATION_SCHEMAS: dict[str, set[str]] = {
    "helper.capabilities": set(),
    "preflight.disk": set(),
    "preflight.marker": set(),
    "preflight.commands": set(),
    "preflight.env": set(),
    "preflight.manager": set(),
    "preflight.browser_use": set(),
    "preflight.acpx": set(),
    "preflight.receipts": set(),
    "preflight.tailscale": set(),
    "bootstrap.acpx_probe": {"release_id"},
    "release.prepare": {"release_id", "commit", "archive_sha256"},
    "release.verify_archive": {"release_id", "archive_sha256"},
    "release.extract": {"release_id"},
    "release.write_marker": {"release_id", "commit"},
    "build.image": {"release_id", "commit"},
    "backup.live": {"commit"},
    "backup.final_stopped": {"commit"},
    "candidate.clone": {"release_id", "backup"},
    "candidate.start": {"release_id", "image_ref", "volume"},
    "candidate.verify": {"commit", "container"},
    "bootstrap.acpx_install": {"release_id", "commit", "node_lock_sha256", "python_lock_sha256"},
    "bootstrap.acpx_provision_candidate": {"release_id", "commit", "manager_port", "runtime"},
    "bootstrap.acpx_start_candidate": {"release_id", "worker_id"},
    "bootstrap.acpx_verify_candidate": {"release_id", "worker_id", "manager_port", "acpx_executable"},
    "bootstrap.acpx_promote": {"release_id", "worker_id", "manager_port", "release_source", "acpx_executable"},
    "bootstrap.acpx_cleanup": {"release_id"},
    "candidate.cleanup": {"release_id"},
    "capture.state": {"commit"},
    "acpx.stage-runtime": {"release_id", "capture"},
    "quiesce.stop_workers": set(),
    "quiesce.stop_live": set(),
    "live.start": {"image_ref", "volume", "port"},
    "workers.rebind": {"release_id", "commit", "capture"},
    "verify.manager": {"commit", "revision_available", "image_id"},
    "verify.browser_use": {"commit", "release_source"},
    "verify.acpx": {"release_source", "expected_absent"},
    "verify.proxychecker": set(),
    "verify.stream": set(),
    "verify.orca": set(),
    "verify.tailscale": set(),
    "restore.runtime": {"capture", "backup"},
    "restore.verify": {"capture", "backup"},
    "rollback.read_state": set(),
    "rollback.verify_backup": {"backup"},
    "rollback.verify_previous": {"previous_runtime"},
    "rollback.start_previous": {"previous_runtime"},
    "state.commit": {"release_id", "current_release", "previous_release", "image", "final_backup", "capture", "acpx_stage", "previous_runtime"},
}
OPTIONAL_OPERATION_ARGS: dict[str, set[str]] = {
    "verify.acpx": {"acpx_executable"},
    "state.commit": {"acpx_stage"},
}
SECRET_PATTERNS = (
    re.compile(r'(?i)"(?:helper_source|token|secret|password|passwd|apikey|api_key)"\s*:\s*"[^"]*"'),
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bcbm_(?:agent|worker)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE),
    re.compile(r"(?i)(?:token|secret|password|passwd|apikey|api_key)=([^&\s]{8,})"),
    re.compile(r"(?i)helper_source=([^\s]+)"),
)
REMOTE_REFUSAL_MESSAGE_LIMIT = 500


class HelperError(RuntimeError):
    pass


REMOTE_REFUSAL_EXCEPTIONS = (
    HelperError,
    OSError,
    subprocess.SubprocessError,
    TimeoutError,
    json.JSONDecodeError,
)


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


def bounded_refusal_reason(value: object) -> str:
    text = redact_text(value).replace("\n", "\\n")
    if len(text) > REMOTE_REFUSAL_MESSAGE_LIMIT:
        return text[: REMOTE_REFUSAL_MESSAGE_LIMIT - 3] + "..."
    return text


def refuse_remote_request(reason: object) -> int:
    print(f"vcvm release remote refused: {bounded_refusal_reason(reason)}", file=sys.stderr)
    return 75


def reject_secret_text(value: object, label: str) -> None:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise HelperError(f"{label} contains secret material")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HelperError(message)


def validate_release_id(value: object) -> str:
    release_id = str(value)
    require(RELEASE_ID_RE.fullmatch(release_id) is not None, "unsafe release id")
    return release_id


def validate_commit(value: object) -> str:
    commit = str(value)
    require(COMMIT_RE.fullmatch(commit) is not None, "unsafe commit")
    return commit


def validate_sha256(value: object) -> str:
    digest = str(value)
    require(SHA256_RE.fullmatch(digest) is not None, "unsafe sha256")
    return digest


def validate_name(value: object, label: str) -> str:
    name = str(value)
    require(SAFE_NAME_RE.fullmatch(name) is not None, f"unsafe {label}")
    return name


def validate_backup_receipt_id(value: object) -> str:
    receipt_id = str(value)
    require(BACKUP_RECEIPT_ID_RE.fullmatch(receipt_id) is not None, "unsafe backup receipt id")
    return receipt_id


def validate_immutable_image(value: object, label: str = "image ref") -> str:
    image_ref = str(value)
    require(IMMUTABLE_IMAGE_RE.fullmatch(image_ref) is not None, f"{label} is not immutable")
    return image_ref


def release_dropin(unit: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{unit}.d" / "50-cloakbrowser-release.conf"


def expected_unit_path(unit: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / unit


def manager_bind_mount_receipt() -> list[dict[str, str]]:
    return [
        {"source": str(source), "target": str(target), "mode": mode}
        for source, target, mode in MANAGER_BIND_MOUNTS
    ]


def _validate_managed_dir(path: Path, label: str) -> None:
    require(path == REMOTE_PATH / label, f"managed layout path is not allowlisted: {label}")
    require(path.exists(), f"managed layout directory is missing: {label}")
    require(path.is_dir(), f"managed layout path is not a directory: {label}")
    require(not path.is_symlink(), f"managed layout directory is a symlink: {label}")
    stat_result = path.stat()
    require(stat_result.st_uid == os.getuid(), f"managed layout owner mismatch: {label}")
    require(stat.S_IMODE(stat_result.st_mode) == MANAGED_LAYOUT_MODE, f"managed layout mode mismatch: {label}")


def _managed_layout_status() -> dict[str, object]:
    require(REMOTE_PATH.exists() and REMOTE_PATH.is_dir(), "managed root is missing")
    require(not REMOTE_PATH.is_symlink(), "managed root is a symlink")
    require(REMOTE_PATH.stat().st_uid == os.getuid(), "managed root owner mismatch")
    paths = {name: REMOTE_PATH / name for name in MANAGED_LAYOUT_DIRS}
    existing = [name for name, path in paths.items() if path.exists() or path.is_symlink()]
    if not existing:
        return {"ok": True, "bootstrap_required": True, "layout_dirs": list(MANAGED_LAYOUT_DIRS)}
    require(set(existing) == set(MANAGED_LAYOUT_DIRS), "managed layout must be complete or exactly absent")
    for name, path in paths.items():
        _validate_managed_dir(path, name)
    return {"ok": True, "bootstrap_required": False, "layout_dirs": list(MANAGED_LAYOUT_DIRS)}


def _ensure_managed_layout() -> None:
    status = _managed_layout_status()
    if status["bootstrap_required"] is True:
        for name in MANAGED_LAYOUT_DIRS:
            path = REMOTE_PATH / name
            require(path == REMOTE_PATH / name, f"managed layout path is not allowlisted: {name}")
            require(not path.exists() and not path.is_symlink(), f"managed layout path must be absent before bootstrap: {name}")
            path.mkdir(mode=MANAGED_LAYOUT_MODE)
    _managed_layout_status()


def _validate_manager_bind_sources() -> None:
    for source, target, mode in MANAGER_BIND_MOUNTS:
        require(source == target, f"Manager bind target must match source: {target}")
        require(mode == "ro", f"Manager bind mount must be read-only: {source}")
        require(source.exists() and source.is_dir(), f"Manager bind source is missing: {source}")
        require(not source.is_symlink(), f"Manager bind source is a symlink: {source}")
        require(source.stat().st_uid == os.getuid(), f"Manager bind source owner mismatch: {source}")


def _manager_docker_run_args(*, container: str, host_port: int, volume: str, image_ref: str) -> list[str]:
    validate_name(volume, "Manager volume")
    _validate_manager_bind_sources()
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        "--restart",
        MANAGER_RESTART_POLICY,
        "--add-host",
        MANAGER_HOST_GATEWAY,
        "-p",
        f"127.0.0.1:{host_port}:{MANAGER_CONTAINER_PORT}",
        "-v",
        f"{volume}:/data",
    ]
    for source, target, mode in MANAGER_BIND_MOUNTS:
        argv.extend(["-v", f"{source}:{target}:{mode}"])
    argv.extend(["--env-file", str(REMOTE_PATH / ".env.vcvm"), image_ref])
    return argv


def _write_json_mode_0600(path: Path, payload: dict[str, object]) -> None:
    _write_text_mode_0600_atomic(path, json.dumps(payload, sort_keys=True) + "\n")


def _write_text_mode_0600_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    require(not tmp.exists() and not tmp.is_symlink(), f"temporary path already exists: {tmp}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    path.chmod(0o600)


def validate_current_pointer(value: object) -> Path | None:
    raw = str(value or "")
    if not raw:
        return None
    pointer = Path(raw)
    require(pointer.is_absolute(), "current pointer must be absolute")
    resolved_releases = RELEASES_PATH.resolve()
    resolved = pointer.resolve()
    require(pointer == resolved, "current pointer must not traverse or use symlinks")
    require(resolved.name == "source", "current pointer must target a release source")
    require(resolved.parent.parent == resolved_releases, "current pointer must stay under releases")
    require(resolved.exists() and resolved.is_dir() and not resolved.is_symlink(), "current pointer must target an existing release source")
    require(not resolved.parent.is_symlink() and not resolved_releases.is_symlink(), "current pointer parents must not be symlinks")
    validate_release_id(resolved.parent.name)
    return resolved


def validate_restore_capture(capture: dict[str, object]) -> dict[str, object]:
    reject_secret_text(json.dumps(capture, sort_keys=True), "restore capture")
    require(capture.get("live_volume") == MANAGER_VOLUME, "captured volume mismatch")
    digest = validate_sha256(capture.get("old_image_digest"))
    image_id = validate_immutable_image(capture.get("old_image_id"), "old image id")
    require(image_id == f"sha256:{digest}", "old image id mismatch")
    previous_revision_available = capture.get("previous_revision_available") is not False
    if previous_revision_available:
        validate_commit(capture.get("previous_revision"))
    else:
        require(capture.get("previous_revision") in {"", None}, "unavailable previous revision must be empty")
    require(capture.get("manager_restart_policy", MANAGER_RESTART_POLICY) == MANAGER_RESTART_POLICY, "captured restart policy mismatch")
    require(capture.get("manager_bind_mounts", manager_bind_mount_receipt()) == manager_bind_mount_receipt(), "captured Manager bind mounts mismatch")
    validate_current_pointer(capture.get("current_pointer"))
    expected_paths = {
        "browser_use_unit_path": expected_unit_path(BROWSER_USE_UNIT),
        "acpx_unit_path": expected_unit_path(ACPX_UNIT),
    }
    for key, expected in expected_paths.items():
        actual = Path(str(capture.get(key, "")))
        require(actual == expected, f"captured unit path is not allowlisted: {key}")
        require(not actual.parent.is_symlink(), f"captured unit path parent is a symlink: {key}")
    for key, unit in (
        ("browser_use_dropin_path", BROWSER_USE_UNIT),
        ("acpx_dropin_path", ACPX_UNIT),
    ):
        require(key in capture, f"captured drop-in path is missing: {key}")
        actual = Path(str(capture[key]))
        require(actual == release_dropin(unit), f"captured drop-in path is not allowlisted: {key}")
        require(not actual.parent.is_symlink(), f"captured drop-in path parent is a symlink: {key}")
    if capture.get("acpx_was_absent") is True:
        require(capture.get("acpx_active_state") == "absent", "captured absent ACPX state mismatch")
        require(capture.get("acpx_unit_sha256") == "0" * 64, "captured absent ACPX unit hash mismatch")
        release_id = str(capture.get("acpx_bootstrap_release_id", ""))
        if release_id:
            validate_release_id(release_id)
    return capture


def release_dir(release_id: str) -> Path:
    release_id = validate_release_id(release_id)
    path = RELEASES_PATH / release_id
    require(path.parent == RELEASES_PATH, "release path escapes releases directory")
    if path.exists() or path.is_symlink():
        stat_result = path.lstat()
        require(not stat.S_ISLNK(stat_result.st_mode), "release directory must not be a symlink")
        require(stat.S_ISDIR(stat_result.st_mode), "release path must be a directory")
    return path


def release_dir_for_create(release_id: str) -> Path:
    path = release_dir(release_id)
    require(not path.is_symlink(), "release create target must not be a symlink")
    return path


def require_existing_release_dir(release_id: str) -> Path:
    path = release_dir(release_id)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise HelperError("release directory is missing") from exc
    require(not stat.S_ISLNK(stat_result.st_mode), "release directory must not be a symlink")
    require(stat.S_ISDIR(stat_result.st_mode), "release path must be a directory")
    return path


def run(
    argv: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    require(not isinstance(argv, str), "commands must use argv arrays")
    return subprocess.run(
        argv,
        input=input_text,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        timeout=timeout,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_file(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def op_helper_capabilities(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"helper_version": HELPER_VERSION, "operations": sorted(OPERATIONS)}


def op_preflight_disk(args: dict[str, object]) -> dict[str, object]:
    del args
    usage = shutil.disk_usage(REMOTE_PATH)
    return {"free_bytes": usage.free, "total_bytes": usage.total, "used_bytes": usage.used}


def op_preflight_marker(args: dict[str, object]) -> dict[str, object]:
    marker = REMOTE_PATH / MANAGED_MARKER
    require(marker.exists() and not marker.is_symlink(), "managed marker is missing")
    stat_result = marker.stat()
    return {"owner_uid": stat_result.st_uid, "owner": "coder" if stat_result.st_uid == os.getuid() else str(stat_result.st_uid)}


def op_preflight_commands(args: dict[str, object]) -> dict[str, object]:
    del args
    missing = [name for name in REQUIRED_COMMANDS if shutil.which(name) is None]
    return {"ok": not missing, "missing": missing, "helper_version": HELPER_VERSION}


def op_preflight_env(args: dict[str, object]) -> dict[str, object]:
    path = REMOTE_PATH / ".env.vcvm"
    require(path.exists() and not path.is_symlink(), "env file is missing")
    mode = stat.S_IMODE(path.stat().st_mode)
    return {"mode": f"{mode:o}", "path": str(path)}


def op_preflight_manager(args: dict[str, object]) -> dict[str, object]:
    del args
    _validate_manager_bind_sources()
    container = json.loads(run(["docker", "inspect", MANAGER_CONTAINER]).stdout)[0]
    image_ref = str(container["Config"]["Image"])
    image_id = str(container["Image"])
    revision = run(["docker", "image", "inspect", image_id, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"], check=False).stdout.strip()
    revision_available = bool(revision)
    if revision_available:
        require(COMMIT_RE.fullmatch(revision) is not None, "running Manager image revision label is malformed")
    mounts = container.get("Mounts", [])
    volumes = [item.get("Name") for item in mounts if item.get("Type") == "volume"]
    host_config = container.get("HostConfig", {})
    restart_policy = dict(host_config.get("RestartPolicy", {})).get("Name", "")
    network_mode = str(host_config.get("NetworkMode", ""))
    return {
        "container": container["Name"].lstrip("/"),
        "image": image_ref,
        "image_id": image_id,
        "image_digest": image_id.removeprefix("sha256:"),
        "revision": revision,
        "revision_available": revision_available,
        "volume": MANAGER_VOLUME if MANAGER_VOLUME in volumes else "",
        "manager_bind_mounts": manager_bind_mount_receipt(),
        "restart_policy": restart_policy,
        "network_mode": network_mode,
    }


def _running_manager_image_id(*, timeout: float | None = None) -> str:
    container = json.loads(run(["docker", "inspect", MANAGER_CONTAINER], timeout=timeout).stdout)[0]
    return validate_immutable_image(container.get("Image"), "running Manager image id")


def _read_unit_fragment(path: Path, name: str) -> str:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise HelperError(f"unit fragment path cannot be inspected: {name}") from exc
    require(not stat.S_ISLNK(stat_result.st_mode), f"unit fragment path is a symlink: {name}")
    require(stat.S_ISREG(stat_result.st_mode), f"unit fragment path is not a regular file: {name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    try:
        fd = os.open(path, flags)
        opened_stat = os.fstat(fd)
        same_fragment = stat_result.st_dev == opened_stat.st_dev and stat_result.st_ino == opened_stat.st_ino
        require(same_fragment, f"unit fragment changed before read: {name}")
        require(stat.S_ISREG(opened_stat.st_mode), f"unit fragment path changed before read: {name}")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = None
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                require(total <= UNIT_FRAGMENT_READ_MAX_BYTES, f"unit fragment content is too large: {name}")
                chunks.append(chunk)
    except OSError as exc:
        raise HelperError(f"unit fragment path cannot be read safely: {name}") from exc
    finally:
        if fd is not None:
            os.close(fd)
    return b"".join(chunks).decode("utf-8")


def _unit_state(name: str) -> dict[str, object]:
    path_result = run(["systemctl", "--user", "show", name, "-p", "FragmentPath", "--value"])
    path_value = path_result.stdout.strip()
    require(bool(path_value), f"unit fragment path is missing: {name}")
    path = Path(path_value)
    content = _read_unit_fragment(path, name)
    active = run(["systemctl", "--user", "is-active", name], check=False).stdout.strip()
    dropin = release_dropin(name)
    dropin_exists = dropin.exists() and not dropin.is_symlink()
    dropin_content = dropin.read_text(encoding="utf-8") if dropin_exists else ""
    return {
        "unit": name,
        "unit_path": str(path),
        "unit_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "active_state": active,
        "content": content,
        "dropin_path": str(dropin),
        "dropin_exists": dropin_exists,
        "dropin_content": dropin_content,
        "dropin_sha256": hashlib.sha256(dropin_content.encode("utf-8")).hexdigest() if dropin_exists else "",
    }


def _unit_show(name: str) -> dict[str, str]:
    output = run(
        [
            "systemctl",
            "--user",
            "show",
            name,
            "-p",
            "WorkingDirectory",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
        ]
    ).stdout
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _acpx_exec_show() -> dict[str, str]:
    output = run(
        [
            "systemctl",
            "--user",
            "show",
            ACPX_UNIT,
            "-p",
            "ExecStart",
            "-p",
            "ExecMainPID",
        ]
    ).stdout
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _effective_unit_exec_start(*contents: str) -> str:
    current: list[str] = []
    for content in contents:
        for line in content.splitlines():
            if not line.startswith("ExecStart="):
                continue
            value = line.split("=", 1)[1].strip()
            if not value:
                current = []
            else:
                current.append(value)
    require(len(current) == 1, "ACPX unit must resolve to exactly one ExecStart")
    return current[0]


def _exec_start_tokens(value: object) -> list[str]:
    raw = str(value or "").strip()
    require(bool(raw), "ACPX ExecStart is missing")
    if raw.startswith("ExecStart="):
        raw = raw.split("=", 1)[1].strip()
    if raw.startswith("{"):
        require(" argv[]=" in raw and " ; ignore_errors=" in raw, "ACPX effective ExecStart is malformed")
        raw = raw.split(" argv[]=", 1)[1].split(" ; ignore_errors=", 1)[0].strip()
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise HelperError("ACPX ExecStart could not be parsed safely") from exc
    require(tokens, "ACPX ExecStart token list is empty")
    return tokens


def _parse_acpx_worker_exec_start(value: object, release_id: str) -> dict[str, str]:
    tokens = _exec_start_tokens(value)
    release = require_existing_release_dir(release_id)
    require(tokens[0] == str(release / "acpx-venv" / "bin" / "python"), "ACPX ExecStart python path is not release-scoped")
    require(tokens[1:3] == ["-m", "scripts.acpx_worker"], "ACPX ExecStart module is not allowlisted")
    options: dict[str, str] = {}
    index = 3
    while index < len(tokens):
        key = tokens[index]
        require(key.startswith("--") and index + 1 < len(tokens), "ACPX ExecStart options are malformed")
        value_token = tokens[index + 1]
        require(key not in options, f"ACPX ExecStart option is duplicated: {key}")
        options[key] = value_token
        index += 2
    required = {
        "--manager-url",
        "--token-file",
        "--worker-id",
        "--worktree",
        "--permission-policy",
        "--mcp-config",
        "--capability-dir",
        "--acpx",
        "--preflight-interval",
    }
    require(set(options) == required, "ACPX ExecStart option set is not allowlisted")
    require(options["--worktree"] == str(release / "source"), "ACPX ExecStart worktree path mismatch")
    require(
        options["--permission-policy"] == str(release / "acpx-capability" / "permission-policy.json"),
        "ACPX ExecStart permission policy path mismatch",
    )
    require(
        options["--mcp-config"] == str(release / "acpx-capability" / "mcp-config.json"),
        "ACPX ExecStart MCP config path mismatch",
    )
    require(options["--capability-dir"] == str(release / "acpx-capability"), "ACPX ExecStart capability path mismatch")
    require(options["--acpx"] == str(release / "acpx-runtime" / ACPX_DIRECT_CLI), "ACPX ExecStart ACPX path mismatch")
    require(options["--manager-url"].startswith("http://127.0.0.1:"), "ACPX manager URL is not loopback")
    require(Path(options["--token-file"]) == REMOTE_PATH / ".env.acpx.vcvm", "ACPX token file is not allowlisted")
    validate_name(options["--worker-id"], "ACPX worker id")
    require(options["--preflight-interval"].isdigit(), "ACPX preflight interval must be numeric")
    return options


def _validate_running_acpx_process(show: dict[str, str], expected_exec_start: str) -> None:
    raw_pid = str(show.get("ExecMainPID", "0"))
    require(raw_pid.isdigit() and int(raw_pid) > 0, "ACPX running process id is missing")
    path = Path("/proc") / raw_pid / "cmdline"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise HelperError("ACPX running process command line is unavailable") from exc
    require(0 < len(payload) <= 16 * 1024, "ACPX running process command line is invalid")
    actual = [item.decode("utf-8") for item in payload.rstrip(b"\0").split(b"\0")]
    require(actual == _exec_start_tokens(expected_exec_start), "ACPX running process is not release-bound")


def _validate_effective_acpx_binding(show: dict[str, str], release_id: str, *, verify_process: bool) -> dict[str, str]:
    release = require_existing_release_dir(release_id)
    require(show.get("WorkingDirectory") == str(release / "source"), "ACPX WorkingDirectory is not release-bound")
    effective = str(show.get("ExecStart", ""))
    options = _parse_acpx_worker_exec_start(effective, release_id)
    if verify_process:
        _validate_running_acpx_process(show, effective)
    return options


def _build_acpx_worker_exec_start(release_id: str, captured: dict[str, str]) -> str:
    release = require_existing_release_dir(release_id)
    tokens = [
        str(release / "acpx-venv" / "bin" / "python"),
        "-m",
        "scripts.acpx_worker",
        "--manager-url",
        captured["--manager-url"],
        "--token-file",
        captured["--token-file"],
        "--worker-id",
        captured["--worker-id"],
        "--worktree",
        str(release / "source"),
        "--permission-policy",
        str(release / "acpx-capability" / "permission-policy.json"),
        "--mcp-config",
        str(release / "acpx-capability" / "mcp-config.json"),
        "--capability-dir",
        str(release / "acpx-capability"),
        "--acpx",
        str(release / "acpx-runtime" / ACPX_DIRECT_CLI),
        "--preflight-interval",
        captured["--preflight-interval"],
    ]
    return shlex.join(tokens)


def _env_auth_token() -> str:
    env_path = REMOTE_PATH / ".env.vcvm"
    require(env_path.exists() and not env_path.is_symlink(), "Manager env file is missing")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("AUTH_TOKEN="):
            token = line.split("=", 1)[1].strip().strip("'\"")
            require(bool(token), "AUTH_TOKEN is empty")
            return token
    raise HelperError("AUTH_TOKEN is missing")


def _manager_json(path: str) -> dict[str, object]:
    return _manager_json_on_port(LIVE_PORT, path)


def _manager_json_on_port(port: int, path: str, *, timeout: float = 5.0) -> dict[str, object]:
    require(port in {LIVE_PORT, CANDIDATE_PORT}, "Manager probe port is not allowlisted")
    require((path.startswith("/api/") or path == "/openapi.json") and "://" not in path, "Manager probe path is not allowlisted")
    require(timeout > 0, "Manager probe timeout must be positive")
    token = _env_auth_token()
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=min(5.0, timeout)) as response:  # noqa: S310 - fixed loopback URL.
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HelperError("Manager authenticated probe failed") from exc
    require(isinstance(payload, dict), "Manager probe returned non-object JSON")
    return payload


def _acpx_adapter_probe() -> dict[str, object]:
    executable = shutil.which("acpx")
    if executable is None:
        return {"ready": False, "reason_code": "adapter_unavailable"}
    return _acpx_adapter_probe_at(Path(executable))


def _acpx_adapter_probe_at(executable: Path, *, timeout: float | None = None) -> dict[str, object]:
    if not executable.exists() or executable.is_symlink() or not executable.is_file():
        return {"ready": False, "reason_code": "adapter_unavailable"}
    version = run([str(executable), "--version"], check=False, timeout=timeout)
    output = f"{version.stdout}\n{version.stderr}"
    if version.returncode != 0:
        return {"ready": False, "reason_code": "adapter_unavailable"}
    if ACPX_VERSION_RE.search(output) is None:
        return {"ready": False, "reason_code": "version_mismatch"}
    return {"ready": True, "reason_code": "ok"}


def _validate_acpx_executable_path(release_id: str, value: object, *, kind: str) -> Path:
    release = release_dir(release_id)
    if kind == "candidate":
        expected = release / ACPX_BOOTSTRAP_DIR / "node-runtime" / ACPX_DIRECT_CLI
    elif kind == "promoted":
        expected = release / "acpx-runtime" / ACPX_DIRECT_CLI
    else:
        raise HelperError("unknown ACPX executable kind")
    path = Path(str(value))
    require(path == expected, "ACPX executable path is not allowlisted")
    parent = path.parent
    while parent != release.parent:
        require(parent.exists() and parent.is_dir() and not parent.is_symlink(), "ACPX executable parent path is unsafe")
        if parent == release:
            break
        parent = parent.parent
    require(path.exists() and path.is_file() and not path.is_symlink(), "ACPX executable must be a non-symlink file")
    require(os.access(path, os.R_OK | os.X_OK), "ACPX executable must be readable and executable")
    return path


def _acpx_manager_preflights_ready(port: int = LIVE_PORT) -> dict[str, object]:
    return _acpx_manager_preflights_ready_with_timeout(port, timeout=5.0)


def _acpx_manager_preflights_ready_with_timeout(port: int, *, timeout: float) -> dict[str, object]:
    payload = _manager_json_on_port(port, "/api/task-harnesses/acpx/preflights", timeout=min(5.0, timeout))
    agents = payload.get("agents", [])
    require(isinstance(agents, list), "ACPX preflight receipt is malformed")
    sanitized_agents: list[dict[str, object]] = []
    malformed_agents = [item for item in agents if not isinstance(item, dict) or not isinstance(item.get("agent"), str)]
    seen_agents: list[str] = []
    for item in agents:
        if not isinstance(item, dict) or not isinstance(item.get("agent"), str):
            continue
        sanitized = {
            "agent": item["agent"],
            "ready": item.get("ready") is True,
            "state": str(item.get("state") or ""),
            "reason_code": str(item.get("reason_code") or ""),
        }
        sanitized_agents.append(sanitized)
        seen_agents.append(str(item["agent"]))
    duplicate_agents = sorted({agent for agent in seen_agents if seen_agents.count(agent) > 1})
    unexpected_agents = sorted(set(seen_agents) - set(EXPECTED_ACPX_PREFLIGHT_AGENTS))
    failures: list[dict[str, object]] = []
    if malformed_agents:
        failures.append({"agent": "malformed", "ready": False, "state": "malformed", "reason_code": "malformed_agent"})
    for agent in duplicate_agents:
        failures.append({"agent": agent, "ready": False, "state": "duplicate", "reason_code": "duplicate_agent"})
    for agent in unexpected_agents:
        failures.append({"agent": agent, "ready": False, "state": "unexpected", "reason_code": "unexpected_agent"})
    by_agent = {str(item["agent"]): item for item in sanitized_agents}
    for agent in EXPECTED_ACPX_PREFLIGHT_AGENTS:
        item = by_agent.get(agent)
        if not isinstance(item, dict):
            failures.append({"agent": agent, "ready": False, "state": "missing", "reason_code": "missing_agent"})
            continue
        is_ready = item.get("ready") is True and item.get("state") == "ready" and item.get("reason_code") == "ok"
        is_auth_blocked = (
            item.get("ready") is False and item.get("state") == "failed" and item.get("reason_code") == "auth_required"
        )
        if not is_ready and not is_auth_blocked:
            failures.append(item)
    ready_agents = [
        str(item["agent"])
        for item in sanitized_agents
        if item["agent"] in EXPECTED_ACPX_PREFLIGHT_AGENTS
        and item.get("ready") is True
        and item.get("state") == "ready"
        and item.get("reason_code") == "ok"
    ]
    auth_blocked_agents = [
        str(item["agent"])
        for item in sanitized_agents
        if item["agent"] in EXPECTED_ACPX_PREFLIGHT_AGENTS
        and item.get("ready") is False
        and item.get("state") == "failed"
        and item.get("reason_code") == "auth_required"
    ]
    reason_code = "ok"
    if failures:
        first_failure = failures[0]
        reason_code = str(first_failure.get("reason_code") or first_failure.get("state") or "not_ready")
    elif not ready_agents:
        reason_code = "no_ready_agent"
    return {
        "ready": not failures and bool(ready_agents),
        "agents": sanitized_agents,
        "failures": failures,
        "reason_code": reason_code,
        "ready_agents": ready_agents,
        "auth_blocked_agents": auth_blocked_agents,
    }


def _acpx_manager_presence_ready(port: int) -> dict[str, object]:
    return _acpx_manager_presence_ready_with_timeout(port, timeout=5.0)


def _acpx_manager_presence_ready_with_timeout(port: int, *, timeout: float) -> dict[str, object]:
    payload = _manager_json_on_port(port, "/api/task-harnesses/acpx/presence", timeout=min(5.0, timeout))
    ready = payload.get("worker_seen_recently") is True and payload.get("state") == "polling"
    if ready:
        reason_code = "ok"
    elif payload.get("worker_seen_recently") is not True:
        reason_code = "worker_not_seen"
    else:
        reason_code = f"state_{_safe_reason_code(payload.get('state'))}"
    return {
        "ready": ready,
        "reason_code": reason_code,
        "presence": payload,
    }


def _sanitize_acpx_presence(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    sanitized: dict[str, object] = {}
    if "harness" in payload:
        sanitized["harness"] = _safe_reason_code(payload.get("harness"))
    if isinstance(payload.get("worker_seen_recently"), bool):
        sanitized["worker_seen_recently"] = payload["worker_seen_recently"]
    if "state" in payload:
        sanitized["state"] = _safe_reason_code(payload.get("state"))
    if "last_seen_at" in payload:
        sanitized["last_seen_at"] = _safe_reason_code(payload.get("last_seen_at"))
    return sanitized


def _restore_release_dropin(unit: str, capture: dict[str, object], prefix: str) -> None:
    dropin = release_dropin(unit)
    require(not dropin.is_symlink(), f"drop-in file is symlink: {unit}")
    existed = bool(capture.get(f"{prefix}_dropin_exists"))
    content = str(capture.get(f"{prefix}_dropin_content") or "")
    if existed:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        _write_text_mode_0600_atomic(dropin, content)
    else:
        dropin.unlink(missing_ok=True)


def _restore_unit_state(unit: str, desired_state: str) -> None:
    if desired_state == "active":
        run(["systemctl", "--user", "restart", unit])
    else:
        run(["systemctl", "--user", "stop", unit], check=False)


def _remove_acpx_bootstrap_artifacts(capture: dict[str, object]) -> None:
    release_id = str(capture.get("acpx_bootstrap_release_id", ""))
    if release_id:
        release = release_dir(validate_release_id(release_id))
        for child in ("acpx-runtime", "acpx-venv", "acpx-capability", ACPX_BOOTSTRAP_DIR):
            target = release / child
            require(target == release / child, "ACPX cleanup target is not allowlisted")
            _remove_tree_if_present(target, f"ACPX release artifact {child}")
    for path in (
        expected_unit_path(ACPX_UNIT),
        release_dropin(ACPX_UNIT),
        REMOTE_PATH / ".env.acpx.vcvm",
    ):
        require(not path.is_symlink(), f"ACPX restore target must not be a symlink: {path}")
        path.unlink(missing_ok=True)


def _token_mode(path: Path) -> str:
    require(path.exists() and not path.is_symlink(), f"token file missing: {path}")
    return f"{stat.S_IMODE(path.stat().st_mode):o}"


def _read_canonical_worker_key() -> str:
    path = BROWSER_USE_TOKEN_PATH
    require(path == BROWSER_USE_TOKEN_PATH, "worker key path is not allowlisted")
    require(path.exists() and path.is_file(), "canonical worker key is missing")
    require(not path.is_symlink(), "canonical worker key is a symlink")
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "canonical worker key mode mismatch")
    token = path.read_text(encoding="utf-8").strip()
    require(re.fullmatch(r"cbm_worker_[A-Za-z0-9_-]{32,}", token) is not None, "canonical worker key is invalid")
    return token


def _read_browser_use_release_commit_marker(release: Path) -> str:
    try:
        release_stat = release.lstat()
    except FileNotFoundError as exc:
        raise HelperError("Browser-Use release directory is missing") from exc
    require(not stat.S_ISLNK(release_stat.st_mode), "release directory must not be a symlink")
    require(stat.S_ISDIR(release_stat.st_mode), "release path must be a directory")
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    marker_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        release_fd = os.open(str(release), dir_flags)
    except OSError as exc:
        raise HelperError("Browser-Use release directory could not be opened safely") from exc
    try:
        opened_release_stat = os.fstat(release_fd)
        require(
            stat.S_ISDIR(opened_release_stat.st_mode)
            and opened_release_stat.st_dev == release_stat.st_dev
            and opened_release_stat.st_ino == release_stat.st_ino,
            "Browser-Use release directory changed while reading commit marker",
        )
        try:
            marker_stat = os.stat("COMMIT", dir_fd=release_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise HelperError("Browser-Use release commit marker is missing") from exc
        require(not stat.S_ISLNK(marker_stat.st_mode), "Browser-Use release commit marker must not be a symlink")
        require(stat.S_ISREG(marker_stat.st_mode), "Browser-Use release commit marker must be a regular file")
        require(marker_stat.st_size <= 41, "Browser-Use release commit marker is too large")
        try:
            marker_fd = os.open("COMMIT", marker_flags, dir_fd=release_fd)
        except OSError as exc:
            raise HelperError("Browser-Use release commit marker could not be opened safely") from exc
        try:
            opened_marker_stat = os.fstat(marker_fd)
            require(
                stat.S_ISREG(opened_marker_stat.st_mode)
                and opened_marker_stat.st_dev == marker_stat.st_dev
                and opened_marker_stat.st_ino == marker_stat.st_ino,
                "Browser-Use release commit marker changed while reading",
            )
            require(opened_marker_stat.st_size <= 41, "Browser-Use release commit marker is too large")
            marker_bytes = os.read(marker_fd, 42)
        finally:
            os.close(marker_fd)
    finally:
        os.close(release_fd)
    require(len(marker_bytes) <= 41, "Browser-Use release commit marker is too large")
    try:
        marker_text = marker_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HelperError("Browser-Use release commit marker is malformed") from exc
    commit = marker_text.removesuffix("\n")
    require(marker_text in {commit, f"{commit}\n"}, "Browser-Use release commit marker is malformed")
    require(COMMIT_RE.fullmatch(commit) is not None, "Browser-Use release commit marker is malformed")
    return commit


def _browser_use_worktree_commit(working_directory: str) -> str:
    path = Path(working_directory)
    require(path.is_absolute(), "Browser-Use WorkingDirectory must be absolute")
    coder_home = Path.home()
    try:
        path.relative_to(coder_home)
    except ValueError as exc:
        raise HelperError("Browser-Use WorkingDirectory must stay under /home/coder") from exc
    require(not path.is_symlink(), "Browser-Use WorkingDirectory must not be a symlink")

    try:
        relative = path.relative_to(RELEASES_PATH)
    except ValueError:
        relative = None
    if relative is not None:
        require(
            len(relative.parts) == 2 and relative.parts[1] == "source",
            "Browser-Use WorkingDirectory must be a release source",
        )
        release_id = _release_id_for_release_source(path)
        release = require_existing_release_dir(release_id)
        return _read_browser_use_release_commit_marker(release)

    inside = run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], check=False).stdout.strip()
    require(inside == "true", "Browser-Use WorkingDirectory is not a git worktree")
    commit = run(["git", "-C", str(path), "rev-parse", "HEAD"], check=False).stdout.strip()
    require(COMMIT_RE.fullmatch(commit) is not None, "Browser-Use WorkingDirectory commit is missing")
    return commit


def op_preflight_browser_use(args: dict[str, object]) -> dict[str, object]:
    del args
    unit = BROWSER_USE_UNIT
    token_path = BROWSER_USE_TOKEN_PATH
    state = _unit_state(unit)
    show = _unit_show(unit)
    commit = _browser_use_worktree_commit(show.get("WorkingDirectory", ""))
    state.update({"active": state["active_state"] == "active", "token_mode": _token_mode(token_path), "commit": commit})
    return state


def _preflight_acpx_with_adapter_probe(args: dict[str, object], adapter_probe: dict[str, object]) -> dict[str, object]:
    unit = str(args.get("unit", ACPX_UNIT))
    token_path = Path(str(args.get("token_path", REMOTE_PATH / ".env.acpx.vcvm")))
    venv = Path(str(args.get("venv", REMOTE_PATH / ".venv-acpx")))
    state = _unit_state(unit)
    state.update(
        {
            "active": state["active_state"] == "active",
            "token_mode": _token_mode(token_path),
            "venv": venv.exists() and not venv.is_symlink(),
            "adapters_ready": adapter_probe["ready"],
            "adapter_reason_code": adapter_probe["reason_code"],
        }
    )
    return state


def _validate_promoted_acpx_venv_path(release_id: str) -> Path:
    release = require_existing_release_dir(release_id)
    venv = release / "acpx-venv"
    require(venv == release / "acpx-venv", "ACPX venv path is not allowlisted")
    require(
        venv.exists() and venv.is_dir() and not venv.is_symlink(),
        "ACPX venv must be an existing non-symlink release venv",
    )
    return venv


def _release_id_from_acpx_unit_working_directory(working_directory: object) -> str | None:
    path = Path(str(working_directory or ""))
    if not path.is_absolute():
        return None
    try:
        relative = path.relative_to(RELEASES_PATH)
    except ValueError:
        return None
    require(
        len(relative.parts) == 2 and relative.parts[1] == "source",
        "ACPX WorkingDirectory must be a release source",
    )
    return _release_id_for_release_source(path)


def op_preflight_acpx(args: dict[str, object]) -> dict[str, object]:
    unit = str(args.get("unit", ACPX_UNIT))
    if unit == ACPX_UNIT:
        _unit_state(unit)
        show = _unit_show(unit)
        release_id = _release_id_from_acpx_unit_working_directory(show.get("WorkingDirectory", ""))
        if release_id is not None:
            release = release_dir(release_id)
            venv = _validate_promoted_acpx_venv_path(release_id)
            acpx_executable = _validate_acpx_executable_path(
                release_id,
                release / "acpx-runtime" / ACPX_DIRECT_CLI,
                kind="promoted",
            )
            release_args = dict(args)
            release_args["venv"] = str(venv)
            result = _preflight_acpx_with_adapter_probe(release_args, _acpx_adapter_probe_at(acpx_executable))
            result["release_id"] = release_id
            result["acpx_executable"] = str(acpx_executable)
            result["working_directory"] = show.get("WorkingDirectory", "")
            result["fragment_path"] = show.get("FragmentPath", "")
            return result
    return _preflight_acpx_with_adapter_probe(args, _acpx_adapter_probe())


def _capture_acpx_state() -> dict[str, object]:
    try:
        acpx = op_preflight_acpx({})
        acpx["was_absent"] = False
        return acpx
    except HelperError:
        probe = _probe_acpx_state()
        if probe.get("state") != "absent":
            raise
        unit = expected_unit_path(ACPX_UNIT)
        dropin = release_dropin(ACPX_UNIT)
        return {
            "unit_path": str(unit),
            "unit_sha256": "0" * 64,
            "active_state": "absent",
            "dropin_path": str(dropin),
            "dropin_exists": False,
            "dropin_content": "",
            "dropin_sha256": "",
            "was_absent": True,
            "absence": probe,
        }


def op_preflight_receipts(args: dict[str, object]) -> dict[str, object]:
    del args
    return _managed_layout_status()


def op_preflight_tailscale(args: dict[str, object]) -> dict[str, object]:
    del args
    result = run(["tailscale", "serve", "status", "--json"], check=False)
    return {"ok": "18115" in result.stdout or str(LIVE_PORT) in result.stdout}


def acpx_bootstrap_dir(release_id: str) -> Path:
    root = release_dir(release_id) / ACPX_BOOTSTRAP_DIR
    require(root.parent == release_dir(release_id), "ACPX bootstrap path escapes release directory")
    return root


def acpx_candidate_worker_id(release_id: str) -> str:
    return validate_name(f"acpx-candidate-{release_id}", "ACPX worker id")


def _path_ref(path: Path) -> dict[str, object]:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    exists = path.exists() and not path.is_symlink()
    mode = f"{stat.S_IMODE(path.stat().st_mode):o}" if exists else ""
    return {"ref": f"sha256:{digest}", "sha256": digest, "mode": mode}


def _require_bootstrap_path(release_id: str, path: Path, label: str) -> Path:
    root = acpx_bootstrap_dir(release_id)
    require(path == root or root in path.parents, f"{label} path escapes ACPX bootstrap directory")
    current = root
    while True:
        try:
            stat_result = current.lstat()
        except FileNotFoundError:
            pass
        else:
            require(not stat.S_ISLNK(stat_result.st_mode), f"{label} path contains symlink")
            if current != path:
                require(stat.S_ISDIR(stat_result.st_mode), f"{label} parent path is not a directory")
        if current == path:
            break
        try:
            current = current / path.relative_to(current).parts[0]
        except (IndexError, ValueError):
            raise HelperError(f"{label} path escapes ACPX bootstrap directory") from None
    return path


def _require_existing_bootstrap_dir(release_id: str, path: Path, label: str) -> Path:
    path = _require_bootstrap_path(release_id, path, label)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise HelperError(f"{label} is missing") from exc
    require(not stat.S_ISLNK(stat_result.st_mode), f"{label} must not be a symlink")
    require(stat.S_ISDIR(stat_result.st_mode), f"{label} must be a directory")
    return path


def _require_existing_bootstrap_file(release_id: str, path: Path, label: str, *, executable: bool = False) -> Path:
    path = _require_bootstrap_path(release_id, path, label)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise HelperError(f"{label} is missing") from exc
    require(not stat.S_ISLNK(stat_result.st_mode), f"{label} must not be a symlink")
    require(stat.S_ISREG(stat_result.st_mode), f"{label} must be a regular file")
    require(os.access(path, os.R_OK), f"{label} must be readable")
    if executable:
        require(os.access(path, os.X_OK), f"{label} must be executable")
    return path


def _require_bootstrap_json_config(
    release_id: str,
    path: Path,
    label: str,
    expected: dict[str, object],
) -> Path:
    path = _require_existing_bootstrap_file(release_id, path, label)
    mode = stat.S_IMODE(path.lstat().st_mode)
    require(mode == 0o600, f"{label} must use mode 0600")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperError(f"{label} must contain valid JSON") from exc
    require(parsed == expected, f"{label} content mismatch")
    return path


def _release_source_worktree(release_id: str, value: object | None = None) -> Path:
    expected = require_existing_release_dir(release_id) / "source"
    path = expected if value is None else Path(str(value))
    require(path == expected, "ACPX promotion source mismatch")
    require(
        path.exists() and path.is_dir() and not path.is_symlink(),
        "ACPX worktree must be an existing non-symlink release source directory",
    )
    return path


def _release_id_for_release_source(value: object) -> str:
    source = Path(str(value))
    require(source.name == "source", "release source must be the release source directory")
    release = source.parent
    require(release.parent == RELEASES_PATH, "release source is not release-scoped")
    release_id = validate_release_id(release.name)
    _release_source_worktree(release_id, source)
    return release_id


def _acpx_cli_under(runtime_root: Path) -> Path:
    return runtime_root / ACPX_DIRECT_CLI


def _write_acpx_bootstrap_configs(release_id: str, release_source: Path, capability: Path) -> dict[str, Path]:
    require(capability == acpx_bootstrap_dir(release_id) / "capability", "ACPX capability path is not allowlisted")
    require(not capability.is_symlink(), "ACPX capability directory must not be a symlink")
    capability.mkdir(parents=True, mode=0o700, exist_ok=True)
    capability.chmod(0o700)
    policy = _require_bootstrap_path(release_id, capability / "permission-policy.json", "ACPX permission policy")
    mcp = _require_bootstrap_path(release_id, capability / "mcp-config.json", "ACPX MCP config")
    _write_json_mode_0600(policy, {"defaultAction": "deny"})
    _write_json_mode_0600(
        mcp,
        {
            "mcpServers": [
                {
                    "name": "cloakbrowser",
                    "command": str(release_source / "scripts" / "cbm-mcp"),
                    "args": [],
                }
            ]
        },
    )
    return {"policy": policy, "mcp": mcp}


def _validate_acpx_tree_links(path: Path, label: str) -> None:
    require(not path.is_symlink(), f"{label} must not be a symlink")
    require(path.exists() and path.is_dir(), f"{label} is missing or unsafe")
    for item in path.rglob("*"):
        if not item.is_symlink():
            continue
        target = os.readlink(item)
        if os.path.isabs(target):
            allowed_python = (
                path.name == "acpx-venv"
                and item.parent == path / "bin"
                and item.name == "python3"
                and target == "/usr/bin/python3"
            )
            require(allowed_python, f"{label} contains unsafe absolute symlink")
            continue
        allowed_python_alias = (
            path.name == "acpx-venv"
            and item.parent == path / "bin"
            and item.name in {"python", "python3.12"}
            and target == "python3"
        )
        if allowed_python_alias:
            continue
        try:
            (item.parent / target).resolve(strict=True).relative_to(path.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise HelperError(f"{label} contains escaping symlink") from exc


def _require_release_asset_dir(release_id: str, path: Path, label: str) -> Path:
    release = require_existing_release_dir(release_id)
    require(path.parent == release, f"{label} path is not release-scoped")
    _validate_acpx_tree_links(path, label)
    return path


def _tree_receipt(path: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for item in sorted([path, *path.rglob("*")], key=lambda value: str(value.relative_to(path) if value != path else ".")):
        relative = "." if item == path else str(item.relative_to(path))
        item_stat = item.lstat()
        mode = stat.S_IMODE(item_stat.st_mode)
        if stat.S_ISLNK(item_stat.st_mode):
            entries.append({"path": relative, "type": "symlink", "mode": f"{mode:o}", "target": os.readlink(item)})
        elif item.is_dir():
            entries.append({"path": relative, "type": "dir", "mode": f"{mode:o}"})
        else:
            entries.append({"path": relative, "type": "file", "mode": f"{mode:o}", "sha256": file_sha256(item)})
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()
    return {"ref": f"sha256:{digest}", "sha256": digest, "mode": f"{stat.S_IMODE(path.stat().st_mode):o}", "entries": len(entries)}


def _rewrite_acpx_mcp_config(path: Path, source_release: Path, target_release: Path) -> None:
    require(path.exists() and path.is_file() and not path.is_symlink(), "ACPX MCP config is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    old_command = str(source_release / "source" / "scripts" / "cbm-mcp")
    new_command = str(target_release / "source" / "scripts" / "cbm-mcp")

    def rewrite(value: object) -> object:
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if value == old_command:
            return new_command
        return value

    rewritten = rewrite(payload)
    text = json.dumps(rewritten, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    require(old_command not in text and new_command in text, "ACPX MCP config rewrite failed")


def _stage_acpx_tree(source: Path, target: Path, tmp_root: Path, label: str) -> Path:
    require(source.exists() and source.is_dir() and not source.is_symlink(), f"{label} source is missing")
    staged = tmp_root / target.name
    shutil.copytree(source, staged, symlinks=True, copy_function=shutil.copy2)
    require(staged.exists() and staged.is_dir() and not staged.is_symlink(), f"{label} staging failed")
    return staged


def _captured_acpx_source_release(capture: dict[str, object]) -> tuple[str, Path]:
    working_directory = str(capture.get("acpx_working_directory") or "")
    if not working_directory:
        show = _unit_show(ACPX_UNIT)
        working_directory = show.get("WorkingDirectory", "")
    source_release_id = _release_id_from_acpx_unit_working_directory(working_directory)
    require(source_release_id is not None, "captured ACPX WorkingDirectory is not release-scoped")
    source_release = require_existing_release_dir(source_release_id)
    exec_start = str(capture.get("acpx_exec_start") or "")
    if not exec_start:
        exec_start = _effective_unit_exec_start(
            str(capture.get("acpx_unit_content") or ""),
            str(capture.get("acpx_dropin_content") or ""),
        )
    _parse_acpx_worker_exec_start(exec_start, source_release_id)
    return source_release_id, source_release


def op_acpx_stage_runtime(args: dict[str, object]) -> dict[str, object]:
    target_release_id = validate_release_id(args["release_id"])
    target_release = require_existing_release_dir(target_release_id)
    capture = dict(args["capture"])
    source_release_id, source_release = _captured_acpx_source_release(capture)
    require(source_release_id != target_release_id, "ACPX source and target releases must differ")
    for name, label in (
        ("acpx-runtime", "ACPX runtime"),
        ("acpx-venv", "ACPX venv"),
        ("acpx-capability", "ACPX capability"),
    ):
        _require_release_asset_dir(source_release_id, source_release / name, label)
        target = target_release / name
        require(not target.is_symlink(), f"{label} target must not be a symlink")
    mcp_entrypoint = target_release / "source" / "scripts" / "cbm-mcp"
    require(
        mcp_entrypoint.exists() and mcp_entrypoint.is_file() and not mcp_entrypoint.is_symlink() and os.access(mcp_entrypoint, os.X_OK),
        "target ACPX MCP entrypoint is missing or unsafe",
    )
    tmp_root = target_release / ".acpx-stage-runtime.tmp"
    require(not tmp_root.exists() and not tmp_root.is_symlink(), "ACPX staging temp path already exists")
    tmp_root.mkdir(mode=0o700)
    try:
        staged_paths = {
            "runtime": _stage_acpx_tree(source_release / "acpx-runtime", target_release / "acpx-runtime", tmp_root, "ACPX runtime"),
            "venv": _stage_acpx_tree(source_release / "acpx-venv", target_release / "acpx-venv", tmp_root, "ACPX venv"),
            "capability": _stage_acpx_tree(
                source_release / "acpx-capability",
                target_release / "acpx-capability",
                tmp_root,
                "ACPX capability",
            ),
        }
        for staged in staged_paths.values():
            staged.chmod(0o700)
        capability = staged_paths["capability"]
        policy = capability / "permission-policy.json"
        mcp = capability / "mcp-config.json"
        require(policy.exists() and policy.is_file() and not policy.is_symlink(), "ACPX permission policy is missing")
        policy.chmod(0o600)
        _rewrite_acpx_mcp_config(mcp, source_release, target_release)
        for key, label in (("runtime", "ACPX runtime"), ("venv", "ACPX venv"), ("capability", "ACPX capability")):
            _validate_acpx_tree_links(staged_paths[key], label)
        receipts = {key: _tree_receipt(path) for key, path in staged_paths.items()}
        targets = {
            "runtime": target_release / "acpx-runtime",
            "venv": target_release / "acpx-venv",
            "capability": target_release / "acpx-capability",
        }
        existing = {key: path.exists() for key, path in targets.items()}
        require(set(existing.values()) in ({False}, {True}), "ACPX target contains partial staging receipt")
        if all(existing.values()):
            current = {key: _tree_receipt(path) for key, path in targets.items()}
            require(current == receipts, "ACPX target staging receipt mismatch")
        else:
            for key, path in staged_paths.items():
                shutil.move(str(path), str(targets[key]))
        for key, path in targets.items():
            require(_tree_receipt(path) == receipts[key], "ACPX staged receipt verification failed")
    finally:
        _remove_tree_if_present(tmp_root, "ACPX staging temp")
    return {"source_release": source_release_id, "target_release": target_release_id, **receipts}


def _remove_tree_if_present(path: Path, label: str) -> None:
    require(not path.is_symlink(), f"{label} must not be a symlink")
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise HelperError(f"{label} cleanup failed") from exc
    require(not path.exists(), f"{label} cleanup failed")


def _local_acpx_binary() -> Path:
    found = shutil.which("acpx")
    if found:
        return Path(found)
    return REMOTE_PATH / ".acpx-runtime" / "node_modules" / ".bin" / "acpx"


def _probe_acpx_state() -> dict[str, object]:
    unit = expected_unit_path(ACPX_UNIT)
    key = REMOTE_PATH / ".env.acpx.vcvm"
    venv_path = REMOTE_PATH / ".venv-acpx"
    capability = REMOTE_PATH / "acpx-capabilities"
    binary = _local_acpx_binary()
    paths = {
        "binary": binary,
        "unit": unit,
        "key": key,
        "venv": venv_path,
        "capability": capability,
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing and set(missing) != set(ACPX_ABSENCE_COMPONENTS):
        return {"state": "blocking", "missing": missing, "reason_code": "misconfigured"}
    if key.exists() and (key.is_symlink() or stat.S_IMODE(key.stat().st_mode) != 0o600):
        return {"state": "blocking", "missing": missing, "reason_code": "auth_failed"}
    if unit.exists():
        active = run(["systemctl", "--user", "is-active", unit.name], check=False).stdout.strip()
        if active not in {"active", "inactive"}:
            return {"state": "blocking", "missing": missing, "reason_code": "misconfigured"}
    if binary.exists():
        probe = _acpx_adapter_probe()
        if probe["reason_code"] == "version_mismatch":
            return {"state": "blocking", "missing": missing, "reason_code": "version_mismatch"}
    if missing:
        return {"state": "absent", "missing": missing, "reason_code": "missing_runtime"}
    adapter = _acpx_adapter_probe()
    if adapter["ready"] is not True:
        return {"state": "blocking", "missing": [], "reason_code": adapter["reason_code"]}
    return {"state": "ready", "missing": [], "reason_code": "ok"}


def op_bootstrap_acpx_probe(args: dict[str, object]) -> dict[str, object]:
    validate_release_id(args["release_id"])
    return _probe_acpx_state()


def op_release_prepare(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    expected_commit = validate_commit(args["commit"])
    expected_archive = validate_sha256(args["archive_sha256"])
    _ensure_managed_layout()
    path = release_dir(release_id)
    if path.exists():
        manifest = json_file(path / "manifest.json")
        require(manifest.get("release_id") == release_id, "existing release id mismatch")
        require(manifest.get("source", {}).get("commit") == expected_commit, "existing release commit mismatch")
        require(manifest.get("source", {}).get("archive_sha256") == expected_archive, "existing release archive mismatch")
        return {"exists": True, "release_id": release_id}
    path.mkdir(parents=False, mode=MANAGED_LAYOUT_MODE)
    _validate_managed_dir(RELEASES_PATH, "releases")
    require(path.exists() and path.is_dir() and not path.is_symlink(), "release directory was not created safely")
    require(path.stat().st_uid == os.getuid(), "release directory owner mismatch")
    require(stat.S_IMODE(path.stat().st_mode) == MANAGED_LAYOUT_MODE, "release directory mode mismatch")
    return {"exists": False, "release_id": release_id}


def op_release_verify_archive(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    expected = validate_sha256(args["archive_sha256"])
    archive = release_dir(release_id) / "source.tar"
    digest = file_sha256(archive)
    require(digest == expected, "archive hash mismatch")
    return {"sha256": digest}


def op_release_extract(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    path = release_dir(release_id)
    source_path = path / "source"
    require(not source_path.exists(), "release source already extracted")
    archive = path / "source.tar"
    members = run(["tar", "-tf", str(archive)]).stdout.splitlines()
    for member in members:
        require(member.startswith("source/"), "archive contains path outside source prefix")
        require(".." not in Path(member).parts, "archive contains traversal")
    run(["tar", "-xf", str(archive), "-C", str(path)])
    return {"source_path": str(source_path)}


def op_release_write_marker(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    commit = validate_commit(args["commit"])
    marker = release_dir(release_id) / "COMMIT"
    require(not marker.exists(), "commit marker already exists")
    marker.write_text(commit + "\n", encoding="utf-8")
    return {"commit_marker": commit}


def op_build_image(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    commit = validate_commit(args["commit"])
    source = release_dir(release_id) / "source"
    tag = f"cloakbrowser-manager:{commit}"
    run(["docker", "build", "--label", f"org.opencontainers.image.revision={commit}", "-t", tag, str(source)])
    image_id = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    digest = image_id.removeprefix("sha256:")
    validate_sha256(digest)
    revision = run(["docker", "image", "inspect", f"sha256:{digest}", "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"]).stdout.strip()
    require(revision == commit, "image OCI revision label mismatch")
    return {"image": tag, "image_id": f"sha256:{digest}", "image_digest": digest, "image_ref": f"sha256:{digest}", "revision": commit}


def _backup_receipt(volume: str, revision: str, kind: str) -> dict[str, object]:
    validate_name(volume, "volume")
    commit = validate_commit(revision)
    receipt_id = f"{kind}-{commit[:12]}"
    validate_name(receipt_id, "receipt id")
    path = REMOTE_PATH / "backups" / f"{receipt_id}.tar"
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/data:ro",
            "-v",
            f"{path.parent}:/backup",
            "alpine",
            "tar",
            "-cf",
            f"/backup/{path.name}",
            "-C",
            "/data",
            ".",
        ]
    )
    digest = file_sha256(path)
    return {
        "receipt_id": receipt_id,
        "sha256": digest,
        "volume": volume,
        "source_revision": commit,
        "compatible": True,
        "path": str(path),
    }


def _validate_backup(payload: dict[str, object]) -> None:
    receipt_id = validate_backup_receipt_id(payload.get("receipt_id"))
    expected_sha = validate_sha256(payload.get("sha256"))
    require(payload.get("volume") == MANAGER_VOLUME, "backup volume mismatch")
    validate_commit(payload.get("source_revision"))
    require(payload.get("compatible") is True, "backup receipt is incompatible")
    path = Path(str(payload.get("path", "")))
    require(path == REMOTE_PATH / "backups" / f"{receipt_id}.tar", "backup path must match receipt id under managed backups")
    require(path.exists() and path.is_file() and not path.is_symlink(), "backup path must be regular non-symlink file")
    require(file_sha256(path) == expected_sha, "backup sha256 mismatch")


def op_backup_live(args: dict[str, object]) -> dict[str, object]:
    return _backup_receipt(MANAGER_VOLUME, str(args["commit"]), "backup-live")


def op_backup_final_stopped(args: dict[str, object]) -> dict[str, object]:
    return _backup_receipt(MANAGER_VOLUME, str(args["commit"]), "backup-final")


def op_candidate_clone(args: dict[str, object]) -> dict[str, object]:
    backup = dict(args["backup"])
    _validate_backup(backup)
    release_id = validate_release_id(args["release_id"])
    candidate_volume = validate_name(f"{MANAGER_VOLUME}-candidate-{release_id}", "candidate volume")
    run(["docker", "volume", "create", candidate_volume])
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{candidate_volume}:/data",
            "-v",
            f"{Path(str(backup['path'])).parent}:/backup:ro",
            "alpine",
            "tar",
            "-xf",
            f"/backup/{Path(str(backup['path'])).name}",
            "-C",
            "/data",
        ]
    )
    return {"volume": candidate_volume, "backup_receipt_id": backup["receipt_id"]}


def op_candidate_start(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    image_ref = str(args["image_ref"])
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_ref) is not None, "candidate must start by immutable image id")
    run(["docker", "image", "inspect", image_ref])
    volume = validate_name(args["volume"], "candidate volume")
    container = f"cloakbrowser-manager-candidate-{release_id}"
    run(_manager_docker_run_args(container=container, host_port=CANDIDATE_PORT, volume=volume, image_ref=image_ref))
    return {"container": container, "port": CANDIDATE_PORT, "container_port": MANAGER_CONTAINER_PORT, "manager_bind_mounts": manager_bind_mount_receipt(), "restart_policy": MANAGER_RESTART_POLICY}


def _candidate_readiness_error(
    *,
    attempt_count: int,
    elapsed_seconds: float,
    deadline_seconds: float,
    reason: str,
    detail: str = "",
) -> HelperError:
    message = (
        "candidate readiness failed: "
        f"attempt_count={attempt_count} "
        f"elapsed_seconds={elapsed_seconds:.2f} "
        f"deadline_seconds={deadline_seconds:g} "
        f"reason={reason}"
    )
    if detail:
        message = f"{message} detail={detail}"
    return HelperError(message)


def _safe_reason_code(value: object) -> str:
    reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return reason[:80] or "unknown"


def _acpx_candidate_readiness_error(
    *,
    attempt_count: int,
    elapsed_seconds: float,
    deadline_seconds: float,
    reason: str,
) -> HelperError:
    return HelperError(
        "ACPX candidate readiness failed: "
        f"attempt_count={attempt_count} "
        f"elapsed_seconds={elapsed_seconds:.2f} "
        f"deadline_seconds={deadline_seconds:g} "
        f"reason={_safe_reason_code(reason)}"
    )


def _acpx_promoted_readiness_error(
    *,
    attempt_count: int,
    elapsed_seconds: float,
    deadline_seconds: float,
    reason: str,
) -> HelperError:
    return HelperError(
        "ACPX promoted readiness failed: "
        f"attempt_count={attempt_count} "
        f"elapsed_seconds={elapsed_seconds:.2f} "
        f"deadline_seconds={deadline_seconds:g} "
        f"reason={_safe_reason_code(reason)}"
    )


def _acpx_probe_timeout(started: float, deadline_seconds: float, *, max_seconds: float = CANDIDATE_PROBE_TIMEOUT_SECONDS) -> float:
    remaining = deadline_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("ACPX candidate readiness deadline exhausted")
    return min(max_seconds, remaining)


def _candidate_remaining_seconds(started: float, deadline_seconds: float) -> float:
    return deadline_seconds - (time.monotonic() - started)


def _candidate_probe_timeout(started: float, deadline_seconds: float) -> float:
    remaining = _candidate_remaining_seconds(started, deadline_seconds)
    if remaining <= 0:
        raise TimeoutError("candidate readiness deadline exhausted")
    return min(CANDIDATE_PROBE_TIMEOUT_SECONDS, remaining)


def _restore_manager_probe_timeout(started: float, deadline_seconds: float) -> float:
    remaining = deadline_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("restore Manager verification deadline exhausted")
    return min(CANDIDATE_PROBE_TIMEOUT_SECONDS, remaining)


def _format_timeout_seconds(value: float) -> str:
    return f"{max(0.001, value):.3f}".rstrip("0").rstrip(".")


def _candidate_curl_args(path: str, timeout: float) -> list[str]:
    connect_timeout = min(CANDIDATE_CURL_CONNECT_TIMEOUT_SECONDS, timeout)
    return [
        "curl",
        "-fsS",
        "--connect-timeout",
        _format_timeout_seconds(connect_timeout),
        "--max-time",
        _format_timeout_seconds(timeout),
        f"http://127.0.0.1:{CANDIDATE_PORT}{path}",
    ]


def _candidate_curl_json(path: str, timeout: float) -> dict[str, object] | list[object]:
    result = run(_candidate_curl_args(path, timeout), check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"curl_{result.returncode}")
    return json.loads(result.stdout)


def _candidate_authenticated_json(path: str, timeout: float) -> dict[str, object] | list[object]:
    require(path.startswith("/api/") and "://" not in path, "candidate authenticated probe path is not allowlisted")
    token = _env_auth_token()
    request = Request(
        f"http://127.0.0.1:{CANDIDATE_PORT}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed loopback URL.
        return json.loads(response.read().decode("utf-8"))


def _candidate_readiness_once(commit: str, container: str, started: float, deadline_seconds: float) -> dict[str, object]:
    health_timeout = _candidate_probe_timeout(started, deadline_seconds)
    health = run(_candidate_curl_args("/health", health_timeout), check=False, timeout=health_timeout)
    if health.returncode != 0:
        raise RuntimeError(f"health_curl_{health.returncode}")
    status_timeout = _candidate_probe_timeout(started, deadline_seconds)
    status = _candidate_curl_json("/api/auth/status", status_timeout)
    require(isinstance(status, dict), "candidate auth status returned non-object JSON")
    auth_required = bool(status.get("auth_required"))
    access_control_enabled = bool(status.get("access_control_enabled"))
    if not auth_required:
        raise _candidate_readiness_error(
            attempt_count=1,
            elapsed_seconds=0.0,
            deadline_seconds=deadline_seconds,
            reason="auth_required",
        )
    if not access_control_enabled:
        raise _candidate_readiness_error(
            attempt_count=1,
            elapsed_seconds=0.0,
            deadline_seconds=deadline_seconds,
            reason="access_control_enabled",
        )
    migrations_timeout = _candidate_probe_timeout(started, deadline_seconds)
    migrations = _candidate_authenticated_json("/api/admin/migrations", migrations_timeout)
    require(isinstance(migrations, list), "candidate migrations returned non-array JSON")
    migration_set_exact = sorted(migrations) == sorted(EXPECTED_MIGRATIONS)
    if not migration_set_exact:
        raise _candidate_readiness_error(
            attempt_count=1,
            elapsed_seconds=0.0,
            deadline_seconds=deadline_seconds,
            reason="migration_set_exact",
        )
    revision_timeout = _candidate_probe_timeout(started, deadline_seconds)
    revision = run(
        ["docker", "inspect", container, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"],
        check=False,
        timeout=revision_timeout,
    ).stdout.strip()
    if revision != commit:
        raise _candidate_readiness_error(
            attempt_count=1,
            elapsed_seconds=0.0,
            deadline_seconds=deadline_seconds,
            reason="revision_mismatch",
        )
    return {
        "health": True,
        "auth_required": auth_required,
        "access_control_enabled": access_control_enabled,
        "migrations": migrations,
        "revision": revision,
        "expected_migrations": list(EXPECTED_MIGRATIONS),
        "migration_set_exact": migration_set_exact,
    }


def op_candidate_verify(args: dict[str, object]) -> dict[str, object]:
    commit = validate_commit(args["commit"])
    container = validate_name(args["container"], "candidate container")
    deadline_seconds = float(CANDIDATE_READINESS_TIMEOUT_SECONDS)
    interval_seconds = float(CANDIDATE_READINESS_POLL_INTERVAL_SECONDS)
    require(deadline_seconds > 0, "candidate readiness deadline must be positive")
    require(interval_seconds > 0, "candidate readiness poll interval must be positive")
    started = time.monotonic()
    attempt_count = 0
    last_reason = "not_started"
    while True:
        attempt_count += 1
        try:
            payload = _candidate_readiness_once(commit, container, started, deadline_seconds)
            elapsed = time.monotonic() - started
            payload.update(
                {
                    "attempt_count": attempt_count,
                    "elapsed_seconds": round(elapsed, 2),
                    "deadline_seconds": deadline_seconds,
                    "readiness_reason": "ready",
                }
            )
            return payload
        except HelperError as exc:
            if str(exc).startswith("candidate readiness failed:"):
                elapsed = time.monotonic() - started
                raise _candidate_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=elapsed,
                    deadline_seconds=deadline_seconds,
                    reason=str(exc).split("reason=", 1)[1].split(" ", 1)[0],
                ) from exc
            raise
        except RuntimeError as exc:
            last_reason = str(exc) or exc.__class__.__name__
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError, TimeoutError, URLError) as exc:
            last_reason = exc.__class__.__name__
        elapsed = time.monotonic() - started
        if elapsed >= deadline_seconds:
            raise _candidate_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))


def op_bootstrap_acpx_install(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    commit = validate_commit(args["commit"])
    expected_node = validate_sha256(args["node_lock_sha256"])
    expected_python = validate_sha256(args["python_lock_sha256"])
    release = require_existing_release_dir(release_id)
    source = release / "source"
    marker = release / "COMMIT"
    require(marker.read_text(encoding="utf-8").strip() == commit, "release commit marker mismatch")
    node_lock = source / ACPX_NODE_LOCK
    python_lock = source / ACPX_PYTHON_LOCK
    require(file_sha256(node_lock) == expected_node, "ACPX node lock digest mismatch")
    require(file_sha256(python_lock) == expected_python, "ACPX python lock digest mismatch")
    root = acpx_bootstrap_dir(release_id)
    node_root = _require_bootstrap_path(release_id, root / "node-runtime", "ACPX node runtime")
    venv_path = _require_bootstrap_path(release_id, root / "venv", "ACPX venv")
    _remove_tree_if_present(node_root, "ACPX node runtime")
    _remove_tree_if_present(venv_path, "ACPX venv")
    shutil.copytree(source / "deploy" / "acpx-runtime", node_root, symlinks=False)
    run(["npm", "ci", "--omit=dev", "--ignore-scripts", "--audit=false", "--fund=false"], cwd=node_root)
    run(["python3", "-m", "venv", str(venv_path)])
    run(["uv", "pip", "sync", "--python", str(venv_path / "bin" / "python"), str(python_lock)])
    acpx = _acpx_cli_under(node_root)
    acpx_version = run([str(acpx), "--version"]).stdout.strip()
    require(acpx_version == "0.12.1", "ACPX installed version mismatch")
    python_version = run([str(venv_path / "bin" / "python"), "--version"]).stdout.strip()
    return {
        "node": {"version": run(["node", "--version"]).stdout.strip()},
        "python": {"version": python_version},
        "acpx": {"version": acpx_version},
        "sdk": {"version": "1.2.1"},
        "mcp": {"version": "1.28.1"},
        "playwright": {"version": "1.61.0"},
        "runtime": {
            "node_root": _path_ref(node_root),
            "venv": _path_ref(venv_path),
            "acpx_executable": str(acpx),
        },
        "lock_digests": {"node": expected_node, "python": expected_python},
    }


def op_bootstrap_acpx_provision_candidate(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    validate_commit(args["commit"])
    require(int(args["manager_port"]) == CANDIDATE_PORT, "candidate ACPX worker must bind to candidate Manager port")
    runtime = dict(args["runtime"])
    release_source = _release_source_worktree(release_id)
    root = acpx_bootstrap_dir(release_id)
    key = _require_bootstrap_path(release_id, root / "candidate.worker.key", "ACPX candidate key")
    unit = _require_bootstrap_path(release_id, root / f"{acpx_candidate_worker_id(release_id)}.service", "ACPX candidate unit")
    capability = _require_bootstrap_path(release_id, root / "capability", "ACPX candidate capability")
    config_paths = _write_acpx_bootstrap_configs(release_id, release_source, capability)
    key.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _write_text_mode_0600_atomic(key, _read_canonical_worker_key() + "\n")
    manager_url = f"http://127.0.0.1:{CANDIDATE_PORT}"
    content = (
        "[Unit]\nDescription=CloakBrowser candidate ACPX worker\n"
        "[Service]\n"
        f"Environment=PATH={ACPX_SYSTEMD_PATH}\n"
        f"WorkingDirectory={release_source}\n"
        f"ExecStart={root / 'venv' / 'bin' / 'python'} -m scripts.acpx_worker --manager-url {manager_url} "
        f"--token-file {key} --worker-id {acpx_candidate_worker_id(release_id)} "
        f"--worktree {release_source} "
        f"--permission-policy {config_paths['policy']} --mcp-config {config_paths['mcp']} "
        f"--capability-dir {capability} --acpx {_acpx_cli_under(root / 'node-runtime')} "
        f"--preflight-interval {ACPX_CANDIDATE_PREFLIGHT_INTERVAL_SECONDS}\n"
    )
    _write_text_mode_0600_atomic(unit, content)
    return {
        "worker_id": acpx_candidate_worker_id(release_id),
        "manager_url": manager_url,
        "key": _path_ref(key),
        "unit": _path_ref(unit),
        "capability": _path_ref(capability),
        "permission_policy": _path_ref(config_paths["policy"]),
        "mcp_config": _path_ref(config_paths["mcp"]),
        "venv": runtime.get("venv", _path_ref(root / "venv")),
        "credential_reused": True,
        "credential_source": "browser_use_worker_key",
    }


def op_bootstrap_acpx_start_candidate(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    worker_id = validate_name(args["worker_id"], "ACPX worker id")
    require(worker_id == acpx_candidate_worker_id(release_id), "unexpected ACPX worker id")
    require_existing_release_dir(release_id)
    unit = _require_existing_bootstrap_file(
        release_id,
        acpx_bootstrap_dir(release_id) / f"{worker_id}.service",
        "ACPX candidate unit",
    )
    expected = expected_unit_path(f"{worker_id}.service")
    if expected.exists():
        expected.unlink()
    expected.symlink_to(unit)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "restart", f"{worker_id}.service"])
    return {"active": True, "worker_id": worker_id}


def op_bootstrap_acpx_verify_candidate(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    worker_id = validate_name(args["worker_id"], "ACPX worker id")
    require(worker_id == acpx_candidate_worker_id(release_id), "unexpected ACPX worker id")
    require(int(args["manager_port"]) == CANDIDATE_PORT, "candidate ACPX readiness must target candidate Manager port")
    acpx_executable = _validate_acpx_executable_path(release_id, args["acpx_executable"], kind="candidate")
    deadline_seconds = float(CANDIDATE_READINESS_TIMEOUT_SECONDS)
    interval_seconds = float(CANDIDATE_READINESS_POLL_INTERVAL_SECONDS)
    require(deadline_seconds > 0, "ACPX candidate readiness deadline must be positive")
    require(interval_seconds > 0, "ACPX candidate readiness poll interval must be positive")
    started = time.monotonic()
    attempt_count = 0
    last_reason = "not_started"
    last_components: dict[str, object] = {}
    last_presence: dict[str, object] = {}
    last_agent_preflights: list[object] = []
    cached_adapter: dict[str, object] | None = None
    while True:
        elapsed = time.monotonic() - started
        if attempt_count > 0 and elapsed >= deadline_seconds:
            raise _acpx_candidate_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        attempt_count += 1
        if cached_adapter is None:
            try:
                adapter = _acpx_adapter_probe_at(
                    acpx_executable,
                    timeout=_acpx_probe_timeout(started, deadline_seconds),
                )
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, TimeoutError) as exc:
                last_reason = f"adapter_{exc.__class__.__name__}"
                elapsed = time.monotonic() - started
                if elapsed >= deadline_seconds:
                    raise _acpx_candidate_readiness_error(
                        attempt_count=attempt_count,
                        elapsed_seconds=elapsed,
                        deadline_seconds=deadline_seconds,
                        reason=last_reason,
                    ) from exc
                time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                continue
            if adapter["ready"] is True:
                cached_adapter = adapter
        else:
            adapter = cached_adapter
        if adapter["ready"] is not True:
            last_reason = f"adapter_{adapter.get('reason_code', 'adapter_unavailable')}"
            if adapter.get("reason_code") in {"adapter_unavailable", "version_mismatch"}:
                raise _acpx_candidate_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=time.monotonic() - started,
                    deadline_seconds=deadline_seconds,
                    reason=str(adapter.get("reason_code", "adapter_unavailable")),
                )
            elapsed = time.monotonic() - started
            if elapsed >= deadline_seconds:
                raise _acpx_candidate_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=elapsed,
                    deadline_seconds=deadline_seconds,
                    reason=last_reason,
                )
            time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
            continue
        try:
            active = run(
                ["systemctl", "--user", "is-active", f"{worker_id}.service"],
                check=False,
                timeout=_acpx_probe_timeout(started, deadline_seconds),
            ).stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, TimeoutError) as exc:
            last_reason = f"systemctl_{exc.__class__.__name__}"
            elapsed = time.monotonic() - started
            if elapsed >= deadline_seconds:
                raise _acpx_candidate_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=elapsed,
                    deadline_seconds=deadline_seconds,
                    reason=last_reason,
                ) from exc
            time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
            continue
        unit_ready = active == "active"
        last_components = {
            "unit": {"ready": unit_ready, "active_state": _safe_reason_code(active)},
            "adapter": {"ready": True, "reason_code": adapter["reason_code"]},
        }
        if active in {"failed", "inactive"}:
            raise _acpx_candidate_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=time.monotonic() - started,
                deadline_seconds=deadline_seconds,
                reason=f"unit_{active}",
            )
        if not unit_ready:
            last_reason = f"unit_{_safe_reason_code(active)}"
        else:
            try:
                presence = _acpx_manager_presence_ready_with_timeout(
                    CANDIDATE_PORT,
                    timeout=_acpx_probe_timeout(started, deadline_seconds, max_seconds=5.0),
                )
            except (HelperError, OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
                last_reason = f"manager_{exc.__class__.__name__}"
                elapsed = time.monotonic() - started
                if elapsed >= deadline_seconds:
                    raise _acpx_candidate_readiness_error(
                        attempt_count=attempt_count,
                        elapsed_seconds=elapsed,
                        deadline_seconds=deadline_seconds,
                        reason=last_reason,
                    ) from exc
                time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                continue
            last_presence = _sanitize_acpx_presence(presence.get("presence"))
            last_components["presence"] = {
                "ready": presence["ready"] is True,
                "reason_code": presence["reason_code"],
            }
            if presence["ready"] is not True:
                last_reason = f"presence_{presence['reason_code']}"
            else:
                try:
                    manager_preflights = _acpx_manager_preflights_ready_with_timeout(
                        CANDIDATE_PORT,
                        timeout=_acpx_probe_timeout(started, deadline_seconds, max_seconds=5.0),
                    )
                except (HelperError, OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
                    last_reason = f"manager_{exc.__class__.__name__}"
                    elapsed = time.monotonic() - started
                    if elapsed >= deadline_seconds:
                        raise _acpx_candidate_readiness_error(
                            attempt_count=attempt_count,
                            elapsed_seconds=elapsed,
                            deadline_seconds=deadline_seconds,
                            reason=last_reason,
                        ) from exc
                    time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                    continue
                last_agent_preflights = list(manager_preflights["agents"]) if isinstance(manager_preflights.get("agents"), list) else []
                last_components["manager_preflights"] = {
                    "ready": manager_preflights["ready"] is True,
                    "reason_code": manager_preflights["reason_code"],
                    "ready_agents": manager_preflights.get("ready_agents", []),
                    "auth_blocked_agents": manager_preflights.get("auth_blocked_agents", []),
                }
                if manager_preflights["ready"] is not True:
                    last_reason = f"manager_preflight_{manager_preflights['reason_code']}"
                else:
                    try:
                        final_active = run(
                            ["systemctl", "--user", "is-active", f"{worker_id}.service"],
                            check=False,
                            timeout=_acpx_probe_timeout(started, deadline_seconds),
                        ).stdout.strip()
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, TimeoutError) as exc:
                        last_reason = f"systemctl_{exc.__class__.__name__}"
                        elapsed = time.monotonic() - started
                        if elapsed >= deadline_seconds:
                            raise _acpx_candidate_readiness_error(
                                attempt_count=attempt_count,
                                elapsed_seconds=elapsed,
                                deadline_seconds=deadline_seconds,
                                reason=last_reason,
                            ) from exc
                        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                        continue
                    last_components["unit"] = {"ready": final_active == "active", "active_state": _safe_reason_code(final_active)}
                    if final_active in {"failed", "inactive"}:
                        raise _acpx_candidate_readiness_error(
                            attempt_count=attempt_count,
                            elapsed_seconds=time.monotonic() - started,
                            deadline_seconds=deadline_seconds,
                            reason=f"unit_{final_active}",
                        )
                    if final_active != "active":
                        last_reason = f"unit_{_safe_reason_code(final_active)}"
                        elapsed = time.monotonic() - started
                        if elapsed >= deadline_seconds:
                            raise _acpx_candidate_readiness_error(
                                attempt_count=attempt_count,
                                elapsed_seconds=elapsed,
                                deadline_seconds=deadline_seconds,
                                reason=last_reason,
                            )
                        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                        continue
                    elapsed = time.monotonic() - started
                    return {
                        "worker_id": worker_id,
                        "present": True,
                        "adapters_ready": True,
                        "presence": last_presence,
                        "agent_preflights": last_agent_preflights,
                        "components": last_components,
                        "attempt_count": attempt_count,
                        "elapsed_seconds": round(elapsed, 2),
                        "deadline_seconds": deadline_seconds,
                        "readiness_reason": "ready",
                        "acpx_executable": _path_ref(acpx_executable),
                    }
        elapsed = time.monotonic() - started
        if elapsed >= deadline_seconds:
            raise _acpx_candidate_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))


def _systemd_show_for_readiness(unit: str, *, timeout: float) -> dict[str, str]:
    output = run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "WorkingDirectory",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
        ],
        timeout=timeout,
    ).stdout
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _promoted_acpx_readiness(
    *,
    worker_id: str,
    release_source: Path,
    acpx_executable: Path,
) -> dict[str, object]:
    deadline_seconds = float(ACPX_PROMOTED_READINESS_TIMEOUT_SECONDS)
    interval_seconds = float(CANDIDATE_READINESS_POLL_INTERVAL_SECONDS)
    require(
        deadline_seconds > 0 and deadline_seconds <= CANDIDATE_READINESS_TIMEOUT_SECONDS,
        "ACPX promoted readiness deadline must not exceed candidate readiness deadline",
    )
    require(interval_seconds > 0, "ACPX promoted readiness poll interval must be positive")
    started = time.monotonic()
    attempt_count = 0
    last_reason = "not_started"
    last_presence: dict[str, object] = {}
    last_agent_preflights: list[object] = []
    last_components: dict[str, object] = {}
    cached_adapter: dict[str, object] | None = None
    release_source_text = str(release_source)
    while True:
        elapsed = time.monotonic() - started
        if attempt_count > 0 and elapsed >= deadline_seconds:
            raise _acpx_promoted_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        attempt_count += 1
        if cached_adapter is None:
            try:
                adapter = _acpx_adapter_probe_at(
                    acpx_executable,
                    timeout=_acpx_probe_timeout(started, deadline_seconds),
                )
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, TimeoutError) as exc:
                last_reason = f"adapter_{exc.__class__.__name__}"
                elapsed = time.monotonic() - started
                if elapsed >= deadline_seconds:
                    raise _acpx_promoted_readiness_error(
                        attempt_count=attempt_count,
                        elapsed_seconds=elapsed,
                        deadline_seconds=deadline_seconds,
                        reason=last_reason,
                    ) from exc
                time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                continue
            if adapter["ready"] is True:
                cached_adapter = adapter
        else:
            adapter = cached_adapter
        if adapter["ready"] is not True:
            last_reason = f"adapter_{adapter.get('reason_code', 'adapter_unavailable')}"
            if adapter.get("reason_code") in {"adapter_unavailable", "version_mismatch"}:
                raise _acpx_promoted_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=time.monotonic() - started,
                    deadline_seconds=deadline_seconds,
                    reason=str(adapter.get("reason_code", "adapter_unavailable")),
                )
            elapsed = time.monotonic() - started
            if elapsed >= deadline_seconds:
                raise _acpx_promoted_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=elapsed,
                    deadline_seconds=deadline_seconds,
                    reason=last_reason,
                )
            time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
            continue
        try:
            active = run(
                ["systemctl", "--user", "is-active", ACPX_UNIT],
                check=False,
                timeout=_acpx_probe_timeout(started, deadline_seconds),
            ).stdout.strip()
            show = _systemd_show_for_readiness(
                ACPX_UNIT,
                timeout=_acpx_probe_timeout(started, deadline_seconds),
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError, TimeoutError) as exc:
            last_reason = f"systemctl_{exc.__class__.__name__}"
            elapsed = time.monotonic() - started
            if elapsed >= deadline_seconds:
                raise _acpx_promoted_readiness_error(
                    attempt_count=attempt_count,
                    elapsed_seconds=elapsed,
                    deadline_seconds=deadline_seconds,
                    reason=last_reason,
                ) from exc
            time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
            continue
        bound = show.get("WorkingDirectory") == release_source_text
        active_ready = active == "active" and show.get("ActiveState", active) == "active"
        last_components = {
            "unit": {"ready": active_ready, "active_state": _safe_reason_code(active)},
            "adapter": {"ready": True, "reason_code": adapter["reason_code"]},
            "binding": {"ready": bound, "reason_code": "ok" if bound else "worktree_mismatch"},
        }
        if active in {"failed", "inactive"}:
            raise _acpx_promoted_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=time.monotonic() - started,
                deadline_seconds=deadline_seconds,
                reason=f"unit_{active}",
            )
        if not active_ready:
            last_reason = f"unit_{_safe_reason_code(active or show.get('ActiveState'))}"
        elif not bound:
            last_reason = "worktree_mismatch"
        else:
            try:
                presence = _acpx_manager_presence_ready_with_timeout(
                    LIVE_PORT,
                    timeout=_acpx_probe_timeout(started, deadline_seconds, max_seconds=5.0),
                )
            except (HelperError, OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
                last_reason = f"manager_{exc.__class__.__name__}"
                elapsed = time.monotonic() - started
                if elapsed >= deadline_seconds:
                    raise _acpx_promoted_readiness_error(
                        attempt_count=attempt_count,
                        elapsed_seconds=elapsed,
                        deadline_seconds=deadline_seconds,
                        reason=last_reason,
                    ) from exc
                time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                continue
            last_presence = _sanitize_acpx_presence(presence.get("presence"))
            last_components["presence"] = {
                "ready": presence["ready"] is True,
                "reason_code": presence["reason_code"],
            }
            if presence["ready"] is not True:
                last_reason = f"presence_{presence['reason_code']}"
            else:
                try:
                    manager_preflights = _acpx_manager_preflights_ready_with_timeout(
                        LIVE_PORT,
                        timeout=_acpx_probe_timeout(started, deadline_seconds, max_seconds=5.0),
                    )
                except (HelperError, OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
                    last_reason = f"manager_{exc.__class__.__name__}"
                    elapsed = time.monotonic() - started
                    if elapsed >= deadline_seconds:
                        raise _acpx_promoted_readiness_error(
                            attempt_count=attempt_count,
                            elapsed_seconds=elapsed,
                            deadline_seconds=deadline_seconds,
                            reason=last_reason,
                        ) from exc
                    time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))
                    continue
                last_agent_preflights = list(manager_preflights["agents"]) if isinstance(manager_preflights.get("agents"), list) else []
                last_components["manager_preflights"] = {
                    "ready": manager_preflights["ready"] is True,
                    "reason_code": manager_preflights["reason_code"],
                    "ready_agents": manager_preflights.get("ready_agents", []),
                    "auth_blocked_agents": manager_preflights.get("auth_blocked_agents", []),
                }
                if manager_preflights["ready"] is not True:
                    last_reason = f"manager_preflight_{manager_preflights['reason_code']}"
                else:
                    elapsed = time.monotonic() - started
                    return {
                        "worker_id": worker_id,
                        "manager_url": f"http://127.0.0.1:{LIVE_PORT}",
                        "active": True,
                        "adapters_ready": True,
                        "bound": True,
                        "preflights": True,
                        "presence": last_presence,
                        "agent_preflights": last_agent_preflights,
                        "components": last_components,
                        "attempt_count": attempt_count,
                        "elapsed_seconds": round(elapsed, 2),
                        "deadline_seconds": deadline_seconds,
                        "readiness_reason": "ready",
                        "acpx_executable": _path_ref(acpx_executable),
                    }
        elapsed = time.monotonic() - started
        if elapsed >= deadline_seconds:
            raise _acpx_promoted_readiness_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))


def op_bootstrap_acpx_promote(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    worker_id = validate_name(args["worker_id"], "ACPX worker id")
    require(worker_id == acpx_candidate_worker_id(release_id), "unexpected ACPX worker id")
    require(int(args["manager_port"]) == LIVE_PORT, "promoted ACPX worker must bind to live Manager port")
    _release_source_worktree(release_id, args["release_source"])
    release = require_existing_release_dir(release_id)
    bootstrap = acpx_bootstrap_dir(release_id)
    promoted_acpx_arg = Path(str(args["acpx_executable"]))
    require(promoted_acpx_arg == release / "acpx-runtime" / ACPX_DIRECT_CLI, "ACPX executable path is not allowlisted")
    candidate_unit = _require_existing_bootstrap_file(release_id, bootstrap / f"{worker_id}.service", "ACPX candidate unit")
    bootstrap_node = _require_existing_bootstrap_dir(release_id, bootstrap / "node-runtime", "ACPX node runtime")
    bootstrap_venv = _require_existing_bootstrap_dir(release_id, bootstrap / "venv", "ACPX venv")
    bootstrap_capability = _require_existing_bootstrap_dir(release_id, bootstrap / "capability", "ACPX capability")
    bootstrap_key = _require_existing_bootstrap_file(release_id, bootstrap / "candidate.worker.key", "ACPX candidate key")
    _require_existing_bootstrap_file(release_id, _acpx_cli_under(bootstrap_node), "ACPX candidate executable", executable=True)
    bootstrap_policy = _require_bootstrap_json_config(
        release_id,
        bootstrap_capability / "permission-policy.json",
        "ACPX permission policy",
        {"defaultAction": "deny"},
    )
    bootstrap_mcp = _require_bootstrap_json_config(
        release_id,
        bootstrap_capability / "mcp-config.json",
        "ACPX MCP config",
        {
            "mcpServers": [
                {
                    "name": "cloakbrowser",
                    "command": str(release / "source" / "scripts" / "cbm-mcp"),
                    "args": [],
                }
            ]
        },
    )
    durable_node = release / "acpx-runtime"
    durable_venv = release / "acpx-venv"
    durable_capability = release / "acpx-capability"
    durable_key = REMOTE_PATH / ".env.acpx.vcvm"
    permanent = expected_unit_path(ACPX_UNIT)
    content = (
        candidate_unit.read_text(encoding="utf-8")
        .replace(str(bootstrap / "venv"), str(durable_venv))
        .replace(str(bootstrap / "node-runtime"), str(durable_node))
        .replace(str(bootstrap / "capability"), str(durable_capability))
        .replace(str(bootstrap / "candidate.worker.key"), str(durable_key))
        .replace(f"127.0.0.1:{CANDIDATE_PORT}", f"127.0.0.1:{LIVE_PORT}")
    )
    interval_token = "--preflight-interval"
    candidate_interval_arg = f" {interval_token} {ACPX_CANDIDATE_PREFLIGHT_INTERVAL_SECONDS}\n"
    production_interval_arg = f" {interval_token} {ACPX_PRODUCTION_PREFLIGHT_INTERVAL_SECONDS}\n"
    require(
        content.count(interval_token) == 1 and content.count(candidate_interval_arg) == 1,
        "promoted ACPX unit missing expected candidate preflight interval",
    )
    content = content.replace(candidate_interval_arg, production_interval_arg)
    require(
        content.count(interval_token) == 1 and content.count(production_interval_arg) == 1,
        "promoted ACPX unit missing production preflight interval",
    )
    require(ACPX_BOOTSTRAP_DIR not in content, "promoted ACPX unit still references temporary bootstrap paths")
    require(f"--worktree {release / 'source'}" in content, "promoted ACPX unit missing release worktree path")
    require(
        f"--acpx {durable_node / ACPX_DIRECT_CLI}" in content,
        "promoted ACPX unit missing durable ACPX executable path",
    )
    require(
        f"--permission-policy {durable_capability / bootstrap_policy.name}" in content,
        "promoted ACPX unit missing durable permission policy path",
    )
    require(
        f"--mcp-config {durable_capability / bootstrap_mcp.name}" in content,
        "promoted ACPX unit missing durable MCP config path",
    )
    require(f"--capability-dir {durable_capability}" in content, "promoted ACPX unit missing durable capability path")
    for path, label in (
        (durable_node, "durable ACPX node runtime"),
        (durable_venv, "durable ACPX venv"),
        (durable_capability, "durable ACPX capability"),
    ):
        require(path.parent == release, f"{label} path is not release-scoped")
        require(not path.is_symlink(), f"{label} must not be a symlink")
        _remove_tree_if_present(path, label)
    shutil.move(str(bootstrap_node), str(durable_node))
    shutil.move(str(bootstrap_venv), str(durable_venv))
    shutil.move(str(bootstrap_capability), str(durable_capability))
    acpx_executable = _validate_acpx_executable_path(release_id, promoted_acpx_arg, kind="promoted")
    require(not durable_key.is_symlink(), "ACPX key target must not be a symlink")
    shutil.copyfile(bootstrap_key, durable_key)
    durable_key.chmod(0o600)
    _write_text_mode_0600_atomic(permanent, content)
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "restart", ACPX_UNIT])
    readiness = _promoted_acpx_readiness(worker_id=worker_id, release_source=release / "source", acpx_executable=acpx_executable)
    return {
        **readiness,
        "unit": _path_ref(permanent),
        "runtime": {"node_root": _path_ref(durable_node), "venv": _path_ref(durable_venv)},
        "key": _path_ref(durable_key),
        "capability": _path_ref(durable_capability),
    }


def op_bootstrap_acpx_cleanup(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    worker_id = acpx_candidate_worker_id(release_id)
    require_existing_release_dir(release_id)
    root = _require_existing_bootstrap_dir(release_id, acpx_bootstrap_dir(release_id), "ACPX bootstrap")
    run(["systemctl", "--user", "stop", f"{worker_id}.service"], check=False)
    expected_unit_path(f"{worker_id}.service").unlink(missing_ok=True)
    _remove_tree_if_present(root, "ACPX bootstrap")
    return {"removed": "release-acpx-only", "unit": worker_id, "root": _path_ref(root)}


def op_candidate_cleanup(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    container = f"cloakbrowser-manager-candidate-{release_id}"
    volume = f"{MANAGER_VOLUME}-candidate-{release_id}"
    run(["docker", "rm", "-f", container], check=False)
    run(["docker", "volume", "rm", volume], check=False)
    return {"removed": "candidate-only", "container": container, "volume": volume}


def op_capture_state(args: dict[str, object]) -> dict[str, object]:
    commit = validate_commit(args["commit"])
    manager = op_preflight_manager({})
    browser = op_preflight_browser_use({})
    acpx = _capture_acpx_state()
    current_target = str(CURRENT_LINK.resolve()) if CURRENT_LINK.exists() else ""
    pointer_state = json_file(STATE_FILE) if STATE_FILE.exists() else {}
    return {
        "source_revision": commit,
        "previous_revision": manager["revision"],
        "previous_revision_available": manager["revision_available"],
        "old_image_digest": manager["image_digest"],
        "old_image_id": manager["image_id"],
        "container_config_receipt": hashlib.sha256(json.dumps(manager, sort_keys=True).encode()).hexdigest(),
        "manager_bind_mounts": manager["manager_bind_mounts"],
        "manager_restart_policy": manager["restart_policy"] or MANAGER_RESTART_POLICY,
        "manager_network_mode": manager["network_mode"] or "bridge",
        "current_pointer": current_target,
        "state": pointer_state,
        "browser_use_unit_sha256": browser["unit_sha256"],
        "browser_use_unit_path": browser["unit_path"],
        "browser_use_active_state": browser["active_state"],
        "browser_use_dropin_path": browser["dropin_path"],
        "browser_use_dropin_exists": browser["dropin_exists"],
        "browser_use_dropin_content": browser["dropin_content"],
        "browser_use_dropin_sha256": browser["dropin_sha256"],
        "acpx_unit_sha256": acpx["unit_sha256"],
        "acpx_unit_path": acpx["unit_path"],
        "acpx_active_state": acpx["active_state"],
        "acpx_dropin_path": acpx["dropin_path"],
        "acpx_dropin_exists": acpx["dropin_exists"],
        "acpx_dropin_content": acpx["dropin_content"],
        "acpx_dropin_sha256": acpx["dropin_sha256"],
        "acpx_working_directory": acpx.get("working_directory", ""),
        "acpx_exec_start": acpx.get("exec_start", ""),
        "acpx_exec_main_pid": acpx.get("exec_main_pid", ""),
        "acpx_was_absent": acpx.get("was_absent") is True,
        "acpx_absence": acpx.get("absence", {}),
        "live_volume": MANAGER_VOLUME,
    }


def op_quiesce_stop_workers(args: dict[str, object]) -> dict[str, object]:
    del args
    for unit in (BROWSER_USE_UNIT, ACPX_UNIT):
        run(["systemctl", "--user", "stop", unit], check=False)
    return {"stopped": True}


def op_quiesce_stop_live(args: dict[str, object]) -> dict[str, object]:
    del args
    run(["docker", "stop", MANAGER_CONTAINER], check=False)
    return {"stopped": True}


def op_live_start(args: dict[str, object]) -> dict[str, object]:
    image_ref = validate_immutable_image(args["image_ref"], "live image ref")
    run(["docker", "image", "inspect", image_ref])
    run(["docker", "rm", "-f", MANAGER_CONTAINER], check=False)
    run(_manager_docker_run_args(container=MANAGER_CONTAINER, host_port=LIVE_PORT, volume=MANAGER_VOLUME, image_ref=image_ref))
    return {"container": MANAGER_CONTAINER, "port": LIVE_PORT, "container_port": MANAGER_CONTAINER_PORT, "manager_bind_mounts": manager_bind_mount_receipt(), "restart_policy": MANAGER_RESTART_POLICY}


def _validate_acpx_bootstrap_absence_for_rebind(capture: dict[str, object], release_id: str) -> bool:
    claims_bootstrap_absence = (
        capture.get("acpx_was_absent") is True
        or bool(capture.get("acpx_bootstrap_release_id"))
        or bool(capture.get("acpx_absence"))
    )
    if not claims_bootstrap_absence:
        return False

    require(capture.get("acpx_bootstrap_release_id") == release_id, "captured ACPX bootstrap absence release mismatch")
    require(capture.get("acpx_was_absent") is True, "captured ACPX bootstrap absence flag mismatch")
    require(capture.get("acpx_active_state") == "absent", "captured ACPX bootstrap absence active state mismatch")
    require(capture.get("acpx_unit_sha256") == "0" * 64, "captured ACPX bootstrap absence unit hash mismatch")
    require(capture.get("acpx_dropin_exists") is False, "captured ACPX bootstrap absence drop-in mismatch")
    absence = capture.get("acpx_absence")
    require(isinstance(absence, dict), "captured ACPX bootstrap absence receipt is missing")
    require(absence.get("state") == "absent", "captured ACPX bootstrap absence receipt state mismatch")
    missing = absence.get("missing")
    require(isinstance(missing, list), "captured ACPX bootstrap absence receipt missing list mismatch")
    require(set(missing) == set(ACPX_ABSENCE_COMPONENTS), "captured ACPX bootstrap absence receipt missing components mismatch")
    if "reason_code" in absence:
        require(absence["reason_code"] == "missing_runtime", "captured ACPX bootstrap absence receipt reason mismatch")
    return True


def op_workers_rebind(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    commit = validate_commit(args["commit"])
    source_path = release_dir(release_id) / "source"
    marker = release_dir(release_id) / "COMMIT"
    require(marker.read_text(encoding="utf-8").strip() == commit, "release commit marker mismatch")
    capture = dict(args["capture"])
    defer_acpx_rebind = _validate_acpx_bootstrap_absence_for_rebind(capture, release_id)
    target_acpx_exec_start = ""
    if not defer_acpx_rebind:
        _unit_state(ACPX_UNIT)
        source_release_id, _source_release = _captured_acpx_source_release(capture)
        captured_exec_start = str(capture.get("acpx_exec_start") or "")
        if not captured_exec_start:
            captured_exec_start = _effective_unit_exec_start(
                str(capture.get("acpx_unit_content") or ""),
                str(capture.get("acpx_dropin_content") or ""),
            )
        captured_options = _parse_acpx_worker_exec_start(captured_exec_start, source_release_id)
        _require_release_asset_dir(release_id, release_dir(release_id) / "acpx-runtime", "ACPX runtime")
        _validate_promoted_acpx_venv_path(release_id)
        _require_release_asset_dir(release_id, release_dir(release_id) / "acpx-capability", "ACPX capability")
        _validate_acpx_executable_path(
            release_id,
            release_dir(release_id) / "acpx-runtime" / ACPX_DIRECT_CLI,
            kind="promoted",
        )
        target_acpx_exec_start = _build_acpx_worker_exec_start(release_id, captured_options)
    dropins: dict[str, dict[str, object]] = {}
    for unit in (BROWSER_USE_UNIT, ACPX_UNIT):
        if unit == ACPX_UNIT and defer_acpx_rebind:
            continue
        validate_name(unit, "unit")
        unit_state = _unit_state(unit)
        require("ExecStart" in unit_state["content"], f"unexpected unit shape: {unit}")
        dropin_dir = release_dropin(unit).parent
        require(not dropin_dir.is_symlink(), f"drop-in dir is symlink: {unit}")
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin = release_dropin(unit)
        require(not dropin.is_symlink(), f"drop-in file is symlink: {unit}")
        content = f"[Service]\nWorkingDirectory={source_path}\nEnvironment=CBM_RELEASE_WORKTREE={source_path}\n"
        if unit == ACPX_UNIT:
            content += f"ExecStart=\nExecStart={target_acpx_exec_start}\n"
        _write_text_mode_0600_atomic(dropin, content)
        digest = file_sha256(dropin)
        run(["systemctl", "--user", "daemon-reload"])
        intended_state = str(capture.get("browser_use_active_state" if unit == BROWSER_USE_UNIT else "acpx_active_state", "active"))
        if intended_state == "active":
            run(["systemctl", "--user", "restart", unit])
        else:
            run(["systemctl", "--user", "stop", unit])
        show = _unit_show(unit)
        require(show.get("WorkingDirectory") == str(source_path), f"unit WorkingDirectory not rebound: {unit}")
        require(bool(show.get("FragmentPath")), f"unit FragmentPath missing: {unit}")
        if unit == ACPX_UNIT and intended_state == "active":
            show.update(_acpx_exec_show())
            _validate_effective_acpx_binding(show, release_id, verify_process=True)
        require(file_sha256(dropin) == digest and dropin.read_text(encoding="utf-8") == content, f"drop-in receipt mismatch: {unit}")
        require(f"{stat.S_IMODE(dropin.stat().st_mode):o}" == "600", f"drop-in mode mismatch: {unit}")
        dropins[unit] = {
            "dropin_path": str(dropin),
            "dropin_sha256": digest,
            "dropin_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "active_state": show.get("ActiveState", intended_state),
            "fragment_path": show.get("FragmentPath", ""),
            "working_directory": show.get("WorkingDirectory", ""),
            "exec_start": show.get("ExecStart", "") if unit == ACPX_UNIT else "",
            "exec_main_pid": show.get("ExecMainPID", "") if unit == ACPX_UNIT else "",
        }
    browser = op_preflight_browser_use({})
    if defer_acpx_rebind:
        acpx = {
            "deferred": True,
            "reason": "bootstrap_prior_absence",
            "release_id": release_id,
            "bootstrap_release_id": capture["acpx_bootstrap_release_id"],
            "unit": ACPX_UNIT,
            "promoted_by": "bootstrap.acpx_promote",
        }
    else:
        acpx = op_preflight_acpx({})
    return {
        "release_source": str(source_path),
        "commit_marker": commit,
        "browser_use": browser,
        "acpx": acpx,
        "dropins": dropins,
    }


def _verify_manager_once(args: dict[str, object], *, timeout: float | Callable[[], float] | None = None) -> dict[str, object]:
    revision_available = args.get("revision_available") is not False
    expected_image_id = validate_immutable_image(args.get("image_id"), "expected Manager image id")
    if revision_available:
        commit = validate_commit(args.get("commit"))
    else:
        commit = ""
        require(args.get("commit") in {"", None}, "Manager commit must be empty when revision is unavailable")

    def command_timeout() -> float | None:
        return timeout() if callable(timeout) else timeout

    health_result = run(["curl", "-fsS", f"http://127.0.0.1:{LIVE_PORT}/health"], check=False, timeout=command_timeout())
    health = health_result.returncode == 0
    auth_result = run(["curl", "-fsS", f"http://127.0.0.1:{LIVE_PORT}/api/auth/status"], check=False, timeout=command_timeout())
    if auth_result.returncode != 0:
        raise HelperError(f"auth_curl_{auth_result.returncode}")
    auth = json.loads(auth_result.stdout)
    require(isinstance(auth, dict), "Manager auth status returned non-object JSON")
    image_id = _running_manager_image_id(timeout=command_timeout())
    require(image_id == expected_image_id, "running Manager image id mismatch")
    if revision_available:
        revision = run(
            ["docker", "inspect", MANAGER_CONTAINER, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"],
            check=False,
            timeout=command_timeout(),
        ).stdout.strip()
        require(revision == commit, "running Manager revision mismatch")
    else:
        revision = ""
    payload = {
        "health": health,
        "auth": bool(auth.get("auth_required")),
        "image_id": image_id,
        "image_digest": image_id.removeprefix("sha256:"),
        "revision": revision,
        "revision_available": revision_available,
        "revision_matches": revision == commit,
    }
    return payload


def op_verify_manager(args: dict[str, object]) -> dict[str, object]:
    return _verify_manager_once(args)


def _restore_manager_verify_error(
    *,
    attempt_count: int,
    elapsed_seconds: float,
    deadline_seconds: float,
    reason: str,
) -> HelperError:
    return HelperError(
        "restore Manager verification failed: "
        f"attempt_count={attempt_count} "
        f"elapsed_seconds={elapsed_seconds:.2f} "
        f"deadline_seconds={deadline_seconds:g} "
        f"reason={_safe_reason_code(reason)}"
    )


def _restore_manager_verify_until_ready(capture: dict[str, object]) -> dict[str, object]:
    deadline_seconds = float(RESTORE_MANAGER_VERIFY_TIMEOUT_SECONDS)
    interval_seconds = float(RESTORE_MANAGER_VERIFY_POLL_INTERVAL_SECONDS)
    require(deadline_seconds > 0, "restore Manager verification deadline must be positive")
    require(interval_seconds > 0, "restore Manager verification poll interval must be positive")
    verify_args = {
        "commit": capture.get("previous_revision", ""),
        "revision_available": capture.get("previous_revision_available") is not False,
        "image_id": capture["old_image_id"],
    }
    started = time.monotonic()
    attempt_count = 0
    last_reason = "not_started"
    while True:
        elapsed = time.monotonic() - started
        if attempt_count > 0 and elapsed >= deadline_seconds:
            raise _restore_manager_verify_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        attempt_count += 1
        try:
            manager = _verify_manager_once(
                verify_args,
                timeout=lambda: _restore_manager_probe_timeout(started, deadline_seconds),
            )
            if manager["health"] is not True:
                last_reason = "health_not_ready"
            elif manager["auth"] is not True:
                last_reason = "auth_not_ready"
            else:
                manager.update(
                    {
                        "attempt_count": attempt_count,
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "deadline_seconds": deadline_seconds,
                        "readiness_reason": "ready",
                    }
                )
                return manager
        except (HelperError, RuntimeError) as exc:
            last_reason = str(exc) or exc.__class__.__name__
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError, TimeoutError) as exc:
            last_reason = exc.__class__.__name__
        elapsed = time.monotonic() - started
        if elapsed >= deadline_seconds:
            raise _restore_manager_verify_error(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed,
                deadline_seconds=deadline_seconds,
                reason=last_reason,
            )
        time.sleep(min(interval_seconds, max(0.0, deadline_seconds - elapsed)))


def op_verify_browser_use(args: dict[str, object]) -> dict[str, object]:
    state = op_preflight_browser_use(args)
    release_source = str(args["release_source"])
    show = _unit_show(BROWSER_USE_UNIT)
    state["bound"] = bool(release_source) and show.get("WorkingDirectory") == release_source
    state["working_directory"] = show.get("WorkingDirectory", "")
    state["fragment_path"] = show.get("FragmentPath", "")
    return state


def op_verify_acpx(args: dict[str, object]) -> dict[str, object]:
    if args.get("expected_absent") is True:
        probe = _probe_acpx_state()
        missing = {str(item) for item in probe.get("missing", [])}
        require(probe.get("state") == "absent" and missing == set(ACPX_ABSENCE_COMPONENTS), "ACPX expected absence verification failed")
        return {"absent": True, "missing": sorted(missing), "reason_code": probe.get("reason_code")}
    release_id = _release_id_for_release_source(args["release_source"])
    release = release_dir(release_id)
    acpx_executable = _validate_acpx_executable_path(release_id, args.get("acpx_executable"), kind="promoted")
    verify_args = dict(args)
    verify_args["venv"] = str(release / "acpx-venv")
    state = _preflight_acpx_with_adapter_probe(verify_args, _acpx_adapter_probe_at(acpx_executable))
    release_source = str(release / "source")
    show = _unit_show(ACPX_UNIT)
    show.update(_acpx_exec_show())
    _validate_effective_acpx_binding(show, release_id, verify_process=True)
    manager_preflights = _acpx_manager_preflights_ready()
    state["bound"] = bool(release_source) and show.get("WorkingDirectory") == release_source
    state["preflights"] = state["adapters_ready"] is True and manager_preflights["ready"] is True
    state["agent_preflights"] = manager_preflights["agents"]
    state["working_directory"] = show.get("WorkingDirectory", "")
    state["fragment_path"] = show.get("FragmentPath", "")
    return state


def op_verify_proxychecker(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"ok": run(["curl", "-fsS", PROXYCHECKER_HOST_HEALTH_URL], check=False).returncode == 0}


def op_verify_stream(args: dict[str, object]) -> dict[str, object]:
    del args
    spec = _manager_json("/openapi.json")
    paths = spec.get("paths")
    require(isinstance(paths, dict), "Manager OpenAPI paths are missing")
    required = {
        "/api/profiles/{profile_id}/live-metrics",
        "/api/profiles/{profile_id}/cdp",
        "/api/profiles/{profile_id}/open-links",
    }
    present = set(str(path) for path in paths)
    missing = sorted(required - present)
    require(not missing, f"Manager stream route missing: {', '.join(missing)}")
    return {"ok": True, "routes_present": sorted(required)}


def op_verify_orca(args: dict[str, object]) -> dict[str, object]:
    del args
    output = run(["orca", "status", "--json"], check=False).stdout
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HelperError("Orca status JSON is malformed") from exc
    require(isinstance(payload, dict), "Orca status JSON is malformed")
    result = payload.get("result")
    require(payload.get("ok") is True and isinstance(result, dict), "Orca status is not ok")
    runtime = result.get("runtime")
    graph = result.get("graph")
    require(isinstance(runtime, dict) and isinstance(graph, dict), "Orca status is incomplete")
    require(runtime.get("reachable") is True, "Orca runtime is unreachable")
    require(runtime.get("state") == "ready", "Orca runtime is not ready")
    require(graph.get("state") == "ready", "Orca graph is not ready")
    return {
        "ok": True,
        "runtime_reachable": True,
        "runtime_state": str(runtime["state"]),
        "graph_state": str(graph["state"]),
    }


def op_verify_tailscale(args: dict[str, object]) -> dict[str, object]:
    return op_preflight_tailscale(args)


def op_restore_runtime(args: dict[str, object]) -> dict[str, object]:
    backup = dict(args["backup"])
    capture = validate_restore_capture(dict(args["capture"]))
    _validate_backup(backup)
    old_image = str(capture["old_image_id"])
    run(["docker", "image", "inspect", old_image])
    run(["docker", "rm", "-f", MANAGER_CONTAINER], check=False)
    # Restore data from exact final backup receipt.
    run(["docker", "volume", "rm", MANAGER_VOLUME], check=False)
    run(["docker", "volume", "create", MANAGER_VOLUME])
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{MANAGER_VOLUME}:/data",
            "-v",
            f"{Path(str(backup['path'])).parent}:/backup:ro",
            "alpine",
            "tar",
            "-xf",
            f"/backup/{Path(str(backup['path'])).name}",
            "-C",
            "/data",
        ]
    )
    _restore_release_dropin(BROWSER_USE_UNIT, capture, "browser_use")
    if capture.get("acpx_was_absent") is True:
        _remove_acpx_bootstrap_artifacts(capture)
    else:
        _restore_release_dropin(ACPX_UNIT, capture, "acpx")
    run(["systemctl", "--user", "daemon-reload"])
    _restore_unit_state(BROWSER_USE_UNIT, str(capture.get("browser_use_active_state", "inactive")))
    if capture.get("acpx_was_absent") is True:
        run(["systemctl", "--user", "stop", ACPX_UNIT], check=False)
    else:
        _restore_unit_state(ACPX_UNIT, str(capture.get("acpx_active_state", "inactive")))
    run(_manager_docker_run_args(container=MANAGER_CONTAINER, host_port=LIVE_PORT, volume=MANAGER_VOLUME, image_ref=old_image))
    if capture.get("current_pointer"):
        CURRENT_LINK.unlink(missing_ok=True)
        CURRENT_LINK.symlink_to(str(capture["current_pointer"]))
    _write_json_mode_0600(STATE_FILE, dict(capture.get("state", {})))
    return {
        "restored": True,
        "backup_receipt_id": backup["receipt_id"],
        "old_image_digest": capture["old_image_digest"],
        "manager_bind_mounts": manager_bind_mount_receipt(),
        "restart_policy": MANAGER_RESTART_POLICY,
    }


def op_restore_verify(args: dict[str, object]) -> dict[str, object]:
    capture = validate_restore_capture(dict(args["capture"]))
    backup = dict(args["backup"])
    _validate_backup(backup)
    manager = _restore_manager_verify_until_ready(capture)
    browser = op_preflight_browser_use({})
    if capture.get("acpx_was_absent") is True:
        acpx = {"unit_sha256": "0" * 64, "active_state": "absent"}
    else:
        acpx = op_preflight_acpx({})
    browser_dropin = release_dropin(BROWSER_USE_UNIT)
    acpx_dropin = release_dropin(ACPX_UNIT)
    return {
        "health": manager["health"],
        "auth": manager["auth"],
        "backup_receipt_id": backup["receipt_id"],
        "old_image_id": manager["image_id"],
        "old_image_digest": capture.get("old_image_digest"),
        "pointer": str(CURRENT_LINK.resolve()) if CURRENT_LINK.exists() else "",
        "browser_use_unit_sha256": browser["unit_sha256"],
        "browser_use_active_state": browser["active_state"],
        "browser_use_dropin_exists": browser_dropin.exists() and not browser_dropin.is_symlink(),
        "browser_use_dropin_sha256": file_sha256(browser_dropin) if browser_dropin.exists() and not browser_dropin.is_symlink() else "",
        "acpx_unit_sha256": acpx["unit_sha256"],
        "acpx_active_state": acpx["active_state"],
        "acpx_dropin_exists": acpx_dropin.exists() and not acpx_dropin.is_symlink(),
        "acpx_dropin_sha256": file_sha256(acpx_dropin) if acpx_dropin.exists() and not acpx_dropin.is_symlink() else "",
    }


def op_rollback_read_state(args: dict[str, object]) -> dict[str, object]:
    del args
    return json_file(STATE_FILE)


def op_rollback_verify_backup(args: dict[str, object]) -> dict[str, object]:
    backup = dict(args["backup"])
    _validate_backup(backup)
    return {"compatible": True, **backup}


def op_rollback_verify_previous(args: dict[str, object]) -> dict[str, object]:
    previous_runtime = dict(args["previous_runtime"])
    image_ref = validate_immutable_image(previous_runtime.get("image_ref"), "previous image ref")
    image_id = validate_immutable_image(previous_runtime.get("image_id"), "previous image id")
    expected_digest = validate_sha256(previous_runtime.get("image_digest"))
    revision_available = previous_runtime.get("revision_available") is not False
    if revision_available:
        expected_revision = validate_commit(previous_runtime.get("revision"))
    else:
        expected_revision = ""
        require(previous_runtime.get("revision") in {"", None}, "unavailable previous revision must be empty")
    require(image_ref == f"sha256:{expected_digest}", "previous image digest mismatch")
    require(image_id == image_ref, "previous image id mismatch")
    run(["docker", "image", "inspect", image_ref])
    if revision_available:
        revision = run(["docker", "image", "inspect", image_ref, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"]).stdout.strip()
        require(revision == expected_revision, "previous image revision mismatch")
    else:
        revision = ""
    return {
        "image_ref": image_ref,
        "image_id": image_id,
        "image_digest": expected_digest,
        "revision": revision,
        "revision_available": revision_available,
        "manager_bind_mounts": manager_bind_mount_receipt(),
        "restart_policy": MANAGER_RESTART_POLICY,
    }


def op_rollback_start_previous(args: dict[str, object]) -> dict[str, object]:
    previous_runtime = dict(args["previous_runtime"])
    verified = op_rollback_verify_previous({"previous_runtime": previous_runtime})
    image_ref = str(verified["image_ref"])
    run(["docker", "rm", "-f", MANAGER_CONTAINER], check=False)
    run(_manager_docker_run_args(container=MANAGER_CONTAINER, host_port=LIVE_PORT, volume=MANAGER_VOLUME, image_ref=image_ref))
    return {"container": MANAGER_CONTAINER, **verified}


def op_state_commit(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    require(args.get("current_release") == release_id, "current release mismatch")
    previous_release = str(args["previous_release"])
    if previous_release:
        validate_release_id(previous_release)
    else:
        capture = args.get("capture")
        require(isinstance(capture, dict), "first release capture must be an object")
        capture_state = capture.get("state")
        require(isinstance(capture_state, dict), "first release capture state must be an object")
        previous_runtime = args.get("previous_runtime")
        require(isinstance(previous_runtime, dict), "first release previous runtime must be an object")
        require(
            not capture_state and not capture.get("current_pointer") and not previous_runtime.get("pointer"),
            "empty previous release requires no prior release state",
        )
    payload = dict(args)
    reject_secret_text(json.dumps(payload, sort_keys=True), "state payload")
    _write_json_mode_0600(STATE_FILE, payload)
    CURRENT_LINK.unlink(missing_ok=True)
    CURRENT_LINK.symlink_to(str(release_dir(release_id) / "source"))
    return payload


OPERATIONS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "helper.capabilities": op_helper_capabilities,
    "preflight.disk": op_preflight_disk,
    "preflight.marker": op_preflight_marker,
    "preflight.commands": op_preflight_commands,
    "preflight.env": op_preflight_env,
    "preflight.manager": op_preflight_manager,
    "preflight.browser_use": op_preflight_browser_use,
    "preflight.acpx": op_preflight_acpx,
    "preflight.receipts": op_preflight_receipts,
    "preflight.tailscale": op_preflight_tailscale,
    "bootstrap.acpx_probe": op_bootstrap_acpx_probe,
    "release.prepare": op_release_prepare,
    "release.verify_archive": op_release_verify_archive,
    "release.extract": op_release_extract,
    "release.write_marker": op_release_write_marker,
    "build.image": op_build_image,
    "backup.live": op_backup_live,
    "backup.final_stopped": op_backup_final_stopped,
    "candidate.clone": op_candidate_clone,
    "candidate.start": op_candidate_start,
    "candidate.verify": op_candidate_verify,
    "bootstrap.acpx_install": op_bootstrap_acpx_install,
    "bootstrap.acpx_provision_candidate": op_bootstrap_acpx_provision_candidate,
    "bootstrap.acpx_start_candidate": op_bootstrap_acpx_start_candidate,
    "bootstrap.acpx_verify_candidate": op_bootstrap_acpx_verify_candidate,
    "bootstrap.acpx_promote": op_bootstrap_acpx_promote,
    "bootstrap.acpx_cleanup": op_bootstrap_acpx_cleanup,
    "candidate.cleanup": op_candidate_cleanup,
    "capture.state": op_capture_state,
    "acpx.stage-runtime": op_acpx_stage_runtime,
    "quiesce.stop_workers": op_quiesce_stop_workers,
    "quiesce.stop_live": op_quiesce_stop_live,
    "live.start": op_live_start,
    "workers.rebind": op_workers_rebind,
    "verify.manager": op_verify_manager,
    "verify.browser_use": op_verify_browser_use,
    "verify.acpx": op_verify_acpx,
    "verify.proxychecker": op_verify_proxychecker,
    "verify.stream": op_verify_stream,
    "verify.orca": op_verify_orca,
    "verify.tailscale": op_verify_tailscale,
    "restore.runtime": op_restore_runtime,
    "restore.verify": op_restore_verify,
    "rollback.read_state": op_rollback_read_state,
    "rollback.verify_backup": op_rollback_verify_backup,
    "rollback.verify_previous": op_rollback_verify_previous,
    "rollback.start_previous": op_rollback_start_previous,
    "state.commit": op_state_commit,
}


def handle_request(request: dict[str, object]) -> dict[str, object]:
    operation = str(request.get("operation", ""))
    args = request.get("args", {})
    require(isinstance(args, dict), "request args must be an object")
    schema = OPERATION_SCHEMAS.get(operation)
    if schema is None:
        raise HelperError(f"unknown remote operation: {operation}")
    optional = OPTIONAL_OPERATION_ARGS.get(operation, set())
    actual = set(args)
    missing = (schema - optional) - actual
    extra = actual - schema - optional
    if missing or extra:
        raise HelperError(f"invalid args for {operation}: missing={sorted(missing)} extra={sorted(extra)}")
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise HelperError(f"unknown remote operation: {operation}")
    return handler(args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        return refuse_remote_request(exc)
    return write_response_or_refusal(request, pretty=args.pretty)


def write_response_or_refusal(request: object, *, pretty: bool = False) -> int:
    try:
        require(isinstance(request, dict), "remote request must be an object")
        payload = handle_request(request)
    except REMOTE_REFUSAL_EXCEPTIONS as exc:
        return refuse_remote_request(exc)
    sys.stdout.write(json.dumps(payload, indent=2 if pretty else None, sort_keys=True) + "\n")
    return 0


def streamed_main(payload: object) -> int:
    try:
        require(isinstance(payload, dict), "streamed payload must be an object")
        request = payload.get("request")
    except REMOTE_REFUSAL_EXCEPTIONS as exc:
        return refuse_remote_request(exc)
    return write_response_or_refusal(request)


if __name__ == "__main__":
    raise SystemExit(main())
