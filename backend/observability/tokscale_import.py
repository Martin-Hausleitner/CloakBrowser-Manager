"""TokScale 4.7.0 safe aggregate JSON importer.

Imports only aggregate token/cost counters. Refuses payloads that contain
prompts, responses, messages, cookies, or other content fields.

TokScale itself is an optional external CLI; this module does not depend on
the TokScale package. Expected document shape (safe aggregate):

{
  "version": "4.7.0",
  "source": "tokscale",
  "period": "2026-07",
  "models": [
    {
      "model": "claude-opus",
      "provider": "anthropic",
      "input_tokens": 1000,
      "output_tokens": 200,
      "cache_read_tokens": 0,
      "cache_creation_tokens": 0,
      "cost_usd": 1.23
    }
  ],
  "totals": {
    "input_tokens": 1000,
    "output_tokens": 200,
    "cost_usd": 1.23
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .privacy import PrivacyError, assert_no_content_payload
from .schema import UsageRecord

DEFAULT_TOKSCALE_VERSION = "4.7.0"


class TokScaleImportError(ValueError):
    """Invalid or unsafe TokScale aggregate payload."""


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TokScaleImportError(f"expected integer token count, got {value!r}") from exc
    if number < 0:
        raise TokScaleImportError("token counts must be >= 0")
    return number


def _as_float_opt(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TokScaleImportError(f"expected numeric cost, got {value!r}") from exc
    if number < 0:
        raise TokScaleImportError("cost_usd must be >= 0")
    return number


def _model_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "models" in payload and isinstance(payload["models"], list):
        return [row for row in payload["models"] if isinstance(row, Mapping)]
    # Alternate shapes some exporters use.
    if "breakdown" in payload and isinstance(payload["breakdown"], list):
        return [row for row in payload["breakdown"] if isinstance(row, Mapping)]
    if "data" in payload and isinstance(payload["data"], list):
        return [row for row in payload["data"] if isinstance(row, Mapping)]
    return []


def import_tokscale_aggregate(
    payload: Mapping[str, Any] | str | Path,
    *,
    run_id: str | None = None,
    profile_id: str | None = None,
    session_id: str | None = None,
    harness: str | None = "tokscale",
    expected_version: str = DEFAULT_TOKSCALE_VERSION,
    require_version: bool = False,
    include_totals_row: bool = True,
) -> list[UsageRecord]:
    """Parse safe TokScale aggregate JSON into UsageRecord rows.

    Raises PrivacyError if content-like keys are present.
    Raises TokScaleImportError on structural problems.
    """
    if isinstance(payload, (str, Path)) and not isinstance(payload, Mapping):
        path = Path(payload)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        else:
            data = json.loads(str(payload))
    else:
        data = dict(payload)  # type: ignore[arg-type]

    if not isinstance(data, Mapping):
        raise TokScaleImportError("TokScale aggregate must be a JSON object")

    # Absolute content ban before any field mapping.
    assert_no_content_payload(data)

    version = str(data.get("version") or data.get("tokscale_version") or "").strip()
    if require_version:
        if version != expected_version:
            raise TokScaleImportError(
                f"expected TokScale version {expected_version}, got {version or 'missing'}"
            )
    elif version and version != expected_version:
        # Soft pin: allow import but stamp attribute for audit.
        pass

    records: list[UsageRecord] = []
    rows = _model_rows(data)
    for row in rows:
        assert_no_content_payload(row)
        model = row.get("model") or row.get("name") or row.get("model_name")
        provider = row.get("provider") or row.get("vendor")
        input_tokens = _as_int(
            row.get("input_tokens", row.get("input", row.get("prompt_tokens", 0)))
        )
        # prompt_tokens is a count field name used by some tools; content itself is forbidden.
        output_tokens = _as_int(
            row.get("output_tokens", row.get("output", row.get("completion_tokens", 0)))
        )
        cache_read = _as_int(
            row.get(
                "cache_read_tokens",
                row.get("cache_read", row.get("cache_read_input_tokens", 0)),
            )
        )
        cache_create = _as_int(
            row.get(
                "cache_creation_tokens",
                row.get("cache_creation", row.get("cache_creation_input_tokens", 0)),
            )
        )
        cost = _as_float_opt(row.get("cost_usd", row.get("cost", row.get("total_cost"))))
        total = row.get("total_tokens")
        attrs: dict[str, Any] = {}
        if version:
            attrs["tokscale.version"] = version
        # tokscale.version is not in SAFE list — put version into source metadata only.
        records.append(
            UsageRecord(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_create,
                total_tokens=_as_int(total) if total is not None else None,
                cost_usd=cost,
                model=str(model) if model else None,
                provider=str(provider) if provider else None,
                run_id=run_id,
                profile_id=profile_id,
                session_id=session_id,
                harness=harness,
                source="tokscale",
                attributes={},
            )
        )

    totals = data.get("totals")
    if include_totals_row and isinstance(totals, Mapping):
        assert_no_content_payload(totals)
        if not rows:
            # Totals-only export is valid.
            records.append(
                UsageRecord(
                    input_tokens=_as_int(totals.get("input_tokens", totals.get("input", 0))),
                    output_tokens=_as_int(
                        totals.get("output_tokens", totals.get("output", 0))
                    ),
                    cache_read_tokens=_as_int(totals.get("cache_read_tokens", 0)),
                    cache_creation_tokens=_as_int(totals.get("cache_creation_tokens", 0)),
                    total_tokens=(
                        _as_int(totals["total_tokens"])
                        if totals.get("total_tokens") is not None
                        else None
                    ),
                    cost_usd=_as_float_opt(totals.get("cost_usd", totals.get("cost"))),
                    model="__totals__",
                    provider=None,
                    run_id=run_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    harness=harness,
                    source="tokscale",
                )
            )
        elif not records:
            pass
        else:
            # Optional summary row when models already present.
            records.append(
                UsageRecord(
                    input_tokens=_as_int(totals.get("input_tokens", totals.get("input", 0))),
                    output_tokens=_as_int(
                        totals.get("output_tokens", totals.get("output", 0))
                    ),
                    cache_read_tokens=_as_int(totals.get("cache_read_tokens", 0)),
                    cache_creation_tokens=_as_int(totals.get("cache_creation_tokens", 0)),
                    total_tokens=(
                        _as_int(totals["total_tokens"])
                        if totals.get("total_tokens") is not None
                        else None
                    ),
                    cost_usd=_as_float_opt(totals.get("cost_usd", totals.get("cost"))),
                    model="__totals__",
                    run_id=run_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    harness=harness,
                    source="tokscale",
                )
            )

    if not records:
        raise TokScaleImportError(
            "no usage rows found; expected models[] and/or totals object"
        )
    return records


def load_tokscale_file(path: Path | str, **kwargs: Any) -> list[UsageRecord]:
    return import_tokscale_aggregate(Path(path), **kwargs)
