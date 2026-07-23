#!/usr/bin/env python3
"""R040 proof: Skyvern harness drives a CloakBrowser Manager profile via CDP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PROOF_DIR = Path(os.environ.get("SKYVERN_PROOF_DIR", ROOT / ".proof"))
PROOF_PNG = PROOF_DIR / "2026-07-24-skyvern-harness.png"
PROOF_JSON = PROOF_DIR / "2026-07-24-skyvern-harness.json"

# Also mirror into the VCVM working copy when present.
VCVM_PROOF = Path.home() / "cloakbrowser-manager-vcvm" / ".proof" / "2026-07-24-skyvern-harness.png"


async def main() -> int:
    base = os.environ.get("CLOAK_MANAGER_URL", "http://127.0.0.1:18115").rstrip("/")
    token = os.environ.get("CLOAK_AUTH_TOKEN") or os.environ.get("AUTH_TOKEN")
    if not token:
        print("BLOCKED: CLOAK_AUTH_TOKEN/AUTH_TOKEN missing", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Bearer {token}"}
    PROOF_DIR.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    from backend.harnesses import skyvern_harness as harness

    caps = harness.capabilities()
    print("capabilities:", json.dumps(caps, indent=2))

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120.0) as client:
        status = (await client.get("/api/status")).json()
        profiles = (await client.get("/api/profiles")).json()
        if not profiles:
            create = await client.post(
                "/api/profiles",
                json={"name": "skyvern-harness-proof", "platform": "linux", "headless": True},
            )
            create.raise_for_status()
            profiles = [create.json()]

        profile = profiles[0]
        profile_id = profile["id"]
        print("profile:", profile_id, profile.get("name"))

        # Ensure running
        st = await client.get(f"/api/profiles/{profile_id}/status")
        st.raise_for_status()
        status_body = st.json()
        running = bool(status_body.get("running") or status_body.get("status") == "running")
        if not running:
            launch = await client.post(f"/api/profiles/{profile_id}/launch")
            if launch.status_code == 409:
                print("launch conflict (already running):", launch.text)
            else:
                launch.raise_for_status()
                print("launched:", launch.json())
                await asyncio.sleep(3)
        else:
            print("already running", status_body)

        # Prefer Manager CDP proxy (proves cloak path through Manager auth).
        browser_address = f"{base}/api/profiles/{profile_id}/cdp"
        cdp_headers = {"Authorization": f"Bearer {token}"}

        result = await harness.run_cdp_navigate_proof(
            browser_address=browser_address,
            headers=cdp_headers,
            url=os.environ.get(
                "SKYVERN_PROOF_URL",
                "https://example.com",
            ),
            screenshot_path=PROOF_PNG,
        )
        print("result:", json.dumps(result, indent=2))

        if result.get("status") != "ok" or not PROOF_PNG.is_file() or PROOF_PNG.stat().st_size < 1000:
            print("BLOCKED: proof screenshot missing or too small", file=sys.stderr)
            return 3

        # Mirror to VCVM path required by operator brief.
        VCVM_PROOF.parent.mkdir(parents=True, exist_ok=True)
        VCVM_PROOF.write_bytes(PROOF_PNG.read_bytes())

        payload = {
            "capabilities": caps,
            "profile_id": profile_id,
            "browser_address": browser_address,
            "result": result,
            "screenshot_bytes": PROOF_PNG.stat().st_size,
            "vcvm_mirror": str(VCVM_PROOF),
        }
        PROOF_JSON.write_text(json.dumps(payload, indent=2))
        (VCVM_PROOF.parent / "2026-07-24-skyvern-harness.json").write_text(
            json.dumps(payload, indent=2)
        )
        print("PROOF_OK", PROOF_PNG, PROOF_PNG.stat().st_size)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
