# Settings-first Browser Use workspace

Date: 2026-07-29

## Goal

Move all provider, model, harness, ACP/ACPX and browser-tool configuration out of the live chat surface into a dedicated Settings tab. Keep the live workspace focused on the conversation and browser while preserving the same routing state and launch behavior.

## Chosen approach

Use a first-class `settings` application view in the existing left navigation. This is preferable to a drawer or modal because the provider and tool inventories are long, include readiness and smoke-test actions, and must work on desktop, full view and mobile without covering the browser.

## Information architecture

### Left navigation

Add `Settings` beside Agent home, Proxies, Profiles, Accounts and Sessions. It opens a dedicated full-width settings workspace.

### Settings workspace

The page owns these controls:

- runtime mode: CLI, ACP or ACPX;
- installed harness and local CLI selection;
- normalized ACP/ACPX provider selection;
- provider transport and model alias;
- browser-tool enablement and canonical order for Unbrowse, Stagehand and Browser Harness;
- readiness status, refresh and smoke-test actions;
- selected browser profile used for tests;
- concise explanations for unavailable/auth-required states.

Settings use one shared routing state with the live workspace. A choice made here is applied to the next run for the selected profile without duplicating controls in the chat.

### Live chat/workspace

The left agent pane contains only:

- one compact identity row: browser/session name, run state and one Settings link;
- one compact runtime row: CLI, ACP, ACPX and a single truncated active route summary;
- typed output or terminal content;
- one compact composer and stop/send action;
- optional collapsed session details.

The provider matrix, model dropdowns, browser-tool editor, harness inventory and smoke-test list must not render inline in the chat or in full-view overlays.

### Full view and mobile

Full view retains browser controls such as viewport, PhoneFit, zoom, screenshot, sessions and exit. It does not duplicate provider configuration. A compact Settings action returns to or opens the dedicated Settings view without creating a second browser tab.

Mobile uses the same principle: runtime mode and active route are visible; editing provider/model/tool configuration happens in the Settings surface, rendered as a single-column page.

## Component boundaries

- `HarnessSettingsWorkspace`: dedicated page and owner of detailed harness/provider/browser-tool controls.
- `ProviderToolControl`: reusable detailed editor, rendered only by Settings after this change.
- `AgentBrowserWorkspace`: consumes routing state and renders only the compact summary plus run controls.
- `App`: owns application view selection and the shared routing/settings state required by both workspaces.

Existing provider readiness, ACPX preflight, harness presence and smoke-test APIs are reused. No second browser-control protocol or dependency is introduced.

## Behavior and error handling

- Unavailable providers and tools remain visible in Settings with an honest reason and disabled selection.
- Active runs cannot silently change their provider route; edits apply to the next run or are disabled while a run is active.
- Leaving Settings preserves the selected provider, model, tools and mode.
- A failed readiness refresh or smoke test appears inline in Settings and does not pollute the chat timeline.
- The live viewer must not remount when the user opens Settings or switches CLI/ACP/ACPX.

## Acceptance criteria

1. Sidebar contains a dedicated `Settings` navigation item.
2. Provider/model/tool matrices are absent from the normal chat and full-view browser overlay.
3. The chat header fits in two compact rows at desktop width and does not exceed the current pane width.
4. Settings exposes all existing provider, model, harness and browser-tool functionality without loss.
5. Settings state is shared with the next launch in the selected live profile.
6. No duplicate provider or harness selectors are visible simultaneously.
7. Desktop and mobile layouts remain usable at 390x844, 768x1024 and 1440x900.
8. Existing same-profile, no-second-browser and canonical tool-order safeguards remain intact.
9. Targeted React tests, the full frontend suite and production build pass.
10. Public VCVM validation confirms navigation, compact chat, Settings controls, CDP/VNC viewer continuity and no page/console errors.

## Non-goals

- Replacing ACP/ACPX or browser-tool backends.
- Adding a new UI framework or design dependency.
- Reworking proxy, account or profile data models.
- Duplicating Browser Use Cloud branding or protected assets.

## Self-review

The scope is limited to information architecture and control placement. It introduces no new protocol, dependency or second browser. The design explicitly preserves all routing behavior and defines desktop, full-view and mobile acceptance evidence.
