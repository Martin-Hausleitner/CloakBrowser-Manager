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
   Phone-fit re-tap stays idempotent (no restart claim, single canvas, same size,
   and zero update/stop/launch traffic when counters are available).
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
2. Pure unit tests in `scripts/test_mobile_ui_gate.py`.
3. Ported prior VCVM E2E evidence onto this branch.

### Slice D (commit `d14e0ec`)

1. Added `fullscreen Phone fit re-tap stays idempotent` to
   `REQUIRED_MOBILE_CHECKS` in `scripts/release_acceptance_gate.py`.
2. Unit test: missing re-tap check raises GateError.
3. Live local Playwright re-tap proof (traffic 0/0/0).

### Slice E (this session)

1. Gate re-tap now installs page-level mutate counters (`fetch` + XHR) and requires
   `update=0 stop=0 launch=0` via `phone_fit_idempotent_state`.
2. Pure unit tests: traffic fail-closed + counter JS contract (**14/14 OK**).
3. Durable proof script: `scripts/phonefit_idempotent_retap_proof.py`.
4. Fresh live re-proof on Vite:5190 + Manager:18115:
   - re-tap settled, no restart claim, size 390x844, canvas=1
   - re-tap traffic: update=0 stop=0 launch=0
   - PNG SHA-256 `bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea`

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
phone_fit_mutate_counter_install_js()
phone_fit_mutate_counter_read_js()
phone_fit_idempotent_state(..., update_count=, stop_count=, launch_count=)
# check: "fullscreen Phone fit re-tap stays idempotent"

# scripts/release_acceptance_gate.py
REQUIRED_MOBILE_CHECKS includes
  "fullscreen Phone fit re-tap stays idempotent"
```

## Proof (this session)

```text
Command: python3 scripts/test_mobile_ui_gate.py -v
Result:  Ran 14 tests  OK

Command: python3 scripts/test_release_acceptance_gate.py -v
Result:  PhoneFit re-tap required check OK
         release pass/fail suite green
         (pre-existing: python3.11 mise shim FAIL on this host only)

Command: cd frontend && npm test -- src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx
Result:  Tests  50 passed (50)

Command: python3 scripts/phonefit_idempotent_retap_proof.py
         (Vite:5190 + Manager:18115, profile a8b99a1f-...)
Result:  PASS — 11/11 checks
         update/stop/launch on re-tap: 0/0/0
         PNG SHA-256 bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea
```

Key tests / checks:

- `keeps a running profile connected when the requested viewport is already applied`
- `stays idempotent on a second Phone-fit apply with the same dimensions`
- `does not claim a restart when live Phone-fit dimensions already match`
- `test_phone_fit_idempotent_state_*` (including traffic zero-required)
- `test_phone_fit_mutate_counter_js_classifies_profile_mutate_paths`
- `test_required_mobile_checks_include_phonefit_re_tap_idempotent`
- live: `re-tap no update/stop/launch traffic`

Changed files this session:

- `scripts/mobile_ui_gate.py`
- `scripts/test_mobile_ui_gate.py`
- `scripts/phonefit_idempotent_retap_proof.py`
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.json`
- `docs/evidence/phonefit-idempotent-local-retap-2026-08-02.png`

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
- gate re-tap contract + traffic counters + pure tests (14/14)
- release acceptance requires re-tap evidence (fail-closed)
- durable live re-tap proof script + fresh local PASS (no mutate traffic)
- prior VCVM E2E evidence retained on-branch
