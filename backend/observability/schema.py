"""Canonical OpenTelemetry-style span and usage schema (local subset).

This is a vendor-neutral record shape inspired by the OpenTelemetry semantic
conventions for traces and GenAI usage. It does not require the OpenTelemetry
SDK or any SaaS exporter.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from .privacy import (
    DEFAULT_CONTENT_CAPTURE,
    PrivacyError,
    looks_secret_like,
    sanitize_attributes,
)

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class SpanKind(str, Enum):
    INTERNAL = "INTERNAL"
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


class SpanStatus(str, Enum):
    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_unix_nano() -> int:
    return time.time_ns()


def _validate_id(value: str, pattern: re.Pattern[str], label: str) -> str:
    cleaned = value.strip().lower()
    if not pattern.match(cleaned):
        raise ValueError(f"{label} must match {pattern.pattern}")
    return cleaned


@dataclass(slots=True)
class SpanRecord:
    """One span in the local canonical schema."""

    name: str
    trace_id: str = field(default_factory=new_trace_id)
    span_id: str = field(default_factory=new_span_id)
    parent_span_id: str | None = None
    kind: SpanKind = SpanKind.INTERNAL
    start_time_unix_nano: int = field(default_factory=_now_unix_nano)
    end_time_unix_nano: int | None = None
    status_code: SpanStatus = SpanStatus.UNSET
    status_message: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    # CloakBrowser / harness correlation (also mirrored into attributes).
    run_id: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    harness: str | None = None  # acp | acpx | browser-use | stagehand | unbrowse | ...
    content_capture: bool = DEFAULT_CONTENT_CAPTURE

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("span name is required")
        self.name = str(self.name).strip()[:256]
        self.trace_id = _validate_id(self.trace_id, _TRACE_ID_RE, "trace_id")
        self.span_id = _validate_id(self.span_id, _SPAN_ID_RE, "span_id")
        if self.parent_span_id is not None:
            self.parent_span_id = _validate_id(
                self.parent_span_id, _SPAN_ID_RE, "parent_span_id"
            )
        if isinstance(self.kind, str):
            self.kind = SpanKind(self.kind)
        if isinstance(self.status_code, str):
            self.status_code = SpanStatus(self.status_code)
        if self.end_time_unix_nano is not None and self.end_time_unix_nano < self.start_time_unix_nano:
            raise ValueError("end_time_unix_nano must be >= start_time_unix_nano")
        if self.status_message and len(self.status_message) > 256:
            self.status_message = self.status_message[:256]
        # Never allow status_message to carry secrets.
        if self.status_message and looks_secret_like(self.status_message):
            raise PrivacyError("status_message looks secret-like")

        attrs = dict(self.attributes or {})
        if self.run_id:
            attrs.setdefault("cbm.run_id", self.run_id)
        if self.profile_id:
            attrs.setdefault("cbm.profile_id", self.profile_id)
        if self.session_id:
            attrs.setdefault("cbm.session_id", self.session_id)
        if self.harness:
            attrs.setdefault("cbm.harness", self.harness)
        self.attributes = sanitize_attributes(
            attrs, content_capture=self.content_capture, strict=True
        )
        self.resource = sanitize_attributes(
            self.resource or {"service.name": "cloakbrowser-manager"},
            content_capture=False,
            strict=False,
        )

    def finish(
        self,
        *,
        status_code: SpanStatus | str = SpanStatus.OK,
        status_message: str | None = None,
        end_time_unix_nano: int | None = None,
    ) -> SpanRecord:
        self.end_time_unix_nano = end_time_unix_nano or _now_unix_nano()
        self.status_code = SpanStatus(status_code) if isinstance(status_code, str) else status_code
        if status_message is not None:
            self.status_message = status_message[:256]
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status_code"] = self.status_code.value
        data["record_type"] = "span"
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpanRecord:
        payload = dict(data)
        payload.pop("record_type", None)
        return cls(
            name=str(payload["name"]),
            trace_id=str(payload.get("trace_id") or new_trace_id()),
            span_id=str(payload.get("span_id") or new_span_id()),
            parent_span_id=payload.get("parent_span_id"),
            kind=SpanKind(payload.get("kind") or SpanKind.INTERNAL),
            start_time_unix_nano=int(payload.get("start_time_unix_nano") or _now_unix_nano()),
            end_time_unix_nano=(
                int(payload["end_time_unix_nano"])
                if payload.get("end_time_unix_nano") is not None
                else None
            ),
            status_code=SpanStatus(payload.get("status_code") or SpanStatus.UNSET),
            status_message=payload.get("status_message"),
            attributes=dict(payload.get("attributes") or {}),
            resource=dict(payload.get("resource") or {}),
            run_id=payload.get("run_id"),
            profile_id=payload.get("profile_id"),
            session_id=payload.get("session_id"),
            harness=payload.get("harness"),
            content_capture=bool(payload.get("content_capture", DEFAULT_CONTENT_CAPTURE)),
        )


@dataclass(slots=True)
class UsageRecord:
    """Token / cost usage aggregate. Never includes prompts or completions."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int | None = None
    cost_usd: float | None = None
    model: str | None = None
    provider: str | None = None
    usage_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str | None = None
    span_id: str | None = None
    run_id: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    harness: str | None = None
    source: str = "native"  # native | tokscale | import
    recorded_at_unix_nano: int = field(default_factory=_now_unix_nano)
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        ):
            value = int(getattr(self, field_name) or 0)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0")
            setattr(self, field_name, value)
        if self.total_tokens is None:
            self.total_tokens = (
                self.input_tokens
                + self.output_tokens
                + self.cache_read_tokens
                + self.cache_creation_tokens
            )
        elif int(self.total_tokens) < 0:
            raise ValueError("total_tokens must be >= 0")
        else:
            self.total_tokens = int(self.total_tokens)
        if self.cost_usd is not None:
            self.cost_usd = float(self.cost_usd)
            if self.cost_usd < 0:
                raise ValueError("cost_usd must be >= 0")
        if self.trace_id:
            self.trace_id = _validate_id(self.trace_id, _TRACE_ID_RE, "trace_id")
        if self.span_id:
            self.span_id = _validate_id(self.span_id, _SPAN_ID_RE, "span_id")
        if self.model:
            self.model = str(self.model).strip()[:128]
        if self.provider:
            self.provider = str(self.provider).strip()[:64]
        if self.source:
            self.source = str(self.source).strip()[:32]
        self.attributes = sanitize_attributes(
            self.attributes or {}, content_capture=False, strict=True
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["record_type"] = "usage"
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageRecord:
        payload = dict(data)
        payload.pop("record_type", None)
        return cls(
            usage_id=str(payload.get("usage_id") or uuid.uuid4().hex),
            input_tokens=int(payload.get("input_tokens") or 0),
            output_tokens=int(payload.get("output_tokens") or 0),
            cache_read_tokens=int(payload.get("cache_read_tokens") or 0),
            cache_creation_tokens=int(payload.get("cache_creation_tokens") or 0),
            total_tokens=payload.get("total_tokens"),
            cost_usd=payload.get("cost_usd"),
            model=payload.get("model"),
            provider=payload.get("provider"),
            trace_id=payload.get("trace_id"),
            span_id=payload.get("span_id"),
            run_id=payload.get("run_id"),
            profile_id=payload.get("profile_id"),
            session_id=payload.get("session_id"),
            harness=payload.get("harness"),
            source=str(payload.get("source") or "native"),
            recorded_at_unix_nano=int(
                payload.get("recorded_at_unix_nano") or _now_unix_nano()
            ),
            attributes=dict(payload.get("attributes") or {}),
        )
