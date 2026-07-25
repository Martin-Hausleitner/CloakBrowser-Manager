"""Strict TDD tests for secret-safe Browser-Use worker provisioning."""

from __future__ import annotations

import importlib.util
import io
import os
import re
import stat
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().with_name("provision_browser_use_worker.py")
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "systemd"
    / "cloakbrowser-browser-use-worker.service.template"
)
REQUIREMENTS = Path(__file__).resolve().with_name("requirements-browser-use-worker.txt")
TOKEN_RE = re.compile(r"^cbm_worker_[0-9a-f]{64}$")
LEAK_RE = re.compile(r"cbm_worker_[0-9a-fA-F]{16,}")


def _load():
    spec = importlib.util.spec_from_file_location("provision_browser_use_worker", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "browser_use_worker.py").write_text("# stub\n", encoding="utf-8")
    (repo / "deploy" / "systemd").mkdir(parents=True)
    if TEMPLATE.is_file():
        (repo / "deploy" / "systemd" / TEMPLATE.name).write_text(
            TEMPLATE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return repo


def _paths(tmp_path: Path) -> dict[str, Path | str]:
    repo = _make_repo(tmp_path)
    return {
        "repo": repo,
        "manager_url": "http://127.0.0.1:18115",
        "worker_env_file": tmp_path / ".env.worker.vcvm",
        "worker_key_file": tmp_path / "secrets" / "worker.key",
        "venv": tmp_path / "venvs" / "browser-use-worker",
        "unit_output": tmp_path / "units" / "cloakbrowser-browser-use-worker.service",
    }


def test_requirements_pin_browser_use_without_pytest():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "browser-use==0.13.6" in text
    assert "pytest" not in text.lower()


def test_generate_worker_token_exact_format():
    mod = _load()
    token = mod.generate_worker_token()
    assert TOKEN_RE.fullmatch(token)
    assert token.startswith("cbm_worker_")
    assert len(token.removeprefix("cbm_worker_")) == 64
    assert token.removeprefix("cbm_worker_") == token.removeprefix("cbm_worker_").lower()
    assert all(ch in "0123456789abcdef" for ch in token.removeprefix("cbm_worker_"))


def test_is_valid_worker_token_accepts_and_rejects():
    mod = _load()
    good = "cbm_worker_" + ("ab" * 32)
    assert mod.is_valid_worker_token(good) is True
    assert mod.is_valid_worker_token("cbm_worker_" + ("AB" * 32)) is False
    assert mod.is_valid_worker_token("cbm_worker_" + ("ab" * 31)) is False
    assert mod.is_valid_worker_token("cbm_agent_" + ("ab" * 32)) is False
    assert mod.is_valid_worker_token("") is False
    assert mod.is_valid_worker_token(None) is False


def test_ensure_key_file_creates_0600_and_never_returns_token(tmp_path: Path):
    mod = _load()
    key_path = tmp_path / "worker.key"
    result = mod.ensure_worker_key_file(key_path)
    assert result is None
    assert key_path.is_file()
    assert _mode(key_path) == 0o600
    token = key_path.read_text(encoding="utf-8").strip()
    assert TOKEN_RE.fullmatch(token)


def test_ensure_key_file_stable_when_valid_exists(tmp_path: Path):
    mod = _load()
    key_path = tmp_path / "worker.key"
    existing = "cbm_worker_" + ("cd" * 32)
    key_path.write_text(existing + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    mod.ensure_worker_key_file(key_path)
    assert key_path.read_text(encoding="utf-8").strip() == existing
    assert _mode(key_path) == 0o600


def test_ensure_key_file_refuses_invalid_existing(tmp_path: Path):
    mod = _load()
    key_path = tmp_path / "worker.key"
    bad = "not-a-valid-worker-token"
    key_path.write_text(bad + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    with pytest.raises(ValueError, match="invalid"):
        mod.ensure_worker_key_file(key_path)
    assert key_path.read_text(encoding="utf-8").strip() == bad


def test_write_worker_env_only_worker_keys_0600_and_backup(tmp_path: Path):
    mod = _load()
    env_path = tmp_path / ".env.worker.vcvm"
    env_path.write_text("STALE=1\nCBM_WORKER_ID=old-id\n", encoding="utf-8")
    env_path.chmod(0o600)
    token = "cbm_worker_" + ("ef" * 32)
    mod.write_worker_env_file(
        env_path,
        worker_id="browser-use-worker",
        worker_token=token,
    )
    text = env_path.read_text(encoding="utf-8")
    assert text.count("CBM_WORKER_ID=") == 1
    assert text.count("CBM_WORKER_TOKEN=") == 1
    assert "CBM_WORKER_ID=browser-use-worker" in text
    assert f"CBM_WORKER_TOKEN={token}" in text
    assert "STALE=" not in text
    assert "AUTH_TOKEN=" not in text
    assert set(line.split("=", 1)[0] for line in text.splitlines() if line.strip()) == {
        "CBM_WORKER_ID",
        "CBM_WORKER_TOKEN",
    }
    assert _mode(env_path) == 0o600
    backups = list(tmp_path.glob(".env.worker.vcvm.bak*"))
    assert len(backups) == 1
    assert "STALE=1" in backups[0].read_text(encoding="utf-8")


def test_write_worker_env_idempotent_same_values(tmp_path: Path):
    mod = _load()
    env_path = tmp_path / ".env.worker.vcvm"
    token = "cbm_worker_" + ("11" * 32)
    env_path.write_text(
        f"CBM_WORKER_ID=browser-use-worker\nCBM_WORKER_TOKEN={token}\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    mod.write_worker_env_file(
        env_path,
        worker_id="browser-use-worker",
        worker_token=token,
    )
    after = env_path.read_text(encoding="utf-8")
    assert after.count("CBM_WORKER_ID=") == 1
    assert after.count("CBM_WORKER_TOKEN=") == 1
    assert "CBM_WORKER_ID=browser-use-worker" in after
    assert f"CBM_WORKER_TOKEN={token}" in after
    assert _mode(env_path) == 0o600
    assert list(tmp_path.glob(".env.worker.vcvm.bak*")) == []


def test_render_unit_uses_cli_flags_no_inline_token(tmp_path: Path):
    mod = _load()
    paths = _paths(tmp_path)
    (paths["venv"] / "bin").mkdir(parents=True)
    (paths["venv"] / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    key = paths["worker_key_file"]
    key.parent.mkdir(parents=True)
    token = "cbm_worker_" + ("22" * 32)
    key.write_text(token + "\n", encoding="utf-8")
    key.chmod(0o600)
    unit = mod.render_systemd_unit(
        repo=paths["repo"],
        venv=paths["venv"],
        manager_url=str(paths["manager_url"]),
        worker_id="browser-use-worker",
        worker_key_file=key,
        template_path=TEMPLATE,
    )
    assert str(paths["repo"]) in unit
    assert f"{paths['venv']}/bin/python" in unit
    assert "scripts/browser_use_worker.py" in unit
    assert "--manager-url" in unit
    assert "http://127.0.0.1:18115" in unit
    assert "--worker-id" in unit
    assert "--token-file" in unit
    assert str(key) in unit
    assert "--token " not in unit.replace("--token-file", "")
    assert token not in unit
    assert "Restart=on-failure" in unit
    assert "UMask=0077" in unit
    assert "WorkingDirectory=" in unit
    assert ".local/bin" in unit
    assert "cursor" not in unit.lower() or "cursor-agent" in unit or ".local/bin" in unit
    assert "BindPaths" not in unit
    assert "/.cursor" not in unit
    assert "CBM_WORKER_TOKEN=" not in unit


def test_provision_end_to_end_secret_safe_and_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    mod = _load()
    paths = _paths(tmp_path)
    (paths["venv"] / "bin").mkdir(parents=True)
    (paths["venv"] / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    result1 = mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_env_file=paths["worker_env_file"],
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="browser-use-worker",
        template_path=TEMPLATE,
    )
    captured = capsys.readouterr()
    combined = captured.out + captured.err + str(result1)
    assert LEAK_RE.search(combined) is None

    key1 = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    assert TOKEN_RE.fullmatch(key1)
    assert _mode(paths["worker_key_file"]) == 0o600
    assert _mode(paths["worker_env_file"]) == 0o600
    env1 = paths["worker_env_file"].read_text(encoding="utf-8")
    assert f"CBM_WORKER_TOKEN={key1}" in env1
    assert "CBM_WORKER_ID=browser-use-worker" in env1
    assert "AUTH_TOKEN=" not in env1
    assert paths["unit_output"].is_file()
    unit_text = paths["unit_output"].read_text(encoding="utf-8")
    assert key1 not in unit_text
    assert "--token-file" in unit_text

    result2 = mod.provision(
        repo=paths["repo"],
        manager_url=str(paths["manager_url"]),
        worker_env_file=paths["worker_env_file"],
        worker_key_file=paths["worker_key_file"],
        venv=paths["venv"],
        unit_output=paths["unit_output"],
        worker_id="browser-use-worker",
        template_path=TEMPLATE,
    )
    key2 = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    assert key2 == key1
    env2 = paths["worker_env_file"].read_text(encoding="utf-8")
    assert env2.count("CBM_WORKER_TOKEN=") == 1
    assert env2.count("CBM_WORKER_ID=") == 1
    captured2 = capsys.readouterr()
    assert LEAK_RE.search(captured2.out + captured2.err + str(result2)) is None


def test_main_requires_explicit_paths_and_hides_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    mod = _load()
    paths = _paths(tmp_path)
    (paths["venv"] / "bin").mkdir(parents=True)
    (paths["venv"] / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    argv = [
        "--repo",
        str(paths["repo"]),
        "--manager-url",
        str(paths["manager_url"]),
        "--worker-env-file",
        str(paths["worker_env_file"]),
        "--worker-key-file",
        str(paths["worker_key_file"]),
        "--venv",
        str(paths["venv"]),
        "--unit-output",
        str(paths["unit_output"]),
    ]
    rc = mod.main(argv)
    assert rc == 0
    out = capsys.readouterr()
    assert LEAK_RE.search(out.out + out.err) is None
    token = paths["worker_key_file"].read_text(encoding="utf-8").strip()
    assert TOKEN_RE.fullmatch(token)
    assert token not in out.out
    assert token not in out.err
    assert paths["worker_env_file"].is_file()
    assert _mode(paths["worker_env_file"]) == 0o600


def test_main_refuses_missing_required_args():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main([])


def test_template_file_exists_and_has_placeholders():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "@WORKING_DIRECTORY@" in text
    assert "@VENV_PYTHON@" in text
    assert "@WORKER_SCRIPT@" in text
    assert "@TOKEN_FILE@" in text
    assert "@PATH_ENVIRONMENT@" in text
    assert "--token-file" in text
    assert "Restart=on-failure" in text
    assert "UMask=0077" in text
    assert "cursor-agent" in text


def test_validate_worker_id_safe_regex_rejects_injection():
    mod = _load()
    assert mod.validate_worker_id("browser-use-worker") == "browser-use-worker"
    assert mod.validate_worker_id("A") == "A"
    assert mod.validate_worker_id("w" + "x" * 63) == "w" + "x" * 63
    for bad in (
        "",
        "-leading-dash",
        "has space",
        "id\nAUTH_TOKEN=pwned",
        "id\nEVIL_FLAG=1",
        "id=value",
        "id;rm",
        "../x",
        "x" * 65,
        "bad\tid",
        "bad\rid",
    ):
        with pytest.raises(ValueError, match="worker id"):
            mod.validate_worker_id(bad)


def test_write_worker_env_rejects_newline_injection_before_write(tmp_path: Path):
    mod = _load()
    env_path = tmp_path / ".env.worker.vcvm"
    token = "cbm_worker_" + ("aa" * 32)
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="worker id"):
        mod.write_worker_env_file(
            env_path,
            worker_id="ok\nAUTH_TOKEN=injected\nEVIL_FLAG=1",
            worker_token=token,
        )
    assert not env_path.exists()
    assert list(tmp_path.iterdir()) == before


def test_main_rejects_invalid_worker_id_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    mod = _load()
    paths = _paths(tmp_path)
    (paths["venv"] / "bin").mkdir(parents=True)
    (paths["venv"] / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    argv = [
        "--repo",
        str(paths["repo"]),
        "--manager-url",
        str(paths["manager_url"]),
        "--worker-env-file",
        str(paths["worker_env_file"]),
        "--worker-key-file",
        str(paths["worker_key_file"]),
        "--venv",
        str(paths["venv"]),
        "--unit-output",
        str(paths["unit_output"]),
        "--worker-id",
        "evil\nAUTH_TOKEN=nope\nEVIL_FLAG=1",
    ]
    rc = mod.main(argv)
    assert rc != 0
    assert not paths["worker_env_file"].exists()
    assert not paths["worker_key_file"].exists()
    assert not paths["unit_output"].exists()
    err = capsys.readouterr().err
    assert LEAK_RE.search(err) is None
    assert "AUTH_TOKEN=nope" not in err


def test_systemd_quote_and_render_preserves_spaces_in_execstart(tmp_path: Path):
    mod = _load()
    repo = tmp_path / "repo with spaces"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "browser_use_worker.py").write_text("# stub\n", encoding="utf-8")
    (repo / "deploy" / "systemd").mkdir(parents=True)
    (repo / "deploy" / "systemd" / TEMPLATE.name).write_text(
        TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    venv = tmp_path / "venv with spaces"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    key = tmp_path / "secrets with spaces" / "worker.key"
    key.parent.mkdir(parents=True)
    key.write_text("cbm_worker_" + ("33" * 32) + "\n", encoding="utf-8")
    key.chmod(0o600)
    home = tmp_path / "home with spaces"
    home.mkdir()

    quoted = mod.systemd_quote(str(venv / "bin" / "python"))
    assert quoted.startswith('"') and quoted.endswith('"')
    assert " " in quoted

    unit = mod.render_systemd_unit(
        repo=repo,
        venv=venv,
        manager_url="http://127.0.0.1:18115",
        worker_id="browser-use-worker",
        worker_key_file=key,
        template_path=TEMPLATE,
        home=home,
    )
    exec_lines = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1
    exec_line = exec_lines[0]
    # Spaces must live inside quotes; flags must remain intact tokens.
    assert "--manager-url" in exec_line
    assert "--worker-id" in exec_line
    assert "--token-file" in exec_line
    assert ' "--manager-url" ' in f" {exec_line} " or "--manager-url" in exec_line
    # Parse argv-like quoted segments: every path with spaces appears inside quotes.
    assert str(venv / "bin" / "python") in exec_line
    assert exec_line.count('"') >= 2
    assert "WorkingDirectory=" in unit
    wd = [ln for ln in unit.splitlines() if ln.startswith("WorkingDirectory=")][0]
    assert "repo with spaces" in wd
    assert wd.startswith('WorkingDirectory="') or " " not in wd.split("=", 1)[1]
    env_line = [ln for ln in unit.splitlines() if ln.startswith("Environment=")][0]
    assert "home with spaces" in env_line
    assert "%" not in unit or "%%" in unit  # bare % must not remain as specifier bait
    # No shell-style unquoted concatenation that would split on spaces before flags.
    assert re.search(r"python\s+/.*scripts/browser_use_worker\.py", exec_line) is None or '"' in exec_line


def test_reject_symlink_targets_for_key_env_unit(tmp_path: Path):
    mod = _load()
    real = tmp_path / "real"
    real.write_text("x\n", encoding="utf-8")
    key_link = tmp_path / "key.link"
    key_link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        mod.ensure_worker_key_file(key_link)

    env_link = tmp_path / "env.link"
    env_link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        mod.write_worker_env_file(
            env_link,
            worker_id="browser-use-worker",
            worker_token="cbm_worker_" + ("44" * 32),
        )

    paths = _paths(tmp_path / "prov")
    (paths["venv"] / "bin").mkdir(parents=True)
    (paths["venv"] / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    unit_link = paths["unit_output"]
    unit_link.parent.mkdir(parents=True, exist_ok=True)
    unit_link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        mod.provision(
            repo=paths["repo"],
            manager_url=str(paths["manager_url"]),
            worker_env_file=paths["worker_env_file"],
            worker_key_file=paths["worker_key_file"],
            venv=paths["venv"],
            unit_output=unit_link,
            worker_id="browser-use-worker",
            template_path=TEMPLATE,
        )


def test_backup_unique_generations_and_idempotent_no_backup(tmp_path: Path):
    mod = _load()
    env_path = tmp_path / ".env.worker.vcvm"
    token1 = "cbm_worker_" + ("55" * 32)
    token2 = "cbm_worker_" + ("66" * 32)
    mod.write_worker_env_file(env_path, worker_id="browser-use-worker", worker_token=token1)
    assert list(tmp_path.glob(".env.worker.vcvm.bak*")) == []

    mod.write_worker_env_file(env_path, worker_id="browser-use-worker", worker_token=token2)
    backups_after_first_change = sorted(tmp_path.glob(".env.worker.vcvm.bak*"))
    assert len(backups_after_first_change) == 1
    assert _mode(backups_after_first_change[0]) == 0o600
    assert f"CBM_WORKER_TOKEN={token1}" in backups_after_first_change[0].read_text(encoding="utf-8")

    mod.write_worker_env_file(env_path, worker_id="worker-b", worker_token=token2)
    backups_after_second_change = sorted(tmp_path.glob(".env.worker.vcvm.bak*"))
    assert len(backups_after_second_change) == 2
    assert backups_after_second_change[0] != backups_after_second_change[1]
    assert all(_mode(p) == 0o600 for p in backups_after_second_change)

    before = set(backups_after_second_change)
    mod.write_worker_env_file(env_path, worker_id="worker-b", worker_token=token2)
    after = set(tmp_path.glob(".env.worker.vcvm.bak*"))
    assert after == before
    assert env_path.read_text(encoding="utf-8") == (
        "CBM_WORKER_ID=worker-b\n"
        f"CBM_WORKER_TOKEN={token2}\n"
    )


def test_manager_url_loopback_origin_only():
    mod = _load()
    assert mod.require_loopback_manager_url("http://127.0.0.1:18115") == "http://127.0.0.1:18115"
    assert mod.require_loopback_manager_url("http://localhost/") == "http://localhost"
    assert mod.require_loopback_manager_url("http://[::1]:8080") == "http://[::1]:8080"
    for bad in (
        "http://127.0.0.1:18115/path",
        "http://user:pass@127.0.0.1:18115",
        "http://127.0.0.1:18115?x=1",
        "http://127.0.0.1:18115#frag",
        "http://example.com:18115",
        "http://127.0.0.1:0",
        "http://127.0.0.1:99999",
        "ftp://127.0.0.1:18115",
    ):
        with pytest.raises(ValueError):
            mod.require_loopback_manager_url(bad)


def test_gitignore_covers_worker_env_backups_and_temps():
    root = Path(__file__).resolve().parents[1]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    assert ".env.worker.vcvm.bak*" in gitignore or ".env.*.bak*" in gitignore
    assert ".env.*.bak*" in gitignore or ".env.worker.vcvm.bak*" in gitignore
    assert ".env.*" in dockerignore
    assert "*.env" in dockerignore
