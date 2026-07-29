import { Stagehand } from "@browserbasehq/stagehand";

const MAX_INPUT_BYTES = 64 * 1024;

async function readInput() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) throw new Error("input_too_large");
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (typeof value?.cdpUrl !== "string" || !value.cdpUrl.startsWith("ws://127.0.0.1:")) {
    throw new Error("invalid_cdp_url");
  }
  const target = new URL(String(value?.targetUrl || ""));
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new Error("invalid_target_url");
  }
  return { cdpUrl: value.cdpUrl, targetUrl: target.href };
}

async function main() {
  let stagehand;
  try {
    const { cdpUrl, targetUrl } = await readInput();
    stagehand = new Stagehand({
      env: "LOCAL",
      verbose: 0,
      disablePino: true,
      keepAlive: true,
      localBrowserLaunchOptions: {
        cdpUrl,
        connectTimeoutMs: 15_000,
      },
    });
    await stagehand.init();
    const pages = stagehand.context.pages();
    const page = pages.at(-1) ?? await stagehand.context.newPage();
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeoutMs: 60_000 });
    const title = await page.title();
    process.stdout.write(`${JSON.stringify({ ok: true, url: page.url(), title })}\n`);
  } catch {
    process.stdout.write(`${JSON.stringify({ ok: false, errorCode: "stagehand_failed" })}\n`);
    process.exitCode = 1;
  } finally {
    if (stagehand) {
      try {
        await stagehand.close();
      } catch {
        // The Manager revokes the short-lived CDP capability after this process exits.
      }
    }
  }
}

await main();
