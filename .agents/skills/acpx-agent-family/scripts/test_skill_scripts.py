#!/usr/bin/env python3
"""TDD tests for acpx-agent-family skill scripts (stdlib unittest, no network).

Does not chmod package scripts or delete skill-tree evidence. Prefer
``python3 -B`` so tests do not write bytecode beside sources.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark_skill import (
    ALLOWED_AGENTS,
    NEGATIVE_FIXTURE_NAMES,
    PIPELINE_STAGES,
    find_bytecode_paths,
    fixtures_dir,
    generate_agent_family_plan,
    load_json_fixture,
    mutate_plan_excessive_concurrency,
    mutate_plan_non_acpx,
    mutate_plan_unsafe_execute,
    mutate_plan_wrong_order,
    resolved_temporary_directory,
    run_benchmark,
    score_structure,
    structural_keys_match_golden,
    validate_plan_fixture,
)
from install_and_verify import (
    SKILL_NAME,
    InstallError,
    install_skill,
    reject_symlinks_in_path,
    skill_root,
)


def _resolved_tmp(prefix: str = "acpx-test-") -> tempfile.TemporaryDirectory[str]:
    """Temp dir under canonical OS temp root (macOS /var alias safe)."""
    return resolved_temporary_directory(prefix=prefix)


def _copy_skill_tree(src: Path, dest: Path) -> Path:
    """Copy skill tree for mutation tests (destination is a temp skill root)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=False, ignore_dangling_symlinks=True)
    # Ensure we do not carry over accidental bytecode from the copy source.
    for path in list(dest.rglob("__pycache__")):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in list(dest.rglob("*.pyc")) + list(dest.rglob("*.pyo")):
        try:
            path.unlink()
        except OSError:
            pass
    return dest


