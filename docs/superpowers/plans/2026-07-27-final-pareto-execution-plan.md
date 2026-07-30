# CloakBrowser Agent Workbench — Final Pareto Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for one ticket at a time. Every ticket must pass spec review before code-quality review. No live VCVM mutation is allowed until CBM-022 is green and the operator preflight proves the exact release.

**Goal:** Deliver a compact, trustworthy browser-agent workbench in which any supported ACP/ACPX or Browser Use harness can operate one explicitly bound CloakBrowser profile, while every state transition, visual feature, deployment and rollback is independently provable.

**Architecture:** CloakBrowser Manager remains the policy and browser-profile control plane. ACP/ACPX is the structured agent-session boundary; Browser Use is one browser-automation worker; a durable workflow engine is an optional outer coordinator only after a measured pilot. Secrets, cookies and system/browser fingerprints remain node-local and are never replicated as workflow state.

**Tech stack:** FastAPI, SQLite, React/Vite, Chromium/CDP, VNC/CDP streaming, Browser Use, ACP/ACPX, MCP/CLI skills, Docker Compose, user-systemd, Tailscale and a later measured Hatchet/Temporal/Restate pilot.

---

## 1. Why this plan exists

The 27 July engineering transcript describes one system problem, not dozens of unrelated feature requests: operators cannot safely delegate because feature visibility, runtime state, worktree ownership, login capability, UI navigation and deployment state are not represented by one trustworthy contract.

The product therefore optimizes for five outcomes:

1. **No disappearing features.** A feature is not delivered until route, API, visible UI state and screenshot evidence are tied to the same commit.
2. **No context amnesia.** Every handoff records repository, worktree, active ticket, completed receipts, unmerged changes, next action, forbidden actions and stop condition.
3. **No accidental deployment path.** `hot_reload`, `staging` and `vcvm_release` are explicit mutually exclusive modes. A local UI iteration must never enter the release pipeline.
4. **No invisible agent work.** Runs expose typed outputs, durable status, replay links and exact profile/harness bindings rather than only free-form Markdown.
5. **No human secret copying by default.** Agents receive a bounded capability to use an origin-bound secret, not plaintext credentials in their prompt or logs.

## 2. Pareto decision

The shortest credible path is:

```text
Canonical state + ticket intake + integration baseline + feature presence
        -> atomic VCVM release and rollback
        -> one real Browser Use success + cancellation proof
        -> compact desktop/mobile workspace polish
        -> ACPX + Browser Use durable workflow pilot
        -> secret-use broker and advanced identity flows
        -> federation/DAO receipts only after the single-node system is boring
```

This deliberately rejects the tempting order of installing a workflow platform, Kubernetes, DAO governance and multiple vaults before one browser task can be released and reproduced.

## 3. Non-negotiable system boundaries

- The protected `CloakHQ/CloakBrowser-Manager` upstream is read-only. Development and PRs target Martin's fork.
- One implementation worker owns one ticket's files at a time. Read-only research and review may run in parallel.
- New worktrees are blocked below 12 GiB free; releases are blocked below 8 GiB; no automatic pruning is allowed.
- A run binds `{profile_id, harness, agent?, allowed_origins, viewport_revision}` before a worker capability is issued.
- Direct CDP, raw Chromium launch flags, proxy credentials and vault reveal are not general harness operations.
- A workflow engine coordinates durable steps; it does not become the source of truth for profiles, secrets, cookies or authorization.
- Federation synchronizes signed receipts and public metadata only. Secrets, cookies, profiles and browser storage stay node-local.
- Governance/DAO voting never participates in request-time authorization.
- Deliberate account bans, refund abuse, bypass/evasion optimization and jailbreak automation from the transcript are excluded. Defensive health checks, legitimate account recovery and authorized security testing are allowed.
- Explicitly excluded workflow classes: induced bans, refund abuse, account marketplace automation, credential exposure, jailbreak/refusal bypass, NSFW workflows, fraud and liability-shifting automation.

## 4. Current evidence-backed status

