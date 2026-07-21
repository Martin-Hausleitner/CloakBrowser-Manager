# Streaming Speed Benchmark Report

Generated: `2026-07-21T10:34:06.608581+00:00`

This report contains only locally observed measurements. Entries marked `not_installed` or `architecture_only` were not benchmarked.

| Candidate | Type | Status | Availability | Key timings | Notes |
|---|---|---|---|---|---|
| KasmVNC Manager health | `http` | `measured` | `available` | connect_ms median 0.135 ms (p95 1.539 ms)<br>first_byte_ms median 2.233 ms (p95 15.891 ms)<br>total_ms median 2.239 ms (p95 15.91 ms) | - |
| KasmVNC/noVNC live WebSocket | `websocket` | `measured` | `available` | connect_ms median 0.172 ms (p95 0.18 ms)<br>first_byte_ms median 4.509 ms (p95 17.305 ms)<br>handshake_ms median 4.541 ms (p95 17.442 ms) | - |
| Selkies WebSocket POC | `websocket` | `not_installed` | `not_measured` | - | required executable not found on PATH: gst-launch-1.0 |
| Sunshine + Moonlight native client path | `architecture` | `architecture_only` | `not_measured` | - | Native-client architecture; no comparable embedded browser WebSocket endpoint is configured in this repository. |
| Apache Guacamole gateway path | `architecture` | `architecture_only` | `not_measured` | - | Gateway architecture reference only; no Guacamole deployment is configured for this local baseline. |

The browser-facing report intentionally omits local paths, commands, endpoints, and credential-bearing headers. These loopback probes are a regression baseline for the current KasmVNC/noVNC path, not a cross-technology winner claim.

See `docs/STREAMING-SPEED-TEST-RUNNER.md` for the reproducible command shape.
