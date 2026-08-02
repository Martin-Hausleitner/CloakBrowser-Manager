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
3. The mobile UI gate must accept match-aware status copy and assert a second
   Phone-fit re-tap stays idempotent (no restart claim, single canvas, same size).

## Steps completed

### Slice A (commit `66651ff`)

1. Ported early-return guard into `applyProfileViewport`.
2. Unit regression: no `update`/`stop`/`launch` when dimensions match.
3. Focused suite green.

### Slice B (commit `0f00f04`)

1. Fixed misleading UI status when live dimensions already match.
2. Double-apply App regression + MobileSplitScreen status regression.
3. Focused suites: **50/50 passed**.

### Slice C (this session)

1. Read BRIEF + STATUS; re-verified Slice A/B still green (50/50 frontend, full 138).
2. VCVM SSH unreachable from this host (`Connection refused`) — no live re-deploy.
3. Hardened `scripts/mobile_ui_gate.py` for PhoneFit idempotency:
   - `fullscreen_viewport_apply_settled_js()` accepts `Saved` or
     `Already matches - no restart`, and stays false while applying
     (`Keeping live session...` / `Restarting...`).
   - After first Phone fit on `iphone-14-portrait`, re-taps Phone fit and checks
     `phone_fit_idempotent_state` (settled, no restart claim, size match, 1 canvas).
4. Added pure unit tests in `scripts/test_mobile_ui_gate.py` (**12/12 OK**).
5. Ported prior VCVM E2E evidence from `feature/phonefit-idempotent`:
   - `docs/reports/PHONEFIT-IDEMPOTENT-VCVM-E2E-2026-07-31.md`
   - `docs/evidence/phonefit-idempotent-vcvm-fullview-2026-07-31.png`
   - SHA-256 `b807c23edcefaabb5ec0c52456617e9e0bfeba693201aebec2dcda089a4bb40c`

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

```py
# scripts/mobile_ui_gate.py
fullscreen_viewport_apply_settled_js()
phone_fit_idempotent_state(...)
# check: "fullscreen Phone fit re-tap stays idempotent"
```

## Proof (local, this session)

```text
Command: cd frontend && npm test -- src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx
Result:  Test Files  2 passed (2)
         Tests  50 passed (50)

Command: python3 scripts/test_mobile_ui_gate.py -v
Result:  Ran 12 tests in 0.002s  OK
         (includes 6 PhoneFit settle/idempotent pure checks)

Command: cd frontend && npm test -- --run
Result:  Test Files  14 passed (14)
         Tests  138 passed (138)
```

Key tests:

- `keeps a running profile connected when the requested viewport is already applied`
- `stays idempotent on a second Phone-fit apply with the same dimensions`
- `does not claim a restart when live Phone-fit dimensions already match`
- `test_phone_fit_idempotent_state_passes_when_match_copy_and_single_canvas`
- `test_phone_fit_idempotent_state_fails_when_restart_is_claimed`
- `test_phone_fit_idempotent_state_fails_when_still_applying`

Changed files this session:

- `scripts/mobile_ui_gate.py`
- `scripts/test_mobile_ui_gate.py`
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-VCVM-E2E-2026-07-31.md` (ported)
- `docs/evidence/phonefit-idempotent-vcvm-fullview-2026-07-31.png` (ported)

## Prior art

`feature/phonefit-idempotent` @ `3a69d2b` / `17e7e79` already has VCVM browser proof
(PID stable, no update/stop/launch traffic, zero console errors) on a newer Manager
codebase. Evidence is now copied into this branch under `docs/evidence/` and
`docs/reports/PHONEFIT-IDEMPOTENT-VCVM-E2E-2026-07-31.md`.

## Remaining (out of this slice)

- Public VCVM deploy of this branch (transaction/rollback gate; SSH to `vcvm`
  currently refused from this workspace).
- Live VCVM re-proof on this exact SHA if release is cut from this branch.
- Unrelated: Tailscale DERP latency, ACPX readiness.

## Verdict

**DONE** for phonefit-idempotent on this lane:

- apply-path guard
- honest status copy
- unit proof (50/50 focused, 138/138 frontend)
- gate re-tap idempotent contract + pure tests (12/12)
- prior VCVM E2E evidence retained on-branch
