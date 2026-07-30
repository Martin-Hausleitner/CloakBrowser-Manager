import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { Stagehand } from "@browserbasehq/stagehand";

const require = createRequire(import.meta.url);
const MAX_REQUEST_BYTES = 16 * 1024;
const MAX_TEXT_CHARS = 8000;
const SUPPORTED_STAGEHAND_VERSION = "3.7.1";
const REQUEST_FILE_ENV = "CBM_STAGEHAND_REQUEST_FILE";

function writeJson(payload, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
  process.exitCode = exitCode;
}

function normalizedOrigin(value) {
  const url = new URL(String(value || ""));
  if (url.protocol !== "http:" && url.protocol !== "https:") return "";
  const defaultPort = url.protocol === "http:" ? "80" : "443";
  const port = url.port && url.port !== defaultPort ? `:${url.port}` : "";
  return `${url.protocol}//${url.hostname.toLowerCase()}${port}`;
}

function assertLocalCdpUrl(value) {
  const url = new URL(String(value || ""));
  if (url.protocol !== "ws:" || url.hostname !== "127.0.0.1") throw new Error("invalid_cdp_url");
  const port = Number(url.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("invalid_cdp_url");
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 1 || parts.some((part) => part.length > 256)) throw new Error("invalid_cdp_url");
  if (url.search || url.hash || url.username || url.password) throw new Error("invalid_cdp_url");
  return url.href;
}

function assertAllowedNavigationUrl(value, allowedOrigins) {
  const url = new URL(String(value || ""));
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("invalid_url");
  if (allowedOrigins.length > 0 && !allowedOrigins.includes(normalizedOrigin(url.href))) {
    throw new Error("origin_denied");
  }
  return url.href;
}

async function assertCurrentOriginAllowed(page, allowedOrigins) {
  if (allowedOrigins.length === 0) return;
  const current = page.url();
  if (!current || !allowedOrigins.includes(normalizedOrigin(current))) {
    throw new Error("origin_denied");
  }
}

function finalPayload(page, action, extra = {}) {
  const url = page.url();
  if (!url) throw new Error("missing_final_url");
  return {
    ok: true,
    connection_mode: "existing-cdp",
    used_model: false,
    action,
    url,
    ...extra,
  };
}

async function readRequest() {
  const requestFile = process.env[REQUEST_FILE_ENV];
  if (typeof requestFile !== "string" || requestFile.length === 0) throw new Error("missing_request_file");
  const stat = await import("node:fs/promises").then((fs) => fs.stat(requestFile));
  if (!stat.isFile() || (stat.mode & 0o077) !== 0 || stat.size > MAX_REQUEST_BYTES) {
    throw new Error("invalid_request_file");
  }
  const parsed = JSON.parse(await readFile(requestFile, "utf8"));
  const action = String(parsed?.action || "");
  if (!["inspect", "navigate", "click", "fill", "read_text"].includes(action)) {
    throw new Error("unsupported_action");
  }
  const allowedOrigins = Array.isArray(parsed?.allowed_origins)
    ? parsed.allowed_origins.map((item) => normalizedOrigin(item)).filter(Boolean)
    : [];
  return {
    action,
    arguments: parsed?.arguments && typeof parsed.arguments === "object" ? parsed.arguments : {},
    allowedOrigins,
    cdpUrl: assertLocalCdpUrl(parsed?.cdpUrl),
    connectTimeoutMs: Math.max(1, Math.min(60000, Number(parsed?.connectTimeoutMs || 15000))),
  };
}

async function preflight() {
  const pkg = require("@browserbasehq/stagehand/package.json");
  const nodeCompatible = nodeVersionIsCompatible(process.versions.node);
  writeJson({
    ok: true,
    mode: "preflight",
    node: process.versions.node,
    stagehandVersion: pkg.version,
    supportsCdpUrl: pkg.version === SUPPORTED_STAGEHAND_VERSION && nodeCompatible,
  });
}

function nodeVersionIsCompatible(version) {
  const parts = String(version || "").split(".").map((part) => Number(part));
  if (parts.length < 3 || parts.slice(0, 3).some((part) => !Number.isInteger(part))) return false;
  const [major, minor, patch] = parts;
  if (major === 20) return minor > 19 || (minor === 19 && patch >= 0);
  if (major === 21) return false;
  if (major === 22) return minor > 12 || (minor === 12 && patch >= 0);
  return major >= 23;
}

async function main() {
  if (process.argv.includes("--preflight")) {
    await preflight();
    return;
  }

  let stagehand;
  try {
    const req = await readRequest();
    stagehand = new Stagehand({
      env: "LOCAL",
      keepAlive: true,
      disablePino: true,
      verbose: 0,
      localBrowserLaunchOptions: {
        cdpUrl: req.cdpUrl,
        connectTimeoutMs: req.connectTimeoutMs,
      },
    });
    await stagehand.init();
    const pages = stagehand.context.pages();
    const page = pages.at(-1);
    if (!page) throw new Error("missing_existing_page");

    switch (req.action) {
      case "navigate": {
        const url = assertAllowedNavigationUrl(req.arguments.url, req.allowedOrigins);
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        writeJson(finalPayload(page, req.action));
        break;
      }
      case "inspect": {
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        writeJson(finalPayload(page, req.action, { title: await page.title() }));
        break;
      }
      case "click": {
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        const selector = String(req.arguments.selector || "");
        const clicked = await page.evaluate((sel) => {
          const element = document.querySelector(sel);
          if (!element) return false;
          element.click();
          return true;
        }, selector);
        if (!clicked) throw new Error("selector_not_found");
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        writeJson(finalPayload(page, req.action, { selector }));
        break;
      }
      case "fill": {
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        const selector = String(req.arguments.selector || "");
        const text = String(req.arguments.text || "");
        const filled = await page.evaluate(
          ({ selector: sel, text: value }) => {
            const element = document.querySelector(sel);
            if (!element) return false;
            element.focus();
            element.value = value;
            element.dispatchEvent(new Event("input", { bubbles: true }));
            element.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          },
          { selector, text },
        );
        if (!filled) throw new Error("selector_not_found");
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        writeJson(finalPayload(page, req.action, { selector, text_length: text.length }));
        break;
      }
      case "read_text": {
        await assertCurrentOriginAllowed(page, req.allowedOrigins);
        const selector = req.arguments.selector ? String(req.arguments.selector) : "body";
        const text = await page.evaluate((sel) => {
          const element = document.querySelector(sel);
          return element ? element.innerText : null;
        }, selector);
        if (text === null) throw new Error("selector_not_found");
        writeJson(finalPayload(page, req.action, { selector: req.arguments.selector || null, text: String(text).slice(0, MAX_TEXT_CHARS) }));
        break;
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "stagehand_failed";
    const classification = message === "origin_denied" ? "origin_denied" : "policy_denied";
    writeJson({ ok: false, classification, error: message }, 1);
  } finally {
    if (stagehand) {
      try {
        await stagehand.close();
      } catch {
        // keepAlive preserves the Manager-owned browser; close only releases Stagehand.
      }
    }
  }
}

await main();
