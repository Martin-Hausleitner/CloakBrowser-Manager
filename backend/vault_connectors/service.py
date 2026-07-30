"""Aggregate local vault connector discovery for agent-safe snapshots."""

from __future__ import annotations

from typing import Any

from .contract import (
    FORBIDDEN_AGENT_OPERATIONS,
    LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
    ConnectorDiscovery,
    SecretReference,
    assert_agent_safe_payload,
    public_connector_payload,
)
from .discovery import discover_local_vault_connectors, discovery_enabled_by_env
from .fake import FakeVaultConnector


class LocalVaultConnectorService:
    """Builds agent/MCP-safe connector status without raw secret material."""

    def __init__(
        self,
        *,
        include_fake: bool = False,
        run_probes: bool | None = None,
    ) -> None:
        self.include_fake = include_fake
        if run_probes is None:
            self.run_probes = discovery_enabled_by_env()
        else:
            self.run_probes = run_probes

    def discover(self) -> list[ConnectorDiscovery]:
        items = list(discover_local_vault_connectors(run_probes=self.run_probes))
        if self.include_fake:
            items.append(FakeVaultConnector().health())
        return items

    def list_fake_references(self) -> list[SecretReference]:
        if not self.include_fake:
            return []
        return FakeVaultConnector().list_references()

    def agent_snapshot(self) -> dict[str, Any]:
        connectors = [public_connector_payload(item) for item in self.discover()]
        secret_references = [
            ref.public_payload() for ref in self.list_fake_references()
        ]
        snapshot: dict[str, Any] = {
            "contract_version": LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
            "reveal_available": False,
            "forbidden_operations": sorted(FORBIDDEN_AGENT_OPERATIONS),
            "connectors": connectors,
            "secret_references": secret_references,
            "passkey_policy": {
                "default_mode": "user_presence_handoff",
                "note": "Device-bound passkeys require human user-presence handoff; no export.",
            },
        }
        assert_agent_safe_payload(snapshot)
        return snapshot


def agent_capability_block(
    *,
    include_fake: bool = False,
    run_probes: bool | None = None,
) -> dict[str, Any]:
    """Fragment embedded in control-plane capability discovery."""
    service = LocalVaultConnectorService(include_fake=include_fake, run_probes=run_probes)
    snapshot = service.agent_snapshot()
    return {
        "available": {
            "rest": True,
            "cli": False,
            "mcp": True,
            "skill": False,
        },
        "rest_operations": ["discover", "status", "list_references"],
        "cli_operations": [],
        "mcp_operations": ["discover", "status"],
        "skill_operations": [],
        "secrets": "reference-only",
        "reveal": False,
        "contract": LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
        "connectors": snapshot["connectors"],
        "passkey_policy": snapshot["passkey_policy"],
        "forbidden_operations": snapshot["forbidden_operations"],
        "mcp_note": "status and opaque secret references only; no reveal",
    }
