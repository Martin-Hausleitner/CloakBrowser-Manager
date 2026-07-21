# Mobile UI/UX and universal browser-action audit

Status: 21 July 2026 · r63 deployed on the VCVM and end-to-end verified

## Decision

The mobile workspace uses one dominant live browser, one compact command dock and one composer. Everything else is disclosed on demand. It deliberately does not copy Browser Use branding or proprietary UI assets. The interaction model is informed by Browser Use's documented live preview, streaming messages and follow-up-task flow, Apple's fullscreen and gesture guidance, WCAG 2.2 touch/focus requirements, and OpenAI's screen/mouse/keyboard computer-use loop.

The default mobile surface contains exactly four persistent actions: **Full**, **Tools**, **Chat** and **Send**. Chat and Tools are mutually exclusive. Chat starts collapsed while a browser is running. Account controls, viewport settings, session switching, profile administration, clipboard tools and typed browser actions live behind Tools. Benchmarks and quality reports stay outside the product UI.

Primary references:

- [Browser Use Chat UI tutorial](https://docs.browser-use.com/cloud/tutorials/chat-ui), [live preview](https://docs.browser-use.com/cloud/browser/live-preview) and [follow-up tasks](https://docs.browser-use.com/cloud/agent/follow-up-tasks)
- [OpenAI: Computer-Using Agent](https://openai.com/index/computer-using-agent/)
- [Apple: Going full screen](https://developer.apple.com/design/human-interface-guidelines/going-full-screen) and [Gestures](https://developer.apple.com/design/human-interface-guidelines/gestures)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and [What's New in WCAG 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/)

## Minimal information architecture

### 1. Live browser

- The VNC canvas is the primary surface and consumes all unused height.
- Portrait defaults to a browser-first split; the collapsed workspace allocates up to 82% of the visual height to the live pane.
- Landscape gives the live browser the main horizontal pane and keeps the control pane narrow.
- Fullscreen keeps the same live canvas rather than opening a second stream.
- Fullscreen exposes distinct Fit, Width and Height modes, visual zoom and a compact width/height editor without leaving the live canvas.
- Visual zoom and pane ratio update immediately. A real profile width/height change is saved and a running browser is deliberately restarted so the framebuffer really changes.
- While an iOS-style software keyboard is open, the root follows `visualViewport.height`, layout transitions are disabled and the browser remains above the composer. Nonessential dock controls disappear until the keyboard closes.

### 2. Command dock

- **Full** opens the distraction-free live viewer.
- **Tools** opens the only settings/action sheet.
- **Chat** opens or collapses the task history.
- **Send** remains beside the text field in both collapsed and expanded chat states.
- Primary buttons, login controls, access administration and paste actions use a compact 36 by 36 CSS-pixel floor with separation between adjacent actions. This is intentionally smaller than Apple's 44-point recommendation but remains above WCAG 2.2's 24-CSS-pixel minimum target size.
- Text fields retain a 16 px font size to prevent automatic iOS focus zoom even though their surrounding control is compact.

### 3. Progressive disclosure

Tools contains:

- Launch/Stop when the signed-in role may operate the profile.
- Three capability-gated Quick actions: Capture, Copy and Paste.
- View, Sessions and role-appropriate Admin disclosures.
- ProfileViewer actions such as CDP, clipboard and the manual iOS paste fallback.
- Signed-in identity and Log out at the bottom of the sheet, never as a permanent footer.

The Sessions grid is an honest session selector with name, state, platform and resolution. It does not fake live thumbnails or silently start multiple streams.

## Quick actions are not bookmarks

Quick actions are reusable, typed browser commands. They do not store destination URLs and they do not become another navigation bar. The current action vocabulary is:

`navigate`, `click`, `double_click`, `scroll`, `type_text`, `keypress`, `drag`, `move`, `wait`, `copy`, `paste`, `screenshot`, `viewport`, `fullscreen`, `focus_remote`, `focus_chat`.

Each command carries an ID, label, action kind, `ui` or `host` scope and optional structured arguments. A host reports the exact actions it supports. The UI enables a Quick action only when that capability is present; unknown action kinds are discarded at the bridge boundary.

This makes the action schema reusable by a harness adapter without binding the UI to a vendor API. For this product build, execution is intentionally stricter: the injected host must identify itself as `codex-computer-use`. Missing, generic or mislabeled bridges fail closed and keep the composer disabled. There is no local fake-success fallback and no browser credential is passed through the chat UI. The current VCVM/CLI environment does not itself provide a production Codex Computer Use browser runtime; the automated gate injects a deterministic host solely to verify this boundary. Server-backed task history persists messages but does not execute an agent by itself.

Current trust boundary: the provider identity is an implementation contract, not cryptographic attestation. A stronger deployment should add a signed or session-bound host handshake before broad external exposure.

## End-to-end evidence

The authenticated r63 gate ran the production container on the VCVM, selected a real browser profile, connected one live VNC canvas and exercised five device layouts plus the access dashboard. The Mac participated only as the browser/test client through the authenticated SSH tunnel.

| Surface | Result |
|---|---:|
| iPhone 14 portrait, 390 × 844 | 66 checks passed |
| iPhone SE portrait, 375 × 667 | 59 checks passed |
| iPhone Pro Max portrait, 430 × 932 | 61 checks passed |
| iPhone 14 landscape, 844 × 390 | 58 checks passed |
| Touch tablet portrait, 768 × 1024 | 58 checks passed |
| Authenticated access dashboard | 5 checks passed |
| Total | **307/307 checks, 25 screenshots** |

The gate verifies, among other things:

- exactly Full, Tools, Chat and Send as the compact primary controls;
- no benchmark navigation or harness picker;
- no horizontal document overflow;
- compact 36-pixel controls and coarse-pointer behavior;
- an emulated open software keyboard whose Visual Viewport, VNC pane, one-row composer and reachable Send action neither overlap nor leave the visible screen;
- Tools/Chat mutual exclusion and keyboard shortcuts that do not steal input focus;
- a one-row composer with a visible Send control in collapsed and expanded chat;
- account/logout controls absent from the compact surface and present only inside Tools;
- live zoom, pane ratio, viewport persistence, distinct fullscreen Fit/Width/Height behavior, fullscreen focus/inert behavior and one-canvas preservation;
- real VNC connection, pointer hit-testing, clipboard round-trip and manual iOS paste;
- honest session cards and the authenticated access dashboard.

Fresh verification also passed 106 frontend tests, 250 backend tests, 18 release/mobile/benchmark script tests, the production frontend build and the VCVM deployment-surface gate. A separate disposable viewer-only login saw exactly one assigned profile, rendered one live 1024 × 576 canvas, exposed no profile/access administration and disconnected immediately after the principal was deactivated. Its connected and revoked states were both captured and visually inspected.

The release preview remains loopback-only on the VCVM. The current Mac-to-VCVM Tailscale path is relayed rather than direct, and Tailscale Serve is disabled by tailnet policy; physical iPhone Safari and private Tailnet HTTPS therefore remain separate release gates.

## Ten highest-value next input and interaction improvements

1. **iOS IME bridge.** Use a controlled hidden input for composition events so predictive text, accented characters, emoji and non-Latin keyboards reach the remote browser reliably.
2. **Keyboard accessory strip.** Provide Esc, Tab, Enter, arrows, Backspace and modifier keys in one disclosure that does not cover the canvas.
3. **Direct-touch / trackpad mode.** Make the input model explicit instead of overloading the same gesture with remote click and local scrolling.
4. **Tap-to-control lock.** Require an intentional control mode before remote taps, preventing accidental clicks while the user scrolls the surrounding page.
5. **Paste sheet with preview and queue.** Separate task input from remote-browser paste, show destination state, and report success/failure without recording clipboard contents.
6. **User-defined Quick-action pins.** Let a profile pin typed, capability-checked commands such as Focus chat, Screenshot or Viewport preset; never turn pins into raw script or URL bookmarks.
7. **Risk confirmations.** Require an explicit confirmation for destructive or sensitive host actions, consistent with computer-use safety guidance.
8. **Reconnect input replay protection.** Drop stale pointer/key events across a stream reconnect and visibly restore control state.
9. **Server-backed task sessions.** Persist conversation IDs, task messages, follow-ups and cancellation state so chat history survives refresh without pretending a local component is an agent runtime.
10. **Touch-to-pixel telemetry and physical-device gate.** Measure touch dispatch to observed frame change on a real iPhone over Tailnet HTTPS, alongside reconnect, keyboard and fullscreen tests.

## Release boundary

The r63 UI is ready as a compact VCVM-hosted mobile-web MVP. It is not yet evidence of a production Codex host bootstrap, physical iPhone Safari compatibility, public-network safety, WAN frame rate or touch-to-pixel latency. Those claims must stay blocked until a real host bridge, private HTTPS and physical-device measurements are present.
