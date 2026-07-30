"""Local span/usage spool: SQLite or JSONL. No SaaS required."""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

from .schema import SpanRecord, UsageRecord

RecordType = Literal["span", "usage"]


class Spool(ABC):
    """Append-only local store for spans and usage records."""

    @abstractmethod
    def append_span(self, span: SpanRecord) -> None: ...

    @abstractmethod
    def append_usage(self, usage: UsageRecord) -> None: ...

    @abstractmethod
    def iter_spans(self) -> Iterator[SpanRecord]: ...

    @abstractmethod
    def iter_usage(self) -> Iterator[UsageRecord]: ...

    @abstractmethod
    def count(self) -> dict[str, int]: ...

    @abstractmethod
    def close(self) -> None: ...

    def append_many(
        self,
        spans: Iterable[SpanRecord] | None = None,
        usage: Iterable[UsageRecord] | None = None,
    ) -> None:
        for span in spans or ():
            self.append_span(span)
        for item in usage or ():
            self.append_usage(item)


class JsonlSpool(Spool):
    """Newline-delimited JSON spool. Simple and portable."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self.path.touch()

    def append_span(self, span: SpanRecord) -> None:
        self._write(span.to_dict())

    def append_usage(self, usage: UsageRecord) -> None:
        self._write(usage.to_dict())

    def _write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _iter_records(self, record_type: RecordType) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("record_type") == record_type:
                    yield payload

    def iter_spans(self) -> Iterator[SpanRecord]:
        for payload in self._iter_records("span"):
            yield SpanRecord.from_dict(payload)

    def iter_usage(self) -> Iterator[UsageRecord]:
        for payload in self._iter_records("usage"):
            yield UsageRecord.from_dict(payload)

    def count(self) -> dict[str, int]:
        spans = sum(1 for _ in self.iter_spans())
        usage = sum(1 for _ in self.iter_usage())
        return {"spans": spans, "usage": usage}

    def close(self) -> None:
        return None


class SqliteSpool(Spool):
    """SQLite spool for queryable local storage."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Local spool: WAL + NORMAL sync keeps durability while avoiding
        # full fsync on every single-span commit.
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA temp_store=MEMORY")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    start_time_unix_nano INTEGER NOT NULL,
                    end_time_unix_nano INTEGER,
                    status_code TEXT NOT NULL,
                    status_message TEXT,
                    run_id TEXT,
                    profile_id TEXT,
                    session_id TEXT,
                    harness TEXT,
                    attributes_json TEXT NOT NULL,
                    resource_json TEXT NOT NULL,
                    content_capture INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
                CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id);
                CREATE INDEX IF NOT EXISTS idx_spans_harness ON spans(harness);

                CREATE TABLE IF NOT EXISTS usage_records (
                    usage_id TEXT PRIMARY KEY,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    cache_creation_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost_usd REAL,
                    model TEXT,
                    provider TEXT,
                    trace_id TEXT,
                    span_id TEXT,
                    run_id TEXT,
                    profile_id TEXT,
                    session_id TEXT,
                    harness TEXT,
                    source TEXT NOT NULL,
                    recorded_at_unix_nano INTEGER NOT NULL,
                    attributes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_run ON usage_records(run_id);
                CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_records(model);
                """
            )
            self._conn.commit()

    def append_span(self, span: SpanRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO spans (
                    span_id, trace_id, parent_span_id, name, kind,
                    start_time_unix_nano, end_time_unix_nano, status_code,
                    status_message, run_id, profile_id, session_id, harness,
                    attributes_json, resource_json, content_capture
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.name,
                    span.kind.value,
                    span.start_time_unix_nano,
                    span.end_time_unix_nano,
                    span.status_code.value,
                    span.status_message,
                    span.run_id,
                    span.profile_id,
                    span.session_id,
                    span.harness,
                    json.dumps(span.attributes, separators=(",", ":")),
                    json.dumps(span.resource, separators=(",", ":")),
                    1 if span.content_capture else 0,
                ),
            )
            self._conn.commit()

    def append_usage(self, usage: UsageRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO usage_records (
                    usage_id, input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, total_tokens, cost_usd, model, provider,
                    trace_id, span_id, run_id, profile_id, session_id, harness,
                    source, recorded_at_unix_nano, attributes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.usage_id,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_creation_tokens,
                    usage.total_tokens or 0,
                    usage.cost_usd,
                    usage.model,
                    usage.provider,
                    usage.trace_id,
                    usage.span_id,
                    usage.run_id,
                    usage.profile_id,
                    usage.session_id,
                    usage.harness,
                    usage.source,
                    usage.recorded_at_unix_nano,
                    json.dumps(usage.attributes, separators=(",", ":")),
                ),
            )
            self._conn.commit()

    def iter_spans(self) -> Iterator[SpanRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans ORDER BY start_time_unix_nano ASC"
            ).fetchall()
        for row in rows:
            yield SpanRecord.from_dict(
                {
                    "span_id": row["span_id"],
                    "trace_id": row["trace_id"],
                    "parent_span_id": row["parent_span_id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "start_time_unix_nano": row["start_time_unix_nano"],
                    "end_time_unix_nano": row["end_time_unix_nano"],
                    "status_code": row["status_code"],
                    "status_message": row["status_message"],
                    "run_id": row["run_id"],
                    "profile_id": row["profile_id"],
                    "session_id": row["session_id"],
                    "harness": row["harness"],
                    "attributes": json.loads(row["attributes_json"] or "{}"),
                    "resource": json.loads(row["resource_json"] or "{}"),
                    "content_capture": bool(row["content_capture"]),
                }
            )

    def iter_usage(self) -> Iterator[UsageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM usage_records ORDER BY recorded_at_unix_nano ASC"
            ).fetchall()
        for row in rows:
            yield UsageRecord.from_dict(
                {
                    "usage_id": row["usage_id"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "cache_read_tokens": row["cache_read_tokens"],
                    "cache_creation_tokens": row["cache_creation_tokens"],
                    "total_tokens": row["total_tokens"],
                    "cost_usd": row["cost_usd"],
                    "model": row["model"],
                    "provider": row["provider"],
                    "trace_id": row["trace_id"],
                    "span_id": row["span_id"],
                    "run_id": row["run_id"],
                    "profile_id": row["profile_id"],
                    "session_id": row["session_id"],
                    "harness": row["harness"],
                    "source": row["source"],
                    "recorded_at_unix_nano": row["recorded_at_unix_nano"],
                    "attributes": json.loads(row["attributes_json"] or "{}"),
                }
            )

    def count(self) -> dict[str, int]:
        with self._lock:
            spans = self._conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
            usage = self._conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0]
        return {"spans": int(spans), "usage": int(usage)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_spool(backend: str, path: Path | str) -> Spool:
    backend_norm = (backend or "sqlite").strip().lower()
    if backend_norm == "jsonl":
        return JsonlSpool(path)
    if backend_norm == "sqlite":
        return SqliteSpool(path)
    raise ValueError(f"unknown spool backend: {backend}")
