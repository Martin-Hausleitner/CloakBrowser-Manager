import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acpx_runtime_lock.py"


def load_module():
    spec = importlib.util.spec_from_file_location("acpx_runtime_lock", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_runtime_lock_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for relative in (
        "deploy/acpx-runtime/package.json",
        "deploy/acpx-runtime/package-lock.json",
        "scripts/requirements-acpx-worker.in",
        "scripts/requirements-acpx-worker.linux-x86_64.py312.txt",
        "scripts/requirements-acpx-worker.txt",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    return repo


def write_acpx_executable(path: Path, version: str = "0.12.1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def write_fake_venv(path: Path, *, python_version: str = "3.12.10", pip_check: str = "ok") -> Path:
    python = path / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "python_version": python_version,
        "packages": {"mcp": "1.28.1", "playwright": "1.61.0"},
    }
    if pip_check == "ok":
        script = (
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ] && [ \"$3\" = \"check\" ]; then\n"
            "  printf '%s\\n' 'No broken requirements found.'\n"
            "  exit 0\n"
            "fi\n"
            f"cat <<'JSON'\n{json.dumps(payload)}\nJSON\n"
        )
    else:
        script = (
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ] && [ \"$3\" = \"check\" ]; then\n"
            "  printf '%s\\n' 'broken dependency for cbm_worker_abcdef0123456789'\n"
            "  exit 1\n"
            "fi\n"
            f"cat <<'JSON'\n{json.dumps(payload)}\nJSON\n"
        )
    python.write_text(script, encoding="utf-8")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    executable = os.environ.get("PYTHON", sys.executable)
    return subprocess.run(
        [executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def remove_stanza_hashes(lock_text: str, package_name: str) -> str:
    lines = lock_text.splitlines()
    output: list[str] = []
    in_stanza = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "--hash=")) and "==" in stripped:
            in_stanza = stripped.startswith(f"{package_name}==")
        if in_stanza and stripped.startswith("--hash=sha256:"):
            continue
        output.append(line)
    return "\n".join(output) + "\n"


def test_repo_locks_validate_and_emit_secret_safe_json(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    module = load_module()

    receipt = module.verify_repo(repo)

    assert receipt["ok"] is True
    assert receipt["node"]["acpx_version"] == "0.12.1"
    assert receipt["node"]["sdk_version"] == "1.2.1"
    assert receipt["python"]["requirements"] == {"mcp": "1.28.1", "playwright": "1.61.0"}
    assert receipt["python"]["lock_target"] == "linux-x86_64.py312"
    assert "cbm_worker_" not in json.dumps(receipt)


def test_cli_schema_reports_failures_without_secret_leaks(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    token = "cbm_worker_abcdef0123456789abcdef0123456789"
    package_json = repo / "deploy" / "acpx-runtime" / "package.json"
    package_json.write_text(package_json.read_text(encoding="utf-8").replace("0.12.1", token), encoding="utf-8")

    completed = run_cli("--repo", str(repo))

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert set(payload) == {"ok", "errors", "repo", "checks"}
    assert payload["ok"] is False
    assert payload["errors"]
    assert token not in completed.stdout
    assert token not in completed.stderr


def test_cli_uses_sys_executable_unless_python_override_is_supplied(tmp_path: Path, monkeypatch):
    repo = copy_runtime_lock_repo(tmp_path)
    monkeypatch.delenv("PYTHON", raising=False)

    completed = run_cli("--repo", str(repo))

    assert completed.args[0] == sys.executable
    assert completed.returncode == 0

    override = write_acpx_executable(tmp_path / "python-override")
    monkeypatch.setenv("PYTHON", str(override))
    completed = run_cli("--repo", str(repo))

    assert completed.args[0] == str(override)


def test_rejects_tampered_node_versions_integrity_sources_and_missing_integrity(tmp_path: Path):
    module = load_module()
    cases = [
        ("version", ("node_modules/acpx", "version", "0.12.2"), "acpx version"),
        ("integrity", ("node_modules/acpx", "integrity", "sha512-bad"), "acpx integrity"),
        ("source", ("node_modules/acpx", "resolved", "git+https://example.invalid/acpx.git"), "source"),
        ("missing", ("node_modules/@agentclientprotocol/sdk", "integrity", None), "integrity"),
        ("link", ("node_modules/commander", "link", True), "link"),
    ]
    for name, (package_key, field, value), message in cases:
        repo = copy_runtime_lock_repo(tmp_path / name)
        lock_path = repo / "deploy" / "acpx-runtime" / "package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if value is None:
            lock["packages"][package_key].pop(field)
        else:
            lock["packages"][package_key][field] = value
        if name == "link":
            lock["packages"][package_key]["resolved"] = "file:../../outside"
            lock["packages"][package_key].pop("integrity", None)
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

        try:
            module.verify_repo(repo)
        except module.LockVerificationError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"{name} tamper was accepted")


def test_verify_repo_rejects_runtime_and_lock_file_symlink_components(tmp_path: Path):
    module = load_module()

    repo = copy_runtime_lock_repo(tmp_path / "runtime-dir")
    real_runtime = tmp_path / "real-runtime"
    runtime = repo / "deploy" / "acpx-runtime"
    runtime.rename(real_runtime)
    runtime.symlink_to(real_runtime, target_is_directory=True)
    try:
        module.verify_repo(repo)
    except module.LockVerificationError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlinked runtime directory was accepted")

    for filename in ("package.json", "package-lock.json"):
        repo = copy_runtime_lock_repo(tmp_path / filename)
        lock_file = repo / "deploy" / "acpx-runtime" / filename
        real_file = tmp_path / f"real-{filename}"
        real_file.write_text(lock_file.read_text(encoding="utf-8"), encoding="utf-8")
        lock_file.unlink()
        lock_file.symlink_to(real_file)
        try:
            module.verify_repo(repo)
        except module.LockVerificationError as exc:
            assert "symlink" in str(exc)
            assert filename in str(exc)
        else:
            raise AssertionError(f"symlinked {filename} was accepted")


def test_verify_repo_rejects_python_lock_symlink_components(tmp_path: Path):
    module = load_module()

    repo = copy_runtime_lock_repo(tmp_path / "scripts-dir")
    real_scripts = tmp_path / "real-scripts"
    scripts_dir = repo / "scripts"
    scripts_dir.rename(real_scripts)
    scripts_dir.symlink_to(real_scripts, target_is_directory=True)
    try:
        module.verify_repo(repo)
    except module.LockVerificationError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlinked scripts directory was accepted")

    for filename in (
        "requirements-acpx-worker.in",
        "requirements-acpx-worker.linux-x86_64.py312.txt",
        "requirements-acpx-worker.txt",
    ):
        repo = copy_runtime_lock_repo(tmp_path / filename)
        requirements_file = repo / "scripts" / filename
        real_file = tmp_path / f"real-{filename}"
        real_file.write_text(requirements_file.read_text(encoding="utf-8"), encoding="utf-8")
        requirements_file.unlink()
        requirements_file.symlink_to(real_file)
        try:
            module.verify_repo(repo)
        except module.LockVerificationError as exc:
            assert "symlink" in str(exc)
            assert filename in str(exc)
        else:
            raise AssertionError(f"symlinked {filename} was accepted")


def test_rejects_broad_production_requirements_and_wrong_lock_target(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    module = load_module()
    (repo / "scripts" / "requirements-acpx-worker.in").write_text(
        "mcp>=1.28\nplaywright==1.61.0\n",
        encoding="utf-8",
    )
    try:
        module.verify_repo(repo)
    except module.LockVerificationError as exc:
        assert "mcp==1.28.1" in str(exc)
    else:
        raise AssertionError("broad production requirement was accepted")

    repo = copy_runtime_lock_repo(tmp_path / "target")
    (repo / "scripts" / "requirements-acpx-worker.linux-x86_64.py312.txt").rename(
        repo / "scripts" / "requirements-acpx-worker.darwin-arm64.py314.txt"
    )
    try:
        module.verify_repo(repo)
    except module.LockVerificationError as exc:
        assert "linux_x86_64" in str(exc) or "linux-x86_64" in str(exc)
    else:
        raise AssertionError("wrong Python lock target was accepted")


def test_rejects_lock_stanzas_without_following_hashes(tmp_path: Path):
    module = load_module()
    for package_name in ("mcp", "playwright", "attrs"):
        repo = copy_runtime_lock_repo(tmp_path / package_name)
        lock_path = repo / "scripts" / "requirements-acpx-worker.linux-x86_64.py312.txt"
        lock_path.write_text(
            remove_stanza_hashes(lock_path.read_text(encoding="utf-8"), package_name),
            encoding="utf-8",
        )

        try:
            module.verify_repo(repo)
        except module.LockVerificationError as exc:
            assert package_name in str(exc)
            assert "hash" in str(exc)
        else:
            raise AssertionError(f"{package_name} stanza without hashes was accepted")


def test_installed_root_requires_exact_non_symlink_executable_and_version(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    module = load_module()
    installed = repo / "deploy" / "acpx-runtime"
    executable = write_acpx_executable(installed / "node_modules" / "acpx" / "dist" / "cli.js")

    receipt = module.verify_installed_root(repo, installed)

    assert receipt["executable"].endswith("deploy/acpx-runtime/node_modules/acpx/dist/cli.js")
    assert receipt["version"] == "0.12.1"

    executable.unlink()
    target = write_acpx_executable(tmp_path / "real-acpx")
    executable.symlink_to(target)
    try:
        module.verify_installed_root(repo, installed)
    except module.LockVerificationError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink executable was accepted")

    executable.unlink()
    write_acpx_executable(executable, "0.12.2")
    try:
        module.verify_installed_root(repo, installed)
    except module.LockVerificationError as exc:
        assert "0.12.1" in str(exc)
    else:
        raise AssertionError("wrong ACPX version was accepted")


def test_installed_root_rejects_lexical_mismatch_and_symlink_components(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    module = load_module()
    installed = repo / "deploy" / "acpx-runtime"
    write_acpx_executable(installed / "node_modules" / "acpx" / "dist" / "cli.js")

    try:
        module.verify_installed_root(repo, installed / ".." / "acpx-runtime")
    except module.LockVerificationError as exc:
        assert "lexical" in str(exc)
    else:
        raise AssertionError("lexically different installed-root was accepted")

    repo = copy_runtime_lock_repo(tmp_path / "runtime-symlink")
    real_runtime = tmp_path / "external-runtime"
    write_acpx_executable(real_runtime / "node_modules" / "acpx" / "dist" / "cli.js")
    runtime_dir = repo / "deploy" / "acpx-runtime"
    runtime_dir.rename(tmp_path / "unused-runtime-dir")
    runtime_dir.symlink_to(real_runtime, target_is_directory=True)
    try:
        module.verify_installed_root(repo, runtime_dir)
    except module.LockVerificationError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlinked runtime directory was accepted")

    repo = copy_runtime_lock_repo(tmp_path / "intermediate-symlink")
    installed = repo / "deploy" / "acpx-runtime"
    external_acpx = tmp_path / "external-acpx"
    write_acpx_executable(external_acpx / "dist" / "cli.js")
    acpx_dir = installed / "node_modules" / "acpx"
    acpx_dir.parent.mkdir(parents=True, exist_ok=True)
    acpx_dir.symlink_to(external_acpx, target_is_directory=True)
    try:
        module.verify_installed_root(repo, installed)
    except module.LockVerificationError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlinked executable path component was accepted")


def test_venv_requires_python_312_exact_packages_and_pip_check(tmp_path: Path):
    module = load_module()
    venv = write_fake_venv(tmp_path / "venv")

    receipt = module.verify_venv(venv)

    assert receipt["python_version"].startswith("3.12.")
    assert receipt["packages"] == {"mcp": "1.28.1", "playwright": "1.61.0"}
    assert receipt["pip_check"] == "ok"

    wrong_python = write_fake_venv(tmp_path / "wrong-python", python_version="3.13.0")
    try:
        module.verify_venv(wrong_python)
    except module.LockVerificationError as exc:
        assert "Python 3.12" in str(exc)
    else:
        raise AssertionError("wrong Python version was accepted")

    broken = write_fake_venv(tmp_path / "broken", pip_check="broken")
    try:
        module.verify_venv(broken)
    except module.LockVerificationError as exc:
        assert "pip check" in str(exc)
        assert "cbm_worker_" not in str(exc)
    else:
        raise AssertionError("broken pip check was accepted")


def test_verifier_does_not_mutate_repo_or_runtime_paths(tmp_path: Path):
    repo = copy_runtime_lock_repo(tmp_path)
    installed = repo / "deploy" / "acpx-runtime"
    write_acpx_executable(installed / "node_modules" / "acpx" / "dist" / "cli.js")
    venv = write_fake_venv(tmp_path / "venv")
    before = {path.relative_to(tmp_path): path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}

    completed = run_cli("--repo", str(repo), "--installed-root", str(installed), "--venv", str(venv))

    assert completed.returncode == 0
    after = {path.relative_to(tmp_path): path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
