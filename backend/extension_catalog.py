"""Comet-derived extension catalog and selectable defaults.

Catalog metadata is config-driven. Resolved on-disk paths come from
``EXTENSION_CATALOG_DIR`` (synced copies) so VCVM never depends on a Mac
Comet profile path. Credentials and host filesystem secrets stay out of API
payloads — only extension ids, names, and relative path hints are returned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.database import DATA_DIR

_REPO_CATALOG = Path(__file__).resolve().parent.parent / "config" / "extension-catalog.json"
_DEFAULTS_FILENAME = "extension-defaults.json"


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


def _resolve_extension_path(ext_id: str) -> str | None:
    root = catalog_dir()
    if not root.exists():
        return None
    direct = root / ext_id
    if (direct / "manifest.json").exists():
        return str(direct.resolve())
    if direct.is_dir():
        versions = sorted(
            (child for child in direct.iterdir() if child.is_dir()),
            key=lambda child: child.name,
        )
        for version in reversed(versions):
            if (version / "manifest.json").exists():
                return str(version.resolve())
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
        path = _resolve_extension_path(ext_id) if include_paths else None
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
            "store_url": None,
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
    selected = set(load_selected_ids())
    paths: list[str] = []
    for item in list_catalog_extensions(include_paths=True):
        if item["id"] in selected and item.get("path"):
            paths.append(str(item["path"]))
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
