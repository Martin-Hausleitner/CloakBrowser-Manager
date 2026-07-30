"""OTLP JSON export and optional endpoint config tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.observability.otlp import (
    OtlpConfigError,
    build_otlp_export_request,
    export_otlp_http,
    span_to_otlp,
    validate_otlp_http_endpoint,
)
from backend.observability.pipeline import TracePipeline
from backend.observability.schema import SpanRecord, UsageRecord
from backend.observability.spool import open_spool


def test_validate_endpoint():
    assert validate_otlp_http_endpoint(None) is None
    assert validate_otlp_http_endpoint("") is None
    assert validate_otlp_http_endpoint("http://127.0.0.1:4318/v1/traces").endswith(
        "/v1/traces"
    )
    with pytest.raises(OtlpConfigError):
        validate_otlp_http_endpoint("ftp://bad")


def test_build_otlp_request_contains_spans_not_prompts():
    span = SpanRecord(
        name="acpx.run",
        run_id="r1",
        harness="acpx",
        attributes={"gen_ai.usage.input_tokens": 3},
    )
    usage = UsageRecord(input_tokens=3, output_tokens=1, model="m", run_id="r1")
    body = build_otlp_export_request([span], [usage])
    raw = json.dumps(body)
    assert "acpx.run" in raw
    assert "prompt" not in raw.lower() or "gen_ai" in raw  # no content field
    assert "password" not in raw
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) >= 2
    assert span_to_otlp(span)["traceId"] == span.trace_id


def test_pipeline_export_files(tmp_path: Path):
    spool = open_spool("sqlite", tmp_path / "t.db")
    with TracePipeline(spool=spool, base_dir=tmp_path) as pipeline:
        pipeline.record_span(SpanRecord(name="n", run_id="r"))
        pipeline.record_usage(UsageRecord(input_tokens=1, output_tokens=1, run_id="r"))
        j = pipeline.export_jsonl(tmp_path / "out.jsonl")
        o = pipeline.export_otlp_json(tmp_path / "out.otlp.json")
        assert j["spans"] == 1
        assert j["usage"] == 1
        assert o["span_count"] >= 1
        assert (tmp_path / "out.jsonl").exists()
        assert "resourceSpans" in json.loads((tmp_path / "out.otlp.json").read_text())


def test_export_otlp_http_posts_json():
    body = build_otlp_export_request([SpanRecord(name="x")])

    class _Resp:
        status = 200

        def read(self):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()) as mocked:
        status, text = export_otlp_http(
            body, endpoint="http://127.0.0.1:4318/v1/traces"
        )
        assert status == 200
        assert "ok" in text
        mocked.assert_called_once()
