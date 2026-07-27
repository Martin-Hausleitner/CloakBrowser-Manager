#!/usr/bin/env python3
"""Tests for CBM-022 VCVM release manifest and fail-closed apply gates."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "cbm_release_manifest.py"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_vcvm.sh"
ROLLBACK_SCRIPT = ROOT / "scripts" / "rollback_vcvm_release.sh"
SCHEMA_FILE = ROOT / "docs" / "contracts" / "vcvm-release-v1.json"
AUTHORIZED_FORK = "https://github.com/example/fork.git"
UPSTREAM = "https://github.com/example/upstream.git"

SPEC = importlib.util.spec_from_file_location("cbm_release_manifest", MANIFEST_SCRIPT)
assert SPEC and SPEC.loader
manifest_mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest_mod
SPEC.loader.exec_module(manifest_mod)


def run(
    *args: str,
    cwd: Path = ROOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
        env=env,
    )


def fixture_repo(tmp_path: Path, *, migrations: bool = True, database_migrations: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "backend").mkdir()
    (repo / "frontend").mkdir()
    (repo / "scripts").mkdir()
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "docker-compose.vcvm.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "scripts" / "deploy_vcvm.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (repo / "backend" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "frontend" / "index.html").write_text("<main></main>\n", encoding="utf-8")
    if migrations:
        (repo / "backend" / "migrations").mkdir()
        (repo / "backend" / "migrations" / "001_init.sql").write_text("select 1;\n", encoding="utf-8")
    if database_migrations:
        (repo / "backend" / "database.py").write_text(
            'migration_version = "agent_workspace_v1"\nmigration_version = "task_runs_v1"\n',
            encoding="utf-8",
        )
    run("git", "init", cwd=repo)
    run("git", "checkout", "-b", "release/test", cwd=repo)
    run("git", "config", "user.email", "unit@example.invalid", cwd=repo)
    run("git", "config", "user.name", "Unit Test", cwd=repo)
    run("git", "remote", "add", "origin", UPSTREAM, cwd=repo)
    run("git", "remote", "add", "fork", AUTHORIZED_FORK, cwd=repo)
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "fixture", cwd=repo)
    return repo


def manifest_cmd(repo: Path, *extra: str) -> tuple[str, ...]:
    return (
        sys.executable,
        str(MANIFEST_SCRIPT),
        "--source-root",
        str(repo),
        "--disk-path",
        str(repo),
        "--disk-total-bytes",
        str(20 * 1024**3),
        "--disk-used-bytes",
        str(11 * 1024**3),
        "--disk-free-bytes",
        str(9 * 1024**3),
        "--measurement-source",
        "override",
        "--source-remote",
        "fork",
        "--expected-source-remote",
        AUTHORIZED_FORK,
        "--created-at",
        "2026-07-27T00:00:00Z",
        "--require-migrations",
        *extra,
    )


def fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    log = tmp_path / "commands.log"
    bin_dir.mkdir()
    for name in ("ssh", "rsync", "docker", "jq"):
        tool = bin_dir / name
        tool.write_text(
            f"#!/usr/bin/env bash\nprintf '{name} %s\\n' \"$*\" >> {log}\nexit 42\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
    return bin_dir, log


def env_with_fake_bin(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir, log = fake_bin(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env, log


def test_manifest_schema_exactness_hashes_fork_source_and_measurement_source(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    payload = json.loads(run(*manifest_cmd(repo)).stdout)
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    manifest_schema = schema["spec"]["manifest"]
    assert "artifact_set_sha256" in manifest_schema["required"]
    for object_name in ("source", "target", "disk", "policy"):
        object_schema = manifest_schema["properties"][object_name]
        assert sorted(object_schema["required"]) == sorted(object_schema["properties"])
        assert object_schema["additionalProperties"] is False
    artifact_schema = manifest_schema["properties"]["artifacts"]["items"]
    assert artifact_schema["required"] == ["path", "type", "sha256"]
    assert artifact_schema["additionalProperties"] is False
    migration_schema = manifest_schema["properties"]["migrations"]["items"]
    assert migration_schema["required"] == ["source", "identifier", "sha256"]
    assert migration_schema["additionalProperties"] is False
    assert manifest_schema["properties"]["disk"]["properties"]["free_bytes"]["minimum"] == 8 * 1024**3
    assert manifest_schema["properties"]["disk"]["properties"]["free_gib"]["minimum"] == 8
    assert "(?!.*\\.\\.)" in manifest_schema["properties"]["release_id"]["pattern"]
    assert payload["schema_version"] == "vcvm-release-v1"
    assert payload["release_id"].endswith(payload["source"]["commit"][:12])
    assert payload["source"]["remote_name"] == "fork"
    assert payload["source"]["remote"] == AUTHORIZED_FORK
    assert payload["disk"]["measurement_source"] == "override"
    assert payload["disk"]["free_bytes"] == 9 * 1024**3
    assert len(payload["artifact_set_sha256"]) == 64
    assert payload["migrations"] == [
        {
            "source": "backend/migrations/001_init.sql",
            "identifier": "backend/migrations/001_init.sql",
            "sha256": manifest_mod.hash_file(repo / "backend" / "migrations" / "001_init.sql"),
        }
    ]


def test_source_provenance_requires_named_authorized_fork(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    upstream_only = run(
        *manifest_cmd(repo, "--source-remote", "origin"),
        check=False,
    )
    assert upstream_only.returncode == 75
    assert "expected fork remote" in upstream_only.stderr
    run("git", "remote", "set-url", "fork", "https://user:supersecret@example.invalid/repo.git", cwd=repo)
    credentialed = run(*manifest_cmd(repo), check=False)
    assert credentialed.returncode == 75
    assert "credentials" in credentialed.stderr
    assert "supersecret" not in credentialed.stderr


def test_release_id_rejects_traversal_whitespace_and_unsafe_custom_ids(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    for unsafe_id in ("../release-0001", "release with spaces", "short", "release/token=secretvalue"):
        result = run(*manifest_cmd(repo, "--release-id", unsafe_id), check=False)
        assert result.returncode == 75
        assert "release_id" in result.stderr


def test_remote_name_and_artifact_paths_fail_closed(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    unsafe_remote = run(*manifest_cmd(repo, "--source-remote", "bad/name"), check=False)
    assert unsafe_remote.returncode == 75
    assert "remote name" in unsafe_remote.stderr

    escaped_artifact = run(*manifest_cmd(repo, "--artifact", "../outside"), check=False)
    assert escaped_artifact.returncode == 75
    assert "escapes source root" in escaped_artifact.stderr

    outside = tmp_path / "outside"
    outside.write_text("not part of the source\n", encoding="utf-8")
    symlink = repo / "outside-link"
    symlink.symlink_to(outside)
    run("git", "add", "outside-link", cwd=repo)
    run("git", "commit", "-m", "symlink", cwd=repo)
    symlink_artifact = run(*manifest_cmd(repo, "--artifact", "outside-link"), check=False)
    assert symlink_artifact.returncode == 75
    assert "must not be a symlink" in symlink_artifact.stderr


def test_low_disk_blocks_before_output_mutation(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    output = tmp_path / "manifest.json"
    result = run(
        *manifest_cmd(repo, "--disk-free-bytes", str(7 * 1024**3), "--output", str(output)),
        check=False,
    )
    assert result.returncode == 75
    assert "less than 8 GiB" in result.stderr
    assert not output.exists()


def test_disk_measurement_source_is_explicit_and_vcvm_remote_requires_all_bytes(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    local_with_override = run(
        *manifest_cmd(repo, "--measurement-source", "local"),
        check=False,
    )
    assert local_with_override.returncode == 75
    assert "measurement_source=override or vcvm-remote" in local_with_override.stderr

    missing_remote_bytes = run(
        sys.executable,
        str(MANIFEST_SCRIPT),
        "--source-root",
        str(repo),
        "--source-remote",
        "fork",
        "--measurement-source",
        "vcvm-remote",
        "--require-migrations",
        check=False,
    )
    assert missing_remote_bytes.returncode == 75
    assert "vcvm-remote disk measurement requires" in missing_remote_bytes.stderr

    inconsistent = run(
        *manifest_cmd(
            repo,
            "--disk-total-bytes",
            str(8 * 1024**3),
            "--disk-used-bytes",
            str(9 * 1024**3),
        ),
        check=False,
    )
    assert inconsistent.returncode == 75
    assert "must not exceed total bytes" in inconsistent.stderr

    impossible_sum = run(
        *manifest_cmd(
            repo,
            "--disk-total-bytes",
            str(10 * 1024**3),
            "--disk-used-bytes",
            str(9 * 1024**3),
            "--disk-free-bytes",
            str(9 * 1024**3),
        ),
        check=False,
    )
    assert impossible_sum.returncode == 75
    assert "sum must not exceed total bytes" in impossible_sum.stderr


def test_database_migration_identifiers_are_detected_when_no_migration_directory(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path, migrations=False, database_migrations=True)
    payload = json.loads(run(*manifest_cmd(repo)).stdout)
    assert [item["identifier"] for item in payload["migrations"]] == [
        "agent_workspace_v1",
        "task_runs_v1",
    ]
    assert {item["source"] for item in payload["migrations"]} == {
        "backend/database.py:migration_version"
    }


def test_empty_migration_set_fails_when_required(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path, migrations=False, database_migrations=False)
    result = run(*manifest_cmd(repo), check=False)
    assert result.returncode == 75
    assert "non-empty migration set" in result.stderr

    without_compatibility_flag = [
        item for item in manifest_cmd(repo) if item != "--require-migrations"
    ]
    no_flag_result = run(*without_compatibility_flag, check=False)
    assert no_flag_result.returncode == 75
    assert "non-empty migration set" in no_flag_result.stderr


def test_source_tree_hash_changes_on_artifact_mismatch(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    first = json.loads(run(*manifest_cmd(repo)).stdout)["artifact_set_sha256"]
    (repo / "backend" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-m", "changed", cwd=repo)
    second = json.loads(run(*manifest_cmd(repo)).stdout)["artifact_set_sha256"]
    assert first != second


def test_deploy_apply_is_unavailable_and_does_not_mutate_fake_remote(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    env, log = env_with_fake_bin(tmp_path)
    result = run(
        str(DEPLOY_SCRIPT),
        "--source-root",
        str(repo),
        "--source-remote",
        "fork",
        "--expected-source-remote",
        AUTHORIZED_FORK,
        "--disk-free-bytes",
        str(9 * 1024**3),
        "--apply",
        cwd=repo,
        env=env,
        check=False,
    )
    assert result.returncode == 78
    assert "live VCVM release is unavailable" in result.stderr
    assert "source-hash verification" in result.stderr
    assert "worker/runtime skew checks" in result.stderr
    assert not log.exists()

    dirty_parent = tmp_path / "dirty"
    dirty_parent.mkdir()
    dirty_repo = fixture_repo(dirty_parent)
    (dirty_repo / "backend" / "app.py").write_text("print('dirty')\n", encoding="utf-8")
    dirty_result = run(
        str(DEPLOY_SCRIPT),
        "--source-root",
        str(dirty_repo),
        "--apply",
        cwd=dirty_repo,
        env=env,
        check=False,
    )
    assert dirty_result.returncode == 78
    assert "live VCVM release is unavailable" in dirty_result.stderr
    assert "clean git checkout" not in dirty_result.stderr
    assert not log.exists()


def test_removed_live_flags_are_rejected_not_accepted_as_noops(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    missing_token = run(
        str(DEPLOY_SCRIPT),
        "--source-root",
        str(repo),
        "--auth-token-file",
        "/definitely/missing",
        cwd=repo,
        check=False,
    )
    assert missing_token.returncode == 64
    assert "legacy live-deploy flags" in missing_token.stderr

    serve = run(
        str(DEPLOY_SCRIPT),
        "--source-root",
        str(repo),
        "--serve-private",
        cwd=repo,
        check=False,
    )
    assert serve.returncode == 64
    assert "legacy live-deploy flags" in serve.stderr


def test_deploy_dry_run_has_no_mutation_and_records_override_not_vcvm_exact(tmp_path: Path) -> None:
    source = fixture_repo(tmp_path)
    isolated = tmp_path / "isolated"
    shutil.copytree(source, isolated, ignore=shutil.ignore_patterns(".git"))
    run("git", "init", cwd=isolated)
    run("git", "checkout", "-b", "dry-run", cwd=isolated)
    run("git", "config", "user.email", "unit@example.invalid", cwd=isolated)
    run("git", "config", "user.name", "Unit Test", cwd=isolated)
    run("git", "remote", "add", "fork", AUTHORIZED_FORK, cwd=isolated)
    run("git", "add", ".", cwd=isolated)
    run("git", "commit", "-m", "isolated", cwd=isolated)
    before = sorted(path.relative_to(isolated).as_posix() for path in isolated.rglob("*"))
    result = run(
        str(DEPLOY_SCRIPT),
        "--source-root",
        str(isolated),
        "--source-remote",
        "fork",
        "--expected-source-remote",
        AUTHORIZED_FORK,
        "--disk-free-bytes",
        str(9 * 1024**3),
        cwd=isolated,
    )
    after = sorted(path.relative_to(isolated).as_posix() for path in isolated.rglob("*"))
    assert result.returncode == 0
    assert "DRY RUN:" in result.stdout
    assert "no SSH, rsync, compose, restart, cleanup, prune, or symlink mutation" in result.stdout
    assert before == after


def test_rollback_apply_is_unavailable_and_validates_release_id_before_fake_ssh(tmp_path: Path) -> None:
    env, log = env_with_fake_bin(tmp_path)
    unsafe = run(str(ROLLBACK_SCRIPT), "--target-release", "../bad", "--apply", env=env, check=False)
    assert unsafe.returncode == 64
    assert "unsafe rollback target" in unsafe.stderr
    assert not log.exists()

    result = run(
        str(ROLLBACK_SCRIPT),
        "--target-release",
        "release-0000001",
        "--apply",
        env=env,
        check=False,
    )
    assert result.returncode == 78
    assert "rollback is unavailable" in result.stderr
    assert "backup compatibility" in result.stderr
    assert not log.exists()
