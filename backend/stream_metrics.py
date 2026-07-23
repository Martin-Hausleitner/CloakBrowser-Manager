"""Client-reported live stream metrics (CDP/VNC) for UI and agents.

Samples are process-local, redacted, and overwritten per profile/transport.
Clients POST measured FPS/RTT/connection state; consumers poll GET.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Transport = Literal["cdp", "vnc"]
ConnectionState = Literal["connecting", "connected", "reconnecting", "failed", "idle"]


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _Sample:
    transport: Transport
    connection_state: ConnectionState = "idle"
    fps: float | None = None
    rtt_ms: float | None = None
    frames_received: int | None = None
    reconnect_count: int | None = None
    dropped_frames: int | None = None
    updated_at: float = field(default_factory=time.time)


class StreamMetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_profile: dict[str, dict[str, _Sample]] = {}

    def reset(self) -> None:
        with self._lock:
            self._by_profile.clear()

    def record(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        transport = str(payload.get("transport") or "cdp")
        if transport not in {"cdp", "vnc"}:
            transport = "cdp"
        state = str(payload.get("connection_state") or "connected")
        if state not in {"connecting", "connected", "reconnecting", "failed", "idle"}:
            state = "connected"

        def _num(key: str) -> float | None:
            value = payload.get(key)
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if number != number or number < 0:
                return None
            return number

        def _int(key: str) -> int | None:
            value = _num(key)
            if value is None:
                return None
            return int(value)

        sample = _Sample(
            transport=transport,  # type: ignore[arg-type]
            connection_state=state,  # type: ignore[arg-type]
            fps=_num("fps"),
            rtt_ms=_num("rtt_ms"),
            frames_received=_int("frames_received"),
            reconnect_count=_int("reconnect_count"),
            dropped_frames=_int("dropped_frames"),
            updated_at=time.time(),
        )
        with self._lock:
            bucket = self._by_profile.setdefault(profile_id, {})
            bucket[transport] = sample
        return self.snapshot(profile_id)

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            bucket = self._by_profile.get(profile_id) or {}
            # Prefer CDP (snappy path) when both present.
            preferred = bucket.get("cdp") or bucket.get("vnc")
            transports = {
                name: {
                    "transport": sample.transport,
                    "connection_state": sample.connection_state,
                    "fps": sample.fps,
                    "rtt_ms": sample.rtt_ms,
                    "frames_received": sample.frames_received,
                    "reconnect_count": sample.reconnect_count,
                    "dropped_frames": sample.dropped_frames,
                    "updated_at": _iso(sample.updated_at),
                }
                for name, sample in bucket.items()
            }
            if preferred is None:
                return {
                    "profile_id": profile_id,
                    "transport": None,
                    "connection_state": "idle",
                    "fps": None,
                    "rtt_ms": None,
                    "frames_received": None,
                    "reconnect_count": None,
                    "dropped_frames": None,
                    "updated_at": None,
                    "transports": transports,
                }
            return {
                "profile_id": profile_id,
                "transport": preferred.transport,
                "connection_state": preferred.connection_state,
                "fps": preferred.fps,
                "rtt_ms": preferred.rtt_ms,
                "frames_received": preferred.frames_received,
                "reconnect_count": preferred.reconnect_count,
                "dropped_frames": preferred.dropped_frames,
                "updated_at": _iso(preferred.updated_at),
                "transports": transports,
            }


stream_metrics = StreamMetricsRegistry()
