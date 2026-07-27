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
import shutil
import stat
import subprocess
import sys
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
LIVE_PORT = 18115
CANDIDATE_PORT = 18116
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
    "candidate.cleanup": {"release_id"},
    "capture.state": {"commit"},
    "quiesce.stop_workers": set(),
    "quiesce.stop_live": set(),
    "live.start": {"image_ref", "volume", "port"},
    "workers.rebind": {"release_id", "commit", "capture"},
    "verify.manager": {"commit"},
    "verify.browser_use": {"commit", "release_source"},
    "verify.acpx": {"release_source"},
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
    "state.commit": {"release_id", "current_release", "previous_release", "image", "final_backup", "capture", "previous_runtime"},
}
SECRET_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bcbm_(?:agent|worker)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE),
    re.compile(r"(?i)(?:token|secret|password|passwd|apikey|api_key)=([^&\s]{8,})"),
)


class HelperError(RuntimeError):
    pass


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


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
    validate_commit(capture.get("previous_revision"))
    validate_current_pointer(capture.get("current_pointer"))
    expected_paths = {
        "browser_use_unit_path": expected_unit_path("cloakbrowser-browser-use.service"),
        "acpx_unit_path": expected_unit_path("cloakbrowser-acpx.service"),
    }
    for key, expected in expected_paths.items():
        actual = Path(str(capture.get(key, "")))
        require(actual == expected, f"captured unit path is not allowlisted: {key}")
        require(not actual.parent.is_symlink(), f"captured unit path parent is a symlink: {key}")
    for key, unit in (
        ("browser_use_dropin_path", "cloakbrowser-browser-use.service"),
        ("acpx_dropin_path", "cloakbrowser-acpx.service"),
    ):
        require(key in capture, f"captured drop-in path is missing: {key}")
        actual = Path(str(capture[key]))
        require(actual == release_dropin(unit), f"captured drop-in path is not allowlisted: {key}")
        require(not actual.parent.is_symlink(), f"captured drop-in path parent is a symlink: {key}")
    return capture


def release_dir(release_id: str) -> Path:
    release_id = validate_release_id(release_id)
    path = (RELEASES_PATH / release_id).resolve()
    require(path.parent == RELEASES_PATH.resolve(), "release path escapes releases directory")
    return path


