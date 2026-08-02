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
4. The release acceptance gate must fail closed if mobile evidence omits the
   re-tap idempotent check.

## Steps completed

### Slice A (commit `66651ff`)

1. Ported early-return guard into `applyProfileViewport`.
2. Unit regression: no `update`/`stop`/`launch` when dimensions match.
3. Focused suite green.

### Slice B (commit `0f00f04`)

1. Fixed misleading UI status when live dimensions already match.
2. Double-apply App regression + MobileSplitScreen status regression.
3. Focused suites: **50/50 passed**.

### Slice C (commit `fc24100`)

1. Hardened `scripts/mobile_ui_gate.py` for PhoneFit idempotency.
2. Pure unit tests in `scripts/test_mobile_ui_gate.py` (**12/12 OK**).
3. Ported prior VCVM E2E evidence onto this branch.

### Slice D (this session)

1. Added `fullscreen Phone fit re-tap stays idempotent` to
   `REQUIRED_MOBILE_CHECKS` in `scripts/release_acceptance_gate.py` so releases
   fail closed without re-tap evidence.
2. Unit test: missing re-tap check raises GateError.
3. Live local proof on this branch SHA:
   - Vite UI `127.0.0.1:5190` (branch) proxied to Manager `127.0.0.1:18115`
   - Profile `a8b99a1f-...` (VCVM Mobile Demo) running at 390x844
   - Full View -> Viewport -> Phone fit -> re-tap Phone fit
   - re-tap: settled, no restart claim, size 390x844, content canvas=1
   - re-tap traffic: update=0 stop=0 launch=0
   - screenshot SHA-256
     `bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea`

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

# scripts/release_acceptance_gate.py
REQUIRED_MOBILE_CHECKS includes
  "fullscreen Phone fit re-tap stays idempotent"
```

## Proof (this session)

```text
Command: cd frontend && npm test -- src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx
Result:  Tests  50 passed (50)

Command: python3 scripts/test_mobile_ui_gate.py -v
Result:  Ran 12 tests  OK

Command: python3 scripts/test_release_acceptance_gate.py -v
Result:  PhoneFit re-tap required check OK
         release pass/fail suite green
         (pre-existing: python3.11 mise shim FAIL on this host only)

Command: Playwright local re-tap (Vite:5190 + Manager:18115)
Result:  PASS — 13/13 checks
         update/stop/launch on re-tap: 0/0/0
         PNG SHA-256 bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea
```

Key tests / checks:

- `keeps a running profile connected when the requested viewport is already applied`
- `stays idempotent on a second Phone-fit apply with the same dimensions`
- `does not claim a restart when live Phone-fit dimensions already match`
- `test_phone_fit_idempotent_state_*` (6 pure cases)
- `test_required_mobile_checks_include_phonefit_re_tap_idempotent`
- live: `re-tap no update/stop/launch traffic`

Changed files this session:

- `scripts/release_acceptance_gate.py`
- `scripts/test_release_acceptance_gate.py`
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.md`
- `docs/evidence/phonefit-idempotent-local-retap-2026-08-02.png`
- `docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md` (PhoneFit row)

## Prior art

`feature/phonefit-idempotent` @ `3a69d2b` / `17e7e79` VCVM browser proof
(PID stable, no update/stop/launch, zero console errors). Evidence retained under
`docs/evidence/phonefit-idempotent-vcvm-fullview-2026-07-31.png`.

## Remaining (out of this slice)

- Public VCVM deploy of this branch (transaction/rollback gate; SSH to `vcvm`
  currently refused from this workspace).
- Full `mobile_ui_gate.py` multi-viewport pack refresh after public deploy.
- Unrelated: Tailscale DERP latency, ACPX readiness.

## Verdict

**DONE** for phonefit-idempotent on this lane:

- apply-path guard
- honest status copy
- unit proof (50/50 focused)
- gate re-tap contract + pure tests (12/12)
- release acceptance requires re-tap evidence (fail-closed)
- live local re-tap proof on this SHA (no mutate traffic)
- prior VCVM E2E evidence retained on-branch
