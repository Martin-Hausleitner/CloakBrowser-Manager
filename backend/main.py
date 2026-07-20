"""CloakBrowser Manager — FastAPI application.

Serves the React dashboard (static files) and provides a REST API
for browser profile management with live VNC viewing.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import struct
import shutil
import time
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import starlette.requests
from starlette.types import ASGIApp, Receive, Scope, Send

if __package__:
    from . import access_control as access
    from . import database as db
    from .browser_manager import BrowserManager
    from .models import (
        ClipboardRequest,
        AccessAgentCreate,
        AccessAgentCreatedResponse,
        AccessAgentResponse,
        AccessAgentUpdate,
        AccessIdentityResponse,
        AccessUserCreate,
        AccessUserResponse,
        AccessUserUpdate,
        LaunchResponse,
        LoginRequest,
        ProfileCreate,
        ProfileResponse,
        ProfileStatusResponse,
        ProfileUpdate,
        StatusResponse,
        TagResponse,
    )
else:  # Support `uvicorn main:app` from the backend directory.
    import access_control as access
    import database as db
    from browser_manager import BrowserManager
    from models import (
        ClipboardRequest,
        AccessAgentCreate,
        AccessAgentCreatedResponse,
        AccessAgentResponse,
        AccessAgentUpdate,
        AccessIdentityResponse,
        AccessUserCreate,
        AccessUserResponse,
        AccessUserUpdate,
        LaunchResponse,
        LoginRequest,
        ProfileCreate,
        ProfileResponse,
        ProfileStatusResponse,
        ProfileUpdate,
        StatusResponse,
        TagResponse,
    )

logger = logging.getLogger("cloakbrowser.manager")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Optional authentication via AUTH_TOKEN env var. If not set, all routes are
# open for local development. ``ACCESS_CONTROL_ENABLED=1`` adds named users and
# scoped Paperclip-agent credentials, but intentionally requires AUTH_TOKEN as
# a bootstrap signing secret to avoid an accidental locked-open deployment.
AUTH_TOKEN: str | None = os.environ.get("AUTH_TOKEN") or None
ACCESS_CONTROL_ENABLED = bool(AUTH_TOKEN) and access.access_control_enabled(
    os.environ.get("ACCESS_CONTROL_ENABLED")
)
if os.environ.get("ACCESS_CONTROL_ENABLED") and not AUTH_TOKEN:
    logger.warning("ACCESS_CONTROL_ENABLED ignored because AUTH_TOKEN is not configured")

# Paths that bypass authentication even when AUTH_TOKEN is set
_AUTH_EXEMPT = frozenset({"/api/auth/status", "/api/auth/login", "/api/status"})
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_BACKOFF_SECONDS = 60.0
_LOGIN_FAILURE_TTL_SECONDS = 10 * 60.0
_LOGIN_FAILURE_MAX_KEYS = 1024
_login_failures: dict[tuple[str, str], tuple[int, float, float]] = {}


def _check_auth(scope: Scope) -> bool:
    """Check if the request has a valid auth token (header or cookie)."""
    # Check Authorization: Bearer <token> header
    for key, val in scope.get("headers", []):
        if key == b"authorization":
            auth_value = val.decode()
            if auth_value.startswith("Bearer "):
                token = auth_value[7:]
                if token and hmac.compare_digest(token, AUTH_TOKEN):
                    return True
            break

    # Check auth_token cookie
    for key, val in scope.get("headers", []):
        if key == b"cookie":
            cookies = SimpleCookie()
            cookies.load(val.decode())
            if "auth_token" in cookies:
                cookie_val = cookies["auth_token"].value
                if cookie_val and hmac.compare_digest(cookie_val, AUTH_TOKEN):
                    return True
            break

    return False


def _access_identity(scope: Scope) -> access.AccessIdentity | None:
    return access.identity_from_scope(scope, AUTH_TOKEN, ACCESS_CONTROL_ENABLED)


def _client_host(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _login_throttle_key(username: str, request: Request) -> tuple[str, str]:
    return (username.casefold(), _client_host(request))


def _cleanup_login_failures(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        key for key, (_count, blocked_until, last_seen) in _login_failures.items()
        if (blocked_until > 0 and blocked_until <= now)
        or last_seen + _LOGIN_FAILURE_TTL_SECONDS <= now
    ]
    for key in expired:
        _login_failures.pop(key, None)

    overflow = len(_login_failures) - _LOGIN_FAILURE_MAX_KEYS
    if overflow <= 0:
        return

    oldest = sorted(
        _login_failures.items(),
        key=lambda item: (item[1][2], item[0][0], item[0][1]),
    )
    for key, _value in oldest[:overflow]:
        _login_failures.pop(key, None)


def _login_backoff_remaining(key: tuple[str, str], now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    _cleanup_login_failures(now)
    count, blocked_until, _last_seen = _login_failures.get(key, (0, 0.0, 0.0))
    if count < _LOGIN_FAILURE_LIMIT:
        return 0.0
    remaining = blocked_until - now
    if remaining <= 0:
        _login_failures.pop(key, None)
        return 0.0
    return remaining


def _record_login_failure(key: tuple[str, str]) -> None:
    now = time.monotonic()
    _cleanup_login_failures(now)
    count, blocked_until, _last_seen = _login_failures.get(key, (0, 0.0, 0.0))
    if blocked_until <= now:
        blocked_until = 0.0
    count += 1
    if count >= _LOGIN_FAILURE_LIMIT:
        blocked_until = now + _LOGIN_BACKOFF_SECONDS
    _login_failures[key] = (count, blocked_until, now)
    _cleanup_login_failures(now)


def _reset_login_failures(key: tuple[str, str]) -> None:
    _login_failures.pop(key, None)


def _is_https(request: Request) -> bool:
    """Check if the original client connection was HTTPS (via reverse proxy header)."""
    proto = request.headers.get("x-forwarded-proto", "")
    return "https" in proto


async def _check_websocket_origin(websocket: WebSocket) -> bool:
    """Reject cross-origin WebSocket connections (CSWSH protection).

    Browsers always send an Origin header on WebSocket upgrades.
    Non-browser clients (Playwright, curl) typically don't — those are allowed.
    If Origin is present, its host must match the request Host header.
    """
    origin = None
    host = None
    for key, val in websocket.scope.get("headers", []):
        if key == b"origin":
            origin = val.decode("latin-1")
        elif key == b"host":
            host = val.decode("latin-1")

    # No Origin header → non-browser client (Playwright, Puppeteer) → allow
    if not origin:
        return True

    # Parse origin to extract host:port
    try:
        parsed = urlparse(origin)
        origin_host = parsed.hostname or ""
        origin_port = parsed.port
    except ValueError:
        logger.warning("WebSocket origin malformed: %s", origin)
        await websocket.close(code=4403, reason="Origin not allowed")
        return False
    # Build origin netloc (host:port or just host if default port)
    if origin_port and origin_port not in (80, 443):
        origin_netloc = f"{origin_host}:{origin_port}"
    else:
        origin_netloc = origin_host

    if not host:
        return True  # no Host header to compare against

    # Strip default port from Host too (some proxies send "example.com:443")
    host_normalized = host
    if host.endswith(":80") or host.endswith(":443"):
        host_normalized = host.rsplit(":", 1)[0]

    if origin_netloc == host_normalized:
        return True

    logger.warning("WebSocket origin mismatch: origin=%s host=%s", origin, host)
    await websocket.close(code=4403, reason="Origin not allowed")
    return False


class AuthMiddleware:
    """Raw ASGI middleware for optional token auth.

    Uses raw ASGI instead of BaseHTTPMiddleware because the latter
    breaks WebSocket routes (wraps request body, preventing WS upgrade).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Pass through non-HTTP/WS scope (e.g. lifespan).
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # Scoped policy mode recognizes bootstrap, signed human sessions, and
        # individual agent bearer keys. The resolved identity is placed on the
        # raw ASGI scope so REST and WebSocket handlers use one decision.
        if ACCESS_CONTROL_ENABLED:
            identity = access.resolve_identity(scope, AUTH_TOKEN)
            if identity:
                scope.setdefault("state", {})["access_identity"] = identity

            # Auth bootstrap/static routes intentionally remain reachable.
            if path in _AUTH_EXEMPT or not path.startswith("/api/"):
                await self.app(scope, receive, send)
                return

            if identity:
                await self.app(scope, receive, send)
                return

            await _reject_unauthenticated(scope, receive, send)
            return

        # Legacy mode: optional single owner token, unchanged for existing
        # deployments that have not opted into access control.
        if not AUTH_TOKEN or path in _AUTH_EXEMPT or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if _check_auth(scope):
            await self.app(scope, receive, send)
            return

        await _reject_unauthenticated(scope, receive, send)


