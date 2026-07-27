# Workflow Engine Market Gate Scoring Schema (v2)

CBM-019 uses a deterministic, preflight, evidence-only scoring matrix. It does
not run workflow engines and does not declare a runtime winner.

## Model

- 20 features per engine
- Each scored feature uses an integer score from 0 to 10
- Max total per engine: 200
- Missing or unaudited evidence is `pending` and remains unscored (`score: null`)
- License/open-core uncertainty is `legal_review` and remains unscored (`score: null`)
- No runtime benchmark numbers are generated or inferred in this lane

## Orthogonal Fields

Every feature keeps evidence state separate from numeric score:

- `evidence_status`: one of `verified`, `partial`, `pending`, `blocked`, `legal_review`
- `score`: integer 0-10 only when evidence was scored; `null` for `pending` and `legal_review`
- `evidence_refs`: fixture-local keys pointing to primary upstream links
- `note`: short explanation of what was or was not proven

`pending` is not a measured zero. It means the retained primary source set did
not prove the criterion in this preflight pass. `legal_review` marks a license
or open-core constraint for legal/product review; it is not an automatic product
block unless actual self-host pilot terms prohibit the required use.

## Feature Set (20)

1. `license_posture_source`
2. `license_constraint_review_needed`
3. `repo_documentation_health`
4. `self_hosting_officially_supported`
5. `self_hosting_installation_artifacts`
6. `support_local_docker_or_dev_compose`
7. `support_kubernetes_deployment`
8. `support_helm_deployment`
9. `api_transport_visibility`
10. `sdk_python`
11. `sdk_typescript_node`
12. `sdk_java_or_go`
13. `workflow_trigger_event_driven`
14. `workflow_scheduling_cron`
15. `retries_supported`
16. `cancellation_supported`
17. `replay_or_resume_support`
18. `failure_recovery_controls`
19. `ui_visualization_graph_or_lineage`
20. `observability_logging_traces`

## Status Policy

- `verified`: direct primary evidence supports the criterion; score should usually be 10
- `partial`: primary evidence exists but is incomplete for CBM pilot needs; score should usually be 5
- `blocked`: direct primary evidence shows the criterion is unavailable or prohibited for the pilot; score may be 0
- `pending`: evidence is missing or unaudited; score must be `null`
- `legal_review`: license/open-core terms need legal/product review; score must be `null`

## Deterministic Validation Contract

The scorer must assert:

- schema identity is `cbm-orchestration-market-gate-v2`
- every engine has exactly the 20 feature IDs above, in order
- all evidence statuses are from the allowed set
- `pending` and `legal_review` features are unscored
- scored features use integer scores in `[0, 10]`
- scored/legal/blocking features cite known evidence refs
- generated report timestamps come from the fixture, not wall-clock time
- repeated scoring of the same fixture yields identical JSON
