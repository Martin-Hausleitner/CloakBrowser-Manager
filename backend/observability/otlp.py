"""Optional OTLP HTTP JSON export. No required SaaS destination."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterable
from urllib.parse import urlparse

from .schema import SpanRecord, UsageRecord


class OtlpConfigError(ValueError):
    """Invalid OTLP endpoint configuration."""


def validate_otlp_http_endpoint(endpoint: str | None) -> str | None:
    """Validate optional OTLP HTTP endpoint. Empty means disabled."""
    if endpoint is None:
        return None
    cleaned = endpoint.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise OtlpConfigError("OTLP endpoint must use http or https")
    if not parsed.netloc:
        raise OtlpConfigError("OTLP endpoint must include a host")
    return cleaned


def span_to_otlp(span: SpanRecord) -> dict[str, Any]:
    """Map a SpanRecord to an OTLP JSON span object (subset)."""
    attributes = [
        {"key": key, "value": _otlp_any_value(value)}
        for key, value in (span.attributes or {}).items()
    ]
    status: dict[str, Any] = {"code": _status_code(span.status_code.value)}
    if span.status_message:
        status["message"] = span.status_message
    payload: dict[str, Any] = {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "kind": _span_kind(span.kind.value),
        "startTimeUnixNano": str(span.start_time_unix_nano),
        "attributes": attributes,
        "status": status,
    }
    if span.parent_span_id:
        payload["parentSpanId"] = span.parent_span_id
    if span.end_time_unix_nano is not None:
        payload["endTimeUnixNano"] = str(span.end_time_unix_nano)
    return payload


def usage_to_otlp_span(usage: UsageRecord) -> dict[str, Any]:
    """Represent usage as a short CLIENT span with gen_ai.usage attributes.

    OTLP traces do not have a first-class usage record; this preserves aggregates
    without content.
    """
    from .schema import SpanKind, SpanRecord, SpanStatus, new_span_id, new_trace_id

    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "usage_aggregate",
    }
    if usage.model:
        attrs["gen_ai.request.model"] = usage.model
    if usage.provider:
        attrs["gen_ai.system"] = usage.provider
    attrs["gen_ai.usage.input_tokens"] = usage.input_tokens
    attrs["gen_ai.usage.output_tokens"] = usage.output_tokens
    if usage.cache_read_tokens:
        attrs["gen_ai.usage.cache_read_input_tokens"] = usage.cache_read_tokens
    if usage.cache_creation_tokens:
        attrs["gen_ai.usage.cache_creation_input_tokens"] = usage.cache_creation_tokens
    if usage.run_id:
        attrs["cbm.run_id"] = usage.run_id
    if usage.harness:
        attrs["cbm.harness"] = usage.harness
    if usage.profile_id:
        attrs["cbm.profile_id"] = usage.profile_id

    span = SpanRecord(
        name=f"usage/{usage.model or 'aggregate'}",
        trace_id=usage.trace_id or new_trace_id(),
        span_id=usage.span_id or new_span_id(),
        kind=SpanKind.INTERNAL,
        start_time_unix_nano=usage.recorded_at_unix_nano,
        end_time_unix_nano=usage.recorded_at_unix_nano,
        status_code=SpanStatus.OK,
        attributes=attrs,
        run_id=usage.run_id,
        profile_id=usage.profile_id,
        session_id=usage.session_id,
        harness=usage.harness,
        content_capture=False,
    )
    return span_to_otlp(span)


def build_otlp_export_request(
    spans: Iterable[SpanRecord],
    usage: Iterable[UsageRecord] | None = None,
    *,
    service_name: str = "cloakbrowser-manager",
    service_version: str | None = None,
) -> dict[str, Any]:
    """Build an OTLP/HTTP JSON ExportTraceServiceRequest body."""
    resource_attrs = [{"key": "service.name", "value": _otlp_any_value(service_name)}]
    if service_version:
        resource_attrs.append(
            {"key": "service.version", "value": _otlp_any_value(service_version)}
        )
    otlp_spans = [span_to_otlp(s) for s in spans]
    for item in usage or ():
        otlp_spans.append(usage_to_otlp_span(item))
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "cbm.observability",
                            "version": "0.1.0",
                        },
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


def export_otlp_http(
    body: dict[str, Any],
    *,
    endpoint: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
) -> tuple[int, str]:
    """POST OTLP JSON to an optional collector. Returns (status_code, body_text)."""
    validated = validate_otlp_http_endpoint(endpoint)
    if not validated:
        raise OtlpConfigError("OTLP endpoint is not configured")
    data = json.dumps(body).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "User-Agent": "cbm-observability/0.1",
    }
    if headers:
        # Do not allow overriding content type to something that would ship secrets.
        for key, value in headers.items():
            if key.lower() == "content-type":
                continue
            req_headers[key] = value
    request = urllib.request.Request(
        validated,
        data=data,
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return int(exc.code), detail
    except urllib.error.URLError as exc:
        raise OtlpConfigError(f"OTLP export failed: {exc.reason}") from exc


def _otlp_any_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"stringValue": ""}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"stringValue": str(value)}


def _span_kind(kind: str) -> int:
    # OTLP enum: 0=UNSPECIFIED, 1=INTERNAL, 2=SERVER, 3=CLIENT, 4=PRODUCER, 5=CONSUMER
    return {
        "INTERNAL": 1,
        "SERVER": 2,
        "CLIENT": 3,
        "PRODUCER": 4,
        "CONSUMER": 5,
    }.get(kind, 1)


def _status_code(code: str) -> int:
    # OTLP: 0=UNSET, 1=OK, 2=ERROR
    return {"UNSET": 0, "OK": 1, "ERROR": 2}.get(code, 0)
