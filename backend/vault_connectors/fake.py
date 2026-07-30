"""In-process fake local vault connector for E2E tests.

Never depends on real accounts, network vaults, or host binaries.
"""

from __future__ import annotations

from .contract import (
    AGENT_SAFE_OPERATIONS,
    ConnectorDiscovery,
    ConnectorStatus,
    LocalVaultConnector,
    PasskeyHandoff,
    PasskeyHandoffMode,
    SecretKind,
    SecretReference,
    filter_agent_operations,
)


class FakeVaultConnector:
    """Deterministic fixture connector. No ``reveal`` method exists."""

    provider_id = "fake"

    def __init__(self) -> None:
        self._refs = (
            SecretReference(
                secret_ref="secretref-fake-login-example-com",
                kind=SecretKind.PASSWORD,
                provider_id=self.provider_id,
                label="example.com login",
                origin="https://example.com",
                raw_value="FAKE_NOT_FOR_AGENTS",
            ),
            SecretReference(
                secret_ref="secretref-fake-totp-example-com",
                kind=SecretKind.TOTP,
                provider_id=self.provider_id,
                label="example.com totp",
                origin="https://example.com",
                raw_value="FAKE_TOTP_SEED",
            ),
            SecretReference(
                secret_ref="secretref-fake-passkey-example-com",
                kind=SecretKind.PASSKEY,
                provider_id=self.provider_id,
                label="example.com passkey",
                origin="https://example.com",
                passkey_handoff=PasskeyHandoff(
                    required=True,
                    mode=PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
                    reason_code="device_bound_authenticator",
                ),
                raw_value=None,
            ),
        )

    def health(self) -> ConnectorDiscovery:
        return ConnectorDiscovery(
            provider_id=self.provider_id,
            provider_kind="fake",
            display_name="Fake Local Vault",
            status=ConnectorStatus.READY,
            installed=True,
            capabilities=tuple(sorted(self.agent_operations())),
            passkey_mode=PasskeyHandoffMode.USER_PRESENCE_HANDOFF,
            reason_code="ready",
            executable_basename=None,
            probe_version="0.0.0-fake",
            probe_ran=False,
        )

    def list_references(self) -> list[SecretReference]:
        return list(self._refs)

    def agent_operations(self) -> frozenset[str]:
        return filter_agent_operations(AGENT_SAFE_OPERATIONS)


# Static type assertion that Fake satisfies the protocol surface used by tests.
def _protocol_check() -> LocalVaultConnector:
    return FakeVaultConnector()
