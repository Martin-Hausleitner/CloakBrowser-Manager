# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [2026-07-24] - Browser-Use, proxies, agent control & live cast

Fork integration release on `integrate-pr-47-27-26` (merged to `main`). Summarizes the Browser-Use shell, proxy inventory, agent/extension control plane, open-links live path, and P1 API fixes.

### Added

- **Browser-Use compact UI** — narrow sidebar, pin, templates / Generate New, mobile settings aligned with normal desktop settings; bulk organize removed from the primary flow.
- **Proxy inventory** — VCVM proxychecker integration, proxy-on-start, geo-aligned auto profiles, and honest risk/auth scoring (no invented authenticity).
- **Comet extension defaults catalog** — harvested defaults shipped in-image / via `EXTENSION_CATALOG_DIR`.
- **Agent control plane** — `cbm_agent` API keys, CLI `cbm_agent_ctl.py`, and operate grants.
- **Extension APIs** — catalog / defaults / templates / sessions / open, plus Chrome extension `extensions/cloak-profile-sync/`.
- **Open-links VNC + CDP** — fullscreen `/session` live path; cast hardening (~12–21 fps) with compositor pulse and screenshot fallback.
- **Live Dev views** — fingerprint / BrowserScan health surfaces and live CDP metrics (FPS/RTT).
- **Steel-browser-inspired open-link patterns** for session launch URLs.
- **Ultragoal vision E2E** — 29/29 on VCVM.

### Fixed

- **P1** — flat session open-links (top-level CDP/VNC URLs compatible with open-links clients).
- **P1** — agent `DELETE` revokes immediately.
- **P1** — proxy score honesty (structured proxychecker reasons without inventing authenticity).
- Live CDP screencast stall after first frame (page-target attach, compositor pulse, captureScreenshot fallback).
- BrowserScan label false positives in profile health checks.
- Extension catalog empty on VCVM deploy (config / catalog dir in image).

### Changed

- Profile organization and access context (projects, folders, pins) retained in API/schema; UI focus shifted to compact Browser-Use shell.
- Redacted admin live launch / VNC diagnostics for operators without leaking ports, paths, or secrets.

