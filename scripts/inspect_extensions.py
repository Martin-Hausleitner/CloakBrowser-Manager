#!/usr/bin/env python3
"""CLI utility to inspect browser extension manifests for CloakBrowser profiles or paths."""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path for backend imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.extensions import parse_extension_manifest, inspect_profile_extensions
from backend import database as db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect read-only extension inventory for CloakBrowser profiles or paths."
    )
    parser.add_argument("--profile-id", help="Profile ID to inspect from database")
    parser.add_argument("--extension-path", help="Direct path to an extension directory to inspect")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if not args.profile_id and not args.extension_path:
        parser.error("Must specify either --profile-id or --extension-path")

    results = []

    if args.extension_path:
        info = parse_extension_manifest(args.extension_path)
        results.append(
            {
                "id": info.id,
                "path": info.path,
                "name": info.name,
                "version": info.version,
                "manifest_version": info.manifest_version,
                "description": info.description,
                "permissions": info.permissions,
                "trust_state": info.trust_state,
                "error": info.error,
            }
        )
    elif args.profile_id:
        profile = db.get_profile(args.profile_id)
        if not profile:
            print(f"Error: Profile '{args.profile_id}' not found.", file=sys.stderr)
            sys.exit(1)
        results = inspect_profile_extensions(profile)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No extensions configured.")
            return

        for ext in results:
            print(f"Extension: {ext['name']} (v{ext['version']})")
            print(f"  Path: {ext['path']}")
            print(f"  Manifest Version: MV{ext['manifest_version']}")
            print(f"  Trust State: {ext['trust_state']}")
            if ext['description']:
                print(f"  Description: {ext['description']}")
            if ext['permissions']:
                print(f"  Permissions: {', '.join(ext['permissions'])}")
            if ext['error']:
                print(f"  Error: {ext['error']}")
            print()


if __name__ == "__main__":
    main()
