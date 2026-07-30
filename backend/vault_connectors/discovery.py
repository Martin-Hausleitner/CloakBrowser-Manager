"""Install-time discovery for local vault tooling.

Default path uses ``shutil.which`` only. Optional probes may run ``--version``
with a short timeout; they never unlock vaults or read accounts.

Results are cached with a bounded TTL (and shorter failure backoff) so async
HTTP handlers do not re-exec PATH resolution or probes on every request.
Probes always use a validated absolute executable path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .contract import (
    ConnectorDiscovery,
    ConnectorStatus,
    PasskeyHandoffMode,
    filter_agent_operations,
)

which: Callable[[str], str | None] = shutil.which

PROBE_TIMEOUT_SECONDS = 2.0
OUTPUT_LIMIT_BYTES = 4_096
DISCOVERY_CACHE_TTL_SECONDS = 30.0
DISCOVERY_CACHE_FAILURE_BACKOFF_SECONDS = 10.0

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
_PROBE_FAILURE_REASONS = frozenset(
    {
        "probe_executable_missing",
        "probe_timeout",
        "probe_os_error",
        "probe_failed",
        "probe_version_unparsed",
        "executable_invalid",
    }
)

# provider_id -> (provider_kind, display_name, candidate binaries, passkey mode)
_PROVIDER_SPECS: tuple[tuple[str, str, str, tuple[str, ...], PasskeyHandoffMode], ...] = (
    (
        "bitwarden-cli",
        "bitwarden_cli",
        "Bitwarden CLI",
        ("bw",),
        PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
    ),
    (
        "vaultwarden",
        "vaultwarden",
        "Vaultwarden",
        ("vaultwarden",),
        PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
    ),
    (
        "keepassxc",
        "keepassxc",
        "KeePassXC",
        ("keepassxc-cli", "keepassxc"),
        PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
    ),
    (
        "gopass",
        "gopass",
        "gopass",
        ("gopass",),
        PasskeyHandoffMode.UNSUPPORTED,
    ),
)

_BASE_CAPABILITIES = filter_agent_operations(
    {
        "discover",
        "status",
        "health",
        "list_references",
        "capabilities",
        "authorize_use",
        "revoke_use",
        "receipt",
    }
)


@dataclass
class _CacheEntry:
    results: tuple[ConnectorDiscovery, ...]
    expires_at: float


_cache_lock = threading.Lock()
_discovery_cache: dict[bool, _CacheEntry] = {}


def clear_discovery_cache() -> None:
    """Drop cached discovery results (tests / explicit refresh)."""
    with _cache_lock:
        _discovery_cache.clear()


def _basename(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.basename(path)


def validate_absolute_executable(path: str | None) -> str | None:
    """Return a real, absolute, executable file path or None."""
    if not path or not isinstance(path, str):
        return None
    cleaned = path.strip()
    if not cleaned or "\x00" in cleaned:
        return None
    try:
        candidate = cleaned if os.path.isabs(cleaned) else os.path.abspath(cleaned)
        real = os.path.realpath(candidate)
    except OSError:
        return None
    if not os.path.isabs(real):
        return None
    try:
        if not os.path.isfile(real):
            return None
        if not os.access(real, os.X_OK):
            return None
    except OSError:
        return None
    return real


def _resolve_executable(binary_name: str) -> str | None:
    """Resolve a PATH entry via which, then validate as an absolute executable."""
    located = which(binary_name)
    return validate_absolute_executable(located)


def _run_version_probe(executable: str) -> tuple[str | None, str]:
    """Return (version, reason_code). Never passes unlock/login args."""
    absolute = validate_absolute_executable(executable)
    if absolute is None:
        return None, "executable_invalid"
    try:
        completed = subprocess.run(
            [absolute, "--version"],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            env=_sanitized_env(),
        )
    except FileNotFoundError:
        return None, "probe_executable_missing"
    except subprocess.TimeoutExpired:
        return None, "probe_timeout"
    except OSError:
        return None, "probe_os_error"

    raw = (completed.stdout or b"") + b"\n" + (completed.stderr or b"")
    text = raw[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace").strip()
    if completed.returncode != 0 and not text:
        return None, "probe_failed"
    match = _VERSION_RE.search(text)
    if match is None:
        return None, "probe_version_unparsed"
    return match.group(1), "probe_ok"


def _sanitized_env() -> dict[str, str]:
    """Inherit a minimal environment; drop known secret-bearing vault env vars."""
    blocked_prefixes = (
        "BW_",
        "BITWARDEN",
        "VAULT_",
        "GOPASS_",
        "PASSWORD",
        "SECRET",
        "TOKEN",
    )
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(upper.startswith(prefix) or prefix in upper for prefix in blocked_prefixes):
            continue
        if upper in {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "SYSTEMROOT",
            "TMPDIR",
        } or upper.startswith("XDG_"):
            env[key] = value
    # Always preserve PATH for which resolution of relative probes.
    if "PATH" in os.environ and "PATH" not in env:
        env["PATH"] = os.environ["PATH"]
    return env


def _discover_uncached(*, run_probes: bool) -> list[ConnectorDiscovery]:
    """Discover installed local vault tooling without authenticating (no cache)."""
    results: list[ConnectorDiscovery] = []
    for provider_id, provider_kind, display_name, binaries, passkey_mode in _PROVIDER_SPECS:
        found_path: str | None = None
        found_name: str | None = None
        for binary in binaries:
            resolved = _resolve_executable(binary)
            if resolved:
                found_path = resolved
                found_name = binary
                break

        if not found_path:
            results.append(
                ConnectorDiscovery(
                    provider_id=provider_id,
                    provider_kind=provider_kind,
                    display_name=display_name,
                    status=ConnectorStatus.NOT_INSTALLED,
                    installed=False,
                    capabilities=tuple(sorted(_BASE_CAPABILITIES)),
                    passkey_mode=passkey_mode,
                    reason_code="binary_not_found",
                    executable_basename=None,
                    probe_version=None,
                    probe_ran=False,
                )
            )
            continue

        version: str | None = None
        reason = "installed_unauthenticated"
        status = ConnectorStatus.INSTALLED
        probe_ran = False
        if run_probes:
            probe_ran = True
            version, probe_reason = _run_version_probe(found_path)
            if version is not None:
                reason = "installed_unauthenticated"
                status = ConnectorStatus.INSTALLED
            else:
                reason = probe_reason
                status = ConnectorStatus.DEGRADED

        results.append(
            ConnectorDiscovery(
                provider_id=provider_id,
                provider_kind=provider_kind,
                display_name=display_name,
                status=status,
                installed=True,
                capabilities=tuple(sorted(_BASE_CAPABILITIES)),
                passkey_mode=passkey_mode,
                reason_code=reason,
                executable_basename=_basename(found_path) or found_name,
                probe_version=version,
                probe_ran=probe_ran,
            )
        )
    return results


def _cache_ttl_for(results: list[ConnectorDiscovery], *, run_probes: bool) -> float:
    if run_probes and any(
        item.probe_ran and item.reason_code in _PROBE_FAILURE_REASONS for item in results
    ):
        return DISCOVERY_CACHE_FAILURE_BACKOFF_SECONDS
    return DISCOVERY_CACHE_TTL_SECONDS


def discover_local_vault_connectors(
    *,
    run_probes: bool = False,
    use_cache: bool = True,
) -> list[ConnectorDiscovery]:
    """Discover installed local vault tooling without authenticating.

    Cached by default so request handlers do not re-run PATH/probe work until TTL
    expiry (or a shorter failure backoff after probe errors).
    """
    if use_cache:
        now = time.monotonic()
        with _cache_lock:
            entry = _discovery_cache.get(bool(run_probes))
            if entry is not None and entry.expires_at > now:
                return list(entry.results)

    results = _discover_uncached(run_probes=run_probes)
    if use_cache:
        ttl = _cache_ttl_for(results, run_probes=run_probes)
        with _cache_lock:
            _discovery_cache[bool(run_probes)] = _CacheEntry(
                results=tuple(results),
                expires_at=time.monotonic() + ttl,
            )
    return results


def discovery_enabled_by_env(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = (source.get("CBM_VAULT_CONNECTOR_PROBES") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
