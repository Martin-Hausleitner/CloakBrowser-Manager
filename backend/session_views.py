"""Minimal fullscreen session HTML pages (VNC deep-link + CDP screencast).

The CDP live viewer is a Manager-owned observer: it discovers page targets via
``/cdp-observer`` and streams screencast frames only. Interactive control stays
on VNC (or an explicitly leased automation surface).
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
    """Self-contained observer screencast page. No secrets or arbitrary CDP."""
    safe_name = html.escape(profile_name or profile_id)
    safe_id = html.escape(profile_id)
    config = json.dumps(
        {
            "profileId": profile_id,
            "cdpWsUrl": cdp_ws_url,
            "cdpListUrl": cdp_list_url,
            "metricsUrl": metrics_url,
            # Observer UI never injects input; interactive is ignored for CDP.
            "interactive": False,
            "screencast": {
                "format": "jpeg",
                "quality": 35,
                "maxWidth": 1280,
                "maxHeight": 720,
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
    canvas {{ max-width: 100%; max-height: 100%; background: #000; cursor: default; image-rendering: auto; }}
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
  let pendingJpeg = null;
  let frames = 0;
  let dropped = 0;
  let lastFpsAt = performance.now();
  let fps = 0;
  let rttMs = null;
  let reconnectCount = 0;
  let metricsTimer = null;
  let lastFrameAt = 0;
  let totalFrames = 0;
  let castStartedAt = 0;

  function setStatus(text) {{ statusEl.textContent = text; }}
  function setMetrics() {{
    const parts = [];
    if (fps) parts.push(fps.toFixed(0) + ' fps');
    if (rttMs != null) parts.push(Math.round(rttMs) + ' ms rtt');
    parts.push('cast');
    if (reconnectCount) parts.push('reconn ' + reconnectCount);
    metricsEl.textContent = parts.join(' · ');
  }}

  function noteFrame() {{
    lastFrameAt = performance.now();
    frames += 1;
    totalFrames += 1;
    const now = lastFrameAt;
    if (now - lastFpsAt >= 1000) {{
      fps = frames * 1000 / (now - lastFpsAt);
      frames = 0;
      lastFpsAt = now;
      setMetrics();
    }}
  }}

  function b64ToUint8(b64) {{
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }}

  async function paintJpegBase64(data) {{
    if (drawing) {{
      pendingJpeg = data;
      dropped += 1;
      return;
    }}
    drawing = true;
    try {{
      const bytes = b64ToUint8(data);
      const bitmap = await createImageBitmap(new Blob([bytes], {{ type: 'image/jpeg' }}));
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {{
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
      }}
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      noteFrame();
    }} catch (_) {{
      await new Promise((resolve) => {{
        const img = new Image();
        img.onload = () => {{
          if (canvas.width !== img.width || canvas.height !== img.height) {{
            canvas.width = img.width;
            canvas.height = img.height;
          }}
          ctx.drawImage(img, 0, 0);
          noteFrame();
          resolve();
        }};
        img.onerror = () => {{ dropped += 1; resolve(); }};
        img.src = 'data:image/jpeg;base64,' + data;
      }});
    }} finally {{
      drawing = false;
      if (pendingJpeg) {{
        const next = pendingJpeg;
        pendingJpeg = null;
        paintJpegBase64(next);
      }}
    }}
  }}

  function sendScreencast(method, params) {{
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('offline'));
    const id = msgId++;
    const payload = {{ id, method, params: params || {{}} }};
    ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {{
      const timer = setTimeout(() => {{ pending.delete(id); reject(new Error('timeout ' + method)); }}, 8000);
      pending.set(id, {{ resolve, reject, timer }});
    }});
  }}

  function ackScreencastFrame(frameSessionId) {{
    if (frameSessionId == null || !ws || ws.readyState !== WebSocket.OPEN) return;
    const id = msgId++;
    try {{
      ws.send(JSON.stringify({{
        id,
        method: 'Page.screencastFrameAck',
        params: {{ sessionId: frameSessionId }},
      }}));
    }} catch (_) {{}}
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

  async function startScreencast() {{
    castStartedAt = performance.now();
    lastFrameAt = castStartedAt;
    await sendScreencast('Page.startScreencast', CONFIG.screencast);
    setStatus('live · CDP cast');
    reconnectAttempt = 0;
  }}

  async function stopScreencast() {{
    try {{ await sendScreencast('Page.stopScreencast', {{}}); }} catch (_) {{}}
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

  function bindSocket(url) {{
    totalFrames = 0;
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
        if (rttMs == null && castStartedAt) rttMs = lastFrameAt - castStartedAt;
        paintJpegBase64(params.data);
        setMetrics();
      }}
    }});
  }}

  async function connect() {{
    if (ws) {{
      try {{ await stopScreencast(); }} catch (_) {{}}
      try {{ ws.close(); }} catch (_) {{}}
      ws = null;
    }}
    if (reconnectTimer) {{ clearTimeout(reconnectTimer); reconnectTimer = null; }}
    const pageUrl = await resolvePageWsUrl();
    if (pageUrl) {{
      bindSocket(pageUrl);
      return;
    }}
    if (CONFIG.cdpWsUrl) {{
      bindSocket(CONFIG.cdpWsUrl);
      return;
    }}
    setStatus('no observer target');
    scheduleReconnect();
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
          frames_received: totalFrames,
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
