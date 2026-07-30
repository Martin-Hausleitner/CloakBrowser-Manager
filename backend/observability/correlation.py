"""ACP / ACPX / harness run correlation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .schema import SpanRecord, UsageRecord

KNOWN_HARNESSES = frozenset(
    {
        "acp",
        "acpx",
        "browser-use",
        "stagehand",
        "unbrowse",
        "codex",
        "claude",
        "cursor",
        "grok",
        "opencode",
        "antigravity",
        "native",
        "tokscale",
    }
)


@dataclass(slots=True)
class RunCorrelation:
    """Correlation keys linking Manager runs, harness sessions, and traces."""

    run_id: str
    harness: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    acp_session_id: str | None = None
    acpx_run_id: str | None = None
    worker_id: str | None = None
    lease_id: str | None = None
    task_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not str(self.run_id).strip():
            raise ValueError("run_id is required")
        self.run_id = str(self.run_id).strip()
        if self.harness:
            self.harness = str(self.harness).strip().lower()
        for attr in (
            "profile_id",
            "session_id",
            "acp_session_id",
            "acpx_run_id",
            "worker_id",
            "lease_id",
            "task_id",
        ):
            value = getattr(self, attr)
            if value is not None:
                setattr(self, attr, str(value).strip())

    def as_attributes(self) -> dict[str, str]:
        attrs: dict[str, str] = {"cbm.run_id": self.run_id}
        if self.harness:
            attrs["cbm.harness"] = self.harness
        if self.profile_id:
            attrs["cbm.profile_id"] = self.profile_id
        if self.session_id:
            attrs["cbm.session_id"] = self.session_id
        if self.acp_session_id:
            attrs["cbm.acp_session_id"] = self.acp_session_id
        if self.acpx_run_id:
            attrs["cbm.acpx_run_id"] = self.acpx_run_id
        if self.worker_id:
            attrs["cbm.worker_id"] = self.worker_id
        if self.lease_id:
            attrs["cbm.lease_id"] = self.lease_id
        if self.task_id:
            attrs["cbm.task_id"] = self.task_id
        return attrs

    def apply_to_span(self, span: SpanRecord) -> SpanRecord:
        span.run_id = span.run_id or self.run_id
        span.harness = span.harness or self.harness
        span.profile_id = span.profile_id or self.profile_id
        span.session_id = span.session_id or self.session_id
        merged = {**self.as_attributes(), **(span.attributes or {})}
        # Re-sanitize via constructor path
        refreshed = SpanRecord.from_dict(
            {
                **span.to_dict(),
                "run_id": span.run_id,
                "harness": span.harness,
                "profile_id": span.profile_id,
                "session_id": span.session_id,
                "attributes": merged,
            }
        )
        return refreshed

    def apply_to_usage(self, usage: UsageRecord) -> UsageRecord:
        return UsageRecord.from_dict(
            {
                **usage.to_dict(),
                "run_id": usage.run_id or self.run_id,
                "harness": usage.harness or self.harness,
                "profile_id": usage.profile_id or self.profile_id,
                "session_id": usage.session_id or self.session_id,
                "attributes": {**self.as_attributes(), **(usage.attributes or {})},
            }
        )


def correlate_records(
    *,
    correlation: RunCorrelation,
    spans: Iterable[SpanRecord] | None = None,
    usage: Iterable[UsageRecord] | None = None,
) -> tuple[list[SpanRecord], list[UsageRecord]]:
    """Stamp correlation keys onto spans and usage records."""
    out_spans = [correlation.apply_to_span(s) for s in (spans or ())]
    out_usage = [correlation.apply_to_usage(u) for u in (usage or ())]
    return out_spans, out_usage


def group_by_run(
    spans: Iterable[SpanRecord],
    usage: Iterable[UsageRecord],
) -> dict[str, dict[str, list[Any]]]:
    """Group records by run_id for export / dashboards."""
    groups: dict[str, dict[str, list[Any]]] = {}
    for span in spans:
        key = span.run_id or "_unassigned"
        groups.setdefault(key, {"spans": [], "usage": []})
        groups[key]["spans"].append(span)
    for item in usage:
        key = item.run_id or "_unassigned"
        groups.setdefault(key, {"spans": [], "usage": []})
        groups[key]["usage"].append(item)
    return groups
