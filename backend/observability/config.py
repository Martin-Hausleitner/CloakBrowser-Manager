"""Pipeline configuration. Local-first; SaaS exporters are optional."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .privacy import DEFAULT_CONTENT_CAPTURE

SpoolBackend = Literal["sqlite", "jsonl"]


@dataclass(slots=True)
class ObservabilityConfig:
    """Runtime config for the local trace pipeline.

    OTLP HTTP is optional. When ``otlp_http_endpoint`` is empty, nothing is
    sent off-box. Helicone and Langfuse are intentionally not hard dependencies.
    """

    spool_backend: SpoolBackend = "sqlite"
    spool_path: str = ".cbm/observability/traces.db"
    content_capture: bool = DEFAULT_CONTENT_CAPTURE
    otlp_http_endpoint: str | None = None
    otlp_headers: dict[str, str] | None = None
    service_name: str = "cloakbrowser-manager"
    service_version: str | None = None
    # TokScale importer expects safe aggregate JSON (version pin for docs).
    tokscale_expected_version: str = "4.7.0"

    def resolved_spool_path(self, base: Path | None = None) -> Path:
        path = Path(self.spool_path)
        if path.is_absolute():
            return path
        root = base or Path.cwd()
        return (root / path).resolve()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Never expose raw header secrets in dumps beyond redacted keys.
        if self.otlp_headers:
            data["otlp_headers"] = {k: "[redacted]" for k in self.otlp_headers}
        return data


def load_config(env: dict[str, str] | None = None) -> ObservabilityConfig:
    """Load config from environment variables (CBM_OTEL_* / CBM_TRACE_*)."""
    source = env if env is not None else os.environ
    backend = (source.get("CBM_TRACE_SPOOL_BACKEND") or "sqlite").strip().lower()
    if backend not in {"sqlite", "jsonl"}:
        backend = "sqlite"
    path = (
        source.get("CBM_TRACE_SPOOL_PATH")
        or source.get("CBM_OTEL_SPOOL_PATH")
        or ".cbm/observability/traces.db"
    )
    content_raw = (
        source.get("CBM_CONTENT_CAPTURE")
        or source.get("CBM_OTEL_CONTENT_CAPTURE")
        or "false"
    ).strip().lower()
    content_capture = content_raw in {"1", "true", "yes", "on"}
    endpoint = (
        source.get("CBM_OTLP_HTTP_ENDPOINT")
        or source.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or source.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ""
    ).strip() or None
    headers_raw = (
        source.get("CBM_OTLP_HEADERS")
        or source.get("OTEL_EXPORTER_OTLP_HEADERS")
        or ""
    ).strip()
    headers: dict[str, str] | None = None
    if headers_raw:
        headers = {}
        for part in headers_raw.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key:
                headers[key] = value.strip()
    service_name = (
        source.get("CBM_OTEL_SERVICE_NAME")
        or source.get("OTEL_SERVICE_NAME")
        or "cloakbrowser-manager"
    )
    service_version = source.get("CBM_OTEL_SERVICE_VERSION") or None
    tokscale_version = source.get("CBM_TOKSCALE_VERSION") or "4.7.0"
    return ObservabilityConfig(
        spool_backend=backend,  # type: ignore[arg-type]
        spool_path=path,
        content_capture=content_capture,
        otlp_http_endpoint=endpoint,
        otlp_headers=headers,
        service_name=service_name,
        service_version=service_version,
        tokscale_expected_version=tokscale_version,
    )
