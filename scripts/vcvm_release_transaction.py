#!/usr/bin/env python3
"""Transactional VCVM release and rollback engine for CBM-022.

The module is executable, but defaults to a dry-run JSON receipt. Live execution
requires --apply plus an executor capable of SSH and transfer operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


MIN_FREE_BYTES = 8 * 1024**3
DEFAULT_HOST = "vcvm"
DEFAULT_REMOTE_PATH = "/home/coder/cloakbrowser-manager"
DEFAULT_VOLUME = "cloakbrowser-manager-vcvm-data"
DEFAULT_MANAGER_CONTAINER = "cloakbrowser-manager-vcvm"
DEFAULT_CANDIDATE_PORT = 18116
DEFAULT_LIVE_PORT = 18115
RELEASE_ID_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]{11,80}$")
HOST_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9._-]{0,31}@)?vcvm$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BACKUP_RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,80}$")
SECRET_PATTERNS = (
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bcbm_(?:agent|worker)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE),
    re.compile(r"(?i)(?:token|secret|password|passwd|apikey|api_key)=([^&\s]{8,})"),
)
REQUIRED_MIGRATIONS = (
    "agent_workspace_v1",
    "task_runs_v1",
    "worker_runtime_v1",
    "task_runs_acpx_v1",
    "worker_harness_presence_v1",
    "worker_harness_preflights_v1",
    "task_run_binding_v1",
)
EXPECTED_MIGRATIONS = REQUIRED_MIGRATIONS
_COMMIT = "0" * 40
_SHA = "e" * 64
_BACKUP = {
    "receipt_id": "backup-final-old",
    "path": "/home/coder/cloakbrowser-manager/backups/backup-final-old.tar",
    "sha256": "c" * 64,
    "volume": DEFAULT_VOLUME,
    "source_revision": _COMMIT,
    "compatible": True,
}
_CAPTURE = {
    "source_revision": _COMMIT,
    "previous_revision": "1" * 40,
    "old_image_digest": "d" * 64,
    "old_image_id": "sha256:" + ("d" * 64),
    "container_config_receipt": "3" * 64,
    "current_pointer": "release-previous-1",
    "state": {"current_release": "release-previous-1"},
    "browser_use_unit_sha256": "1" * 64,
    "browser_use_unit_path": "/home/coder/.config/systemd/user/browser-use.service",
    "browser_use_active_state": "active",
    "acpx_unit_sha256": "2" * 64,
    "acpx_unit_path": "/home/coder/.config/systemd/user/acpx.service",
    "acpx_active_state": "active",
    "live_volume": DEFAULT_VOLUME,
}
_IMAGE = {
    "image_ref": "sha256:" + _SHA,
    "image_id": "sha256:" + _SHA,
    "image_digest": _SHA,
    "revision": _COMMIT,
}
PHASE_REQUEST_EXAMPLES: dict[str, tuple[str, ...]] = {
    "helper.capabilities": ("cbm-release", "helper-capabilities", "{}"),
    "preflight.disk": ("cbm-release-probe", "disk", "{}"),
    "preflight.marker": ("cbm-release-probe", "marker", "{}"),
    "preflight.commands": ("cbm-release-probe", "commands", "{}"),
    "preflight.env": ("cbm-release-probe", "env", "{}"),
    "preflight.manager": ("cbm-release-probe", "manager", "{}"),
    "preflight.browser_use": ("cbm-release-probe", "browser-use", "{}"),
    "preflight.acpx": ("cbm-release-probe", "acpx", "{}"),
    "preflight.receipts": ("cbm-release-probe", "receipts", "{}"),
    "preflight.tailscale": ("cbm-release-probe", "tailscale", "{}"),
    "release.prepare": ("cbm-release", "prepare", json.dumps({"release_id": "release-0000001", "commit": _COMMIT, "archive_sha256": _SHA})),
    "release.verify_archive": ("cbm-release", "verify-archive", json.dumps({"release_id": "release-0000001", "archive_sha256": _SHA})),
    "release.extract": ("cbm-release", "extract", json.dumps({"release_id": "release-0000001"})),
    "release.write_marker": ("cbm-release", "write-marker", json.dumps({"release_id": "release-0000001", "commit": _COMMIT})),
    "build.image": ("cbm-release", "build-image", json.dumps({"release_id": "release-0000001", "commit": _COMMIT})),
    "backup.live": ("cbm-release", "backup-live-volume", json.dumps({"commit": _COMMIT})),
    "backup.final_stopped": ("cbm-release", "backup-stopped-live-volume", json.dumps({"commit": _COMMIT})),
    "candidate.clone": ("cbm-release", "clone-candidate-volume", json.dumps({"release_id": "release-0000001", "backup": _BACKUP})),
    "candidate.start": ("cbm-release", "start-candidate", json.dumps({"release_id": "release-0000001", "image_ref": "sha256:" + _SHA, "volume": DEFAULT_VOLUME + "-candidate-release-0000001"})),
    "candidate.verify": ("cbm-release", "verify-candidate", json.dumps({"commit": _COMMIT, "container": "cloakbrowser-manager-candidate-release-0000001"})),
    "candidate.cleanup": ("cbm-release", "candidate-cleanup", json.dumps({"release_id": "release-0000001"})),
    "capture.state": ("cbm-release", "capture-live-state", json.dumps({"commit": _COMMIT})),
    "quiesce.stop_workers": ("cbm-release", "stop-workers", "{}"),
    "quiesce.stop_live": ("cbm-release", "stop-live", "{}"),
    "live.start": ("cbm-release", "start-live", json.dumps({"image_ref": "sha256:" + _SHA, "volume": DEFAULT_VOLUME, "port": DEFAULT_LIVE_PORT})),
    "workers.rebind": ("cbm-release", "rebind-workers", json.dumps({"release_id": "release-0000001", "commit": _COMMIT, "capture": _CAPTURE})),
    "verify.manager": ("cbm-release", "verify-manager", json.dumps({"commit": _COMMIT})),
    "verify.browser_use": ("cbm-release", "verify-browser-use", json.dumps({"commit": _COMMIT, "release_source": "/home/coder/cloakbrowser-manager/releases/release-0000001/source"})),
    "verify.acpx": ("cbm-release", "verify-acpx", json.dumps({"release_source": "/home/coder/cloakbrowser-manager/releases/release-0000001/source"})),
    "verify.proxychecker": ("cbm-release", "verify-proxychecker", "{}"),
    "verify.stream": ("cbm-release", "verify-stream", "{}"),
    "verify.orca": ("cbm-release", "verify-orca", "{}"),
    "verify.tailscale": ("cbm-release", "verify-tailscale", "{}"),
    "restore.runtime": ("cbm-release", "restore-runtime", json.dumps({"capture": _CAPTURE, "backup": _BACKUP})),
    "restore.verify": ("cbm-release", "verify-restored-runtime", json.dumps({"capture": _CAPTURE, "backup": _BACKUP})),
    "rollback.read_state": ("cbm-release", "read-state", "{}"),
    "rollback.verify_backup": ("cbm-release", "verify-backup", json.dumps({"backup": _BACKUP})),
    "rollback.verify_previous": ("cbm-release", "verify-previous", json.dumps({"previous_runtime": _IMAGE})),
    "rollback.start_previous": ("cbm-release", "start-previous", json.dumps({"previous_runtime": _IMAGE})),
    "state.commit": ("cbm-release", "commit-state", json.dumps({"release_id": "release-0000001", "current_release": "release-0000001", "previous_release": "release-previous-1", "image": _IMAGE, "final_backup": _BACKUP, "capture": _CAPTURE, "previous_runtime": {"image_digest": "d" * 64, "pointer": "release-previous-1"}})),
}
POST_QUIESCE_PHASES = {
    "quiesce.stop_workers",
    "quiesce.stop_live",
    "backup.final_stopped",
    "live.start",
    "workers.rebind",
    "verify.manager",
    "verify.browser_use",
    "verify.acpx",
    "verify.proxychecker",
    "verify.stream",
    "verify.orca",
    "verify.tailscale",
    "state.commit",
}


class TransactionError(RuntimeError):
    """Fail-closed release error with a stable phase label."""

    def __init__(self, message: str, *, phase: str = "unknown") -> None:
        super().__init__(message)
        self.phase = phase


class RemoteExecutor(Protocol):
    """Boundary used by the transaction engine and tests."""

    def run_json(self, request: dict[str, object], *, phase: str, mutation: bool = False) -> dict[str, object]:
        """Run a command and return a JSON object."""

    def upload(self, local_path: Path, remote_path: str, *, phase: str) -> None:
        """Upload one local file to an exact remote destination."""


@dataclass(frozen=True)
class ReleaseConfig:
    source_root: Path
    release_id: str
    expected_source_remote: str
    source_remote: str = "fork"
    host: str = DEFAULT_HOST
    remote_path: str = DEFAULT_REMOTE_PATH
    apply: bool = False
    expected_current_worker_commit: str | None = None


@dataclass(frozen=True)
class RollbackConfig:
    source_root: Path
    host: str = DEFAULT_HOST
    remote_path: str = DEFAULT_REMOTE_PATH
    target_release: str = ""
    apply: bool = False


class SSHRemoteExecutor:
    """Real SSH/SCP executor.

    The transaction supplies argv arrays. This executor does not interpolate
    command strings locally; SSH receives argv tokens directly.
    """

    BOOTSTRAP = (
        "import json,sys;"
        "payload=json.load(sys.stdin);"
        "ns={'__name__':'vcvm_release_remote_streamed'};"
        "exec(payload['helper_source'],ns);"
        "result=ns['handle_request'](payload['request']);"
        "print(json.dumps(result,sort_keys=True))"
    )

    def __init__(self, host: str, *, helper_path: Path | None = None) -> None:
        validate_host(host)
        self.host = host
        self.helper_path = helper_path or Path(__file__).with_name("vcvm_release_remote.py")
        self.helper_source = self.helper_path.read_text(encoding="utf-8")

    def run_json(self, request: dict[str, object], *, phase: str, mutation: bool = False) -> dict[str, object]:
        del mutation
        payload = json.dumps({"helper_source": self.helper_source, "request": request})
        result = subprocess.run(
            ("ssh", "-o", "BatchMode=yes", "--", self.host, "python3", "-c", shlex.quote(self.BOOTSTRAP)),
            check=True,
            text=True,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise TransactionError(f"remote phase did not return JSON: {phase}", phase=phase) from exc
        if not isinstance(payload, dict):
            raise TransactionError(f"remote phase returned non-object JSON: {phase}", phase=phase)
        return payload

    def upload(self, local_path: Path, remote_path: str, *, phase: str) -> None:
        del phase
        subprocess.run(("scp", str(local_path), f"{self.host}:{remote_path}"), check=True)


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


def reject_secret_text(value: object, label: str) -> None:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise TransactionError(f"{label} contains secret material", phase="local.validate")


def require(condition: bool, message: str, *, phase: str) -> None:
    if not condition:
        raise TransactionError(message, phase=phase)


def validate_host_path(host: str, remote_path: str) -> None:
    validate_host(host)
    require(remote_path == DEFAULT_REMOTE_PATH, f"unexpected remote path: {remote_path}", phase="local.validate")


def validate_host(host: str) -> None:
    require(HOST_RE.fullmatch(host) is not None, f"unexpected target host: {host}", phase="local.validate")


def validate_release_id(release_id: str) -> str:
    require(bool(RELEASE_ID_RE.fullmatch(release_id)), "unsafe release_id", phase="local.validate")
    reject_secret_text(release_id, "release_id")
    return release_id


def validate_sha256(value: object, label: str = "sha256") -> str:
    digest = str(value)
    require(SHA256_RE.fullmatch(digest) is not None, f"invalid {label}", phase="remote.validate")
    return digest


def validate_commit(value: object) -> str:
    commit = str(value)
    require(COMMIT_RE.fullmatch(commit) is not None, "invalid commit", phase="remote.validate")
    return commit


def validate_expected_worker_commit(value: object) -> str:
    commit = str(value or "")
    require(
        COMMIT_RE.fullmatch(commit) is not None,
        "expected current worker commit must be a full 40-hex commit",
        phase="local.validate",
    )
    return commit


def validate_backup_receipt(value: object, *, phase: str) -> dict[str, object]:
    require(isinstance(value, dict), "backup receipt must be an object", phase=phase)
    receipt = dict(value)
    receipt_id = str(receipt.get("receipt_id", ""))
    require(BACKUP_RECEIPT_ID_RE.fullmatch(receipt_id) is not None, "backup receipt id is unsafe", phase=phase)
    require(
        str(receipt.get("path", "")) == f"{DEFAULT_REMOTE_PATH}/backups/{receipt_id}.tar",
        "backup path must match receipt id under managed backups",
        phase=phase,
    )
    validate_sha256(receipt.get("sha256"), "backup sha256")
    require(receipt.get("volume") == DEFAULT_VOLUME, "backup volume mismatch", phase=phase)
    validate_commit(receipt.get("source_revision"))
    require(receipt.get("compatible") is True, "backup receipt is incompatible", phase=phase)
    return receipt


def validate_image_result(value: object, commit: str, *, phase: str) -> dict[str, object]:
    require(isinstance(value, dict), "image result must be an object", phase=phase)
    image = dict(value)
    validate_sha256(image.get("image_digest"), "image digest")
    require(str(image.get("image_ref", "")) == f"sha256:{image['image_digest']}", "image ref must use immutable image id", phase=phase)
    require(str(image.get("image_id", "")) == f"sha256:{image['image_digest']}", "image id mismatch", phase=phase)
    require(image.get("revision") == commit, "image revision does not match release commit", phase=phase)
    return image


def validate_previous_runtime(value: object, *, phase: str) -> dict[str, object]:
    require(isinstance(value, dict), "previous runtime must be an object", phase=phase)
    runtime = dict(value)
    digest = validate_sha256(runtime.get("image_digest"), "previous image digest")
    require(str(runtime.get("image_ref", "")) == f"sha256:{digest}", "previous image ref must use immutable image id", phase=phase)
    require(str(runtime.get("image_id", "")) == f"sha256:{digest}", "previous image id mismatch", phase=phase)
    validate_commit(runtime.get("revision"))
    return runtime


def remote_request(phase: str, args: dict[str, object]) -> dict[str, object]:
    if phase not in PHASE_REQUEST_EXAMPLES:
        raise TransactionError(f"unknown remote phase: {phase}", phase=phase)
    expected = set(json.loads(PHASE_REQUEST_EXAMPLES[phase][-1]).keys())
    actual = set(args)
    if actual != expected:
        raise TransactionError(
            f"invalid args for remote phase {phase}: missing={sorted(expected - actual)} extra={sorted(actual - expected)}",
            phase=phase,
        )
    return {"operation": phase, "args": args}


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


def git_commit(root: Path) -> str:
    commit = run_git(root, "rev-parse", "HEAD")
    require(bool(COMMIT_RE.fullmatch(commit)), "git HEAD is not a full commit id", phase="local.git")
    return commit


def source_metadata(root: Path, remote_name: str, expected_remote: str) -> dict[str, object]:
    require(REMOTE_NAME_RE.fullmatch(remote_name) is not None, "source remote name is unsafe", phase="local.git")
    commit = git_commit(root)
    status = run_git(root, "status", "--porcelain")
    require(status == "", "release source must be a clean git checkout", phase="local.git")
    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    remote = run_git(root, "remote", "get-url", remote_name)
    reject_secret_text(remote, "source remote")
    parsed = urlsplit(remote)
    require(not (parsed.scheme and parsed.username), "source remote contains credentials", phase="local.git")
    require(remote == expected_remote, "source remote does not match expected fork remote", phase="local.git")
    reject_secret_text(branch, "source branch")
    return {"commit": commit, "branch": branch, "remote_name": remote_name, "remote": remote}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_git_archive(root: Path, commit: str) -> tuple[Path, str]:
    tmp = tempfile.NamedTemporaryFile(prefix=f"cbm-{commit[:12]}-", suffix=".tar", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        subprocess.run(
            ("git", "archive", "--format=tar", "--prefix=source/", "-o", str(tmp_path), commit),
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path, sha256_file(tmp_path)


def transaction_manifest(config: ReleaseConfig, source: dict[str, object], archive_sha256: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "vcvm-release-transaction-v1",
        "release_id": config.release_id,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": {
            **source,
            "archive_sha256": archive_sha256,
        },
        "target": {
            "host": config.host,
            "remote_path": config.remote_path,
            "release_dir": f"{config.remote_path}/releases/{config.release_id}",
            "live_port": DEFAULT_LIVE_PORT,
            "candidate_port": DEFAULT_CANDIDATE_PORT,
        },
    }
    reject_secret_text(json.dumps(payload, sort_keys=True), "transaction manifest")
    return payload


def remote_release_dir(config: ReleaseConfig) -> str:
    return f"{config.remote_path}/releases/{config.release_id}"


def remote_archive_path(config: ReleaseConfig) -> str:
    return f"{remote_release_dir(config)}/source.tar"


def remote_manifest_path(config: ReleaseConfig) -> str:
    return f"{remote_release_dir(config)}/manifest.json"


def _remote(executor: RemoteExecutor, phase: str, args: dict[str, object] | None = None, *, mutation: bool = False) -> dict[str, object]:
    request = remote_request(phase, args or {})
    return executor.run_json(request, phase=phase, mutation=mutation)


def run_preflight(config: ReleaseConfig, executor: RemoteExecutor, source: dict[str, object]) -> dict[str, object]:
    expected_worker_commit = validate_expected_worker_commit(config.expected_current_worker_commit)
    capabilities = _remote(executor, "helper.capabilities")
    require(capabilities.get("helper_version") == "vcvm-release-helper-v1", "helper capability/version check failed", phase="helper.capabilities")
    operations = set(capabilities.get("operations", []))
    require({"preflight.disk", "release.extract", "state.commit"}.issubset(operations), "helper capability/version check failed", phase="helper.capabilities")
    disk = _remote(executor, "preflight.disk")
    require(int(disk.get("free_bytes", 0)) >= MIN_FREE_BYTES, "refusing release: less than 8 GiB free", phase="preflight.disk")

    marker = _remote(executor, "preflight.marker")
    require(marker.get("owner") == "coder", "marker ownership is not the expected VCVM user", phase="preflight.marker")

    commands = _remote(executor, "preflight.commands")
    require(commands.get("ok") is True, "required remote command is missing", phase="preflight.commands")

    env = _remote(executor, "preflight.env")
    require(env.get("mode") == "600", "existing Manager env file must be mode 600", phase="preflight.env")

    manager = _remote(executor, "preflight.manager")
    require(manager.get("container") == DEFAULT_MANAGER_CONTAINER, "unexpected Manager container", phase="preflight.manager")
    require(manager.get("volume") == DEFAULT_VOLUME, "unexpected Manager data volume", phase="preflight.manager")
    validate_sha256(manager.get("image_digest"), "Manager image digest")

    browser_use = _remote(executor, "preflight.browser_use")
    require(browser_use.get("active") is True, "Browser Use unit is not active", phase="preflight.browser_use")
    require(browser_use.get("token_mode") == "600", "Browser Use token must be mode 600", phase="preflight.browser_use")
    require(bool(browser_use.get("unit_path")), "Browser Use unit path is missing", phase="preflight.browser_use")
    validate_sha256(browser_use.get("unit_sha256"), "Browser Use unit hash")
    require(
        str(browser_use.get("commit", "")) == expected_worker_commit,
        "worker commit skew: Browser Use is not bound to the expected commit",
        phase="preflight.browser_use",
    )

    acpx = _remote(executor, "preflight.acpx")
    require(acpx.get("active") is True, "ACPX unit is missing or inactive", phase="preflight.acpx")
    require(acpx.get("token_mode") == "600", "ACPX token must be mode 600", phase="preflight.acpx")
    require(acpx.get("venv") is True, "ACPX venv is missing", phase="preflight.acpx")
    require(acpx.get("adapters_ready") is True, "ACPX adapter preflight is missing", phase="preflight.acpx")
    require(bool(acpx.get("unit_path")), "ACPX unit path is missing", phase="preflight.acpx")
    validate_sha256(acpx.get("unit_sha256"), "ACPX unit hash")

    receipts = _remote(executor, "preflight.receipts")
    require(receipts.get("ok") is True, "service receipt prerequisites are missing", phase="preflight.receipts")

    tailscale = _remote(executor, "preflight.tailscale")
    require(tailscale.get("ok") is True, "Tailscale private route is missing", phase="preflight.tailscale")
    return {"disk": disk, "manager": manager, "browser_use": browser_use, "acpx": acpx, "source": source}


def prepare_release_source(
    config: ReleaseConfig,
    executor: RemoteExecutor,
    archive_path: Path,
    archive_sha256: str,
    manifest: dict[str, object],
) -> bool:
    prepare = _remote(
        executor,
        "release.prepare",
        {"release_id": config.release_id, "commit": str(manifest["source"]["commit"]), "archive_sha256": archive_sha256},
        mutation=True,
    )
    existing = prepare.get("exists") is True
    if not existing:
        executor.upload(archive_path, remote_archive_path(config), phase="release.upload_archive")
        manifest_file = tempfile.NamedTemporaryFile("w", prefix="cbm-manifest-", suffix=".json", delete=False, encoding="utf-8")
        manifest_path = Path(manifest_file.name)
        try:
            json.dump(manifest, manifest_file, sort_keys=True)
            manifest_file.write("\n")
            manifest_file.close()
            executor.upload(manifest_path, remote_manifest_path(config), phase="release.upload_manifest")
        finally:
            manifest_file.close()
            manifest_path.unlink(missing_ok=True)
    remote_hash = _remote(
        executor,
        "release.verify_archive",
        {"release_id": config.release_id, "archive_sha256": archive_sha256},
    )
    require(remote_hash.get("sha256") == archive_sha256, "archive hash mismatch after upload", phase="release.verify_archive")
    if not existing:
        _remote(executor, "release.extract", {"release_id": config.release_id}, mutation=True)
        _remote(
            executor,
            "release.write_marker",
            {"release_id": config.release_id, "commit": str(manifest["source"]["commit"])},
            mutation=True,
        )
    return existing


def cleanup_candidate(executor: RemoteExecutor, config: ReleaseConfig) -> None:
    try:
        _remote(executor, "candidate.cleanup", {"release_id": config.release_id}, mutation=True)
    except Exception as exc:  # pragma: no cover - cleanup failure should not hide original candidate failure.
        print(f"candidate cleanup failed: {redact_text(exc)}", file=sys.stderr)


def restore_old_runtime(
    executor: RemoteExecutor,
    *,
    reason: TransactionError,
    capture: dict[str, object],
    final_backup: dict[str, object],
) -> None:
    _remote(
        executor,
        "restore.runtime",
        {"capture": capture, "backup": final_backup},
        mutation=True,
    )
    restored = _remote(
        executor,
        "restore.verify",
        {"capture": capture, "backup": final_backup},
    )
    if (
        restored.get("health") is not True
        or restored.get("auth") is not True
        or restored.get("backup_receipt_id") != final_backup.get("receipt_id")
        or restored.get("old_image_digest") != capture.get("old_image_digest")
        or restored.get("browser_use_unit_sha256") != capture.get("browser_use_unit_sha256")
        or restored.get("browser_use_active_state") != capture.get("browser_use_active_state")
        or restored.get("acpx_unit_sha256") != capture.get("acpx_unit_sha256")
        or restored.get("acpx_active_state") != capture.get("acpx_active_state")
    ):
        raise TransactionError("rollback verification failed after release failure", phase="restore.verify") from reason


def run_release(config: ReleaseConfig, executor: RemoteExecutor) -> dict[str, object]:
    validate_host_path(config.host, config.remote_path)
    validate_release_id(config.release_id)
    source = source_metadata(config.source_root.resolve(), config.source_remote, config.expected_source_remote)
    archive_path, archive_sha256 = create_git_archive(config.source_root.resolve(), str(source["commit"]))
    manifest = transaction_manifest(config, source, archive_sha256)
    quiesce_started = False
    capture: dict[str, object] = {}
    final_backup: dict[str, object] = {}
    candidate_touched = False
    try:
        preflight = run_preflight(config, executor, source)
        prepare_release_source(config, executor, archive_path, archive_sha256, manifest)
        image = validate_image_result(
            _remote(executor, "build.image", {"release_id": config.release_id, "commit": str(source["commit"])}, mutation=True),
            str(source["commit"]),
            phase="build.image",
        )
        backup_live = validate_backup_receipt(
            _remote(executor, "backup.live", {"commit": str(source["commit"])}, mutation=True),
            phase="backup.live",
        )
        candidate_touched = True
        candidate_clone = _remote(
            executor,
            "candidate.clone",
            {"release_id": config.release_id, "backup": backup_live},
            mutation=True,
        )
        candidate_start = _remote(
            executor,
            "candidate.start",
            {"release_id": config.release_id, "image_ref": str(image["image_ref"]), "volume": str(candidate_clone["volume"])},
            mutation=True,
        )
        candidate = _remote(
            executor,
            "candidate.verify",
            {"commit": str(source["commit"]), "container": str(candidate_start["container"])},
        )
        if (
            candidate.get("health") is not True
            or candidate.get("auth_required") is not True
            or candidate.get("access_control_enabled") is not True
            or candidate.get("revision") != source["commit"]
            or sorted(candidate.get("migrations", [])) != sorted(REQUIRED_MIGRATIONS)
        ):
            cleanup_candidate(executor, config)
            raise TransactionError("candidate verification failed: migration set or runtime checks did not match", phase="candidate.verify")
        capture = _remote(executor, "capture.state", {"commit": str(source["commit"])})
        validate_commit(capture.get("previous_revision"))
        validate_sha256(capture.get("old_image_digest"), "old image digest")
        validate_sha256(capture.get("container_config_receipt"), "container config receipt")
        validate_sha256(capture.get("browser_use_unit_sha256"), "Browser Use unit hash")
        validate_sha256(capture.get("acpx_unit_sha256"), "ACPX unit hash")
        require(capture.get("live_volume") == DEFAULT_VOLUME, "captured live volume mismatch", phase="capture.state")
        quiesce_started = True
        _remote(executor, "quiesce.stop_workers", mutation=True)
        _remote(executor, "quiesce.stop_live", mutation=True)
        final_backup = validate_backup_receipt(
            _remote(executor, "backup.final_stopped", {"commit": str(source["commit"])}, mutation=True),
            phase="backup.final_stopped",
        )
        _remote(
            executor,
            "live.start",
            {"image_ref": str(image["image_ref"]), "volume": DEFAULT_VOLUME, "port": DEFAULT_LIVE_PORT},
            mutation=True,
        )
        rebind = _remote(
            executor,
            "workers.rebind",
            {"release_id": config.release_id, "commit": str(source["commit"]), "capture": capture},
            mutation=True,
        )
        require(rebind.get("release_source") == f"{remote_release_dir(config)}/source", "worker rebind source mismatch", phase="workers.rebind")
        require(rebind.get("commit_marker") == source["commit"], "worker rebind commit mismatch", phase="workers.rebind")
        release_source = f"{remote_release_dir(config)}/source"
        verify_runtime(executor, str(source["commit"]), release_source)
        previous_release = str(capture.get("state", {}).get("current_release") or capture.get("current_pointer") or "")
        state_payload = {
            "release_id": config.release_id,
            "current_release": config.release_id,
            "previous_release": previous_release,
            "image": image,
            "final_backup": final_backup,
            "capture": capture,
            "previous_runtime": {
                "image_ref": capture.get("old_image_id"),
                "image_id": capture.get("old_image_id"),
                "image_digest": capture.get("old_image_digest"),
                "revision": capture.get("previous_revision"),
                "pointer": capture.get("current_pointer"),
            },
        }
        reject_secret_text(json.dumps(state_payload, sort_keys=True), "state payload")
        _remote(executor, "state.commit", state_payload, mutation=True)
        cleanup_candidate(executor, config)
    except TransactionError as exc:
        if candidate_touched and exc.phase == "candidate.verify":
            cleanup_candidate(executor, config)
            raise
        if candidate_touched and exc.phase in {"candidate.clone", "candidate.start"}:
            cleanup_candidate(executor, config)
            raise
        if quiesce_started or exc.phase in POST_QUIESCE_PHASES:
            restore_old_runtime(executor, reason=exc, capture=capture, final_backup=final_backup or backup_live)
            raise TransactionError(f"release failed after quiesce and old runtime was restored: {exc}", phase=exc.phase) from exc
        raise
    finally:
        archive_path.unlink(missing_ok=True)
    return {
        "status": "success",
        "release_id": config.release_id,
        "source": {
            "commit": source["commit"],
            "commit_marker": source["commit"],
            "archive_sha256": archive_sha256,
        },
        "target": {
            "host": config.host,
            "remote_path": config.remote_path,
            "manager_port": DEFAULT_LIVE_PORT,
        },
        "backup": {
            "pre_candidate": backup_live,
            "final_stopped": final_backup,
        },
        "image": {"image_digest": image["image_digest"], "image_ref": image["image_ref"]},
        "preflight": {
            "disk_free_bytes": preflight["disk"]["free_bytes"],
        },
    }


def verify_runtime(executor: RemoteExecutor, commit: str, release_source: str) -> None:
    validate_commit(commit)
    require(bool(release_source), "runtime release source is required", phase="verify.runtime")
    manager = _remote(executor, "verify.manager", {"commit": commit})
    manager_ok = manager.get("health") is True and manager.get("auth") is True and manager.get("revision") == commit
    require(manager_ok, "Manager verification failed", phase="verify.manager")
    browser_use = _remote(executor, "verify.browser_use", {"commit": commit, "release_source": release_source})
    require(browser_use.get("active") is True and browser_use.get("bound") is True, "Browser Use verification failed", phase="verify.browser_use")
    acpx = _remote(executor, "verify.acpx", {"release_source": release_source})
    require(acpx.get("active") is True and acpx.get("preflights") is True, "ACPX verification failed", phase="verify.acpx")
    proxychecker = _remote(executor, "verify.proxychecker")
    require(proxychecker.get("ok") is True, "proxychecker verification failed", phase="verify.proxychecker")
    stream = _remote(executor, "verify.stream")
    require(stream.get("ok") is True, "stream verification failed", phase="verify.stream")
    orca = _remote(executor, "verify.orca")
    require(orca.get("status") == "ready", "Orca verification failed", phase="verify.orca")
    tailscale = _remote(executor, "verify.tailscale")
    require(tailscale.get("ok") is True, "Tailscale verification failed", phase="verify.tailscale")


def run_rollback(config: RollbackConfig, executor: RemoteExecutor) -> dict[str, object]:
    validate_host_path(config.host, config.remote_path)
    validate_release_id(config.target_release)
    state = _remote(executor, "rollback.read_state")
    require(state.get("previous_release") == config.target_release, "target release is not the recorded previous release", phase="rollback.read_state")
    backup_receipt = validate_backup_receipt(state.get("final_backup"), phase="rollback.read_state")
    previous_runtime = validate_previous_runtime(state.get("previous_runtime"), phase="rollback.read_state")
    backup = _remote(executor, "rollback.verify_backup", {"backup": backup_receipt})
    require(backup.get("compatible") is True, "rollback backup receipt is not compatible", phase="rollback.verify_backup")
    previous = _remote(executor, "rollback.verify_previous", {"previous_runtime": previous_runtime})
    require(
        previous.get("image_ref") == previous_runtime["image_ref"]
        and previous.get("image_digest") == previous_runtime["image_digest"]
        and previous.get("revision") == previous_runtime["revision"],
        "previous runtime image verification failed",
        phase="rollback.verify_previous",
    )
    capture = dict(state.get("capture") or {})
    try:
        _remote(executor, "quiesce.stop_workers", mutation=True)
        _remote(executor, "quiesce.stop_live", mutation=True)
        _remote(executor, "restore.runtime", {"capture": capture, "backup": backup_receipt}, mutation=True)
        _remote(executor, "rollback.start_previous", {"previous_runtime": previous_runtime}, mutation=True)
        verify_runtime(executor, str(previous_runtime["revision"]), f"{config.remote_path}/releases/{config.target_release}/source")
        state_payload = {
            "release_id": config.target_release,
            "current_release": config.target_release,
            "previous_release": str(state.get("current_release")),
            "image": previous_runtime,
            "final_backup": backup_receipt,
            "capture": capture,
            "previous_runtime": dict(state.get("image") or {}),
        }
        _remote(executor, "state.commit", state_payload, mutation=True)
    except TransactionError as exc:
        restore_old_runtime(executor, reason=exc, capture=capture, final_backup=backup_receipt)
        raise
    return {"status": "rolled_back", "current_release": config.target_release, "backup_receipt": backup_receipt}


def dry_run_receipt(args: argparse.Namespace) -> dict[str, object]:
    validate_host_path(args.host, args.remote_path)
    validate_release_id(args.release_id)
    source = source_metadata(args.source_root.resolve(), args.source_remote, args.expected_source_remote)
    return {
        "status": "dry_run",
        "would_mutate": False,
        "release_id": args.release_id,
        "source": {"commit": source["commit"], "branch": source["branch"], "remote_name": source["remote_name"]},
        "target": {"host": args.host, "remote_path": args.remote_path},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    release = subparsers.add_parser("release")
    rollback = subparsers.add_parser("rollback")
    for item in (parser, release):
        item.add_argument("--source-root", type=Path, default=Path.cwd())
        item.add_argument("--host", default=DEFAULT_HOST)
        item.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
        item.add_argument("--release-id", default=f"release-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
        item.add_argument("--source-remote", default="fork")
        item.add_argument("--expected-source-remote", required=False, default="")
        item.add_argument("--expected-current-worker-commit", required=False)
        item.add_argument("--apply", action="store_true")
    rollback.add_argument("--source-root", type=Path, default=Path.cwd())
    rollback.add_argument("--host", default=DEFAULT_HOST)
    rollback.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    rollback.add_argument("--target-release", required=True)
    rollback.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "release"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "rollback":
            if not args.apply:
                payload = {"status": "dry_run", "would_mutate": False, "target_release": args.target_release}
            else:
                payload = run_rollback(
                    RollbackConfig(
                        source_root=args.source_root,
                        host=args.host,
                        remote_path=args.remote_path,
                        target_release=args.target_release,
                        apply=True,
                    ),
                    SSHRemoteExecutor(args.host),
                )
        elif not args.apply:
            payload = dry_run_receipt(args)
        else:
            payload = run_release(
                ReleaseConfig(
                    source_root=args.source_root,
                    release_id=args.release_id,
                    expected_source_remote=args.expected_source_remote,
                    source_remote=args.source_remote,
                    host=args.host,
                    remote_path=args.remote_path,
                    apply=True,
                    expected_current_worker_commit=args.expected_current_worker_commit,
                ),
                SSHRemoteExecutor(args.host),
            )
    except (TransactionError, subprocess.CalledProcessError, OSError) as exc:
        print(f"vcvm release transaction refused: {redact_text(exc)}", file=sys.stderr)
        return 75
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
