"""Provider-neutral local vault connectors (discovery + reference-only agent surface)."""

from __future__ import annotations

from .contract import (
    AGENT_SAFE_OPERATIONS,
    FORBIDDEN_AGENT_OPERATIONS,
    LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
    SECRET_REFERENCE_PATTERN,
    ConnectorDiscovery,
    ConnectorStatus,
    LocalVaultConnector,
    PasskeyHandoff,
    PasskeyHandoffMode,
    SecretKind,
    SecretReference,
    assert_agent_safe_payload,
    filter_agent_operations,
    is_valid_secret_ref,
    public_connector_payload,
)
from .discovery import discover_local_vault_connectors, discovery_enabled_by_env
from .fake import FakeVaultConnector
from .service import LocalVaultConnectorService, agent_capability_block

__all__ = [
    "AGENT_SAFE_OPERATIONS",
    "FORBIDDEN_AGENT_OPERATIONS",
    "LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION",
    "SECRET_REFERENCE_PATTERN",
    "ConnectorDiscovery",
    "ConnectorStatus",
    "FakeVaultConnector",
    "LocalVaultConnector",
    "LocalVaultConnectorService",
    "PasskeyHandoff",
    "PasskeyHandoffMode",
    "SecretKind",
    "SecretReference",
    "agent_capability_block",
    "assert_agent_safe_payload",
    "discover_local_vault_connectors",
    "discovery_enabled_by_env",
    "filter_agent_operations",
    "is_valid_secret_ref",
    "public_connector_payload",
]
