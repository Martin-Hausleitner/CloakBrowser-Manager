# Agent Instructions

Product acceptance is tracked in the CBM GitHub issues linked from
`docs/superpowers/plans/2026-07-27-universal-agent-browser-control-plane-masterplan.md`.
Use **bd** (beads) as the local execution mirror. Run `bd onboard` to get started.

Read `VISION.md`, `ARCHITECTURE.md`, `TESTING.md`, `SECURITY.md`, and
`GOVERNANCE.md` before release-significant work.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

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

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- Push only to `https://github.com/Martin-Hausleitner/CloakBrowser-Manager.git`
- Treat `CloakHQ/CloakBrowser-Manager` as read-only; never push or merge there
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
