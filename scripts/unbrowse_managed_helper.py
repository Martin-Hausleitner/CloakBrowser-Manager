#!/usr/bin/env python3
"""Execute a vault-backed Unbrowse flow against a Manager-owned profile.

The helper claims exactly one ``browser-harness`` run, exchanges the claim for
the Manager's short-lived CDP capability, and exposes that authenticated CDP
socket only through an ephemeral loopback WebSocket relay.  Flow files contain
selectors and vault pointers, never cleartext secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from aiohttp import ClientSession, WSMsgType, web

from scripts.browser_use_worker import ManagerClient, redact_text


HARNESS = "browser-harness"
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
VAULT_PREFIXES = ("keychain://", "op://", "bw://")


class HarnessClient(ManagerClient):
    def claim(self) -> dict[str, Any] | None:
        response = self.request(
            "POST",
            f"/internal/task-runs/claim?{urlencode({'harness': HARNESS})}",
        )
        if response.status_code == 204:
            return None
        return response.json()


def normalized_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("flow URL must be absolute http(s)")
    host = parsed.hostname.lower()
    port = parsed.port
    default = 80 if parsed.scheme == "http" else 443
    netloc = host if port in {None, default} else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def normalized_navigation_url(value: str) -> str:
    parsed = urlsplit(value)
    origin = normalized_origin(value)
    path = parsed.path or "/"
    return f"{origin}{path}"


def select_target(claim: dict[str, Any], flow: dict[str, Any]) -> str:
    target = str(flow.get("url") or "").strip()
    allowed = {normalized_origin(str(item)) for item in claim.get("allowed_origins") or []}
    if not allowed or normalized_origin(target) not in allowed:
        raise ValueError("flow URL origin is not allowed by the run")
    task_urls = URL_RE.findall(str(claim.get("task") or ""))
    if not any(normalized_origin(item) == normalized_origin(target) for item in task_urls):
        raise ValueError("flow URL must also be explicit in the persisted task")
    return target


def validate_flow(flow: dict[str, Any], *, task: str = "") -> None:
    actions = flow.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("flow requires actions")
    flow_origin = normalized_origin(str(flow.get("url") or ""))
    task_urls = {
        normalized_navigation_url(item.rstrip(".,);]"))
        for item in URL_RE.findall(task)
    }
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("flow actions must be objects")
        op = action.get("op")
        if op == "fill":
            selector = str(action.get("selector") or "").strip()
            pointer = str(action.get("pointer") or "").strip()
            source = str(action.get("source") or "").strip()
            if not selector:
                raise ValueError("fill requires a selector")
            if flow.get("engine") == "cdp":
                if source not in {"vcvm_email", "browser_use_password"}:
                    raise ValueError("CDP fill requires an approved VCVM value source")
            elif not pointer.startswith(VAULT_PREFIXES):
                raise ValueError("fill requires a vault pointer")
        elif op == "submit":
            continue
        elif op == "click":
            if not str(action.get("selector") or "").strip():
                raise ValueError("click requires a selector")
        elif op == "wait":
            delay = int(action.get("milliseconds") or 0)
            if delay < 0 or delay > 30_000:
                raise ValueError("wait is outside the safe range")
        elif op == "snapshot":
            continue
        elif op == "navigate":
            navigation_url = str(action.get("url") or "")
            if normalized_origin(navigation_url) != flow_origin:
                raise ValueError("navigation origin is not allowed")
            if task_urls and normalized_navigation_url(navigation_url) not in task_urls:
                raise ValueError("navigation URL must be explicit in the persisted task")
        else:
            raise ValueError("unsupported flow action")


def absolute_ws_url(manager_url: str, cdp_url: str) -> str:
    absolute = urljoin(manager_url.rstrip("/") + "/", cdp_url)
    parsed = urlsplit(absolute)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, ""))


def rewrite_discovery(value: Any, *, local_base: str, upstream_path: str) -> Any:
    if isinstance(value, dict):
        return {
            key: rewrite_discovery(item, local_base=local_base, upstream_path=upstream_path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            rewrite_discovery(item, local_base=local_base, upstream_path=upstream_path)
            for item in value
        ]
    if isinstance(value, str) and value.startswith(("ws://", "wss://")):
        parsed = urlsplit(value)
        suffix = parsed.path
        if suffix.startswith(upstream_path):
            suffix = suffix[len(upstream_path):]
        return f"{local_base}{suffix or '/'}"
    return value


def validate_cdp_state(state: dict[str, Any], flow: dict[str, Any]) -> None:
    """Enforce only redacted, positive browser-state completion gates."""
    if state.get("captcha"):
        raise RuntimeError("signup requires human verification")
    if any(
        state.get(key)
        for key in (
            "invalidEmail",
            "temporaryEmail",
            "passwordPolicy",
            "alreadyExists",
        )
    ):
        raise RuntimeError("managed Browser Harness reported an authentication form error")
    if int(state.get("errorElements") or 0) > 0:
        raise RuntimeError("managed Browser Harness reported a visible authentication error")
    if any(
        bool(item.get("genericError"))
        for item in state.get("fetchResults") or []
        if isinstance(item, dict)
    ):
        raise RuntimeError("managed Browser Harness authentication request failed")
    if flow.get("require_authenticated") and not state.get("authenticated"):
        raise RuntimeError("managed Browser Harness login was not proven")
    if (
        flow.get("require_progress")
        and int(state.get("formCount") or 0) > 0
        and not state.get("verification")
    ):
        raise RuntimeError("signup form did not advance")


async def start_cdp_gateway(
    *,
    upstream_http: str,
    headers: dict[str, str],
    bind_port: int = 0,
) -> tuple[web.AppRunner, str]:
    upstream_parts = urlsplit(upstream_http)
    upstream_path = upstream_parts.path.rstrip("/")
    upstream_origin = urlunsplit((upstream_parts.scheme, upstream_parts.netloc, "", "", ""))
    nonce = secrets.token_urlsafe(24)

    async def gateway(request: web.Request) -> web.StreamResponse:
        raw_tail = request.match_info.get("tail", "")
        is_websocket = request.headers.get("Upgrade", "").lower() == "websocket"
        if is_websocket:
            supplied_nonce, separator, suffix = raw_tail.partition("/")
            if not hmac.compare_digest(supplied_nonce, nonce):
                raise web.HTTPUnauthorized()
        else:
            suffix = raw_tail
            if suffix == nonce:
                suffix = ""
            elif suffix.startswith(f"{nonce}/"):
                suffix = suffix[len(nonce) + 1 :]
            if suffix not in {"", "json/version", "json/list", "json/protocol"}:
                raise web.HTTPNotFound()
        suffix_path = f"/{suffix}" if suffix else ("" if is_websocket else "/json/version")
        query = f"?{request.query_string}" if request.query_string else ""
        upstream_url = f"{upstream_origin}{upstream_path}{suffix_path}{query}"
        if is_websocket:
            client_ws = web.WebSocketResponse(max_msg_size=0, autoping=True)
            await client_ws.prepare(request)
            ws_parts = urlsplit(upstream_url)
            ws_scheme = "wss" if ws_parts.scheme == "https" else "ws"
            ws_url = urlunsplit((ws_scheme, ws_parts.netloc, ws_parts.path, ws_parts.query, ""))
            async with ClientSession() as session:
                async with session.ws_connect(ws_url, headers=headers, max_msg_size=0) as upstream_ws:
                    async def client_to_upstream() -> None:
                        async for message in client_ws:
                            if message.type == WSMsgType.TEXT:
                                await upstream_ws.send_str(message.data)
                            elif message.type == WSMsgType.BINARY:
                                await upstream_ws.send_bytes(message.data)

                    async def upstream_to_client() -> None:
                        async for message in upstream_ws:
                            if message.type == WSMsgType.TEXT:
                                await client_ws.send_str(message.data)
                            elif message.type == WSMsgType.BINARY:
                                await client_ws.send_bytes(message.data)

                    tasks = {
                        asyncio.create_task(client_to_upstream()),
                        asyncio.create_task(upstream_to_client()),
                    }
                    _done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            return client_ws

        async with ClientSession() as session:
            async with session.get(upstream_url, headers=headers) as response:
                body = await response.read()
                content_type = response.headers.get("Content-Type", "application/json")
                if "json" in content_type:
                    parsed = json.loads(body.decode("utf-8"))
                    local_base = f"ws://{request.host}/{nonce}"
                    safe = rewrite_discovery(parsed, local_base=local_base, upstream_path=upstream_path)
                    return web.json_response(safe, status=response.status)
                return web.Response(body=body, status=response.status, content_type=content_type.split(";")[0])

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", gateway)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", bind_port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    sockets = getattr(site, "_server").sockets
    port = int(sockets[0].getsockname()[1])
    return runner, f"ws://127.0.0.1:{port}/{nonce}"


async def run_command(args: list[str], *, timeout: float) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Unbrowse command timed out") from None
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        detail = redact_text(stderr.decode("utf-8", "replace"))[:400].strip()
        raise RuntimeError(
            f"Unbrowse command failed ({process.returncode})"
            + (f": {detail}" if detail else "")
        )
    lines = [line for line in stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {"ok": True}


async def start_unbrowse_server(unbrowse_bin: str) -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        unbrowse_bin,
        "breath",
        "serve",
        "--json",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    for _ in range(40):
        if process.returncode is not None:
            raise RuntimeError("Unbrowse compatibility server failed to start")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", 6969)
            writer.close()
            await writer.wait_closed()
            return process
        except OSError:
            await asyncio.sleep(0.1)
    process.terminate()
    await process.wait()
    raise RuntimeError("Unbrowse compatibility server did not become ready")


async def store_vault_value(unbrowse_bin: str, pointer: str, value: str) -> None:
    process = await asyncio.create_subprocess_exec(
        unbrowse_bin,
        "build",
        "value-source",
        pointer,
        "--from-stdin",
        "--json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.communicate(value.encode("utf-8"))
    if process.returncode != 0:
        raise RuntimeError("failed to store generated value in the VCVM vault")


async def cdp_call(
    socket: Any,
    counter: list[int],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    counter[0] += 1
    request_id = counter[0]
    body: dict[str, Any] = {"id": request_id, "method": method, "params": params or {}}
    if session_id:
        body["sessionId"] = session_id
    await socket.send_json(body)
    while True:
        message = await socket.receive(timeout=20)
        if message.type != WSMsgType.TEXT:
            if message.type in {WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR}:
                raise RuntimeError("managed CDP socket closed")
            continue
        payload = json.loads(message.data)
        if payload.get("id") != request_id:
            continue
        if payload.get("error"):
            raise RuntimeError("managed CDP action failed")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}


async def execute_cdp_actions(
    local_ws: str,
    target: str,
    flow: dict[str, Any],
    *,
    unbrowse_bin: str,
) -> dict[str, Any]:
    email_path = Path("/run/user/1000/cbm-browser-use-email")
    email = email_path.read_text(encoding="utf-8").strip()
    if not email or "@" not in email:
        raise RuntimeError("VCVM mailbox identity is unavailable")
    generated_password = secrets.token_urlsafe(36)
    await store_vault_value(
        unbrowse_bin,
        "keychain://com.cloakbrowser/browser-use-password-vcvm",
        generated_password,
    )

    counter = [0]
    async with ClientSession() as session:
        async with session.ws_connect(local_ws, max_msg_size=0) as socket:
            targets = await cdp_call(socket, counter, "Target.getTargets")
            target_infos = targets.get("targetInfos") or []
            target_id = ""
            target_origin = normalized_origin(target)
            for item in target_infos:
                if not isinstance(item, dict) or item.get("type") != "page":
                    continue
                try:
                    if normalized_origin(str(item.get("url") or "")) == target_origin:
                        target_id = str(item.get("targetId") or "")
                        break
                except ValueError:
                    continue
            if not target_id:
                raise RuntimeError("managed signup page target was not found")
            attached = await cdp_call(
                socket,
                counter,
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            page_session = str(attached.get("sessionId") or "")
            if not page_session:
                raise RuntimeError("managed page session was not created")

            if flow.get("reset_origin_storage"):
                await cdp_call(
                    socket,
                    counter,
                    "Storage.clearDataForOrigin",
                    {"origin": target_origin, "storageTypes": "all"},
                    session_id=page_session,
                )
                await cdp_call(
                    socket,
                    counter,
                    "Page.navigate",
                    {"url": target},
                    session_id=page_session,
                )
                await asyncio.sleep(3)

            for action_index, action in enumerate(flow["actions"], start=1):
                op = str(action["op"])
                if op == "fill":
                    source = str(action["source"])
                    value = email if source == "vcvm_email" else generated_password
                    selector = str(action["selector"])
                    index = max(0, int(action.get("index") or 0))
                    expression = (
                        "(()=>{const e=Array.from(document.querySelectorAll(" + json.dumps(selector) + "))["
                        + str(index)
                        + "];if(!e)return false;e.focus();e.select();return true})()"
                    )
                    found = False
                    for _ in range(60):
                        result = await cdp_call(
                            socket,
                            counter,
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                            session_id=page_session,
                        )
                        found = bool(((result.get("result") or {}).get("value")))
                        if found:
                            break
                        await asyncio.sleep(0.25)
                    if not found:
                        path_result = await cdp_call(
                            socket,
                            counter,
                            "Runtime.evaluate",
                            {
                                "expression": "location.pathname",
                                "returnByValue": True,
                            },
                            session_id=page_session,
                        )
                        current_path = str(
                            ((path_result.get("result") or {}).get("value")) or ""
                        )[:120]
                        raise RuntimeError(
                            f"approved input was not found at action {action_index} "
                            f"path={current_path}"
                        )
                    for character in value:
                        await cdp_call(
                            socket,
                            counter,
                            "Input.dispatchKeyEvent",
                            {"type": "char", "text": character},
                            session_id=page_session,
                        )
                elif op == "click":
                    selector = str(action["selector"])
                    expression = (
                        "(()=>{const e=document.querySelector(" + json.dumps(selector)
                        + ");if(!e)return false;e.click();return true})()"
                    )
                    found = False
                    for _ in range(60):
                        result = await cdp_call(
                            socket,
                            counter,
                            "Runtime.evaluate",
                            {"expression": expression, "returnByValue": True},
                            session_id=page_session,
                        )
                        found = bool(((result.get("result") or {}).get("value")))
                        if found:
                            break
                        await asyncio.sleep(0.25)
                    if not found:
                        raise RuntimeError(
                            f"approved action was not found at action {action_index}"
                        )
                elif op == "submit":
                    expression = (
                        "(()=>{const f=document.querySelector('form');if(!f)return false;"
                        "window.__cbmSubmitEvents=0;window.__cbmSubmitStarted=performance.now();"
                        "try{window.__cbmFetchResults=JSON.parse(sessionStorage.getItem('__cbmAuthResults')||'[]')}catch{window.__cbmFetchResults=[]};"
                        "if(!window.__cbmFetchWrapped){window.__cbmFetchWrapped=true;const of=window.fetch.bind(window);"
                        "window.fetch=async(...a)=>{const r=await of(...a);try{const raw=typeof a[0]==='string'?a[0]:a[0]?.url||'';"
                        "const u=new URL(raw,location.href);if(u.pathname.includes('/auth/password/sign-')){const x=await r.clone().text();"
                        "const l=x.toLowerCase();const sdkError=/\\\"status\\\"\\s*:\\s*\\\"error\\\"/.test(l);"
                        "const cm=x.match(/\\\"(?:errorCode|error_code)\\\"\\s*:\\s*\\\"([A-Z0-9_]{1,80})\\\"/i);const ec=(cm?.[1]||'').toUpperCase();"
                        "const errorCategory=/USER_EMAIL_ALREADY_EXISTS|CONTACT_CHANNEL_ALREADY_USED/.test(ec)?'email_exists':ec==='EMAIL_PASSWORD_MISMATCH'?'mismatch':ec==='SIGN_UP_NOT_ENABLED'?'signup_disabled':ec==='PASSWORD_AUTHENTICATION_NOT_ENABLED'?'password_auth_disabled':/VERIFY/.test(ec)?'verification':/PASSWORD|WEAK/.test(ec)?'password_policy':/RATE|TOO_MANY/.test(ec)?'rate_limit':ec?'other':'none';"
                        "const red={status:r.status,path:u.host+u.pathname,"
                        "duplicate:/user_email_already_exists|contact_channel_already_used_for_auth_by_someone_else/.test(l),temporary:/temporary|disposable/.test(l),"
                        "password:/password|weak|complex/.test(l),rate:/rate|too many/.test(l),"
                        "verification:/verify|verification|email_not_verified/.test(l),"
                        "authMaterial:/access[_-]?token|refresh[_-]?token|session[_-]?token|\\\"user\\\"/.test(l),"
                        "emailPasswordMismatch:/email_password_mismatch/.test(l),"
                        "signupDisabled:/sign_up_not_enabled/.test(l),"
                        "passwordAuthDisabled:/password_authentication_not_enabled/.test(l),"
                        "authRejected:sdkError||/email_password_mismatch|sign_up_not_enabled|password_authentication_not_enabled|user_email_already_exists|contact_channel_already_used_for_auth_by_someone_else/.test(l),"
                        "errorCategory,genericError:!r.ok||sdkError};"
                        "window.__cbmFetchResults.push(red);sessionStorage.setItem('__cbmAuthResults',JSON.stringify(window.__cbmFetchResults.slice(-5)));}}catch{}return r};}"
                        "f.addEventListener('submit',()=>{window.__cbmSubmitEvents++},{capture:true,once:false});"
                        "if(f.requestSubmit){f.requestSubmit();}else{f.submit();}return true})()"
                    )
                    await cdp_call(
                        socket,
                        counter,
                        "Runtime.evaluate",
                        {"expression": expression, "returnByValue": True},
                        session_id=page_session,
                    )
                elif op == "wait":
                    await asyncio.sleep(int(action.get("milliseconds") or 0) / 1000)
                elif op == "navigate":
                    next_url = str(action.get("url") or "")
                    if normalized_origin(next_url) != normalized_origin(target):
                        raise RuntimeError("navigation origin is not allowed")
                    await cdp_call(
                        socket,
                        counter,
                        "Page.navigate",
                        {"url": next_url},
                        session_id=page_session,
                    )
                    await asyncio.sleep(3)
                elif op == "snapshot":
                    expression = (
                        "(()=>{const t=(document.body?.innerText||'').toLowerCase();"
                        "let saved=[];try{saved=JSON.parse(sessionStorage.getItem('__cbmAuthResults')||'[]')}catch{}"
                        "const fr=Array.isArray(window.__cbmFetchResults)?window.__cbmFetchResults.slice(-5):(Array.isArray(saved)?saved.slice(-5):[]);"
                        "const storageKeys=Object.keys(localStorage).concat(Object.keys(sessionStorage)).filter(k=>!k.startsWith('__cbm'));"
                        "const authResultProven=fr.some(x=>String(x.path||'').endsWith('/auth/password/sign-in')&&x.status>=200&&x.status<400&&x.authMaterial&&!x.authRejected&&!x.genericError);"
                        "const routeSignedIn=!/^\\/(sign-in|signin|signup|sign-up|auth)(\\/|$)/.test(location.pathname)&&document.querySelectorAll('input[type=password]').length===0;return {"
                        "title:document.title,path:location.pathname,formCount:document.forms.length,"
                        "emailField:!!document.querySelector('input[type=email]'),"
                        "passwordFields:document.querySelectorAll('input[type=password]').length,"
                        "captcha:/captcha|verify you are human|cloudflare/.test(t),"
                        "verification:/verify|verification|check your email/.test(t),"
                        "invalidEmail:/invalid email|valid email address/.test(t),"
                        "temporaryEmail:/temporary email|disposable email|email.*not allowed/.test(t),"
                        "passwordPolicy:/password.{0,40}(must|least|require|weak)/.test(t),"
                        "alreadyExists:/already exists|already registered|sign in instead/.test(t),"
                        "errorElements:document.querySelectorAll('[role=alert],.cl-formFieldErrorText,[data-error]').length,"
                        "formValid:document.forms[0]?.checkValidity?.()??false,"
                        "submitCount:document.querySelectorAll('button[type=submit]').length,"
                        "submitDisabled:!!document.querySelector('button[type=submit]')?.disabled,"
                        "submitEvents:Number(window.__cbmSubmitEvents||0),"
                        "fetchResults:fr,"
                        "newResources:performance.getEntriesByType('resource').filter(e=>e.startTime>Number(window.__cbmSubmitStarted||Infinity)).slice(-10).map(e=>{try{const u=new URL(e.name);return u.host+u.pathname}catch{return 'invalid'}}),"
                        "authStorage:storageKeys.some(k=>/stack|auth|token|session/i.test(k)),"
                        "authResultProven,"
                        "accountMarker:/log out|logout|dashboard|workspace|sessions|browser use cloud/.test(t),"
                        "authenticated:authResultProven||(routeSignedIn&&/log out|logout|dashboard|workspace|sessions|browser use cloud/.test(t)&&storageKeys.some(k=>/stack|auth|token|session/i.test(k))),"
                        "emailLength:(document.querySelector('input[type=email]')?.value||'').length,"
                        "passwordLengths:Array.from(document.querySelectorAll('input[type=password]')).map(e=>e.value.length)}})()"
                    )
                    result = await cdp_call(
                        socket,
                        counter,
                        "Runtime.evaluate",
                        {"expression": expression, "returnByValue": True},
                        session_id=page_session,
                    )
                    state = (result.get("result") or {}).get("value")
                    if isinstance(state, dict):
                        return state
    return {"title": "", "path": "", "formCount": -1}


async def heartbeat_loop(client: HarnessClient, run_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        body = await asyncio.to_thread(client.heartbeat, run_id)
        if body.get("cancel_requested"):
            raise RuntimeError("run cancelled")
        delay = max(1.0, min(float(body.get("heartbeat_interval_seconds") or 5), 15.0))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            continue


async def execute_flow(
    client: HarnessClient,
    claim: dict[str, Any],
    flow: dict[str, Any],
    *,
    unbrowse_bin: str,
) -> dict[str, Any]:
    run_id = str(claim["id"])
    target = select_target(claim, flow)
    validate_flow(flow, task=str(claim.get("task") or ""))
    capability = await asyncio.to_thread(client.issue_capability, run_id)
    upstream_http = urljoin(client.base_url.rstrip("/") + "/", str(capability.get("cdp_url") or ""))
    headers = {str(k): str(v) for k, v in dict(capability.get("headers") or {}).items()}
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(heartbeat_loop(client, run_id, stop))
    session_id = ""
    gateway_runner: web.AppRunner | None = None
    unbrowse_server: asyncio.subprocess.Process | None = None
    try:
        unbrowse_server = await start_unbrowse_server(unbrowse_bin)
        gateway_runner, local_ws = await start_cdp_gateway(
            upstream_http=upstream_http,
            headers=headers,
        )
        try:
            opened = await run_command(
                [unbrowse_bin, "breath", "go", target, "--ws", local_ws, "--timeout", "30000", "--json"],
                timeout=45,
            )
            session_id = str(opened.get("session_id") or opened.get("sessionId") or "")
            if not session_id:
                raise RuntimeError("Unbrowse did not return a session")
            await asyncio.to_thread(
                client.output,
                run_id,
                kind="status",
                summary="Unbrowse attached to the managed profile",
                payload={"status": "running", "progress": 10},
                idempotency_key=f"unbrowse:{run_id}:attached",
            )
            if flow.get("engine") == "cdp":
                state = await execute_cdp_actions(
                    local_ws,
                    target,
                    flow,
                    unbrowse_bin=unbrowse_bin,
                )
                await asyncio.to_thread(
                    client.output,
                    run_id,
                    kind="observation",
                    summary="Managed Browser Harness flow reached its post-action state",
                    payload={
                        "title": str(state.get("title") or "")[:200],
                        "note": (
                            f"path={str(state.get('path') or '')[:120]} "
                            f"forms={int(state.get('formCount') or 0)} "
                            f"verification={bool(state.get('verification'))} "
                            f"captcha={bool(state.get('captcha'))} "
                            f"invalid_email={bool(state.get('invalidEmail'))} "
                            f"temporary_email={bool(state.get('temporaryEmail'))} "
                            f"password_policy={bool(state.get('passwordPolicy'))} "
                            f"already_exists={bool(state.get('alreadyExists'))} "
                            f"errors={int(state.get('errorElements') or 0)} "
                            f"form_valid={bool(state.get('formValid'))} "
                            f"submit_count={int(state.get('submitCount') or 0)} "
                            f"submit_disabled={bool(state.get('submitDisabled'))} "
                            f"submit_events={int(state.get('submitEvents') or 0)} "
                            f"fetch_count={len(list(state.get('fetchResults') or []))} "
                            f"fetch_statuses={[int(item.get('status') or 0) for item in state.get('fetchResults') or [] if isinstance(item, dict)][:5]} "
                            f"failed_fetch_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('genericError'))} "
                            f"signup_fetch_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and str(item.get('path') or '').endswith('/auth/password/sign-up'))} "
                            f"signin_fetch_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and str(item.get('path') or '').endswith('/auth/password/sign-in'))} "
                            f"auth_rejected_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('authRejected'))} "
                            f"duplicate_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('duplicate'))} "
                            f"temporary_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('temporary'))} "
                            f"password_issue_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('password'))} "
                            f"rate_limit_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('rate'))} "
                            f"email_password_mismatch_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('emailPasswordMismatch'))} "
                            f"signup_disabled_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('signupDisabled'))} "
                            f"password_auth_disabled_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('passwordAuthDisabled'))} "
                            f"error_categories={[str(item.get('errorCategory') or 'none') for item in state.get('fetchResults') or [] if isinstance(item, dict)][:5]} "
                            f"verification_fetch_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('verification'))} "
                            f"auth_material_count={sum(1 for item in state.get('fetchResults') or [] if isinstance(item, dict) and item.get('authMaterial'))} "
                            f"resource_count={len(list(state.get('newResources') or []))} "
                            f"auth_storage={bool(state.get('authStorage'))} "
                            f"auth_result_proven={bool(state.get('authResultProven'))} "
                            f"account_marker={bool(state.get('accountMarker'))} "
                            f"authenticated={bool(state.get('authenticated'))} "
                            f"email_length={int(state.get('emailLength') or 0)} "
                            f"password_lengths={list(state.get('passwordLengths') or [])[:2]}"
                        ),
                    },
                    idempotency_key=f"unbrowse:{run_id}:cdp-state",
                )
                validate_cdp_state(state, flow)
                return state
            for index, action in enumerate(flow["actions"], start=1):
                op = str(action["op"])
                if op == "fill":
                    command = [unbrowse_bin, "breath", "fill", str(action["selector"]), str(action["pointer"]), "--session", session_id, "--json"]
                    await run_command(command, timeout=30)
                elif op == "click":
                    command = [unbrowse_bin, "breath", "click", str(action["selector"]), "--session", session_id, "--json"]
                    await run_command(command, timeout=30)
                elif op == "submit":
                    command = [unbrowse_bin, "breath", "submit"]
                    if action.get("selector"):
                        command.append(str(action["selector"]))
                    command.extend(["--session", session_id, "--wait", "--timeout", "30000", "--json"])
                    await run_command(command, timeout=40)
                elif op == "wait":
                    await asyncio.sleep(int(action.get("milliseconds") or 0) / 1000)
                elif op == "snapshot":
                    snap = await run_command([unbrowse_bin, "eval", "snap", "--session", session_id, "--json"], timeout=30)
                    title = str(snap.get("title") or "")[:200]
                    await asyncio.to_thread(
                        client.output,
                        run_id,
                        kind="observation",
                        summary="Unbrowse captured a post-action snapshot",
                        payload={"title": title, "note": "snapshot captured"},
                        idempotency_key=f"unbrowse:{run_id}:snapshot:{index}",
                    )
            return {"session_id": session_id}
        finally:
            await gateway_runner.cleanup()
            gateway_runner = None
    finally:
        stop.set()
        heartbeat.cancel()
        if session_id:
            try:
                await run_command([unbrowse_bin, "breath", "sync", "--session", session_id, "--json"], timeout=20)
            except Exception:
                pass
        if gateway_runner is not None:
            await gateway_runner.cleanup()
        if unbrowse_server is not None and unbrowse_server.returncode is None:
            unbrowse_server.terminate()
            try:
                await asyncio.wait_for(unbrowse_server.wait(), timeout=5)
            except asyncio.TimeoutError:
                unbrowse_server.kill()
                await unbrowse_server.wait()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def async_main(args: argparse.Namespace) -> int:
    flow = json.loads(Path(args.flow).read_text(encoding="utf-8"))
    if not isinstance(flow, dict):
        raise ValueError("flow must be a JSON object")
    client = HarnessClient(args.manager_url, token_file=args.token_file)
    claim = await asyncio.to_thread(client.claim)
    if not claim:
        return 3
    run_id = str(claim.get("id") or "")
    if claim.get("harness") != HARNESS or not run_id:
        return 4
    completed = False
    try:
        await execute_flow(client, claim, flow, unbrowse_bin=args.unbrowse_bin)
        await asyncio.to_thread(
            client.output,
            run_id,
            kind="summary",
            summary="Secure Unbrowse flow completed",
            payload={"status": "succeeded", "text": "Secure flow completed without persisting cleartext secrets."},
            idempotency_key=f"unbrowse:{run_id}:summary",
        )
        await asyncio.to_thread(client.complete, run_id)
        completed = True
        return 0
    except Exception as exc:
        await asyncio.to_thread(client.fail, run_id, error_code="internal_error", message=redact_text(str(exc)))
        return 1
    finally:
        if not completed:
            try:
                await asyncio.to_thread(client.revoke_capability, run_id)
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--flow", required=True)
    parser.add_argument("--unbrowse-bin", default="unbrowse")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
