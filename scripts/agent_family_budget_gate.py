#!/usr/bin/env python3
"""Local deterministic canonical meter for agent-family pipelines.

Metrics-only usage accounting with:
  - exact-repeat dedupe
  - conflicting event_id rejection
  - mirror double-count prevention (canonical_event_id + source precedence)
  - canonical-equivalence fingerprint before mirror dedupe/upgrade
  - per-run token and cost budgets (integer microusd only)
  - 0600 metrics-only report emission with redacted rejection ids

No network, no env secrets, no floats for money, no shell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_TOKEN_VALUE = 1_000_000_000_000  # 1e12 hard ceiling per field
MAX_COST_MICROUSD = 1_000_000_000_000_000  # 1e15 microusd ceiling
DEFAULT_MAX_EVENTS = 10_000
MAX_REPORTABLE_ID_LEN = 128

# Authoritative known providers for agent-family pipelines.
KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "xai",
        "grok",
        "codex",
        "claude",
        "cursor",
        "opencode",
        "antigravity",
        "azure-openai",
        "bedrock",
        "local",
    }
)

# Higher number wins when multiple mirrors report the same canonical_event_id.
SOURCE_PRECEDENCE: dict[str, int] = {
    "provider": 3,
    "acpx": 2,
    "orca": 1,
}

REQUIRED_FIELDS: tuple[str, ...] = (
    "event_id",
    "run_id",
    "stage_id",
    "provider",
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "source",
    "timestamp",
)

# Metrics-only: reject any payload that looks like raw prompts or secrets.
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "prompt",
        "messages",
        "content",
        "api_key",
        "secret",
        "authorization",
        "raw_response",
        "completion",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "system_prompt",
        "user_prompt",
        "raw_prompt",
        "body",
        "headers",
    }
)

# Identifiers that must never appear raw in rejection reports.
_SECRET_ID_RE = re.compile(
    r"(?i)("
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|api[_-]?key"
    r"|bearer\s+\S+"
    r"|password"
    r"|secret"
    r"|-----BEGIN"
    r"|token=[^\s&]+"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r")"
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+\-/]{0,127}$")

# Narrow allowlist: only these non-accepted outcomes are benign (not rejections,
# do not flip report.passed). Everything else fail-closes by default.
BENIGN_INGEST_OUTCOMES: frozenset[str] = frozenset(
    {
        "deduped_exact_repeat",
        "deduped_mirror",
    }
)


class GateError(RuntimeError):
    """Deterministic gate failure (CLI / load errors)."""


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: str
    event_id: str = ""
    run_id: str = ""


@dataclass
class _StoredEvent:
    event_id: str
    run_id: str
    stage_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    cost_microusd: int
    source: str
    timestamp: str
    canonical_event_id: str
    fingerprint: tuple[Any, ...]
    canonical_equiv: tuple[Any, ...]


@dataclass
class _RunTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    cost_microusd: int = 0
    events_accepted: int = 0
    events_deduped: int = 0
    events_rejected: int = 0
    token_budget_breached: bool = False
    cost_budget_breached: bool = False
    conflict: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_tokens


def _hash_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"redacted:{digest}"


def reportable_id(value: str | None) -> str:
    """Return a metrics-safe identifier for rejection reports.

    Safe opaque ids pass through (length-bounded). Secret-looking or
    otherwise invalid values are replaced with a stable hash prefix so the
    raw marker never appears in reports.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return _hash_id(repr(value))
    if value == "":
        return ""
    if len(value) > MAX_REPORTABLE_ID_LEN:
        return _hash_id(value)
    if _SECRET_ID_RE.search(value):
        return _hash_id(value)
    if not _SAFE_ID_RE.match(value):
        return _hash_id(value)
    return value


