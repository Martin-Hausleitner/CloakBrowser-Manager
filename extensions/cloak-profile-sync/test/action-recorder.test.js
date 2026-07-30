import assert from "node:assert/strict";
import test from "node:test";

import {
  appendRecorderEvent,
  createRecorderState,
  normalizeRecorderEvent,
  exportRecordingFlow,
  isRecorderExpired,
  MAX_RECORDER_EVENTS,
} from "../lib/action-recorder.js";

test("normalizes navigation clicks and sensitive inputs without raw secrets", () => {
  const state = createRecorderState({ startedAt: 10, idSeed: "demo" });
  const events = [
    normalizeRecorderEvent(state, {
      type: "navigation",
      url: "https://example.test/login?session=abc123&safe=1",
      title: "Login",
      at: 30,
    }),
    normalizeRecorderEvent(state, {
      type: "click",
      url: "https://example.test/login",
      selector: "#submit",
      text: "Sign in",
      button: 0,
      at: 40,
    }),
    normalizeRecorderEvent(state, {
      type: "input",
      url: "https://example.test/login",
      selector: "input[name=password]",
      field: { name: "password", type: "password", autocomplete: "current-password" },
      value: "CorrectHorseBatteryStaple1!",
      at: 50,
    }),
    normalizeRecorderEvent(state, {
      type: "input",
      url: "https://example.test/login",
      selector: "input[name=email]",
      field: { name: "email", type: "email", autocomplete: "email" },
      value: "person@example.test",
      at: 60,
    }),
  ];

  const exported = exportRecordingFlow({ id: "rec-1", startedAt: 10, stoppedAt: 80, events });
  const json = JSON.stringify(exported);

  assert.deepEqual(exported.steps.map((step) => step.action), ["navigate", "click", "fill", "fill"]);
  assert.equal(exported.steps[0].url, "https://example.test/login?safe=1");
  assert.match(exported.steps[2].secretRef, /^secretref-/);
  assert.equal(exported.steps[2].valueLength, 27);
  assert.equal(exported.steps[3].value, undefined);
  assert.equal(exported.steps[3].valueLength, "person@example.test".length);
  assert.equal(json.includes("CorrectHorseBatteryStaple1!"), false);
  assert.equal(json.includes("session=abc123"), false);
  assert.equal(json.includes("person@example.test"), false);
});

test("exports a stable flow while stopped and omits raw token-like selectors", () => {
  const state = createRecorderState({ startedAt: 1000, idSeed: "stable" });
  const event = normalizeRecorderEvent(state, {
    type: "input",
    url: "https://app.example.test/?token=abc&view=form",
    selector: "input[name=api_token][value=shh]",
    field: { name: "api_token", type: "text", autocomplete: "off" },
    value: "sk-live-secret",
    at: 1010,
  });
  const first = exportRecordingFlow({
    id: state.id,
    startedAt: state.startedAt,
    stoppedAt: 2000,
    events: [event],
  });
  const second = exportRecordingFlow({
    id: state.id,
    startedAt: state.startedAt,
    stoppedAt: 2000,
    events: [event],
  });

  assert.deepEqual(second, first);
  assert.match(first.steps[0].secretRef, /^secretref-/);
  assert.equal(JSON.stringify(first).includes("sk-live-secret"), false);
  assert.equal(JSON.stringify(first).includes("token=abc"), false);
  assert.equal(first.steps[0].url, "https://app.example.test/?view=form");
  assert.equal(JSON.stringify(first).includes("value=shh"), false);
});

test("expires recordings and stops at the bounded event limit", () => {
  const state = createRecorderState({ startedAt: 1_000, idSeed: "bounded", ttlMs: 1_000 });
  assert.equal(state.expiresAt, 2_000);
  assert.equal(isRecorderExpired(state, 1_999), false);
  assert.equal(isRecorderExpired(state, 2_000), true);

  for (let index = 0; index < MAX_RECORDER_EVENTS; index += 1) {
    appendRecorderEvent(state, {
      type: "click",
      url: "https://example.test/",
      selector: `#button-${index}`,
      text: "Continue",
      at: 1_001 + index,
    });
  }
  assert.equal(state.events.length, MAX_RECORDER_EVENTS);
  assert.throws(
    () =>
      appendRecorderEvent(state, {
        type: "click",
        url: "https://example.test/",
        selector: "#overflow",
        text: "Continue",
        at: 1_900,
      }),
    /event limit/i,
  );
});

test("drops secret-like visible click labels", () => {
  const state = createRecorderState({ startedAt: 10, idSeed: "click-secret" });
  const event = normalizeRecorderEvent(state, {
    type: "click",
    url: "https://example.test/",
    selector: "button",
    text: "Authorization: Bearer raw-secret-value",
    at: 11,
  });
  const exported = exportRecordingFlow({ ...state, events: [event], stoppedAt: 12 });
  assert.equal(JSON.stringify(exported).includes("raw-secret-value"), false);
  assert.equal(exported.steps[0].text, undefined);
});
