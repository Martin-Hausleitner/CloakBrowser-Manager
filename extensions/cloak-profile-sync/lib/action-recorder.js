const SENSITIVE_FIELD_RE =
  /(pass(word)?|pwd|secret|token|bearer|session|cookie|otp|totp|mfa|2fa|passkey|credential|cvv|cvc|card|cc-|cc_|credit|iban|routing|account)/i;

const SENSITIVE_QUERY_RE =
  /(access[_-]?token|auth|authorization|bearer|code|cookie|credential|id[_-]?token|api[_-]?key|otp|password|passkey|refresh[_-]?token|secret|session|sid|token|totp)/i;

const SENSITIVE_TEXT_RE =
  /(authorization\s*:\s*bearer|bearer\s+[a-z0-9._~+/=-]{8,}|(?:password|passwd|pwd|token|secret|cookie|otp|totp|api[_-]?key)\s*[:=])/i;

export const MAX_RECORDER_EVENTS = 500;
export const DEFAULT_RECORDER_TTL_MS = 30 * 60 * 1000;

const ACTIONS = {
  navigation: "navigate",
  click: "click",
  input: "fill",
};

export function createRecorderState({
  startedAt = Date.now(),
  idSeed = "",
  ttlMs = DEFAULT_RECORDER_TTL_MS,
} = {}) {
  return {
    active: true,
    id: `recording-${stableHash(`${idSeed}:${startedAt}`).slice(0, 10)}`,
    startedAt,
    expiresAt: startedAt + Math.max(1, Number(ttlMs) || DEFAULT_RECORDER_TTL_MS),
    sequence: 0,
    secretCount: 0,
    events: [],
  };
}

export function normalizeRecorderEvent(state, rawEvent) {
  if (!state?.active) throw new Error("Recorder is not active");
  const type = String(rawEvent?.type || "");
  if (!ACTIONS[type]) throw new Error(`Unsupported recorder event: ${type || "missing"}`);

  const sequence = nextSequence(state);
  const event = {
    sequence,
    at: Number(rawEvent.at || Date.now()),
    type,
    action: ACTIONS[type],
    url: sanitizeUrl(rawEvent.url),
  };

  if (type === "navigation") {
    event.title = cleanText(rawEvent.title, 120);
    return event;
  }

  event.selector = sanitizeSelector(rawEvent.selector);

  if (type === "click") {
    event.button = Number.isInteger(rawEvent.button) ? rawEvent.button : 0;
    const text = cleanText(rawEvent.text, 80);
    event.text = SENSITIVE_TEXT_RE.test(text) ? "" : text;
    return event;
  }

  const field = normalizeField(rawEvent.field);
  const valueLength = Number.isInteger(rawEvent.valueLength)
    ? rawEvent.valueLength
    : String(rawEvent.value ?? "").length;
  const sensitive = Boolean(rawEvent.sensitive) || isSensitiveInput(field, rawEvent.selector);
  event.field = field;
  event.valueLength = valueLength;
  event.sensitive = sensitive;
  if (sensitive) {
    state.secretCount = (state.secretCount || 0) + 1;
    event.secretRef = `secretref-${stableHash(`${state.id}:${sequence}:${field.name}:${field.type}:${event.selector}`).slice(0, 16)}`;
  }
  return event;
}

export function appendRecorderEvent(state, rawEvent) {
  if (state.events.length >= MAX_RECORDER_EVENTS) {
    throw new Error(`Recorder event limit reached (${MAX_RECORDER_EVENTS})`);
  }
  const event = normalizeRecorderEvent(state, rawEvent);
  state.events.push(event);
  return event;
}

export function isRecorderExpired(state, now = Date.now()) {
  return Boolean(state?.expiresAt && Number(now) >= Number(state.expiresAt));
}

export function exportRecordingFlow(recording) {
  const events = [...(recording.events || [])].sort(
    (a, b) => Number(a.at || 0) - Number(b.at || 0) || Number(a.sequence || 0) - Number(b.sequence || 0),
  );
  return {
    schema: "cloakbrowser.secure-action-recording.v1",
    id: recording.id,
    startedAt: recording.startedAt,
    stoppedAt: recording.stoppedAt || null,
    stepCount: events.length,
    secretRefs: events.filter((event) => event.secretRef).map((event) => event.secretRef),
    prompt: buildPrompt(events),
    steps: events.map(exportStep),
  };
}

export function isSensitiveInput(field = {}, selector = "") {
  const haystack = [
    field.type,
    field.name,
    field.id,
    field.autocomplete,
    field.ariaLabel,
    field.placeholder,
    selector,
  ]
    .filter(Boolean)
    .join(" ");
  return SENSITIVE_FIELD_RE.test(haystack);
}

export function sanitizeUrl(url) {
  if (!url) return "";
  try {
    const parsed = new URL(String(url));
    for (const key of [...parsed.searchParams.keys()]) {
      if (SENSITIVE_QUERY_RE.test(key)) parsed.searchParams.delete(key);
    }
    parsed.username = "";
    parsed.password = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return "";
  }
}

function exportStep(event) {
  const base = {
    id: `step-${String(event.sequence).padStart(3, "0")}`,
    action: event.action,
    url: event.url,
  };
  if (event.action === "navigate") {
    if (event.title) base.title = event.title;
    return base;
  }
  base.selector = event.selector;
  if (event.action === "click") {
    if (event.text) base.text = event.text;
    base.button = event.button || 0;
    return base;
  }
  base.field = event.field;
  base.valueLength = event.valueLength || 0;
  if (event.secretRef) base.secretRef = event.secretRef;
  return base;
}

function buildPrompt(events) {
  const lines = ["Replay this Browser Use flow with the recorded selectors and redacted secret references:"];
  for (const event of events) {
    if (event.action === "navigate") lines.push(`- navigate ${event.url}`);
    if (event.action === "click") lines.push(`- click ${event.selector}`);
    if (event.action === "fill") {
      const target = event.secretRef || `metadata-only length ${event.valueLength || 0}`;
      lines.push(`- fill ${event.selector} using ${target}`);
    }
  }
  return lines.join("\n");
}

function normalizeField(field = {}) {
  return {
    name: cleanToken(field.name),
    id: cleanToken(field.id),
    type: cleanToken(field.type || "text"),
    autocomplete: cleanToken(field.autocomplete),
    ariaLabel: cleanText(field.ariaLabel, 80),
    placeholder: isSensitiveInput(field)
      ? ""
      : cleanText(field.placeholder, 80),
  };
}

function nextSequence(state) {
  state.sequence = Number(state.sequence || 0) + 1;
  return state.sequence;
}

function sanitizeSelector(selector) {
  const text = String(selector || "").trim();
  if (!text) return "[unknown]";
  return text
    .replace(
      /\[(?:value|data-(?:token|secret|password|session|otp|code))\s*[*^$|~]?=\s*(?:"[^"]*"|'[^']*'|[^\]]+)\]/gi,
      "[data-redacted]",
    )
    .replace(/([?&](?:password|token|secret|session|otp|totp|code)=)[^"'\]\s)]+/gi, "$1[redacted]")
    .slice(0, 240);
}

function cleanToken(value) {
  return String(value || "").replace(/[^\w:.-]/g, "").slice(0, 80);
}

function cleanText(value, max) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
}

function stableHash(value) {
  let hash = 0x811c9dc5;
  const text = String(value);
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}
