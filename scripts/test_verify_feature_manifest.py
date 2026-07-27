from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().with_name("verify_feature_manifest.py")
SPEC = importlib.util.spec_from_file_location("verify_feature_manifest", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def write_manifest(path: Path, body: str) -> Path:
    manifest = path / "features.yaml"
    manifest.write_text(body, encoding="utf-8")
    return manifest


def test_default_manifest_passes_static_verification():
    result = verifier.verify_manifest(verifier.DEFAULT_MANIFEST, verifier.REPO_ROOT)

    assert {feature.id for feature in result["features"]} >= verifier.REQUIRED_FEATURE_IDS
    assert "/api/task-runs/{run_id}/outputs" in result["api_routes"]
    assert "agent.managed-output" in result["ui_states"]
    assert "agent.managed-output" in result["mounted_ui_states"]


def test_manifest_fails_when_required_feature_is_missing(tmp_path: Path):
    manifest = write_manifest(
        tmp_path,
        """
env_flags:
  - VITE_BENCHMARK_REPORT_URL
features:
  - id: projects
    route: /
    role: admin
    required_apis: [/api/projects]
    locator: app.desktop.home
    mobile_locator: mobile.workspace
    screenshot_state: app.desktop.home
    feature_flags: []
    forbidden_feature_flags: []
""".lstrip(),
    )

    with pytest.raises(verifier.ManifestError, match="missing required feature ids"):
        verifier.verify_manifest(manifest, verifier.REPO_ROOT)


def test_manifest_fails_on_missing_api_route(tmp_path: Path):
    body = verifier.DEFAULT_MANIFEST.read_text(encoding="utf-8").replace(
        "/api/projects, /api/profiles",
        "/api/projects, /api/not-a-route",
        1,
    )
    manifest = write_manifest(tmp_path, body)

    with pytest.raises(verifier.ManifestError, match="not backend-backed: /api/not-a-route"):
        verifier.verify_manifest(manifest, verifier.REPO_ROOT)


def test_manifest_fails_on_missing_source_locator(tmp_path: Path):
    body = verifier.DEFAULT_MANIFEST.read_text(encoding="utf-8").replace(
        "locator: app.desktop.home",
        "locator: app.desktop.missing",
        1,
    )
    manifest = write_manifest(tmp_path, body)

    with pytest.raises(verifier.ManifestError, match="not in UI_STATE"):
        verifier.verify_manifest(manifest, verifier.REPO_ROOT)


def test_manifest_fails_on_registry_only_locator():
    feature = verifier.Feature(
        id="registry-only",
        route="/",
        role="viewer",
        required_apis=("/api/status",),
        locator="registry.only",
        mobile_locator="app.desktop.home",
        screenshot_state="app.desktop.home",
        feature_flags=(),
        forbidden_feature_flags=(),
    )

    with pytest.raises(verifier.ManifestError, match="not mounted by App/components"):
        verifier.validate_feature(
            feature,
            ui_states={"registry.only", "app.desktop.home"},
            mounted_ui_states={"app.desktop.home"},
            api_routes={"/api/status"},
            source_text="/api/status",
            declared_env_flags=set(),
        )


def test_manifest_fails_on_undeclared_env_flag(tmp_path: Path):
    body = verifier.DEFAULT_MANIFEST.read_text(encoding="utf-8").replace(
        "feature_flags: []",
        "feature_flags: [VITE_NOT_DECLARED]",
        1,
    )
    manifest = write_manifest(tmp_path, body)

    with pytest.raises(verifier.ManifestError, match="not declared in env_flags"):
        verifier.verify_manifest(manifest, verifier.REPO_ROOT)


def test_live_smoke_contract_fails_404():
    feature = verifier.Feature(
        id="missing",
        route="/missing",
        role="viewer",
        required_apis=("/api/status",),
        locator="app.desktop.home",
        mobile_locator="mobile.workspace",
        screenshot_state="app.desktop.home",
        feature_flags=(),
        forbidden_feature_flags=(),
    )

    with pytest.raises(verifier.ManifestError, match="returned 404"):
        verifier.validate_live_smoke_response(feature, "http://127.0.0.1/missing", 404, True)


def test_live_smoke_contract_fails_absent_state():
    feature = verifier.Feature(
        id="absent",
        route="/",
        role="viewer",
        required_apis=("/api/status",),
        locator="app.desktop.home",
        mobile_locator="mobile.workspace",
        screenshot_state="app.desktop.home",
        feature_flags=(),
        forbidden_feature_flags=(),
    )

    with pytest.raises(verifier.ManifestError, match="missing state 'app.desktop.home'"):
        verifier.validate_live_smoke_response(feature, "http://127.0.0.1/", 200, False)
