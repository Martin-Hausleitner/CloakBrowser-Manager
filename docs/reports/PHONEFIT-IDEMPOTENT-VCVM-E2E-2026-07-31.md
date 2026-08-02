# PhoneFit idempotency — VCVM E2E

Date: 31 July 2026
Branch: `feature/phonefit-idempotent`
Commit: `3a69d2b5911759db585dc2416fa9c466e59e1f74`

## Outcome

PhoneFit no longer stops and relaunches a running CloakBrowser profile when its
requested framebuffer already equals the saved `screen_width` and
`screen_height`. This removes a real noVNC disconnect from the common
`390 x 844 -> Phone fit` path while preserving the existing restart for actual
viewport changes.

The public VCVM release was not overwritten: the reviewed deploy wrapper still
fails closed while the transaction/rollback gate is pending. The browser proof
ran on an isolated Vite instance on the VCVM and proxied to the real Manager at
`127.0.0.1:18115`.

## Reproduction before the fix

On the deployed UI, opening mobile Full View, expanding `Viewport`, and choosing
`Phone fit` for the already-`390 x 844` profile returned `Saved`, but the page
console recorded:

```text
Tried changing state of a disconnected RFB object
```

The old helper always executed `update -> stop -> launch` for a running profile,
even when both dimensions were unchanged.

## Test-driven fix

1. RED: a new `applyProfileViewport` regression test expected unchanged
   dimensions to return `true` without calling `update`, `stop`, or `launch`;
   it failed before the code change.
2. GREEN: after the one-line idempotency guard, the focused test passed.
3. Regression suite: **152 frontend tests passed**.
4. Production build: passed. The existing large-bundle warning remains a
   separate performance backlog item.
5. Independent review: `APPROVE`, no P0–P3 findings.
6. Staged Gitleaks scan: no findings.

## Real VCVM browser proof

The isolated UI controlled the real running profile
`a8b99a1f-bd77-4249-917f-0ad681ea5519` through the existing Manager/VNC path.
After Full View → Viewport → Phone fit:

- saved and displayed viewport: `390 x 844`;
- live canvas count: `1`;
- document width/client width: `390 / 390` (no horizontal overflow);
- CloakBrowser PID and start time: identical before and after;
- profile traffic after the click: GET polling only, no update/stop/launch;
- page errors: `0`;
- console messages: `0`;
- screenshot: 390 × 844 PNG, SHA-256
  `b807c23edcefaabb5ec0c52456617e9e0bfeba693201aebec2dcda089a4bb40c`.

![Fixed PhoneFit in mobile Full View](../evidence/phonefit-idempotent-vcvm-fullview-2026-07-31.png)

## Remaining boundaries

- The public URL still serves the earlier release until the reviewed
  transaction and rollback apply path is green.
- Direct Mac-to-VCVM Tailscale traffic currently uses `DERP(nue)`. Measured
  Manager loopback `/api/profiles` was p50 5.704 ms / p95 8.787 ms, while the
  public Tailnet HTTPS route was p50 269.853 ms / p95 432.817 ms. Restoring a
  direct Tailscale path is the highest-impact latency action.
- Browser Use already attaches to the Manager-granted profile CDP. ACPX and the
  other browser-tool workers remain stale on the live release and need separate
  readiness/E2E gates rather than fallback claims.