async def _reject_unauthenticated(scope: Scope, receive: Receive, send: Send) -> None:
    """Reject both HTTP and WebSocket callers without bypassing ASGI rules."""
    if scope["type"] == "websocket":
        # ASGI requires receiving websocket.connect before sending close.
        await receive()
        await send({"type": "websocket.close", "code": 4401, "reason": "Unauthorized"})
    else:
        response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)


# Singleton browser manager
browser_mgr = BrowserManager()

# Frontend build directory (React production build)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# RFB server message translator — KasmVNC BinaryClipboard → standard RFB
# ---------------------------------------------------------------------------


def _parse_kasmvnc_clipboard(data: bytes) -> str | None:
    """Extract text/plain from KasmVNC BinaryClipboard (type 180).

    Format: type(1) + action(1) + flags(4) + entries...
    Each entry: mime_len(u8) + mime(N) + data_len(u32 BE) + data(M)
    """
    if len(data) < 7:
        return None
    offset = 6  # skip type(1) + action(1) + flags(4)
    while offset < len(data):
        if offset + 1 > len(data):
            break
        mime_len = data[offset]
        offset += 1
        if offset + mime_len > len(data):
            break
        mime_type = data[offset:offset + mime_len]
        offset += mime_len
        if offset + 4 > len(data):
            break
        data_len = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if mime_type == b"text/plain":
            end = min(offset + data_len, len(data))
            return data[offset:end].decode("utf-8", errors="replace")
        offset += data_len
    return None


def _build_server_cut_text(text: str) -> bytes:
    """Build standard RFB ServerCutText (type 3) message.

    RFB spec mandates Latin-1 encoding for ServerCutText.
    Characters outside Latin-1 (CJK, emoji, etc.) are replaced with '?'.
    """
    text_bytes = text.encode("latin-1", errors="replace")
    return struct.pack(">BxxxI", 3, len(text_bytes)) + text_bytes


# ---------------------------------------------------------------------------
# RFB client message filter — strip extension types KasmVNC doesn't support
# ---------------------------------------------------------------------------
# noVNC v1.4 batches multiple RFB messages into one WebSocket frame.
# KasmVNC 1.3.3 crashes on unsupported types (150, 248, etc.).
# We parse message boundaries using known sizes and keep only standard types.

# Client→server message sizes (fixed, except 2 and 6 which encode length)
_RFB_MSG_SIZE: dict[int, int | None] = {
    0: 20,    # SetPixelFormat
    2: None,  # SetEncodings — 4 + numEncodings*4 (rewritten to strip bad pseudo-encodings)
    3: 10,    # FramebufferUpdateRequest
    4: 8,     # KeyEvent
    5: 6,     # PointerEvent
    6: None,  # ClientCutText — 8 + length
}

# Extension types that noVNC sends — known sizes so we can skip past them
# instead of breaking and dropping all trailing data in the frame.
_RFB_EXTENSION_SIZE: dict[int, int] = {
    150: 10,  # EnableContinuousUpdates (1+1+2+2+2+2)
    248: 10,  # QEMU-like key event (observed from noVNC 1.4.0)
    252: 4,   # xvp (1+1+1+1)
    255: 4,   # QEMU audio control (1+1+2) — noVNC QEMUExtendedKeyEvent is actually 12
}

