export const UI_STATE = {
  appAuthChecking: "app.auth.checking",
  appAuthError: "app.auth.error",
  appAccessDashboard: "app.access.dashboard",
  appDesktopShell: "app.desktop.shell",
  appDesktopHome: "app.desktop.home",
  appDesktopProxies: "app.desktop.proxies",
  appDesktopProfiles: "app.desktop.profiles",
  appDesktopAccounts: "app.desktop.accounts",
  appDesktopSessions: "app.desktop.sessions",
  appDesktopEmpty: "app.desktop.empty",
  appDesktopCreate: "app.desktop.create",
  appDesktopEdit: "app.desktop.edit",
  appDesktopAgentWorkspace: "app.desktop.agent-workspace",
  appMobileWorkspace: "app.mobile.workspace",
  appMobileProfileForm: "app.mobile.profile-form",
  accessDashboard: "access.dashboard",
  accessIdentities: "access.identities",
  accessGroups: "access.groups",
  agentWorkspace: "agent.workspace",
  agentSessionPane: "agent.session-pane",
  agentViewerPane: "agent.viewer-pane",
  agentViewerFullscreen: "agent.viewer-fullscreen",
  agentViewerGrid: "agent.viewer-grid",
  agentLiveMetrics: "agent.live-metrics",
  agentManagedOutput: "agent.managed-output",
  agentOrcaTranscript: "agent.orca-transcript",
  mobileWorkspace: "mobile.workspace",
  mobileLivePane: "mobile.live-pane",
  mobileLiveMetrics: "mobile.live-metrics",
  mobileControlPane: "mobile.control-pane",
  mobileBrowserFrame: "mobile.browser-frame",
  mobileToolsSheet: "mobile.tools-sheet",
  mobileViewportControls: "mobile.viewport-controls",
  mobileSessionGrid: "mobile.session-grid",
  mobileAdminTools: "mobile.admin-tools",
  mobileTaskChat: "mobile.task-chat",
  mobileFullscreenBrowser: "mobile.fullscreen-browser",
  profileViewer: "profile.viewer",
  profileViewerConnecting: "profile.viewer.connecting",
  profileViewerConnected: "profile.viewer.connected",
  profileViewerReconnecting: "profile.viewer.reconnecting",
  profileViewerFailed: "profile.viewer.failed",
  profileViewerViewOnly: "profile.viewer.view-only",
  proxyOverview: "proxy.overview",
  proxyOverviewLoading: "proxy.overview.loading",
  proxyOverviewEmpty: "proxy.overview.empty",
  proxyOverviewList: "proxy.overview.list",
  proxyOverviewError: "proxy.overview.error",
} as const;

export type UIStateId = (typeof UI_STATE)[keyof typeof UI_STATE];

export interface UIFlowTransition {
  from: UIStateId;
  action: string;
  to: UIStateId;
  expectedVisible: readonly UIStateId[];
  id?: string;
}

