/**
 * Background service worker — message hub for open-cloud / open-local.
 */

import { loadSettings } from "../lib/api.js";
import {
  appendRecorderEvent,
  createRecorderState,
  exportRecordingFlow,
  isRecorderExpired,
  MAX_RECORDER_EVENTS,
  sanitizeUrl,
} from "../lib/action-recorder.js";
import { openInCloud, openLocal } from "../lib/open-links.js";
import { runLocalControlLoop } from "../lib/local-control.js";

const RECORDER_KEY = "secureActionRecorder";

runLocalControlLoop((message) => handleRecorderMessage(message, {})).catch(() => {});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url && changeInfo.status !== "complete") return;
  recordTabNavigation(tabId, tab).catch(() => {});
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handle(message, sender)
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

async function handle(message, sender) {
  if (!message || typeof message.type !== "string" || !message.type) {
    throw new Error("Missing message type");
  }
  if (message.type.startsWith("RECORDER_")) {
    return handleRecorderMessage(message, sender);
  }
  const settings = await loadSettings();

  if (message.type === "OPEN_CLOUD") {
    return openInCloud(settings, message.profile);
  }
  if (message.type === "OPEN_LOCAL") {
    return openLocal(settings, message.profile);
  }
  throw new Error(`Unknown message type: ${message.type}`);
}

async function handleRecorderMessage(message, sender) {
  if (message.type === "RECORDER_GET_STATUS") {
    return recorderStatus(await loadRecorder());
  }
  if (message.type === "RECORDER_START") {
    return startRecorder();
  }
  if (message.type === "RECORDER_STOP") {
    return stopRecorder();
  }
  if (message.type === "RECORDER_CLEAR") {
    await saveRecorder(null);
    return recorderStatus(null);
  }
  if (message.type === "RECORDER_EXPORT") {
    const state = await loadRecorder();
    if (!state) return null;
    return exportRecordingFlow(state);
  }
  if (message.type === "RECORDER_EVENT") {
    const state = await loadRecorder();
    if (!state?.active) return recorderStatus(state);
    if (!sender?.tab?.id || sender.tab.id !== state.tabId) {
      throw new Error("Recorder event source does not match the active tab");
    }
    try {
      appendRecorderEvent(state, message.event);
    } catch (error) {
      if (!String(error?.message || error).includes("event limit")) throw error;
      state.active = false;
      state.stoppedAt = Date.now();
      state.stopReason = "event_limit";
    }
    await saveRecorder(state);
    return recorderStatus(state);
  }
  throw new Error(`Unknown recorder message type: ${message.type}`);
}

async function startRecorder() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab available for recording");
  const state = createRecorderState({ startedAt: Date.now(), idSeed: `${tab.id}:${tab.url || ""}` });
  state.tabId = tab.id;
  state.windowId = tab.windowId;
  if (tab.url) {
    appendRecorderEvent(state, {
      type: "navigation",
      url: tab.url,
      title: tab.title || "",
      at: Date.now(),
    });
  }
  await injectRecorder(tab.id);
  await saveRecorder(state);
  return recorderStatus(state);
}

async function stopRecorder() {
  const state = await loadRecorder();
  if (!state) return recorderStatus(null);
  state.active = false;
  state.stoppedAt = Date.now();
  await saveRecorder(state);
  return {
    ...recorderStatus(state),
    export: exportRecordingFlow(state),
  };
}

async function recordTabNavigation(tabId, tab) {
  const state = await loadRecorder();
  if (!state?.active || state.tabId !== tabId) return;
  const url = tab?.url || "";
  if (!url || url.startsWith("chrome://") || url.startsWith("chrome-extension://")) return;
  const previous = state.events[state.events.length - 1];
  if (previous?.type === "navigation" && previous.url === sanitizeUrl(url)) return;
  try {
    appendRecorderEvent(state, {
      type: "navigation",
      url,
      title: tab.title || "",
      at: Date.now(),
    });
  } catch (error) {
    if (!String(error?.message || error).includes("event limit")) throw error;
    state.active = false;
    state.stoppedAt = Date.now();
    state.stopReason = "event_limit";
  }
  await saveRecorder(state);
  if (state.active) await injectRecorder(tabId).catch(() => {});
}

async function injectRecorder(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content/recorder-content.js"],
  });
}

async function loadRecorder() {
  const stored = await chrome.storage.session.get({ [RECORDER_KEY]: null });
  const state = stored[RECORDER_KEY];
  if (state?.active && isRecorderExpired(state)) {
    state.active = false;
    state.stoppedAt = Number(state.expiresAt) || Date.now();
    state.stopReason = "expired";
    await saveRecorder(state);
  }
  return state;
}

async function saveRecorder(state) {
  if (!state) {
    await chrome.storage.session.remove([RECORDER_KEY]);
    return;
  }
  await chrome.storage.session.set({ [RECORDER_KEY]: state });
}

function recorderStatus(state) {
  return {
    active: Boolean(state?.active),
    id: state?.id || null,
    tabId: state?.tabId || null,
    startedAt: state?.startedAt || null,
    stoppedAt: state?.stoppedAt || null,
    expiresAt: state?.expiresAt || null,
    stopReason: state?.stopReason || null,
    eventCount: state?.events?.length || 0,
    secretCount: state?.secretCount || 0,
    eventLimit: MAX_RECORDER_EVENTS,
  };
}