def canonical_equivalence(
    *,
    run_id: str,
    stage_id: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cache_tokens: int,
    cost_microusd: int,
) -> tuple[Any, ...]:
    """Billable identity shared by mirrors of the same canonical_event_id.

    Source and timestamp intentionally excluded so provider/acpx/orca mirrors
    can still dedupe/upgrade when metrics match.
    """
    return (
        run_id,
        stage_id,
        provider,
        input_tokens,
        output_tokens,
        cache_tokens,
        cost_microusd,
    )


@dataclass
class BudgetMeter:
    """Thread-safe canonical meter for one budget envelope."""

    budget: Mapping[str, Any]
    max_events: int = DEFAULT_MAX_EVENTS
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _by_event_id: dict[str, _StoredEvent] = field(default_factory=dict, repr=False)
    # canonical_event_id -> currently winning stored event
    _by_canonical: dict[str, _StoredEvent] = field(default_factory=dict, repr=False)
    _runs: dict[str, _RunTotals] = field(default_factory=dict, repr=False)
    _rejections: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _accepted_count: int = 0  # currently counted (winning) events
    _ingest_attempts: int = 0

    def __post_init__(self) -> None:
        max_tokens = self.budget.get("max_tokens_per_run")
        max_cost = self.budget.get("max_cost_microusd_per_run")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
            raise GateError("budget.max_tokens_per_run must be a non-negative int")
        if not isinstance(max_cost, int) or isinstance(max_cost, bool) or max_cost < 0:
            raise GateError("budget.max_cost_microusd_per_run must be a non-negative int")
        if not isinstance(self.max_events, int) or self.max_events < 1:
            raise GateError("max_events must be a positive int")

    def ingest(self, raw: Mapping[str, Any]) -> IngestResult:
        with self._lock:
            self._ingest_attempts += 1
            return self._ingest_unlocked(raw)

    def _record_rejection(
        self,
        *,
        event_id: str,
        run_id: str,
        reason: str,
        stage_id: str = "",
        canonical_event_id: str = "",
        mark_run: bool = True,
    ) -> None:
        if mark_run and run_id:
            run = self._runs.setdefault(run_id, _RunTotals())
            run.events_rejected += 1
            if reason == "token_budget_exceeded":
                run.token_budget_breached = True
            if reason == "cost_budget_exceeded":
                run.cost_budget_breached = True
            if reason in {"conflicting_event_id", "conflicting_canonical_event_id"}:
                run.conflict = True
        self._rejections.append(
            {
                "event_id": reportable_id(event_id),
                "run_id": reportable_id(run_id),
                "stage_id": reportable_id(stage_id),
                "canonical_event_id": reportable_id(canonical_event_id),
                "reason": reason,
            }
        )

    def _ingest_unlocked(self, raw: Mapping[str, Any]) -> IngestResult:
        if not isinstance(raw, Mapping):
            return self._reject("", "", "invalid_type")

        # Bound applies to accepted+currently-stored winners; also block flood of new ids.
        raw_event_id = raw.get("event_id", "")
        if (
            len(self._by_event_id) >= self.max_events
            and str(raw_event_id) not in self._by_event_id
        ):
            return self._reject(
                str(raw_event_id),
                str(raw.get("run_id", "")),
                "event_bound_exceeded",
            )

        for key in raw:
            if str(key).lower() in FORBIDDEN_FIELDS:
                return self._reject(
                    str(raw.get("event_id", "")),
                    str(raw.get("run_id", "")),
                    "forbidden_field",
                    stage_id=str(raw.get("stage_id", "") or ""),
                    canonical_event_id=str(raw.get("canonical_event_id", "") or ""),
                )

        for req in REQUIRED_FIELDS:
            if req not in raw or raw[req] is None or raw[req] == "":
                return self._reject(
                    str(raw.get("event_id", "")),
                    str(raw.get("run_id", "")),
                    "missing_field",
                    stage_id=str(raw.get("stage_id", "") or ""),
                    canonical_event_id=str(raw.get("canonical_event_id", "") or ""),
                )

        event_id = raw["event_id"]
        run_id = raw["run_id"]
        stage_id = raw["stage_id"]
        provider = raw["provider"]
        source = raw["source"]
        timestamp = raw["timestamp"]

        if not isinstance(event_id, str) or not event_id:
            return self._reject(
                "",
                str(run_id) if isinstance(run_id, str) else "",
                "invalid_type",
            )
        if not isinstance(run_id, str) or not run_id:
            return self._reject(event_id, "", "invalid_type")
        if not isinstance(stage_id, str) or not stage_id:
            return self._reject(event_id, run_id, "invalid_type", stage_id="")
        if not isinstance(provider, str):
            return self._reject(event_id, run_id, "invalid_type", stage_id=stage_id)
        if not isinstance(source, str):
            return self._reject(event_id, run_id, "invalid_type", stage_id=stage_id)
        if not isinstance(timestamp, str) or not timestamp:
            return self._reject(event_id, run_id, "invalid_type", stage_id=stage_id)

        if provider not in KNOWN_PROVIDERS:
            return self._reject(
                event_id,
                run_id,
                "unknown_provider",
                stage_id=stage_id,
            )
        if source not in SOURCE_PRECEDENCE:
            return self._reject(
                event_id,
                run_id,
                "unknown_source",
                stage_id=stage_id,
            )

        # Resolve canonical id before metric validation so rejection reports
        # can always redact it when present.
        canonical = raw.get("canonical_event_id")
        if canonical is None or canonical == "":
            canonical_event_id = event_id
        elif not isinstance(canonical, str):
            return self._reject(event_id, run_id, "invalid_type", stage_id=stage_id)
        else:
            canonical_event_id = canonical

        try:
            input_tokens = _require_nonneg_int(raw["input_tokens"], "token")
            output_tokens = _require_nonneg_int(raw["output_tokens"], "token")
            cache_tokens = _require_nonneg_int(raw["cache_tokens"], "token")
        except _ValueReject as exc:
            return self._reject(
                event_id,
                run_id,
                exc.reason,
                stage_id=stage_id,
                canonical_event_id=canonical_event_id,
            )

        if "cost_microusd" not in raw or raw["cost_microusd"] is None:
            cost_microusd = 0
        else:
            try:
                cost_microusd = _require_nonneg_int(raw["cost_microusd"], "money")
            except _ValueReject as exc:
                return self._reject(
                    event_id,
                    run_id,
                    exc.reason,
                    stage_id=stage_id,
                    canonical_event_id=canonical_event_id,
                )

        canon_equiv = canonical_equivalence(
            run_id=run_id,
            stage_id=stage_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            cost_microusd=cost_microusd,
        )
        fingerprint = (
            *canon_equiv,
            source,
            timestamp,
            canonical_event_id,
        )

        # Exact / conflict check on event_id.
        existing = self._by_event_id.get(event_id)
        if existing is not None:
            if existing.fingerprint == fingerprint:
                run = self._runs.setdefault(run_id, _RunTotals())
                run.events_deduped += 1
                return IngestResult(False, "deduped_exact_repeat", event_id, run_id)
            self._record_rejection(
                event_id=event_id,
                run_id=run_id,
                reason="conflicting_event_id",
                stage_id=stage_id,
                canonical_event_id=canonical_event_id,
            )
            return IngestResult(False, "conflicting_event_id", event_id, run_id)

        stored = _StoredEvent(
            event_id=event_id,
            run_id=run_id,
            stage_id=stage_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            cost_microusd=cost_microusd,
            source=source,
            timestamp=timestamp,
            canonical_event_id=canonical_event_id,
            fingerprint=fingerprint,
            canonical_equiv=canon_equiv,
        )

        winner = self._by_canonical.get(canonical_event_id)
        if winner is not None:
            # Mirrors must share billable identity; otherwise fail closed.
            if winner.canonical_equiv != stored.canonical_equiv:
                self._record_rejection(
                    event_id=event_id,
                    run_id=run_id,
                    reason="conflicting_canonical_event_id",
                    stage_id=stage_id,
                    canonical_event_id=canonical_event_id,
                )
                return IngestResult(
                    False,
                    "conflicting_canonical_event_id",
                    event_id,
                    run_id,
                )

            win_rank = SOURCE_PRECEDENCE[winner.source]
            new_rank = SOURCE_PRECEDENCE[source]
            if new_rank <= win_rank:
                # Same or lower precedence mirror of an already-counted event.
                self._by_event_id[event_id] = stored
                run = self._runs.setdefault(run_id, _RunTotals())
                run.events_deduped += 1
                return IngestResult(False, "deduped_mirror", event_id, run_id)
            # Higher precedence and equivalent metrics: replace winner.
            return self._upgrade_winner(stored, winner)

        # Fresh canonical identity — budget check then accept.
        return self._accept_new(stored)

    def _accept_new(self, stored: _StoredEvent) -> IngestResult:
        run = self._runs.setdefault(stored.run_id, _RunTotals())
        max_tokens = int(self.budget["max_tokens_per_run"])
        max_cost = int(self.budget["max_cost_microusd_per_run"])
        add_tokens = stored.input_tokens + stored.output_tokens + stored.cache_tokens
        new_tokens = run.total_tokens + add_tokens
        new_cost = run.cost_microusd + stored.cost_microusd

        if new_tokens > max_tokens:
            self._record_rejection(
                event_id=stored.event_id,
                run_id=stored.run_id,
                reason="token_budget_exceeded",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )
            return IngestResult(
                False,
                "token_budget_exceeded",
                stored.event_id,
                stored.run_id,
            )

        if new_cost > max_cost:
            self._record_rejection(
                event_id=stored.event_id,
                run_id=stored.run_id,
                reason="cost_budget_exceeded",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )
            return IngestResult(
                False,
                "cost_budget_exceeded",
                stored.event_id,
                stored.run_id,
            )

        # Bound on currently accepted winners.
        if self._accepted_count >= self.max_events:
            return self._reject(
                stored.event_id,
                stored.run_id,
                "event_bound_exceeded",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )

        self._by_event_id[stored.event_id] = stored
        self._by_canonical[stored.canonical_event_id] = stored
        run.input_tokens += stored.input_tokens
        run.output_tokens += stored.output_tokens
        run.cache_tokens += stored.cache_tokens
        run.cost_microusd += stored.cost_microusd
        run.events_accepted += 1
        self._accepted_count += 1
        return IngestResult(True, "accepted", stored.event_id, stored.run_id)

    def _upgrade_winner(self, stored: _StoredEvent, previous: _StoredEvent) -> IngestResult:
        """Replace a lower-precedence mirror with a higher-precedence one.

        Caller must already verify canonical_equiv match. Token/cost deltas
        keep totals reflecting only the winning mirror.
        """
        if stored.canonical_equiv != previous.canonical_equiv:
            # Defensive: never upgrade on mismatched billable identity.
            self._record_rejection(
                event_id=stored.event_id,
                run_id=stored.run_id,
                reason="conflicting_canonical_event_id",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )
            return IngestResult(
                False,
                "conflicting_canonical_event_id",
                stored.event_id,
                stored.run_id,
            )

        run = self._runs.setdefault(stored.run_id, _RunTotals())
        prev_run = self._runs.setdefault(previous.run_id, _RunTotals())

        # Remove previous contribution.
        prev_run.input_tokens -= previous.input_tokens
        prev_run.output_tokens -= previous.output_tokens
        prev_run.cache_tokens -= previous.cache_tokens
        prev_run.cost_microusd -= previous.cost_microusd
        if prev_run.events_accepted > 0:
            prev_run.events_accepted -= 1
        self._accepted_count = max(0, self._accepted_count - 1)

        max_tokens = int(self.budget["max_tokens_per_run"])
        max_cost = int(self.budget["max_cost_microusd_per_run"])
        add_tokens = stored.input_tokens + stored.output_tokens + stored.cache_tokens
        new_tokens = run.total_tokens + add_tokens
        new_cost = run.cost_microusd + stored.cost_microusd

        if new_tokens > max_tokens:
            # Roll back previous removal to keep meter consistent, reject upgrade.
            prev_run.input_tokens += previous.input_tokens
            prev_run.output_tokens += previous.output_tokens
            prev_run.cache_tokens += previous.cache_tokens
            prev_run.cost_microusd += previous.cost_microusd
            prev_run.events_accepted += 1
            self._accepted_count += 1
            self._record_rejection(
                event_id=stored.event_id,
                run_id=stored.run_id,
                reason="token_budget_exceeded",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )
            return IngestResult(
                False,
                "token_budget_exceeded",
                stored.event_id,
                stored.run_id,
            )

        if new_cost > max_cost:
            prev_run.input_tokens += previous.input_tokens
            prev_run.output_tokens += previous.output_tokens
            prev_run.cache_tokens += previous.cache_tokens
            prev_run.cost_microusd += previous.cost_microusd
            prev_run.events_accepted += 1
            self._accepted_count += 1
            self._record_rejection(
                event_id=stored.event_id,
                run_id=stored.run_id,
                reason="cost_budget_exceeded",
                stage_id=stored.stage_id,
                canonical_event_id=stored.canonical_event_id,
            )
            return IngestResult(
                False,
                "cost_budget_exceeded",
                stored.event_id,
                stored.run_id,
            )

        self._by_event_id[stored.event_id] = stored
        self._by_canonical[stored.canonical_event_id] = stored
        run.input_tokens += stored.input_tokens
        run.output_tokens += stored.output_tokens
        run.cache_tokens += stored.cache_tokens
        run.cost_microusd += stored.cost_microusd
        run.events_accepted += 1
        self._accepted_count += 1
        return IngestResult(
            True,
            "accepted_precedence_upgrade",
            stored.event_id,
            stored.run_id,
        )

    def _reject(
        self,
        event_id: str,
        run_id: str,
        reason: str,
        *,
        stage_id: str = "",
        canonical_event_id: str = "",
    ) -> IngestResult:
        self._record_rejection(
            event_id=event_id,
            run_id=run_id,
            reason=reason,
            stage_id=stage_id,
            canonical_event_id=canonical_event_id,
        )
        return IngestResult(False, reason, event_id, run_id)

    def report(self) -> dict[str, Any]:
        with self._lock:
            runs_out: dict[str, Any] = {}
            totals_in = 0
            totals_out = 0
            totals_cache = 0
            totals_cost = 0
            any_fail = False
            for run_id in sorted(self._runs):
                run = self._runs[run_id]
                budget_ok = (
                    not run.token_budget_breached
                    and not run.cost_budget_breached
                    and not run.conflict
                    and run.total_tokens <= int(self.budget["max_tokens_per_run"])
                    and run.cost_microusd <= int(self.budget["max_cost_microusd_per_run"])
                )
                if not budget_ok:
                    any_fail = True
                payload = {
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "cache_tokens": run.cache_tokens,
                    "total_tokens": run.total_tokens,
                    "cost_microusd": run.cost_microusd,
                    "events_accepted": run.events_accepted,
                    "events_deduped": run.events_deduped,
                    "events_rejected": run.events_rejected,
                    "budget_ok": budget_ok,
                    "token_budget_breached": run.token_budget_breached,
                    "cost_budget_breached": run.cost_budget_breached,
                    "conflict": run.conflict,
                    "max_tokens_per_run": int(self.budget["max_tokens_per_run"]),
                    "max_cost_microusd_per_run": int(
                        self.budget["max_cost_microusd_per_run"]
                    ),
                }
                # Never emit secret-looking run identifiers in the report surface.
                runs_out[reportable_id(run_id)] = payload
                totals_in += run.input_tokens
                totals_out += run.output_tokens
                totals_cache += run.cache_tokens
                totals_cost += run.cost_microusd

            # Fail closed: any recorded rejection that is not in the narrow
            # benign allowlist makes the report fail (future reasons included).
            for rej in self._rejections:
                reason = str(rej.get("reason") or "")
                if reason not in BENIGN_INGEST_OUTCOMES:
                    any_fail = True

            return {
                "schema_version": SCHEMA_VERSION,
                "passed": not any_fail,
                "metrics_only": True,
                "ingest_attempts": self._ingest_attempts,
                "accepted_events": self._accepted_count,
                "rejection_count": len(self._rejections),
                "runs": runs_out,
                "totals": {
                    "input_tokens": totals_in,
                    "output_tokens": totals_out,
                    "cache_tokens": totals_cache,
                    "total_tokens": totals_in + totals_out + totals_cache,
                    "cost_microusd": totals_cost,
                },
                "rejections": list(self._rejections),
            }


