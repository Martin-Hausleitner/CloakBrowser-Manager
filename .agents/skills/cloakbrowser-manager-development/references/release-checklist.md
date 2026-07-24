---
description: Release, VCVM, browser, security, evidence, commit, and push checks for the CloakBrowser Manager fork.
metadata:
  tags: [cloakbrowser, vcvm, mobile-vnc, release, verification]
  source: internal
---

# CloakBrowser Manager release checklist

Use the smallest focused checks while developing, then run every applicable release layer before claiming the branch is ready.

## 1. Source and repository safety

- Confirm the active worktree, branch, `fork`, and `origin` URLs.
- Inspect all dirty and untracked paths; preserve unrelated work.
- Search the staged diff for credentials, bearer tokens, passwords, cookies, proxy URLs, host paths, and raw browser content.
- Run `git diff --check` and inspect `git diff --cached --stat` plus `git diff --cached`.

## 2. Backend

Focused examples:

```bash
.venv/bin/pytest backend/tests/test_models.py backend/tests/test_database.py backend/tests/test_api.py backend/tests/test_access_control.py -q
```

Full suite:

```bash
.venv/bin/pytest -q
```

When changing release scripts, compile them with the oldest supported Python runtime as well as the local interpreter. A passing Python 3.14 check does not prove Python 3.11 compatibility.

## 3. Frontend

Focused organization, access, mobile, API, and harness checks:

```bash
cd frontend
npm test -- --run \
  src/App.test.tsx \
  src/components/ProfileList.test.tsx \
  src/components/mobile/MobileSplitScreen.test.tsx \
  src/components/AccessDashboard.test.tsx \
  src/lib/profileOrganization.test.ts \
  src/lib/api.test.ts \
  src/lib/taskHarness.test.ts
npm run build
```

Full suite:

```bash
cd frontend
npm test
npm run build
```

## 4. Release scripts and static gates

Run the repository tests for scripts and deployment surfaces before a live deployment:

```bash
.venv/bin/pytest scripts -q
python3 scripts/release_acceptance_gate.py --help
python3 scripts/mobile_ui_gate.py --help
python3 scripts/mobile_webkit_gate.py --help
```

Use the actual gate options documented by each script for the target environment. Do not weaken a failed or blocked prerequisite.

## 5. VCVM deployment

- Deploy only after local unit, integration, build, and static gates pass.
- Confirm FastAPI, React, SQLite, CloakBrowser, KasmVNC/noVNC, and profile storage all run on the VCVM.
- Keep the Manager on loopback or private Tailnet HTTPS; never publish it unauthenticated.
- Verify authenticated profile listing, launch, VNC connection, framebuffer resize/apply, stop, and scoped denial behavior.
- Restore temporary viewport or profile changes made by the gate.

## 6. Browser and mobile evidence

At minimum, cover:

- compact portrait iPhone viewport;
- short iPhone viewport with the software keyboard represented by `visualViewport`;
- large iPhone portrait and landscape;
- touch tablet;
- fullscreen browser preview;
- session grid and switching;
- Fit, Width, Height, Phone fit, viewer zoom, and persisted framebuffer size;
- chat collapse/expand, composer, Copy, Paste, and Capture capability states;
- administrator and scoped operator views;
- no horizontal overflow and no VNC/composer overlap.

Capture screenshots only after the corresponding assertion passes. Record browser engine and emulation/device limits explicitly.

## 7. Streaming evidence

Report separately:

- VCVM-local HTTP latency;
- VCVM-local VNC WebSocket open and first RFB frame;
- Mac-to-VCVM route and whether Tailscale is direct or DERP-relayed;
- browser-observed first non-black frame;
- visual canvas-change rate, if used.

Do not call those values encoded FPS or touch-to-pixel latency unless the measurement actually covers those definitions.

## 8. Documentation, commit, and push

- Update `README.md` and `docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md` with fresh evidence.
- Keep historical evidence labeled by date/revision.
- Commit explicit paths with a conventional message.
- Push only to `fork`.
- Verify `git ls-remote fork <branch>` equals local `git rev-parse HEAD`.
- Report any generated or unrelated paths intentionally left uncommitted.
