import copy
import json
from pathlib import Path

import pytest

import score_workflow_market_gate as scorer


FIXTURE = Path(__file__).with_name("workflow_market_gate.fixture.json")


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_has_twenty_ordered_features_per_engine():
    data = scorer.load_fixture(FIXTURE)

    assert data["criteria"] == scorer.CRITERIA
    assert len(data["criteria"]) == 20
    for engine in data["engines"]:
        assert list(engine["features"]) == scorer.CRITERIA


def test_pending_and_legal_review_are_unscored_not_zero():
    data = scorer.load_fixture(FIXTURE)

    legal_review_seen = False
    pending_seen = False
    for engine in data["engines"]:
        for feature in engine["features"].values():
            if feature["evidence_status"] == "pending":
                pending_seen = True
                assert feature["score"] is None
            if feature["evidence_status"] == "legal_review":
                legal_review_seen = True
                assert feature["score"] is None

    assert pending_seen
    assert legal_review_seen


def test_legal_review_license_is_not_reported_as_product_block():
    data = scorer.load_fixture(FIXTURE)
    report = scorer.build_report(data, FIXTURE)
    rows = {entry["id"]: entry for entry in report["entries"]}

    for engine_id in ("restate", "inngest", "windmill"):
        assert rows[engine_id]["legal_review_criteria"] == 1
        assert rows[engine_id]["blocked_criteria"] == 0
        assert rows[engine_id]["evidence_status"] in {"legal_review", "pending_with_legal_review"}


def test_fixture_rejects_old_five_criterion_shape():
    data = load_fixture()
    broken = copy.deepcopy(data)
    broken["criteria"] = broken["criteria"][:5]

    with pytest.raises(ValueError, match="20-feature contract"):
        scorer.validate_fixture(broken)


def test_fixture_rejects_pending_numeric_score():
    data = load_fixture()
    broken = copy.deepcopy(data)
    broken["engines"][0]["features"]["support_helm_deployment"]["score"] = 0

    with pytest.raises(ValueError, match="pending evidence must be unscored"):
        scorer.validate_fixture(broken)


def test_report_generation_is_deterministic():
    data = scorer.load_fixture(FIXTURE)

    first = scorer.build_report(data, FIXTURE)
    second = scorer.build_report(data, FIXTURE)

    assert first == second
    assert first["generated_at_utc"] == data["generated_at_utc"]
    assert first["max_score"] == 200


def test_airflow_uses_full_twenty_feature_ceiling_with_unscored_sdk_gaps():
    data = scorer.load_fixture(FIXTURE)
    report = scorer.build_report(data, FIXTURE)
    airflow = next(entry for entry in report["entries"] if entry["id"] == "airflow")

    assert airflow["score"] == 180
    assert airflow["max_score"] == 200
    assert airflow["unscored_pending"] == 2
