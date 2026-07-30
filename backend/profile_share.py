"""Safe opt-in profile state transfer for disposable CloakBrowser profiles.

This module intentionally does **not** read Chromium user-data stores
(``Cookies``, ``Login Data``, ``Web Data``, passkey DBs). Only caller-supplied
synthetic cookies bound to local fixture origins may be transferred, and only
when both export and import/clone pass ``opt_in=True``.

Proxy credentials, passwords, tokens, and passkeys are never copied.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from backend import database as db
from backend import extension_catalog
from backend import models as profile_models
from backend.origin_policy import normalize_origin

PROFILE_SHARE_SCHEMA = "cloakbrowser.profile-share.v1"
SYNTHETIC_COOKIE_SIDECAR = "cbm-synthetic-cookies.json"

_SYNTHETIC_COOKIE_NAME_RE = re.compile(r"^cbm_synthetic_[a-z0-9_]{1,64}$")
_SYNTHETIC_COOKIE_VALUE_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_JWT_LIKE_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_FIXTURE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_METADATA_ALLOWLIST = frozenset(
    {
        "name",
        "sandbox_id",
        "project_id",
        "folder_path",
        "pinned",
        "accent_color",
        "harness",
        "fingerprint_seed",
        "timezone",
        "locale",
        "platform",
        "user_agent",
        "screen_width",
        "screen_height",
        "gpu_vendor",
        "gpu_renderer",
        "hardware_concurrency",
        "humanize",
        "human_preset",
        "headless",
        "geoip",
        "clipboard_sync",
        "auto_launch",
        "color_scheme",
        "search_engine",
        "extension_ids",
        "launch_args",
        "notes",
    }
)

_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "proxy",
        "proxy_url",
        "proxy_display",
        "password",
        "passwords",
        "token",
        "tokens",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "secrets",
        "passkey",
        "passkeys",
        "cookie",
        "cookies",
        "user_data_dir",
        "credential",
        "credentials",
    }
)

_EXCLUDED = {
    "proxy_credentials": True,
    "passwords": True,
    "tokens": True,
    "passkeys": True,
    "real_cookies": True,
}


class ProfileShareError(ValueError):
    """Raised when a share export/import/clone violates the safety contract."""


def is_fixture_origin(origin: str) -> bool:
    """Return True only for explicit local HTTP fixture origins (no path/query)."""
    try:
        normalized = normalize_origin(origin)
    except ValueError:
        return False
    parts = urlparse(normalized)
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    return host in _FIXTURE_HOSTS


def validate_synthetic_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one synthetic cookie for fixture-origin transfer."""
    if not isinstance(cookie, dict):
        raise ProfileShareError("synthetic cookie must be an object")

    name = str(cookie.get("name") or "").strip()
    value = str(cookie.get("value") or "").strip()
    domain = str(cookie.get("domain") or "").strip().lower().lstrip(".")
    path = str(cookie.get("path") or "/").strip() or "/"
    origin = str(cookie.get("origin") or "").strip()
    same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "Lax")

    if not _SYNTHETIC_COOKIE_NAME_RE.fullmatch(name):
        raise ProfileShareError("cookie name must be synthetic (cbm_synthetic_*)")
    if not _SYNTHETIC_COOKIE_VALUE_RE.fullmatch(value):
        raise ProfileShareError("cookie value must be synthetic alphanumeric material")
    if _JWT_LIKE_RE.fullmatch(value):
        raise ProfileShareError("forbidden JWT-like cookie value")
    if any(marker in value.lower() for marker in ("password", "secret", "token=", "sk-live")):
        raise ProfileShareError("forbidden secret-like cookie value")
    if not is_fixture_origin(origin):
        raise ProfileShareError("cookie origin must be a local HTTP fixture origin")

    normalized_origin = normalize_origin(origin)
    origin_host = (urlparse(normalized_origin).hostname or "").lower()
    if domain not in {origin_host, f".{origin_host}"} and domain != origin_host:
        # Domain must match fixture host exactly (no public suffix cookies).
        if domain != origin_host:
            raise ProfileShareError("cookie domain must match fixture origin host")

    if path != "/" and (not path.startswith("/") or ".." in path or "\\" in path):
        raise ProfileShareError("cookie path is invalid")

    if same_site not in {"Strict", "Lax", "None"}:
        raise ProfileShareError("cookie sameSite must be Strict, Lax, or None")

    return {
        "name": name,
        "value": value,
        "domain": origin_host,
        "path": path,
        "secure": bool(cookie.get("secure", False)),
        "httpOnly": bool(cookie.get("httpOnly", cookie.get("http_only", False))),
        "sameSite": same_site,
        "origin": normalized_origin,
    }


