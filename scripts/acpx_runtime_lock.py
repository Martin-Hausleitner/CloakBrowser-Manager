"""Read-only verifier for the pinned ACPX host runtime locks."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ACPX_VERSION = "0.12.1"
SDK_VERSION = "1.2.1"
NODE_ENGINE = ">=22.13.0"
ACPX_INTEGRITY = "sha512-MoV932yPJUcjkX2L9u5TeFncvrxVD+jNTd3ES/tslaiT78S/7CwtgintmX9bonPbSgOB9vinvN+1OCHTQq4HJg=="
SDK_INTEGRITY = "sha512-jwYUdOQR7tc+Zfch53VL4JJyUNK/46q03uUTYb+PjECsmnNl94XFXOfYLJ8RBpMNidXd1rpOAVgb0vqD98xImA=="
PYTHON_REQUIREMENTS = {"mcp": "1.28.1", "playwright": "1.61.0"}
PYTHON_LOCK_NAME = "requirements-acpx-worker.linux-x86_64.py312.txt"
PYTHON_LOCK_TARGET = "linux-x86_64.py312"
UV_EXCLUDE_NEWER = "2026-07-27T00:00:00Z"
SECRET_RE = re.compile(r"cbm_worker_[0-9A-Za-z_=-]+")
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)")


class LockVerificationError(ValueError):
    """Raised for a reproducibility or runtime-lock contract violation."""


def _sanitize(value: object) -> str:
    return SECRET_RE.sub("[REDACTED]", str(value))


def _fail(message: str) -> None:
    raise LockVerificationError(_sanitize(message))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"{path.name} is not readable JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail(f"{path.name} must contain a JSON object")
    return parsed


def _runtime_dir(repo: Path) -> Path:
    return repo / "deploy" / "acpx-runtime"


def _scripts_dir(repo: Path) -> Path:
    return repo / "scripts"


def _require_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        _fail(f"{label} must not be a symlink")
    if not path.is_file():
        _fail(f"{label} is missing")
    return path


def _require_no_symlink_components(root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        _fail(f"{label} must be under repo root")
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            _fail(f"{label} contains symlink path component: {component}")


def _verify_package_json(path: Path) -> dict[str, Any]:
    package = _read_json(_require_file(path, "ACPX package.json"))
    if package.get("private") is not True:
        _fail("ACPX package.json must be private")
    if package.get("dependencies", {}).get("acpx") != ACPX_VERSION:
        _fail(f"ACPX package.json acpx version must be {ACPX_VERSION}")
    if package.get("engines", {}).get("node") != NODE_ENGINE:
        _fail(f"ACPX package.json node engine must be {NODE_ENGINE}")
    if package.get("overrides", {}).get("@agentclientprotocol/sdk") != SDK_VERSION:
        _fail(f"ACPX package.json SDK override must be {SDK_VERSION}")
    return package


def _verify_allowed_source(package_key: str, entry: dict[str, Any]) -> None:
    resolved = entry.get("resolved")
    if resolved is None:
        return
    if not isinstance(resolved, str):
        _fail(f"{package_key} resolved source must be a string")
    parsed = urlparse(resolved)
    if resolved.startswith(("git+", "file:", "file://")) or parsed.scheme in {"git", "ssh", "file"}:
        _fail(f"{package_key} source must not use git/file protocols")
    if parsed.scheme == "http":
        _fail(f"{package_key} source must not use plaintext http")
    if parsed.scheme and parsed.scheme != "https":
        _fail(f"{package_key} source protocol is not allowed")


def _verify_package_integrities(packages: dict[str, Any]) -> None:
    for package_key, entry in packages.items():
        if not isinstance(entry, dict):
            _fail(f"{package_key or '<root>'} package entry must be an object")
        if package_key == "":
            continue
        if entry.get("link") is True:
            _fail(f"{package_key} must not be a link package")
        _verify_allowed_source(package_key or "<root>", entry)
        resolved = entry.get("resolved")
        if isinstance(resolved, str) and resolved.startswith(("file:", "file://")):
            continue
        if "integrity" not in entry:
            _fail(f"{package_key} is missing integrity")
        if not isinstance(entry["integrity"], str) or not entry["integrity"].startswith("sha512-"):
            _fail(f"{package_key} integrity must be sha512")


def _verify_package_lock(path: Path) -> dict[str, Any]:
    lock = _read_json(_require_file(path, "ACPX package-lock.json"))
    if lock.get("lockfileVersion") != 3:
        _fail("ACPX package-lock.json lockfileVersion must be 3")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        _fail("ACPX package-lock.json packages must be an object")
    root = packages.get("")
    if not isinstance(root, dict):
        _fail("ACPX package-lock.json root package is missing")
    if root.get("dependencies", {}).get("acpx") != ACPX_VERSION:
        _fail(f"ACPX lock root dependency must be {ACPX_VERSION}")
    if root.get("engines", {}).get("node") != NODE_ENGINE:
        _fail(f"ACPX lock root node engine must be {NODE_ENGINE}")
    acpx = packages.get("node_modules/acpx")
    sdk = packages.get("node_modules/@agentclientprotocol/sdk")
    if not isinstance(acpx, dict):
        _fail("node_modules/acpx is missing from ACPX lock")
    if not isinstance(sdk, dict):
        _fail("node_modules/@agentclientprotocol/sdk is missing from ACPX lock")
    if acpx.get("version") != ACPX_VERSION:
        _fail(f"acpx version must be {ACPX_VERSION}")
    if acpx.get("integrity") != ACPX_INTEGRITY:
        _fail("acpx integrity does not match reviewed lock")
    if acpx.get("engines", {}).get("node") != NODE_ENGINE:
        _fail(f"acpx node engine must be {NODE_ENGINE}")
    if sdk.get("version") != SDK_VERSION:
        _fail(f"SDK version must be {SDK_VERSION}")
    if sdk.get("integrity") != SDK_INTEGRITY:
        _fail("SDK integrity does not match reviewed lock")
    _verify_package_integrities(packages)
    return lock


def _read_requirement_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in _require_file(path, f"{path.name}").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        match = PIN_RE.match(line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _read_requirement_stanzas(path: Path) -> dict[str, dict[str, Any]]:
    stanzas: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for raw in _require_file(path, f"{path.name}").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = PIN_RE.match(line)
        if match:
            current = match.group(1).lower()
            stanzas[current] = {"version": match.group(2), "hashes": []}
            inline_hashes = HASH_RE.findall(line)
            stanzas[current]["hashes"].extend(inline_hashes)
            continue
        if current is not None:
            stanzas[current]["hashes"].extend(HASH_RE.findall(line))
    return stanzas


def _verify_python_inputs(repo_path: Path) -> dict[str, Any]:
    scripts_dir = _scripts_dir(repo_path)
    requirements_in = scripts_dir / "requirements-acpx-worker.in"
    legacy_path = scripts_dir / "requirements-acpx-worker.txt"
    lock_path = scripts_dir / PYTHON_LOCK_NAME
    for path, label in (
        (requirements_in, "requirements-acpx-worker.in"),
        (lock_path, PYTHON_LOCK_NAME),
        (legacy_path, "requirements-acpx-worker.txt"),
    ):
        _require_no_symlink_components(repo_path, path, label)
    input_pins = _read_requirement_pins(requirements_in)
    if input_pins != PYTHON_REQUIREMENTS:
        _fail("requirements-acpx-worker.in must contain exactly mcp==1.28.1 and playwright==1.61.0")

    legacy = _require_file(legacy_path, "legacy requirements-acpx-worker.txt").read_text(encoding="utf-8")
    if "LEGACY NON-PRODUCTION" not in legacy:
        _fail("legacy requirements-acpx-worker.txt must be marked LEGACY NON-PRODUCTION")
    if "uv pip sync" in legacy:
        _fail("legacy requirements-acpx-worker.txt must not document production sync")

    lock_text = _require_file(lock_path, PYTHON_LOCK_NAME).read_text(encoding="utf-8")
    header = "\n".join(lock_text.splitlines()[:3])
    for required in ("--python-version 3.12", "--python-platform x86_64-manylinux2014", f"--exclude-newer {UV_EXCLUDE_NEWER}"):
        if required not in header:
            _fail(f"{PYTHON_LOCK_NAME} must record {required}")
    lock_stanzas = _read_requirement_stanzas(lock_path)
    lock_pins = {name: str(stanza["version"]) for name, stanza in lock_stanzas.items()}
    for name, version in PYTHON_REQUIREMENTS.items():
        if lock_pins.get(name) != version:
            _fail(f"{PYTHON_LOCK_NAME} must lock {name}=={version}")
    for name, stanza in lock_stanzas.items():
        if not stanza["hashes"]:
            _fail(f"{PYTHON_LOCK_NAME} package {name} must include at least one sha256 hash")
    return {
        "requirements": PYTHON_REQUIREMENTS.copy(),
        "lock_file": PYTHON_LOCK_NAME,
        "lock_target": PYTHON_LOCK_TARGET,
        "exclude_newer": UV_EXCLUDE_NEWER,
    }


def verify_repo(repo: Path | str) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    runtime = _runtime_dir(repo_path)
    package_json = runtime / "package.json"
    package_lock = runtime / "package-lock.json"
    _require_no_symlink_components(repo_path, package_json, "ACPX package.json")
    _require_no_symlink_components(repo_path, package_lock, "ACPX package-lock.json")
    _verify_package_json(package_json)
    _verify_package_lock(package_lock)
    python = _verify_python_inputs(repo_path)
    return {
        "ok": True,
        "repo": str(repo_path),
        "node": {
            "package": "deploy/acpx-runtime/package.json",
            "lock": "deploy/acpx-runtime/package-lock.json",
            "lockfileVersion": 3,
            "node_engine": NODE_ENGINE,
            "acpx_version": ACPX_VERSION,
            "sdk_version": SDK_VERSION,
        },
        "python": python,
    }


def verify_installed_root(repo: Path | str, installed_root: Path | str) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    installed_input = Path(installed_root).expanduser()
    if not installed_input.is_absolute():
        _fail("--installed-root must be an absolute lexical path")
    expected_root = _runtime_dir(repo_path)
    if installed_input != expected_root:
        _fail(f"--installed-root lexical path must be exactly {expected_root}")
    executable = installed_input / "node_modules" / "acpx" / "dist" / "cli.js"
    _require_no_symlink_components(repo_path, executable, "ACPX executable")
    _require_file(executable, "ACPX executable")
    try:
        executable.resolve().relative_to(repo_path)
    except ValueError:
        _fail("ACPX executable resolved path must remain under resolved repo root")
    completed = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    version = (completed.stdout or "").strip()
    if completed.returncode != 0 or version != ACPX_VERSION:
        _fail(f"ACPX executable --version must be {ACPX_VERSION}")
    return {"executable": str(executable), "version": version}


def _run_venv_metadata(python: Path) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m, json, sys; "
        "print(json.dumps({'python_version': '.'.join(map(str, sys.version_info[:3])), "
        "'packages': {'mcp': m.version('mcp'), 'playwright': m.version('playwright')}}))"
    )
    completed = subprocess.run([str(python), "-c", code], check=False, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        _fail(f"venv metadata check failed: {completed.stderr or completed.stdout}")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"venv metadata check returned invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail("venv metadata check must return an object")
    return parsed


def verify_venv(venv: Path | str) -> dict[str, Any]:
    venv_path = Path(venv).expanduser().resolve()
    python = venv_path / "bin" / "python"
    if python.is_symlink():
        _fail("venv python must not be a symlink")
    _require_file(python, "venv python")
    metadata = _run_venv_metadata(python)
    python_version = str(metadata.get("python_version", ""))
    if not python_version.startswith("3.12."):
        _fail("venv Python 3.12 is required")
    packages = metadata.get("packages")
    if packages != PYTHON_REQUIREMENTS:
        _fail("venv packages must be exactly mcp==1.28.1 and playwright==1.61.0")
    pip_check = subprocess.run([str(python), "-m", "pip", "check"], check=False, capture_output=True, text=True, timeout=20)
    if pip_check.returncode != 0:
        _fail(f"pip check failed: {pip_check.stdout or pip_check.stderr}")
    return {"python": str(python), "python_version": python_version, "packages": packages, "pip_check": "ok"}


def verify_all(repo: Path | str, installed_root: Path | None = None, venv: Path | None = None) -> dict[str, Any]:
    receipt = verify_repo(repo)
    checks: dict[str, Any] = {"repo": receipt}
    if installed_root is not None:
        checks["installed_root"] = verify_installed_root(repo, installed_root)
    if venv is not None:
        checks["venv"] = verify_venv(venv)
    return {"ok": True, "errors": [], "repo": receipt["repo"], "checks": checks}


def _error_payload(repo: Path | str, exc: Exception) -> dict[str, Any]:
    return {"ok": False, "errors": [_sanitize(exc)], "repo": str(Path(repo).expanduser()), "checks": {}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--venv", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = verify_all(args.repo, args.installed_root, args.venv)
    except (LockVerificationError, OSError, subprocess.SubprocessError) as exc:
        payload = _error_payload(args.repo, exc)
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
