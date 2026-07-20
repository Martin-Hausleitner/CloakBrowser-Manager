# Draft: scoped browser access for Paperclip agents

> This is a discussion draft. It contains no live credentials, profile names, network addresses, or browser content.

## Context

We are adding a mobile VNC workspace to CloakBrowser Manager. The next security milestone is to let a Paperclip-managed agent access only explicitly assigned browser sandboxes, while human administrators can decide which users can view, interact with, or automate each sandbox.

## Proposal

- Add an explicit `sandbox_id` to each browser profile.
- Add local human identities and scoped agent credentials.
- Store only password/API-key hashes; show a generated agent key only once.
- Enforce scopes in the backend for profile discovery, VNC, clipboard, CDP REST, and CDP WebSockets.
- Separate `view`, `interact`, `operate`, and `automate`; CDP is never implied by view access.
- Use a compact admin dashboard to manage people, agent identities, sandbox grants, rotation, and deactivation.
- Keep a bootstrap admin path for safe migration and make enforcement opt-in until tested.

## Why not rely on the UI alone?

VNC and CDP are direct API/WebSocket surfaces. A frontend-only filter would still allow a user or agent to try a known profile URL. The backend therefore needs to decide every resource access before it connects to KasmVNC or Chrome.

## Questions for maintainers and Paperclip users

1. Is `sandbox_id` the right first scope, or should policies also support individual profile overrides from day one?
2. Should a Paperclip agent receive a long-lived rotating opaque key, or should the integration exchange short-lived credentials with a Paperclip instance?
3. Is a viewer-only VNC stream useful enough to ship in the first release, given that input must be strictly discarded server-side?
4. Which audit event fields are most useful without collecting browser contents, clipboard data, prompts, or credentials?
5. Are there Paperclip adapter hooks or credential-rotation conventions that should be matched before implementing the bridge?

## Non-goals for the first release

- No browser-content recording or clipboard logging.
- No automatic sharing of an owner/admin token with agents.
- No public network exposure; deployment remains private/authenticated.
- No policy decision based solely on an agent prompt or a frontend assertion.

## Validation plan

Automated tests will prove that unauthorized profile, VNC, clipboard, and CDP requests fail; authorized scope-matched requests succeed; rotating an agent credential invalidates the old key; and the mobile dashboard never displays a profile that its signed-in identity cannot access.
