"""Shared JSON-schema normalization for structured CLI model adapters."""

from __future__ import annotations

from typing import Any


_SCHEMA_KEY_ALIASES = {"min_items": "minItems"}
_SCHEMA_IDENTIFIER_MAPS = frozenset({"$defs", "definitions", "properties"})


def normalize_cli_json_schema(value: Any) -> Any:
    """Translate Browser-Use legacy schema keywords without renaming fields."""
    if isinstance(value, list):
        return [normalize_cli_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in _SCHEMA_IDENTIFIER_MAPS and isinstance(item, dict):
            normalized[key] = {
                identifier: normalize_cli_json_schema(schema)
                for identifier, schema in item.items()
            }
            continue
        normalized[_SCHEMA_KEY_ALIASES.get(key, key)] = normalize_cli_json_schema(item)
    return normalized
