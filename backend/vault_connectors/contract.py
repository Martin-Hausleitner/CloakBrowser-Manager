"""Provider-neutral local vault connector contract.

Agent and MCP surfaces may receive secret references, capabilities, and status.
Raw secret material is never part of the public contract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION = "local-vault-connector-v1"

SECRET_REFERENCE_PATTERN = re.compile(
    r"^secretref-[A-Za-z0-9][A-Za-z0-9._-]{2,143}$"
)

# Operations that agents/MCP must never be offered.
FORBIDDEN_AGENT_OPERATIONS: frozenset[str] = frozenset(
    {
        "reveal",
        "export_raw",
        "dump",
        "unlock_and_print",
        "get_password",
        "get_totp_seed",
        "export_passkey",
    }
)

# Agent-safe operations (status / reference metadata only in MVP).
AGENT_SAFE_OPERATIONS: frozenset[str] = frozenset(
    {
        "discover",
        "status",
        "health",
        "list_references",
        "capabilities",
        "authorize_use",  # receipt-only future path; no value return
        "revoke_use",
        "receipt",
    }
)

_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "cookie",
        "cookies",
        "totp",
        "otp",
        "totp_seed",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "value",
        "raw_value",
        "private_key",
        "session_key",
        "client_secret",
    }
)

# Policy/classification keys that may mention secret *modes* without carrying values.
_ALLOWED_POLICY_KEYS = frozenset(
    {
        "kind",
        "secret_kind",
        "item_kind",
        "secrets",
        "secret_policy",
        "passkey_mode",
        "passkey_policy",
        "passkey_handoff",
        "forbidden_operations",
    }
)

_SAFE_POLICY_STRINGS = frozenset(
    {
        "reference-only",
        "reference-digest-only",
        "user_presence_handoff",
        "unsupported",
        "unknown",
        "password",
        "totp",
        "passkey",
        "note",
        "api_token",
        "ssh_key",
        "reveal",
        "export_raw",
        "dump",
        "unlock_and_print",
        "get_password",
        "get_totp_seed",
        "export_passkey",
    }
)

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|cookie|totp|otp|api[_-]?key)\s*[:=]"
)
_AUTH_BEARER_RE = re.compile(
    r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"
)


class ConnectorStatus(str, Enum):
    READY = "ready"
    INSTALLED = "installed"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    MISCONFIGURED = "misconfigured"
    NOT_INSTALLED = "not_installed"


class PasskeyHandoffMode(str, Enum):
    """Passkeys are never exported; device presence is a human handoff."""

    USER_PRESENCE_HANDOFF = "user_presence_handoff"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class SecretKind(str, Enum):
    PASSWORD = "password"
    TOTP = "totp"
    PASSKEY = "passkey"
    NOTE = "note"
    API_TOKEN = "api_token"
    SSH_KEY = "ssh_key"
    UNKNOWN = "unknown"


def is_valid_secret_ref(value: str) -> bool:
    return bool(value) and SECRET_REFERENCE_PATTERN.fullmatch(value) is not None


def assert_agent_safe_payload(payload: Any, *, path: str = "$") -> None:
    """Fail closed if a public agent/MCP payload contains secret-like material."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_l = str(key).lower()
            if key_l in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"forbidden secret-like field at {path}.{key_l}")
            if key_l in _ALLOWED_POLICY_KEYS and isinstance(value, str):
                if value in _SAFE_POLICY_STRINGS or not (
                    _SENSITIVE_ASSIGNMENT_RE.search(value)
                    or _AUTH_BEARER_RE.search(value)
                ):
                    continue
                raise ValueError(f"forbidden secret-like content at {path}.{key_l}")
            if isinstance(value, str) and (
                _SENSITIVE_ASSIGNMENT_RE.search(value) or _AUTH_BEARER_RE.search(value)
            ):
                raise ValueError(f"forbidden secret-like content at {path}.{key_l}")
            assert_agent_safe_payload(value, path=f"{path}.{key_l}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_agent_safe_payload(item, path=f"{path}[{index}]")
        return
    if isinstance(payload, Enum):
        return
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        if isinstance(payload, str) and (
            _SENSITIVE_ASSIGNMENT_RE.search(payload) or _AUTH_BEARER_RE.search(payload)
        ):
            raise ValueError(f"forbidden secret-like content at {path}")
        return
    # Dataclasses / plain objects should already be converted to public dicts.
    raise ValueError(f"unsupported payload type at {path}: {type(payload).__name__}")


@dataclass(frozen=True)
class PasskeyHandoff:
    required: bool
    mode: PasskeyHandoffMode
    reason_code: str = "device_bound_authenticator"

    def public_payload(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "mode": self.mode.value,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class SecretReference:
    """Opaque handle. ``raw_value`` is intentionally absent from public payloads."""

    secret_ref: str
    kind: SecretKind
    provider_id: str
    label: str | None = None
    origin: str | None = None
    passkey_handoff: PasskeyHandoff | None = None
    # Internal-only field for fake/test fixtures; never serialized publicly.
    raw_value: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not is_valid_secret_ref(self.secret_ref):
            raise ValueError("secret_ref is invalid")
        if self.kind == SecretKind.PASSKEY and self.passkey_handoff is None:
            object.__setattr__(
                self,
                "passkey_handoff",
                PasskeyHandoff(
                    required=True,
                    mode=PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
                ),
            )

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "secret_ref": self.secret_ref,
            "kind": self.kind.value,
            "provider_id": self.provider_id,
        }
        if self.label is not None:
            payload["label"] = self.label
        if self.origin is not None:
            payload["origin"] = self.origin
        if self.passkey_handoff is not None:
            payload["passkey_handoff"] = self.passkey_handoff.public_payload()
        assert_agent_safe_payload(payload)
        return payload


@dataclass(frozen=True)
class ConnectorDiscovery:
    provider_id: str
    provider_kind: str
    display_name: str
    status: ConnectorStatus
    installed: bool
    capabilities: tuple[str, ...]
    passkey_mode: PasskeyHandoffMode
    reason_code: str
    reference_prefix: str = "secretref-"
    executable_basename: str | None = None
    probe_version: str | None = None
    probe_ran: bool = False

    def public_payload(self) -> dict[str, Any]:
        return public_connector_payload(self)


def public_connector_payload(status: ConnectorDiscovery) -> dict[str, Any]:
    payload = {
        "provider_id": status.provider_id,
        "provider_kind": status.provider_kind,
        "display_name": status.display_name,
        "status": status.status.value,
        "installed": status.installed,
        "capabilities": list(status.capabilities),
        "passkey_mode": status.passkey_mode.value,
        "reason_code": status.reason_code,
        "reference_prefix": status.reference_prefix,
        "executable_basename": status.executable_basename,
        "probe_version": status.probe_version,
        "probe_ran": status.probe_ran,
        "reveal_available": False,
    }
    assert_agent_safe_payload(payload)
    return payload


@runtime_checkable
class LocalVaultConnector(Protocol):
    """Provider-neutral local connector. No agent-facing reveal method."""

    @property
    def provider_id(self) -> str: ...

    def health(self) -> ConnectorDiscovery: ...

    def list_references(self) -> list[SecretReference]: ...

    def agent_operations(self) -> frozenset[str]: ...


def filter_agent_operations(operations: Iterable[str]) -> frozenset[str]:
    cleaned = {op.strip() for op in operations if op and op.strip()}
    return frozenset(cleaned - FORBIDDEN_AGENT_OPERATIONS)
