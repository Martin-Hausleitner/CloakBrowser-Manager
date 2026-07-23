/**
 * Open Cloud = Manager/VCVM session (proxy-on-start via Manager launch).
 * Open Local = Mac CloakBrowser via localhost bridge (proxy-on-start enforced).
 *
 * Aligns with Manager /api/extension/sessions/open when deployed
 * (steel-browser sessionViewerUrl / debugUrl style link sets).
 */

import {
  ManagerApi,
  bridgeHealth,
  bridgeLaunch,
  DEFAULT_BRIDGE,
} from "./api.js";

/**
 * Open the profile in Manager cloud/VCVM (VNC workspace).
 * Always requests launch so proxy-on-start is applied when a proxy is configured.
 */
export async function openInCloud(settings, profile) {
  const api = new ManagerApi(settings);

  const session = await api.openSession({
    profileId: profile.id,
    launch: true,
    prefer: "cloud",
  });

  if (session) {
    const url =
      session.open_url ||
      session.links?.cloud?.session_viewer_url ||
      session.links?.local?.session_viewer_url;
    if (!url) throw new Error("Manager open-session returned no viewer URL");
    await chrome.tabs.create({ url });
    return {
      mode: "cloud",
      via: "extension-sessions-open",
      url,
      status: session.status,
      launched: session.launched,
      proxy_on_start: Boolean(profile.proxy_configured || profile.proxy),
    };
  }

  // Fallback: classic launch + deep-link (?profile=)
  let status = profile.status;
  if (status !== "running") {
    try {
      await api.launchProfile(profile.id);
      status = "running";
    } catch (err) {
      if (err.status !== 409) throw err;
      status = "running";
    }
  }

  const base = String(settings.managerBase || "").replace(/\/$/, "");
  const url = `${base}/?profile=${encodeURIComponent(profile.id)}`;
  await chrome.tabs.create({ url });
  return {
    mode: "cloud",
    via: "launch+deeplink",
    url,
    status,
    proxy_on_start: Boolean(profile.proxy_configured || profile.proxy),
  };
}

/**
 * Launch the same profile settings in local CloakBrowser on this Mac.
 * Proxy credentials stay on the bridge (fetched from Manager); UI never shows them.
 */
export async function openLocal(settings, profile) {
  const bridgeBase = settings.bridgeBase || DEFAULT_BRIDGE;
  const health = await bridgeHealth(bridgeBase);
  if (!health.ok) {
    const err = new Error(
      "Local bridge offline. Run: python3 extensions/cloak-profile-sync/host/local_bridge.py",
    );
    err.code = "BRIDGE_OFFLINE";
    err.health = health;
    throw err;
  }

  if (!settings.token && settings.authMode !== "password") {
    throw new Error("Auth token required for Open Local (bridge fetches the profile securely).");
  }

  const result = await bridgeLaunch(bridgeBase, {
    profile_id: profile.id,
    manager_base: settings.managerBase,
    token: settings.token || undefined,
    username: settings.authMode === "password" ? settings.username : undefined,
    password: settings.authMode === "password" ? settings.password : undefined,
    // Bridge MUST apply proxy when profile has one (proxy-on-start).
    require_proxy_if_configured: true,
  });

  return {
    mode: "local",
    via: "local-bridge",
    result,
    proxy_on_start: Boolean(result.proxy_applied),
  };
}
