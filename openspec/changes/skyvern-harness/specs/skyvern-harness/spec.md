## ADDED Requirements

### Requirement: Skyvern harness capability discovery
The system SHALL expose a Skyvern harness capability endpoint that reports whether the optional Skyvern runtime is importable, whether an LLM endpoint is configured, and that browser automation is routed through CloakBrowser Manager CDP.

#### Scenario: Capabilities when Skyvern is missing
- **WHEN** a client requests Skyvern harness capabilities and the `skyvern` package is not installed
- **THEN** the response status is `unavailable` and includes an AGPL license notice field

#### Scenario: Capabilities when Skyvern is installed
- **WHEN** a client requests Skyvern harness capabilities and the `skyvern` package is importable
- **THEN** the response status is `ready` or `degraded` (if LLM missing) and `cdp_routing` is `cloakbrowser-manager`

### Requirement: Cloak CDP binding for Skyvern
The system SHALL build a Skyvern-compatible browser address that points at the Manager CDP proxy for a specific profile, including authorization headers when Manager auth is enabled.

#### Scenario: Bind running profile
- **WHEN** an operator binds the Skyvern harness to a running profile
- **THEN** the harness returns an absolute CDP HTTP URL under `/api/profiles/{profile_id}/cdp` and does not launch a separate vanilla Chromium

#### Scenario: Reject stopped profile
- **WHEN** an operator binds the Skyvern harness to a profile that is not running
- **THEN** the system returns an error instructing the caller to launch the profile first

### Requirement: Skyvern task execution through cloak layer
The system SHALL execute Skyvern automation against the bound CloakBrowser profile so fingerprint, proxy, and session state remain those of the Manager profile.

#### Scenario: CDP-connected Skyvern session
- **WHEN** the harness runs a connect-and-navigate task for a bound profile
- **THEN** navigation occurs in the CloakBrowser session reachable via Manager CDP and a screenshot artifact path is returned

#### Scenario: Agent task without LLM
- **WHEN** a full Skyvern agent `run_task` is requested but no LLM credentials/endpoint are configured
- **THEN** the harness returns `blocked` with a clear reason instead of faking agent output

### Requirement: License transparency
The system SHALL document that Skyvern is AGPL-3.0 and is an optional dependency not vendored into the MIT Manager source tree.

#### Scenario: License field on capabilities
- **WHEN** capabilities are requested
- **THEN** the payload includes `skyvern_license` equal to `AGPL-3.0`
