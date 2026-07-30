"""Async CDP WebAuthn helper for synthetic virtual-authenticator benchmarks.

Scope (intentionally narrow):
- enable / disable the WebAuthn domain
- add / remove virtual authenticators
- automatic presence simulation
- user-verified flag control
- optional seed of *synthetic* credentials for software/synced test scenarios

Hard denies:
- WebAuthn.getCredentials / WebAuthn.getCredential
- any other credential-extraction or password-manager APIs
- real account / Google passkey material (never accepted as input)

Errors raised by this module are sanitized so private key blobs and raw
upstream exception text never surface to callers or logs.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

CdpSend = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]

ALLOWED_PROTOCOLS = frozenset({"ctap2", "u2f"})
ALLOWED_TRANSPORTS = frozenset({"usb", "nfc", "ble", "cable", "internal"})
ALLOWED_CTAP2_VERSIONS = frozenset({"ctap2_0", "ctap2_1"})
ALLOWED_USER_VERIFICATION = frozenset({"required", "preferred", "discouraged"})

DENIED_CDP_METHODS = frozenset(
    {
        "WebAuthn.getCredentials",
        "WebAuthn.getCredential",
        "PasswordManager.getSavedPasswords",
        "PasswordManager.setCredentials",
        "PasswordManager.addCredential",
        "PasswordManager.removeCredential",
        "PasswordManager.removeSavedPassword",
        "PasswordManager.enable",
        "PasswordManager.disable",
    }
)

_SENSITIVE_MARKERS = (
    "privatekey",
    "private_key",
    "mighagea",
    "credentialid",
    "password",
    "secret",
    "bearer",
    "authorization",
)


class WebAuthnError(Exception):
    """Base error; ``str(self)`` is always sanitized for logs and evidence."""


class WebAuthnValidationError(WebAuthnError):
    """Caller-supplied options or arguments failed validation."""


class WebAuthnStateError(WebAuthnError):
    """Operation rejected because of session / authenticator / credential state."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class WebAuthnDeniedError(WebAuthnError):
    """Extraction or real-credential APIs are hard-denied by policy."""


def _sanitize_message(raw: object) -> str:
    text = str(raw or "")
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "CDP WebAuthn operation failed"
    # Cap length so verbose upstream dumps cannot leak structure.
    if len(text) > 160:
        return "CDP WebAuthn operation failed"
    cleaned = text.strip() or "CDP WebAuthn operation failed"
    return cleaned


@dataclass(frozen=True)
class VirtualAuthenticatorOptions:
    """Validated options for ``WebAuthn.addVirtualAuthenticator``."""

    protocol: str = "ctap2"
    transport: str = "internal"
    has_resident_key: bool = True
    has_user_verification: bool = True
    automatic_presence: bool = True
    is_user_verified: bool = True
    ctap2_version: str = "ctap2_1"

    def validate(self) -> None:
        if self.protocol not in ALLOWED_PROTOCOLS:
            raise WebAuthnValidationError(
                f"protocol must be one of {sorted(ALLOWED_PROTOCOLS)}"
            )
        if self.transport not in ALLOWED_TRANSPORTS:
            raise WebAuthnValidationError(
                f"transport must be one of {sorted(ALLOWED_TRANSPORTS)}"
            )
        if (
            self.protocol == "ctap2"
            and self.ctap2_version not in ALLOWED_CTAP2_VERSIONS
        ):
            raise WebAuthnValidationError(
                f"ctap2_version must be one of {sorted(ALLOWED_CTAP2_VERSIONS)}"
            )
        for name in (
            "has_resident_key",
            "has_user_verification",
            "automatic_presence",
            "is_user_verified",
        ):
            if not isinstance(getattr(self, name), bool):
                raise WebAuthnValidationError(f"{name} must be a bool")

    def to_cdp(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "protocol": self.protocol,
            "transport": self.transport,
            "hasResidentKey": self.has_resident_key,
            "hasUserVerification": self.has_user_verification,
            "automaticPresenceSimulation": self.automatic_presence,
            "isUserVerified": self.is_user_verified,
        }
        if self.protocol == "ctap2":
            payload["ctap2Version"] = self.ctap2_version
        return payload


@dataclass
class SyntheticCredentialRef:
    """Opaque handle for a synthetic credential. Never carries private key material."""

    authenticator_id: str
    credential_id: str
    rp_id: str
    status: str = "active"

    def __post_init__(self) -> None:
        # Defense in depth: refuse any accidental private-key attributes.
        blocked = ("private_key", "privateKey", "privatekey", "secret", "password")
        for name in blocked:
            if name in self.__dict__:
                raise WebAuthnDeniedError(
                    "Synthetic credential ref cannot hold secrets"
                )


