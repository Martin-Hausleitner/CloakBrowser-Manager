"""Safe opt-in profile state transfer for disposable CloakBrowser profiles.

This module intentionally does **not** read Chromium user-data stores
(``Cookies``, ``Login Data``, ``Web Data``, passkey DBs). Only caller-supplied
synthetic cookies bound to local fixture origins may be transferred, and only
when both export and import/clone pass ``opt_in=True``.

Proxy credentials, passwords, tokens, passkeys, free-form notes, and arbitrary
launch flags are never copied. Extension load paths are resolved only through
``BrowserManager._build_profile_launch_args``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
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

# Only these Chromium user-set flags may travel in a share package.
# Extension loading is never transferred as a free-form flag; catalog IDs are.
SAFE_LAUNCH_ARG_PREFIXES: tuple[str, ...] = (
    "--lang=",
    "--accept-lang=",
)

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
        # notes intentionally excluded — free-form text is a secret leak surface
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
        "notes",
        "note",
        "authorization",
        "bearer",
        "header",
        "headers",
    }
)

_SECRET_TEXT_RE = re.compile(
    r"(?i)("
    r"\bbearer\s+[A-Za-z0-9\-._~+/]+=*"
    r"|\bauthorization\s*:\s*\S+"
    r"|\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*\S+"
    r"|\bsk-live-[A-Za-z0-9]+"
    r"|\b(?:https?|socks5?)://[^/\s\"']+:[^/\s\"']+@"
    r"|\bset-cookie\s*:"
    r")"
)

_EXCLUDED = {
    "proxy_credentials": True,
    "passwords": True,
    "tokens": True,
    "passkeys": True,
    "real_cookies": True,
    "notes": True,
    "arbitrary_launch_args": True,
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


def _contains_secret_like_text(value: Any) -> bool:
    """Recursively detect Bearer/header/password/token-like material."""
    if value is None:
        return False
    if isinstance(value, str):
        if _SECRET_TEXT_RE.search(value):
            return True
        return bool(_JWT_LIKE_RE.fullmatch(value.strip()))
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if any(part in key_l for part in _FORBIDDEN_METADATA_KEYS):
                return True
            if _contains_secret_like_text(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_secret_like_text(item) for item in value)
    return False


def _reject_secret_like(value: Any, *, field: str) -> Any:
    if _contains_secret_like_text(value):
        raise ProfileShareError(f"secret-like text rejected in {field}")
    return value


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
    secure = bool(cookie.get("secure", False))

    if not _SYNTHETIC_COOKIE_NAME_RE.fullmatch(name):
        raise ProfileShareError("cookie name must be synthetic (cbm_synthetic_*)")
    if not _SYNTHETIC_COOKIE_VALUE_RE.fullmatch(value):
        raise ProfileShareError("cookie value must be synthetic alphanumeric material")
    if _JWT_LIKE_RE.fullmatch(value):
        raise ProfileShareError("forbidden JWT-like cookie value")
    if any(marker in value.lower() for marker in ("password", "secret", "token=", "sk-live")):
        raise ProfileShareError("forbidden secret-like cookie value")
    _reject_secret_like(value, field="cookie.value")

    try:
        normalized_origin = normalize_origin(origin)
    except ValueError as exc:
        raise ProfileShareError("cookie origin must be a local HTTP fixture origin") from exc

    origin_parts = urlparse(normalized_origin)
    origin_host = (origin_parts.hostname or "").lower()
    if origin_host not in _FIXTURE_HOSTS:
        raise ProfileShareError("cookie origin must be a local HTTP fixture origin")

    # SameSite=None requires Secure + HTTPS. HTTP fixture origins must reject it.
    if same_site not in {"Strict", "Lax", "None"}:
        raise ProfileShareError("cookie sameSite must be Strict, Lax, or None")
    if same_site == "None" and (not secure or origin_parts.scheme != "https"):
        raise ProfileShareError(
            "SameSite=None requires Secure=true and an HTTPS origin"
        )

    # Local HTTP fixtures are the only transfer surface today.
    if origin_parts.scheme != "http":
        raise ProfileShareError("cookie origin must be a local HTTP fixture origin")
    if not is_fixture_origin(normalized_origin):
        raise ProfileShareError("cookie origin must be a local HTTP fixture origin")

    if domain != origin_host:
        raise ProfileShareError("cookie domain must match fixture origin host")

    if path != "/" and (not path.startswith("/") or ".." in path or "\\" in path):
        raise ProfileShareError("cookie path is invalid")

    return {
        "name": name,
        "value": value,
        "domain": origin_host,
        "path": path,
        "secure": secure,
        "httpOnly": bool(cookie.get("httpOnly", cookie.get("http_only", False))),
        "sameSite": same_site,
        "origin": normalized_origin,
    }


def _is_safe_launch_arg_prefix(arg: str) -> bool:
    return any(arg.startswith(prefix) for prefix in SAFE_LAUNCH_ARG_PREFIXES)


def _safe_launch_args(raw: list[Any] | None) -> list[str]:
    """Keep only allowlisted launch-arg prefixes free of secret-like text."""
    cleaned: list[str] = []
    for item in raw or []:
        if not isinstance(item, str):
            continue
        arg = item.strip()
        if not arg:
            continue
        if profile_models._manager_owned_launch_arg(arg):
            continue
        if not _is_safe_launch_arg_prefix(arg):
            continue
        if _contains_secret_like_text(arg):
            continue
        cleaned.append(arg)
    return cleaned


def _safe_scalar(value: Any, *, field: str) -> Any:
    """Allow only simple non-secret scalars for transferable metadata fields."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _reject_secret_like(value, field=field)
    # Nested free-form structures are not transferable.
    raise ProfileShareError(f"unsupported metadata type for {field}")


