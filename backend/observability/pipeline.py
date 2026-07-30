"""High-level local-first trace pipeline facade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .config import ObservabilityConfig, load_config
from .correlation import RunCorrelation, correlate_records
from .otlp import (
    build_otlp_export_request,
    export_otlp_http,
    validate_otlp_http_endpoint,
)
from .schema import SpanRecord, UsageRecord
from .spool import Spool, open_spool
from .tokscale_import import import_tokscale_aggregate


class TracePipeline:
    """Record, correlate, spool, export — without required SaaS."""

    def __init__(
        self,
        config: ObservabilityConfig | None = None,
        *,
        spool: Spool | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.config = config or load_config()
        self.base_dir = base_dir or Path.cwd()
        self._spool = spool or open_spool(
            self.config.spool_backend,
            self.config.resolved_spool_path(self.base_dir),
        )
        self._owns_spool = spool is None

    @property
    def spool(self) -> Spool:
        return self._spool

    def record_span(self, span: SpanRecord) -> SpanRecord:
        # Enforce default content_capture from config when span still defaulted.
        if not self.config.content_capture and span.content_capture:
            # Rebuild without content capture.
            span = SpanRecord.from_dict(
                {**span.to_dict(), "content_capture": False, "attributes": span.attributes}
            )
        self._spool.append_span(span)
        return span

    def record_usage(self, usage: UsageRecord) -> UsageRecord:
        self._spool.append_usage(usage)
        return usage

    def start_span(
        self,
        name: str,
        *,
        correlation: RunCorrelation | None = None,
        **kwargs: Any,
    ) -> SpanRecord:
        payload = dict(kwargs)
        payload.setdefault("content_capture", self.config.content_capture)
        payload.setdefault(
            "resource",
            {
                "service.name": self.config.service_name,
                **(
                    {"service.version": self.config.service_version}
                    if self.config.service_version
                    else {}
                ),
            },
        )
        span = SpanRecord(name=name, **payload)
        if correlation is not None:
            span = correlation.apply_to_span(span)
        return self.record_span(span)

    def import_tokscale(
        self,
        payload: Any,
        *,
        correlation: RunCorrelation | None = None,
        require_version: bool = False,
    ) -> list[UsageRecord]:
        records = import_tokscale_aggregate(
            payload,
            run_id=correlation.run_id if correlation else None,
            profile_id=correlation.profile_id if correlation else None,
            session_id=correlation.session_id if correlation else None,
            harness=(correlation.harness if correlation and correlation.harness else "tokscale"),
            expected_version=self.config.tokscale_expected_version,
            require_version=require_version,
        )
        if correlation is not None:
            _, records = correlate_records(correlation=correlation, usage=records)
        for item in records:
            self.record_usage(item)
        return records

    def export_jsonl(self, path: Path | str) -> dict[str, int]:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        count_spans = 0
        count_usage = 0
        with out.open("w", encoding="utf-8") as handle:
            for span in self._spool.iter_spans():
                handle.write(json.dumps(span.to_dict(), separators=(",", ":")) + "\n")
                count_spans += 1
            for usage in self._spool.iter_usage():
                handle.write(json.dumps(usage.to_dict(), separators=(",", ":")) + "\n")
                count_usage += 1
        return {"spans": count_spans, "usage": count_usage}

    def export_otlp_json(self, path: Path | str) -> dict[str, Any]:
        body = build_otlp_export_request(
            self._spool.iter_spans(),
            self._spool.iter_usage(),
            service_name=self.config.service_name,
            service_version=self.config.service_version,
        )
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(body, indent=2), encoding="utf-8")
        spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
        return {"path": str(out), "span_count": len(spans)}

    def maybe_export_otlp_http(self) -> dict[str, Any] | None:
        endpoint = validate_otlp_http_endpoint(self.config.otlp_http_endpoint)
        if not endpoint:
            return None
        body = build_otlp_export_request(
            self._spool.iter_spans(),
            self._spool.iter_usage(),
            service_name=self.config.service_name,
            service_version=self.config.service_version,
        )
        status, text = export_otlp_http(
            body,
            endpoint=endpoint,
            headers=self.config.otlp_headers,
        )
        return {"status_code": status, "body": text[:500]}

    def stats(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "counts": self._spool.count(),
        }

    def close(self) -> None:
        if self._owns_spool:
            self._spool.close()

    def __enter__(self) -> TracePipeline:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
