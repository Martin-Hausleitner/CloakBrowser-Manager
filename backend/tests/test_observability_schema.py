"""Tests for canonical span and usage schema."""

from __future__ import annotations

import pytest

from backend.observability.privacy import PrivacyError
from backend.observability.schema import (
    SpanKind,
    SpanRecord,
    SpanStatus,
    UsageRecord,
    new_span_id,
    new_trace_id,
)


def test_span_defaults_and_ids():
    span = SpanRecord(name="acpx.run", harness="acpx", run_id="run-1")
    assert len(span.trace_id) == 32
    assert len(span.span_id) == 16
    assert span.kind is SpanKind.INTERNAL
    assert span.content_capture is False
    assert span.attributes["cbm.run_id"] == "run-1"
    assert span.attributes["cbm.harness"] == "acpx"


def test_span_rejects_forbidden_prompt_attribute():
    with pytest.raises(PrivacyError):
        SpanRecord(name="x", attributes={"prompt": "secret user text"})


def test_span_rejects_cookie_attribute():
    with pytest.raises(PrivacyError):
        SpanRecord(name="x", attributes={"cookie": "session=abc"})


def test_span_allows_safe_gen_ai_usage_attributes():
    span = SpanRecord(
        name="llm.call",
        attributes={
            "gen_ai.request.model": "claude-opus",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 4,
        },
    )
    assert span.attributes["gen_ai.usage.input_tokens"] == 10


def test_span_drops_unknown_attributes_when_content_capture_false():
    span = SpanRecord(
        name="x",
        attributes={"custom.metric": 1, "cbm.run_id": "r1"},
    )
    assert "custom.metric" not in span.attributes
    assert span.attributes["cbm.run_id"] == "r1"


def test_span_finish_and_roundtrip():
    span = SpanRecord(name="task", trace_id=new_trace_id(), span_id=new_span_id())
    span.finish(status_code=SpanStatus.OK)
    assert span.end_time_unix_nano is not None
    restored = SpanRecord.from_dict(span.to_dict())
    assert restored.name == "task"
    assert restored.status_code is SpanStatus.OK


def test_usage_totals_and_roundtrip():
    usage = UsageRecord(
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=2,
        model="m",
        source="tokscale",
    )
    assert usage.total_tokens == 17
    restored = UsageRecord.from_dict(usage.to_dict())
    assert restored.input_tokens == 10
    assert restored.source == "tokscale"


def test_usage_rejects_negative():
    with pytest.raises(ValueError):
        UsageRecord(input_tokens=-1)
