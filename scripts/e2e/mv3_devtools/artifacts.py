"""Redacted artifact capture for MV3 DevTools E2E runs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.e2e.mv3_devtools.redaction import artifact_is_safe, redact_value


@dataclass
class ArtifactStore:
    root: Path
    blockers: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def record_step(self, name: str, payload: dict[str, Any], *, ok: bool | None = None) -> Path:
        safe = redact_value(payload)
        if not artifact_is_safe(safe):
            raise ValueError(f"artifact for step {name!r} failed safety redaction gate")
        entry = {
            "name": name,
            "ok": ok if ok is not None else bool(safe.get("ok", True)),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": safe,
        }
        self.steps.append(entry)
        path = self.root / f"step-{len(self.steps):02d}-{_slug(name)}.json"
        path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def record_blocker(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> Path:
        entry = {
            "code": code,
            "message": str(message)[:500],
            "details": redact_value(details or {}),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if not artifact_is_safe(entry):
            raise ValueError("blocker artifact failed safety redaction gate")
        self.blockers.append(entry)
        path = self.root / f"blocker-{len(self.blockers):02d}-{_slug(code)}.json"
        path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def write_summary(self, summary: dict[str, Any]) -> Path:
        safe = redact_value(summary)
        if not artifact_is_safe(safe):
            raise ValueError("summary artifact failed safety redaction gate")
        path = self.root / "summary.json"
        path.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:80] or "item"