def run(argv: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    require(not isinstance(argv, str), "commands must use argv arrays")
    return subprocess.run(
        argv,
        input=input_text,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    container = json.loads(run(["docker", "inspect", MANAGER_CONTAINER]).stdout)[0]
    image_ref = str(container["Config"]["Image"])
    image_id = str(container["Image"])
    revision = run(["docker", "image", "inspect", image_id, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"], check=False).stdout.strip()
    require(COMMIT_RE.fullmatch(revision) is not None, "running Manager image revision label is missing")
    mounts = container.get("Mounts", [])
    volumes = [item.get("Name") for item in mounts if item.get("Type") == "volume"]
    return {
        "container": container["Name"].lstrip("/"),
        "image": image_ref,
        "image_id": image_id,
        "image_digest": image_id.removeprefix("sha256:"),
        "revision": revision,
        "volume": MANAGER_VOLUME if MANAGER_VOLUME in volumes else "",
    }


def _unit_state(name: str) -> dict[str, object]:
    path_result = run(["systemctl", "--user", "show", name, "-p", "FragmentPath", "--value"])
    path = Path(path_result.stdout.strip())
    active = run(["systemctl", "--user", "is-active", name], check=False).stdout.strip()
    content = path.read_text(encoding="utf-8") if path.exists() else ""
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
    token = _env_auth_token()
    request = Request(
        f"http://127.0.0.1:{LIVE_PORT}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed loopback URL.
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HelperError("Manager authenticated probe failed") from exc
    require(isinstance(payload, dict), "Manager probe returned non-object JSON")
    return payload


def _acpx_adapter_probe() -> dict[str, object]:
    executable = shutil.which("acpx")
    if executable is None:
        return {"ready": False, "reason_code": "adapter_unavailable"}
    version = run([executable, "--version"], check=False)
    output = f"{version.stdout}\n{version.stderr}"
    if version.returncode != 0:
        return {"ready": False, "reason_code": "adapter_unavailable"}
    if ACPX_VERSION_RE.search(output) is None:
        return {"ready": False, "reason_code": "version_mismatch"}
    return {"ready": True, "reason_code": "ok"}


def _acpx_manager_preflights_ready() -> dict[str, object]:
    payload = _manager_json("/api/task-harnesses/acpx/preflights")
    agents = payload.get("agents", [])
    require(isinstance(agents, list) and agents, "ACPX preflight receipt is empty")
    failures = [
        item
        for item in agents
        if not isinstance(item, dict)
        or item.get("ready") is not True
        or item.get("state") != "ready"
        or item.get("reason_code") != "ok"
    ]
    return {"ready": not failures, "agents": agents}


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


def _token_mode(path: Path) -> str:
    require(path.exists() and not path.is_symlink(), f"token file missing: {path}")
    return f"{stat.S_IMODE(path.stat().st_mode):o}"


def op_preflight_browser_use(args: dict[str, object]) -> dict[str, object]:
    unit = str(args.get("unit", "cloakbrowser-browser-use.service"))
    token_path = Path(str(args.get("token_path", REMOTE_PATH / ".env.worker.vcvm")))
    state = _unit_state(unit)
    commit = run(["git", "-C", str(REMOTE_PATH), "rev-parse", "--short=12", "HEAD"], check=False).stdout.strip()
    state.update({"active": state["active_state"] == "active", "token_mode": _token_mode(token_path), "commit": commit})
    return state


def op_preflight_acpx(args: dict[str, object]) -> dict[str, object]:
    unit = str(args.get("unit", "cloakbrowser-acpx.service"))
    token_path = Path(str(args.get("token_path", REMOTE_PATH / ".env.acpx.vcvm")))
    venv = Path(str(args.get("venv", REMOTE_PATH / ".venv-acpx")))
    state = _unit_state(unit)
    adapter_probe = _acpx_adapter_probe()
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


def op_preflight_receipts(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"ok": (REMOTE_PATH / "receipts").exists()}


def op_preflight_tailscale(args: dict[str, object]) -> dict[str, object]:
    del args
    result = run(["tailscale", "serve", "status", "--json"], check=False)
    return {"ok": "18115" in result.stdout or str(LIVE_PORT) in result.stdout}


def op_release_prepare(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    expected_commit = validate_commit(args["commit"])
    expected_archive = validate_sha256(args["archive_sha256"])
    path = release_dir(release_id)
    if path.exists():
        manifest = json_file(path / "manifest.json")
        require(manifest.get("release_id") == release_id, "existing release id mismatch")
        require(manifest.get("source", {}).get("commit") == expected_commit, "existing release commit mismatch")
        require(manifest.get("source", {}).get("archive_sha256") == expected_archive, "existing release archive mismatch")
        return {"exists": True, "release_id": release_id}
    path.mkdir(parents=False, mode=0o755)
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
    candidate_volume = f"{MANAGER_VOLUME}-candidate-{release_id}"
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
    run(["docker", "run", "-d", "--name", container, "-p", f"127.0.0.1:{CANDIDATE_PORT}:8000", "-v", f"{volume}:/data", "--env-file", str(REMOTE_PATH / ".env.vcvm"), image_ref])
    return {"container": container, "port": CANDIDATE_PORT}


def op_candidate_verify(args: dict[str, object]) -> dict[str, object]:
    validate_commit(args["commit"])
    health = run(["curl", "-fsS", f"http://127.0.0.1:{CANDIDATE_PORT}/health"], check=False).returncode == 0
    status = json.loads(run(["curl", "-fsS", f"http://127.0.0.1:{CANDIDATE_PORT}/api/auth/status"]).stdout)
    migrations = json.loads(run(["curl", "-fsS", f"http://127.0.0.1:{CANDIDATE_PORT}/api/admin/migrations"]).stdout)
    revision = run(["docker", "inspect", str(args["container"]), "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"]).stdout.strip()
    return {
        "health": health,
        "auth_required": bool(status.get("auth_required")),
        "access_control_enabled": bool(status.get("access_control_enabled")),
        "migrations": migrations,
        "revision": revision,
        "expected_migrations": list(EXPECTED_MIGRATIONS),
        "migration_set_exact": sorted(migrations) == sorted(EXPECTED_MIGRATIONS),
    }


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
    acpx = op_preflight_acpx({})
    current_target = str(CURRENT_LINK.resolve()) if CURRENT_LINK.exists() else ""
    pointer_state = json_file(STATE_FILE) if STATE_FILE.exists() else {}
    return {
        "source_revision": commit,
        "previous_revision": manager["revision"],
        "old_image_digest": manager["image_digest"],
        "old_image_id": manager["image_id"],
        "container_config_receipt": hashlib.sha256(json.dumps(manager, sort_keys=True).encode()).hexdigest(),
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
        "live_volume": MANAGER_VOLUME,
    }


def op_quiesce_stop_workers(args: dict[str, object]) -> dict[str, object]:
    del args
    for unit in ("cloakbrowser-browser-use.service", "cloakbrowser-acpx.service"):
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
    run(["docker", "run", "-d", "--name", MANAGER_CONTAINER, "-p", f"127.0.0.1:{LIVE_PORT}:8000", "-v", f"{MANAGER_VOLUME}:/data", "--env-file", str(REMOTE_PATH / ".env.vcvm"), image_ref])
    return {"container": MANAGER_CONTAINER, "port": LIVE_PORT}


def op_workers_rebind(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    commit = validate_commit(args["commit"])
    source_path = release_dir(release_id) / "source"
    marker = release_dir(release_id) / "COMMIT"
    require(marker.read_text(encoding="utf-8").strip() == commit, "release commit marker mismatch")
    capture = dict(args["capture"])
    dropins: dict[str, dict[str, object]] = {}
    for unit in ("cloakbrowser-browser-use.service", "cloakbrowser-acpx.service"):
        validate_name(unit, "unit")
        unit_state = _unit_state(unit)
        require("ExecStart" in unit_state["content"], f"unexpected unit shape: {unit}")
        dropin_dir = release_dropin(unit).parent
        require(not dropin_dir.is_symlink(), f"drop-in dir is symlink: {unit}")
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin = release_dropin(unit)
        require(not dropin.is_symlink(), f"drop-in file is symlink: {unit}")
        content = f"[Service]\nWorkingDirectory={source_path}\nEnvironment=CBM_RELEASE_WORKTREE={source_path}\n"
        _write_text_mode_0600_atomic(dropin, content)
        digest = file_sha256(dropin)
        run(["systemctl", "--user", "daemon-reload"])
        intended_state = str(capture.get("browser_use_active_state" if "browser-use" in unit else "acpx_active_state", "active"))
        if intended_state == "active":
            run(["systemctl", "--user", "restart", unit])
        else:
            run(["systemctl", "--user", "stop", unit])
        show = _unit_show(unit)
        require(show.get("WorkingDirectory") == str(source_path), f"unit WorkingDirectory not rebound: {unit}")
        require(bool(show.get("FragmentPath")), f"unit FragmentPath missing: {unit}")
        require(file_sha256(dropin) == digest and dropin.read_text(encoding="utf-8") == content, f"drop-in receipt mismatch: {unit}")
        require(f"{stat.S_IMODE(dropin.stat().st_mode):o}" == "600", f"drop-in mode mismatch: {unit}")
        dropins[unit] = {
            "dropin_path": str(dropin),
            "dropin_sha256": digest,
            "dropin_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "active_state": show.get("ActiveState", intended_state),
            "fragment_path": show.get("FragmentPath", ""),
            "working_directory": show.get("WorkingDirectory", ""),
        }
    browser = op_preflight_browser_use({})
    acpx = op_preflight_acpx({})
    return {
        "release_source": str(source_path),
        "commit_marker": commit,
        "browser_use": browser,
        "acpx": acpx,
        "dropins": dropins,
    }


def op_verify_manager(args: dict[str, object]) -> dict[str, object]:
    commit = args.get("commit")
    health = run(["curl", "-fsS", f"http://127.0.0.1:{LIVE_PORT}/health"], check=False).returncode == 0
    auth = json.loads(run(["curl", "-fsS", f"http://127.0.0.1:{LIVE_PORT}/api/auth/status"]).stdout)
    revision = run(["docker", "inspect", MANAGER_CONTAINER, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"], check=False).stdout.strip()
    payload = {"health": health, "auth": bool(auth.get("auth_required")), "revision": revision}
    if commit is not None:
        payload["revision_matches"] = revision == validate_commit(commit)
    return payload


def op_verify_browser_use(args: dict[str, object]) -> dict[str, object]:
    state = op_preflight_browser_use(args)
    release_source = str(args["release_source"])
    show = _unit_show("cloakbrowser-browser-use.service")
    state["bound"] = bool(release_source) and show.get("WorkingDirectory") == release_source
    state["working_directory"] = show.get("WorkingDirectory", "")
    state["fragment_path"] = show.get("FragmentPath", "")
    return state


def op_verify_acpx(args: dict[str, object]) -> dict[str, object]:
    state = op_preflight_acpx(args)
    release_source = str(args["release_source"])
    show = _unit_show("cloakbrowser-acpx.service")
    manager_preflights = _acpx_manager_preflights_ready()
    state["bound"] = bool(release_source) and show.get("WorkingDirectory") == release_source
    state["preflights"] = state["adapters_ready"] is True and manager_preflights["ready"] is True
    state["agent_preflights"] = manager_preflights["agents"]
    state["working_directory"] = show.get("WorkingDirectory", "")
    state["fragment_path"] = show.get("FragmentPath", "")
    return state


def op_verify_proxychecker(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"ok": run(["curl", "-fsS", "http://host.docker.internal:18899/health"], check=False).returncode == 0}


def op_verify_stream(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"ok": run(["curl", "-fsS", f"http://127.0.0.1:{LIVE_PORT}/api/stream/status"], check=False).returncode == 0}


def op_verify_orca(args: dict[str, object]) -> dict[str, object]:
    del args
    return {"status": run(["orca", "status"], check=False).stdout.strip()}


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
    _restore_release_dropin("cloakbrowser-browser-use.service", capture, "browser_use")
    _restore_release_dropin("cloakbrowser-acpx.service", capture, "acpx")
    run(["systemctl", "--user", "daemon-reload"])
    _restore_unit_state("cloakbrowser-browser-use.service", str(capture.get("browser_use_active_state", "inactive")))
    _restore_unit_state("cloakbrowser-acpx.service", str(capture.get("acpx_active_state", "inactive")))
    run(["docker", "run", "-d", "--name", MANAGER_CONTAINER, "-p", f"127.0.0.1:{LIVE_PORT}:8000", "-v", f"{MANAGER_VOLUME}:/data", "--env-file", str(REMOTE_PATH / ".env.vcvm"), old_image])
    if capture.get("current_pointer"):
        CURRENT_LINK.unlink(missing_ok=True)
        CURRENT_LINK.symlink_to(str(capture["current_pointer"]))
    _write_json_mode_0600(STATE_FILE, dict(capture.get("state", {})))
    return {"restored": True, "backup_receipt_id": backup["receipt_id"], "old_image_digest": capture["old_image_digest"]}


def op_restore_verify(args: dict[str, object]) -> dict[str, object]:
    capture = dict(args["capture"])
    backup = dict(args["backup"])
    _validate_backup(backup)
    manager = op_verify_manager({})
    browser = op_preflight_browser_use({})
    acpx = op_preflight_acpx({})
    browser_dropin = release_dropin("cloakbrowser-browser-use.service")
    acpx_dropin = release_dropin("cloakbrowser-acpx.service")
    return {
        "health": manager["health"],
        "auth": manager["auth"],
        "backup_receipt_id": backup["receipt_id"],
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
    expected_digest = validate_sha256(previous_runtime.get("image_digest"))
    expected_revision = validate_commit(previous_runtime.get("revision"))
    require(image_ref == f"sha256:{expected_digest}", "previous image digest mismatch")
    run(["docker", "image", "inspect", image_ref])
    revision = run(["docker", "image", "inspect", image_ref, "--format", "{{ index .Config.Labels \"org.opencontainers.image.revision\" }}"]).stdout.strip()
    require(revision == expected_revision, "previous image revision mismatch")
    return {"image_ref": image_ref, "image_digest": expected_digest, "revision": revision}


def op_rollback_start_previous(args: dict[str, object]) -> dict[str, object]:
    previous_runtime = dict(args["previous_runtime"])
    verified = op_rollback_verify_previous({"previous_runtime": previous_runtime})
    image_ref = str(verified["image_ref"])
    run(["docker", "rm", "-f", MANAGER_CONTAINER], check=False)
    run(["docker", "run", "-d", "--name", MANAGER_CONTAINER, "-p", f"127.0.0.1:{LIVE_PORT}:8000", "-v", f"{MANAGER_VOLUME}:/data", "--env-file", str(REMOTE_PATH / ".env.vcvm"), image_ref])
    return {"container": MANAGER_CONTAINER, **verified}


def op_state_commit(args: dict[str, object]) -> dict[str, object]:
    release_id = validate_release_id(args["release_id"])
    require(args.get("current_release") == release_id, "current release mismatch")
    validate_release_id(args["previous_release"])
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
    "candidate.cleanup": op_candidate_cleanup,
    "capture.state": op_capture_state,
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
    actual = set(args)
    missing = schema - actual
    extra = actual - schema
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
        payload = handle_request(request)
    except (HelperError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"vcvm release remote refused: {redact_text(exc)}", file=sys.stderr)
        return 75
    sys.stdout.write(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
