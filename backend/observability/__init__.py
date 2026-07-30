"""Local-first, vendor-neutral trace pipeline for CloakBrowser Manager.

This package records OpenTelemetry-style spans and token-usage aggregates
without requiring SaaS vendors. Content capture is off by default; prompts,
responses, cookies, and secrets are never stored.
"""

from __future__ import annotations

from .config import ObservabilityConfig, load_config
from .correlation import RunCorrelation, correlate_records
from .pipeline import TracePipeline
from .schema import SpanKind, SpanRecord, SpanStatus, UsageRecord
from .spool import JsonlSpool, SqliteSpool, open_spool
from .tokscale_import import import_tokscale_aggregate

__all__ = [
    "ObservabilityConfig",
    "RunCorrelation",
    "SpanKind",
    "SpanRecord",
    "SpanStatus",
    "SqliteSpool",
    "JsonlSpool",
    "TracePipeline",
    "UsageRecord",
    "correlate_records",
    "import_tokscale_aggregate",
    "load_config",
    "open_spool",
]

__version__ = "0.1.0"
