# Contributing Contract

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Review cadence | Recheck when development workflow, CI, or release gates change |
| Related docs | [GOVERNANCE.md](GOVERNANCE.md), [TESTING.md](TESTING.md), [SECURITY.md](SECURITY.md) |

## Contribution Goal

Contributions should move one scoped product claim from plan to verified evidence without weakening ownership, security, or release truth.

## Before Editing

1. Identify the active CBM GitHub issue and its matching local `bd` execution item.
2. Read the relevant vision, architecture, security, and testing sections.
   For release-significant work, also read [VISION_LIFECYCLE.md](VISION_LIFECYCLE.md)
   and confirm that the linked active plan and current-truth status still apply.
3. Check current worktree status and preserve unrelated edits.
4. Define the smallest file ownership surface.
5. Choose the verification command before changing files.

## During Implementation

- Prefer existing patterns and utilities.
- Keep diffs small and reversible.
- Add or update tests before relying on UI or release claims.
- Keep secrets out of prompts, args, logs, snapshots, and screenshots.
- Use typed outputs and stable evidence names for agent-visible behavior.
- Label unavailable or unproven states honestly in UI and docs.

## Documentation Changes

Docs changes should:

- include status, owner, and review metadata for contract files;
- cross-link relevant root docs and plans;
- distinguish planned, implemented, locally tested, live-proven, historical, blocked, and known gaps;
- cite external inspiration or official references when they shaped structure;
- pass markdown link, placeholder, secret, and whitespace checks.
- update the active-plan pointer or current-truth block when the authoritative
  plan, evidence posture, deployment revision, or stop condition changes.

## Verification Expectations

Use the smallest proof that can validate the claim, then broaden when the blast radius requires it:

- docs-only: link/placeholder/secret scan plus `git diff --check`;
- backend: targeted pytest and negative auth/redaction tests;
- frontend: targeted Vitest/React tests and build;
- UI behavior: browser screenshots or Playwright proof;
- mobile/fullscreen: mobile gate with keyboard and Phone Fit evidence;
- release: full release gate and redacted artifact review.

## Pull Request or Handoff Summary

Report:

- changed files;
- product claim;
- tests and commands run;
- evidence artifacts or screenshots;
- known gaps and unproven claims;
- stop condition reached;
- actions not taken, such as no commit, push, deploy, or secret exposure.

Verified authorized work is pushed only to Martin's fork. The CloakHQ upstream
is a read-only comparison source.

## Known Gaps

- This docs slice does not add CI enforcement for root docs.
- Project-state handoff automation is planned under CBM-001.

## Inspirations

Inspired structurally by [Block Buzz CONTRIBUTING.md](https://github.com/block/buzz/blob/main/CONTRIBUTING.md), adapted to this repository's CBM tickets, evidence gates, and secret-safe browser automation workflow.
