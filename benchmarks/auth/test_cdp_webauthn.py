"""Focused unit tests for the async CDP WebAuthn wrapper.

No real Google account, browser, or passkey material is used. The CDP client
is a pure in-memory fake that records method calls.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from benchmarks.auth import cdp_webauthn as wa
import pytest


class FakeCdp:
    """Minimal async CDP client used by the wrapper under test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, Exception] = {}
        self._auth_seq = 0

    async def send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = dict(params or {})
        self.calls.append((method, payload))
        if method in self.errors:
            raise self.errors[method]
        if method in self.responses:
            return dict(self.responses[method])
        if method == "WebAuthn.addVirtualAuthenticator":
            self._auth_seq += 1
            return {"authenticatorId": f"virt-auth-{self._auth_seq}"}
        return {}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


def _options(**overrides: Any) -> wa.VirtualAuthenticatorOptions:
    base = {
        "protocol": "ctap2",
        "transport": "internal",
        "has_resident_key": True,
        "has_user_verification": True,
        "automatic_presence": True,
        "is_user_verified": True,
    }
    base.update(overrides)
    return wa.VirtualAuthenticatorOptions(**base)


# ---------------------------------------------------------------------------
# Options validation
# ---------------------------------------------------------------------------


def test_options_reject_unknown_protocol():
    with pytest.raises(wa.WebAuthnValidationError, match="protocol"):
        _options(protocol="fido2").validate()


def test_options_reject_unknown_transport():
    with pytest.raises(wa.WebAuthnValidationError, match="transport"):
        _options(transport="lightning").validate()


def test_options_reject_non_bool_flags():
    with pytest.raises(wa.WebAuthnValidationError, match="has_user_verification"):
        _options(has_user_verification="yes").validate()  # type: ignore[arg-type]


def test_options_to_cdp_uses_protocol_field_names():
    opts = _options(
        protocol="ctap2",
        transport="usb",
        has_resident_key=True,
        has_user_verification=True,
        automatic_presence=False,
        is_user_verified=True,
        ctap2_version="ctap2_1",
    )
    opts.validate()
    cdp = opts.to_cdp()
    assert cdp == {
        "protocol": "ctap2",
        "ctap2Version": "ctap2_1",
        "transport": "usb",
        "hasResidentKey": True,
        "hasUserVerification": True,
        "automaticPresenceSimulation": False,
        "isUserVerified": True,
    }


# ---------------------------------------------------------------------------
# Enable / add / remove / presence / UV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_sends_webauthn_enable_without_ui_by_default():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)

    await session.enable()

    assert cdp.calls == [("WebAuthn.enable", {"enableUI": False})]
    assert session.is_enabled is True


@pytest.mark.asyncio
async def test_add_virtual_authenticator_enables_presence_and_uv():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()

    authenticator_id = await session.add_virtual_authenticator(_options())

    assert authenticator_id == "virt-auth-1"
    assert "WebAuthn.addVirtualAuthenticator" in cdp.methods()
    assert (
        "WebAuthn.setAutomaticPresenceSimulation",
        {"authenticatorId": "virt-auth-1", "enabled": True},
    ) in cdp.calls
    assert (
        "WebAuthn.setUserVerified",
        {"authenticatorId": "virt-auth-1", "isUserVerified": True},
    ) in cdp.calls
    assert session.has_authenticator("virt-auth-1")


@pytest.mark.asyncio
async def test_add_virtual_authenticator_requires_enable_first():
    session = wa.CdpWebAuthn(FakeCdp().send)

    with pytest.raises(wa.WebAuthnStateError, match="not enabled|not_enabled"):
        await session.add_virtual_authenticator(_options())


@pytest.mark.asyncio
async def test_remove_virtual_authenticator_tracks_removal():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())

    await session.remove_virtual_authenticator(auth_id)

    assert (
        "WebAuthn.removeVirtualAuthenticator",
        {"authenticatorId": auth_id},
    ) in cdp.calls
    assert not session.has_authenticator(auth_id)


@pytest.mark.asyncio
async def test_set_automatic_presence_and_user_verified_require_known_authenticator():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        await session.set_automatic_presence("missing", True)
    assert exc_info.value.code == "no_authenticator"

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        await session.set_user_verified("missing", True)
    assert exc_info.value.code == "no_authenticator"


# ---------------------------------------------------------------------------
# State: UV required / no authenticator / revoked synthetic credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_ready_no_authenticator():
    session = wa.CdpWebAuthn(FakeCdp().send)
    await session.enable()

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        session.assert_ready_for_assertion("virt-auth-1", user_verification="required")
    assert exc_info.value.code == "no_authenticator"


@pytest.mark.asyncio
async def test_assert_ready_uv_required_when_not_verified():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(
        _options(is_user_verified=False, has_user_verification=True)
    )

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        session.assert_ready_for_assertion(auth_id, user_verification="required")
    assert exc_info.value.code == "user_verification_required"


@pytest.mark.asyncio
async def test_assert_ready_uv_required_when_authenticator_lacks_uv():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(
        _options(has_user_verification=False, is_user_verified=True)
    )

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        session.assert_ready_for_assertion(auth_id, user_verification="required")
    assert exc_info.value.code == "user_verification_required"