@dataclass
class _AuthenticatorState:
    options: VirtualAuthenticatorOptions
    is_user_verified: bool
    automatic_presence: bool


class CdpWebAuthn:
    """Small async wrapper around CDP WebAuthn for synthetic benchmark use."""

    def __init__(self, send: CdpSend) -> None:
        self._send = send
        self._enabled = False
        self._authenticators: dict[str, _AuthenticatorState] = {}
        self._credentials: dict[str, SyntheticCredentialRef] = {}

    # -- public state -------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def has_authenticator(self, authenticator_id: str) -> bool:
        return authenticator_id in self._authenticators

    # -- lifecycle ----------------------------------------------------------

    async def enable(self, *, enable_ui: bool = False) -> None:
        await self.send_allowed("WebAuthn.enable", {"enableUI": bool(enable_ui)})
        self._enabled = True

    async def add_virtual_authenticator(
        self,
        options: VirtualAuthenticatorOptions | Mapping[str, Any] | None = None,
    ) -> str:
        if not self._enabled:
            raise WebAuthnStateError("not_enabled", "WebAuthn domain is not enabled")
        opts = self._coerce_options(options)
        opts.validate()
        result = await self.send_allowed(
            "WebAuthn.addVirtualAuthenticator",
            {"options": opts.to_cdp()},
        )
        authenticator_id = str(result.get("authenticatorId") or "").strip()
        if not authenticator_id:
            raise WebAuthnError("CDP WebAuthn operation failed")
        self._authenticators[authenticator_id] = _AuthenticatorState(
            options=opts,
            is_user_verified=opts.is_user_verified,
            automatic_presence=opts.automatic_presence,
        )
        # Explicit follow-up commands keep state deterministic even when CDP
        # defaults differ across Chromium builds.
        await self.set_automatic_presence(authenticator_id, opts.automatic_presence)
        await self.set_user_verified(authenticator_id, opts.is_user_verified)
        return authenticator_id

    async def remove_virtual_authenticator(self, authenticator_id: str) -> None:
        self._require_authenticator(authenticator_id)
        await self.send_allowed(
            "WebAuthn.removeVirtualAuthenticator",
            {"authenticatorId": authenticator_id},
        )
        del self._authenticators[authenticator_id]
        for ref in self._credentials.values():
            if ref.authenticator_id == authenticator_id and ref.status == "active":
                ref.status = "revoked"

    async def set_automatic_presence(
        self, authenticator_id: str, enabled: bool
    ) -> None:
        self._require_authenticator(authenticator_id)
        await self.send_allowed(
            "WebAuthn.setAutomaticPresenceSimulation",
            {"authenticatorId": authenticator_id, "enabled": bool(enabled)},
        )
        self._authenticators[authenticator_id].automatic_presence = bool(enabled)

    async def set_user_verified(
        self, authenticator_id: str, is_user_verified: bool
    ) -> None:
        self._require_authenticator(authenticator_id)
        await self.send_allowed(
            "WebAuthn.setUserVerified",
            {
                "authenticatorId": authenticator_id,
                "isUserVerified": bool(is_user_verified),
            },
        )
        self._authenticators[authenticator_id].is_user_verified = bool(is_user_verified)

    async def seed_synthetic_credential(
        self,
        authenticator_id: str,
        *,
        rp_id: str,
        is_resident: bool = True,
        user_handle: bytes | None = None,
    ) -> SyntheticCredentialRef:
        self._require_authenticator(authenticator_id)
        rp = str(rp_id or "").strip()
        if not rp:
            raise WebAuthnValidationError("rp_id is required for synthetic seed")
        if any(part in rp.lower() for part in ("google.com", "accounts.google")):
            raise WebAuthnDeniedError(
                "Real account relying parties are denied for synthetic seed"
            )

        credential_id_bytes = secrets.token_bytes(32)
        credential_id_b64 = base64.b64encode(credential_id_bytes).decode("ascii")
        private_key_b64 = _generate_synthetic_pkcs8_p256()
        handle = user_handle if user_handle is not None else secrets.token_bytes(16)
        handle_b64 = base64.b64encode(handle).decode("ascii")

        # Local only for the CDP call; never stored on the returned ref.
        credential_payload = {
            "credentialId": credential_id_b64,
            "isResidentCredential": bool(is_resident),
            "rpId": rp,
            "privateKey": private_key_b64,
            "userHandle": handle_b64,
            "signCount": 0,
        }
        await self.send_allowed(
            "WebAuthn.addCredential",
            {
                "authenticatorId": authenticator_id,
                "credential": credential_payload,
            },
        )
        # Drop local secret references immediately after the send.
        del private_key_b64
        del credential_payload

        ref = SyntheticCredentialRef(
            authenticator_id=authenticator_id,
            credential_id=credential_id_b64,
            rp_id=rp,
            status="active",
        )
        self._credentials[credential_id_b64] = ref
        return ref

    async def revoke_synthetic_credential(
        self, authenticator_id: str, credential_id: str
    ) -> None:
        self._require_authenticator(authenticator_id)
        ref = self._credentials.get(credential_id)
        if ref is None or ref.authenticator_id != authenticator_id:
            raise WebAuthnStateError("no_credential", "Synthetic credential is unknown")
        if ref.status == "revoked":
            raise WebAuthnStateError(
                "credential_revoked", "Synthetic credential is revoked"
            )
        await self.send_allowed(
            "WebAuthn.removeCredential",
            {
                "authenticatorId": authenticator_id,
                "credentialId": credential_id,
            },
        )
        ref.status = "revoked"

    def assert_ready_for_assertion(
        self,
        authenticator_id: str,
        *,
        user_verification: str = "preferred",
        credential_id: str | None = None,
    ) -> None:
        if user_verification not in ALLOWED_USER_VERIFICATION:
            raise WebAuthnValidationError(
                f"user_verification must be one of {sorted(ALLOWED_USER_VERIFICATION)}"
            )
        state = self._authenticators.get(authenticator_id)
        if state is None:
            raise WebAuthnStateError("no_authenticator", "No virtual authenticator")

        if credential_id is not None:
            ref = self._credentials.get(credential_id)
            if ref is None or ref.authenticator_id != authenticator_id:
                raise WebAuthnStateError(
                    "no_credential", "Synthetic credential is unknown"
                )
            if ref.status == "revoked":
                raise WebAuthnStateError(
                    "credential_revoked", "Synthetic credential is revoked"
                )

        if user_verification == "required" and (
            not state.options.has_user_verification or not state.is_user_verified
        ):
            raise WebAuthnStateError(
                "user_verification_required",
                "User verification is required but not satisfied",
            )

    async def cleanup(self) -> None:
        """Best-effort remove of tracked authenticators and domain disable."""
        for authenticator_id in list(self._authenticators):
            try:
                await self.send_allowed(
                    "WebAuthn.removeVirtualAuthenticator",
                    {"authenticatorId": authenticator_id},
                )
            except WebAuthnError:
                pass
            self._authenticators.pop(authenticator_id, None)
        for ref in self._credentials.values():
            ref.status = "revoked"
        if self._enabled:
            try:
                await self.send_allowed("WebAuthn.disable", {})
            except WebAuthnError:
                pass
            self._enabled = False

    # -- hard-denied extraction surfaces ------------------------------------

    async def get_credentials(self, authenticator_id: str) -> None:
        raise WebAuthnDeniedError("Credential extraction is denied")

    async def get_credential(self, authenticator_id: str, credential_id: str) -> None:
        raise WebAuthnDeniedError("Credential extraction is denied")

    async def send_allowed(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a CDP method after denying extraction / real-credential APIs."""
        if method in DENIED_CDP_METHODS:
            raise WebAuthnDeniedError("Credential extraction is denied")
        if method.startswith("PasswordManager."):
            raise WebAuthnDeniedError("Credential extraction is denied")
        if method in {"WebAuthn.getCredentials", "WebAuthn.getCredential"}:
            raise WebAuthnDeniedError("Credential extraction is denied")
        try:
            result = await self._send(method, params)
        except WebAuthnError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize any CDP transport error
            raise WebAuthnError(_sanitize_message(exc)) from None
        if not isinstance(result, dict):
            return {}
        return result

    # -- internals ----------------------------------------------------------

    def _require_authenticator(self, authenticator_id: str) -> _AuthenticatorState:
        state = self._authenticators.get(authenticator_id)
        if state is None:
            raise WebAuthnStateError("no_authenticator", "No virtual authenticator")
        return state

    @staticmethod
    def _coerce_options(
        options: VirtualAuthenticatorOptions | Mapping[str, Any] | None,
    ) -> VirtualAuthenticatorOptions:
        if options is None:
            return VirtualAuthenticatorOptions()
        if isinstance(options, VirtualAuthenticatorOptions):
            return options
        allowed = {
            "protocol",
            "transport",
            "has_resident_key",
            "has_user_verification",
            "automatic_presence",
            "is_user_verified",
            "ctap2_version",
        }
        unknown = set(options) - allowed
        if unknown:
            raise WebAuthnValidationError(f"unknown option keys: {sorted(unknown)}")
        return VirtualAuthenticatorOptions(**dict(options))


def _generate_synthetic_pkcs8_p256() -> str:
    """Return a one-off synthetic ECDSA P-256 PKCS#8 private key (base64).

    Generated ephemerally for CDP ``addCredential`` only. Never persists and
    is never attached to public return values.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(der).decode("ascii")
