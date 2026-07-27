# Testing and Evidence Contract

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck when a CBM ticket changes acceptance, release gates, or evidence storage |
| Related docs | [VISION.md](VISION.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md) |

## Testing Goal

Testing must prove the product claim being made. Unit tests, integration tests, browser screenshots, mobile gates, VCVM checks, and release artifacts each answer different questions. They cannot be substituted for each other without saying so.

## Evidence Labels

Use these labels consistently:

- `planned`: ticket or design exists, implementation not complete;
- `implemented`: code exists and focused checks pass;
- `locally tested`: local suite or smoke check passed;
- `live-proven on VCVM`: deployed VCVM path passed with artifacts;
- `mobile-proven`: mobile viewport gate passed with screenshots and no overlap;
- `physical-device-proven`: real device and browser evidence exists;
- `blocked`: prerequisite unavailable and not bypassed;
- `historical`: useful prior evidence, not a fresh release claim.

## Required Gates by Change Type

| Change type | Minimum proof |
| --- | --- |
| Docs-only contract | Markdown link/placeholder/secret scan plus `git diff --check` |
| Backend API/state | Targeted pytest, auth/negative cases, redaction checks, migration checks if schema changes |
| Frontend UI | Targeted Vitest/React tests, production build, browser screenshot or Playwright proof for visible flows |
| Mobile/fullscreen/PhoneFit | Mobile gate with keyboard, fullscreen, Phone Fit, session switching, non-overlap screenshots |
| Browser worker/harness | Contract tests, worker heartbeat/claim/cancel tests, real managed-profile E2E before live claim |
| Secrets/proxy/passkeys | Negative leak tests, broker/provider tests, redacted artifact scan, no raw credential serialization |
| VCVM release | Deployment receipt, health checks, screenshots, route/latency labels, rollback/stop condition |
| Workflow engine | Benchmark against the same scenario, cancellation/retry/replay proof, resource measurements, license and ops review |

## Mobile and Fullscreen Contract

Mobile proof must include:

- narrow and tall phone viewport;
- short phone viewport with keyboard open;
- fullscreen browser controls;
- Phone Fit application and restoration behavior;
- session grid/switcher;
- visible typed outputs or restored run state;
- no browser/composer/control overlap;
- explicit browser/device label, especially when Chromium emulation is not Safari.

## Evidence Hygiene

- Store screenshots and reports as artifacts when they are per-run evidence.
- Redact tokens, cookies, proxy credentials, authorization headers, local-only paths, private tunnel endpoints, and raw account data.
- Do not convert a human visual check into an automated claim unless the evidence is attached.
- Do not use historical numbers as current release proof.

## Traceability Checklist

Before calling a CBM ticket complete, confirm:

- the ticket maps to this vision and [ARCHITECTURE.md](ARCHITECTURE.md);
- acceptance criteria are explicit in the active plan or issue;
- targeted tests prove the changed behavior;
- negative tests cover forbidden behavior;
- evidence artifacts are redacted and named;
- release notes or reports state remaining gaps.

## Known Gaps

- A first-class feature-presence acceptance manifest is planned in CBM-003.
- Physical iPhone Safari proof is pending; Chromium viewport evidence must remain labeled as such.
- Direct Tailnet route evidence is pending until fresh route proof exists.

## Inspirations

Inspired structurally by [Block Buzz TESTING.md](https://github.com/block/buzz/blob/main/TESTING.md), adapted to CloakBrowser Manager's browser, mobile, VCVM, and agent-harness evidence gates.
