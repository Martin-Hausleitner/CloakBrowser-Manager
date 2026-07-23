"""Admin-only live launch and VNC diagnostics.

Process-local counters and timings for running profiles. Values are intentionally
redacted: no display numbers, ports, paths, URLs, proxy data, or browser content.
Unmeasured timings stay explicitly unavailable rather than being invented.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

MetricAvailability = Literal["measured", "unavailable"]
ProfileRuntimeStatus = Literal["running", "launching", "stopped"]

_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "display",
        "ws_port",
        "vnc_ws_port",
        "cdp_port",
        "cdp_url",
        "vnc_url",
        "url",
        "path",
        "command",
        "proxy",
        "token",
        "password",
        "cookie",
        "user_data_dir",
        "launch_args",
        "stdout",
        "stderr",
        "log",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def metric(value: float | int | None) -> dict[str, Any]:
    """Return a measured or explicitly unavailable metric payload."""
    if value is None:
        return {"availability": "unavailable", "value": None}
    if isinstance(value, bool):
        return {"availability": "unavailable", "value": None}
    if isinstance(value, float):
        if value != value or value < 0:  # NaN or negative
            return {"availability": "unavailable", "value": None}
        return {"availability": "measured", "value": round(value, 3)}
    if isinstance(value, int) and value < 0:
        return {"availability": "unavailable", "value": None}
    return {"availability": "measured", "value": value}


@dataclass
class _VncSession:
    connected_mono: float
    connected_wall: float
    open_ms: float | None = None
    first_framebuffer_ms: float | None = None
    saw_framebuffer: bool = False


@dataclass
class _ProfileDiagnostics:
    profile_id: str
    status: ProfileRuntimeStatus = "stopped"
    launch_started_mono: float | None = None
    launched_at_wall: float | None = None
    launch_duration_ms: float | None = None
    stopped_at_wall: float | None = None
    total_vnc_connections: int = 0
    last_vnc_connected_at_wall: float | None = None
    last_vnc_disconnected_at_wall: float | None = None
    last_vnc_websocket_open_ms: float | None = None
    last_vnc_first_framebuffer_ms: float | None = None
    active_sessions: dict[int, _VncSession] = field(default_factory=dict)


class LiveDiagnosticsRegistry:
    """Thread-safe in-memory registry for admin live diagnostics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: dict[str, _ProfileDiagnostics] = {}
        self._total_launches = 0
        self._total_vnc_connections = 0
        self._next_session_id = 1

    def reset(self) -> None:
        with self._lock:
            self._profiles.clear()
            self._total_launches = 0
            self._total_vnc_connections = 0
            self._next_session_id = 1

    def _get(self, profile_id: str) -> _ProfileDiagnostics:
        profile = self._profiles.get(profile_id)
        if profile is None:
            profile = _ProfileDiagnostics(profile_id=profile_id)
            self._profiles[profile_id] = profile
        return profile

    def mark_launch_started(self, profile_id: str) -> None:
        with self._lock:
            profile = self._get(profile_id)
            profile.status = "launching"
            profile.launch_started_mono = time.monotonic()
            profile.stopped_at_wall = None
            profile.launch_duration_ms = None
            profile.launched_at_wall = None

    def mark_launch_succeeded(self, profile_id: str) -> None:
        with self._lock:
            profile = self._get(profile_id)
            now_mono = time.monotonic()
            started = profile.launch_started_mono
            profile.status = "running"
            profile.launched_at_wall = time.time()
            profile.launch_duration_ms = (
                round((now_mono - started) * 1000.0, 3) if started is not None else None
            )
            self._total_launches += 1

    def mark_launch_failed(self, profile_id: str) -> None:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return
            profile.status = "stopped"
            profile.stopped_at_wall = time.time()
            if not profile.active_sessions and profile.total_vnc_connections == 0:
                # Keep failed launch rows only when they already have history.
                if profile.launched_at_wall is None and profile.launch_duration_ms is None:
                    self._profiles.pop(profile_id, None)

    def mark_stopped(self, profile_id: str) -> None:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return
            profile.status = "stopped"
            profile.stopped_at_wall = time.time()
            profile.active_sessions.clear()

    def begin_vnc_session(self, profile_id: str) -> int:
        with self._lock:
            profile = self._get(profile_id)
            session_id = self._next_session_id
            self._next_session_id += 1
            now_mono = time.monotonic()
            now_wall = time.time()
            profile.active_sessions[session_id] = _VncSession(
                connected_mono=now_mono,
                connected_wall=now_wall,
            )
            profile.total_vnc_connections += 1
            profile.last_vnc_connected_at_wall = now_wall
            self._total_vnc_connections += 1
            return session_id

    def mark_vnc_websocket_open(self, profile_id: str, session_id: int) -> None:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return
            session = profile.active_sessions.get(session_id)
            if session is None:
                return
            open_ms = round((time.monotonic() - session.connected_mono) * 1000.0, 3)
            session.open_ms = open_ms
            profile.last_vnc_websocket_open_ms = open_ms

    def mark_vnc_first_framebuffer(self, profile_id: str, session_id: int) -> None:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return
            session = profile.active_sessions.get(session_id)
            if session is None or session.saw_framebuffer:
                return
            session.saw_framebuffer = True
            first_ms = round((time.monotonic() - session.connected_mono) * 1000.0, 3)
            session.first_framebuffer_ms = first_ms
            profile.last_vnc_first_framebuffer_ms = first_ms

    def end_vnc_session(self, profile_id: str, session_id: int) -> None:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return
            session = profile.active_sessions.pop(session_id, None)
            profile.last_vnc_disconnected_at_wall = time.time()
            if session is not None:
                if session.open_ms is not None:
                    profile.last_vnc_websocket_open_ms = session.open_ms
                if session.first_framebuffer_ms is not None:
                    profile.last_vnc_first_framebuffer_ms = session.first_framebuffer_ms

    def snapshot(self, *, running_profile_ids: set[str] | None = None) -> dict[str, Any]:
        """Return a redacted administrator diagnostics payload."""
        with self._lock:
            running_ids = set(running_profile_ids or ())
            active_vnc = sum(len(p.active_sessions) for p in self._profiles.values())
            profiles_out: list[dict[str, Any]] = []

            # Prefer currently running profiles, then retained history.
            ordered_ids = sorted(running_ids) + sorted(
                pid for pid in self._profiles if pid not in running_ids
            )
            seen: set[str] = set()
            for profile_id in ordered_ids:
                if profile_id in seen:
                    continue
                seen.add(profile_id)
                profile = self._profiles.get(profile_id)
                if profile is None:
                    if profile_id not in running_ids:
                        continue
                    profile = _ProfileDiagnostics(profile_id=profile_id, status="running")

                status: ProfileRuntimeStatus
                if profile_id in running_ids:
                    status = "running" if profile.status != "launching" else "launching"
                else:
                    status = "stopped" if profile.status != "launching" else "stopped"

                profiles_out.append(
                    {
                        "profile_id": profile_id,
                        "status": status,
                        "launched_at": _iso(profile.launched_at_wall),
                        "stopped_at": (
                            _iso(profile.stopped_at_wall) if status == "stopped" else None
                        ),
                        "metrics": {
                            "launch_duration_ms": metric(profile.launch_duration_ms),
                            "active_vnc_connections": metric(len(profile.active_sessions)),
                            "total_vnc_connections": metric(profile.total_vnc_connections),
                            "vnc_websocket_open_ms": metric(profile.last_vnc_websocket_open_ms),
                            "vnc_first_framebuffer_ms": metric(
                                profile.last_vnc_first_framebuffer_ms
                            ),
                        },
                        "timestamps": {
                            "last_vnc_connected_at": _iso(profile.last_vnc_connected_at_wall),
                            "last_vnc_disconnected_at": _iso(
                                profile.last_vnc_disconnected_at_wall
                            ),
                        },
                    }
                )

            payload = {
                "generated_at": _utc_now().isoformat().replace("+00:00", "Z"),
                "running_profiles": len(running_ids),
                "active_vnc_connections": active_vnc,
                "total_launches": self._total_launches,
                "total_vnc_connections": self._total_vnc_connections,
                "profiles": profiles_out,
            }
            return redact_diagnostics_payload(payload)


