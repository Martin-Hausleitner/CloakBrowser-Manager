# PhoneFit idempotent — STATUS

Date: 2026-08-02
Lane: `cbm-phonefit` (grok-4.5)
Branch: `Martin-Hausleitner/cbm-phonefit`

## Claim

1. `applyProfileViewport` is idempotent: when the requested framebuffer already
   matches the profile `screen_width` x `screen_height`, it returns success without
   calling `update`, `stop`, or `launch`. Re-tapping Phone fit on a live 390x844
   session must not disconnect noVNC.
2. The mobile viewport editor status copy must match that behavior: matching
   dimensions must not claim a live restart.

## Steps completed

### Slice A (prior commit `66651ff`)

1. Ported early-return guard into `applyProfileViewport`.
2. Unit regression: no `update`/`stop`/`launch` when dimensions match.
3. Focused suite green.

### Slice B (this session)

1. Read BRIEF + STATUS; confirmed Slice A still present and remote-synced.
2. Re-ran App tests: 9/9 (then 10/10 after double-apply regression).
3. Fixed misleading UI status when live dimensions already match:
   - idle: `Already matches - no restart`
   - applying: `Keeping live session...`
   - changed size still: `Restarts live browser to apply` / `Restarting live browser...`
4. Added double-apply App regression + MobileSplitScreen status regression.
5. Focused suites: **50/50 passed**.

## Code

```ts
// frontend/src/App.tsx — applyProfileViewport
if (profile.screen_width === width && profile.screen_height === height) return true;
```

```ts
// frontend/src/components/mobile/MobileSplitScreen.tsx — viewportStatus
// when running && viewportMatchesProfile:
//   applying -> "Keeping live session..."
//   idle     -> "Already matches - no restart"
```

## Proof (local, this session)

```text
Command: cd frontend && npm test -- src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx
Result:  Test Files  2 passed (2)
         Tests  50 passed (50)
         Duration  ~3.7s
```

Key tests:

- `keeps a running profile connected when the requested viewport is already applied`
- `stays idempotent on a second Phone-fit apply with the same dimensions`
- `does not claim a restart when live Phone-fit dimensions already match`
- `shows live viewport restart state and prevents duplicate apply submissions` (still covers real size change)

Changed files this session:

- `frontend/src/App.test.tsx`
- `frontend/src/components/mobile/MobileSplitScreen.tsx`
- `frontend/src/components/mobile/MobileSplitScreen.test.tsx`
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md`

## Prior art

`feature/phonefit-idempotent` @ `3a69d2b` / `17e7e79` already has VCVM browser proof
(PID stable, no update/stop/launch traffic, zero console errors) on a newer Manager
codebase. This lane keeps the same apply guard on `cbm-phonefit` and now aligns UI
status copy with that behavior.

## Remaining (out of this slice)

- Public VCVM deploy of this branch (transaction/rollback gate).
- Live VCVM re-proof on this exact SHA if release is cut from this branch.
- Unrelated: Tailscale DERP latency, ACPX readiness.

## Verdict

**DONE** for phonefit-idempotent on this lane: apply-path guard + honest status copy + unit proof (50/50).
