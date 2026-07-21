# Mobile UI/UX and universal browser-action audit

Status: 21 July 2026 · r56 locally implemented and end-to-end verified

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
- Visual zoom and pane ratio update immediately. A real profile width/height change is saved for the next browser launch and is labelled accordingly.

### 2. Command dock

- **Full** opens the distraction-free live viewer.
- **Tools** opens the only settings/action sheet.
- **Chat** opens or collapses the task history.
- **Send** remains beside the text field in both collapsed and expanded chat states.
- Every visible interactive control is at least 44 by 44 CSS pixels.

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

This makes the action schema reusable by a harness adapter without binding the UI to a vendor API. For this product build, execution is intentionally stricter: the injected host must identify itself as `codex-computer-use`. Missing, generic or mislabeled bridges fail closed and keep the composer disabled. There is no local fake-success fallback and no browser credential is passed through the chat UI.

Current trust boundary: the provider identity is an implementation contract, not cryptographic attestation. A stronger deployment should add a signed or session-bound host handshake before broad external exposure.

## End-to-end evidence

The authenticated r56 gate ran the production container, selected a real browser profile, connected one live VNC canvas and exercised five device layouts plus the access dashboard.

| Surface | Result |
|---|---:|
| iPhone 14 portrait, 390 × 844 | 58 checks passed |
| iPhone SE portrait, 375 × 667 | 57 checks passed |
| iPhone Pro Max portrait, 430 × 932 | 59 checks passed |
| iPhone 14 landscape, 844 × 390 | 56 checks passed |
| Touch tablet portrait, 768 × 1024 | 56 checks passed |
| Authenticated access dashboard | 5 checks passed |
| Total | **291/291 checks, 23 screenshots** |

The gate verifies, among other things:

- exactly Full, Tools, Chat and Send as the compact primary controls;
- no benchmark navigation or harness picker;
- no horizontal document overflow;
- 44-pixel touch targets and coarse-pointer behavior;
- Tools/Chat mutual exclusion and keyboard shortcuts that do not steal input focus;
- a one-row composer with a visible Send control in collapsed and expanded chat;
- account/logout controls absent from the compact surface and present only inside Tools;
- live zoom, pane ratio, viewport persistence, fullscreen focus/inert behavior and one-canvas preservation;
- real VNC connection, pointer hit-testing, clipboard round-trip and manual iOS paste;
- honest session cards and the authenticated access dashboard.

Fresh verification also passed 79 frontend tests, 223 backend tests and the production frontend build. The release preview is loopback-only; physical iPhone Safari and private Tailnet HTTPS remain separate release gates.

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

The r56 UI is ready as a compact local mobile-web MVP. It is not yet evidence of a production Codex host bootstrap, physical iPhone Safari compatibility, public-network safety, WAN frame rate or touch-to-pixel latency. Those claims must stay blocked until a real host bridge, private HTTPS and physical-device measurements are present.