class _ValueReject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _require_nonneg_int(value: Any, kind: str) -> int:
    """Validate integer metrics. kind is 'token' or 'money'."""
    if isinstance(value, bool) or not isinstance(value, int):
        if kind == "money" and isinstance(value, float):
            raise _ValueReject("non_integer_money")
        if kind == "token":
            raise _ValueReject("non_integer_token")
        raise _ValueReject("invalid_type")
    if value < 0:
        raise _ValueReject("negative_value")
    ceiling = MAX_COST_MICROUSD if kind == "money" else MAX_TOKEN_VALUE
    if value > ceiling:
        raise _ValueReject("overflow_value")
    return value


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    """Write metrics-only JSON report with mode 0600."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(report, indent=2, sort_keys=True) + "\n"
    # Write privately: create/truncate with 0600 then write.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            # fd is owned by handle; do not close twice.
            fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    os.chmod(path, 0o600)


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load events from JSON array or JSONL. Metrics-only validation deferred to meter."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise GateError("events JSON must be a list or JSONL")
        out: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise GateError("each event must be an object")
            out.append(item)
        return out
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GateError(f"invalid JSONL at line {line_no}: {exc}") from exc
        if not isinstance(item, dict):
            raise GateError(f"event at line {line_no} must be an object")
        events.append(item)
    return events


def load_budget(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GateError("budget must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Canonical agent-family budget meter "
            "(metrics-only, local, deterministic)."
        ),
    )
    parser.add_argument(
        "--events",
        required=True,
        type=Path,
        help="Path to usage events (JSON array or JSONL).",
    )
    parser.add_argument(
        "--budget",
        required=True,
        type=Path,
        help="Path to budget JSON (max_tokens_per_run, max_cost_microusd_per_run).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path for 0600 metrics-only report JSON.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=DEFAULT_MAX_EVENTS,
        help=f"Hard bound on distinct event_ids (default {DEFAULT_MAX_EVENTS}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        events = load_events(args.events)
        budget = load_budget(args.budget)
        meter = BudgetMeter(budget, max_events=int(args.max_events))
        for event in events:
            meter.ingest(event)
        report = meter.report()
        write_report(args.output, report)
    except (GateError, OSError, json.JSONDecodeError, ValueError) as exc:
        # Fail closed: still try to emit a minimal metrics-only failure report.
        fail_report = {
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "metrics_only": True,
            "error": type(exc).__name__,
            "runs": {},
            "totals": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_tokens": 0,
                "total_tokens": 0,
                "cost_microusd": 0,
            },
            "rejections": [
                {
                    "event_id": "",
                    "run_id": "",
                    "stage_id": "",
                    "canonical_event_id": "",
                    "reason": "load_error",
                }
            ],
        }
        try:
            write_report(args.output, fail_report)
        except OSError:
            pass
        print(f"agent_family_budget_gate: {exc}", file=sys.stderr)
        return 2

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
