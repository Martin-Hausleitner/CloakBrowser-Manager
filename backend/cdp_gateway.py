"""CDP gateway helpers: direct lease socket registry and observer filters."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

TOKEN_QUERY_KEYS = frozenset(
    {
        "token",
        "lease",
        "lease_token",
        "automation_lease",
        "access_token",
        "api_key",
        "key",
        "auth",
        "authorization",
        "secret",
        "password",
    }
)

OBSERVER_CLIENT_MAX_BYTES = 16_384
OBSERVER_UPSTREAM_MAX_BYTES = 6_000_000
OBSERVER_FRAME_DATA_MAX_CHARS = 5_500_000
OBSERVER_ALLOWED_START_KEYS = frozenset(
    {"format", "quality", "maxWidth", "maxHeight", "everyNthFrame"}
)
OBSERVER_ALLOWED_METHODS = frozenset(
    {
        "Page.startScreencast",
        "Page.screencastFrameAck",
        "Page.stopScreencast",
    }
)


class ObserverFrameRejected(ValueError):
    """Client or upstream observer frame failed allowlist validation."""


def query_has_token_like_key(query_params: Iterable[str] | dict[str, Any]) -> bool:
    keys = query_params.keys() if isinstance(query_params, dict) else query_params
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in TOKEN_QUERY_KEYS:
            return True
        if "token" in normalized or "lease" in normalized:
            return True
    return False


def _require_int(value: Any, *, field_name: str, min_value: int, max_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObserverFrameRejected(f"invalid {field_name}")
    if value < min_value or value > max_value:
        raise ObserverFrameRejected(f"invalid {field_name}")
    return value


def validate_observer_client_message(raw: str | bytes) -> dict[str, Any]:
    """Allow only fixed screencast client commands with bounded params."""
    if isinstance(raw, bytes):
        if len(raw) > OBSERVER_CLIENT_MAX_BYTES:
            raise ObserverFrameRejected("oversized")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ObserverFrameRejected("invalid encoding") from exc
    else:
        if not isinstance(raw, str):
            raise ObserverFrameRejected("invalid type")
        if len(raw.encode("utf-8")) > OBSERVER_CLIENT_MAX_BYTES:
            raise ObserverFrameRejected("oversized")
        text = raw

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ObserverFrameRejected("invalid json") from exc
    if not isinstance(payload, dict):
        raise ObserverFrameRejected("invalid payload")

    allowed_top = {"id", "method", "params"}
    if set(payload) - allowed_top:
        raise ObserverFrameRejected("unknown keys")
    msg_id = payload.get("id")
    if not isinstance(msg_id, int) or isinstance(msg_id, bool) or msg_id < 1:
        raise ObserverFrameRejected("invalid id")
    method = payload.get("method")
    if method not in OBSERVER_ALLOWED_METHODS:
        raise ObserverFrameRejected("method denied")
    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ObserverFrameRejected("invalid params")

    if method == "Page.startScreencast":
        if set(params) - OBSERVER_ALLOWED_START_KEYS:
            raise ObserverFrameRejected("unknown params")
        fmt = params.get("format", "jpeg")
        if fmt not in {"jpeg", "png"}:
            raise ObserverFrameRejected("invalid format")
        quality = _require_int(
            params.get("quality", 50), field_name="quality", min_value=1, max_value=100
        )
        max_width = _require_int(
            params.get("maxWidth", 1280),
            field_name="maxWidth",
            min_value=16,
            max_value=4096,
        )
        max_height = _require_int(
            params.get("maxHeight", 720),
            field_name="maxHeight",
            min_value=16,
            max_value=4096,
        )
        every_nth = _require_int(
            params.get("everyNthFrame", 1),
            field_name="everyNthFrame",
            min_value=1,
            max_value=30,
        )
        sanitized = {
            "id": msg_id,
            "method": method,
            "params": {
                "format": fmt,
                "quality": quality,
                "maxWidth": max_width,
                "maxHeight": max_height,
                "everyNthFrame": every_nth,
            },
        }
        return sanitized

    if method == "Page.screencastFrameAck":
        if set(params) - {"sessionId"}:
            raise ObserverFrameRejected("unknown params")
        session_id = _require_int(
            params.get("sessionId"),
            field_name="sessionId",
            min_value=0,
            max_value=2_147_483_647,
        )
        return {
            "id": msg_id,
            "method": method,
            "params": {"sessionId": session_id},
        }

    # Page.stopScreencast
    if set(params):
        raise ObserverFrameRejected("unknown params")
    return {"id": msg_id, "method": method, "params": {}}


_METADATA_KEYS = frozenset(
    {
        "offsetTop",
        "pageScaleFactor",
        "deviceWidth",
        "deviceHeight",
        "scrollOffsetX",
        "scrollOffsetY",
        "timestamp",
    }
)


def filter_observer_upstream_message(
    raw: str | bytes,
    *,
    accepted_ids: set[int],
) -> str | None:
    """Return sanitized upstream JSON text, or None to drop."""
    if isinstance(raw, bytes):
        if len(raw) > OBSERVER_UPSTREAM_MAX_BYTES:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        if not isinstance(raw, str):
            return None
        if len(raw.encode("utf-8")) > OBSERVER_UPSTREAM_MAX_BYTES:
            return None
        text = raw

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    if "id" in payload:
        msg_id = payload.get("id")
        if not isinstance(msg_id, int) or isinstance(msg_id, bool):
            return None
        if msg_id not in accepted_ids:
            return None
        # Responses may contain result or error only.
        keys = set(payload)
        if keys - {"id", "result", "error", "sessionId"}:
            return None
        return json.dumps(payload, separators=(",", ":"))

    method = payload.get("method")
    if method != "Page.screencastFrame":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    data = params.get("data")
    if not isinstance(data, str) or not data or len(data) > OBSERVER_FRAME_DATA_MAX_CHARS:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", data):
        return None
    session_id = params.get("sessionId")
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 0:
        return None
    metadata = params.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        return None
    if set(metadata) - _METADATA_KEYS:
        return None
    sanitized = {
        "method": "Page.screencastFrame",
        "params": {
            "data": data,
            "sessionId": session_id,
            "metadata": metadata,
        },
    }
    return json.dumps(sanitized, separators=(",", ":"))


@dataclass(eq=False)
class DirectCdpSocketHandle:
    lease_id: str
    profile_id: str
    owner_kind: str
    owner_id: str
    expires_at: datetime
    revoked: asyncio.Event = field(default_factory=asyncio.Event)


class DirectCdpSocketRegistry:
    """Process-local registry for direct CDP sockets bound to automation leases."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.poll_interval_seconds = poll_interval_seconds
        self._handles: set[DirectCdpSocketHandle] = set()
        self._lock = asyncio.Lock()

    def register(
        self,
        *,
        lease_id: str,
        profile_id: str,
        owner_kind: str,
        owner_id: str,
        expires_at: datetime,
    ) -> DirectCdpSocketHandle:
        handle = DirectCdpSocketHandle(
            lease_id=lease_id,
            profile_id=profile_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            expires_at=expires_at,
        )
        self._handles.add(handle)
        return handle

    def unregister(self, handle: DirectCdpSocketHandle | None) -> None:
        if handle is not None:
            self._handles.discard(handle)

    def update_expiry(self, lease_id: str, expires_at: datetime) -> None:
        for handle in tuple(self._handles):
            if handle.lease_id == lease_id and not handle.revoked.is_set():
                handle.expires_at = expires_at

    def revoke_lease(self, lease_id: str) -> None:
        for handle in tuple(self._handles):
            if handle.lease_id == lease_id:
                handle.revoked.set()

    def revoke_leases(self, lease_ids: Iterable[str]) -> None:
        wanted = set(lease_ids)
        for handle in tuple(self._handles):
            if handle.lease_id in wanted:
                handle.revoked.set()

    def revoke_profile(self, profile_id: str) -> None:
        for handle in tuple(self._handles):
            if handle.profile_id == profile_id:
                handle.revoked.set()

    async def watch_until_revoked_or_expired(
        self, handle: DirectCdpSocketHandle
    ) -> str:
        """Block until lease revocation or expiry. Returns reason."""
        while not handle.revoked.is_set():
            now = self._clock()
            remaining = (handle.expires_at - now).total_seconds()
            if remaining <= 0:
                handle.revoked.set()
                return "expired"
            wait = min(self.poll_interval_seconds, max(remaining, 0.01))
            try:
                await asyncio.wait_for(handle.revoked.wait(), timeout=wait)
            except asyncio.TimeoutError:
                continue
        return "revoked"
