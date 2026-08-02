# PhoneFit idempotent — STATUS

Date: 2026-08-02
Lane: `cbm-phonefit` (grok-4.5)
Branch: `Martin-Hausleitner/cbm-phonefit`

## Claim

`applyProfileViewport` is idempotent: when the requested framebuffer already
matches the profile `screen_width` x `screen_height`, it returns success without
calling `update`, `stop`, or `launch`. Re-tapping Phone fit on a live 390x844
session must not disconnect noVNC.

## Step completed this session

1. Read BRIEF + existing feature-branch evidence pattern
   (`feature/phonefit-idempotent` had the same fix on a newer tree).
2. Ported the minimal guard into this lane's base (`App.tsx`).
3. Locked behavior with a unit regression in `App.test.tsx`.
4. Ran focused suite: **9/9 passed**.

## Code change

```ts
// frontend/src/App.tsx — applyProfileViewport
if (profile.screen_width === width && profile.screen_height === height) return true;
```

Regression: `keeps a running profile connected when the requested viewport is already applied`
asserts `update`/`stop`/`launch` are never called when width/height already match.

## Proof (local, this session)

```text
Command: cd frontend && npm test -- src/App.test.tsx
Result:  Test Files  1 passed (1)
         Tests  9 passed (9)
         Duration  ~2.7s
```

Changed files:

- `frontend/src/App.tsx` (idempotent early return)
- `frontend/src/App.test.tsx` (regression)
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md` (this file)

## Prior art / related evidence

The superpowers worktree `feature/phonefit-idempotent` @ `3a69d2b` /
`17e7e79` already recorded VCVM browser proof (PID stable, no update/stop/launch
traffic, zero console errors) on a newer Manager codebase. This lane re-applies
the same TDD slice on the current `cbm-phonefit` checkout so the guard is present
here and re-proven by unit tests.

## Remaining (out of this slice)

- Public VCVM deploy of this branch (transaction/rollback gate).
- Live VCVM re-proof on this exact SHA if release is cut from this branch.
- Unrelated: Tailscale DERP latency, ACPX readiness.

## Verdict

**DONE** for the phonefit-idempotent code + unit-proof slice on this lane.
