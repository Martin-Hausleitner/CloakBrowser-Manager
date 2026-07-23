"""Unit tests for admin-only live diagnostics registry and redaction."""

from __future__ import annotations

import struct

from backend.live_diagnostics import (
    FirstFramebufferDetector,
    LiveDiagnosticsRegistry,
    metric,
    redact_diagnostics_payload,
)


def test_metric_unavailable_for_missing_values():
    assert metric(None) == {"availability": "unavailable", "value": None}
    assert metric(float("nan")) == {"availability": "unavailable", "value": None}
    assert metric(-1) == {"availability": "unavailable", "value": None}


def test_metric_measured_for_non_negative_values():
    assert metric(0) == {"availability": "measured", "value": 0}
    assert metric(12.3456) == {"availability": "measured", "value": 12.346}


def test_redaction_strips_forbidden_keys():
    payload = {
        "profile_id": "abc",
        "display": ":3",
        "ws_port": 5901,
        "cdp_url": "http://127.0.0.1:9222",
        "proxy": "http://user:pass@host:8080",
        "nested": {"token": "secret", "ok": True, "password_hint": "nope"},
        "metrics": {"launch_duration_ms": {"availability": "unavailable", "value": None}},
    }
    cleaned = redact_diagnostics_payload(payload)
    assert cleaned["profile_id"] == "abc"
    assert "display" not in cleaned
    assert "ws_port" not in cleaned
    assert "cdp_url" not in cleaned
    assert "proxy" not in cleaned
    assert "token" not in cleaned["nested"]
    assert "password_hint" not in cleaned["nested"]
    assert cleaned["nested"]["ok"] is True
    assert cleaned["metrics"]["launch_duration_ms"]["availability"] == "unavailable"


def test_registry_records_launch_and_vnc_counters():
    registry = LiveDiagnosticsRegistry()
    registry.mark_launch_started("p1")
    registry.mark_launch_succeeded("p1")
    session = registry.begin_vnc_session("p1")
    registry.mark_vnc_websocket_open("p1", session)
    registry.mark_vnc_first_framebuffer("p1", session)

    snap = registry.snapshot(running_profile_ids={"p1"})
    assert snap["running_profiles"] == 1
    assert snap["total_launches"] == 1
    assert snap["total_vnc_connections"] == 1
    assert snap["active_vnc_connections"] == 1
    profile = snap["profiles"][0]
    assert profile["profile_id"] == "p1"
    assert profile["status"] == "running"
    assert profile["metrics"]["launch_duration_ms"]["availability"] == "measured"
    assert profile["metrics"]["vnc_websocket_open_ms"]["availability"] == "measured"
    assert profile["metrics"]["vnc_first_framebuffer_ms"]["availability"] == "measured"
    assert profile["launched_at"] is not None
    assert "display" not in profile
    assert "ws_port" not in profile

    registry.end_vnc_session("p1", session)
    registry.mark_stopped("p1")
    stopped = registry.snapshot(running_profile_ids=set())
    assert stopped["active_vnc_connections"] == 0
    assert stopped["profiles"][0]["status"] == "stopped"
    assert stopped["profiles"][0]["metrics"]["active_vnc_connections"]["value"] == 0


def test_unmeasured_vnc_timings_stay_unavailable():
    registry = LiveDiagnosticsRegistry()
    registry.mark_launch_started("p2")
    registry.mark_launch_succeeded("p2")
    snap = registry.snapshot(running_profile_ids={"p2"})
    metrics = snap["profiles"][0]["metrics"]
    assert metrics["vnc_websocket_open_ms"] == {"availability": "unavailable", "value": None}
    assert metrics["vnc_first_framebuffer_ms"] == {
        "availability": "unavailable",
        "value": None,
    }


def test_first_framebuffer_detector_on_simple_stream():
    detector = FirstFramebufferDetector()
    name = b"desk"
    pixel_format = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    server_init = struct.pack(">HH", 100, 200) + pixel_format + struct.pack(">I", len(name)) + name
    assert detector.observe(b"RFB 003.008\n") is False
    assert detector.observe(b"\x01\x01") is False
    assert detector.observe(b"\x00\x00\x00\x00") is False
    assert detector.observe(server_init) is False
    assert detector.observe(b"\x00\x00\x00") is True
    assert detector.seen is True
