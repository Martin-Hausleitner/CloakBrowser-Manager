# UI Flow Registry

The frontend exposes stable `data-ui-state` tokens for verifier scripts and browser
tests. Tokens are defined in `frontend/src/lib/uiFlowRegistry.ts`; use those
constants instead of hard-coded selector strings.

## Contract

- `UI_STATE` is the canonical list of state ids.
- `UI_TRANSITIONS` records supported navigation edges as:
  `{ from, action, to, expectedVisible, id? }`.
- `expectedVisible` lists the states a verifier should require after the
  transition. It must include `to`.
- Registry validation rejects duplicate ids, duplicate `(from, action)` pairs,
  unknown states, and transitions whose `expectedVisible` omits the target.
- `expectUiState(root, state)` fails closed:
  - unknown states throw `Unknown UI state`
  - known but absent states throw `Missing UI state`

## Usage

```ts
import { UI_STATE, expectUiState } from "../src/lib/uiFlowRegistry";

expectUiState(document.body, UI_STATE.appDesktopAgentWorkspace);
expectUiState(document.body, UI_STATE.agentViewerPane);
```

State tokens are metadata only. They must not drive styling or product behavior.
The registry includes explicit verifier paths for desktop profiles, task launch,
access controls, desktop/mobile fullscreen, mobile sessions, proxy overview, and
the mobile task chat.
