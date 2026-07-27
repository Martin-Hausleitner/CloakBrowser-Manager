# Agent Instructions

Product acceptance is tracked in the CBM GitHub issues linked from
`docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md`.
Use **bd** (beads) as the local execution mirror. Run `bd onboard` to get started.

Read `VISION.md`, `VISION_LIFECYCLE.md`, `ARCHITECTURE.md`, `TESTING.md`,
`SECURITY.md`, and `GOVERNANCE.md` before release-significant work. Confirm that
the active plan and current-truth block still match the repository, environment,
and ticket before acting.

For a new project, start from `docs/templates/PROJECT-VISION-TEMPLATE.md`, link
the vision pack from its `AGENTS.md`, and keep exactly one authoritative active
plan with explicit replacement and stop conditions.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Changed Work)

When a task changes tracked project files and the current task authorizes landing
those changes, complete all steps below. Changed work is not complete until the
intended fork push succeeds.

Read-only audits, reviews, diagnostics, and status reports do not create commits
or pushes. They end with an evidence-backed report and an explicit statement that
no files were changed. A user instruction that forbids a commit, push, or external
write always takes precedence.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push fork HEAD:feature/browser-use-agent-workspace
   git status  # MUST show the intended fork branch is up to date
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES FOR CHANGED WORK:**
- Authorized changed work is NOT complete until `git push` succeeds
- Push only to `https://github.com/Martin-Hausleitner/CloakBrowser-Manager.git`
- Treat `CloakHQ/CloakBrowser-Manager` as read-only; never push or merge there
- NEVER strand authorized changed work locally when the task requires landing it
- NEVER say "ready to push when you are" for an already authorized landing
- If push fails, resolve and retry until it succeeds
