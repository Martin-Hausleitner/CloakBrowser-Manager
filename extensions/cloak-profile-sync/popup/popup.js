import {
  ManagerApi,
  loadSettings,
  saveSettings,
  clearSecrets,
  bridgeHealth,
  DEFAULT_BASE,
  DEFAULT_BRIDGE,
} from "../lib/api.js";
import { formatProxyLine, formatHealthLine, extensionStoreUrl } from "../lib/mask.js";

const els = {
  statusLine: document.getElementById("statusLine"),
  authPanel: document.getElementById("authPanel"),
  managerBase: document.getElementById("managerBase"),
  bridgeBase: document.getElementById("bridgeBase"),
  token: document.getElementById("token"),
  username: document.getElementById("username"),
  password: document.getElementById("password"),
  tokenFields: document.getElementById("tokenFields"),
  passwordFields: document.getElementById("passwordFields"),
  saveAuthBtn: document.getElementById("saveAuthBtn"),
  clearAuthBtn: document.getElementById("clearAuthBtn"),
  settingsBtn: document.getElementById("settingsBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  errorBox: document.getElementById("errorBox"),
  profilesView: document.getElementById("profilesView"),
  proxiesView: document.getElementById("proxiesView"),
  bridgeLine: document.getElementById("bridgeLine"),
  sourceLine: document.getElementById("sourceLine"),
  tabs: document.getElementById("tabs"),
};

let settings = null;
let workspace = null;
const detailCache = new Map(); // profileId -> { extensions, health }

init();

async function init() {
  settings = await loadSettings();
  fillSettingsForm(settings);
  wireUi();
  await refreshBridge();
  if (settings.token || (settings.username && settings.password)) {
    await loadWorkspace();
  } else {
    showAuth(true);
    showError("Connect to Manager with a token or username/password.");
  }
}

function wireUi() {
  els.settingsBtn.addEventListener("click", () => {
    showAuth(els.authPanel.classList.contains("hidden"));
  });
  els.refreshBtn.addEventListener("click", () => loadWorkspace());
  els.saveAuthBtn.addEventListener("click", saveAuth);
  els.clearAuthBtn.addEventListener("click", async () => {
    await clearSecrets();
    settings = await loadSettings();
    fillSettingsForm(settings);
    showError("Secrets cleared.");
  });

  document.querySelectorAll('input[name="authMode"]').forEach((input) => {
    input.addEventListener("change", syncAuthModeUi);
  });

  els.tabs.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab");
    if (!btn) return;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === btn));
    const tab = btn.dataset.tab;
    els.profilesView.classList.toggle("hidden", tab !== "profiles");
    els.proxiesView.classList.toggle("hidden", tab !== "proxies");
  });
}

function fillSettingsForm(s) {
  els.managerBase.value = s.managerBase || DEFAULT_BASE;
  els.bridgeBase.value = s.bridgeBase || DEFAULT_BRIDGE;
  els.token.value = s.token || "";
  els.username.value = s.username || "";
  els.password.value = s.password || "";
  const mode = s.authMode || "token";
  document.querySelectorAll('input[name="authMode"]').forEach((input) => {
    input.checked = input.value === mode;
  });
  syncAuthModeUi();
}

function syncAuthModeUi() {
  const mode = document.querySelector('input[name="authMode"]:checked')?.value || "token";
  els.tokenFields.classList.toggle("hidden", mode !== "token");
  els.passwordFields.classList.toggle("hidden", mode !== "password");
}

function showAuth(open) {
  els.authPanel.classList.toggle("hidden", !open);
}

function showError(msg) {
  if (!msg) {
    els.errorBox.classList.add("hidden");
    els.errorBox.textContent = "";
    return;
  }
  els.errorBox.textContent = msg;
  els.errorBox.classList.remove("hidden");
}

async function saveAuth() {
  const mode = document.querySelector('input[name="authMode"]:checked')?.value || "token";
  await saveSettings({
    managerBase: els.managerBase.value.trim() || DEFAULT_BASE,
    bridgeBase: els.bridgeBase.value.trim() || DEFAULT_BRIDGE,
    authMode: mode,
    token: els.token.value.trim(),
    username: els.username.value.trim(),
    password: els.password.value,
  });
  settings = await loadSettings();
  showAuth(false);
  await refreshBridge();
  await loadWorkspace();
}

