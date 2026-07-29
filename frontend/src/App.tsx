import { useState, useCallback, useEffect, useRef } from "react";
import { ArrowLeft, Lock, PanelLeftClose, PanelLeft, ShieldCheck, Globe2, LayoutGrid, Users, KeyRound, History, Settings2 } from "lucide-react";
import { useProfiles } from "./hooks/useProfiles";
import {
  api,
  setOnUnauthorized,
  type Account,
  type AccessIdentity,
  type AccessPermission,
  type Profile,
  type ProfileCreateData,
  type ProfileHarness,
  type TaskOutput,
} from "./lib/api";
import { hasAccessPermission } from "./lib/accessPermissions";
import {
  ACTIVE_TASK_RUN_STATES,
  forgetBrowserUseRun,
  readRememberedBrowserUseRun,
} from "./lib/managedTaskRunStorage";
import { UI_STATE, type UIStateId } from "./lib/uiFlowRegistry";
import { ProfileList } from "./components/ProfileList";
import { ProfileForm } from "./components/ProfileForm";
import { CreateProfileFlow } from "./components/CreateProfileFlow";
import { ProfileViewer } from "./components/ProfileViewer";
import { LaunchButton } from "./components/LaunchButton";
import { StatusIndicator } from "./components/StatusIndicator";
import { LoginPage } from "./components/LoginPage";
import { MobileSplitScreen } from "./components/mobile/MobileSplitScreen";
import { AccessDashboard } from "./components/AccessDashboard";
import { ProfileHealthSummary } from "./components/ProfileHealthSummary";
import { BrowserUseHome } from "./components/BrowserUseHome";
import { ProxyOverview } from "./components/ProxyOverview";
import { ProfilesWorkspace } from "./components/ProfilesWorkspace";
import { AccountsOverview } from "./components/AccountsOverview";
import { SessionsOverview } from "./components/SessionsOverview";
import { LiveDevPanel } from "./components/LiveDevPanel";
import { SessionStreamButtons } from "./components/SessionStreamButtons";
import { AgentBrowserWorkspace } from "./components/workspace/AgentBrowserWorkspace";
import { HarnessSettingsWorkspace } from "./components/HarnessSettingsWorkspace";
import { WorkspaceRuntimeConfigProvider } from "./components/workspace/WorkspaceRuntimeConfig";

type AuthState = "checking" | "required" | "ok" | "error";
type View = "home" | "empty" | "create" | "edit" | "view" | "access" | "proxies" | "profiles" | "accounts" | "sessions" | "settings";
const MOBILE_WORKSPACE_QUERY = "(max-width: 767px), (pointer: coarse) and (max-width: 1024px)";
type MobileConnectionStatus = "connecting" | "connected" | "reconnecting" | "failed";
const FIXED_PROJECTS = ["default", "proxied", "mobile", "research"] as const;

interface InitialPromptDraft {
  id: string;
  profileId: string;
  task: string;
}

interface ApplyProfileViewportOptions {
  profile: Profile | null;
  width: number;
  height: number;
  canManageProfiles: boolean;
  canOperateProfile: boolean;
  update: (id: string, data: Partial<ProfileCreateData>) => Promise<Profile | undefined>;
  stop: (id: string) => Promise<boolean>;
  launch: (id: string) => Promise<unknown>;
}

export async function applyProfileViewport({
  profile,
  width,
  height,
  canManageProfiles,
  canOperateProfile,
  update,
  stop,
  launch,
}: ApplyProfileViewportOptions) {
  if (!profile || !canManageProfiles) return false;
  if (profile.status === "running" && !canOperateProfile) return false;

  const updatedProfile = await update(profile.id, { screen_width: width, screen_height: height });
  if (!updatedProfile) return false;
  if (profile.status !== "running") return true;

  const stopped = await stop(profile.id);
  if (!stopped) return false;

  const result = await launch(updatedProfile.id);
  return !!result;
}

interface ToggleProfilePinOptions {
  profile: Profile | null;
  canManageProfiles: boolean;
  update: (id: string, data: Partial<ProfileCreateData>) => Promise<Profile | undefined>;
}

export async function toggleProfilePin({
  profile,
  canManageProfiles,
  update,
}: ToggleProfilePinOptions) {
  if (!profile || !canManageProfiles) return false;
  const updated = await update(profile.id, { pinned: !profile.pinned });
  return Boolean(updated);
}

