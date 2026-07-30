# IDR Architecture Report: Local Extension, Vault, and Tracing

**Date:** 2026-07-30
**Scope:** CloakBrowser local MV3 extension, vault integration, and agent tracing/observability.
**Sources:** NotebookLM research packets (`SECURE-ACTION-RECORDER-RESEARCH-PACKET`, `UNIVERSAL-IDENTITY-SECRET-PROFILE-MANAGEMENT-FINAL`), web official docs.

## 1. Challenging Assumptions

*   **Assumption:** CloakBrowser must build and maintain its own password manager and secret UI.
    *   **Challenge:** Building a new secret management UI duplicates effort and introduces risk. Instead, CloakBrowser should act as the compact control plane and delegate human secrets to Vaultwarden and machine secrets to Infisical. CloakBrowser only stores opaque references, never raw secrets.
*   **Assumption:** Agent tracing and cost attribution should be handled via a proxy gateway.
    *   **Challenge:** While proxy-based solutions (like Helicone) are fast to set up, they lack deep visibility into autonomous agentic loops and reasoning steps. An SDK/OTel-native approach (like Langfuse or OpenLIT) provides better semantic evaluation without breaking end-to-end tracing.
*   **Assumption:** The MV3 extension can persistently store recorded automation events.
    *   **Challenge:** Chrome MV3 remote code restrictions and privacy guidelines require strict bounded state. The recorder must use in-memory `chrome.storage.session`, and push normalized, redacted events immediately to the Manager.

## 2. Vault and Secret Provider Comparison

| Provider | Primary Use Case | Strengths | Weaknesses for CloakBrowser |
| :--- | :--- | :--- | :--- |
| **Vaultwarden** | Human Vault | AGPL-3.0. Bitwarden API compatible. Excellent for human passwords, passkeys, TOTP, and notes. Reuses existing Bitwarden UI/extensions. | Lacks robust machine identity/PKI management. |
| **Infisical** | Agent/Machine Vault | MIT (core). Built for dynamic secrets, machine identities, rotation, and PKI. Strong SDKs. | Not designed as a consumer passkey UX. |
| **KeePassXC** | Sovereign Offline Backup | Local offline KDBX vault with strong local encryption. | No central governance or RBAC. Suitable only for import/export, not as a control plane. |

**Decision:** Vaultwarden for human vaults; Infisical for machine/agent identities.

## 3. Tracing and Observability Comparison

| Tool | Architecture | Best For | CloakBrowser Fit |
| :--- | :--- | :--- | :--- |
| **Langfuse** | SDK/Tracing (All-in-One) | Comprehensive tracing, evaluations, and deep agent trajectories. | **High:** Deep evaluation primitives fit perfectly with autonomous agent workflows where internal reasoning steps must be traced. |
| **Helicone** | Proxy-based Gateway | Zero-code setup, cost logging, and caching. | **Low/Medium:** Fast but lacks deep visibility into complex multi-step agent thought processes. |
| **OpenLIT** | OTel-native SDK | Integrating LLM traces into an existing OpenTelemetry stack. | **High:** Standardized, prevents vendor lock-in, and aligns with OTel ecosystem. |
| **OTel (OpenTelemetry)** | Protocol/Framework | Universal standard for application monitoring. | **Foundation:** Should be the underlying standard adopted, relying on OpenLIT or Langfuse for the LLM-specific layer. |

**Decision:** Standardize on OTel data models. Support OpenLIT for vendor-agnostic infrastructure, and Langfuse for deep agentic evaluation where required.

## 4. Commercially Redistributable vs. External Connectors

To maintain CloakBrowser's commercial viability and license hygiene, we define strict boundaries:

*   **Commercially Redistributable (Bundled):**
    Code licensed under MIT, Apache-2.0, or BSD. These can be directly compiled, bundled, and redistributed within the CloakBrowser installer.
    *   *Examples:* Infisical (core SDKs), OpenLIT, Langfuse (SDKs), OpenTelemetry frameworks, browser extensions.
*   **External Connectors (Self-Hosted/BYO):**
    Code licensed under AGPL-3.0 or proprietary licenses. These must **never** be bundled directly into the commercial product. CloakBrowser will only provide "connectors" (API integrations). The user or operator must self-host these services or provide their own endpoints.
    *   *Examples:* Vaultwarden (AGPL-3.0), Passbolt (AGPL-3.0), Helicone Cloud.

## 5. Security Invariants (No Secrets)

*   **Zero Raw Secrets:** No passwords, TOTP seeds, proxy credentials, or API keys may be logged, output to stdout, stored in the CloakBrowser database, or placed into the model's context window.
*   **Origin-Bound:** The Secret Use Broker must strictly verify the origin just before injection.
*   **Redacted Telemetry:** Tracing tools (Langfuse/OpenLIT) must scrub payloads of PII, session cookies, and authentication headers before transmitting traces.

## 6. Blockers & Next Steps

*   **NotebookLM Blocker:** The NotebookLM wrapper is currently reporting a stale, unauthenticated browser state. Deep simulation using NotebookLM remains blocked until the session is re-authenticated. We are relying on static research packets.
*   **Next Steps:**
    *   Implement `chrome.storage.session` recorder limitations in the MV3 extension.
    *   Build OTel/Langfuse adapters in the agent harness, ensuring payload redaction.
    *   Finalize Vaultwarden and Infisical API connectors without bundling AGPL code.