async function refreshBridge() {
  const health = await bridgeHealth(settings.bridgeBase || DEFAULT_BRIDGE);
  els.bridgeLine.textContent = health.ok
    ? `Bridge: ok${health.cloakbrowser ? ` · ${health.cloakbrowser}` : ""}`
    : "Bridge: offline";
}

async function loadWorkspace() {
  showError("");
  els.statusLine.textContent = "Loading…";
  try {
    const api = new ManagerApi(settings);
    workspace = await api.loadWorkspace();
    els.sourceLine.textContent = workspace.source === "catalog" ? "API: catalog" : "API: legacy";
    const running = workspace.profiles.filter((p) => p.status === "running").length;
    els.statusLine.textContent = `${workspace.profiles.length} profiles · ${running} running`;
    renderProfiles();
    renderProxies();
    await refreshBridge();
  } catch (err) {
    showError(err.message || String(err));
    els.statusLine.textContent = "Disconnected";
    showAuth(true);
  }
}

function renderProfiles() {
  const root = els.profilesView;
  root.innerHTML = "";

  if (workspace?.defaultExtensions?.length) {
    const box = document.createElement("div");
    box.className = "defaults";
    box.textContent = `Default extensions available: ${workspace.defaultExtensions
      .map((e) => e.name || e.id)
      .slice(0, 6)
      .join(", ")}${workspace.defaultExtensions.length > 6 ? "…" : ""}`;
    root.appendChild(box);
  }

  const profiles = workspace?.profiles || [];
  if (!profiles.length) {
    root.innerHTML += `<div class="empty">No profiles visible for this identity.</div>`;
    return;
  }

  for (const profile of profiles) {
    root.appendChild(renderProfileCard(profile));
  }
}

function renderProfileCard(profile) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.id = profile.id;

  const proxy = formatProxyLine(profile, workspace.proxies || []);
  const tags = (profile.tags || [])
    .map((t) => `<span class="badge">${escapeHtml(typeof t === "string" ? t : t.tag)}</span>`)
    .join("");

  card.innerHTML = `
    <div class="card-head">
      <div>
        <div class="name">${escapeHtml(profile.name)}</div>
        <div class="meta">
          <span>${escapeHtml(profile.project_id || "default")}</span>
          ${profile.folder_path ? ` · ${escapeHtml(profile.folder_path)}` : ""}
          · <span class="${proxy.tone}">${escapeHtml(proxy.text)}</span>
        </div>
      </div>
    </div>
    <div class="badges">
      <span class="badge ${profile.status === "running" ? "running" : "stopped"}">${escapeHtml(profile.status)}</span>
      <span class="badge">${profile.status === "running" ? "cloud/VCVM" : "mac-ready"}</span>
      ${profile.proxy_configured || profile.proxy ? `<span class="badge proxy">proxy-on-start</span>` : ""}
      ${tags}
    </div>
    <div class="meta health-line" data-role="health"></div>
    <div class="ext-row" data-role="extensions"></div>
    <div class="actions">
      <button type="button" class="btn tiny primary" data-action="cloud">Open Cloud</button>
      <button type="button" class="btn tiny" data-action="local">Open Local</button>
      <button type="button" class="btn tiny" data-action="details">Details</button>
    </div>
  `;

  card.querySelector('[data-action="cloud"]').addEventListener("click", () => openCloud(profile, card));
  card.querySelector('[data-action="local"]').addEventListener("click", () => openLocal(profile, card));
  card.querySelector('[data-action="details"]').addEventListener("click", () => loadDetails(profile, card));

  return card;
}

async function openCloud(profile, card) {
  setBusy(card, true);
  showError("");
  try {
    const res = await chrome.runtime.sendMessage({ type: "OPEN_CLOUD", profile });
    if (!res?.ok) throw new Error(res?.error || "Open Cloud failed");
    flash(card, `Cloud · ${res.result?.via || "ok"}`);
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(card, false);
  }
}

async function openLocal(profile, card) {
  setBusy(card, true);
  showError("");
  try {
    const res = await chrome.runtime.sendMessage({ type: "OPEN_LOCAL", profile });
    if (!res?.ok) throw new Error(res?.error || "Open Local failed");
    const proxyNote = res.result?.proxy_on_start ? " · proxy applied" : "";
    flash(card, `Local launched${proxyNote}`);
    await refreshBridge();
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setBusy(card, false);
  }
}

