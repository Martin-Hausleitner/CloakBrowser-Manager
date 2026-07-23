/**
 * Redact proxy credentials and IP addresses for UI display.
 * Never render raw passwords from Manager profile.proxy fields.
 */

export function maskIpv4Host(host) {
  const parts = String(host || "").split(".");
  if (parts.length !== 4 || parts.some((p) => !/^\d{1,3}$/.test(p))) {
    return host || "unknown";
  }
  return `${parts[0]}.${parts[1]}.x.x`;
}

export function maskUsername(username) {
  const value = String(username || "");
  if (!value) return null;
  if (value.length <= 2) return `${value[0] || "*"}***`;
  return `${value[0]}***${value[value.length - 1]}`;
}

/**
 * Mask a proxy URL or host:port:user:pass string for display.
 * Returns null when unset.
 */
export function maskProxy(raw) {
  if (!raw) return null;
  const text = String(raw).trim();
  if (!text) return null;

  try {
    if (text.includes("://")) {
      const url = new URL(text);
      const host = maskIpv4Host(url.hostname);
      const user = maskUsername(url.username);
      const auth = user ? `${user}@` : "";
      const port = url.port ? `:${url.port}` : "";
      return `${url.protocol}//${auth}${host}${port}`;
    }
  } catch {
    // fall through
  }

  const parts = text.split(":");
  if (parts.length === 4) {
    const [host, port, user] = parts;
    return `http://${maskUsername(user)}@${maskIpv4Host(host)}:${port}`;
  }
  if (parts.length === 2) {
    return `http://${maskIpv4Host(parts[0])}:${parts[1]}`;
  }
  return "configured";
}

export function formatProxyLine(profile, proxies = []) {
  if (!profile?.proxy_configured && !profile?.proxy) {
    return { text: "no proxy", tone: "muted" };
  }

  const maskedFromProfile = maskProxy(profile.proxy);
  const inventoryHit = findInventoryMatch(profile, proxies);
  if (inventoryHit) {
    const label = `${inventoryHit.host_masked}${inventoryHit.port ? `:${inventoryHit.port}` : ""}`;
    const auth = inventoryHit.username_masked ? ` · ${inventoryHit.username_masked}` : "";
    const score =
      inventoryHit.authenticity_score != null
        ? ` · auth ${inventoryHit.authenticity_score}`
        : "";
    const state = inventoryHit.check_state && inventoryHit.check_state !== "missing"
      ? ` · ${inventoryHit.check_state}`
      : "";
    return {
      text: `${label}${auth}${score}${state}`,
      tone: toneForCheck(inventoryHit.check_state),
    };
  }

  return {
    text: maskedFromProfile || "proxy on",
    tone: "neutral",
  };
}

function findInventoryMatch(profile, proxies) {
  if (!proxies?.length) return null;
  if (!profile.proxy) {
    // Catalog-only: cannot match host; return first active warning/passed with same project later.
    return null;
  }
  try {
    const raw = profile.proxy.includes("://") ? profile.proxy : `http://${profile.proxy}`;
    const url = new URL(raw);
    const prefix = url.hostname.split(".").slice(0, 2).join(".");
    return (
      proxies.find(
        (item) =>
          item.port === Number(url.port || 0) &&
          String(item.host_masked || "").startsWith(prefix),
      ) || null
    );
  } catch {
    return null;
  }
}

function toneForCheck(state) {
  if (state === "passed") return "ok";
  if (state === "warning") return "warn";
  if (state === "failed") return "bad";
  return "neutral";
}

/** Compact health score line from ProfileHealthResponse. */
export function formatHealthLine(health) {
  if (!health) return null;
  const parts = [];
  if (health.proxy_authenticity_score != null) {
    parts.push(`auth ${health.proxy_authenticity_score}`);
  }
  if (health.browser_scan_score != null) {
    parts.push(`scan ${health.browser_scan_score}`);
  }
  if (health.fingerprint_consistency_score != null) {
    parts.push(`fp ${health.fingerprint_consistency_score}`);
  }
  if (!parts.length) {
    return health.state && health.state !== "unavailable"
      ? { text: health.state, tone: "neutral" }
      : null;
  }
  const worst = Math.min(
    ...[
      health.proxy_authenticity_score,
      health.browser_scan_score,
      health.fingerprint_consistency_score,
    ].filter((n) => typeof n === "number"),
  );
  const tone = worst >= 95 ? "ok" : worst >= 80 ? "neutral" : worst >= 50 ? "warn" : "bad";
  return { text: parts.join(" · "), tone };
}

/** Chrome Web Store / edge link when id looks like a 32-char extension id. */
export function extensionStoreUrl(extId) {
  const id = String(extId || "");
  if (!/^[a-p]{32}$/.test(id)) return null;
  return `https://chromewebstore.google.com/detail/${id}`;
}
