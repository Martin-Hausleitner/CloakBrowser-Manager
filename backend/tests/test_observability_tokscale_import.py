"""TokScale 4.7.0 safe aggregate importer tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.observability.privacy import PrivacyError
from backend.observability.tokscale_import import (
    TokScaleImportError,
    import_tokscale_aggregate,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "observability"
    / "tokscale_aggregate_4_7_0.json"
)


def test_import_fixture_aggregate():
    records = import_tokscale_aggregate(
        FIXTURE,
        run_id="run-42",
        harness="acpx",
        require_version=True,
    )
    # 2 models + 1 totals
    assert len(records) == 3
    assert records[0].source == "tokscale"
    assert records[0].run_id == "run-42"
    assert records[0].model == "claude-opus"
    assert records[0].input_tokens == 12000
    assert records[0].cache_read_tokens == 800
    assert records[-1].model == "__totals__"
    assert records[-1].cost_usd == 5.66


def test_import_refuses_prompt_content():
    payload = {
        "version": "4.7.0",
        "models": [{"model": "x", "input_tokens": 1, "prompt": "do not store"}],
    }
    with pytest.raises(PrivacyError):
        import_tokscale_aggregate(payload)


def test_import_refuses_messages():
    payload = {
        "version": "4.7.0",
        "models": [{"model": "x", "input_tokens": 1}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    with pytest.raises(PrivacyError):
        import_tokscale_aggregate(payload)


def test_require_version_mismatch():
    payload = {"version": "4.6.0", "totals": {"input_tokens": 1, "output_tokens": 0}}
    with pytest.raises(TokScaleImportError):
        import_tokscale_aggregate(payload, require_version=True)


def test_totals_only():
    payload = {
        "version": "4.7.0",
        "totals": {"input_tokens": 9, "output_tokens": 3, "cost_usd": 0.1},
    }
    records = import_tokscale_aggregate(payload)
    assert len(records) == 1
    assert records[0].input_tokens == 9


def test_empty_raises():
    with pytest.raises(TokScaleImportError):
        import_tokscale_aggregate({"version": "4.7.0"})


def test_import_from_json_string():
    payload = json.dumps(
        {"version": "4.7.0", "totals": {"input_tokens": 2, "output_tokens": 1}}
    )
    records = import_tokscale_aggregate(payload)
    assert records[0].total_tokens == 3
