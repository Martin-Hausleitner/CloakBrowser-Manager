import { describe, expect, it } from "vitest";
import {
  UI_STATE,
  UI_TRANSITIONS,
  assertUiFlowRegistry,
  expectUiState,
  isUiState,
  queryUiState,
} from "./uiFlowRegistry";

describe("uiFlowRegistry", () => {
  it("keeps transition contract shape, ids, and from/action pairs unique", () => {
    expect(() => assertUiFlowRegistry()).not.toThrow();
    expect(UI_TRANSITIONS.every((transition) => "action" in transition)).toBe(true);
    expect(UI_TRANSITIONS.every((transition) => !("via" in transition))).toBe(true);
    expect(UI_TRANSITIONS.every((transition) => transition.expectedVisible.includes(transition.to))).toBe(
      true,
    );

    expect(() =>
      assertUiFlowRegistry([
        ...UI_TRANSITIONS,
        {
          ...UI_TRANSITIONS[0],
          from: UI_TRANSITIONS[0].to,
        },
      ]),
    ).toThrow(/Duplicate UI transition id/);

    expect(() =>
      assertUiFlowRegistry([
        ...UI_TRANSITIONS,
        {
          ...UI_TRANSITIONS[0],
          id: "duplicate-from-action",
          to: UI_STATE.appDesktopAccounts,
        },
      ]),
    ).toThrow(/Duplicate UI transition from\/action/);
  });

  it("requires expectedVisible to include the target and only known states", () => {
    expect(() =>
      assertUiFlowRegistry([
        {
          id: "missing-target",
          from: UI_STATE.appDesktopHome,
          action: "sidebar.proxies",
          to: UI_STATE.appDesktopProxies,
          expectedVisible: [UI_STATE.appDesktopShell],
        },
      ]),
    ).toThrow(/must include target state/);

    expect(() =>
      assertUiFlowRegistry([
        {
          id: "unknown-expected",
          from: UI_STATE.appDesktopHome,
          action: "sidebar.proxies",
          to: UI_STATE.appDesktopProxies,
          expectedVisible: [UI_STATE.appDesktopProxies, "missing.state" as never],
        },
      ]),
    ).toThrow(/Unknown expected visible state/);
  });

  it("fails closed when a required state is absent or unknown", () => {
    const root = document.createElement("div");
    root.innerHTML = `<main data-ui-state="${UI_STATE.appDesktopHome}"></main>`;

    expect(isUiState(UI_STATE.appDesktopHome)).toBe(true);
    expect(queryUiState(root, UI_STATE.appDesktopHome)?.tagName).toBe("MAIN");
    expect(expectUiState(root, UI_STATE.appDesktopHome).tagName).toBe("MAIN");
    expect(() => expectUiState(root, UI_STATE.appDesktopAgentWorkspace)).toThrow(
      /Missing UI state/,
    );
    expect(() => expectUiState(root, "app.desktop.missing" as never)).toThrow(
      /Unknown UI state/,
    );
  });
});