export const UI_TRANSITIONS = [
  {
    id: "desktop-home-to-proxies",
    from: UI_STATE.appDesktopHome,
    action: "sidebar.proxies",
    to: UI_STATE.appDesktopProxies,
    expectedVisible: [
      UI_STATE.appDesktopShell,
      UI_STATE.appDesktopProxies,
      UI_STATE.proxyOverview,
    ],
  },
  {
    id: "desktop-home-to-profiles",
    from: UI_STATE.appDesktopHome,
    action: "sidebar.profiles",
    to: UI_STATE.appDesktopProfiles,
    expectedVisible: [
      UI_STATE.appDesktopShell,
      UI_STATE.appDesktopProfiles,
    ],
  },
  {
    id: "desktop-home-to-access-dashboard",
    from: UI_STATE.appDesktopHome,
    action: "sidebar.access",
    to: UI_STATE.appAccessDashboard,
    expectedVisible: [
      UI_STATE.appAccessDashboard,
      UI_STATE.accessDashboard,
      UI_STATE.accessIdentities,
    ],
  },
  {
    id: "desktop-profile-select-to-agent-workspace",
    from: UI_STATE.appDesktopProfiles,
    action: "profile.select",
    to: UI_STATE.appDesktopAgentWorkspace,
    expectedVisible: [
      UI_STATE.appDesktopShell,
      UI_STATE.appDesktopAgentWorkspace,
      UI_STATE.agentWorkspace,
      UI_STATE.agentSessionPane,
      UI_STATE.agentViewerPane,
    ],
  },
  {
    id: "desktop-home-selected-launch-to-agent-workspace",
    from: UI_STATE.appDesktopHome,
    action: "home.launch-selected",
    to: UI_STATE.appDesktopAgentWorkspace,
    expectedVisible: [
      UI_STATE.appDesktopShell,
      UI_STATE.appDesktopAgentWorkspace,
      UI_STATE.agentWorkspace,
      UI_STATE.agentSessionPane,
      UI_STATE.agentViewerPane,
    ],
  },
  {
    id: "desktop-agent-viewer-to-fullscreen",
    from: UI_STATE.agentViewerPane,
    action: "agent.viewer.fullscreen",
    to: UI_STATE.agentViewerFullscreen,
    expectedVisible: [
      UI_STATE.agentWorkspace,
      UI_STATE.agentViewerPane,
      UI_STATE.agentViewerFullscreen,
    ],
  },
  {
    id: "desktop-agent-fullscreen-to-grid",
    from: UI_STATE.agentViewerFullscreen,
    action: "agent.viewer.grid",
    to: UI_STATE.agentViewerGrid,
    expectedVisible: [
      UI_STATE.agentWorkspace,
      UI_STATE.agentViewerPane,
      UI_STATE.agentViewerFullscreen,
      UI_STATE.agentViewerGrid,
    ],
  },
  {
    id: "mobile-workspace-to-tools",
    from: UI_STATE.mobileWorkspace,
    action: "mobile.command-dock.tools",
    to: UI_STATE.mobileToolsSheet,
    expectedVisible: [
      UI_STATE.mobileWorkspace,
      UI_STATE.mobileControlPane,
      UI_STATE.mobileToolsSheet,
    ],
  },
  {
    id: "mobile-tools-to-viewport",
    from: UI_STATE.mobileToolsSheet,
    action: "mobile.tools.viewport",
    to: UI_STATE.mobileViewportControls,
    expectedVisible: [
      UI_STATE.mobileWorkspace,
      UI_STATE.mobileToolsSheet,
      UI_STATE.mobileViewportControls,
    ],
  },
  {
    id: "mobile-tools-to-session-grid",
    from: UI_STATE.mobileToolsSheet,
    action: "mobile.tools.sessions",
    to: UI_STATE.mobileSessionGrid,
    expectedVisible: [
      UI_STATE.mobileWorkspace,
      UI_STATE.mobileToolsSheet,
      UI_STATE.mobileSessionGrid,
    ],
  },
  {
    id: "mobile-workspace-to-chat",
    from: UI_STATE.mobileWorkspace,
    action: "mobile.command-dock.chat",
    to: UI_STATE.mobileTaskChat,
    expectedVisible: [
      UI_STATE.mobileWorkspace,
      UI_STATE.mobileTaskChat,
    ],
  },
  {
    id: "mobile-workspace-to-fullscreen-browser",
    from: UI_STATE.mobileWorkspace,
    action: "mobile.command-dock.fullscreen",
    to: UI_STATE.mobileFullscreenBrowser,
    expectedVisible: [
      UI_STATE.mobileWorkspace,
      UI_STATE.mobileLivePane,
      UI_STATE.mobileFullscreenBrowser,
    ],
  },
  {
    id: "access-identities-to-groups",
    from: UI_STATE.accessIdentities,
    action: "access.tabs.groups",
    to: UI_STATE.accessGroups,
    expectedVisible: [
      UI_STATE.accessDashboard,
      UI_STATE.accessGroups,
    ],
  },
] as const satisfies readonly UIFlowTransition[];

const KNOWN_UI_STATES = new Set<UIStateId>(Object.values(UI_STATE));

export function isUiState(value: string): value is UIStateId {
  return KNOWN_UI_STATES.has(value as UIStateId);
}

export function uiStateAttr(...states: Array<UIStateId | false | null | undefined>): string {
  return states.filter(Boolean).join(" ");
}

export function queryUiState(root: ParentNode, state: UIStateId): HTMLElement | null {
  if (!isUiState(state)) {
    throw new Error(`Unknown UI state: ${state}`);
  }
  return root.querySelector<HTMLElement>(`[data-ui-state~="${state}"]`);
}

export function expectUiState(root: ParentNode, state: UIStateId): HTMLElement {
  const element = queryUiState(root, state);
  if (!element) {
    throw new Error(`Missing UI state: ${state}`);
  }
  return element;
}

export function assertUiFlowRegistry(
  transitions: readonly UIFlowTransition[] = UI_TRANSITIONS,
): void {
  const stateValues = Object.values(UI_STATE);
  const duplicateState = firstDuplicate(stateValues);
  if (duplicateState) {
    throw new Error(`Duplicate UI state id: ${duplicateState}`);
  }

  const transitionIds = transitions
    .map((transition) => transition.id)
    .filter((id): id is string => Boolean(id));
  const duplicateTransition = firstDuplicate(transitionIds);
  if (duplicateTransition) {
    throw new Error(`Duplicate UI transition id: ${duplicateTransition}`);
  }

  const duplicateFromAction = firstDuplicate(
    transitions.map((transition) => `${transition.from}\u0000${transition.action}`),
  );
  if (duplicateFromAction) {
    const [from, action] = duplicateFromAction.split("\u0000");
    throw new Error(`Duplicate UI transition from/action: ${from} ${action}`);
  }

  for (const transition of transitions) {
    if (!isUiState(transition.from)) {
      throw new Error(`Unknown transition source state: ${transition.from}`);
    }
    if (!isUiState(transition.to)) {
      throw new Error(`Unknown transition target state: ${transition.to}`);
    }
    if (!transition.action) {
      throw new Error(`UI transition ${transition.id} is missing a trigger`);
    }
    for (const state of transition.expectedVisible) {
      if (!isUiState(state)) {
        throw new Error(`Unknown expected visible state: ${state}`);
      }
    }
    if (!transition.expectedVisible.includes(transition.to)) {
      throw new Error(`UI transition ${transition.id} expectedVisible must include target state`);
    }
  }
}

function firstDuplicate(values: readonly string[]): string | null {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) return value;
    seen.add(value);
  }
  return null;
}