| Ticket | GitHub | Status on 27 July | Evidence / next gate |
|---|---|---|---|
| CBM-001 | [#3](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/3) | Implemented | `aa0da15`; handoff receipt and redaction tests exist. |
| CBM-002 | [#4](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/4) | Implemented | `78b9959`; read-only merge/disk radar, never deletes. |
| CBM-003 | [#5](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/5) | Static gate implemented | `214ba4e`; live smoke and screenshot freshness remain. |
| CBM-004 | [#6](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/6) | Implemented | `1e9d509`; stable UI state graph and transition tests. |
| CBM-005 | [#7](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/7) | Implemented | `d645056`; cancellation cleanup and error classification. |
| CBM-006 | [#8](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/8) | Migration exact-set repair in progress | Not accepted until the repair is committed and live-proven on VCVM with exact Manager/worker commits. |
| CBM-007 | [#9](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/9) | Partial | `293b0ab`; discovery is truthful, broad mutation parity remains unavailable. |
| CBM-008 | [#10](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/10) | Gate implemented, acceptance red | `0355238`; live Manager/worker commits and run proof still mismatch. |
| CBM-009 | [#11](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/11) | Implemented | `8f8fd1e`; release must deploy the same commit to Manager and workers. |
| CBM-010 | [#12](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/12) | Partial | Browser-first workspace exists; consolidation and visual evidence remain. |
| CBM-011 | [#13](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/13) | Partial | Phone Fit/full-view fixes exist; complete desktop/mobile parity remains. |
| CBM-012 | [#14](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/14) | Partial | Sessions/projects exist; fixed-session/grid behavior needs acceptance proof. |
| CBM-013 | [#15](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/15) | Partial | Defensive profile health exists; measured proxy/fingerprint correlation remains. |
| CBM-014 | [#16](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/16) | Partial | Trusted extension binding exists; artifact provenance/rollback remains. |
| CBM-015 | [#17](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/17) | Not runtime-proven | Streaming comparison requires same-host measured evidence. |
| CBM-016 | [#18](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/18) | Research only | Provider-neutral one-use secret handle not implemented. |
| CBM-017 | [#19](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/19) | Partial | Access UI/policies exist; agent/secret assignment model remains. |
| CBM-018 | [#20](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/20) | Planned | Password/OIDC/device-code/TOTP/passkey capability matrix remains. |
| CBM-019 | [#21](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/21) | Evidence preflight complete | `1de218b`; no runtime winner until identical pilots and real run screenshots. |
| CBM-020 | [#22](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/22) | Planned | Must wait for CBM-008 and CBM-022. |
| CBM-021 | [#23](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/23) | Implemented | `b3730bd`; rerun after release-transaction repair. |
| CBM-022 | [#24](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/24) | Transaction reviewed, apply disabled | Spec and quality/security reviews approve the transaction engine; live apply still waits for exact VCVM preflight and a reviewed wrapper-enable change. |
| CBM-023 | [#25](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/25) | Architecture only | Defer runtime implementation until single-node SLOs are met. |
| CBM-024 | [#26](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/26) | Planned | Final real screenshots/report only after live acceptance. |
| CBM-025 | [#28](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/28) | Planned | Global ticket-intake schema, cold-start replay proof and integration-baseline ownership. |
| CBM-026 | [#29](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/issues/29) | Planned | Active-lane operator-change routing metadata and capability-aware login surface. |

## 5. Transcript problem-to-ticket map

| Observed pain | Primary ticket | Required proof |
|---|---|---|
| Feature or button disappears after deploy | CBM-003, CBM-021, CBM-022 | Manifest locator + mounted state + route smoke + commit-bound screenshot. |
| New manager cannot continue the previous manager's work | CBM-001 | Strict ProjectStateV1 receipt consumed before task dispatch. |
| Cold-start manager cannot prove ProjectStateV1 replay | CBM-001, CBM-025 | Fresh process reconstructs active ticket, lane, worktree, receipts and stop condition from receipts only. |
| Ticket format differs by agent or transcript | CBM-025 | One global intake schema with source transcript, acceptance, dependencies, exclusions and evidence fields. |
| Unknown dirty/unmerged worktrees consume disk | CBM-002 | Read-only audit with merge/upstream/size state before spawning work. |
| Worktree starts from stale or ambiguous baseline | CBM-002, CBM-025 | Integration baseline, merge owner and no-stale-baseline check are recorded before edits. |
| Agent edits the wrong panel or validates the wrong view | CBM-004 | Stable `data-ui-state` and expected transition path. |
| Operator changes lane mid-run without routed metadata | CBM-026 | Active-lane routing records operator change, authority, affected repo/worktree/features and next proof. |
| Local text/UI iteration accidentally redeploys VCVM | CBM-001, CBM-022 | Explicit mode receipt; release wrapper refuses dirty/incorrect mode. |
| Frontend exists but backend route is absent or old | CBM-003, CBM-021 | Static API/UI contract plus isolated Docker smoke. |
| Feature evidence ignores repo overlap | CBM-003, CBM-024, CBM-025 | Final report includes repo x feature overlap matrix tied to commit and screenshots. |
| Harness/model runs out of auth, capacity or availability | CBM-006, CBM-017, CBM-020 | Per-adapter readiness, bounded failover policy and approval for account changes. |
| OIDC/device-code/passkey login cannot finish | CBM-018, CBM-026 | Reachable operator login surface exists for each declared auth capability; device-bound credentials pause for handoff. |
| Operator must explain the same UX flow repeatedly | CBM-004, CBM-010, CBM-024 | UI graph, compact workspace and reusable visual acceptance evidence. |
| Work is hard to observe or replay | CBM-019, CBM-020 | Outer workflow run + ACPX session replay + linked Manager run/profile IDs. |
| Too much concurrency crashes Mac/VCVM | CBM-002, CBM-020, CBM-022 | Admission budget for RAM/disk/workers; no unbounded agent fan-out. |
| Credentials enter agent context | CBM-016, CBM-017 | Secret-use lease with no reveal, origin/run binding and audit receipt. |

## 6. Execution waves and ticket definitions

### Wave P0-A — Make releases boring

#### Ticket CBM-025: Ticket intake and integration baseline

**Business value:** Makes every new request dispatchable, replayable and mergeable before implementation starts.

**Owned surfaces:** Project-state/ticket schemas, handoff receipt validation, worktree admission metadata, final evidence templates and tests.

**Dependencies:** CBM-001 and CBM-002.

**Acceptance:**

- One global ticket-intake schema captures source, scope, owner, dependencies, acceptance, exclusions, tests/proofs, rollback/hold conditions and evidence artifacts.
- Cold-start `ProjectStateV1` replay proves active ticket, current lane, worktree path, baseline commit, completed receipts, unmerged changes, next action and stop condition without reading chat history.
- Worktree integration metadata records baseline commit, upstream/merge target, merge owner and `no_stale_baseline` result before writes.
- Final evidence includes a repo x feature overlap matrix that maps every claimed feature to route/API/UI/test/screenshot evidence or marks it explicitly absent/deferred.
- Schema migration has exact-set tests and fixture replay from current receipts.

**Rollback / hold:** Hold implementation dispatch when intake is incomplete, baseline is stale, merge owner is missing or replay cannot reconstruct state from receipts only.

#### Ticket CBM-022: Atomic VCVM release and rollback

**Business value:** Prevents the exact failure where a deploy fixes one layer and silently replaces another with older code.

**Owned surfaces:** `scripts/vcvm_release_transaction.py`, `scripts/vcvm_release_remote.py`, deployment wrappers, release contract, deployment documentation and their tests.

**Acceptance:**

- Structured `{operation,args}` protocol is used by actual release and rollback paths.
- Immutable local image identity is one exact `sha256:<64hex>` value end-to-end and the OCI revision equals the clean fork commit.
- Candidate is built and verified before quiescing live services.
- Backup path is exactly `/home/coder/cloakbrowser-manager/backups/<receipt-id>.tar`; the regular non-symlink file hash is rechecked immediately before restore.
- Browser Use and ACPX workers are rebound through atomic mode-0600 systemd drop-ins, restarted according to captured state and verified against the release source.
- Every post-quiesce failure restores image, volume, unit/drop-in state and prior active/inactive state, then proves health/auth.
- ACPX readiness is measured or unavailable; it is never hardcoded true.
- Public `--apply` remains disabled until spec and quality reviewers approve and the full target suite is green.

**Required command:**

```bash
uv run --with pytest==8.3.5 python -m pytest \
  scripts/test_vcvm_release_transaction.py \
  scripts/test_vcvm_release_remote.py \
  scripts/test_cbm_release_manifest.py \
  scripts/test_vcvm_deployment.py -q
```

### Wave P0-B — Deploy one exact version and prove one task

#### Ticket CBM-026: Operator change router

**Business value:** Keeps active agent lanes correct when an operator changes profile, auth path, repo, feature target or release mode during a run.

**Owned surfaces:** Active-lane routing metadata, operator-change events, auth-capability routing UI/API, run receipts and tests.

**Dependencies:** CBM-001, CBM-004, CBM-017, CBM-018 and CBM-025.

**Acceptance:**

- Operator changes are typed as lane-affecting or lane-local and record actor, time, affected repo/worktree/features, current UI state, auth capability and required next proof.
- Lane-affecting changes either update the active ticket receipt before the next tool action or fence the run until a manager accepts the reroute.
- Each auth capability has a reachable operator login surface: password/use lease, OIDC redirect, device code, TOTP, software/synced passkey and device-bound passkey handoff.
- Routing never exposes credentials, raw cookies, proxy secrets or device-bound keys to agent prompts/logs.
- Tests cover mid-run profile switch, mode switch, auth-capability change, stale-baseline reroute and refusal of excluded workflow classes.

**Rollback / hold:** Hold the lane when routing metadata is missing, the operator surface is unreachable, a credential would be exposed, or the requested workflow matches an explicit exclusion.

#### Ticket CBM-008: Browser Use success and cancellation proof

**Business value:** Establishes the first trustworthy end-to-end product loop before investing in more orchestration.

**Dependencies:** CBM-006, CBM-009, CBM-021 and CBM-022 green; ACPX unit/token/venv provisioned; Manager and worker commits identical.

**Acceptance:**

- Release exact reviewed commit to the VCVM through the approved transaction only.
- Start one isolated demo profile and run a harmless Browser Use navigation on an allowed origin.
- Correlate Manager run ID, profile ID, worker lease, CDP target, first action, ordered outputs, screenshot hash, viewport and terminal state.
- Cancel a second claimed run and prove no post-cancel browser action/output while the profile remains running.
- Validate through the private Tailscale route from Mac and an iPhone-sized viewport.

### Wave P0-C — Make the UI compact and self-explanatory

#### Tickets CBM-010, CBM-011 and CBM-012

**Business value:** Removes the repeated human work of explaining which panel, button and viewer state an agent must use.

**Acceptance:**

- Desktop uses one browser-first workspace; mobile uses one split view with browser above and task chat below.
- Primary surfaces are limited to project/session/profile selection, task composer and browser viewer; admin/proxy/extension/access surfaces are progressive disclosure.
- Fullscreen and Phone Fit share the same fit/zoom/viewport/revision state as normal view.
- iOS keyboard changes the visual viewport without shrinking the saved device viewport or covering composer controls.
- Fixed browser sessions remain separate from temporary project chats; archive/done never deletes evidence.
- Every visual change is validated by state identity, interaction test and real screenshot at desktop and iPhone viewport.

### Wave P1 — Durable workflow pilot, not platform adoption

#### Tickets CBM-019 and CBM-020

**Business value:** Makes long agent work observable and resumable without turning the workflow engine into a new monolith.

**Decision gate:** Hatchet is the primary pilot because it best matches the local-first/VCVM, Python/TypeScript and low-operations constraints; Temporal is the durability reference and fallback. This is a pilot decision, not a production adoption claim. Run both on the same `profile_health -> acquire_lease -> acpx_agent_task -> browser_task -> evidence_gate -> release_lease` flow. Pilot Restate only if its local footprint or recovery result can beat one of them. Hatchet's current upstream license is MIT; the imported Grok transcript/export incorrectly called it Apache-2.0 and must not be used as license evidence.

**Acceptance:**

- Real run IDs and real dashboard screenshots; no mocked graph claims.
- Same task, inputs, worker limits and acceptance gate for every engine.
- Measure setup effort, idle/load RAM, first-task latency, retry correctness, cancellation fencing, worker restart recovery, replay usefulness and deletion/export behavior.
- High-level durable run links to ACPX's detailed session replay and the Manager's typed browser outputs.
- No engine is adopted unless it reduces operator time enough to justify its ongoing operations.

### Wave P2 — Secret use and identity

#### Tickets CBM-016, CBM-017 and CBM-018

**Business value:** Automates legitimate login handoffs without leaking credentials or requiring agents to parse arbitrary secret formats.

**Acceptance:**

- Human vault and machine-secret provider remain replaceable behind one provider-neutral handle.
- `use` and `reveal` are separate permissions; normal agents receive `use` only.
- Password/free text, OIDC redirect, device code, TOTP, software/synced passkey and device-bound passkey are represented as explicit capabilities.
- Device-bound flows pause for a human/session handoff; keys are never copied.
- Account creation, refunds, payments, production deploy and reveal require explicit approval.

### Wave P3 — Federation only after local stability

#### Ticket CBM-023

**Business value:** Allows multiple operator nodes later without copying the most sensitive state.

**Acceptance:**

- Signed receipts include issuer node, sequence, timestamp, expiry, schema, artifact hashes and approvals.
- Duplicate, replayed, revoked, expired and wrong-node receipts fail closed.
- Only receipts/capabilities/public metadata synchronize; browser storage, secrets and cookies do not.
- Matrix, NATS JetStream, Git and content-addressed storage are measured as transports; federation is not mislabeled as consensus or DAO governance.

## 7. Parallelization contract

Parallel work is allowed only when file ownership and dependencies are disjoint:

| Lane | May run now | Must not overlap |
|---|---|---|
| Release transaction implementation | Yes | No other writer on CBM-022 files. |
| Release spec/security review | Read-only | Must review a stable snapshot and repeat after repairs. |
| Workflow-engine primary-source research | Yes | No runtime adoption or deployment. |
| UI implementation | Hold until live version is exact | Avoid polishing an obsolete runtime/UI. |
| Secrets/identity implementation | Hold | Research only until P0 browser loop is green. |
| Federation/DAO implementation | Hold | Architecture documentation only. |

The system itself must later enforce this with project-state receipts, worktree overlap detection and a resource admission budget. A manager may delegate, but it may not silently exceed the Mac/VCVM capacity envelope.

## 8. Quality gates

Every ticket passes these gates in order:

1. **RED:** A behavioral regression test fails for the expected missing/incorrect behavior.
2. **GREEN:** Targeted tests pass without weakening assertions.
3. **Spec review:** A separate reviewer checks every acceptance line and rejects extras or omissions.
4. **Quality/security review:** A separate reviewer checks correctness, secret boundaries, rollback and maintainability.
5. **Repository gate:** Backend suite, frontend tests/build, Ruff, actionlint, feature manifest, schema validation, Docker smoke and secret scan.
6. **Runtime gate:** Exact Manager/worker commit, migrations, health/auth, Tailscale route and real browser interaction.
7. **Visual gate:** Desktop and iPhone viewport screenshots are tied to commit, URL class, UI state and run/profile IDs.
8. **Final evidence gate:** Repo x feature overlap matrix, ticket-intake receipt, integration-baseline receipt and operator-change routing receipt are complete or explicitly marked not applicable.

No green test output is reused from a previous commit as evidence for a new one.

## 9. Cost and strategy guard

The transcript's financial constraint changes engineering behavior:

- Optimize for operator hours removed, not number of tools installed.
- Keep Kubernetes out until one VCVM cannot meet a measured SLO or isolation requirement.
- Keep Paperclip/DAO/federation out of the critical request path until a smaller local contract fails in practice.
- Use the cheapest adequate harness for bounded implementation; use stronger models for architecture/review; record actual success/cost rather than model reputation.
- Stop a lane when it cannot name the next acceptance proof.
- A new dependency requires a replacement/deletion or a measured outcome that justifies its operational cost.

## 10. Stop condition

This plan is not complete when code is merged. It is complete only when:

- the reviewed fork commit is running on VCVM for Manager, Browser Use and ACPX;
- Browser Use completes one harmless profile-bound task and cancellation is fenced;
- the compact workspace works at desktop and iPhone viewport through Tailscale;
- the feature manifest, release receipt, screenshots and README all reference the same commit;
- cold-start ProjectStateV1 replay, ticket-intake schema, integration baseline and operator-change routing are proven or held with explicit blockers;
- remaining tickets are truthfully marked partial, blocked or deferred with no fabricated capability or benchmark result.
