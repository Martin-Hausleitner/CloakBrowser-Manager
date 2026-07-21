# Draft: scoped browser access for Paperclip agents

> This is a discussion draft. It contains no live credentials, profile names, network addresses, or browser content.

## Context

The implementation is available for review in a fork of CloakBrowser Manager. It gives a Paperclip-managed agent access only to explicitly assigned browser sandboxes, while human administrators decide which users can view, interact with, operate, or automate each sandbox.

This is Paperclip-inspired control-plane behavior, not a dependency on a Paperclip server and not a copy of Paperclip's UI or branding.

## Implementation under review

- Each browser profile has an explicit `sandbox_id`.
- Local people use signed sessions; agents use individual opaque credentials whose hashes alone are persisted. A generated agent key is shown only once.
- The backend enforces scopes for profile discovery, VNC, clipboard, CDP REST, and CDP WebSockets.
- `view`, `interact`, `operate`, and `automate` are distinct. CDP is never implied by a view grant.
- Existing VNC/CDP WebSockets carry revocable leases, so deactivation, grant changes and credential rotation terminate active access immediately.
- Viewer-only RFB filtering is stateful, limits negotiated encodings and drops keyboard, pointer and clipboard input before the upstream server.
- The compact admin dashboard manages people, agent identities, sandbox grants, deactivation, and agent-key rotation. Its control tier and independent CDP toggle support combinations such as `operate + automate`, and an effective-access disclosure lists the concrete profiles and resulting capabilities.
- The bootstrap-admin path and `ACCESS_CONTROL_ENABLED=1` make migration explicit and reversible.
- Denied REST and WebSocket policy decisions are recorded as metadata-only audit events; browser content, clipboard data and credentials are never audit payloads.

## Why not rely on the UI alone?

VNC and CDP are direct API/WebSocket surfaces. A frontend-only filter would still allow a user or agent to try a known profile URL. The backend therefore needs to decide every resource access before it connects to KasmVNC or Chrome.

## Validation completed in an isolated local deployment

- A viewer saw only its assigned sandbox; a direct request for another profile and a lifecycle request both returned the same not-found response.
- A Paperclip-style agent credential with `operate + automate` saw only its assigned sandbox, passed lifecycle authorization, reached scoped CDP, and received the same not-found response for a profile in another sandbox.
- Rotating an agent key invalidated the old key immediately.
- A disposable viewer-only login saw exactly one assigned profile and one live 1024 × 576 noVNC canvas, with no profile or access administration. Its captured frame contained real browser content. Deactivating the principal terminated the already-connected VNC session immediately; the test account remained inactive afterward.
- The public container check is a metadata-free `/health` endpoint. Runtime/profile counts in `/api/status` require authentication.
- The authenticated mobile acceptance gate passed 307/307 checks across five viewports plus the access dashboard, with 25 screenshots. It also verified compact 36 px controls, the open software-keyboard layout, account controls behind Tools, and a one-row composer. The deterministic UI gate injects the `codex-computer-use` host contract for boundary testing; it is not evidence of a production Codex Computer Use browser runtime on the VCVM.

No production deployment, upstream merge, public URL, live credential, or browser content is included in this draft.

## Questions for maintainers and Paperclip users

1. Is `sandbox_id` the right first scope, or should policies also support individual profile overrides from day one?
2. Should a Paperclip agent receive a long-lived rotating opaque key, or should the integration exchange short-lived credentials with a Paperclip instance?
3. Is a viewer-only VNC stream useful enough to ship in the first release, given that input must be strictly discarded server-side?
4. Which audit event fields are most useful without collecting browser contents, clipboard data, prompts, or credentials?
5. Are there Paperclip adapter hooks or credential-rotation conventions that should be matched before an optional bridge is added?

## Non-goals for the first release

- No browser-content recording or clipboard logging.
- No automatic sharing of an owner/admin token with agents.
- No public network exposure; deployment remains private/authenticated.
- No policy decision based solely on an agent prompt or a frontend assertion.

## Follow-up validation

Before an upstream merge, repeat the API/WebSocket matrix against the proposed target branch and confirm the mobile dashboard never displays a profile that its signed-in identity cannot access. For a physical iPhone acceptance test, private Tailnet HTTPS must first be enabled by the Tailnet administrator.
