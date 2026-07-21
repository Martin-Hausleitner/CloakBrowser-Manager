# Streaming Speed Benchmark Report

Generated: `2026-07-21T13:47:14.473092+00:00`

This report contains only locally observed measurements. Entries marked `not_installed` or `architecture_only` were not benchmarked.

| Candidate | Type | Status | Availability | Key timings | Notes |
|---|---|---|---|---|---|
| KasmVNC Manager health | `http` | `measured` | `available` | connect_ms median 0.116 ms (p95 0.178 ms)<br>first_byte_ms median 1.438 ms (p95 4.005 ms)<br>total_ms median 1.447 ms (p95 4.017 ms) | - |
| KasmVNC/noVNC live WebSocket | `websocket` | `measured` | `available` | connect_ms median 0.121 ms (p95 0.174 ms)<br>first_byte_ms median 3.435 ms (p95 8.894 ms)<br>handshake_ms median 3.457 ms (p95 8.931 ms) | - |
| Selkies WebSocket POC | `websocket` | `not_installed` | `not_measured` | - | required executable not found on PATH: gst-launch-1.0 |
| Sunshine + Moonlight native client path | `architecture` | `architecture_only` | `not_measured` | - | Native-client architecture; no comparable embedded browser WebSocket endpoint is configured in this repository. |
| Apache Guacamole gateway path | `architecture` | `architecture_only` | `not_measured` | - | Gateway architecture reference only; no Guacamole deployment is configured for this local baseline. |

The browser-facing report intentionally omits local paths, commands, endpoints, and credential-bearing headers.
See `docs/STREAMING-SPEED-TEST-RUNNER.md` for the reproducible command shape.
