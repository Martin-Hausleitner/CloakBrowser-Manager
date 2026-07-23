/**
 * Background service worker — message hub for open-cloud / open-local.
 */

import { loadSettings } from "../lib/api.js";
import { openInCloud, openLocal } from "../lib/open-links.js";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handle(message)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((err) =>
      sendResponse({
        ok: false,
        error: err.message || String(err),
        code: err.code || null,
      }),
    );
  return true;
});

async function handle(message) {
  if (!message || !message.type) throw new Error("Missing message type");
  const settings = await loadSettings();

  if (message.type === "OPEN_CLOUD") {
    return openInCloud(settings, message.profile);
  }
  if (message.type === "OPEN_LOCAL") {
    return openLocal(settings, message.profile);
  }
  throw new Error(`Unknown message type: ${message.type}`);
}