def _safe_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    """Export only allowlisted, non-secret profile fields.

    Free-form ``notes`` and arbitrary ``launch_args`` are never exported.
    """
    meta: dict[str, Any] = {}
    for key in sorted(_METADATA_ALLOWLIST):
        if key not in profile:
            continue
        if key in _FORBIDDEN_METADATA_KEYS:
            continue
        value = profile[key]
        if key == "extension_ids":
            ids = [str(item).strip() for item in (value or []) if str(item).strip()]
            _reject_secret_like(ids, field="extension_ids")
            meta[key] = ids
        elif key == "launch_args":
            meta[key] = _safe_launch_args(value if isinstance(value, list) else [])
        else:
            try:
                meta[key] = _safe_scalar(value, field=key)
            except ProfileShareError:
                # Drop unsafe field rather than fail entire export for one bad scalar.
                continue

    for forbidden in _FORBIDDEN_METADATA_KEYS:
        meta.pop(forbidden, None)
    meta.pop("notes", None)
    # Final recursive secret sweep on the assembled object.
    if _contains_secret_like_text(meta):
        raise ProfileShareError("secret-like text rejected in metadata")
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
    # Re-run the same allowlist/secret filters used on export.
    safe_meta = _safe_metadata({"id": payload.get("source_profile_id") or "payload", **metadata_in})

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
        notes=None,  # never import free-form notes
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
                "notes",
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


def _browser_manager_launch_args(profile: dict[str, Any]) -> list[str]:
    """Call the real BrowserManager launch-arg builder (not a string mock)."""
    # Local import avoids circular import at module load (browser_manager → models).
    from backend.browser_manager import BrowserManager

    manager = BrowserManager()
    return list(manager._build_profile_launch_args(profile))