@pytest.mark.asyncio
async def test_assert_ready_revoked_synthetic_credential():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())
    ref = await session.seed_synthetic_credential(auth_id, rp_id="example.test")
    await session.revoke_synthetic_credential(auth_id, ref.credential_id)

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        session.assert_ready_for_assertion(
            auth_id,
            user_verification="preferred",
            credential_id=ref.credential_id,
        )
    assert exc_info.value.code == "credential_revoked"
    assert ref.status == "revoked"


@pytest.mark.asyncio
async def test_assert_ready_happy_path():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())
    ref = await session.seed_synthetic_credential(auth_id, rp_id="example.test")

    session.assert_ready_for_assertion(
        auth_id,
        user_verification="required",
        credential_id=ref.credential_id,
    )


# ---------------------------------------------------------------------------
# Synthetic credential seed (safe) + extraction denial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_synthetic_credential_sends_add_credential_without_exposing_private_key():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())

    ref = await session.seed_synthetic_credential(auth_id, rp_id="example.test")

    assert ref.authenticator_id == auth_id
    assert ref.rp_id == "example.test"
    assert ref.status == "active"
    assert ref.credential_id
    assert not hasattr(ref, "private_key")
    assert "privateKey" not in ref.__dict__
    assert "private_key" not in dir(ref) or not getattr(ref, "private_key", None)

    add_calls = [
        params for method, params in cdp.calls if method == "WebAuthn.addCredential"
    ]
    assert len(add_calls) == 1
    credential = add_calls[0]["credential"]
    assert add_calls[0]["authenticatorId"] == auth_id
    assert credential["rpId"] == "example.test"
    assert credential["isResidentCredential"] is True
    assert isinstance(credential["credentialId"], str)
    assert isinstance(credential["privateKey"], str)
    # private key must look like base64 PKCS#8 material, never a real account label
    base64.b64decode(credential["privateKey"], validate=True)
    assert "google" not in credential["privateKey"].lower()
    assert "password" not in str(credential).lower()


@pytest.mark.asyncio
async def test_seed_requires_rp_id():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())

    with pytest.raises(wa.WebAuthnValidationError, match="rp_id"):
        await session.seed_synthetic_credential(auth_id, rp_id="")


@pytest.mark.asyncio
async def test_get_credentials_is_explicitly_denied():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())

    with pytest.raises(wa.WebAuthnDeniedError, match="denied|extraction"):
        await session.get_credentials(auth_id)

    with pytest.raises(wa.WebAuthnDeniedError):
        await session.get_credential(auth_id, "any")

    assert "WebAuthn.getCredentials" not in cdp.methods()
    assert "WebAuthn.getCredential" not in cdp.methods()


@pytest.mark.asyncio
async def test_raw_extraction_methods_are_denied_via_send_guard():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()

    for method in (
        "WebAuthn.getCredentials",
        "WebAuthn.getCredential",
        "PasswordManager.getSavedPasswords",
    ):
        with pytest.raises(wa.WebAuthnDeniedError):
            await session.send_allowed(method, {})

    assert "WebAuthn.getCredentials" not in cdp.methods()


# ---------------------------------------------------------------------------
# Cleanup + sanitized errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_authenticators_and_disables_domain():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())
    await session.seed_synthetic_credential(auth_id, rp_id="example.test")

    await session.cleanup()

    assert (
        "WebAuthn.removeVirtualAuthenticator",
        {"authenticatorId": auth_id},
    ) in cdp.calls
    assert ("WebAuthn.disable", {}) in cdp.calls
    assert not session.has_authenticator(auth_id)
    assert session.is_enabled is False


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_and_tolerates_remove_failures():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    await session.add_virtual_authenticator(_options())
    cdp.errors["WebAuthn.removeVirtualAuthenticator"] = RuntimeError(
        "upstream leaked privateKey=MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgSECRET"
    )

    await session.cleanup()
    await session.cleanup()

    assert session.is_enabled is False


@pytest.mark.asyncio
async def test_cdp_failures_are_sanitized_and_never_echo_private_material():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    secret_blob = (
        "privateKey=MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg"
        "SUPER-SECRET-KEY-MATERIAL-DO-NOT-LEAK"
    )
    cdp.errors["WebAuthn.addVirtualAuthenticator"] = RuntimeError(secret_blob)

    with pytest.raises(wa.WebAuthnError) as exc_info:
        await session.add_virtual_authenticator(_options())

    message = str(exc_info.value)
    assert "SUPER-SECRET" not in message
    assert "privateKey" not in message
    assert "MIGHAgEAMBMG" not in message
    assert re.search(r"WebAuthn|operation failed|CDP", message, re.IGNORECASE)


@pytest.mark.asyncio
async def test_revoke_synthetic_credential_requires_known_active_ref():
    cdp = FakeCdp()
    session = wa.CdpWebAuthn(cdp.send)
    await session.enable()
    auth_id = await session.add_virtual_authenticator(_options())

    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        await session.revoke_synthetic_credential(auth_id, "unknown-cred")
    assert exc_info.value.code == "no_credential"

    # After seed + revoke, second revoke is a state error without re-sending private keys.
    ref = await session.seed_synthetic_credential(auth_id, rp_id="example.test")
    await session.revoke_synthetic_credential(auth_id, ref.credential_id)
    with pytest.raises(wa.WebAuthnStateError) as exc_info:
        await session.revoke_synthetic_credential(auth_id, ref.credential_id)
    assert exc_info.value.code == "credential_revoked"
    assert "private" not in str(exc_info.value).lower()
