# Local Vault Connectors MVP

**Date:** 2026-07-30

**Contract:** `local-vault-connector-v1` (`docs/contracts/local-vault-connector-v1.json`)

**Code:** `backend/vault_connectors/`

**API:** `GET /api/v2/vault-connectors`

**Capability surface:** `GET /api/v2/capabilities` → `resources.secret-references`

## Goal

Own a provider-neutral local vault connector contract so Manager, agents, and MCP can discover installed local vault tooling and talk about secrets as **opaque references** only.

This MVP does **not** implement full origin-bound injection leases or provider unlock flows. It is the safe discovery and contract foundation for CBM-016-style secret-broker work.

## Supported discovery targets

| Provider id | Kind | Detection | Passkeys |
| --- | --- | --- | --- |
| `bitwarden-cli` | Bitwarden CLI (`bw`) | `PATH` binary | `user_presence_handoff` |
| `vaultwarden` | Vaultwarden server binary | `PATH` binary | `user_presence_handoff` |
| `keepassxc` | KeePassXC / `keepassxc-cli` | `PATH` binary | `user_presence_handoff` |
| `gopass` | gopass | `PATH` binary | `unsupported` |
| `fake` | In-process test fixture | env `CBM_VAULT_CONNECTOR_FAKE` | `user_presence_handoff` |

## Agent / MCP rules

Allowed public material:

- connector status and reason codes
- capability lists
- opaque `secretref-…` identifiers
- passkey handoff descriptors (`user_presence_handoff`)

Forbidden:

- `reveal`, raw password/token/cookie/TOTP seed/passkey export
- provider session material in responses
- embedding secret values in labels, notes, or error strings

## Optional probes

Set `CBM_VAULT_CONNECTOR_PROBES=1` to run short `--version` probes (2s timeout). Probes never unlock vaults, never pass credentials, and never require real accounts.

Discovery results are TTL-cached (30s success / 10s failure backoff). `GET /api/v2/vault-connectors` offloads discovery onto a worker thread so the asyncio event loop does not block on PATH probes. Probes use validated absolute executable paths only.

## Fake-provider E2E

`FakeVaultConnector` is always available to unit/E2E tests in-process. Production API responses include it only when `CBM_VAULT_CONNECTOR_FAKE=1`.

## Tests

```bash
python -m pytest backend/tests/test_vault_connectors.py backend/tests/test_agent_control_plane.py -q
```

## Non-goals (explicit)

- No real Bitwarden/Vaultwarden/KeePassXC/gopass account login
- No ciphertext storage in profile rows
- No agent-facing value injection in this slice
- No Infisical/OpenBao hosted bakeoff (separate CBM-016 work)
