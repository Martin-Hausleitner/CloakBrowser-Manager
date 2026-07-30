"""ACP/ACPX/harness correlation tests."""

from __future__ import annotations

from backend.observability.correlation import (
    RunCorrelation,
    correlate_records,
    group_by_run,
)
from backend.observability.schema import SpanRecord, UsageRecord


def test_correlation_stamps_spans_and_usage():
    corr = RunCorrelation(
        run_id="run-9",
        harness="acpx",
        profile_id="prof-1",
        session_id="sess-1",
        acp_session_id="acp-1",
        acpx_run_id="acpx-9",
        worker_id="worker-1",
        lease_id="lease-1",
    )
    span = SpanRecord(name="agent.step")
    usage = UsageRecord(input_tokens=1, output_tokens=1)
    spans, usages = correlate_records(correlation=corr, spans=[span], usage=[usage])
    assert spans[0].run_id == "run-9"
    assert spans[0].harness == "acpx"
    assert spans[0].attributes["cbm.acpx_run_id"] == "acpx-9"
    assert spans[0].attributes["cbm.acp_session_id"] == "acp-1"
    assert usages[0].run_id == "run-9"
    assert usages[0].profile_id == "prof-1"


def test_group_by_run():
    spans = [
        SpanRecord(name="a", run_id="r1"),
        SpanRecord(name="b", run_id="r2"),
    ]
    usage = [UsageRecord(input_tokens=1, run_id="r1")]
    groups = group_by_run(spans, usage)
    assert len(groups["r1"]["spans"]) == 1
    assert len(groups["r1"]["usage"]) == 1
    assert len(groups["r2"]["spans"]) == 1
