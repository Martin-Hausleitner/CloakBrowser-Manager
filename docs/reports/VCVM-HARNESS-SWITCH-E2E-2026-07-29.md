# VCVM Harness Switch — E2E status, 2026-07-29

## Result

The compact VCVM workspace now detects Browser Use, Unbrowse, Stagehand, ACPX and the locally installed Orca CLI adapters from one disclosure menu. Each managed harness has a separate readiness check and a safe `example.com` run button. The operator can switch the selected harness without remounting the browser and can take over the live browser while a managed run is active.

Live UI: <https://vcvm.tail6a40cd.ts.net/>

Source branch: `feature/browser-use-agent-workspace`

## Verified green paths

All successful runs below used the same running VCVM profile and completed without a health override.

| Harness | Run | Result | Typed output |
| --- | --- | --- | --- |
| Browser Use | `f23ff442-9502-4c68-8feb-24365927804b` | `succeeded` | action, observations, screenshot, summary |
| Unbrowse | `cdfe4c99-9f63-4337-b144-12e4a460569b` | `succeeded` | action, status, observation, summary |
| Stagehand | `00896df6-ea32-4e0e-b2ed-cf9aa59faae2` | `succeeded` | action, status, observation, summary |

The health snapshot for the direct-egress profile is now truthful and versioned as `run-health.v2`:

- `fingerprint_consistency_score=100`
- `browser_scan_score=100`
- `measured_authenticity_score=100`
- `measured_authenticity_source=browser_signals`
- `proxy_configured=false`
- `proxychecker=skipped`
- `health_decision.allowed=true`
- no override

For a proxied profile, the policy still requires a measured Proxy-Checker authenticity source. Browser scores cannot substitute for proxy provenance.

## Fixes delivered

- `09db5ba` — refresh the profile measurement before a one-click harness test; use measured browser signals for direct-egress profiles and measured Proxy-Checker data for proxied profiles.
- `924c28a` — keep private ACP thought chunks out of public typed outputs.
- `efed94c` — stop background adapter preflight before executing a real claimed run.
- `82e3e85` — replace a Manager-rejected adapter fragment with a bounded generic status instead of failing the whole run or weakening validation.
- `a8d570f` — isolate ACP subprocess groups, terminate descendant processes and allow a 90-second authenticated adapter startup.

## Automated verification

- Backend: `871 passed`
- Frontend: `233 passed`
- Frontend production build: passed
- Health-policy regression suite: `51 passed`
- Workspace regression suite: `46 passed`
- ACPX worker/runner/E2E regression suite after hardening: `93 passed`
- Independent scoped code review: no findings
- Container: healthy, image digest `sha256:ab39eac74602cee45c3f841878368385ace5f0f196fea7745dc70e642f7bcfee`

## Open P0: Grok Build through ACPX

Grok Build is selectable and its sterile preflight reports ready, but the real MCP-backed session still times out during `sessions ensure` before the prompt reaches the browser. The most recent run was `688fd98b-5d8d-4806-8a53-3ef8e0c1c288`. Its health gate passed at score 100 and no override was used; the failure is isolated to ACPX/Grok session startup.

This path is not marked green. Browser Use, Unbrowse and Stagehand are the verified managed-browser paths. AGY can be launched in the mirrored Orca terminal with the profile-control skill injected, but its current account eligibility/login state still requires operator attention.

## Next P0

Replace the Grok ACPX `sessions ensure` dependency with a bounded adapter-specific startup contract, or repair the MCP-backed Grok session handshake. The acceptance condition is one UI-started Grok run that navigates the selected Manager profile, emits typed outputs, ends `succeeded`, and has `health_override=null`.
