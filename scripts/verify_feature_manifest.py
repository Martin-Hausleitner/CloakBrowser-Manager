#!/usr/bin/env python3
"""Verify the acceptance feature manifest against source-backed contracts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "acceptance" / "features.yaml"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "ci" / "feature-manifest-smoke"

REQUIRED_FEATURE_IDS = {
    "projects",
    "pinned-profiles",
    "proxy-overview",
    "phone-fit",
    "fullscreen-viewport-controls",
    "sessions",
    "typed-outputs",
    "acpx-selector",
    "extension-catalog",
    "access-dashboard",
    "screenshot-action",
}

REQUIRED_FEATURE_FIELDS = {
    "id",
    "route",
    "role",
    "required_apis",
    "locator",
    "mobile_locator",
    "screenshot_state",
    "feature_flags",
    "forbidden_feature_flags",
}

ALLOWED_ROLES = {"admin", "operator", "viewer"}
SOURCE_DIRS = ("backend", "frontend/src", "scripts")
ENV_FLAG_RE = re.compile(r"\b(VITE_[A-Z0-9_]+|CLOAKBROWSER_FEATURE_[A-Z0-9_]+)\b")
BACKEND_ROUTE_RE = re.compile(r"@app\.(?:get|post|put|patch|delete|websocket)\(\s*[\"']([^\"']+)[\"']")
UI_STATE_RE = re.compile(r"^\s*([A-Za-z0-9_]+):\s*[\"']([^\"']+)[\"'],?\s*$")


@dataclass(frozen=True)
class Feature:
    id: str
    route: str
    role: str
    required_apis: tuple[str, ...]
    locator: str
    mobile_locator: str
    screenshot_state: str
    feature_flags: tuple[str, ...]
    forbidden_feature_flags: tuple[str, ...]


class ManifestError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--smoke-base-url", default=os.environ.get("FEATURE_MANIFEST_SMOKE_BASE_URL"))
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    try:
        result = verify_manifest(args.manifest, args.repo_root)
        if args.smoke_base_url:
            result["smoke"] = run_live_smoke(
                features=result["features"],
                base_url=args.smoke_base_url,
                artifact_dir=args.artifact_dir,
            )
    except ManifestError as exc:
        print(f"feature manifest verification failed: {exc}", file=sys.stderr)
        return 1

    serializable = {
        **result,
        "features": [feature.__dict__ for feature in result["features"]],
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "feature manifest verification passed: "
        f"{len(result['features'])} features, "
        f"{len(result['api_routes'])} API routes, "
        f"{len(result['ui_states'])} UI states",
    )
    return 0


def verify_manifest(manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    manifest = parse_manifest(manifest_path)
    features = [coerce_feature(item, index) for index, item in enumerate(manifest.get("features", []), start=1)]
    if not features:
        raise ManifestError("features must be a non-empty list")

    ids = [feature.id for feature in features]
    duplicate_ids = sorted({feature_id for feature_id in ids if ids.count(feature_id) > 1})
    if duplicate_ids:
        raise ManifestError(f"duplicate feature ids: {', '.join(duplicate_ids)}")

    missing_features = sorted(REQUIRED_FEATURE_IDS.difference(ids))
    if missing_features:
        raise ManifestError(f"missing required feature ids: {', '.join(missing_features)}")

    ui_states_by_key = load_ui_states(repo_root)
    ui_states = set(ui_states_by_key.values())
    mounted_ui_states = collect_mounted_ui_states(repo_root, ui_states_by_key)
    api_routes = collect_backend_routes(repo_root)
    source_text = read_source_text(repo_root)
    declared_env_flags = set(as_string_list(manifest.get("env_flags", []), "env_flags"))
    used_env_flags = set(ENV_FLAG_RE.findall(source_text))
    undeclared_env_flags = sorted(used_env_flags.difference(declared_env_flags))
    if undeclared_env_flags:
        raise ManifestError(f"undeclared env/feature flags in source: {', '.join(undeclared_env_flags)}")

    for feature in features:
        validate_feature(
            feature,
            ui_states,
            mounted_ui_states,
            api_routes,
            source_text,
            declared_env_flags,
        )

    return {
        "features": features,
        "api_routes": sorted(api_routes),
        "ui_states": sorted(ui_states),
        "mounted_ui_states": sorted(mounted_ui_states),
        "env_flags": sorted(declared_env_flags),
    }


def parse_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ManifestError(f"manifest not found: {path}")
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            key, value = split_key_value(line, line_number)
            current_key = key
            current_item = None
            result[key] = parse_value(value)
            continue
        if current_key is None:
            raise ManifestError(f"line {line_number}: indented value before a top-level key")
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:]
            if ":" in value:
                item_key, item_value = split_key_value(value, line_number)
                current_item = {item_key: parse_value(item_value)}
                result.setdefault(current_key, [])
                if not isinstance(result[current_key], list):
                    raise ManifestError(f"line {line_number}: {current_key} must be a list")
                result[current_key].append(current_item)
            else:
                result.setdefault(current_key, [])
                if not isinstance(result[current_key], list):
                    raise ManifestError(f"line {line_number}: {current_key} must be a list")
                result[current_key].append(parse_scalar(value))
                current_item = None
            continue
        if current_item is None:
            raise ManifestError(f"line {line_number}: nested key without a list item")
        item_key, item_value = split_key_value(stripped, line_number)
        current_item[item_key] = parse_value(item_value)
    return result


def split_key_value(line: str, line_number: int) -> tuple[str, str]:
    if ":" not in line:
        raise ManifestError(f"line {line_number}: expected key: value")
    key, value = line.split(":", 1)
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
        raise ManifestError(f"line {line_number}: invalid key {key!r}")
    return key, value.strip()


def parse_value(value: str) -> Any:
    if value == "":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    return parse_scalar(value)


def parse_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def coerce_feature(item: Any, index: int) -> Feature:
    if not isinstance(item, dict):
        raise ManifestError(f"feature #{index} must be a map")
    missing = sorted(REQUIRED_FEATURE_FIELDS.difference(item))
    if missing:
        raise ManifestError(f"feature #{index} missing fields: {', '.join(missing)}")
    extra = sorted(set(item).difference(REQUIRED_FEATURE_FIELDS))
    if extra:
        raise ManifestError(f"feature #{index} has unknown fields: {', '.join(extra)}")
    feature_id = as_non_empty_string(item["id"], f"feature #{index}.id")
    return Feature(
        id=feature_id,
        route=as_route(item["route"], f"{feature_id}.route"),
        role=as_role(item["role"], f"{feature_id}.role"),
        required_apis=tuple(as_string_list(item["required_apis"], f"{feature_id}.required_apis")),
        locator=as_non_empty_string(item["locator"], f"{feature_id}.locator"),
        mobile_locator=as_non_empty_string(item["mobile_locator"], f"{feature_id}.mobile_locator"),
        screenshot_state=as_non_empty_string(item["screenshot_state"], f"{feature_id}.screenshot_state"),
        feature_flags=tuple(as_string_list(item["feature_flags"], f"{feature_id}.feature_flags")),
        forbidden_feature_flags=tuple(as_string_list(item["forbidden_feature_flags"], f"{feature_id}.forbidden_feature_flags")),
    )


def as_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value.strip()


def as_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ManifestError(f"{field} must be a list")
    strings = [as_non_empty_string(item, field) for item in value]
    duplicate = sorted({item for item in strings if strings.count(item) > 1})
    if duplicate:
        raise ManifestError(f"{field} contains duplicates: {', '.join(duplicate)}")
    return strings


def as_role(value: Any, field: str) -> str:
    role = as_non_empty_string(value, field)
    if role not in ALLOWED_ROLES:
        raise ManifestError(f"{field} must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
    return role


def as_route(value: Any, field: str) -> str:
    route = as_non_empty_string(value, field)
    if not route.startswith("/"):
        raise ManifestError(f"{field} must start with /")
    return route


def validate_feature(
    feature: Feature,
    ui_states: set[str],
    mounted_ui_states: set[str],
    api_routes: set[str],
    source_text: str,
    declared_env_flags: set[str],
) -> None:
    if not feature.required_apis:
        raise ManifestError(f"{feature.id}: required_apis cannot be empty")

    for state_field, state in (
        ("locator", feature.locator),
        ("mobile_locator", feature.mobile_locator),
        ("screenshot_state", feature.screenshot_state),
    ):
        if state not in ui_states:
            raise ManifestError(f"{feature.id}: {state_field} {state!r} is not in UI_STATE")
        if state not in mounted_ui_states:
            raise ManifestError(f"{feature.id}: {state_field} {state!r} is not mounted by App/components")

    for api_route in feature.required_apis:
        if api_route not in api_routes:
            raise ManifestError(f"{feature.id}: required API route is not backend-backed: {api_route}")
        if api_route not in source_text:
            raise ManifestError(f"{feature.id}: required API route is not source-backed: {api_route}")

    undeclared_feature_flags = sorted(set(feature.feature_flags).difference(declared_env_flags))
    if undeclared_feature_flags:
        raise ManifestError(f"{feature.id}: feature_flags are not declared in env_flags: {', '.join(undeclared_feature_flags)}")

    forbidden_declared_flags = sorted(set(feature.forbidden_feature_flags).intersection(declared_env_flags))
    if forbidden_declared_flags:
        raise ManifestError(f"{feature.id}: forbidden feature flags are declared: {', '.join(forbidden_declared_flags)}")

    forbidden_used_flags = sorted(flag for flag in feature.forbidden_feature_flags if flag in source_text)
    if forbidden_used_flags:
        raise ManifestError(f"{feature.id}: forbidden feature flags are referenced in source: {', '.join(forbidden_used_flags)}")


def load_ui_states(repo_root: Path) -> dict[str, str]:
    registry = repo_root / "frontend" / "src" / "lib" / "uiFlowRegistry.ts"
    if not registry.exists():
        raise ManifestError(f"UI registry not found: {registry}")
    states: dict[str, str] = {}
    in_object = False
    for line in registry.read_text(encoding="utf-8").splitlines():
        if line.startswith("export const UI_STATE = {"):
            in_object = True
            continue
        if in_object and line.startswith("} as const"):
            break
        if in_object:
            match = UI_STATE_RE.match(line)
            if match:
                states[match.group(1)] = match.group(2)
    if not states:
        raise ManifestError("no UI_STATE entries found")
    return states


def collect_mounted_ui_states(repo_root: Path, ui_states_by_key: dict[str, str]) -> set[str]:
    source_roots = [
        repo_root / "frontend" / "src" / "App.tsx",
        repo_root / "frontend" / "src" / "components",
    ]
    mounted_keys: set[str] = set()
    key_pattern = re.compile(r"\bUI_STATE\.([A-Za-z0-9_]+)\b")
    for source_root in source_roots:
        if source_root.is_file():
            paths = [source_root]
        elif source_root.is_dir():
            paths = sorted(source_root.rglob("*.tsx"))
        else:
            continue
        for path in paths:
            if path.name.endswith(".test.tsx"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            mounted_keys.update(key_pattern.findall(text))
    mounted_states = {
        ui_states_by_key[key]
        for key in mounted_keys
        if key in ui_states_by_key
    }
    if not mounted_states:
        raise ManifestError("no mounted UI_STATE references found in App/components")
    return mounted_states


def collect_backend_routes(repo_root: Path) -> set[str]:
    main_py = repo_root / "backend" / "main.py"
    if not main_py.exists():
        raise ManifestError(f"backend route source not found: {main_py}")
    routes = set(BACKEND_ROUTE_RE.findall(main_py.read_text(encoding="utf-8")))
    if not routes:
        raise ManifestError("no backend API routes found")
    return routes


def read_source_text(repo_root: Path) -> str:
    chunks: list[str] = []
    for source_dir in SOURCE_DIRS:
        root = repo_root / source_dir
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx", ".json"}:
                continue
            if path.name.endswith(".tsbuildinfo") or path.name.startswith("test_") or path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
                continue
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def run_live_smoke(features: list[Feature], base_url: str, artifact_dir: Path) -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ManifestError(
            "live smoke requires Python Playwright; install it only in the smoke job with "
            "`python -m pip install playwright && python -m playwright install chromium`"
        ) from exc

    artifact_dir.mkdir(parents=True, exist_ok=True)
    base_url = base_url.rstrip("/")
    smoke_results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            for feature in features:
                url = f"{base_url}{feature.route}"
                response = page.goto(url, wait_until="networkidle", timeout=15000)
                status = response.status if response is not None else 0
                screenshot_path = artifact_dir / f"{feature.id}.png"
                try:
                    validate_live_smoke_response(feature, url, status, state_found=True)
                    selector = f'[data-ui-state~="{feature.screenshot_state}"]'
                    page.wait_for_selector(selector, timeout=5000)
                except PlaywrightTimeoutError as exc:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    try:
                        validate_live_smoke_response(feature, url, status, state_found=False)
                    except ManifestError as manifest_exc:
                        raise manifest_exc from exc
                except ManifestError:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    raise
                page.screenshot(path=str(screenshot_path), full_page=True)
                smoke_results.append(
                    {
                        "id": feature.id,
                        "route": feature.route,
                        "status": status,
                        "state": feature.screenshot_state,
                        "screenshot": str(screenshot_path),
                    }
                )
        finally:
            browser.close()
        (artifact_dir / "feature-manifest-smoke.json").write_text(
            json.dumps(smoke_results, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return smoke_results


def validate_live_smoke_response(feature: Feature, url: str, status: int, state_found: bool) -> None:
    if status == 404:
        raise ManifestError(f"{feature.id}: smoke route returned 404: {url}")
    if not state_found:
        raise ManifestError(f"{feature.id}: smoke route missing state {feature.screenshot_state!r}: {url}")


def fetch_text(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except urllib.error.URLError as exc:
        raise ManifestError(f"smoke request failed for {url}: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
