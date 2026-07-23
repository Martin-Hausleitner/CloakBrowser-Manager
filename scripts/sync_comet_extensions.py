#!/usr/bin/env python3
"""Harvest selectable Comet extensions into EXTENSION_CATALOG_DIR.

Copies only catalog ids from config/extension-catalog.json. Never prints
secrets. Safe to rsync the resulting directory onto the VCVM data volume.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comet-extensions",
        default=str(Path.home() / "Library/Application Support/Comet/Default/Extensions"),
        help="Source Comet Extensions directory",
    )
    parser.add_argument(
        "--catalog-config",
        default=str(Path(__file__).resolve().parent.parent / "config" / "extension-catalog.json"),
    )
    parser.add_argument(
        "--dest",
        default=str(Path(__file__).resolve().parent.parent / "artifacts" / "extension-catalog"),
        help="Destination catalog directory (EXTENSION_CATALOG_DIR)",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="Optional subset of extension ids (default: catalog selected/default_selected)",
    )
    args = parser.parse_args()

    source = Path(args.comet_extensions).expanduser()
    dest = Path(args.dest).expanduser()
    config = json.loads(Path(args.catalog_config).read_text(encoding="utf-8"))
    entries = [row for row in config.get("extensions", []) if isinstance(row, dict)]
    if args.ids:
        wanted = set(args.ids)
    else:
        wanted = {
            str(row["id"])
            for row in entries
            if row.get("default_selected") or row.get("id")
        }

    if not source.is_dir():
        print(f"Comet extensions directory missing: {source}", file=sys.stderr)
        return 2

    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    missing = 0
    for ext_id in sorted(wanted):
        src_root = source / ext_id
        if not src_root.is_dir():
            missing += 1
            print(f"skip missing {ext_id}")
            continue
        versions = sorted((child for child in src_root.iterdir() if child.is_dir()), key=lambda p: p.name)
        if not versions:
            missing += 1
            print(f"skip empty {ext_id}")
            continue
        version = versions[-1]
        target = dest / ext_id / version.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(version, target)
        copied += 1
        print(f"copied {ext_id}@{version.name}")

    print(f"done copied={copied} missing={missing} dest={dest}")
    return 0 if copied else 1


if __name__ == "__main__":
    raise SystemExit(main())
