# VCVM Trusted Extension CLI — E2E Report

Date: 2026-07-29  
Runtime: [https://vcvm.tail6a40cd.ts.net/](https://vcvm.tail6a40cd.ts.net/)  
Branch: `feature/browser-use-agent-workspace`  
Commits: `0dcb18f`, `96acef3`

## Outcome

The VCVM now exposes a CLI-first management surface for its trusted browser-extension catalog. Agents can search the catalog, inspect or replace defaults, and enable or disable catalog extensions for a profile. The Manager still resolves the filesystem path itself; the CLI does not accept extension paths, CRX files, downloads, or arbitrary launch arguments.

Every valid Chrome extension catalog item now includes a canonical Chrome Web Store link. Profile inventory preserves the catalog extension ID even when the installed extension lives inside a version directory, and localized manifest placeholders fall back to the readable catalog name.

## Agent commands

```text
extensions list [--query TEXT]
extensions search QUERY
extensions defaults
extensions set-defaults [--extension-id ID ...]
extensions enable PROFILE_ID EXTENSION_ID
extensions disable PROFILE_ID EXTENSION_ID
```

The control-plane capability response advertises the same REST, CLI, and skill operations. MCP remains discovery-only for this resource.

## Automated verification

- Focused extension/control-plane suite: 36 passed.
- Inventory regression suite after the live bug fix: 14 passed.
- Full backend plus CLI suite: 899 passed, one existing Starlette/httpx deprecation warning.
- Docker image built and deployed successfully.
- Container health reached `healthy`.

## Live E2E proof

A disposable VCVM profile completed this sequence through the real Manager:

1. Create an empty Browser Use profile.
2. Enable `uBlock Origin Lite` by trusted catalog ID through the CLI.
3. Launch the real CloakBrowser profile.
4. Inspect the running profile's installed-extension inventory.
5. Verify ID, readable name, version, manifest version, and Chrome Web Store link.
6. Stop the profile.
7. Disable the extension through the CLI.
8. Delete the disposable profile.

Observed inventory:

```json
{
  "id": "ddkjiahejlhfcafbddmgiahcphecmpfh",
  "name": "uBlock Origin Lite",
  "version": "2026.714.1952",
  "manifest_version": 3,
  "store_url": "https://chromewebstore.google.com/detail/ddkjiahejlhfcafbddmgiahcphecmpfh"
}
```

The first live run uncovered an inventory bug: the version-directory name was returned as the extension ID. Regression tests were added, the inventory now keeps catalog identity, and the complete live sequence passed on the second run.

## Visible VCVM validation

The production URL was opened from the VCVM with Agent Browser at 1440×900. It loaded the compact Browser Use home screen as the bootstrap administrator without console/page errors.

![Live VCVM UI](../evidence/extension-cli-live-ui-2026-07-29.png)

## Safety boundary and remaining work

This slice deliberately does not download or execute arbitrary extensions. A future Chrome Web Store link/upload installer needs a separate approval and security gate covering signed package verification, host allowlisting, archive validation, content-addressed storage, permission review, rollback, and audit history.

The current UI remains observation-first: configuration is agent/CLI-owned, while humans inspect profiles and installed extensions visually. Dedicated icon rendering and a read-only extension detail drawer are still future UI work.
