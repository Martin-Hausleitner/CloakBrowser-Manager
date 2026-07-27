# Browser-Use VCVM Acceptance

**Date:** 2026-07-27
**Issue:** CBM-008 / GitHub #10
**Result:** Blocked, fail-closed

## Summary

The PR worktree is at `8f8fd1e`. The separate authorized VCVM checkout is at
`50a9e43` on `feature/browser-use-agent-workspace`, and the Browser-Use worker
systemd `WorkingDirectory` points at that checkout. The running Manager runtime
commit was not proven in this lane because no OCI revision label or explicit
runtime manifest was captured. The Manager container is healthy and the
Browser-Use worker unit is active, but this live runtime cannot prove the PR
head.

The prior low-disk blocker is resolved: read-only inventory reported
`130335172` KiB free on the target volume, approximately 124 GiB. No deployment,
restart, prune, cleanup, or new live Browser-Use run was performed in this lane.

## Evidence

- Redacted machine-readable bundle:
  `docs/evidence/browser-use-vcvm-acceptance-2026-07-27.json`
- Local expected commit / PR head: `8f8fd1e`
- Live VCVM checkout commit: `50a9e43`
- Live Manager runtime commit: unavailable; no OCI revision label or explicit
  runtime manifest captured
- Live worker WorkingDirectory commit: `50a9e43`
- Manager health: `/health` returned `{"ok": true}`
- Manager container: `cloakbrowser-manager-vcvm` healthy
- Worker service: `cloakbrowser-browser-use-worker.service` active,
  `NRestarts=0`
- Worker unit shape: venv Python runs `-m scripts.browser_use_worker` with
  `--token-file`

## Acceptance Gate

`scripts/vcvm_browser_use_acceptance.py` is offline/replay safe by default. It
fails when any required proof is missing or mismatched:

- Manager and worker commits must match the expected PR head.
- The seven Browser-Use workspace migrations must be present.
- Browser Use must match the pinned `0.13.6` runtime.
- The worker must be active and launched via token-file systemd unit shape.
- Run evidence must prove profile binding, viewport, CDP target, first action,
  typed output ordering, screenshot hash, current URL, terminal success, and
  cancellation fencing.
- Health overrides produce `degraded`, never `passed`.

The current redacted bundle is expected to fail because Manager runtime commit
proof is unavailable, the worker checkout proves version skew, and no fresh
run-level acceptance evidence exists for `8f8fd1e`.

## Blocker

Release/version skew is the current blocker. A future acceptance pass needs the
already-authorized VCVM runtime to run the PR head, then a fresh bounded
Browser-Use run and cancellation proof captured into the evidence bundle.
