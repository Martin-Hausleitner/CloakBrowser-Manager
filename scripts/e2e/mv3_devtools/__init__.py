"""Strict real-Chromium E2E harness for the cloak-profile-sync MV3 extension."""

from __future__ import annotations

EXTENSION_ID = "fjcjfaeimhopmpnoemigapegahhjnbkl"
ALLOWED_EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
DEFAULT_ARTIFACT_DIRNAME = "mv3-devtools-e2e"

__all__ = [
    "ALLOWED_EXTENSION_ORIGIN",
    "DEFAULT_ARTIFACT_DIRNAME",
    "EXTENSION_ID",
]