def build_extension_launch_evidence(
    profile: dict[str, Any],
    *,
    include_raw_paths: bool = False,
) -> dict[str, Any]:
    """Return redacted evidence from BrowserManager's real launch-arg builder.

    This proves the Manager would emit ``--load-extension=…`` for the profile's
    catalog IDs. It is **not** runtime proof that Chromium loaded the extension;
    callers must run a disposable browser session for that claim.
    """
    extension_ids = [str(item) for item in (profile.get("extension_ids") or [])]
    paths = extension_catalog.catalog_paths_for_ids(extension_ids)
    manager_args = _browser_manager_launch_args(profile)
    load_args = [arg for arg in manager_args if arg.startswith("--load-extension=")]
    load_arg = load_args[-1] if load_args else None

    redacted_load = _redact_load_extension_arg(load_arg)
    redacted_launch_args: list[str] = []
    for arg in manager_args:
        if arg.startswith("--load-extension="):
            redacted = _redact_load_extension_arg(arg)
            if redacted is not None:
                redacted_launch_args.append(redacted)
        elif arg.startswith("--remote-debugging-port"):
            # Manager-owned; omit from redacted evidence.
            continue
        elif _is_safe_launch_arg_prefix(arg) or arg.startswith("--fingerprint"):
            if not _contains_secret_like_text(arg):
                redacted_launch_args.append(arg)
        # Drop other free-form args from evidence payloads.

    evidence: dict[str, Any] = {
        "profile_id": str(profile.get("id") or ""),
        "extension_ids": extension_ids,
        "resolved_path_digests": [_path_digest(path) for path in paths],
        "resolved_path_count": len(paths),
        "load_extension_arg": redacted_load,
        "launch_args": redacted_launch_args,
        "extensions_resolved": bool(paths) and load_arg is not None,
        "browser_manager_builder": True,
        "loader_claim": "browser_manager_launch_args",
        "runtime_extension_proof": "not_run",
        "redacted": True,
    }
    if include_raw_paths:
        # In-process only — never write this branch into reports or CLI stdout.
        evidence["resolved_paths"] = paths
        evidence["load_extension_arg_raw"] = load_arg
        evidence["launch_args_raw"] = manager_args
    return evidence


def cleanup_disposable_profile(
    profile_id: str,
    *,
    remove_user_data: bool = True,
    get_profile: Callable[[str], dict[str, Any] | None] | None = None,
    delete_profile: Callable[[str], bool] | None = None,
    rmtree: Callable[[str | Path], None] | None = None,
) -> dict[str, Any]:
    """Remove user_data_dir first (hard-fail), then the DB row.

    Order is intentional: a failed filesystem delete must not orphan a missing
    DB row that still has on-disk profile state. ``shutil.rmtree`` is called
    **without** ``ignore_errors``.
    """
    loader = get_profile or db.get_profile
    deleter = delete_profile or db.delete_profile
    remove_tree = rmtree or shutil.rmtree
    profile = loader(profile_id)
    if profile is None:
        return {
            "deleted": False,
            "user_data_removed": False,
            "profile_id": profile_id,
            "redacted": True,
        }

    user_data_dir = Path(str(profile.get("user_data_dir") or ""))
    user_data_removed = False
    user_data_error: str | None = None

    if remove_user_data and str(user_data_dir) not in {"", ".", "/"}:
        if user_data_dir.exists():
            try:
                remove_tree(user_data_dir)
            except OSError as exc:
                user_data_error = type(exc).__name__
                return {
                    "deleted": False,
                    "user_data_removed": False,
                    "user_data_error": user_data_error,
                    "profile_id": profile_id,
                    "redacted": True,
                }
            if user_data_dir.exists():
                return {
                    "deleted": False,
                    "user_data_removed": False,
                    "user_data_error": "PathStillExists",
                    "profile_id": profile_id,
                    "redacted": True,
                }
            user_data_removed = True
        else:
            user_data_removed = True

    deleted = bool(deleter(profile_id))
    return {
        "deleted": deleted,
        "user_data_removed": user_data_removed if remove_user_data else False,
        "user_data_error": user_data_error,
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
