#!/usr/bin/env python3
"""CLI entrypoint for disposable profile share E2E.

Usage:
  python scripts/e2e/run_profile_share_e2e.py
  python scripts/e2e/run_profile_share_e2e.py --work-dir /tmp/cbm-share-e2e

Prints a redacted JSON result to stdout. Exit code 0 on success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import database as db  # noqa: E402
from scripts.e2e.profile_share_loader import run_profile_share_e2e  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run disposable profile share E2E")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Disposable work directory (created if missing). Default: temp dir.",
    )
    parser.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Do not delete the work directory on success.",
    )
    args = parser.parse_args(argv)

    tmp_ctx = None
    if args.work_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="cbm-profile-share-e2e-")
        work_dir = Path(tmp_ctx.name)
    else:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    catalog_dir = work_dir / "extension-catalog"
    os.environ["EXTENSION_CATALOG_DIR"] = str(catalog_dir)

    db_file = work_dir / "profiles.db"
    db.DB_PATH = db_file
    db.DATA_DIR = work_dir
    db.init_db()

    try:
        result = run_profile_share_e2e(work_dir=work_dir, extension_catalog_dir=catalog_dir)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.ok else 1
    finally:
        if tmp_ctx is not None and not args.keep_work_dir:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