class TestInstallAndVerify(unittest.TestCase):
    def test_skill_root_name(self) -> None:
        root = skill_root()
        self.assertEqual(root.name, SKILL_NAME)
        self.assertTrue((root / "SKILL.md").is_file())

    def test_default_is_dry_run_no_write(self) -> None:
        root = skill_root()
        with _resolved_tmp() as tmp:
            dest = Path(tmp).resolve() / "out"
            report = install_skill(destination=dest, source=root, apply=False)
            self.assertEqual(report.mode, "dry-run")
            self.assertEqual(report.copied, [])
            self.assertTrue(report.would_copy)
            self.assertTrue(report.passed)
            skill_dest = dest / SKILL_NAME
            self.assertFalse(skill_dest.exists())
            for rel in report.would_copy:
                self.assertNotIn("__pycache__", rel)
                self.assertFalse(rel.endswith(".pyc"))

    def test_apply_copies_and_verifies_hashes(self) -> None:
        root = skill_root()
        with _resolved_tmp() as tmp:
            dest = Path(tmp).resolve() / "skills"
            report = install_skill(destination=dest, source=root, apply=True)
            self.assertEqual(report.mode, "apply")
            self.assertTrue(report.passed)
            self.assertTrue(report.verified)
            skill_dest = dest / SKILL_NAME
            self.assertTrue((skill_dest / "SKILL.md").is_file())
            self.assertTrue(
                (skill_dest / "scripts" / "install_and_verify.py").is_file()
            )
            src_text = (root / "SKILL.md").read_text(encoding="utf-8")
            dst_text = (skill_dest / "SKILL.md").read_text(encoding="utf-8")
            self.assertEqual(src_text, dst_text)

    def test_apply_is_idempotent(self) -> None:
        root = skill_root()
        with _resolved_tmp() as tmp:
            dest = Path(tmp).resolve() / "skills"
            first = install_skill(destination=dest, source=root, apply=True)
            second = install_skill(destination=dest, source=root, apply=True)
            self.assertTrue(first.passed and first.verified)
            self.assertTrue(second.passed and second.verified)
            self.assertEqual(
                {f.sha256 for f in first.files},
                {f.sha256 for f in second.files},
            )

    def test_rejects_source_symlink_directory(self) -> None:
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            link = base / "linked-skill"
            try:
                link.symlink_to(root, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported")
            dest = base / "dest"
            with self.assertRaises(InstallError) as ctx:
                install_skill(destination=dest, source=link, apply=False)
            self.assertIn("symlink", str(ctx.exception).lower())

    def test_rejects_destination_symlink_parent(self) -> None:
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            real_parent = base / "real-skills"
            real_parent.mkdir()
            link_parent = base / "link-skills"
            try:
                link_parent.symlink_to(real_parent, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported")
            with self.assertRaises(InstallError) as ctx:
                install_skill(
                    destination=link_parent / "nested",
                    source=root,
                    apply=False,
                )
            self.assertIn("symlink", str(ctx.exception).lower())

    def test_explicit_symlink_parent_rejects_resolved_target_succeeds(self) -> None:
        """User symlink parents stay rejected; same resolved real path installs."""
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            real_parent = base / "real-skills"
            real_parent.mkdir()
            link_parent = base / "link-skills"
            try:
                link_parent.symlink_to(real_parent, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported")

            with self.assertRaises(InstallError) as ctx:
                install_skill(
                    destination=link_parent / "nested",
                    source=root,
                    apply=False,
                )
            msg = str(ctx.exception).lower()
            self.assertIn("symlink", msg)
            self.assertIn("canonical", msg)

            # Same physical target via resolved non-symlink path must succeed.
            report = install_skill(
                destination=real_parent / "nested",
                source=root,
                apply=False,
            )
            self.assertTrue(report.passed)
            self.assertEqual(report.mode, "dry-run")
            self.assertTrue(report.would_copy)

    def test_rejects_source_symlink_file_path_escape(self) -> None:
        """A symlink file under a cloned skill tree must fail closed."""
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            fake = _copy_skill_tree(root, base / SKILL_NAME)
            outside = base / "outside-secret.txt"
            outside.write_text("secret-material-not-in-skill\n", encoding="utf-8")
            escape = fake / "scripts" / "escape.py"
            try:
                escape.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks not supported")
            dest = base / "dest"
            with self.assertRaises(InstallError) as ctx:
                install_skill(destination=dest, source=fake, apply=False)
            msg = str(ctx.exception).lower()
            self.assertTrue(
                "symlink" in msg or "escape" in msg,
                msg=str(ctx.exception),
            )

    def test_rejects_symlink_pycache_directory_before_skip(self) -> None:
        """__pycache__ -> outside must fail closed (not skipped as junk name)."""
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            fake = _copy_skill_tree(root, base / SKILL_NAME)
            outside = base / "outside-pycache-target"
            outside.mkdir()
            (outside / "leaked.pyc").write_bytes(b"\0\0")
            pycache_link = fake / "scripts" / "__pycache__"
            try:
                pycache_link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported")
            self.assertTrue(pycache_link.is_symlink())
            dest = base / "dest"
            with self.assertRaises(InstallError) as ctx:
                install_skill(destination=dest, source=fake, apply=False)
            self.assertIn("symlink", str(ctx.exception).lower())
            # Evidence remains (installer must not delete source tree).
            self.assertTrue(pycache_link.is_symlink())
            self.assertTrue((outside / "leaked.pyc").is_file())

    def test_reject_symlinks_in_path_lstats_ancestors(self) -> None:
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            real = base / "real"
            real.mkdir()
            link = base / "lnk"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported")
            with self.assertRaises(InstallError):
                reject_symlinks_in_path(link / "child", label="test")

    def test_rejects_install_into_self(self) -> None:
        root = skill_root()
        with self.assertRaises(InstallError):
            install_skill(destination=root, source=root, apply=False)

    def test_requires_explicit_dest(self) -> None:
        import inspect

        sig = inspect.signature(install_skill)
        self.assertIn("destination", sig.parameters)

    def test_excludes_regular_pycache_from_manifest(self) -> None:
        """Regular (non-symlink) __pycache__ dirs are skipped, not installed."""
        root = skill_root()
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            fake = _copy_skill_tree(root, base / SKILL_NAME)
            cache = fake / "scripts" / "__pycache__"
            cache.mkdir()
            (cache / "mod.cpython-312.pyc").write_bytes(b"\x00pyc")
            report = install_skill(
                destination=base / "out", source=fake, apply=False
            )
            self.assertTrue(report.passed)
            for rel in report.would_copy:
                self.assertNotIn("__pycache__", rel.split("/"))
                self.assertFalse(rel.endswith(".pyc"))
            # Source evidence remains.
            self.assertTrue((cache / "mod.cpython-312.pyc").is_file())


class TestPlanGeneration(unittest.TestCase):
    def test_default_plan_order(self) -> None:
        plan = generate_agent_family_plan(mode="plan")
        names = [s["name"] for s in plan["stages"]]
        self.assertEqual(tuple(names), PIPELINE_STAGES)
        self.assertFalse(plan["would_execute"])
        self.assertEqual(plan["harness"], "acpx")
        self.assertEqual(validate_plan_fixture(plan), [])

    def test_matches_hand_authored_golden(self) -> None:
        golden = load_json_fixture(fixtures_dir() / "golden_plan.json")
        plan = generate_agent_family_plan(mode="plan")
        self.assertEqual(validate_plan_fixture(golden), [])
        self.assertEqual(structural_keys_match_golden(plan, golden), [])

    def test_agent_stages_acpx_only(self) -> None:
        plan = generate_agent_family_plan(
            agent_map={"plan": "opencode", "forge": "grok-build"}
        )
        for stage in plan["stages"]:
            if stage["kind"] == "agent":
                self.assertEqual(stage["harness"], "acpx")
                self.assertIn(stage["agent"], ALLOWED_AGENTS)
                self.assertTrue(stage["route"].startswith("acpx:"))

    def test_rejects_disallowed_agent(self) -> None:
        with self.assertRaises(ValueError):
            generate_agent_family_plan(agent="claude")

    def test_rejects_casual_execute_without_policies(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            generate_agent_family_plan(mode="execute")
        self.assertIn("permission_policy", str(ctx.exception))

    def test_execute_with_absolute_policies(self) -> None:
        with _resolved_tmp() as tmp:
            base = Path(tmp).resolve()
            policy = base / "permission-policy.json"
            mcp = base / "mcp.json"
            policy.write_text(
                json.dumps(
                    {
                        "autoApprove": [],
                        "autoDeny": ["*"],
                        "defaultAction": "deny",
                    }
                ),
                encoding="utf-8",
            )
            mcp.write_text(
                json.dumps({"mcpServers": []}),
                encoding="utf-8",
            )
            plan = generate_agent_family_plan(
                mode="execute",
                permission_policy=policy,
                mcp_config=mcp,
            )
            self.assertTrue(plan["would_execute"])
            self.assertEqual(plan["mode"], "execute")
            self.assertTrue(str(plan["permission_policy"]).startswith("/"))
            self.assertTrue(str(plan["mcp_config"]).startswith("/"))
            self.assertEqual(validate_plan_fixture(plan), [])

    def test_execute_rejects_relative_policy_paths(self) -> None:
        with self.assertRaises(ValueError):
            generate_agent_family_plan(
                mode="execute",
                permission_policy="relative/policy.json",
                mcp_config="relative/mcp.json",
            )

    def test_tribunal_before_release(self) -> None:
        plan = generate_agent_family_plan()
        self.assertIn("release", plan["edges"]["tribunal"])
        self.assertEqual(plan["edges"]["release"], [])
        names = [s["name"] for s in plan["stages"]]
        self.assertLess(names.index("tribunal"), names.index("release"))

    def test_only_auth_benchmark_uses_browser(self) -> None:
        plan = generate_agent_family_plan()
        browsers = [s["name"] for s in plan["stages"] if s["uses_browser"]]
        self.assertEqual(browsers, ["auth-benchmark"])

    def test_negative_fixtures_fail_validation(self) -> None:
        for name in NEGATIVE_FIXTURE_NAMES:
            path = fixtures_dir() / name
            self.assertTrue(path.is_file(), msg=name)
            plan = load_json_fixture(path)
            errors = validate_plan_fixture(plan)
            self.assertTrue(errors, msg=f"{name} should fail, got {errors}")

    def test_mutations_fail_validation(self) -> None:
        good = generate_agent_family_plan(mode="plan")
        for mutator in (
            mutate_plan_wrong_order,
            mutate_plan_non_acpx,
            mutate_plan_excessive_concurrency,
            mutate_plan_unsafe_execute,
        ):
            errors = validate_plan_fixture(mutator(good))
            self.assertTrue(errors, msg=mutator.__name__)


class TestBenchmark(unittest.TestCase):
    def test_scripts_have_shebang_without_mutating_mode(self) -> None:
        """Assert expected script behavior without chmod of package files."""
        root = skill_root()
        for name in ("install_and_verify.py", "benchmark_skill.py"):
            path = root / "scripts" / name
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertTrue(
                text.startswith("#!/usr/bin/env python3"),
                msg=f"{name} missing python3 shebang",
            )
            compile(text, str(path), "exec")
            # Observe mode only — do not mutate.
            mode = path.stat().st_mode
            self.assertTrue(stat.S_ISREG(mode))
            # Product accepts shebang without +x; if +x is set, that is fine too.
            self.assertTrue(mode & stat.S_IRUSR)

    def test_benchmark_passes_on_clean_source(self) -> None:
        root = skill_root()
        # Precondition: clean source must have no bytecode (caller uses python3 -B).
        existing = find_bytecode_paths(root)
        self.assertEqual(
            existing,
            [],
            msg=f"source tree has bytecode before benchmark: {existing}",
        )
        report = run_benchmark(root)
        failed = [s for s in report.scores if not s.passed]
        self.assertTrue(
            report.passed,
            msg=f"benchmark failed: {[(s.name, s.detail) for s in failed]}",
        )
        self.assertEqual(report.total, report.max_total)
        self.assertGreaterEqual(report.total, 8)
        # Benchmark must not create bytecode under the skill tree.
        self.assertEqual(find_bytecode_paths(root), [])

    def test_injected_bytecode_fails_structure_and_remains_present(self) -> None:
        """Temp-copy regression: injected bytecode fails score and is not deleted."""
        root = skill_root()
        with _resolved_tmp() as tmp:
            clone = _copy_skill_tree(root, Path(tmp).resolve() / SKILL_NAME)
            cache = clone / "scripts" / "__pycache__"
            cache.mkdir()
            injected = cache / "evil.cpython-312.pyc"
            injected.write_bytes(b"injected-bytecode-evidence")
            # Structure alone fails.
            structure = score_structure(clone)
            self.assertFalse(structure.passed)
            self.assertIn("bytecode", structure.detail.lower())
            # Full benchmark fails (structure is a required score).
            report = run_benchmark(clone)
            self.assertFalse(report.passed)
            structure_scores = [s for s in report.scores if s.name == "structure"]
            self.assertEqual(len(structure_scores), 1)
            self.assertFalse(structure_scores[0].passed)
            # Evidence must still be present after scoring (no purge).
            self.assertTrue(injected.is_file())
            self.assertTrue(cache.is_dir())
            self.assertIn(
                "scripts/__pycache__/evil.cpython-312.pyc",
                find_bytecode_paths(clone),
            )
            self.assertEqual(
                injected.read_bytes(), b"injected-bytecode-evidence"
            )


if __name__ == "__main__":
    # No chmod of package scripts; no purge of skill-tree evidence.
    raise SystemExit(unittest.main(verbosity=2))
