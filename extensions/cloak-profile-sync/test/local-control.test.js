import test from "node:test";
import assert from "node:assert/strict";

import {
  commandToRecorderMessage,
  isAllowedExtensionOrigin,
} from "../lib/local-control.js";


test("local control exposes only bounded recorder operations", () => {
  assert.deepEqual(commandToRecorderMessage("status"), { type: "RECORDER_GET_STATUS" });
  assert.deepEqual(commandToRecorderMessage("start"), { type: "RECORDER_START" });
  assert.deepEqual(commandToRecorderMessage("stop"), { type: "RECORDER_STOP" });
  assert.deepEqual(commandToRecorderMessage("export"), { type: "RECORDER_EXPORT" });
  assert.deepEqual(commandToRecorderMessage("clear"), { type: "RECORDER_CLEAR" });
  assert.throws(() => commandToRecorderMessage("navigate"), /Unsupported local control/);
  assert.throws(() => commandToRecorderMessage("shell"), /Unsupported local control/);
});


test("bridge pairing is fixed to this extension identity", () => {
  assert.equal(
    isAllowedExtensionOrigin("chrome-extension://fjcjfaeimhopmpnoemigapegahhjnbkl"),
    true,
  );
  assert.equal(isAllowedExtensionOrigin("https://example.com"), false);
  assert.equal(isAllowedExtensionOrigin("chrome-extension://other"), false);
});
