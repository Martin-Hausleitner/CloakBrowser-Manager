import { useState, useCallback, useEffect } from "react";
import { ArrowLeft, Lock, PanelLeftClose, PanelLeft, ShieldCheck, Globe2, LayoutGrid, Plus } from "lucide-react";
import { useProfiles } from "./hooks/useProfiles";
import {
  api,
  setOnUnauthorized,
  type AccessIdentity,
  type AccessPermission,
  type Profile,
  type ProfileCreateData,
  type ProfileHarness,
} from "./lib/api";
import { hasAccessPermission } from "./lib/accessPermissions";
import { ProfileList } from "./components/ProfileList";
import { ProfileForm } from "./components/ProfileForm";
import { ProfileViewer } from "./components/ProfileViewer";
import { LaunchButton } from "./components/LaunchButton";
import { StatusIndicator } from "./components/StatusIndicator";
import { LoginPage } from "./components/LoginPage";
import { MobileSplitScreen } from "./components/mobile/MobileSplitScreen";
import { AccessDashboard } from "./components/AccessDashboard";
import { ProfileHealthSummary } from "./components/ProfileHealthSummary";
import { BrowserUseHome } from "./components/BrowserUseHome";
import { ProxyOverview } from "./components/ProxyOverview";

type AuthState = "checking" | "required" | "ok" | "error";
type View = "home" | "empty" | "create" | "edit" | "view" | "access" | "proxies";
const MOBILE_WORKSPACE_QUERY = "(max-width: 767px), (pointer: coarse) and (max-width: 1024px)";
type MobileConnectionStatus = "connecting" | "connected" | "reconnecting" | "failed";
const FIXED_PROJECTS = ["default", "proxied", "mobile", "research"] as const;

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
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (authState === "error") {
    return (
      <div className="h-screen flex items-center justify-center bg-surface-0">
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
  const [view, setView] = useState<View>("home");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileFullscreenOpen, setMobileFullscreenOpen] = useState(false);
  const [mobileBrowserZoom, setMobileBrowserZoom] = useState(100);
  const [mobileRemoteToolsOpen, setMobileRemoteToolsOpen] = useState(false);
  const [mobileConnectionStatus, setMobileConnectionStatus] = useState<MobileConnectionStatus>("connecting");
  const [projectId, setProjectId] = useState<string>("default");
  const [harness, setHarness] = useState<ProfileHarness>("browser-use");
  const [taskDraft, setTaskDraft] = useState("");
  const isMobile = useIsMobile();

  const selected = profiles.find((p) => p.id === selectedId) ?? null;
  const canManageProfiles = isAdministrator(identity);
  const canOperateSelected = Boolean(selected && canAccess(identity, selected, "operate"));
  const canInteractSelected = Boolean(selected && canAccess(identity, selected, "interact"));
  const projects = Array.from(
    new Set<string>([
      ...FIXED_PROJECTS,
      ...profiles.map((profile) => profile.project_id || "default"),
    ]),
  );

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

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    const profile = profiles.find((p) => p.id === id);
    if (profile?.project_id) setProjectId(profile.project_id);
    if (profile?.harness) setHarness(profile.harness);
    setView(isMobile ? "view" : profile?.status === "running" ? "view" : canManageProfiles ? "edit" : "home");
  }, [canManageProfiles, isMobile, profiles]);

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


  const handleBulkOrganize = async (payload: {
    profile_ids: string[];
    project_id?: string;
    folder_path?: string;
    pinned?: boolean;
  }) => {
    await api.bulkOrganizeProfiles(payload);
    await refresh();
  };

const handleTogglePin = useCallback(async (id: string) => {
    const profile = profiles.find((candidate) => candidate.id === id) ?? null;
    await toggleProfilePin({
      profile,
      canManageProfiles,
      update,
    });
  }, [canManageProfiles, profiles, update]);

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
        <div className="flex h-dvh flex-col overflow-hidden bg-surface-0">
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
          browserZoom={mobileBrowserZoom}
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
        />
      </>
    );
  }

  return (
    <div className="h-screen flex">
      {/* Compact Browser-Use style sidebar */}
      {sidebarOpen && (
        <div className="w-56 border-r border-border bg-surface-1 flex-shrink-0 flex flex-col">
          <div className="border-b border-border px-3 py-3">
            <div className="text-sm font-semibold tracking-tight">Browser Use</div>
            <div className="mt-2 space-y-1">
              <button
                type="button"
                onClick={() => setView("home")}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs ${
                  view === "home" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                }`}
              >
                <LayoutGrid className="h-3.5 w-3.5" />
                Agent home
              </button>
              {canManageProfiles ? (
                <button
                  type="button"
                  onClick={() => setView("proxies")}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs ${
                    view === "proxies" ? "bg-surface-3 text-gray-100" : "text-gray-400 hover:bg-surface-2"
                  }`}
                >
                  <Globe2 className="h-3.5 w-3.5" />
                  Proxies
                </button>
              ) : null}
              {canManageProfiles ? (
                <button
                  type="button"
                  onClick={handleNew}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs text-gray-400 hover:bg-surface-2"
                >
                  <Plus className="h-3.5 w-3.5" />
                  New profile
                </button>
              ) : null}
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
              onBulkOrganize={canManageProfiles ? handleBulkOrganize : undefined}
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
            {selected && view !== "home" && view !== "proxies" && (
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
            {selected && view !== "home" && view !== "proxies" && (
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

        {/* Error banner */}
        {error && (
          <div className="px-4 py-2 bg-red-600/15 border-b border-red-600/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto overscroll-contain">
          {view === "home" && (
            <BrowserUseHome
              projects={projects}
              projectId={projectId}
              harness={harness}
              profiles={profiles}
              task={taskDraft}
              canManage={canManageProfiles}
              selectedProfile={selected}
              onProjectChange={setProjectId}
              onHarnessChange={setHarness}
              onTaskChange={setTaskDraft}
              onOpenProxies={() => setView("proxies")}
              onCreateProjectProfile={handleNew}
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

          {view === "empty" && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <p className="text-gray-500 text-sm">Select a profile or create a new one</p>
              </div>
            </div>
          )}

          {view === "create" && canManageProfiles && (
            <ProfileForm
              profile={null}
              onSave={handleCreate}
              onCancel={() => setView("home")}
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

          {view === "view" && selected && selected.status === "running" && (
            <ProfileViewer
              key={selected.id}
              profileId={selected.id}
              cdpUrl={selected.cdp_url}
              clipboardSync={selected.clipboard_sync}
              canInteract={canInteractSelected}
              onDisconnect={handleVncDisconnect}
            />
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