def redact_diagnostics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop forbidden keys recursively and keep unavailable metrics explicit."""

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_l = str(key).lower()
                if key_l in _FORBIDDEN_RESPONSE_KEYS or any(
                    bad in key_l for bad in ("password", "token", "cookie", "secret", "proxy")
                ):
                    continue
                cleaned[key] = _walk(item)
            return cleaned
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(payload)


live_diagnostics = LiveDiagnosticsRegistry()


class FirstFramebufferDetector:
    """Best-effort detector for the first RFB FramebufferUpdate (type 0).

    Used only for live diagnostics. Parse failures leave the metric unavailable
    instead of guessing a frame time from arbitrary WebSocket bytes.
    """

    def __init__(self) -> None:
        self._phase = "protocol_version"
        self._buffer = bytearray()
        self.seen = False

    def observe(self, data: bytes) -> bool:
        if self.seen or not data:
            return self.seen
        if len(self._buffer) + len(data) > 8 * 1024 * 1024:
            self._buffer.clear()
            self._phase = "abandoned"
            return False
        self._buffer.extend(data)
        try:
            while self._buffer and not self.seen:
                if self._phase == "abandoned":
                    self._buffer.clear()
                    return False
                if self._phase == "protocol_version":
                    if len(self._buffer) < 12:
                        return False
                    version = bytes(self._buffer[:12])
                    if not version.startswith(b"RFB 003.") or version[11:12] != b"\n":
                        self._phase = "abandoned"
                        self._buffer.clear()
                        return False
                    del self._buffer[:12]
                    self._phase = "security"
                    continue
                if self._phase == "security":
                    # SecurityTypes (1 + N) or SecurityResult (4) then ServerInit.
                    # Accept either None-security shortcut and advance on ServerInit
                    # by scanning for a plausible desktop-name length once we have
                    # at least the fixed 24-byte ServerInit prefix later.
                    if not self._buffer:
                        return False
                    # RFB 3.8: number-of-security-types + types, client selects, then
                    # 4-byte SecurityResult. We cannot fully validate without the
                    # client stream, so wait until we see a SecurityResult of 0 and
                    # then treat the remainder as ServerInit.
                    if self._buffer[0] == 0 and len(self._buffer) >= 5:
                        # failure reason path — abandon timing rather than guess
                        self._phase = "abandoned"
                        self._buffer.clear()
                        return False
                    if len(self._buffer) >= 1 and self._buffer[0] > 0:
                        count = self._buffer[0]
                        need = 1 + count
                        if len(self._buffer) < need:
                            return False
                        del self._buffer[:need]
                        self._phase = "security_result"
                        continue
                    # RFB <= 3.3 style 4-byte security type
                    if len(self._buffer) >= 4 and self._buffer[0] == 0 and self._buffer[1] == 0:
                        del self._buffer[:4]
                        self._phase = "server_init"
                        continue
                    return False
                if self._phase == "security_result":
                    if len(self._buffer) < 4:
                        return False
                    result = int.from_bytes(self._buffer[:4], "big")
                    del self._buffer[:4]
                    if result != 0:
                        self._phase = "abandoned"
                        self._buffer.clear()
                        return False
                    self._phase = "server_init"
                    continue
                if self._phase == "server_init":
                    if len(self._buffer) < 24:
                        return False
                    name_len = int.from_bytes(self._buffer[20:24], "big")
                    total = 24 + name_len
                    if name_len < 0 or name_len > 1_000_000:
                        self._phase = "abandoned"
                        self._buffer.clear()
                        return False
                    if len(self._buffer) < total:
                        return False
                    del self._buffer[:total]
                    self._phase = "normal"
                    continue
                if self._phase == "normal":
                    message_type = self._buffer[0]
                    if message_type == 0:
                        self.seen = True
                        self._buffer.clear()
                        return True
                    # Skip unknown/non-framebuffer traffic without claiming a frame.
                    self._buffer.clear()
                    return False
                return False
        except Exception:
            self._phase = "abandoned"
            self._buffer.clear()
            return False
        return self.seen