export default function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [authRequired, setAuthRequired] = useState(false);
  const [accessControlEnabled, setAccessControlEnabled] = useState(false);
  const [identity, setIdentity] = useState<AccessIdentity | null>(null);

  const refreshAuth = useCallback(async () => {
    const status = await api.authStatus();
    setAuthRequired(status.auth_required);
    setAccessControlEnabled(status.access_control_enabled);
    setIdentity(status.identity);
    setAuthState(!status.auth_required || status.authenticated ? "ok" : "required");
  }, []);

  useEffect(() => {
    setOnUnauthorized(() => {
      setIdentity(null);
      setAuthState("required");
    });

    refreshAuth()
      .catch((err) => {
        console.warn("[auth] status check failed:", err);
        setAuthState("error");
      });

    return () => setOnUnauthorized(null);
  }, [refreshAuth]);

  if (authState === "checking") {
    return (
      <div className="h-screen flex items-center justify-center">
        <div className="text-gray-500 text-sm" data-ui-state={UI_STATE.appAuthChecking}>Loading...</div>
      </div>
    );
  }

  if (authState === "error") {
    return (
      <div className="h-screen flex items-center justify-center bg-surface-0" data-ui-state={UI_STATE.appAuthError}>
        <div className="text-center">
          <p className="text-red-400 text-sm mb-2">Unable to reach the server</p>
          <button
            onClick={() => {
              setAuthState("checking");
              refreshAuth()
                .catch(() => setAuthState("error"));
            }}
            className="text-xs text-gray-400 hover:text-gray-200 underline"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (authState === "required") {
    return <LoginPage accessControlEnabled={accessControlEnabled} onSuccess={refreshAuth} />;
  }

  return (
    <WorkspaceRuntimeConfigProvider>
      <AppContent
        authRequired={authRequired}
        accessControlEnabled={accessControlEnabled}
        identity={identity}
        onLogout={async () => {
          await api.logout();
          setIdentity(null);
          setAuthState(authRequired ? "required" : "ok");
        }}
      />
    </WorkspaceRuntimeConfigProvider>
  );
}

interface AppContentProps {
  authRequired: boolean;
  accessControlEnabled: boolean;
  identity: AccessIdentity | null;
  onLogout: () => void;
}

function AppContent({ authRequired, accessControlEnabled, identity, onLogout }: AppContentProps) {
  const { profiles, loading, error, refresh, create, update, remove, launch, stop } = useProfiles();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [view, setView] = useState<View>("home");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileFullscreenOpen, setMobileFullscreenOpen] = useState(false);
  const [mobileBrowserZoom, setMobileBrowserZoom] = useState(100);
  const [mobileRemoteToolsOpen, setMobileRemoteToolsOpen] = useState(false);
  const [mobileConnectionStatus, setMobileConnectionStatus] = useState<MobileConnectionStatus>("connecting");
  const [mobileTaskOutputs, setMobileTaskOutputs] = useState<TaskOutput[]>([]);
  const [workspaceRunActive, setWorkspaceRunActive] = useState(false);
  const [projectId, setProjectId] = useState<string>("default");
  const [harness, setHarness] = useState<ProfileHarness>("browser-use");
  const [taskDraft, setTaskDraft] = useState("");
  const [initialPromptDraft, setInitialPromptDraft] = useState<InitialPromptDraft | null>(null);
  const initialPromptDraftCounter = useRef(0);
  const isMobile = useIsMobile();

  const selected = profiles.find((p) => p.id === selectedId) ?? null;
  const canManageProfiles = isAdministrator(identity);
  const canOperateSelected = Boolean(selected && canAccess(identity, selected, "operate"));
  const canInteractSelected = Boolean(selected && canAccess(identity, selected, "interact"));
  const canAutomateSelected = Boolean(selected && canAccess(identity, selected, "automate"));
  const projects = Array.from(
    new Set<string>([
      ...FIXED_PROJECTS,
      ...profiles.map((profile) => profile.project_id || "default"),
    ]),
  );

  useEffect(() => {
    if (view !== "accounts") return;

    let cancelled = false;
    setAccountsLoading(true);
    setAccountsError(null);
    api.listAccounts()
      .then((items) => {
        if (!cancelled) setAccounts(items);
      })
      .catch((err) => {
        if (!cancelled) {
          setAccountsError(err instanceof Error ? err.message : "Unable to load accounts");
        }
      })
      .finally(() => {
        if (!cancelled) setAccountsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [view]);

  useEffect(() => {
    if (!isMobile || loading || profiles.length === 0) return;

    const selectedStillExists = selectedId
      ? profiles.some((profile) => profile.id === selectedId)
      : false;
    if (selectedStillExists) return;

    const nextProfile = profiles.find((profile) => profile.status === "running") ?? profiles[0];
    if (!nextProfile) return;

    setSelectedId(nextProfile.id);
    setView("view");
  }, [isMobile, loading, profiles, selectedId]);

  useEffect(() => {
    setMobileTaskOutputs([]);
    setWorkspaceRunActive(false);

    if (!isMobile || !selected) return;

    const rememberedRunId = readRememberedBrowserUseRun(selected.id);
    if (!rememberedRunId) return;

    const controller = new AbortController();
    let cancelled = false;
    let pollTimer: number | null = null;

    const stopPolling = () => {
      if (pollTimer != null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const refreshRememberedRun = async () => {
      try {
        const [run, outputs] = await Promise.all([
          api.getTaskRun(rememberedRunId, { signal: controller.signal }),
          api.listTaskRunOutputs(rememberedRunId, { signal: controller.signal }),
        ]);
        if (cancelled) return;
        if (run.profile_id_snapshot !== selected.id) {
          forgetBrowserUseRun(selected.id);
          setMobileTaskOutputs([]);
          setWorkspaceRunActive(false);
          stopPolling();
          return;
        }
        setMobileTaskOutputs(outputs);
        const runActive = ACTIVE_TASK_RUN_STATES.has(run.status);
        setWorkspaceRunActive(runActive);
        if (!runActive) {
          stopPolling();
        }
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        console.warn("[mobile-task-output] remembered run restore failed:", err);
        forgetBrowserUseRun(selected.id);
        setMobileTaskOutputs([]);
        setWorkspaceRunActive(false);
        stopPolling();
      }
    };

    void refreshRememberedRun();
    pollTimer = window.setInterval(() => void refreshRememberedRun(), 1500);

    return () => {
      cancelled = true;
      controller.abort();
      setWorkspaceRunActive(false);
      stopPolling();
    };
  }, [isMobile, selected?.id, selected?.screen_height, selected?.screen_width]);

  // Deep-link from the Cloak Profile Sync extension (?profile=<id>).
  useEffect(() => {
    if (loading || profiles.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const profileParam = params.get("profile");
    if (!profileParam) return;
    const match = profiles.find((profile) => profile.id === profileParam);
    if (!match) return;
    setSelectedId(match.id);
    if (match.project_id) setProjectId(match.project_id);
    if (match.harness) setHarness(match.harness);
    const viewParam = params.get("view");
    const wantFullscreen = params.get("fullscreen") === "1";
    if (match.status === "running") {
      setView("view");
      if (wantFullscreen || viewParam === "vnc") {
        setMobileFullscreenOpen(true);
      }
    } else {
      setView("home");
    }
  }, [loading, profiles]);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    const profile = profiles.find((p) => p.id === id);
    if (profile?.project_id) setProjectId(profile.project_id);
    if (profile?.harness) setHarness(profile.harness);
    // Desktop and mobile both open the live workspace for the selected profile.
    setView("view");
  }, [profiles]);

  const handleNew = useCallback(() => {
    if (!canManageProfiles) return;
    setSelectedId(null);
    setView("create");
  }, [canManageProfiles]);

  const handleCreate = useCallback(async (data: ProfileCreateData) => {
    if (!canManageProfiles) return;
    const profile = await create({
      ...data,
      project_id: data.project_id || projectId,
      harness: data.harness || harness,
    });
    if (profile) {
      setSelectedId(profile.id);
      setView("edit");
    }
  }, [canManageProfiles, create, harness, projectId]);

  const handleUpdate = useCallback(async (data: ProfileCreateData) => {
    if (!selectedId || !canManageProfiles) return;
    await update(selectedId, data);
  }, [canManageProfiles, selectedId, update]);

  const handleDelete = useCallback(async () => {
    if (!selectedId || !canManageProfiles) return;
    await remove(selectedId);
    setSelectedId(null);
    setView("home");
  }, [canManageProfiles, selectedId, remove]);

  const handleLaunch = useCallback(async () => {
    if (!selectedId || !canAccess(identity, profiles.find((profile) => profile.id === selectedId) ?? null, "operate")) return;
    const result = await launch(selectedId);
    if (result) setView("view");
  }, [identity, profiles, selectedId, launch]);

  const handOffTaskDraft = useCallback((profileId: string) => {
    if (!taskDraft) return;
    initialPromptDraftCounter.current += 1;
    setInitialPromptDraft({
      id: `${profileId}:${initialPromptDraftCounter.current}`,
      profileId,
      task: taskDraft,
    });
  }, [taskDraft]);

  const handleStop = useCallback(async () => {
    if (!selectedId || !canAccess(identity, profiles.find((profile) => profile.id === selectedId) ?? null, "operate")) return;
    await stop(selectedId);
    setView(canManageProfiles ? "edit" : "home");
  }, [canManageProfiles, identity, profiles, selectedId, stop]);

  const handleVncDisconnect = useCallback(() => {
    setView(isMobile ? "view" : canManageProfiles ? "edit" : "home");
  }, [canManageProfiles, isMobile]);

  const handleViewportApply = useCallback(async (width: number, height: number) => {
    return applyProfileViewport({
      profile: selected,
      width,
      height,
      canManageProfiles,
      canOperateProfile: canOperateSelected,
      update,
      stop,
      launch,
    });
  }, [canManageProfiles, canOperateSelected, launch, selected, stop, update]);

  const handleWorkspaceRunActivityChange = useCallback((active: boolean) => {
    setWorkspaceRunActive(active);
  }, []);


  const handleTogglePin = useCallback(async (id: string) => {
    const profile = profiles.find((candidate) => candidate.id === id) ?? null;
    await toggleProfilePin({
      profile,
      canManageProfiles,
      update,
    });
  }, [canManageProfiles, profiles, update]);

  const showTableTabs = isTableView(view);

  if (view === "access" && canManageProfiles && accessControlEnabled) {
    return <AccessDashboard onClose={() => setView(selected ? "view" : "home")} />;
  }

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center">
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (isMobile) {
    if (canManageProfiles && (view === "create" || (view === "edit" && selected))) {
      const editing = view === "edit" && selected;

      return (
        <div
          className="flex h-dvh flex-col overflow-hidden bg-surface-0"
          data-ui-state={UI_STATE.appMobileProfileForm}
        >
          <div className="flex items-center justify-between border-b border-border bg-surface-1 px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setView(selected ? "view" : "empty")}
                className="mobile-icon-button"
                aria-label="Back to mobile browser workspace"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">
                  {editing ? selected.name : "New browser profile"}
                </p>
                <p className="text-[11px] text-gray-500">
                  {editing ? "Profile settings" : "Create a reusable profile"}
                </p>
              </div>
            </div>
            {editing ? (
              <LaunchButton
                status={selected.status}
                onLaunch={handleLaunch}
                onStop={handleStop}
              />
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            <ProfileForm
              profile={editing ? selected : null}
              onSave={editing ? handleUpdate : handleCreate}
              onDelete={editing ? handleDelete : undefined}
              onCancel={() => setView(selected ? "view" : "empty")}
            />
          </div>
        </div>
      );
    }


    if (view === "settings") {
      return (
        <HarnessSettingsWorkspace
          mobile
          profiles={profiles}
          selectedProfile={selected}
          onBack={() => setView(selected ? "view" : "home")}
          runActive={workspaceRunActive}
          onSelectProfile={(profileId) => {
            setSelectedId(profileId);
            const profile = profiles.find((item) => item.id === profileId);
            if (profile?.project_id) setProjectId(profile.project_id);
            if (profile?.harness) setHarness(profile.harness);
          }}
        />
      );
    }

    const browserView =
      selected && selected.status === "running" ? (
        <ProfileViewer
          key={selected.id}
          profileId={selected.id}
          cdpUrl={selected.cdp_url}
          clipboardSync={selected.clipboard_sync}
          canInteract={canInteractSelected}
          compactControls
          layoutMode={mobileFullscreenOpen ? "fullscreen" : "inline"}
          viewportScale={mobileBrowserZoom / 100}
          remoteToolsOpen={mobileRemoteToolsOpen}
          remoteToolsPortalId="mobile-remote-browser-tools-portal"
          onRemoteToolsOpenChange={setMobileRemoteToolsOpen}
          onConnectionStatusChange={setMobileConnectionStatus}
          onDisconnect={handleVncDisconnect}
        />
      ) : null;

    return (
      <>
        <MobileSplitScreen
          profiles={profiles}
          selected={selected}
          selectedId={selectedId}
          error={error}
          authRequired={authRequired}
          canManageProfiles={canManageProfiles}
          canOperate={canOperateSelected}
          canInteract={canInteractSelected}
          canManageAccess={canManageProfiles && accessControlEnabled}
          identityName={identity?.display_name ?? null}
          browserView={browserView}
          liveMetricsView={
            selected ? (
              <LiveDevPanel
                profileId={selected.id}
                running={selected.status === "running"}
                connectionStatus={mobileConnectionStatus}
                variant="mobile"
              />
            ) : null
          }
          browserZoom={mobileBrowserZoom}
          taskOutputs={mobileTaskOutputs}
          browserConnectionStatus={selected?.status === "running" ? mobileConnectionStatus : null}
          remoteToolsOpen={mobileRemoteToolsOpen}
          onRemoteToolsOpenChange={setMobileRemoteToolsOpen}
          onSelect={handleSelect}
          onNew={handleNew}
          onEdit={() => setView("edit")}
          onLaunch={handleLaunch}
          onStop={handleStop}
          onViewportApply={handleViewportApply}
          onFullscreenChange={setMobileFullscreenOpen}
          onBrowserZoomChange={setMobileBrowserZoom}
          onAccessControls={() => setView("access")}
          onLogout={onLogout}
          onOpenSettings={() => setView("settings")}
        />
      </>
    );
  }

  return (
    <div className="h-screen flex" data-ui-state={UI_STATE.appDesktopShell}>
      {/* Compact Browser-Use style sidebar */}
      {sidebarOpen && (
        <div className="w-48 border-r border-border bg-surface-1 flex-shrink-0 flex flex-col">
          <div className="border-b border-border px-2 py-2">
            <div className="px-1 text-xs font-semibold tracking-tight">Browser Use</div>
            <div className="mt-1.5 space-y-0.5">
              <button
                type="button"
                onClick={() => setView("home")}
                className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                  view === "home" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <LayoutGrid className="h-3 w-3" />
                Agent home
              </button>
              {canManageProfiles ? (
                <button
                  type="button"
                  onClick={() => setView("proxies")}
                  className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                    view === "proxies" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                  }`}
                >
                  <Globe2 className="h-3 w-3" />
                  Proxies
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => setView("profiles")}
                className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                  view === "profiles" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <Users className="h-3 w-3" />
                Profiles
              </button>
              <button
                type="button"
                onClick={() => setView("accounts")}
                className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                  view === "accounts" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <KeyRound className="h-3 w-3" />
                Accounts
              </button>
              <button
                type="button"
                onClick={() => setView("sessions")}
                className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                  view === "sessions" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <History className="h-3 w-3" />
                Sessions
              </button>
              <button
                type="button"
                onClick={() => setView("settings")}
                className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] ${
                  view === "settings" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <Settings2 className="h-3 w-3" />
                Settings
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1">
            <ProfileList
              profiles={profiles}
              selectedId={selectedId}
              onSelect={handleSelect}
              onNew={handleNew}
              canCreate={canManageProfiles}
              canManage={canManageProfiles}
              onTogglePin={handleTogglePin}
              compact
            />
          </div>
        </div>
      )}

      {/* Main panel */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-surface-1">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-gray-500 hover:text-gray-300 p-1"
              title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            >
              {sidebarOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
            </button>
            {selected && view !== "home" && view !== "proxies" && view !== "profiles" && view !== "accounts" && view !== "sessions" && view !== "settings" && (
              <div className="flex items-center gap-2">
                <StatusIndicator status={selected.status} size="md" />
                <span className="text-sm font-medium">{selected.name}</span>
                <span className="text-xs text-gray-500 capitalize">{selected.platform}</span>
                <ProfileHealthSummary
                  profileId={selected.id}
                  canRun={canOperateSelected}
                  running={selected.status === "running"}
                />
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {selected && selected.status === "running" ? (
              <SessionStreamButtons profileId={selected.id} running />
            ) : null}
            {selected && view !== "home" && view !== "proxies" && view !== "profiles" && view !== "accounts" && view !== "sessions" && view !== "settings" && (
              canOperateSelected && (
              <LaunchButton
                status={selected.status}
                onLaunch={handleLaunch}
                onStop={handleStop}
              />
              )
            )}
            {accessControlEnabled && canManageProfiles && (
              <button
                onClick={() => setView("access")}
                className="text-gray-500 hover:text-gray-300 p-1"
                title="Browser access controls"
                aria-label="Browser access controls"
              >
                <ShieldCheck className="h-4 w-4" />
              </button>
            )}
            {identity && (
              <span className="hidden max-w-40 truncate text-xs text-gray-500 sm:inline">
                {identity.display_name}
              </span>
            )}
            {authRequired && (
              <button
                onClick={onLogout}
                className="text-gray-500 hover:text-gray-300 p-1"
                title="Log out"
              >
                <Lock className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {showTableTabs ? (
          <div className="border-b border-border bg-surface-0 px-4 py-2">
            <div className="inline-flex rounded-lg border border-border bg-surface-1 p-1" role="tablist" aria-label="Tables workspace">
              {[
                { id: "profiles", label: "Profiles", available: true },
                { id: "accounts", label: "Accounts & 2FA", available: true },
                { id: "proxies", label: "Proxies", available: canManageProfiles },
                { id: "sessions", label: "Sessions", available: true },
              ].filter((tab) => tab.available).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={view === tab.id}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium ${
                    view === tab.id ? "bg-surface-3 text-gray-100" : "text-gray-500 hover:text-gray-200"
                  }`}
                  onClick={() => setView(tab.id as View)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {/* Error banner */}
        {error && (
          <div className="px-4 py-2 bg-red-600/15 border-b border-red-600/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {selected && view === "view" ? (
          <LiveDevPanel
            profileId={selected.id}
            running={selected.status === "running"}
            connectionStatus={mobileConnectionStatus}
          />
        ) : null}

        {/* Content */}
        <div
          className="flex-1 overflow-hidden overscroll-contain"
          data-ui-state={desktopViewState(view)}
        >
          {view === "home" && (
            <BrowserUseHome
              projects={projects}
              projectId={projectId}
              profiles={profiles}
              task={taskDraft}
              selectedProfile={selected}
              onProjectChange={(nextProjectId) => {
                setProjectId(nextProjectId);
                if (!selected || (selected.project_id || "default") !== nextProjectId) {
                  setSelectedId(null);
                }
              }}
              onTaskChange={setTaskDraft}
              onSelectProfile={(profileId) => {
                setSelectedId(profileId);
                const profile = profiles.find((item) => item.id === profileId);
                if (profile?.project_id) setProjectId(profile.project_id);
                if (profile?.harness) setHarness(profile.harness);
              }}
              onOpenSettings={(profileId) => {
                if (!profileId) {
                  handleNew();
                  return;
                }
                setSelectedId(profileId);
                setView(canManageProfiles ? "edit" : "home");
              }}
              onLaunchSelected={async () => {
                if (!selected) return;
                handOffTaskDraft(selected.id);
                if (selected.status === "running") {
                  setView("view");
                  return;
                }
                await handleLaunch();
              }}
            />
          )}

          {view === "proxies" && canManageProfiles && (
            <ProxyOverview
              harness={harness}
              projectId={projectId || "proxied"}
              onProfileCreated={async (profile) => {
                await refresh();
                setSelectedId(profile.id);
                setProjectId(profile.project_id || "proxied");
                setHarness(profile.harness);
                setView("edit");
              }}
            />
          )}

          {view === "profiles" && (
            <ProfilesWorkspace
              profiles={profiles}
              selectedId={selectedId}
              canManage={canManageProfiles}
              onSelect={(profileId) => {
                setSelectedId(profileId);
                const profile = profiles.find((item) => item.id === profileId);
                if (profile?.project_id) setProjectId(profile.project_id);
                if (profile?.harness) setHarness(profile.harness);
              }}
              onEdit={(profileId) => {
                setSelectedId(profileId);
                setView(canManageProfiles ? "edit" : "profiles");
              }}
            />
          )}

          {view === "accounts" && (
            <AccountsOverview
              profiles={profiles}
              accounts={accounts}
              loading={accountsLoading}
              error={accountsError}
              selectedId={selectedId}
              onSelect={(profileId) => {
                setSelectedId(profileId);
                const profile = profiles.find((item) => item.id === profileId);
                if (profile?.project_id) setProjectId(profile.project_id);
                if (profile?.harness) setHarness(profile.harness);
                setView("view");
              }}
            />
          )}

          {view === "sessions" && (
            <SessionsOverview
              profiles={profiles}
              selectedId={selectedId}
              onSelectProfile={(profileId) => {
                setSelectedId(profileId);
                const profile = profiles.find((item) => item.id === profileId);
                if (profile?.project_id) setProjectId(profile.project_id);
                if (profile?.harness) setHarness(profile.harness);
                setView("view");
              }}
            />
          )}

          {view === "settings" && (
            <HarnessSettingsWorkspace
              profiles={profiles}
              selectedProfile={selected}
              runActive={workspaceRunActive}
              onSelectProfile={(profileId) => {
                setSelectedId(profileId);
                const profile = profiles.find((item) => item.id === profileId);
                if (profile?.project_id) setProjectId(profile.project_id);
                if (profile?.harness) setHarness(profile.harness);
              }}
            />
          )}

          {view === "empty" && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <p className="text-gray-500 text-sm">Select a profile or create a new one</p>
              </div>
            </div>
          )}

          {view === "create" && canManageProfiles && (
            <CreateProfileFlow
              projectId={projectId}
              harness={harness}
              onCancel={() => setView("home")}
              onSave={handleCreate}
              onCreated={async (profileId) => {
                await refresh();
                setSelectedId(profileId);
                setView("edit");
              }}
            />
          )}

          {view === "edit" && selected && canManageProfiles && (
            <ProfileForm
              profile={selected}
              onSave={handleUpdate}
              onDelete={handleDelete}
              onCancel={() => {
                setSelectedId(null);
                setView("home");
              }}
            />
          )}

          {selected && (
            <div
              data-testid="desktop-agent-workspace-host"
              className={view === "view" ? "h-full" : "hidden h-full"}
              aria-hidden={view === "view" ? undefined : true}
              inert={view === "view" ? undefined : true}
            >
              <AgentBrowserWorkspace
                profiles={profiles}
                selectedProfile={selected}
                canAutomate={canAutomateSelected}
                canInteract={canInteractSelected}
                canManageViewport={canManageProfiles && canOperateSelected}
                onViewportApply={handleViewportApply}
                onSelectProfile={(profileId) => {
                  setSelectedId(profileId);
                  const profile = profiles.find((item) => item.id === profileId);
                  if (profile?.project_id) setProjectId(profile.project_id);
                  if (profile?.harness) setHarness(profile.harness);
                }}
                onConnectionStatusChange={setMobileConnectionStatus}
                onOpenSettings={() => setView("settings")}
                onRunActivityChange={handleWorkspaceRunActivityChange}
                initialPromptDraft={
                  initialPromptDraft?.profileId === selected.id ? initialPromptDraft : null
                }
                onInitialPromptDraftApplied={(draftId) => {
                  setInitialPromptDraft((current) => (
                    current?.id === draftId ? null : current
                  ));
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function isAdministrator(identity: AccessIdentity | null) {
  return Boolean(identity && (identity.kind === "bootstrap" || identity.kind === "anonymous" || identity.role === "admin"));
}

function canAccess(identity: AccessIdentity | null, profile: Profile | null, permission: AccessPermission) {
  if (!identity || !profile) return false;
  if (isAdministrator(identity)) return true;
  return hasAccessPermission(identity.grants, profile.sandbox_id, permission);
}

function isTableView(view: View) {
  return view === "profiles" || view === "accounts" || view === "proxies" || view === "sessions";
}

function desktopViewState(view: View): UIStateId {
  const states: Record<View, UIStateId> = {
    home: UI_STATE.appDesktopHome,
    empty: UI_STATE.appDesktopEmpty,
    create: UI_STATE.appDesktopCreate,
    edit: UI_STATE.appDesktopEdit,
    view: UI_STATE.appDesktopAgentWorkspace,
    access: UI_STATE.appAccessDashboard,
    proxies: UI_STATE.appDesktopProxies,
    profiles: UI_STATE.appDesktopProfiles,
    accounts: UI_STATE.appDesktopAccounts,
    sessions: UI_STATE.appDesktopSessions,
    settings: UI_STATE.appDesktopSettings,
  };
  return states[view];
}

function useIsMobile() {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia(MOBILE_WORKSPACE_QUERY).matches;
  });

  useEffect(() => {
    const query = window.matchMedia(MOBILE_WORKSPACE_QUERY);
    const update = () => setIsMobile(query.matches);

    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isMobile;
}
