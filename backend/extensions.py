"""Extension manifest parsing and read-only inventory inspection."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal


TrustState = Literal["valid", "untrusted_manifest", "missing_manifest", "invalid_path"]


@dataclass
class ExtensionManifestInfo:
    id: str
    path: str
    name: str
    version: str
    manifest_version: int
    description: str
    permissions: list[str]
    trust_state: TrustState
    error: str | None


def parse_extension_manifest(ext_path_str: str) -> ExtensionManifestInfo:
    """Safely inspect a Chrome/Browser extension directory and parse manifest.json."""
    path = Path(ext_path_str).resolve()
    if not path.exists() or not path.is_dir():
        return ExtensionManifestInfo(
            id=path.name,
            path=str(path),
            name=path.name,
            version="0.0.0",
            manifest_version=0,
            description="",
            permissions=[],
            trust_state="invalid_path",
            error="Extension path does not exist or is not a directory",
        )

    manifest_file = path / "manifest.json"
    if not manifest_file.exists():
        return ExtensionManifestInfo(
            id=path.name,
            path=str(path),
            name=path.name,
            version="0.0.0",
            manifest_version=0,
            description="",
            permissions=[],
            trust_state="missing_manifest",
            error="manifest.json missing in extension directory",
        )

    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return ExtensionManifestInfo(
            id=path.name,
            path=str(path),
            name=path.name,
            version="0.0.0",
            manifest_version=0,
            description="",
            permissions=[],
            trust_state="untrusted_manifest",
            error=f"Invalid manifest JSON: {type(exc).__name__}",
        )

    if not isinstance(data, dict):
        return ExtensionManifestInfo(
            id=path.name,
            path=str(path),
            name=path.name,
            version="0.0.0",
            manifest_version=0,
            description="",
            permissions=[],
            trust_state="untrusted_manifest",
            error="manifest.json top-level JSON must be an object",
        )

    name = str(data.get("name", path.name))
    version = str(data.get("version", "1.0.0"))
    manifest_version_raw = data.get("manifest_version", 2)
    try:
        manifest_version = int(manifest_version_raw)
    except (ValueError, TypeError):
        manifest_version = 2

    description = str(data.get("description", ""))
    permissions_raw = data.get("permissions", [])
    permissions = (
        [str(p) for p in permissions_raw if isinstance(p, (str, int))]
        if isinstance(permissions_raw, list)
        else []
    )

    return ExtensionManifestInfo(
        id=path.name,
        path=str(path),
        name=name,
        version=version,
        manifest_version=manifest_version,
        description=description,
        permissions=permissions,
        trust_state="valid",
        error=None,
    )


def extract_load_extension_paths(launch_args: list[str]) -> list[str]:
    """Extract unique directory paths from --load-extension launch arguments."""
    paths: list[str] = []
    for arg in launch_args:
        if arg.startswith("--load-extension="):
            raw = arg.split("=", 1)[1]
            for p in raw.split(","):
                p_trimmed = p.strip()
                if p_trimmed and p_trimmed not in paths:
                    paths.append(p_trimmed)
    return paths


def inspect_profile_extensions(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Inspect all extensions configured via --load-extension for a profile."""
    launch_args = profile.get("launch_args") or []
    if isinstance(launch_args, str):
        try:
            launch_args = json.loads(launch_args)
        except Exception:
            launch_args = []

    ext_paths = extract_load_extension_paths(launch_args)
    results = []
    for p in ext_paths:
        info = parse_extension_manifest(p)
        results.append(
            {
                "id": info.id,
                "path": info.path,
                "name": info.name,
                "version": info.version,
                "manifest_version": info.manifest_version,
                "description": info.description,
                "permissions": info.permissions,
                "trust_state": info.trust_state,
                "error": info.error,
            }
        )
    return results