# Whitelist of encodings safe to send to KasmVNC.
# Instead of trying to blocklist problematic pseudo-encodings (error-prone —
# we had wrong numbers), we ONLY keep known-good encodings.
# Anything not on this list is stripped from SetEncodings.
_ALLOWED_ENCODINGS: set[int] = {
    # Framebuffer encodings (standard RFB)
    0,    # Raw
    1,    # CopyRect
    2,    # RRE
    5,    # Hextile
    7,    # Tight
    16,   # ZRLE
    # Safe pseudo-encodings
    -239,  # Cursor (0xFFFFFF11) — cursor shape
    -224,  # LastRect (0xFFFFFF20) — performance optimization
    # Tight quality/compress levels (these are just hints)
    *range(-32, -22),   # quality levels 0-9
    *range(-256, -246),  # compress levels 0-9
}


def _rfb_msg_length(data: bytes, offset: int) -> int | None:
    """Return total length of the RFB message at offset, or None if unrecognized."""
    if offset >= len(data):
        return None
    msg_type = data[offset]
    fixed = _RFB_MSG_SIZE.get(msg_type)
    if fixed is not None:
        return fixed
    remaining = len(data) - offset
    if msg_type == 2 and remaining >= 4:  # SetEncodings
        num_enc = struct.unpack_from(">H", data, offset + 2)[0]
        return 4 + num_enc * 4
    if msg_type == 6 and remaining >= 8:  # ClientCutText
        length = struct.unpack_from(">I", data, offset + 4)[0]
        return 8 + length
    # Known extension types — skip past them instead of giving up
    ext_size = _RFB_EXTENSION_SIZE.get(msg_type)
    if ext_size is not None:
        return ext_size
    return None  # truly unknown type


def _rewrite_set_encodings(data: bytes, offset: int, msg_len: int) -> bytes:
    """Keep only whitelisted encodings in a SetEncodings message."""
    _log = logging.getLogger("cloakbrowser.manager")
    num_enc = struct.unpack_from(">H", data, offset + 2)[0]
    kept = []
    stripped = []
    for i in range(num_enc):
        enc = struct.unpack_from(">i", data, offset + 4 + i * 4)[0]  # signed
        if enc in _ALLOWED_ENCODINGS:
            kept.append(enc)
        else:
            stripped.append(enc)
    if not stripped:
        return data[offset:offset + msg_len]
    _log.info("RFB filter: SetEncodings keeping %d: %s, stripped %d: %s", len(kept), kept, len(stripped), stripped)
    result = struct.pack(">BxH", 2, len(kept))
    for enc in kept:
        result += struct.pack(">i", enc)
    return result


def _rewrite_pointer_event(data: bytes, offset: int) -> bytes:
    """Convert standard 6-byte PointerEvent to KasmVNC's 11-byte format.

    Standard RFB:  [5:u8][mask:u8][x:u16][y:u16]          = 6 bytes
    KasmVNC:       [5:u8][mask:u16][x:u16][y:u16][sx:s16][sy:s16] = 11 bytes
    """
    mask = data[offset + 1]
    x = struct.unpack_from(">H", data, offset + 2)[0]
    y = struct.unpack_from(">H", data, offset + 4)[0]
    # Expand mask from u8 to u16.  Scroll deltas (sx, sy) are zero because
    # noVNC encodes scroll as button-mask bits (3=up, 4=down, 5=left, 6=right)
    # which pass through in the mask.  KasmVNC accepts mask-bit scroll on its
    # extended 11-byte format, so explicit deltas are unnecessary.
    return struct.pack(">BHHHhh", 5, mask, x, y, 0, 0)


def _filter_rfb_client_messages(data: bytes) -> bytes:
    """Parse concatenated RFB messages, keep only standard types (0-6).

    Rewrites PointerEvents from 6-byte standard to 11-byte KasmVNC format
    and strips unsupported pseudo-encodings from SetEncodings.
    """
    _log = logging.getLogger("cloakbrowser.manager")
    result = bytearray()
    offset = 0
    msg_idx = 0
    while offset < len(data):
        msg_type = data[offset]
        msg_len = _rfb_msg_length(data, offset)
        if msg_len is None:
            _log.info("RFB filter: DROPPING unknown type=%d at offset=%d/%d, skipping %d trailing bytes, hex=%s",
                       msg_type, offset, len(data), len(data) - offset, data[offset:offset+20].hex())
            break
        if offset + msg_len > len(data):
            # Incomplete message — DO NOT forward partial data, it desynchronizes
            # the RFB stream (KasmVNC buffers partial reads across frames).
            _log.warning("RFB filter: DROPPING incomplete type=%d need=%d have=%d — would desync stream",
                         msg_type, msg_len, len(data) - offset)
            break
        msg_idx += 1
        if msg_type in _RFB_MSG_SIZE:
            # Standard RFB type — keep (with rewrites for KasmVNC compatibility)
            _log.debug("RFB filter: KEEP type=%d len=%d at offset=%d (msg #%d in frame)", msg_type, msg_len, offset, msg_idx)
            if msg_type == 2:  # SetEncodings — whitelist safe encodings
                result.extend(_rewrite_set_encodings(data, offset, msg_len))
            elif msg_type == 5:  # PointerEvent — expand to KasmVNC's 11-byte format
                result.extend(_rewrite_pointer_event(data, offset))
            else:
                result.extend(data[offset:offset + msg_len])
        else:
            # Extension type (150, 248, etc.) — skip but continue parsing
            _log.debug("RFB filter: SKIP extension type=%d len=%d at offset=%d (msg #%d in frame)", msg_type, msg_len, offset, msg_idx)
        offset += msg_len
    if len(result) != len(data):
        _log.info("RFB filter: input=%d output=%d (delta %+d bytes)", len(data), len(result), len(result) - len(data))
    return bytes(result)


