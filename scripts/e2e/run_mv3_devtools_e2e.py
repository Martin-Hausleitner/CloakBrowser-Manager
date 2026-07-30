#!/usr/bin/env python3
"""CLI entrypoint for the MV3 DevTools real-Chromium E2E suite."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e.mv3_devtools.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
