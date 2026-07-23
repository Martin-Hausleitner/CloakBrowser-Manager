"""Minimal fullscreen session HTML pages (VNC deep-link + CDP screencast).

The CDP live viewer is oriented after steel-browser's cast/debug streamer and
Browser-Use ``liveUrl``: one absolute URL that opens a snappy fullscreen feed
via CDP ``Page.startScreencast`` rather than RFB/VNC.

Tuned for ultra-low latency: low JPEG quality, immediate frame ack, skip-busy
draws, and stable reconnect with backoff. Posts live metrics to the Manager.
"""

from __future__ import annotations

import html
import json


def vnc_fullscreen_path(profile_id: str) -> str:
    """SPA deep-link that opens the existing noVNC surface fullscreen."""
    return f"/?profile={profile_id}&view=vnc&fullscreen=1"


def cdp_fullscreen_path(profile_id: str) -> str:
    """Standalone CDP screencast fullscreen page (Browser-Use-style live URL)."""
    return f"/session/{profile_id}/live"


def render_cdp_live_html(
    *,
    profile_id: str,
    profile_name: str,
    cdp_ws_url: str,
    metrics_url: str,
    interactive: bool,
    cdp_list_url: str | None = None,
) -> str:
    """Self-contained low-latency CDP screencast page. No secrets embedded."""
    safe_name = html.escape(profile_name or profile_id)
    safe_id = html.escape(profile_id)
    config = json.dumps(
        {
            "profileId": profile_id,
            "cdpWsUrl": cdp_ws_url,
            "cdpListUrl": cdp_list_url,
            "metricsUrl": metrics_url,
            "interactive": bool(interactive),
            "screencast": {
                "format": "jpeg",
                "quality": 40,
                "maxWidth": 1280,
                "maxHeight": 800,
                "everyNthFrame": 1,
            },
        }
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Live · {safe_name}</title>
  <style>
    :root {{ color-scheme: dark; }}
    html, body {{ margin: 0; height: 100%; background: #050607; color: #e8eaed; font: 12px/1.35 ui-sans-serif, system-ui, sans-serif; }}
    #bar {{ display: flex; gap: 8px; align-items: center; padding: 6px 10px; background: #0f1318; border-bottom: 1px solid #1c2430; }}
    #bar strong {{ font-weight: 600; letter-spacing: 0.01em; }}
    #status, #metrics {{ opacity: 0.8; }}
    #stage {{ position: absolute; inset: 34px 0 0 0; display: flex; align-items: center; justify-content: center; background: #000; }}
    canvas {{ max-width: 100%; max-height: 100%; background: #000; cursor: crosshair; image-rendering: auto; }}
    button, a button {{ background: #2563eb; color: #fff; border: 0; border-radius: 6px; padding: 5px 9px; cursor: pointer; font: inherit; }}
    button.secondary {{ background: #222a35; }}
    a {{ text-decoration: none; }}
  </style>
</head>
<body>
  <div id="bar">
    <strong>{safe_name}</strong>
    <span id="status">connecting…</span>
    <span id="metrics"></span>
    <span style="flex:1"></span>
    <button class="secondary" id="reload" type="button">Reconnect</button>
    <a href="/?profile={safe_id}&view=vnc&fullscreen=1"><button type="button">VNC</button></a>
  </div>
  <div id="stage"><canvas id="frame" tabindex="0"></canvas></div>
  <script>
  const CONFIG = {config};
  const statusEl = document.getElementById('status');
  const metricsEl = document.getElementById('metrics');
  const canvas = document.getElementById('frame');
  const ctx = canvas.getContext('2d', {{ alpha: false, desynchronized: true }});
  let ws = null;
  let msgId = 1;
  const pending = new Map();
  let reconnectAttempt = 0;
  let reconnectTimer = null;
  let drawing = false;
  let frames = 0;
  let dropped = 0;
  let lastFpsAt = performance.now();
  let fps = 0;
  let rttMs = null;
  let reconnectCount = 0;
  let metricsTimer = null;
  // Prefer page-target WS; browser WS uses flattened pageSessionId for Page.*/Input.*.
  let pageSessionId = null;
  let usingBrowserRoot = false;
  let streamMode = 'screencast'; // screencast | screenshot
  let screenshotLoop = null;
  let lastFrameAt = 0;
  let stallTimer = null;

  function setStatus(text) {{ statusEl.textContent = text; }}
  function setMetrics() {{
    const parts = [];
    if (fps) parts.push(fps.toFixed(0) + ' fps');
    if (rttMs != null) parts.push(Math.round(rttMs) + ' ms rtt');
    if (streamMode === 'screenshot') parts.push('shot');
    if (reconnectCount) parts.push('reconn ' + reconnectCount);
    metricsEl.textContent = parts.join(' · ');
  }}

  function noteFrame() {{
    lastFrameAt = performance.now();
    frames += 1;
    const now = lastFrameAt;
    if (now - lastFpsAt >= 1000) {{
      fps = frames * 1000 / (now - lastFpsAt);
      frames = 0;
      lastFpsAt = now;
      setMetrics();
    }}
  }}

  function paintJpegBase64(data) {{
    if (drawing) {{ dropped += 1; return; }}
    drawing = true;
    const img = new Image();
    img.onload = () => {{
      if (canvas.width !== img.width || canvas.height !== img.height) {{
        canvas.width = img.width;
        canvas.height = img.height;
      }}
      ctx.drawImage(img, 0, 0);
      drawing = false;
      noteFrame();
    }};
    img.onerror = () => {{ drawing = false; dropped += 1; }};
    img.src = 'data:image/jpeg;base64,' + data;
  }}

  function send(method, params = {{}}, opts = {{}}) {{
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('offline'));
    const id = msgId++;
    const payload = {{ id, method, params }};
    const sessionId = opts.sessionId !== undefined ? opts.sessionId : pageSessionId;
    // Target.* / Browser.* talk to the browser root; Page/Input need the page session.
    if (sessionId && !method.startsWith('Target.') && !method.startsWith('Browser.')) {{
      payload.sessionId = sessionId;
    }}
    ws.send(JSON.stringify(payload));
    if (opts.noWait) return Promise.resolve({{}});
    return new Promise((resolve, reject) => {{
      const timer = setTimeout(() => {{ pending.delete(id); reject(new Error('timeout ' + method)); }}, 8000);
      pending.set(id, {{ resolve, reject, timer }});
    }});
  }}

  function ackScreencastFrame(frameSessionId) {{
    if (frameSessionId == null || !ws || ws.readyState !== WebSocket.OPEN) return;
    const id = msgId++;
    const payload = {{
      id,
      method: 'Page.screencastFrameAck',
      params: {{ sessionId: frameSessionId }},
    }};
    if (pageSessionId) payload.sessionId = pageSessionId;
    try {{ ws.send(JSON.stringify(payload)); }} catch (_) {{}}
  }}

  async function resolvePageWsUrl() {{
    if (!CONFIG.cdpListUrl) return null;
    try {{
      const resp = await fetch(CONFIG.cdpListUrl, {{ credentials: 'include', headers: {{ Accept: 'application/json' }} }});
      if (!resp.ok) return null;
      const list = await resp.json();
      const page = (Array.isArray(list) ? list : []).find((t) => t && t.type === 'page' && t.webSocketDebuggerUrl);
      return page ? page.webSocketDebuggerUrl : null;
    }} catch (_) {{
      return null;
    }}
  }}

  async function attachPageSession() {{
    pageSessionId = null;
    await send('Target.setDiscoverTargets', {{ discover: true }});
    const {{ targetInfos }} = await send('Target.getTargets');
    let page = (targetInfos || []).find((t) => t.type === 'page' && !t.attached);
    if (!page) {{
      page = (targetInfos || []).find((t) => t.type === 'page');
    }}
    if (!page) {{
      const created = await send('Target.createTarget', {{ url: 'about:blank' }});
      page = {{ targetId: created.targetId }};
    }}
    const attached = await send('Target.attachToTarget', {{
      targetId: page.targetId,
      flatten: true,
    }});
    pageSessionId = attached.sessionId;
    return pageSessionId;
  }}

  function stopScreenshotLoop() {{
    if (screenshotLoop) {{
      clearTimeout(screenshotLoop);
      screenshotLoop = null;
    }}
  }}

  async function screenshotTick() {{
    if (!ws || ws.readyState !== WebSocket.OPEN || streamMode !== 'screenshot') return;
    const t0 = performance.now();
    try {{
      const result = await send('Page.captureScreenshot', {{
        format: 'jpeg',
        quality: CONFIG.screencast.quality || 40,
      }});
      rttMs = performance.now() - t0;
      if (result && result.data) paintJpegBase64(result.data);
      setMetrics();
    }} catch (_) {{}}
    screenshotLoop = setTimeout(() => {{ screenshotTick(); }}, 50);
  }}

  function startScreenshotFallback(reason) {{
    if (streamMode === 'screenshot') return;
    streamMode = 'screenshot';
    stopScreenshotLoop();
    send('Page.stopScreencast').catch(() => {{}});
    setStatus('live · CDP shot');
    screenshotTick();
  }}

  function watchScreencastStall() {{
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => {{
      // Cloak Chromium often emits a single screencast frame then stalls.
      if (streamMode === 'screencast' && performance.now() - lastFrameAt > 1200) {{
        startScreenshotFallback('stall');
      }}
    }}, 1400);
  }}

  async function startScreencast() {{
    streamMode = 'screencast';
    stopScreenshotLoop();
    if (usingBrowserRoot) {{
      await attachPageSession();
    }} else {{
      pageSessionId = null;
    }}
    await send('Page.enable');
    lastFrameAt = performance.now();
    await send('Page.startScreencast', CONFIG.screencast);
    setStatus('live · CDP');
    reconnectAttempt = 0;
    watchScreencastStall();
    pingLoop();
  }}

  async function pingLoop() {{
    while (ws && ws.readyState === WebSocket.OPEN) {{
      if (streamMode === 'screenshot') {{
        // RTT sampled inside screenshotTick.
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }}
      const t0 = performance.now();
      try {{
        await send('Page.getLayoutMetrics');
        rttMs = performance.now() - t0;
        setMetrics();
      }} catch (_) {{}}
      await new Promise((r) => setTimeout(r, 2000));
    }}
  }}

  function scheduleReconnect() {{
    if (reconnectTimer) return;
    const delay = Math.min(2000, 200 * Math.pow(1.6, reconnectAttempt++));
    reconnectCount += 1;
    setStatus('reconnecting in ' + Math.round(delay) + 'ms');
    reconnectTimer = setTimeout(() => {{
      reconnectTimer = null;
      connect();
    }}, delay);
  }}

  function bindSocket(url, browserRoot) {{
    usingBrowserRoot = !!browserRoot;
    pageSessionId = null;
    streamMode = 'screencast';
    stopScreenshotLoop();
    setStatus('connecting…');
    ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    ws.addEventListener('open', () => {{
      startScreencast().catch((err) => {{
        setStatus('start failed: ' + (err && err.message ? err.message : 'error'));
        scheduleReconnect();
      }});
    }});
    ws.addEventListener('close', () => {{
      stopScreenshotLoop();
      setStatus('disconnected');
      scheduleReconnect();
    }});
    ws.addEventListener('error', () => setStatus('socket error'));
    ws.addEventListener('message', (ev) => {{
      let msg;
      try {{ msg = JSON.parse(typeof ev.data === 'string' ? ev.data : new TextDecoder().decode(ev.data)); }}
      catch (_) {{ return; }}
      if (msg.id && pending.has(msg.id)) {{
        const entry = pending.get(msg.id);
        clearTimeout(entry.timer);
        pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(msg.error.message || 'cdp error'));
        else entry.resolve(msg.result || {{}});
        return;
      }}
      if (msg.method === 'Page.screencastFrame') {{
        const params = msg.params || {{}};
        ackScreencastFrame(params.sessionId);
        lastFrameAt = performance.now();
        paintJpegBase64(params.data);
      }}
    }});
  }}

  async function connect() {{
    if (ws) {{
      try {{ ws.close(); }} catch (_) {{}}
      ws = null;
    }}
    if (reconnectTimer) {{ clearTimeout(reconnectTimer); reconnectTimer = null; }}
    if (stallTimer) {{ clearTimeout(stallTimer); stallTimer = null; }}
    stopScreenshotLoop();
    const pageUrl = await resolvePageWsUrl();
    if (pageUrl) {{
      bindSocket(pageUrl, false);
      return;
    }}
    bindSocket(CONFIG.cdpWsUrl, true);
  }}

  function canvasPoint(ev) {{
    const rect = canvas.getBoundingClientRect();
    return {{
      x: (ev.clientX - rect.left) * (canvas.width / rect.width),
      y: (ev.clientY - rect.top) * (canvas.height / rect.height),
    }};
  }}

  if (CONFIG.interactive) {{
    canvas.addEventListener('pointerdown', async (ev) => {{
      canvas.focus();
      const p = canvasPoint(ev);
      await send('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 }}).catch(() => {{}});
    }});
    canvas.addEventListener('pointerup', async (ev) => {{
      const p = canvasPoint(ev);
      await send('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 }}).catch(() => {{}});
    }});
    canvas.addEventListener('keydown', async (ev) => {{
      ev.preventDefault();
      await send('Input.dispatchKeyEvent', {{ type: 'keyDown', key: ev.key, code: ev.code, text: ev.key.length === 1 ? ev.key : undefined }}).catch(() => {{}});
    }});
    canvas.addEventListener('keyup', async (ev) => {{
      ev.preventDefault();
      await send('Input.dispatchKeyEvent', {{ type: 'keyUp', key: ev.key, code: ev.code }}).catch(() => {{}});
    }});
  }} else {{
    canvas.style.cursor = 'default';
  }}

  async function postMetrics() {{
    if (!CONFIG.metricsUrl) return;
    const state = (!ws || ws.readyState === WebSocket.CONNECTING)
      ? 'connecting'
      : (ws.readyState === WebSocket.OPEN ? 'connected' : 'reconnecting');
    try {{
      await fetch(CONFIG.metricsUrl, {{
        method: 'POST',
        credentials: 'include',
        headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
        body: JSON.stringify({{
          transport: 'cdp',
          connection_state: state,
          fps,
          rtt_ms: rttMs,
          frames_received: frames,
          reconnect_count: reconnectCount,
          dropped_frames: dropped,
        }}),
      }});
    }} catch (_) {{}}
  }}

  document.getElementById('reload').addEventListener('click', () => {{
    reconnectAttempt = 0;
    if (reconnectTimer) {{ clearTimeout(reconnectTimer); reconnectTimer = null; }}
    connect();
  }});
  connect();
  metricsTimer = setInterval(postMetrics, 1000);
  </script>
</body>
</html>
"""
