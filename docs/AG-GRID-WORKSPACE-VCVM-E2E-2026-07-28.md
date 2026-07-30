# AG Grid Workspace and ACPX VCVM E2E Report

Date: **28 July 2026**  
Branch: `feature/browser-use-agent-workspace`  
Release commit: `83cad83ba79d2e7369dfb85cee123caab7cc32ec`  
VCVM release: `release-20260728-83cad83-grid`  
Tailnet HTTPS route: `https://vcvm.tail6a40cd.ts.net`

## Outcome

The central operational workspace is live on VCVM and follows the dense, sortable table pattern demonstrated by the official [AG Grid example](https://www.ag-grid.com/example/) while staying on [AG Grid Community](https://www.ag-grid.com/react-data-grid/getting-started/). The authenticated desktop UI exposes four adjacent table views:

1. **Profiles**
2. **Accounts & 2FA**
3. **Proxies**
4. **Sessions**

The same release also completed a real Antigravity/ACPX/Claude browser task through the split live workspace.

## Table acceptance

| View | Verified columns and behavior | Result |
| --- | --- | --- |
| Profiles | Status, name, project/folder, harness, platform, viewport, proxy, updated time, actions; quick filter; selected row; pagination | Pass |
| Accounts & 2FA | Profile, project, harness, session, auth, Bitwarden, Keypad, notes, actions; needs-2FA filter; no credentials or cookies | Pass |
| Proxies | State, masked label, country, redacted credential indicator, latency, risk, authenticity, timezone, locale, last check | Pass |
| Sessions | Profile, title, project, workflow, status, retention and activity aggregated only from visible profiles | Pass |

AG Grid Enterprise-only row grouping, pivoting and server-side row model were not added. They are not required for the current dataset sizes and would introduce a commercial-license boundary. See [AG Grid licensing](https://www.ag-grid.com/license-pricing/).

## Browser-path evidence

- Authenticated test identity: `martin-demo`.
- The account is a dedicated VCVM demonstration administrator so all four operational views are visible without exposing the bootstrap administrator token.
- The password is not stored in Git, this report, browser logs or screenshots. It remains in the local macOS Keychain.
- Profiles grid showed four real profiles and the selected `Antigravity Browser Use MVP` row.
- Quick-filtering for `Antigravity` reduced the data rows to exactly one and preserved row selection.
- Desktop document width matched the viewport; no horizontal page overflow was present.
- Browser console warning/error capture returned an empty list.
- Proxy rows remained masked; the UI never returned a full proxy URL or password.

## Mobile fallback

The same authenticated page was tested with an explicit `390 x 844` viewport:

```json
{
  "appState": "mobile.workspace",
  "grids": 0,
  "height": 844,
  "scrollHeight": 844,
  "scrollWidth": 390,
  "width": 390
}
```

This proves the desktop AG Grid is not mounted on the phone breakpoint. The compact browser task workspace fills the visual viewport without horizontal overflow, preserves the `390 x 844` profile viewport, and exposes browser, tools and chat controls through the mobile command dock.

## Live ACPX/Claude browser E2E

| Field | Evidence |
| --- | --- |
| Profile | `Antigravity Browser Use MVP` |
| Profile ID | `835993a6-bf4f-4e20-9d6d-82f3740ca3de` |
| Harness | Antigravity preset backed by ACPX/Claude |
| Run ID | `532dae1a-3775-47d2-9e83-e46bc9464156` |
| Task | Open `https://example.com` and report the page title |
| Initial policy result | `blocked_health` because measured authenticity was below the automation threshold |
| Operator action | Used the visible, explicit `Run with override` test control |
| Final status | `succeeded` |
| Final answer | Page title: `Example Domain` |
| Typed UI output | Status, action, observation, metric and summary cards |
| Session closure | Reported cleanly closed |
| Last visible usage | 32,665 tokens |

The health block is retained as a positive policy result, not hidden as a failure. The override was used only for the harmless `example.com` acceptance target.

## Release and runtime evidence

- Tailscale route terminated at the release-bound Manager on VCVM.
- Manager health returned `ok`.
- Release marker, container revision and source binding all matched `83cad83ba79d2e7369dfb85cee123caab7cc32ec`.
- Browser Use and ACPX services were active and bound to the promoted release paths.
- The live split workspace reported the managed profile as connected.
- Observed Manager API latency in the live developer bar ranged from approximately 56 to 83 ms during acceptance.

## Known gaps

1. The in-app browser screenshot command timed out twice even though DOM, interaction, task and console evidence passed. No new screenshot is claimed or committed for this run.
2. Browser Use through Cursor remains provider-auth blocked; this report proves the Antigravity/ACPX/Claude route, not every provider.
3. Physical iPhone Safari remains an external acceptance gate. The `390 x 844` result is Chromium responsive evidence, not relabeled Safari proof.
4. A global, unbounded sessions endpoint was intentionally not invented. The Sessions view aggregates bounded history only from profiles visible to the signed-in identity.

## Security invariants

- No administrator bootstrap token is shown to the browser user.
- No password, cookie, passkey, one-time code, full proxy address or proxy credential is included in the UI evidence.
- Account and proxy views are redacted control-plane metadata, not a secret-retrieval interface.
- Server-side authorization remains authoritative; UI tab visibility is not treated as permission enforcement.
