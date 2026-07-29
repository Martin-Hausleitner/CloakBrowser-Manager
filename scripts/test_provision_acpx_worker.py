"""Strict TDD tests for secret-safe ACPX worker provisioning."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("provision_acpx_worker.py")
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "systemd" / "cloakbrowser-acpx-worker.service.template"
TOKEN_RE = re.compile(r"^cbm_worker_[0-9a-f]{64}$")
LEAK_RE = re.compile(r"cbm_worker_[0-9a-fA-F]{16,}")


def _load():
    spec = importlib.util.spec_from_file_location("provision_acpx_worker", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _write_executable(path: Path, version: str = "0.12.1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
    os.chmod(path, 0o700)
    return path


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "acpx_worker.py").write_text("# stub\n", encoding="utf-8")
    (repo / "deploy" / "systemd").mkdir(parents=True)
    (repo / "deploy" / "systemd" / TEMPLATE.name).write_text(
        TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return repo


def _write_private_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _paths(tmp_path: Path) -> dict[str, Path | str]:
    case_root = tmp_path / "case"
    case_root.mkdir(mode=0o700)
    os.chmod(case_root, 0o700)
    assert _mode(case_root) == 0o700
    secrets_dir = case_root / "secrets"
    units_dir = case_root / "units"
    secrets_dir.mkdir(mode=0o700)
    units_dir.mkdir(mode=0o700)
    os.chmod(secrets_dir, 0o700)
    os.chmod(units_dir, 0o700)
    assert _mode(secrets_dir) == 0o700
    assert _mode(units_dir) == 0o700

    repo = _make_repo(case_root)
    venv = case_root / "venvs" / "acpx-worker"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    acpx = _write_executable(case_root / "bin" / "acpx")
    policy = _write_private_json(
        case_root / "policy" / "acpx-policy.json",
        {"autoApprove": [], "autoDeny": ["write"], "escalate": ["read"], "defaultAction": "deny"},
    )
    mcp = _write_private_json(
        case_root / "mcp" / "acpx-mcp.json",
        {"mcpServers": [{"name": "cloakbrowser", "command": "cbm-mcp", "args": []}]},
    )
    capability_dir = case_root / "capabilities"
    capability_dir.mkdir(mode=0o700)
    return {
        "repo": repo,
        "manager_url": "http://127.0.0.1:18115",
        "worker_key_file": secrets_dir / "acpx-worker.key",
        "venv": venv,
        "unit_output": units_dir / "cloakbrowser-acpx-worker.service",
        "permission_policy": policy,
        "mcp_config": mcp,
        "capability_dir": capability_dir,
        "acpx": acpx,
    }


def test_render_unit_uses_module_token_file_and_no_placeholder_leftovers(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    key = paths["worker_key_file"]
    key.parent.mkdir(parents=True, exist_ok=True)
    token = "cbm_worker_" + ("12" * 32)
    key.write_text(token + "\n", encoding="utf-8")
    os.chmod(key, 0o600)

    unit = mod.render_systemd_unit(
        repo=paths["repo"],
        venv=paths["venv"],
        manager_url=str(paths["manager_url"]),
        worker_id="acpx-worker",
        worker_key_file=key,
        permission_policy=paths["permission_policy"],
        mcp_config=paths["mcp_config"],
        capability_dir=paths["capability_dir"],
        acpx=paths["acpx"],
        template_path=TEMPLATE,
    )

    exec_line = [line for line in unit.splitlines() if line.startswith("ExecStart=")][0]
    assert "-m scripts.acpx_worker" in unit
    assert "scripts/acpx_worker.py" not in exec_line
    assert "--token-file" in unit
    assert "--token " not in unit.replace("--token-file", "")
    assert token not in unit
    assert LEAK_RE.search(unit) is None
    assert "UMask=0077" in unit
    assert "Restart=on-failure" in unit
    assert "PATH=" in unit
    assert f"Environment=CBM_MCP_PYTHON={paths['venv'] / 'bin' / 'python'}" in unit
    assert str(paths["acpx"]) in unit
    assert str(paths["permission_policy"]) in unit
    assert str(paths["mcp_config"]) in unit
    assert str(paths["capability_dir"]) in unit
    assert "@" not in unit


def test_provision_end_to_end_secret_safe_and_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    mod = _load()
    paths = _paths(tmp_path)

    result1 = mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="acpx-worker",
        permission_policy=paths["permission_policy"],
        mcp_config=paths["mcp_config"],
        capability_dir=paths["capability_dir"],
        acpx=paths["acpx"],
        template_path=TEMPLATE,
    )
    token1 = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    captured1 = capsys.readouterr()
    combined1 = captured1.out + captured1.err + json.dumps(result1)
    assert TOKEN_RE.fullmatch(token1)
    assert token1 not in combined1
    assert LEAK_RE.search(combined1) is None
    assert _mode(paths["worker_key_file"]) == 0o600
    assert _mode(paths["unit_output"]) == 0o600
    unit1 = paths["unit_output"].read_text(encoding="utf-8")
    assert token1 not in unit1
    assert f"Environment=CBM_MCP_PYTHON={paths['venv'] / 'bin' / 'python'}" in unit1
    assert "--token-file" in unit1
    assert result1["version"] == "0.12.1"
    assert result1["worker_id"] == "acpx-worker"
    assert result1["key_mode"] == "0o600"
    assert result1["unit_mode"] == "0o600"

    result2 = mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="acpx-worker",
        permission_policy=paths["permission_policy"],
        mcp_config=paths["mcp_config"],
        capability_dir=paths["capability_dir"],
        acpx=paths["acpx"],
        template_path=TEMPLATE,
    )
    token2 = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    assert token2 == token1
    assert token2 not in json.dumps(result2)
    assert list(tmp_path.glob("**/*.bak*")) == []


def test_dry_run_validates_and_returns_planned_paths_without_writes(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    result = mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="acpx-worker",
        permission_policy=paths["permission_policy"],
        mcp_config=paths["mcp_config"],
        capability_dir=paths["capability_dir"],
        acpx=paths["acpx"],
        template_path=TEMPLATE,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["would_create_key"] is True
    assert result["would_write_unit"] is True
    assert result["version"] == "0.12.1"
    assert not paths["worker_key_file"].exists()
    assert not paths["unit_output"].exists()


def test_requires_absolute_paths_and_loopback_manager_url(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    kwargs = {
        "repo": paths["repo"],
        "manager_url": "http://127.0.0.1:18115",
        "worker_key_file": paths["worker_key_file"],
        "venv": paths["venv"],
        "unit_output": paths["unit_output"],
        "worker_id": "acpx-worker",
        "permission_policy": paths["permission_policy"],
        "mcp_config": paths["mcp_config"],
        "capability_dir": paths["capability_dir"],
        "acpx": paths["acpx"],
        "template_path": TEMPLATE,
        "dry_run": True,
    }
    for key in (
        "repo",
        "worker_key_file",
        "venv",
        "unit_output",
        "permission_policy",
        "mcp_config",
        "capability_dir",
        "acpx",
    ):
        bad = dict(kwargs)
        bad[key] = Path("relative")
        with pytest.raises(ValueError, match="absolute"):
            mod.provision(**bad)

    bad = dict(kwargs)
    bad["manager_url"] = "https://example.com"
    with pytest.raises(ValueError, match="loopback"):
        mod.provision(**bad)


def test_rejects_invalid_worker_ids_and_acpx_versions(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    with pytest.raises(ValueError, match="worker id"):
        mod.provision(
            repo=paths["repo"],
            manager_url=str(paths["manager_url"]),
            worker_key_file=paths["worker_key_file"],
            venv=paths["venv"],
            unit_output=paths["unit_output"],
            worker_id="evil\nCBM_WORKER_TOKEN=nope",
            permission_policy=paths["permission_policy"],
            mcp_config=paths["mcp_config"],
            capability_dir=paths["capability_dir"],
            acpx=paths["acpx"],
            template_path=TEMPLATE,
            dry_run=True,
        )

    wrong_acpx = _write_executable(tmp_path / "bin" / "acpx-wrong", "0.12.2")
    with pytest.raises(ValueError, match="acpx 0.12.1"):
        mod.provision(
            repo=paths["repo"],
            manager_url=str(paths["manager_url"]),
            worker_key_file=paths["worker_key_file"],
            venv=paths["venv"],
            unit_output=paths["unit_output"],
            worker_id="acpx-worker",
            permission_policy=paths["permission_policy"],
            mcp_config=paths["mcp_config"],
            capability_dir=paths["capability_dir"],
            acpx=wrong_acpx,
            template_path=TEMPLATE,
            dry_run=True,
        )


def test_rejects_missing_unsafe_paths_modes_and_symlinks(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    kwargs = {
        "repo": paths["repo"],
        "manager_url": str(paths["manager_url"]),
        "worker_key_file": paths["worker_key_file"],
        "venv": paths["venv"],
        "unit_output": paths["unit_output"],
        "worker_id": "acpx-worker",
        "permission_policy": paths["permission_policy"],
        "mcp_config": paths["mcp_config"],
        "capability_dir": paths["capability_dir"],
        "acpx": paths["acpx"],
        "template_path": TEMPLATE,
        "dry_run": True,
    }

    (paths["repo"] / "scripts" / "acpx_worker.py").unlink()
    with pytest.raises(FileNotFoundError, match="acpx_worker.py"):
        mod.provision(**kwargs)
    (paths["repo"] / "scripts" / "acpx_worker.py").write_text("# stub\n", encoding="utf-8")

    os.chmod(paths["permission_policy"], 0o644)
    with pytest.raises(ValueError, match="0600"):
        mod.provision(**kwargs)
    os.chmod(paths["permission_policy"], 0o600)

    os.chmod(paths["capability_dir"], 0o755)
    with pytest.raises(ValueError, match="0700"):
        mod.provision(**kwargs)
    os.chmod(paths["capability_dir"], 0o700)

    real = tmp_path / "real-key"
    real.write_text("x\n", encoding="utf-8")
    key_link = tmp_path / "key.link"
    key_link.symlink_to(real)
    bad = dict(kwargs)
    bad["worker_key_file"] = key_link
    with pytest.raises(ValueError, match="symlink"):
        mod.provision(**bad)

    mcp_link = tmp_path / "mcp.link"
    mcp_link.symlink_to(paths["mcp_config"])
    bad = dict(kwargs)
    bad["mcp_config"] = mcp_link
    with pytest.raises(ValueError, match="symlink"):
        mod.provision(**bad)


def test_rejects_symlinked_output_parent_before_any_write_in_apply_and_dry_run(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    real_unit_parent = tmp_path / "real-units"
    real_unit_parent.mkdir()
    linked_unit_parent = tmp_path / "linked-units"
    linked_unit_parent.symlink_to(real_unit_parent, target_is_directory=True)
    paths["unit_output"] = linked_unit_parent / "cloakbrowser-acpx-worker.service"

    with pytest.raises(ValueError, match="unit output parent"):
        mod.provision(
            repo=paths["repo"],
            manager_url=str(paths["manager_url"]),
            worker_key_file=paths["worker_key_file"],
            venv=paths["venv"],
            unit_output=paths["unit_output"],
            worker_id="acpx-worker",
            permission_policy=paths["permission_policy"],
            mcp_config=paths["mcp_config"],
            capability_dir=paths["capability_dir"],
            acpx=paths["acpx"],
            template_path=TEMPLATE,
        )
    assert not paths["worker_key_file"].exists()
    assert not paths["unit_output"].exists()
    assert list(real_unit_parent.iterdir()) == []

    with pytest.raises(ValueError, match="unit output parent"):
        mod.provision(
            repo=paths["repo"],
            manager_url=str(paths["manager_url"]),
            worker_key_file=paths["worker_key_file"],
            venv=paths["venv"],
            unit_output=paths["unit_output"],
            worker_id="acpx-worker",
            permission_policy=paths["permission_policy"],
            mcp_config=paths["mcp_config"],
            capability_dir=paths["capability_dir"],
            acpx=paths["acpx"],
            template_path=TEMPLATE,
            dry_run=True,
        )
    assert not paths["worker_key_file"].exists()
    assert not paths["unit_output"].exists()
    assert list(real_unit_parent.iterdir()) == []


def test_cli_prints_secret_safe_json_and_sanitizes_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    mod = _load()
    paths = _paths(tmp_path)
    argv = [
        "--repo",
        str(paths["repo"]),
        "--manager-url",
        str(paths["manager_url"]),
        "--worker-key-file",
        str(paths["worker_key_file"]),
        "--venv",
        str(paths["venv"]),
        "--unit-output",
        str(paths["unit_output"]),
        "--worker-id",
        "acpx-worker",
        "--permission-policy",
        str(paths["permission_policy"]),
        "--mcp-config",
        str(paths["mcp_config"]),
        "--capability-dir",
        str(paths["capability_dir"]),
        "--acpx",
        str(paths["acpx"]),
        "--template",
        str(TEMPLATE),
    ]
    assert mod.main(argv) == 0
    out = capsys.readouterr()
    payload = json.loads(out.out)
    token = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    assert token not in out.out
    assert token not in out.err
    assert out.err == ""
    assert LEAK_RE.search(out.out + out.err) is None
    assert payload["status"] == "ok"
    assert payload["error_code"] is None
    assert payload["message"]
    assert payload["worker_id"] == "acpx-worker"
    assert payload["dry_run"] is False

    bad_argv = argv.copy()
    bad_argv[bad_argv.index("--worker-id") + 1] = "evil\nCBM_WORKER_TOKEN=" + token
    assert mod.main(bad_argv) == 1
    bad = capsys.readouterr()
    assert bad.out == ""
    err_payload = json.loads(bad.err)
    assert err_payload == {
        "status": "error",
        "error_code": "validation_error",
        "message": "provision failed",
    }
    assert token not in bad.err
    assert str(paths["repo"]) not in bad.err
    assert "CBM_WORKER_TOKEN" not in bad.err
    assert LEAK_RE.search(bad.err) is None


def test_provisioner_runs_only_acpx_version_check_for_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mod = _load()
    paths = _paths(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):  # noqa: ANN001
        calls.append([str(part) for part in command])
        if command == [str(paths["acpx"]), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="0.12.1\n", stderr="")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="acpx-worker",
        permission_policy=paths["permission_policy"],
        mcp_config=paths["mcp_config"],
        capability_dir=paths["capability_dir"],
        acpx=paths["acpx"],
        template_path=TEMPLATE,
        dry_run=True,
    )
    assert calls == [[str(paths["acpx"]), "--version"]]
    assert not any({"systemctl", "ssh", "pip", "npm"} & set(call) for call in calls)