def _filter_rfb_viewer_messages(data: bytes) -> bytes:
    """Keep only display-negotiation RFB messages for a view-only session.

    A noVNC client must still send SetPixelFormat, SetEncodings and
    FramebufferUpdateRequest after the initial RFB handshake; otherwise it
    never receives a framebuffer.  Key events, pointer events and clipboard
    transfers are intentionally excluded.  This makes a ``view`` grant a real
    live viewer rather than a connection that appears successful but stays
    black.
    """
    result = bytearray()
    offset = 0
    while offset < len(data):
        msg_type = data[offset]
        msg_len = _rfb_msg_length(data, offset)
        if msg_len is None or offset + msg_len > len(data):
            break

        if msg_type == 0:  # SetPixelFormat
            result.extend(data[offset:offset + msg_len])
        elif msg_type == 2:  # SetEncodings, with the same safe allow-list
            result.extend(_rewrite_set_encodings(data, offset, msg_len))
        elif msg_type == 3:  # FramebufferUpdateRequest
            result.extend(data[offset:offset + msg_len])
        offset += msg_len
    return bytes(result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await browser_mgr.cleanup_stale()
    browser_mgr._auto_launch_task = asyncio.create_task(browser_mgr.auto_launch_all())
    logger.info("CloakBrowser Manager started")
    yield
    logger.info("Shutting down — stopping all browsers...")
    if browser_mgr._auto_launch_task and not browser_mgr._auto_launch_task.done():
        browser_mgr._auto_launch_task.cancel()
        await asyncio.gather(browser_mgr._auto_launch_task, return_exceptions=True)
    await browser_mgr.cleanup_all()


app = FastAPI(title="CloakBrowser Manager", lifespan=lifespan)
app.add_middleware(AuthMiddleware)


def _require_identity(scope: Scope) -> access.AccessIdentity:
    identity = _access_identity(scope)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity


def _require_admin(scope: Scope) -> access.AccessIdentity:
    identity = _require_identity(scope)
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return identity


def _require_profile_permission(
    scope: Scope, profile_id: str, permission: access.Permission
) -> tuple[dict[str, object], access.AccessIdentity]:
    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    identity = _require_identity(scope)
    if not access.can_access_profile(identity, profile, permission):
        # Keep a profile outside the caller's scope indistinguishable from a
        # missing one. This applies equally to direct REST and WebSocket URLs.
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile, identity


async def _require_websocket_profile_permission(
    websocket: WebSocket, profile_id: str, permission: access.Permission
) -> tuple[dict[str, object], access.AccessIdentity] | None:
    profile = db.get_profile(profile_id)
    identity = _access_identity(websocket.scope)
    if not profile or not identity or not access.can_access_profile(identity, profile, permission):
        await websocket.close(code=4404, reason="Profile not found")
        return None
    return profile, identity


def _profile_response(profile: dict[str, object], identity: access.AccessIdentity) -> ProfileResponse:
    """Build a minimally disclosed profile response for scoped callers."""
    response = dict(profile)
    status = browser_mgr.get_status(str(response["id"]))
    response["status"] = status["status"]
    response["vnc_ws_port"] = status["vnc_ws_port"]
    response["cdp_url"] = status["cdp_url"]
    response["tags"] = [TagResponse(**tag) for tag in response.get("tags", [])]

    if ACCESS_CONTROL_ENABLED and not identity.is_admin:
        # Viewer/operator cards do not reveal proxy credentials, fingerprint
        # implementation details, local data paths, notes, or CDP endpoints.
        response["proxy"] = None
        response["user_agent"] = None
        response["gpu_vendor"] = None
        response["gpu_renderer"] = None
        response["hardware_concurrency"] = None
        response["fingerprint_seed"] = 0
        response["launch_args"] = []
        response["notes"] = None
        response["user_data_dir"] = ""
        response["vnc_ws_port"] = None
        if not access.can_access_profile(identity, response, "automate"):
            response["cdp_url"] = None

    return ProfileResponse(**response)


def _access_user_response(user: dict[str, object]) -> AccessUserResponse:
    return AccessUserResponse(
        id=str(user["id"]),
        username=str(user["username"]),
        role=str(user["role"]),
        active=bool(user["active"]),
        created_at=str(user["created_at"]),
        grants=user.get("grants", []),
    )


def _access_agent_response(agent: dict[str, object]) -> AccessAgentResponse:
    return AccessAgentResponse(
        id=str(agent["id"]),
        display_name=str(agent["display_name"]),
        paperclip_agent_id=agent.get("paperclip_agent_id") or None,
        active=bool(agent["active"]),
        created_at=str(agent["created_at"]),
        grants=agent.get("grants", []),
    )


# ── Authentication ────────────────────────────────────────────────────────────


@app.get("/api/auth/status")
async def auth_status(request: starlette.requests.Request):
    """Check if auth is enabled and if the current request is authenticated.

    Exempt from auth middleware so the frontend can always call it.
    """
    if ACCESS_CONTROL_ENABLED:
        identity = _access_identity(request.scope)
    elif AUTH_TOKEN and _check_auth(request.scope):
        identity = access.bootstrap_identity()
    elif not AUTH_TOKEN:
        identity = _access_identity(request.scope)
    else:
        identity = None
    authenticated = (
        bool(identity)
        if ACCESS_CONTROL_ENABLED
        else _check_auth(request.scope)
        if AUTH_TOKEN
        else False
    )
    return {
        "auth_required": AUTH_TOKEN is not None,
        "access_control_enabled": ACCESS_CONTROL_ENABLED,
        "authenticated": authenticated,
        "identity": identity.public() if identity else None,
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest, request: Request, response: Response):
    if not AUTH_TOKEN:
        return {"ok": True}

    is_https = _is_https(request)
    if body.token:
        if not hmac.compare_digest(body.token, AUTH_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        response.delete_cookie(
            key="cbm_session", path="/", secure=is_https, samesite="strict",
        )
        response.set_cookie(
            key="auth_token",
            value=AUTH_TOKEN,
            httponly=True,
            samesite="strict",
            secure=is_https,
            path="/",
        )
        return {"ok": True, "identity": access.bootstrap_identity().public()}

    if not ACCESS_CONTROL_ENABLED or not body.username or not body.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    throttle_key = _login_throttle_key(body.username, request)
    retry_after = _login_backoff_remaining(throttle_key)
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )
    user = db.get_access_user_by_username(body.username)
    valid_user = (
        user
        and bool(user.get("active"))
        and access.verify_password(body.password, str(user["password_hash"]))
    )
    if not valid_user:
        _record_login_failure(throttle_key)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _reset_login_failures(throttle_key)
    session = access.create_session(str(user["id"]), AUTH_TOKEN)
    response.set_cookie(
        key="cbm_session",
        value=session,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=is_https,
        path="/",
    )
    response.delete_cookie(
        key="auth_token", path="/", secure=is_https, samesite="strict",
    )
    # The new cookie is attached to the response, so build the identity from
    # the verified database record rather than asking the old request cookie.
    return {
        "ok": True,
        "identity": access.AccessIdentity(
            kind="user",
            id=str(user["id"]),
            display_name=str(user["username"]),
            role=str(user["role"]),
            grants=tuple(user.get("grants", [])),
        ).public(),
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    is_https = _is_https(request)
    response.delete_cookie(
        key="auth_token", path="/", secure=is_https, samesite="strict",
    )
    response.delete_cookie(
        key="cbm_session", path="/", secure=is_https, samesite="strict",
    )
    return {"ok": True}


# ── Scoped identity administration ───────────────────────────────────────────


@app.get("/api/access/me", response_model=AccessIdentityResponse)
async def access_me(request: Request):
    return AccessIdentityResponse(**_require_identity(request.scope).public())


@app.get("/api/access/users", response_model=list[AccessUserResponse])
async def list_access_users(request: Request):
    _require_admin(request.scope)
    return [_access_user_response(user) for user in db.list_access_users()]


@app.post("/api/access/users", response_model=AccessUserResponse, status_code=201)
async def create_access_user(body: AccessUserCreate, request: Request):
    actor = _require_admin(request.scope)
    try:
        user = db.create_access_user(
            body.username,
            access.hash_password(body.password),
            body.role,
            [grant.model_dump() for grant in body.grants],
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Username already exists") from exc
        raise
    db.record_access_audit_event(actor.kind, actor.id, "access_user.create", "allowed")
    return _access_user_response(user)


@app.put("/api/access/users/{user_id}", response_model=AccessUserResponse)
async def update_access_user(user_id: str, body: AccessUserUpdate, request: Request):
    actor = _require_admin(request.scope)
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        data["password_hash"] = access.hash_password(data.pop("password"))
    if "grants" in data and data["grants"] is not None:
        data["grants"] = [grant.model_dump() for grant in data["grants"]]
    user = db.update_access_user(user_id, **data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.record_access_audit_event(actor.kind, actor.id, "access_user.update", "allowed")
    return _access_user_response(user)


@app.get("/api/access/agents", response_model=list[AccessAgentResponse])
async def list_access_agents(request: Request):
    _require_admin(request.scope)
    return [_access_agent_response(agent) for agent in db.list_access_agents()]


@app.post("/api/access/agents", response_model=AccessAgentCreatedResponse, status_code=201)
async def create_access_agent(body: AccessAgentCreate, request: Request):
    actor = _require_admin(request.scope)
    key = access.generate_agent_key()
    agent = db.create_access_agent(
        body.display_name,
        access.hash_agent_key(key),
        body.paperclip_agent_id,
        [grant.model_dump() for grant in body.grants],
    )
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.create", "allowed")
    return AccessAgentCreatedResponse(**_access_agent_response(agent).model_dump(), api_key=key)


@app.put("/api/access/agents/{agent_id}", response_model=AccessAgentResponse)
async def update_access_agent(agent_id: str, body: AccessAgentUpdate, request: Request):
    actor = _require_admin(request.scope)
    data = body.model_dump(exclude_unset=True)
    if "grants" in data and data["grants"] is not None:
        data["grants"] = [grant.model_dump() for grant in data["grants"]]
    agent = db.update_access_agent(agent_id, **data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.update", "allowed")
    return _access_agent_response(agent)


@app.post("/api/access/agents/{agent_id}/rotate-key", response_model=AccessAgentCreatedResponse)
async def rotate_access_agent_key(agent_id: str, request: Request):
    actor = _require_admin(request.scope)
    key = access.generate_agent_key()
    agent = db.update_access_agent(agent_id, key_hash=access.hash_agent_key(key))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    db.record_access_audit_event(actor.kind, actor.id, "access_agent.rotate_key", "allowed")
    return AccessAgentCreatedResponse(**_access_agent_response(agent).model_dump(), api_key=key)


@app.get("/api/access/sandboxes")
async def list_access_sandboxes(request: Request):
    _require_admin(request.scope)
    counts: dict[str, int] = {}
    for profile in db.list_profiles():
        sandbox_id = str(profile.get("sandbox_id") or "default")
        counts[sandbox_id] = counts.get(sandbox_id, 0) + 1
    return [{"sandbox_id": key, "profile_count": counts[key]} for key in sorted(counts)]


# ── Profile CRUD ──────────────────────────────────────────────────────────────


@app.get("/api/profiles", response_model=list[ProfileResponse])
async def list_profiles(request: Request):
    identity = _require_identity(request.scope)
    return [
        _profile_response(profile, identity)
        for profile in db.list_profiles()
        if access.can_access_profile(identity, profile, "view")
    ]


@app.post("/api/profiles", response_model=ProfileResponse, status_code=201)
async def create_profile(req: ProfileCreate, request: Request):
    identity = _require_admin(request.scope)
    data = req.model_dump()
    tags = data.pop("tags", None)
    if tags:
        data["tags"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in tags]
    else:
        data["tags"] = []
    profile = db.create_profile(**data)
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.create", "allowed", str(profile.get("sandbox_id") or "default"), profile["id"]
    )
    return _profile_response(profile, identity)


@app.get("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "view")
    return _profile_response(profile, identity)


@app.put("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile(profile_id: str, req: ProfileUpdate, request: Request):
    identity = _require_admin(request.scope)
    # Only pass fields that were explicitly set
    data = req.model_dump(exclude_unset=True)
    tags = data.pop("tags", None)
    if tags is not None:
        data["tags"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in tags]
    profile = db.update_profile(profile_id, **data)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.update", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return _profile_response(profile, identity)


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: str, request: Request):
    identity = _require_admin(request.scope)
    # Stop browser if running
    if profile_id in browser_mgr.running:
        await browser_mgr.stop(profile_id)

    profile = db.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    user_data_dir = Path(profile["user_data_dir"])

    # DB first — if this fails, filesystem is untouched
    db.delete_profile(profile_id)

    # Then clean up disk
    if user_data_dir.exists():
        shutil.rmtree(user_data_dir, ignore_errors=True)

    db.record_access_audit_event(
        identity.kind, identity.id, "profile.delete", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return {"ok": True}


# ── Launch / Stop ─────────────────────────────────────────────────────────────


@app.post("/api/profiles/{profile_id}/launch", response_model=LaunchResponse)
async def launch_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "operate")
    if profile_id in browser_mgr.running:
        raise HTTPException(status_code=409, detail="Profile is already running")

    try:
        running = await browser_mgr.launch(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to launch profile %s: %s", profile_id, exc)
        raise HTTPException(status_code=500, detail="Failed to launch browser")

    db.record_access_audit_event(
        identity.kind, identity.id, "profile.launch", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return LaunchResponse(
        profile_id=profile_id,
        status="running",
        vnc_ws_port=running.ws_port,
        display=f":{running.display}",
        cdp_url=(
            f"/api/profiles/{profile_id}/cdp"
            if access.can_access_profile(identity, profile, "automate")
            else None
        ),
    )


@app.post("/api/profiles/{profile_id}/stop")
async def stop_profile(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "operate")
    if profile_id not in browser_mgr.running:
        raise HTTPException(status_code=404, detail="Profile is not running")
    await browser_mgr.stop(profile_id)
    db.record_access_audit_event(
        identity.kind, identity.id, "profile.stop", "allowed", str(profile.get("sandbox_id") or "default"), profile_id
    )
    return {"ok": True}


@app.get("/api/profiles/{profile_id}/status", response_model=ProfileStatusResponse)
async def get_profile_status(profile_id: str, request: Request):
    profile, identity = _require_profile_permission(request.scope, profile_id, "view")
    status = browser_mgr.get_status(profile_id)
    if ACCESS_CONTROL_ENABLED and not identity.is_admin:
        # Scoped callers connect through stable, authenticated proxy routes;
        # exposing ephemeral local listener ports or an automation endpoint is
        # unnecessary and leaks more than a view grant needs.
        status["vnc_ws_port"] = None
        if not access.can_access_profile(identity, profile, "automate"):
            status["cdp_url"] = None
    return ProfileStatusResponse(**status)


# ── System Status ─────────────────────────────────────────────────────────────


@app.get("/api/status", response_model=StatusResponse)
async def get_system_status():
    from cloakbrowser.config import CHROMIUM_VERSION

    profiles = db.list_profiles()
    return StatusResponse(
        running_count=len(browser_mgr.running),
        binary_version=CHROMIUM_VERSION,
        profiles_total=len(profiles),
    )


# ── Clipboard Relay ──────────────────────────────────────────────────────────

_CLIPBOARD_MAX_READ = 1_048_576  # 1MB cap on GET response

# Track xclip processes per display so we can kill the old one before spawning new
_xclip_procs: dict[int, asyncio.subprocess.Process] = {}


@app.post("/api/profiles/{profile_id}/clipboard")
async def set_clipboard(profile_id: str, body: ClipboardRequest, request: Request):
    """Push text into the VNC session's X clipboard via xclip."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "interact")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    import os

    # Kill previous xclip for this display (it stays alive to serve paste)
    old = _xclip_procs.pop(running.display, None)
    if old and old.returncode is None:
        old.kill()
        await old.wait()

    env = {**os.environ, "DISPLAY": f":{running.display}"}
    proc = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard",
        stdin=asyncio.subprocess.PIPE,
        env=env,
    )
    # xclip reads stdin then stays alive to serve paste requests.
    proc.stdin.write(body.text.encode())  # type: ignore[union-attr]
    await proc.stdin.drain()  # type: ignore[union-attr]
    proc.stdin.close()  # type: ignore[union-attr]

    _xclip_procs[running.display] = proc

    return {"ok": True}


@app.get("/api/profiles/{profile_id}/clipboard")
async def get_clipboard(profile_id: str, request: Request):
    """Read the VNC session's clipboard.

    Chrome doesn't write to X11 clipboard under KasmVNC, so xclip can't read it.
    Instead, read via Playwright's CDP connection to Chrome (navigator.clipboard.readText).
    Falls back to xclip for non-Chrome clipboard owners.
    """
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "interact")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    # Read Chrome's current text selection via Playwright.
    # Chrome's native copy (via VNC Ctrl+C) doesn't write to X11 clipboard
    # and doesn't fire DOM events, so we read the visible selection instead.
    # The init script also captures copy events when they do fire.
    # Check all pages — user may have copied in any tab
    try:
        for page in running.context.pages:
            try:
                text = await page.evaluate("window.__clipboardText || ''")
                if text:
                    return {"text": text[:_CLIPBOARD_MAX_READ]}
            except Exception as exc:
                logger.debug("Clipboard read failed on page: %s", exc)
                continue
    except Exception as exc:
        logger.debug("Playwright clipboard read failed: %s", exc)

    # Fallback: xclip for non-Chrome clipboard owners
    import os

    env = {**os.environ, "DISPLAY": f":{running.display}"}
    proc = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard", "-o",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"text": ""}

    if proc.returncode != 0:
        return {"text": ""}

    text = stdout[:_CLIPBOARD_MAX_READ].decode("utf-8", errors="replace")
    return {"text": text}


# ── VNC WebSocket Proxy ──────────────────────────────────────────────────────


@app.websocket("/api/profiles/{profile_id}/vnc")
async def vnc_proxy(websocket: WebSocket, profile_id: str):
    """Proxy WebSocket frames between the frontend and a profile's KasmVNC."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "view")
    if not access_result:
        return
    _profile, identity = access_result
    can_interact = access.can_access_profile(identity, _profile, "interact")

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    # Accept with client's requested subprotocol (if any) — RFC 6455 requires
    # the server must not respond with a subprotocol the client didn't request.
    requested = websocket.scope.get("subprotocols", [])
    subprotocol = "binary" if "binary" in requested else None
    await websocket.accept(subprotocol=subprotocol)

    import websockets

    vnc_url = f"ws://127.0.0.1:{running.ws_port}/websockify"

    try:
        async with websockets.connect(
            vnc_url,
            subprotocols=["binary"],
            origin=f"http://127.0.0.1:{running.ws_port}",
            max_size=None,  # VNC frames can be large (1920x1080 framebuffer)
            ping_interval=None,  # KasmVNC doesn't respond to WS pings
            ping_timeout=None,
            compression=None,  # KasmVNC can't handle permessage-deflate
        ) as vnc_ws:
            logger.info(
                "VNC proxy: connected to KasmVNC for %s (subprotocol=%s)",
                profile_id, vnc_ws.subprotocol,
            )

            # noVNC v1.4 sends extension message types (150=ContinuousUpdates,
            # 248=QEMUKey, etc.) that KasmVNC 1.3.3 doesn't support, causing
            # "unknown message type" → disconnect.
            #
            # noVNC batches multiple RFB messages into a single WebSocket frame,
            # so we must parse the RFB stream to find message boundaries and strip
            # unsupported types before forwarding. Standard client→server types
            # have known fixed sizes (except SetEncodings and ClientCutText which
            # encode their length).

            async def client_to_vnc():
                count = 0
                handshake = 0  # first 3 messages are RFB handshake
                dropped = 0
                try:
                    while True:
                        msg = await websocket.receive()
                        msg_type = msg.get("type", "")
                        if msg_type == "websocket.disconnect":
                            logger.info("VNC proxy [c->v]: client disconnect (code=%s) after %d msgs (%d dropped)", msg.get("code"), count, dropped)
                            break
                        if "bytes" in msg and msg["bytes"]:
                            count += 1
                            data = msg["bytes"]
                            handshake += 1

                            # First 3 messages are RFB handshake — forward as-is
                            if handshake <= 3:
                                logger.debug("VNC handshake #%d: %d bytes hex=%s", handshake, len(data), data[:20].hex())
                                await vnc_ws.send(data)
                                continue

                            if not can_interact:
                                # Viewers may negotiate display settings and
                                # request frames, but never emit keyboard,
                                # pointer or clipboard input to the browser.
                                viewer_messages = _filter_rfb_viewer_messages(data)
                                if viewer_messages:
                                    await vnc_ws.send(viewer_messages)
                                else:
                                    dropped += 1
                                continue

                            # Parse RFB messages and strip unsupported types
                            filtered = _filter_rfb_client_messages(data)
                            if filtered:
                                # Safety: verify first byte is a valid RFB client type
                                if filtered[0] not in _RFB_MSG_SIZE:
                                    logger.error("RFB SAFETY: refusing to send data with invalid first byte=%d hex=%s",
                                                 filtered[0], filtered[:20].hex())
                                    dropped += 1
                                    continue
                                logger.debug("VNC send: %d bytes first_type=%d hex=%s", len(filtered), filtered[0], filtered[:100].hex())
                                await vnc_ws.send(filtered)
                            else:
                                dropped += 1

                        elif "text" in msg and msg["text"]:
                            if not can_interact:
                                dropped += 1
                                continue
                            # noVNC only sends binary frames — text frames are unexpected
                            # and would bypass the RFB filter, so drop them.
                            count += 1
                            logger.warning("VNC proxy [c->v]: DROPPING text frame len=%d (noVNC should only send binary)", len(msg["text"]))
                            dropped += 1
                        else:
                            logger.warning("VNC proxy [c->v]: unhandled msg keys=%s type=%s", list(msg.keys()), msg_type)
                except WebSocketDisconnect as exc:
                    logger.info("VNC proxy [c->v]: WebSocketDisconnect code=%s after %d msgs (%d dropped)", exc.code, count, dropped)
                except Exception as exc:
                    logger.warning("VNC proxy [c->v]: %s: %s (after %d msgs)", type(exc).__name__, exc, count)

            async def vnc_to_client():
                count = 0
                try:
                    async for msg in vnc_ws:
                        count += 1
                        if isinstance(msg, bytes) and len(msg) > 0:
                            msg_type = msg[0]
                            if msg_type == 180:
                                # KasmVNC BinaryClipboard → convert to standard
                                # ServerCutText (type 3) so noVNC can handle it
                                text = _parse_kasmvnc_clipboard(msg)
                                if text:
                                    logger.info("VNC proxy [v->c]: clipboard %d chars", len(text))
                                    await websocket.send_bytes(_build_server_cut_text(text))
                                else:
                                    logger.info("VNC proxy [v->c]: dropped type 180 (no text/plain)")
                                continue
                            await websocket.send_bytes(msg)
                        elif isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                    logger.info("VNC proxy [v->c]: KasmVNC stream ended after %d msgs (close_code=%s)", count, vnc_ws.close_code)
                except WebSocketDisconnect as exc:
                    logger.info("VNC proxy [v->c]: client disconnect code=%s after %d msgs", exc.code, count)
                except Exception as exc:
                    logger.warning("VNC proxy [v->c]: %s: %s (after %d msgs)", type(exc).__name__, exc, count)

            c2v = asyncio.create_task(client_to_vnc(), name="c2v")
            v2c = asyncio.create_task(vnc_to_client(), name="v2c")

            done, pending = await asyncio.wait(
                [c2v, v2c],
                return_when=asyncio.FIRST_COMPLETED,
            )
            finished = [t.get_name() for t in done]
            still_running = [t.get_name() for t in pending]

            # Check if Xvnc is still alive
            vnc_instance = browser_mgr.vnc._allocated.get(running.display)
            xvnc_alive = vnc_instance and vnc_instance.process and vnc_instance.process.poll() is None
            logger.info(
                "VNC proxy: finished=%s pending=%s xvnc_alive=%s display=:%d for %s",
                finished, still_running, xvnc_alive, running.display, profile_id,
            )

            # Dump Xvnc log on disconnect
            import os
            xvnc_log = f"/tmp/xvnc-{running.display}.log"
            if os.path.exists(xvnc_log):
                with open(xvnc_log) as f:
                    log_content = f.read()
                if log_content.strip():
                    for line in log_content.strip().split("\n")[-20:]:
                        logger.info("Xvnc[:%d] %s", running.display, line)

            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.error("VNC proxy connect error for %s: %s: %s", profile_id, type(exc).__name__, exc)
    finally:
        try:
            await websocket.close()
        except Exception as exc:
            logger.debug("VNC proxy: websocket.close() failed: %s", exc)


# ── CDP WebSocket Proxy ──────────────────────────────────────────────────────
# Simple bidirectional passthrough — CDP is standard JSON over WebSocket,
# no protocol translation needed (unlike VNC which requires RFB filtering).


@app.get("/api/profiles/{profile_id}/cdp")
async def cdp_info(profile_id: str, request: Request):
    """Return CDP connection info. Prevents SPA catch-all from serving index.html."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")
    return {
        "cdp_url": f"/api/profiles/{profile_id}/cdp",
        "usage": "playwright.chromium.connect_over_cdp('http://<host>/api/profiles/"
        + profile_id + "/cdp')",
    }


@app.get("/api/profiles/{profile_id}/cdp/json/version/")
@app.get("/api/profiles/{profile_id}/cdp/json/version")
async def cdp_json_version(profile_id: str, request: Request):
    """Proxy Chrome's /json/version, rewriting WS URLs to go through our proxy."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://127.0.0.1:{running.cdp_port}/json/version", timeout=5
            )
            data = resp.json()
    except Exception as exc:
        logger.error("CDP proxy: failed to reach Chrome CDP for %s: %s", profile_id, exc)
        raise HTTPException(status_code=502, detail="CDP endpoint unreachable")

    # Rewrite webSocketDebuggerUrl to point through our proxy
    host = request.headers.get("host", "localhost:8080")
    ws_scheme = "wss" if _is_https(request) else "ws"
    data["webSocketDebuggerUrl"] = f"{ws_scheme}://{host}/api/profiles/{profile_id}/cdp"
    return data


@app.get("/api/profiles/{profile_id}/cdp/json/list/")
@app.get("/api/profiles/{profile_id}/cdp/json/list")
@app.get("/api/profiles/{profile_id}/cdp/json/")
@app.get("/api/profiles/{profile_id}/cdp/json")
async def cdp_json_list(profile_id: str, request: Request):
    """Proxy Chrome's /json/list, rewriting WS URLs."""
    _profile, _identity = _require_profile_permission(request.scope, profile_id, "automate")
    running = browser_mgr.running.get(profile_id)
    if not running:
        raise HTTPException(status_code=404, detail="Profile not running")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://127.0.0.1:{running.cdp_port}/json/list", timeout=5
            )
            data = resp.json()
    except Exception as exc:
        logger.error("CDP proxy: failed to reach Chrome CDP for %s: %s", profile_id, exc)
        raise HTTPException(status_code=502, detail="CDP endpoint unreachable")

    host = request.headers.get("host", "localhost:8080")
    ws_scheme = "wss" if _is_https(request) else "ws"
    for entry in data:
        if "webSocketDebuggerUrl" in entry:
            ws_path = entry["webSocketDebuggerUrl"].split("/devtools/")[-1]
            entry["webSocketDebuggerUrl"] = (
                f"{ws_scheme}://{host}/api/profiles/{profile_id}/cdp/devtools/{ws_path}"
            )
    return data


async def _proxy_cdp_websocket(
    websocket: WebSocket, target_url: str, label: str,
) -> None:
    """Bidirectional WebSocket proxy between a FastAPI client and a CDP target.

    Used by both browser-level and page-level CDP proxy endpoints.
    """
    import websockets

    try:
        async with websockets.connect(
            target_url, max_size=None, ping_interval=None, ping_timeout=None
        ) as cdp_ws:
            logger.info("%s: connected to %s", label, target_url)

            async def client_to_cdp():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"]:
                            await cdp_ws.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await cdp_ws.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception as exc:
                    logger.warning("%s [c->cdp]: %s: %s", label, type(exc).__name__, exc)

            async def cdp_to_client():
                try:
                    async for msg in cdp_ws:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except WebSocketDisconnect:
                    pass
                except Exception as exc:
                    logger.warning("%s [cdp->c]: %s: %s", label, type(exc).__name__, exc)

            c2d = asyncio.create_task(client_to_cdp(), name="c2d")
            d2c = asyncio.create_task(cdp_to_client(), name="d2c")
            done, pending = await asyncio.wait(
                [c2d, d2c], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            logger.info("%s: disconnected", label)

    except Exception as exc:
        logger.error("%s error: %s", label, exc)
    finally:
        try:
            await websocket.close()
        except Exception as exc:
            logger.debug("%s: websocket.close() failed: %s", label, exc)


@app.websocket("/api/profiles/{profile_id}/cdp")
async def cdp_proxy(websocket: WebSocket, profile_id: str):
    """Proxy WebSocket frames between external tools and Chrome's CDP."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "automate")
    if not access_result:
        return

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    await websocket.accept()

    # Get browser-level CDP WebSocket URL from Chrome
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://127.0.0.1:{running.cdp_port}/json/version", timeout=5
            )
            ws_url = resp.json()["webSocketDebuggerUrl"]
    except Exception as exc:
        logger.error("CDP proxy: failed to get WS URL for %s: %s", profile_id, exc)
        await websocket.close(code=4005, reason="CDP not available")
        return

    await _proxy_cdp_websocket(websocket, ws_url, f"CDP proxy [{profile_id}]")


@app.websocket("/api/profiles/{profile_id}/cdp/devtools/{path:path}")
async def cdp_page_proxy(websocket: WebSocket, profile_id: str, path: str):
    """Proxy page-specific CDP WebSocket connections (e.g. /devtools/page/GUID)."""
    if not await _check_websocket_origin(websocket):
        return

    access_result = await _require_websocket_profile_permission(websocket, profile_id, "automate")
    if not access_result:
        return

    running = browser_mgr.running.get(profile_id)
    if not running:
        await websocket.close(code=4004, reason="Profile not running")
        return

    await websocket.accept()

    target_url = f"ws://127.0.0.1:{running.cdp_port}/devtools/{path}"
    await _proxy_cdp_websocket(websocket, target_url, f"CDP page proxy [{profile_id}]")


# ── Static Frontend ───────────────────────────────────────────────────────────

# Serve React build. Must be AFTER API routes so /api/* isn't caught by the SPA.
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA — all non-API routes return index.html."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        file_path = FRONTEND_DIR / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
