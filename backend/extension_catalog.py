"""Comet-derived extension catalog and selectable defaults.

Catalog metadata is config-driven. Resolved on-disk paths come from
``EXTENSION_CATALOG_DIR`` (synced copies) so VCVM never depends on a Mac
Comet profile path. Credentials and host filesystem secrets stay out of API
payloads — only extension ids, names, and relative path hints are returned.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from backend.database import DATA_DIR

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_CATALOG = _REPO_ROOT / "config" / "extension-catalog.json"
_DEFAULTS_FILENAME = "extension-defaults.json"
_CHROME_EXTENSION_ID = re.compile(r"^[a-p]{32}$")


def chrome_web_store_url(ext_id: str) -> str | None:
    """Return the public store page for a valid Chrome extension id."""
    if not _CHROME_EXTENSION_ID.fullmatch(ext_id):
        return None
    return f"https://chromewebstore.google.com/detail/{ext_id}"


def catalog_dir() -> Path:
    configured = (os.environ.get("EXTENSION_CATALOG_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return DATA_DIR / "extension-catalog"


def defaults_path() -> Path:
    return DATA_DIR / _DEFAULTS_FILENAME


def load_catalog_config() -> dict[str, Any]:
    path = Path(os.environ.get("EXTENSION_CATALOG_CONFIG") or _REPO_CATALOG)
    if not path.exists():
        return {"source": "comet", "source_label": "Comet", "extensions": []}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {"source": "comet", "source_label": "Comet", "extensions": []}
    extensions = payload.get("extensions")
    if not isinstance(extensions, list):
        payload["extensions"] = []
    return payload


def _resolve_extension_path(ext_id: str, bundled_path: str | None = None) -> str | None:
    root = catalog_dir()
    if root.exists():
        resolved_root = root.resolve()

        def is_contained(candidate: Path) -> bool:
            try:
                candidate.resolve().relative_to(resolved_root)
            except ValueError:
                return False
            return True

        direct = root / ext_id
        if is_contained(direct) and (direct / "manifest.json").exists():
            return str(direct.resolve())
        if direct.is_dir():
            versions = sorted(
                (child for child in direct.iterdir() if child.is_dir()),
                key=lambda child: child.name,
            )
            for version in reversed(versions):
                if is_contained(version) and (version / "manifest.json").exists():
                    return str(version.resolve())

    if bundled_path:
        candidate = (_REPO_ROOT / bundled_path).resolve()
        try:
            candidate.relative_to(_REPO_ROOT.resolve())
        except ValueError:
            return None
        if (candidate / "manifest.json").is_file():
            return str(candidate)
    return None


def list_catalog_extensions(*, include_paths: bool = True) -> list[dict[str, Any]]:
    config = load_catalog_config()
    rows: list[dict[str, Any]] = []
    for raw in config.get("extensions") or []:
        if not isinstance(raw, dict):
            continue
        ext_id = str(raw.get("id") or "").strip()
        if not ext_id:
            continue
        bundled_path = raw.get("bundled_path") if isinstance(raw.get("bundled_path"), str) else None
        path = _resolve_extension_path(ext_id, bundled_path) if include_paths else None
        rows.append(
            {
                "id": ext_id,
                "name": str(raw.get("name") or ext_id),
                "description": str(raw.get("description") or ""),
                "default_selected": bool(raw.get("default_selected")),
                "tags": [str(tag) for tag in (raw.get("tags") or []) if isinstance(tag, str)],
            "available": bool(path),
            "path": path,
            "icon_url": raw.get("icon_url") if isinstance(raw.get("icon_url"), str) else None,
            "store_url": (
                raw.get("store_url")
                if isinstance(raw.get("store_url"), str)
                else chrome_web_store_url(ext_id)
            ),
            }
        )
    return rows


def _default_selected_ids(catalog: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in catalog if item.get("default_selected")]


def load_selected_ids() -> list[str]:
    catalog = list_catalog_extensions(include_paths=False)
    known = {item["id"] for item in catalog}
    path = defaults_path()
    if not path.exists():
        return _default_selected_ids(catalog)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_selected_ids(catalog)
    selected = payload.get("selected_ids") if isinstance(payload, dict) else None
    if not isinstance(selected, list):
        return _default_selected_ids(catalog)
    return [str(item) for item in selected if str(item) in known]


def save_selected_ids(selected_ids: list[str]) -> list[str]:
    catalog = list_catalog_extensions(include_paths=False)
    known = {item["id"] for item in catalog}
    cleaned = [ext_id for ext_id in selected_ids if ext_id in known]
    # Preserve order, drop duplicates
    cleaned = list(dict.fromkeys(cleaned))
    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"selected_ids": cleaned, "updated_from": "api"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return cleaned


def defaults_payload() -> dict[str, Any]:
    catalog = list_catalog_extensions(include_paths=True)
    selected = set(load_selected_ids())
    extensions = []
    for item in catalog:
        tags = list(item.get("tags") or [])
        entry = {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"] or None,
            "category": tags[0] if tags else None,
            "tags": tags,
            "recommended": bool(item["default_selected"]),
            "default_selected": bool(item["default_selected"]),
            "selectable": True,
            "selected": item["id"] in selected,
            "available": bool(item["available"]),
            "icon_url": item["icon_url"],
            "store_url": item["store_url"],
        }
        if item.get("path"):
            entry["path"] = item["path"]
        extensions.append(entry)
    config = load_catalog_config()
    selected_ids = [item["id"] for item in extensions if item["selected"]]
    return {
        "source": str(config.get("source") or "comet"),
        "source_label": str(config.get("source_label") or "Comet"),
        "catalog_dir_configured": bool(
            (os.environ.get("EXTENSION_CATALOG_DIR") or "").strip() or catalog_dir().exists()
        ),
        "selected_ids": selected_ids,
        "extensions": extensions,
        "items": extensions,  # alias for Chrome sync extension client
        "count": len(extensions),
    }


def selected_load_extension_arg() -> str | None:
    """Build a single ``--load-extension=a,b`` arg from selected available paths."""
    return load_extension_arg_for_ids(load_selected_ids())


def catalog_paths_for_ids(extension_ids: list[str] | None) -> list[str]:
    """Return existing extension directories for known catalog ids only."""
    requested = {str(extension_id) for extension_id in (extension_ids or [])}
    if not requested:
        return []
    paths: list[str] = []
    for item in list_catalog_extensions(include_paths=True):
        path = item.get("path")
        if item["id"] in requested and isinstance(path, str) and path not in paths:
            paths.append(path)
    return paths


def load_extension_arg_for_ids(extension_ids: list[str] | None) -> str | None:
    """Resolve only catalog-owned extension ids into a Chromium launch argument."""
    paths = catalog_paths_for_ids(extension_ids)
    if not paths:
        return None
    return "--load-extension=" + ",".join(paths)


def merge_launch_args_with_defaults(launch_args: list[str] | None) -> list[str]:
    """Ensure selected catalog extensions are present in launch_args (API parity)."""
    args = [str(arg) for arg in (launch_args or [])]
    default_arg = selected_load_extension_arg()
    if not default_arg:
        return args
    # Replace any existing --load-extension=… with the merged selected set.
    without = [arg for arg in args if not arg.startswith("--load-extension=")]
    return without + [default_arg]
