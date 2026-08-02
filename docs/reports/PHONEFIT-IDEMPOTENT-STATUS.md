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
   re-tap idempotent check **or** omits mutate-traffic zeros in that check's
   evidence payload **or** omits proof that the page mutate counters were
   installed (false-zero guard).
5. The durable live proof script must use the **same** page mutate counters +
   `phone_fit_idempotent_state` contract as the gate (not Playwright-only),
   dual-witness zeros (page + Playwright), and emit release-shaped evidence
   (`trafficChecked`, `trafficOk`, `counterInstalled`, zero counts).

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

### Slice E (commit `7a110c4`)

1. Gate re-tap installs page-level mutate counters (`fetch` + XHR) and requires
   `update=0 stop=0 launch=0` via `phone_fit_idempotent_state`.
2. Pure unit tests: traffic fail-closed + counter JS contract (**14/14 OK**).
3. Durable proof script: `scripts/phonefit_idempotent_retap_proof.py`.
4. Live re-proof: traffic 0/0/0 + PNG.

### Slice F (commit `33dd91b`)

1. Release acceptance now **inspects re-tap evidence** and fails closed unless
   `trafficChecked=true`, `trafficOk=true`, and `updateCount/stopCount/launchCount`
   are all `0` (`assert_phone_fit_re_tap_traffic_evidence`).
2. Unit tests: missing evidence, non-zero launch, and healthy path.
3. Proof script also asserts Manager profile markers stay put across re-tap:
   status running, screen 390x844, `updated_at` unchanged, VNC port unchanged.
4. Live re-proof: **16/16 PASS**.

### Slice G (commit `b0f090a`)

1. Mutate-counter **read** always reports `installed` from
   `window.__phoneFitMutateInstalled` (install already set the flag).
2. `phone_fit_idempotent_state(..., counter_installed=)` fails closed when
   hooks are explicitly missing (`counter_installed=False`) so false-zero
   traffic cannot pass.
3. Live re-tap path always traffic-checks and passes
   `counter_installed=bool(mutate.get("installed"))` — empty eval cannot
   look like UI-only pass.
4. Release acceptance requires `counterInstalled is True` in re-tap evidence
   (in addition to traffic zeros).
5. Unit tests: **15/15** mobile gate; release phonefit cases green
   (pre-existing: python3.11 mise shim FAIL on this host only).
6. Fresh live re-proof: traffic 0/0/0, profile stable.

### Slice H (this session)

1. Durable proof script imports `mobile_ui_gate` page counter install/read +
   `phone_fit_idempotent_state` (parity with live gate path).
2. Re-tap installs page hooks before click; fails if install returns false.
3. Dual-witness traffic: page counters **and** Playwright request intercept
   must both be zero; missing `counterInstalled` fails closed.
4. Emits canonical check name `fullscreen Phone fit re-tap stays idempotent`
   with release-shaped evidence (`build_retap_gate_evidence`).
5. Unit test: proof builder evidence satisfies
   `assert_phone_fit_re_tap_traffic_evidence`; missing hooks / page leak /
   Playwright leak all fail.
6. Fresh live re-proof: **19/19 PASS**, page 0/0/0 + Playwright 0/0/0,
   `counterInstalled=true`, profile `updated_at` + `vnc_ws_port` stable.

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
phone_fit_mutate_counter_install_js()  # sets __phoneFitMutateInstalled
phone_fit_mutate_counter_read_js()     # returns {installed, update, stop, launch}
phone_fit_idempotent_state(..., counter_installed=)
# check: "fullscreen Phone fit re-tap stays idempotent"

# scripts/release_acceptance_gate.py
PHONE_FIT_RE_TAP_CHECK in REQUIRED_MOBILE_CHECKS
assert_phone_fit_re_tap_traffic_evidence(checks)
# requires trafficChecked + trafficOk + counterInstalled + zeros

# scripts/phonefit_idempotent_retap_proof.py
build_retap_gate_evidence(...)  # page + Playwright dual witness
# live: install page counters, re-tap, emit gate-shaped check
```

## Proof (this session)

```text
Command: python3 scripts/test_mobile_ui_gate.py -v
Result:  Ran 15 tests  OK

Command: python3 scripts/test_release_acceptance_gate.py -v
Result:  PhoneFit re-tap + proof-builder contract OK
         (pre-existing: python3.11 mise shim FAIL on this host only)

Command: python3 scripts/phonefit_idempotent_retap_proof.py
         (Vite:5190 + Manager:18115, profile a8b99a1f-...)
Result:  PASS — 19/19 checks
         page update/stop/launch: 0/0/0, counterInstalled=true
         playwright update/stop/launch: 0/0/0
         gate check evidence release-compatible
         profile updated_at unchanged; vnc_ws_port 6100 unchanged
         PNG SHA-256 bae3257260d6a5336a787907e8a59916cc870c543794ee1411bb85d2b9a862ea
```

Key tests / checks:

- `keeps a running profile connected when the requested viewport is already applied`
- `stays idempotent on a second Phone-fit apply with the same dimensions`
- `does not claim a restart when live Phone-fit dimensions already match`
- `test_phone_fit_idempotent_state_*` (including traffic zero-required)
- `test_phone_fit_idempotent_state_fails_when_counter_not_installed`
- `test_phone_fit_mutate_counter_js_classifies_profile_mutate_paths`
- `test_required_mobile_checks_include_phonefit_re_tap_idempotent`
- `test_phone_fit_re_tap_requires_zero_mutate_traffic_evidence`
- `test_proof_builder_evidence_satisfies_release_traffic_contract`
- live: `re-tap page counters installed flag`
- live: `re-tap no update/stop/launch traffic` (page + Playwright)
- live: `fullscreen Phone fit re-tap stays idempotent` (gate-shaped)
- live: `profile updated_at unchanged after re-tap`
- live: `profile vnc/cdp endpoints unchanged after re-tap`

Changed files this session:

- `scripts/phonefit_idempotent_retap_proof.py`
- `scripts/test_release_acceptance_gate.py`
- `docs/reports/PHONEFIT-IDEMPOTENT-STATUS.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.md`
- `docs/reports/PHONEFIT-IDEMPOTENT-LOCAL-PROOF-2026-08-02.json`
- `docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md`

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
- unit proof (focused frontend; 15/15 mobile gate)
- gate re-tap contract + traffic counters + installed-hook guard
- release acceptance requires re-tap name, mutate-traffic zeros, **and**
  `counterInstalled`
- durable live re-tap proof uses **same page counters** as gate + Playwright
  dual witness + release-shaped evidence (19 checks)
- prior VCVM E2E evidence retained on-branch