async function loadDetails(profile, card) {
  const extRoot = card.querySelector('[data-role="extensions"]');
  const healthRoot = card.querySelector('[data-role="health"]');
  extRoot.textContent = "Loading extensions…";
  healthRoot.textContent = "";

  try {
    const api = new ManagerApi(settings);
    const cached = detailCache.get(profile.id);
    const [extensionsResp, health] = await Promise.all([
      cached?.extensions
        ? Promise.resolve(cached.extensions)
        : api.getProfileExtensions(profile.id),
      cached?.health ? Promise.resolve(cached.health) : api.getProfileHealth(profile.id).catch(() => null),
    ]);
    detailCache.set(profile.id, { extensions: extensionsResp, health });

    const healthLine = formatHealthLine(health);
    healthRoot.innerHTML = healthLine
      ? `Health: <span class="${healthLine.tone}">${escapeHtml(healthLine.text)}</span>`
      : "Health: unavailable";

    const items = extensionsResp?.extensions || [];
    if (!items.length) {
      extRoot.innerHTML = `<span class="meta muted">No profile extensions installed</span>`;
      return;
    }
    extRoot.innerHTML = "";
    for (const ext of items) {
      const store = extensionStoreUrl(ext.id);
      const chip = document.createElement(store ? "a" : "span");
      chip.className = "ext-chip";
      if (store) {
        chip.href = store;
        chip.target = "_blank";
        chip.rel = "noreferrer";
        chip.title = ext.description || ext.name;
      }
      const dot = document.createElement("span");
      dot.className = `ext-dot${ext.trust_state === "valid" ? "" : ext.trust_state === "untrusted_manifest" ? " warn" : " bad"}`;
      chip.appendChild(dot);
      const label = document.createElement("span");
      label.textContent = `${ext.name || ext.id} · ${ext.version || "?"}`;
      chip.appendChild(label);
      // Future: ext.icon_url from Manager default-extension catalog
      if (ext.icon_url) {
        const img = document.createElement("img");
        img.src = ext.icon_url;
        img.alt = "";
        img.width = 12;
        img.height = 12;
        chip.prepend(img);
      }
      extRoot.appendChild(chip);
    }
  } catch (err) {
    extRoot.innerHTML = `<span class="meta bad">${escapeHtml(err.message || String(err))}</span>`;
  }
}

function renderProxies() {
  const root = els.proxiesView;
  root.innerHTML = "";
  const proxies = workspace?.proxies || [];
  if (!proxies.length) {
    root.innerHTML = `<div class="empty">No proxy inventory (admin-only or empty).</div>`;
    return;
  }
  for (const proxy of proxies) {
    const card = document.createElement("article");
    card.className = "card";
    const auth =
      proxy.authenticity_score != null ? ` · authenticity ${proxy.authenticity_score}` : "";
    const risk = proxy.risk_score != null ? ` · risk ${proxy.risk_score}` : "";
    const user = proxy.username_masked ? ` · ${proxy.username_masked}` : "";
    card.innerHTML = `
      <div class="name">${escapeHtml(proxy.label || `${proxy.host_masked}:${proxy.port || "?"}`)}</div>
      <div class="meta">
        ${escapeHtml(proxy.host_masked)}${proxy.port ? `:${proxy.port}` : ""}${escapeHtml(user)}
        · <span class="${toneClass(proxy.check_state)}">${escapeHtml(proxy.check_state || "missing")}</span>
        ${escapeHtml(auth)}${escapeHtml(risk)}
        ${proxy.country_code ? ` · ${escapeHtml(proxy.country_code)}` : ""}
      </div>
    `;
    root.appendChild(card);
  }
}

function toneClass(state) {
  if (state === "passed") return "ok";
  if (state === "warning") return "warn";
  if (state === "failed") return "bad";
  return "muted";
}

function setBusy(card, busy) {
  card.querySelectorAll("button").forEach((btn) => {
    btn.disabled = busy;
  });
}

function flash(card, text) {
  const meta = card.querySelector(".health-line") || card.querySelector(".meta");
  if (meta) meta.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
