#!/usr/bin/env python3
"""Queue and execute one VCVM-only Browser Harness signup/signin proof."""

from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "http://127.0.0.1:18115"
PROFILE_ID = "a8b99a1f-bd77-4249-917f-0ad681ea5519"
TOKEN_FILE = "/home/coder/.config/cloakbrowser/browser-use-worker-key"
FLOW_FILE = Path("/run/user/1000/cbm-browser-use-signup-signin-flow.json")
RUN_ID_FILE = Path("/run/user/1000/cbm-browser-use-signup-run-id")


def container_env() -> dict[str, str]:
    raw = subprocess.check_output(
        ["docker", "inspect", "cloakbrowser-manager-vcvm"], text=True
    )
    item = json.loads(raw)[0]
    return {
        key: value
        for entry in item.get("Config", {}).get("Env", [])
        if "=" in entry
        for key, value in [entry.split("=", 1)]
    }


def request(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with opener.open(req, timeout=60) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    auth_token = container_env().get("AUTH_TOKEN")
    if not auth_token:
        raise RuntimeError("VCVM Manager auth is unavailable")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, _ = request(opener, "POST", "/api/auth/login", {"token": auth_token})
    if status != 200:
        raise RuntimeError("VCVM Manager login failed")

    status, health = request(
        opener, "POST", f"/api/profiles/{PROFILE_ID}/health/run"
    )
    if status != 202:
        raise RuntimeError(f"VCVM profile health trigger failed ({status})")
    for _ in range(45):
        time.sleep(2)
        status, health = request(
            opener, "GET", f"/api/profiles/{PROFILE_ID}/health"
        )
        if status == 200 and isinstance(health, dict) and health.get("state") not in {
            "pending",
            "running",
            "unavailable",
        }:
            break
    if (
        status != 200
        or not isinstance(health, dict)
        or health.get("state") != "passed"
        or health.get("blockers")
        or int(health.get("fingerprint_consistency_score") or 0) < 70
        or int(health.get("browser_scan_score") or 0) < 70
    ):
        raise RuntimeError("VCVM profile health did not pass the E2E gate")

    status, session = request(
        opener,
        "POST",
        "/api/task-sessions",
        {
            "profile_id": PROFILE_ID,
            "title": "Browser Use Cloud VCVM E2E",
            "metadata": {"source": "browser-harness", "scope": "vcvm-only"},
        },
    )
    if status != 201 or not isinstance(session, dict) or not session.get("id"):
        raise RuntimeError(f"VCVM task session creation failed ({status})")

    task = (
        "Using only the VCVM-managed profile, open "
        "https://cloud.browser-use.com/signup, create the temporary test account, "
        "then open https://cloud.browser-use.com/signin and prove the same account "
        "can authenticate, then open https://cloud.browser-use.com/ to verify the "
        "signed-in product surface. Never emit credentials."
    )
    status, run = request(
        opener,
        "POST",
        f"/api/task-sessions/{session['id']}/runs",
        {
            "harness": "browser-harness",
            "task": task,
            "profile_id": PROFILE_ID,
            "launch_if_stopped": False,
            "allowed_origins": ["https://cloud.browser-use.com"],
            "max_steps": 40,
            "timeout_seconds": 600,
            "model_alias": "default",
        },
    )
    if status != 201 or not isinstance(run, dict) or not run.get("id"):
        detail = run.get("detail") if isinstance(run, dict) else None
        raise RuntimeError(f"VCVM Browser Harness run creation failed ({status}: {detail})")
    run_id = str(run["id"])
    RUN_ID_FILE.write_text(run_id, encoding="utf-8")
    os.chmod(RUN_ID_FILE, 0o600)

    if run.get("status") == "blocked_health":
        decision = run.get("health_decision") or {}
        failed = set(decision.get("failed_reasons") or [])
        non_overridable = set(decision.get("non_overridable_reasons") or [])
        legacy_gap_only = failed <= {"measured_authenticity_below_threshold"}
        if non_overridable or not legacy_gap_only:
            raise RuntimeError("VCVM run health has a non-overridable failure")
        status, run = request(
            opener,
            "POST",
            f"/api/task-runs/{run_id}/override-health",
            {
                "reason": (
                    "Fresh VCVM probe passed with fingerprint and browser-scan scores "
                    "at or above policy threshold; direct-egress profile has no proxy score."
                )
            },
        )
        if status != 200 or not isinstance(run, dict) or run.get("status") != "queued":
            raise RuntimeError("VCVM run health override was not accepted")

    flow = {
        "engine": "cdp",
        "url": "https://cloud.browser-use.com/signup",
        "reset_origin_storage": True,
        "require_authenticated": True,
        "actions": [
            {"op": "fill", "selector": "#email", "source": "vcvm_email"},
            {
                "op": "fill",
                "selector": "#password",
                "source": "browser_use_password",
            },
            {
                "op": "fill",
                "selector": "input[type=password]",
                "source": "browser_use_password",
                "index": 1,
            },
            {"op": "submit"},
            {"op": "wait", "milliseconds": 8000},
            {"op": "navigate", "url": "https://cloud.browser-use.com/signin"},
            {"op": "fill", "selector": "#email", "source": "vcvm_email"},
            {
                "op": "fill",
                "selector": "#password",
                "source": "browser_use_password",
            },
            {"op": "submit"},
            {"op": "wait", "milliseconds": 10000},
            {"op": "navigate", "url": "https://cloud.browser-use.com/"},
            {"op": "wait", "milliseconds": 8000},
            {"op": "snapshot"},
        ],
    }
    write_private_json(FLOW_FILE, flow)
    try:
        completed = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "scripts.unbrowse_managed_helper",
                "--manager-url",
                BASE_URL,
                "--token-file",
                TOKEN_FILE,
                "--flow",
                str(FLOW_FILE),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=660,
            check=False,
        )
    finally:
        FLOW_FILE.unlink(missing_ok=True)

    run_status, current = request(opener, "GET", f"/api/task-runs/{run_id}")
    output_status, outputs = request(
        opener, "GET", f"/api/task-runs/{run_id}/outputs"
    )
    summaries = []
    if output_status == 200 and isinstance(outputs, list):
        summaries = [
            str(item.get("summary") or "")[:160]
            for item in outputs
            if isinstance(item, dict)
        ]
    print(
        json.dumps(
            {
                "run_id": run_id,
                "helper_exit": completed.returncode,
                "api_status": run_status,
                "run_status": current.get("status") if isinstance(current, dict) else None,
                "output_summaries": summaries,
            }
        )
    )
    return 0 if completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
