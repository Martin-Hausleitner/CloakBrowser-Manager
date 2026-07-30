const EXTENSION_ID = "fjcjfaeimhopmpnoemigapegahhjnbkl";
export const ALLOWED_EXTENSION_ORIGIN = `chrome-extension://${EXTENSION_ID}`;
export const DEFAULT_CONTROL_BRIDGE = "http://127.0.0.1:18766";

const COMMAND_MESSAGES = Object.freeze({
  status: "RECORDER_GET_STATUS",
  start: "RECORDER_START",
  stop: "RECORDER_STOP",
  export: "RECORDER_EXPORT",
  clear: "RECORDER_CLEAR",
});

export function commandToRecorderMessage(operation) {
  const type = COMMAND_MESSAGES[String(operation || "").trim().toLowerCase()];
  if (!type) throw new Error("Unsupported local control operation");
  return { type };
}

export function isAllowedExtensionOrigin(origin) {
  return origin === ALLOWED_EXTENSION_ORIGIN;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function jsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) {
    throw new Error("Local extension control bridge rejected the request");
  }
  return payload;
}

async function createSession(fetchImpl, bridgeBase) {
  const response = await fetchImpl(`${bridgeBase}/v1/extension/session`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-CBM-Extension-Id": EXTENSION_ID,
    },
    body: "{}",
  });
  const payload = await jsonResponse(response);
  if (typeof payload.sessionToken !== "string" || payload.sessionToken.length < 43) {
    throw new Error("Local extension control returned an invalid session");
  }
  return payload.sessionToken;
}

export async function runLocalControlLoop(
  executeRecorderMessage,
  {
    bridgeBase = DEFAULT_CONTROL_BRIDGE,
    fetchImpl = fetch,
    retryDelayMs = 2000,
  } = {},
) {
  let sessionToken = null;
  for (;;) {
    try {
      if (!sessionToken) sessionToken = await createSession(fetchImpl, bridgeBase);
      const response = await fetchImpl(`${bridgeBase}/v1/extension/commands/next?wait=15`, {
        method: "GET",
        cache: "no-store",
        headers: { Authorization: `Bearer ${sessionToken}` },
      });
      const payload = await jsonResponse(response);
      const command = payload.command;
      if (!command) continue;
      let result;
      try {
        result = await executeRecorderMessage(commandToRecorderMessage(command.operation));
      } catch (error) {
        result = {
          ok: false,
          error: String(error?.message || "recorder command failed").slice(0, 240),
        };
      }
      await jsonResponse(
        await fetchImpl(
          `${bridgeBase}/v1/extension/commands/${encodeURIComponent(command.id)}/result`,
          {
            method: "POST",
            cache: "no-store",
            headers: {
              Authorization: `Bearer ${sessionToken}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ result }),
          },
        ),
      );
    } catch (_error) {
      sessionToken = null;
      await delay(retryDelayMs);
    }
  }
}
