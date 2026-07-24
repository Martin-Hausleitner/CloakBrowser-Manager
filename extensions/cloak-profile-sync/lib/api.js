/**
 * Manager API client for the CloakBrowser Profile Sync extension.
 *
 * Prefers /api/extension/* when deployed; falls back to /api/profiles + /api/proxies.
 */

const DEFAULT_BASE = "http://127.0.0.1:18117";
const DEFAULT_BRIDGE = "http://127.0.0.1:18765";

export { DEFAULT_BASE, DEFAULT_BRIDGE };

export async function loadSettings() {
  const stored = await chrome.storage.local.get({
    managerBase: DEFAULT_BASE,
    bridgeBase: DEFAULT_BRIDGE,
    authMode: "token", // token | password
    token: "",
    username: "",
    persistToken: true,
  });
  const session = await chrome.storage.session.get({ password: "" });
  return { ...stored, password: session.password || "" };
}

export async function saveSettings(partial) {
  const next = { ...partial };
  if ("password" in next) {
    await chrome.storage.session.set({ password: next.password || "" });
    delete next.password;
  }
  if (Object.keys(next).length) {
    await chrome.storage.local.set(next);
  }
}

export async function clearSecrets() {
  await chrome.storage.local.remove(["token"]);
  await chrome.storage.session.remove(["password"]);
}

function authHeaders(settings) {
  const headers = { "Content-Type": "application/json", Accept: "application/json" };
  if (settings.token) {
    headers.Authorization = `Bearer ${settings.token}`;
  }
  return headers;
}

async function ensureOriginPermission(baseUrl) {
  let origin;
  try {
    origin = `${new URL(baseUrl).origin}/*`;
  } catch {
    throw new Error("Invalid Manager URL");
  }
  const alwaysAllowed =
    origin.startsWith("http://127.0.0.1/") || origin.startsWith("http://localhost/");
  if (alwaysAllowed) return;
  const granted = await chrome.permissions.contains({ origins: [origin] });
  if (!granted) {
    const ok = await chrome.permissions.request({ origins: [origin] });
    if (!ok) throw new Error(`Permission denied for ${origin}`);
  }
}

function detailMessage(body, fallback) {
  if (!body) return fallback;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return body.error || fallback;
}

export class ManagerApi {
  constructor(settings) {
    this.settings = settings;
    this.base = String(settings.managerBase || DEFAULT_BASE).replace(/\/$/, "");
  }

