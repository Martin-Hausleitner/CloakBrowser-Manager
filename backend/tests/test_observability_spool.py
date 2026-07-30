"""SQLite and JSONL spool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.observability.schema import SpanRecord, UsageRecord
from backend.observability.spool import JsonlSpool, SqliteSpool, open_spool


@pytest.mark.parametrize("backend", ["sqlite", "jsonl"])
def test_spool_append_and_iterate(tmp_path: Path, backend: str):
    path = tmp_path / ("t.db" if backend == "sqlite" else "t.jsonl")
    spool = open_spool(backend, path)
    try:
        span = SpanRecord(name="s1", run_id="r1", harness="acpx")
        usage = UsageRecord(input_tokens=1, output_tokens=2, run_id="r1", source="native")
        spool.append_span(span)
        spool.append_usage(usage)
        spans = list(spool.iter_spans())
        usages = list(spool.iter_usage())
        assert len(spans) == 1
        assert spans[0].name == "s1"
        assert spans[0].run_id == "r1"
        assert len(usages) == 1
        assert usages[0].output_tokens == 2
        assert spool.count() == {"spans": 1, "usage": 1}
    finally:
        spool.close()


def test_jsonl_and_sqlite_classes(tmp_path: Path):
    j = JsonlSpool(tmp_path / "a.jsonl")
    s = SqliteSpool(tmp_path / "a.db")
    try:
        j.append_span(SpanRecord(name="j"))
        s.append_span(SpanRecord(name="s"))
        assert j.count()["spans"] == 1
        assert s.count()["spans"] == 1
    finally:
        j.close()
        s.close()
