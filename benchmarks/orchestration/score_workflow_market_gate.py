#!/usr/bin/env python3
"""Validate and score the CBM-019 workflow-engine market-gate fixture.

This is a preflight evidence gate, not a runtime benchmark. Missing evidence is
left unscored so it cannot be mistaken for poor runtime performance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "cbm-orchestration-market-gate-v2"
REPORT_SCHEMA = "cbm-orchestration-market-gate-score-v2"

CRITERIA = [
    "license_posture_source",
    "license_constraint_review_needed",
    "repo_documentation_health",
    "self_hosting_officially_supported",
    "self_hosting_installation_artifacts",
    "support_local_docker_or_dev_compose",
    "support_kubernetes_deployment",
    "support_helm_deployment",
    "api_transport_visibility",
    "sdk_python",
    "sdk_typescript_node",
    "sdk_java_or_go",
    "workflow_trigger_event_driven",
    "workflow_scheduling_cron",
    "retries_supported",
    "cancellation_supported",
    "replay_or_resume_support",
    "failure_recovery_controls",
    "ui_visualization_graph_or_lineage",
    "observability_logging_traces",
]

ALLOWED_STATUSES = {"verified", "partial", "pending", "blocked", "legal_review"}
UNSCORED_STATUSES = {"pending", "legal_review"}
MAX_SCORE = len(CRITERIA) * 10


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_fixture(data)
    return data


def validate_fixture(data: dict[str, Any]) -> None:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"Unexpected fixture schema: {data.get('schema')!r}")
    if data.get("criteria") != CRITERIA:
        raise ValueError("Fixture criteria must exactly match the CBM-019 20-feature contract")
    if not data.get("generated_at_utc"):
        raise ValueError("Fixture must include generated_at_utc")

    engines = data.get("engines")
    if not isinstance(engines, list) or not engines:
        raise ValueError("Fixture must include at least one engine")

    seen_ids: set[str] = set()
    for engine in engines:
        engine_id = _require_string(engine, "id")
        if engine_id in seen_ids:
            raise ValueError(f"Duplicate engine id: {engine_id}")
        seen_ids.add(engine_id)
        _require_string(engine, "name")
        _require_string(engine, "repo")
        _require_string(engine, "license")
        _require_string(engine, "license_url")
        if not isinstance(engine.get("evidence"), dict) or not engine["evidence"]:
            raise ValueError(f"{engine_id}: evidence map is required")

        features = engine.get("features")
        if not isinstance(features, dict):
            raise ValueError(f"{engine_id}: features must be an object keyed by criterion id")
        feature_ids = list(features)
        if feature_ids != CRITERIA:
            raise ValueError(f"{engine_id}: feature ids must exactly match criteria order")

        for feature_id, feature in features.items():
            if not isinstance(feature, dict):
                raise ValueError(f"{engine_id}/{feature_id}: feature must be an object")
            status = feature.get("evidence_status")
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"{engine_id}/{feature_id}: unsupported evidence_status {status!r}")
            score = feature.get("score")
            if status in UNSCORED_STATUSES:
                if score is not None:
                    raise ValueError(f"{engine_id}/{feature_id}: {status} evidence must be unscored")
            elif not isinstance(score, int) or not 0 <= score <= 10:
                raise ValueError(f"{engine_id}/{feature_id}: scored evidence must use an integer 0-10")

            refs = feature.get("evidence_refs")
            if status in {"verified", "partial", "blocked", "legal_review"}:
                if not isinstance(refs, list) or not refs:
                    raise ValueError(f"{engine_id}/{feature_id}: evidence_refs required for {status}")
                missing = [ref for ref in refs if ref not in engine["evidence"]]
                if missing:
                    raise ValueError(f"{engine_id}/{feature_id}: unknown evidence refs {missing}")
            elif refs is not None and not isinstance(refs, list):
                raise ValueError(f"{engine_id}/{feature_id}: evidence_refs must be a list when present")


def _require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string field: {key}")
    return value


def score_engine(engine: dict[str, Any]) -> dict[str, Any]:
    feature_results = []
    earned = 0
    scored_count = 0
    pending_count = 0
    legal_review_count = 0
    blocked_count = 0

    for feature_id in CRITERIA:
        feature = engine["features"][feature_id]
        status = feature["evidence_status"]
        score = feature["score"]
        if score is None:
            pending_count += status == "pending"
            legal_review_count += status == "legal_review"
        else:
            earned += score
            scored_count += 1
            blocked_count += status == "blocked"
        feature_results.append(
            {
                "id": feature_id,
                "evidence_status": status,
                "score": score,
                "evidence_refs": feature.get("evidence_refs", []),
                "note": feature.get("note", ""),
            }
        )

    evidence_status = "verified"
    if pending_count:
        evidence_status = "pending"
    if legal_review_count:
        evidence_status = "legal_review" if evidence_status == "verified" else "pending_with_legal_review"
    if blocked_count:
        evidence_status = "blocked"

    return {
        "id": engine["id"],
        "name": engine["name"],
        "license": engine["license"],
        "score": earned,
        "max_score": MAX_SCORE,
        "scored_criteria": scored_count,
        "unscored_pending": pending_count,
        "legal_review_criteria": legal_review_count,
        "blocked_criteria": blocked_count,
        "evidence_status": evidence_status,
        "features": feature_results,
    }


def build_report(data: dict[str, Any], fixture_path: Path) -> dict[str, Any]:
    entries = [score_engine(engine) for engine in data["engines"]]
    totals = [entry["score"] for entry in entries]
    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": data["generated_at_utc"],
        "fixture": str(fixture_path),
        "criteria": CRITERIA,
        "max_score": MAX_SCORE,
        "scoring_policy": {
            "score_range": "0-10 per feature; total max 200",
            "unscored_statuses": sorted(UNSCORED_STATUSES),
            "runtime_benchmark_policy": "preflight evidence only; runtime winner is not selected here",
        },
        "entries": entries,
        "scores": {
            "min": min(totals) if totals else 0,
            "max": max(totals) if totals else 0,
            "avg": round(sum(totals) / len(totals), 2) if totals else 0,
        },
    }


def build_markdown(data: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [
        "# Workflow Engine Market Gate Score (Evidence-only)",
        "",
        f"Date generated: {report['generated_at_utc']}",
        "",
        "This is a CBM-019 preflight evidence matrix, not a runtime benchmark. "
        "Pending and legal-review criteria are unscored, not counted as measured zero performance.",
        "",
        "## Candidate Scores",
        "",
        "| Engine | Score (max 200) | Evidence status | Scored | Pending | Legal review | Blocked |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in report["entries"]:
        lines.append(
            f"| {row['name']} | {row['score']} | {row['evidence_status']} | "
            f"{row['scored_criteria']}/20 | {row['unscored_pending']} | "
            f"{row['legal_review_criteria']} | {row['blocked_criteria']} |"
        )

    lines.extend(
        [
            "",
            "## Feature Detail",
            "",
        ]
    )
    for row in report["entries"]:
        lines.extend([f"### {row['name']}", ""])
        lines.append("| Feature | Status | Score | Note |")
        lines.append("|---|---|---:|---|")
        for feature in row["features"]:
            score = "unscored" if feature["score"] is None else str(feature["score"])
            note = feature["note"].replace("|", "\\|")
            lines.append(f"| `{feature['id']}` | {feature['evidence_status']} | {score} | {note} |")
        lines.append("")

    lines.extend(
        [
            "## Evidence-only Policy",
            "",
            "- No runtime or hosted benchmark values are generated by this script.",
            "- Numeric score and evidence status are separate fields.",
            "- `pending` means the preflight source set did not contain enough primary evidence.",
            "- `legal_review` marks license or open-core constraints for counsel/product review; it is not an automatic product block.",
            "- A `blocked` feature is reserved for direct evidence that a required pilot capability is prohibited or unavailable.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="score workflow engine market gate fixture")
    parser.add_argument("--fixture", type=Path, default=Path("benchmarks/orchestration/workflow_market_gate.fixture.json"))
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    fixture = load_fixture(args.fixture)
    if args.validate_only:
        print(json.dumps({"valid": True, "schema": SCHEMA, "engines": len(fixture["engines"])}, indent=2))
        return 0

    report = build_report(fixture, args.fixture)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(build_markdown(fixture, report) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "generated": report["generated_at_utc"],
                "count": len(report["entries"]),
                "max_score": report["scores"]["max"],
                "criteria_per_engine": len(CRITERIA),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