  async request(path, options = {}) {
    await ensureOriginPermission(this.base);
    const url = `${this.base}${path}`;
    const init = {
      ...options,
      headers: {
        ...authHeaders(this.settings),
        ...(options.headers || {}),
      },
      credentials: this.settings.authMode === "password" ? "include" : "omit",
    };

    if (
      this.settings.authMode === "password" &&
      this.settings.username &&
      this.settings.password &&
      !this._loggedIn
    ) {
      await this.loginWithPassword();
    }

    let res = await fetch(url, init);
    if (res.status === 401 && this.settings.authMode === "password") {
      await this.loginWithPassword();
      res = await fetch(url, init);
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      const err = new Error(detailMessage(body, res.statusText || `HTTP ${res.status}`));
      err.status = res.status;
      throw err;
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async loginWithPassword() {
    await ensureOriginPermission(this.base);
    const res = await fetch(`${this.base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "include",
      body: JSON.stringify({
        username: this.settings.username,
        password: this.settings.password,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      const err = new Error(detailMessage(body, "Login failed"));
      err.status = res.status;
      throw err;
    }
    this._loggedIn = true;
    return res.json();
  }

  authStatus() {
    return this.request("/api/auth/status");
  }

  /**
   * Preferred one-shot bootstrap. Returns null when Manager build lacks the route.
   */
  async getCatalog() {
    try {
      return await this.request("/api/extension/catalog");
    } catch (err) {
      if (err.status === 404 || err.status === 405) return null;
      throw err;
    }
  }

  listProfiles() {
    return this.request("/api/profiles");
  }

  getProfile(id) {
    return this.request(`/api/profiles/${encodeURIComponent(id)}`);
  }

  listProxies() {
    return this.request("/api/proxies");
  }

  getStatus() {
    return this.request("/api/status");
  }

  getProfileHealth(id) {
    return this.request(`/api/profiles/${encodeURIComponent(id)}/health`);
  }

  getProfileExtensions(id) {
    return this.request(`/api/profiles/${encodeURIComponent(id)}/extensions`);
  }

  /**
   * Future Manager catalog of selectable default (Comet) extensions.
   * Returns null until the API exists.
   */
  async getDefaultExtensions() {
    try {
      return await this.request("/api/extension/defaults");
    } catch (err) {
      if (err.status === 404 || err.status === 405) return null;
      throw err;
    }
  }

  launchProfile(id) {
    return this.request(`/api/profiles/${encodeURIComponent(id)}/launch`, {
      method: "POST",
    });
  }

  /**
   * One-click open: launch (optional) + steel-style local/cloud link set.
   * Returns null when route is not deployed yet.
   */
  async openSession({ profileId, launch = true, prefer = "cloud" }) {
    try {
      return await this.request("/api/extension/sessions/open", {
        method: "POST",
        body: JSON.stringify({
          profile_id: profileId,
          launch,
          prefer,
        }),
      });
    } catch (err) {
      if (err.status === 404 || err.status === 405) return null;
      throw err;
    }
  }

  /**
   * Load profiles + proxies for the popup.
   * Catalog is preferred (redacted). Falls back to classic list endpoints.
   */
  async loadWorkspace() {
    const catalog = await this.getCatalog();
    if (catalog) {
      let defaults = null;
      try {
        defaults = await this.getDefaultExtensions();
      } catch {
        defaults = null;
      }
      return {
        source: "catalog",
        profiles: (catalog.profiles || []).map(normalizeCatalogProfile),
        proxies: catalog.proxies || [],
        bases: catalog.bases || {},
        endpoints: catalog.endpoints || {},
        capabilities: catalog.capabilities || {},
        defaultExtensions: defaults?.extensions || defaults?.items || null,
      };
    }

    const [profiles, proxies, status] = await Promise.all([
      this.listProfiles(),
      this.listProxies().catch(() => []),
      this.getStatus().catch(() => null),
    ]);
    let defaults = null;
    try {
      defaults = await this.getDefaultExtensions();
    } catch {
      defaults = null;
    }
    return {
      source: "legacy",
      profiles: (profiles || []).map(normalizeLegacyProfile),
      proxies: proxies || [],
      bases: { local: this.base, cloud: null },
      endpoints: {},
      capabilities: {
        can_list_proxies: true,
        can_open_sessions: true,
        cloud_base_configured: false,
      },
      status,
      defaultExtensions: defaults?.extensions || defaults?.items || null,
    };
  }
}

function normalizeCatalogProfile(p) {
  return {
    id: p.id,
    name: p.name,
    project_id: p.project_id || "default",
    folder_path: p.folder_path || "",
    sandbox_id: p.sandbox_id || "default",
    harness: p.harness || "browser-use",
    pinned: Boolean(p.pinned),
    timezone: p.timezone || null,
    locale: p.locale || null,
    proxy_configured: Boolean(p.proxy_configured),
    // Never trust raw proxy from other payloads in UI — catalog is redacted.
    proxy: null,
    tags: Array.isArray(p.tags) ? p.tags : [],
    status: p.status || (p.running ? "running" : "stopped"),
    location: p.running || p.status === "running" ? "cloud" : "stopped",
  };
}

function normalizeLegacyProfile(p) {
  return {
    id: p.id,
    name: p.name,
    project_id: p.project_id || "default",
    folder_path: p.folder_path || "",
    sandbox_id: p.sandbox_id || "default",
    harness: p.harness || "browser-use",
    pinned: Boolean(p.pinned),
    timezone: p.timezone || null,
    locale: p.locale || null,
    proxy_configured: Boolean(p.proxy),
    // Keep raw only for internal matching; UI must use maskProxy().
    proxy: p.proxy || null,
    tags: Array.isArray(p.tags) ? p.tags : [],
    status: p.status || "stopped",
    location: p.status === "running" ? "cloud" : "stopped",
    humanize: p.humanize,
    fingerprint_seed: p.fingerprint_seed,
  };
}

export async function bridgeHealth(bridgeBase = DEFAULT_BRIDGE) {
  const base = String(bridgeBase || DEFAULT_BRIDGE).replace(/\/$/, "");
  try {
    const res = await fetch(`${base}/health`, { method: "GET" });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return await res.json();
  } catch (err) {
    return { ok: false, error: err.message || "bridge unreachable" };
  }
}

export async function bridgeLaunch(bridgeBase, payload) {
  const base = String(bridgeBase || DEFAULT_BRIDGE).replace(/\/$/, "");
  const res = await fetch(`${base}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  if (!res.ok) {
    const err = new Error(detailMessage(body, `Bridge HTTP ${res.status}`));
    err.status = res.status;
    throw err;
  }
  return body;
}
