"""Install-time discovery for local vault tooling.

Default path uses ``shutil.which`` only. Optional probes may run ``--version``
with a short timeout; they never unlock vaults or read accounts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from .contract import (
    AGENT_SAFE_OPERATIONS,
    ConnectorDiscovery,
    ConnectorStatus,
    PasskeyHandoffMode,
    filter_agent_operations,
)

which: Callable[[str], str | None] = shutil.which

PROBE_TIMEOUT_SECONDS = 2.0
OUTPUT_LIMIT_BYTES = 4_096

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

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


def _basename(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.basename(path)


def _run_version_probe(executable: str) -> tuple[str | None, str]:
    """Return (version, reason_code). Never passes unlock/login args."""
    try:
        completed = subprocess.run(
            [executable, "--version"],
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
        if upper in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "TMPDIR"}:
            env[key] = value
        elif upper.startswith("XDG_"):
            env[key] = value
    # Always preserve PATH for which resolution of relative probes.
    if "PATH" in os.environ and "PATH" not in env:
        env["PATH"] = os.environ["PATH"]
    return env


def discover_local_vault_connectors(*, run_probes: bool = False) -> list[ConnectorDiscovery]:
    """Discover installed local vault tooling without authenticating."""
    results: list[ConnectorDiscovery] = []
    for provider_id, provider_kind, display_name, binaries, passkey_mode in _PROVIDER_SPECS:
        found_path: str | None = None
        found_name: str | None = None
        for binary in binaries:
            path = which(binary)
            if path:
                found_path = path
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


def discovery_enabled_by_env(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = (source.get("CBM_VAULT_CONNECTOR_PROBES") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
