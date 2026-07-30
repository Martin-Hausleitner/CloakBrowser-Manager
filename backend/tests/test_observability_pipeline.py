"""TracePipeline integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from backend.observability.config import ObservabilityConfig, load_config
from backend.observability.correlation import RunCorrelation
from backend.observability.pipeline import TracePipeline
from backend.observability.schema import SpanRecord

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "observability"
    / "tokscale_aggregate_4_7_0.json"
)


def test_load_config_defaults_content_capture_false(monkeypatch):
    monkeypatch.delenv("CBM_CONTENT_CAPTURE", raising=False)
    monkeypatch.delenv("CBM_OTEL_CONTENT_CAPTURE", raising=False)
    cfg = load_config({})
    assert cfg.content_capture is False
    assert cfg.otlp_http_endpoint is None
    assert cfg.tokscale_expected_version == "4.7.0"


def test_load_config_otlp_optional(monkeypatch):
    cfg = load_config(
        {
            "CBM_OTLP_HTTP_ENDPOINT": "http://localhost:4318/v1/traces",
            "CBM_OTLP_HEADERS": "Authorization=Bearer x,X-Extra=1",
            "CBM_TRACE_SPOOL_BACKEND": "jsonl",
        }
    )
    assert cfg.otlp_http_endpoint.endswith("/v1/traces")
    assert cfg.otlp_headers is not None
    assert "Authorization" in cfg.otlp_headers
    assert cfg.spool_backend == "jsonl"
    # headers redacted in dump
    dumped = cfg.to_dict()
    assert dumped["otlp_headers"]["Authorization"] == "[redacted]"


def test_pipeline_tokscale_and_correlation(tmp_path: Path):
    cfg = ObservabilityConfig(
        spool_backend="sqlite",
        spool_path=str(tmp_path / "traces.db"),
        content_capture=False,
    )
    corr = RunCorrelation(
        run_id="run-pipe",
        harness="acpx",
        profile_id="p1",
        acpx_run_id="acpx-1",
    )
    with TracePipeline(cfg, base_dir=tmp_path) as pipeline:
        pipeline.start_span("harness.run", correlation=corr)
        records = pipeline.import_tokscale(FIXTURE, correlation=corr, require_version=True)
        assert len(records) == 3
        stats = pipeline.stats()
        assert stats["counts"]["spans"] == 1
        assert stats["counts"]["usage"] == 3
        exported = pipeline.export_jsonl(tmp_path / "export.jsonl")
        assert exported["usage"] == 3
        lines = (tmp_path / "export.jsonl").read_text().splitlines()
        for line in lines:
            payload = json.loads(line)
            assert "prompt" not in json.dumps(payload).lower() or payload.get("record_type")
            blob = json.dumps(payload)
            assert "do not" not in blob
            assert '"prompt"' not in blob
            assert '"cookie"' not in blob


def test_pipeline_maybe_export_otlp_none_without_endpoint(tmp_path: Path):
    cfg = ObservabilityConfig(
        spool_backend="jsonl",
        spool_path=str(tmp_path / "t.jsonl"),
        otlp_http_endpoint=None,
    )
    with TracePipeline(cfg, base_dir=tmp_path) as pipeline:
        pipeline.record_span(SpanRecord(name="x"))
        assert pipeline.maybe_export_otlp_http() is None