def _safe_launch_args(raw: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    for item in raw or []:
        if not isinstance(item, str):
            continue
        if profile_models._manager_owned_launch_arg(item):
            continue
        cleaned.append(item)
    return cleaned


def _safe_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for key in _METADATA_ALLOWLIST:
        if key not in profile:
            continue
        if key in _FORBIDDEN_METADATA_KEYS:
            continue
        value = profile[key]
        if key == "extension_ids":
            meta[key] = [str(item) for item in (value or []) if str(item).strip()]
        elif key == "launch_args":
            meta[key] = _safe_launch_args(value if isinstance(value, list) else [])
        elif key == "proxy":
            continue
        else:
            meta[key] = value
    # Hard-ban any accidental secret keys.
    for forbidden in _FORBIDDEN_METADATA_KEYS:
        meta.pop(forbidden, None)
    return meta


def _empty_export() -> dict[str, Any]:
    return {
        "schema": PROFILE_SHARE_SCHEMA,
        "opt_in": False,
        "metadata": {},
        "synthetic_cookies": [],
        "excluded": dict(_EXCLUDED),
        "redacted": True,
    }


def export_profile_share(
    profile: dict[str, Any],
    *,
    opt_in: bool = False,
    synthetic_cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Export a redacted share package. State transfer requires ``opt_in=True``.

    Synthetic cookies are taken only from the explicit caller list — never from
    on-disk Chromium profile stores.
    """
    if not opt_in:
        return _empty_export()

    if not isinstance(profile, dict) or not profile.get("id"):
        raise ProfileShareError("profile snapshot is required")

    cookies: list[dict[str, Any]] = []
    for raw in synthetic_cookies or []:
        cookies.append(validate_synthetic_cookie(raw))

    return {
        "schema": PROFILE_SHARE_SCHEMA,
        "opt_in": True,
        "source_profile_id": str(profile["id"]),
        "metadata": _safe_metadata(profile),
        "synthetic_cookies": cookies,
        "excluded": dict(_EXCLUDED),
        "redacted": True,
    }


def redact_share_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of a share payload with secrets stripped."""
    if not isinstance(payload, dict):
        raise ProfileShareError("share payload must be an object")

    metadata_in = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    safe_meta = {
        key: value
        for key, value in metadata_in.items()
        if key in _METADATA_ALLOWLIST and key not in _FORBIDDEN_METADATA_KEYS
    }
    if "launch_args" in safe_meta:
        safe_meta["launch_args"] = _safe_launch_args(
            safe_meta["launch_args"] if isinstance(safe_meta["launch_args"], list) else []
        )
    if "extension_ids" in safe_meta:
        safe_meta["extension_ids"] = [
            str(item) for item in (safe_meta["extension_ids"] or []) if str(item).strip()
        ]

    cookies: list[dict[str, Any]] = []
    for raw in payload.get("synthetic_cookies") or []:
        if isinstance(raw, dict):
            cookies.append(validate_synthetic_cookie(raw))

    return {
        "schema": PROFILE_SHARE_SCHEMA,
        "opt_in": bool(payload.get("opt_in")),
        "source_profile_id": (
            str(payload["source_profile_id"])
            if payload.get("source_profile_id")
            else None
        ),
        "metadata": safe_meta,
        "synthetic_cookies": cookies,
        "excluded": dict(_EXCLUDED),
        "redacted": True,
    }


def _write_synthetic_cookie_sidecar(
    user_data_dir: str | Path, cookies: list[dict[str, Any]]
) -> Path | None:
    if not cookies:
        return None
    root = Path(user_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / SYNTHETIC_COOKIE_SIDECAR
    path.write_text(json.dumps(cookies, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def import_profile_share(
    payload: dict[str, Any],
    *,
    opt_in: bool = False,
    create_profile: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new disposable profile from an opt-in share package."""
    if not opt_in:
        raise ProfileShareError("import requires explicit opt-in")

    clean = redact_share_payload(payload)
    if not clean.get("opt_in"):
        raise ProfileShareError("import requires opt-in share payload")

    metadata = dict(clean.get("metadata") or {})
    extension_ids = list(metadata.pop("extension_ids", []) or [])
    launch_args = _safe_launch_args(metadata.pop("launch_args", []) or [])
    source_name = str(metadata.pop("name", "") or "profile")
    # Always mint a new disposable name; never reuse source identity blindly.
    name = f"share-import-{source_name}"[:80]
    fingerprint_seed = metadata.pop("fingerprint_seed", None)

    creator = create_profile or db.create_profile
    created = creator(
        name=name,
        fingerprint_seed=fingerprint_seed,
        extension_ids=extension_ids,
        launch_args=launch_args,
        proxy=None,  # never import proxy credentials
        **{
            key: value
            for key, value in metadata.items()
            if key in _METADATA_ALLOWLIST
            and key
            not in {
                "name",
                "fingerprint_seed",
                "extension_ids",
                "launch_args",
                "proxy",
            }
        },
    )
    _write_synthetic_cookie_sidecar(
        created["user_data_dir"], list(clean.get("synthetic_cookies") or [])
    )
    return created


def clone_profile(
    profile_id: str,
    *,
    opt_in: bool = False,
    synthetic_cookies: list[dict[str, Any]] | None = None,
    get_profile: Callable[[str], dict[str, Any] | None] | None = None,
    create_profile: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Clone profile metadata into a new disposable profile with opt-in state."""
    if not opt_in:
        raise ProfileShareError("clone requires explicit opt-in")

    loader = get_profile or db.get_profile
    source = loader(profile_id)
    if not source:
        raise ProfileShareError("source profile not found")

    payload = export_profile_share(
        source,
        opt_in=True,
        synthetic_cookies=synthetic_cookies,
    )
    # Prefer clone naming over import naming.
    payload["metadata"] = {
        **payload["metadata"],
        "name": f"share-clone-{source.get('name') or 'profile'}"[:72],
    }
    created = import_profile_share(
        payload,
        opt_in=True,
        create_profile=create_profile,
    )
    # import_profile_share prefixes with share-import-; normalize clone name.
    desired = f"share-clone-{source.get('name') or 'profile'}"[:80]
    if created.get("name") != desired:
        updated = db.update_profile(created["id"], name=desired)
        if updated is not None:
            return updated
    return created


def _path_digest(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def _redact_load_extension_arg(load_arg: str | None) -> str | None:
    """Replace absolute extension paths with digests for evidence payloads."""
    if not load_arg or not load_arg.startswith("--load-extension="):
        return load_arg
    raw = load_arg.split("=", 1)[1]
    digests = [_path_digest(part.strip()) for part in raw.split(",") if part.strip()]
    if not digests:
        return "--load-extension="
    return "--load-extension=" + ",".join(f"sha256:{item}" for item in digests)


def build_extension_launch_evidence(
    profile: dict[str, Any],
    *,
    include_raw_paths: bool = False,
) -> dict[str, Any]:
    """Return redacted evidence that catalog extension IDs resolve into launch args.

    Absolute host paths are digested by default so evidence artifacts never
    embed machine-local filesystem locations. Pass ``include_raw_paths=True``
    only for in-process assertions that never leave the test process.
    """
    extension_ids = [str(item) for item in (profile.get("extension_ids") or [])]
    paths = extension_catalog.catalog_paths_for_ids(extension_ids)
    load_arg = extension_catalog.load_extension_arg_for_ids(extension_ids)

    # Compose the same safe launch surface BrowserManager uses (without CDP/proxy).
    launch_args = _safe_launch_args(profile.get("launch_args") or [])
    if load_arg:
        launch_args = [arg for arg in launch_args if not arg.startswith("--load-extension=")]
        launch_args.append(load_arg)

    redacted_load = _redact_load_extension_arg(load_arg)
    redacted_launch_args = [
        _redact_load_extension_arg(arg) if arg.startswith("--load-extension=") else arg
        for arg in launch_args
    ]

    evidence: dict[str, Any] = {
        "profile_id": str(profile.get("id") or ""),
        "extension_ids": extension_ids,
        "resolved_path_digests": [_path_digest(path) for path in paths],
        "resolved_path_count": len(paths),
        "load_extension_arg": redacted_load,
        "launch_args": redacted_launch_args,
        "extensions_resolved": bool(paths) and load_arg is not None,
        "redacted": True,
    }
    if include_raw_paths:
        # In-process only — never write this branch into reports or CLI stdout.
        evidence["resolved_paths"] = paths
        evidence["load_extension_arg_raw"] = load_arg
    return evidence


def cleanup_disposable_profile(
    profile_id: str,
    *,
    remove_user_data: bool = True,
    get_profile: Callable[[str], dict[str, Any] | None] | None = None,
    delete_profile: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Delete a disposable profile row and optionally its user_data_dir tree."""
    loader = get_profile or db.get_profile
    deleter = delete_profile or db.delete_profile
    profile = loader(profile_id)
    if profile is None:
        return {
            "deleted": False,
            "user_data_removed": False,
            "profile_id": profile_id,
        }

    user_data_dir = Path(str(profile.get("user_data_dir") or ""))
    deleted = bool(deleter(profile_id))
    user_data_removed = False
    if remove_user_data and user_data_dir and str(user_data_dir) not in {"", ".", "/"}:
        if user_data_dir.exists():
            shutil.rmtree(user_data_dir, ignore_errors=True)
            user_data_removed = not user_data_dir.exists()
        else:
            user_data_removed = True

    return {
        "deleted": deleted,
        "user_data_removed": user_data_removed,
        "profile_id": profile_id,
        "redacted": True,
    }


def load_synthetic_cookies(user_data_dir: str | Path) -> list[dict[str, Any]]:
    """Load synthetic cookie sidecar only (never Chromium Cookies DB)."""
    path = Path(user_data_dir) / SYNTHETIC_COOKIE_SIDECAR
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileShareError(f"invalid synthetic cookie sidecar: {type(exc).__name__}") from exc
    if not isinstance(raw, list):
        raise ProfileShareError("synthetic cookie sidecar must be a list")
    return [validate_synthetic_cookie(item) for item in raw if isinstance(item, dict)]
