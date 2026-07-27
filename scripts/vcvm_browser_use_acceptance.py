#!/usr/bin/env python3
"""Fail-closed VCVM Browser-Use acceptance verifier.

Default mode verifies a redacted JSON evidence bundle. ``--live`` performs only
read-only SSH inventory and writes the same bundle shape; it never creates runs,
deploys, restarts, prunes, or reads credential files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHEMA_VERSION = 1
EXPECTED_BROWSER_USE_VERSION = "0.13.6"
EXPECTED_MIGRATIONS = (
    "agent_workspace_v1",
    "task_runs_v1",
    "worker_runtime_v1",
    "task_runs_acpx_v1",
    "worker_harness_presence_v1",
    "worker_harness_preflights_v1",
    "task_run_binding_v1",
)
EXPECTED_OUTPUT_KINDS = ("action", "observation", "observation", "screenshot", "summary")
TERMINAL_SUCCESS = "succeeded"
TOKEN_RE = re.compile(r"\bcbm_(?:worker|run|agent|lease)_[A-Za-z0-9_-]+\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
CLI_TOKEN_RE = re.compile(
    r"(?i)(--(?:auth-)?token(?:-file)?(?:=|\s+))([^\s]+)"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)"
    r"(\s*[:=]\s*)[^\s,;&]+"
)
SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "refresh_token",
    "secret",
    "token",
}


class AcceptanceError(RuntimeError):
    """Verifier input or execution failure."""


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    message: str
    evidence: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_url_credentials(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = f"<redacted>@{netloc.rsplit('@', 1)[1]}"
        query_items = []
        changed = False
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in SECRET_QUERY_KEYS:
                query_items.append((key, "<redacted>"))
                changed = True
            else:
                query_items.append((key, item_value))
        query = urlencode(query_items, doseq=True) if changed else parsed.query
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))

    return re.sub(r"\b(?:https?|wss?)://[^\s<>)\"']+", replace_url, text)


def redact_text(text: str) -> str:
    redacted = redact_url_credentials(text)
    redacted = TOKEN_RE.sub("<redacted>", redacted)
    redacted = BEARER_RE.sub("Bearer <redacted>", redacted)
    redacted = CLI_TOKEN_RE.sub(lambda match: f"{match.group(1)}<redacted>", redacted)
    redacted = ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted)
    return redacted


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"{path} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise AcceptanceError(f"{path} must contain a JSON object")
    return data


def nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def passed(check_id: str, message: str, **evidence: Any) -> Check:
    return Check(check_id, "passed", message, redact(evidence))


def failed(check_id: str, message: str, **evidence: Any) -> Check:
    return Check(check_id, "failed", message, redact(evidence))


def degraded(check_id: str, message: str, **evidence: Any) -> Check:
    return Check(check_id, "degraded", message, redact(evidence))


def expected_value(evidence: dict[str, Any], key: str, fallback: Any) -> Any:
    return nested(evidence, f"expected.{key}", fallback)


def _short(value: Any) -> str:
    return str(value or "").strip()[:12]


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def verify_commits(evidence: dict[str, Any]) -> list[Check]:
    expected_manager = _short(expected_value(evidence, "manager_commit", ""))
    expected_worker = _short(expected_value(evidence, "worker_commit", expected_manager))
    manager = _short(nested(evidence, "live.manager.commit", ""))
    worker = _short(nested(evidence, "live.worker.commit", ""))
    checks: list[Check] = []
    checks.append(
        passed("manager_commit", "Manager commit matches PR head", expected=expected_manager, actual=manager)
        if expected_manager and manager == expected_manager
        else failed(
            "manager_commit",
            "Manager commit does not prove the expected PR head",
            expected=expected_manager,
            actual=manager,
        )
    )
    checks.append(
        passed("worker_commit", "Worker commit matches PR head", expected=expected_worker, actual=worker)
        if expected_worker and worker == expected_worker
        else failed(
            "worker_commit",
            "Worker commit does not prove the expected PR head",
            expected=expected_worker,
            actual=worker,
        )
    )
    return checks


def verify_migrations(evidence: dict[str, Any]) -> Check:
    expected = tuple(expected_value(evidence, "migrations", list(EXPECTED_MIGRATIONS)) or [])
    actual = tuple(nested(evidence, "live.manager.migrations", []) or [])
    missing = [item for item in expected if item not in actual]
    unexpected = [item for item in actual if item not in expected]
    if actual and not missing and not unexpected:
        return passed("migration_set", "Migration set matches Browser-Use workspace contract", actual=list(actual))
    return failed(
        "migration_set",
        "Migration set is missing or mismatched",
        expected=list(expected),
        actual=list(actual),
        missing=missing,
        unexpected=unexpected,
    )


def verify_runtime(evidence: dict[str, Any]) -> list[Check]:
    expected_version = str(expected_value(evidence, "browser_use_version", EXPECTED_BROWSER_USE_VERSION))
    version = str(nested(evidence, "live.worker.browser_use_version", "") or "")
    active = nested(evidence, "live.worker.active", None)
    unit_exec = str(nested(evidence, "live.worker.unit_exec", "") or "")
    worker_id = str(nested(evidence, "live.worker.worker_id", "") or "")
    checks = [
        passed("browser_use_version", "Browser Use version matches pin", expected=expected_version, actual=version)
        if version == expected_version
        else failed(
            "browser_use_version",
            "Browser Use version is unavailable or mismatched",
            expected=expected_version,
            actual=version,
        )
    ]
    unit_ok = (
        active is True
        and "-m scripts.browser_use_worker" in unit_exec
        and "--token-file" in unit_exec
        and "--token " not in unit_exec
        and bool(worker_id)
    )
    checks.append(
        passed("worker_presence", "Browser-Use worker is active with token-file unit shape", worker_id=worker_id)
        if unit_ok
        else failed(
            "worker_presence",
            "Browser-Use worker presence or unit shape is not proven",
            active=active,
            worker_id=worker_id,
            unit_exec=unit_exec,
        )
    )
    return checks


def verify_profile_binding(evidence: dict[str, Any]) -> list[Check]:
    run = nested(evidence, "run", {}) or {}
    profile_id = str(run.get("profile_id") or "")
    binding = run.get("profile_binding") if isinstance(run.get("profile_binding"), dict) else {}
    cdp = run.get("cdp_target") if isinstance(run.get("cdp_target"), dict) else {}
    binding_ok = (
        bool(profile_id)
        and binding.get("profile_id") == profile_id
        and binding.get("same_profile_visible") is True
        and bool(binding.get("user_data_dir_fingerprint"))
    )
    cdp_ok = (
        bool(cdp.get("target_id") or cdp.get("websocket_url") or cdp.get("version_url"))
        and cdp.get("profile_id") == profile_id
    )
    return [
        passed("profile_binding", "Run is bound to the visible persistent profile", profile_id=profile_id)
        if binding_ok
        else failed(
            "profile_binding",
            "Run/profile binding proof is missing or mismatched",
            profile_id=profile_id,
            binding=binding,
        ),
        passed("cdp_target", "CDP target is bound to the same profile", profile_id=profile_id)
        if cdp_ok
        else failed("cdp_target", "CDP target proof is missing or mismatched", profile_id=profile_id, cdp_target=cdp),
    ]


def verify_viewport_and_url(evidence: dict[str, Any]) -> list[Check]:
    run = nested(evidence, "run", {}) or {}
    viewport = run.get("viewport") if isinstance(run.get("viewport"), dict) else {}
    expected_viewport = expected_value(evidence, "viewport", None)
    viewport_ok = (
        isinstance(viewport.get("width"), int)
        and isinstance(viewport.get("height"), int)
        and viewport["width"] > 0
        and viewport["height"] > 0
        and (not expected_viewport or viewport == expected_viewport)
    )
    current_url = str(run.get("current_url") or "")
    expected_url = expected_value(evidence, "current_url", None)
    url_ok = current_url.startswith(("http://", "https://")) and (
        expected_url is None or current_url == expected_url
    )
    return [
        passed("viewport", "Viewport proof is present and matches expectation", viewport=viewport)
        if viewport_ok
        else failed(
            "viewport",
            "Viewport proof is missing or mismatched",
            expected=expected_viewport,
            actual=viewport,
        ),
        passed("current_url", "Current URL is present and matches expectation", current_url=current_url)
        if url_ok
        else failed(
            "current_url",
            "Current URL proof is missing or mismatched",
            expected=expected_url,
            actual=current_url,
        ),
    ]


def verify_outputs(evidence: dict[str, Any]) -> list[Check]:
    run = nested(evidence, "run", {}) or {}
    outputs = run.get("outputs") if isinstance(run.get("outputs"), list) else []
    dict_outputs = [item for item in outputs if isinstance(item, dict)]
    sequences = [positive_int(item.get("sequence")) for item in dict_outputs]
    kinds = [item.get("kind") for item in outputs if isinstance(item, dict)]
    expected_kinds = list(expected_value(evidence, "typed_output_kinds", list(EXPECTED_OUTPUT_KINDS)) or [])
    expected_sequences = list(range(1, len(dict_outputs) + 1))
    ordering_ok = (
        len(dict_outputs) == len(outputs)
        and None not in sequences
        and sequences == expected_sequences
    )
    kinds_ok = kinds[: len(expected_kinds)] == expected_kinds
    first_action = run.get("first_action") if isinstance(run.get("first_action"), dict) else {}
    first_sequence = positive_int(first_action.get("sequence"))
    first_action_ok = (
        bool(dict_outputs)
        and kinds[0] == "action"
        and first_sequence == sequences[0]
        and first_action.get("kind", "action") == "action"
        and bool(first_action.get("name") or nested(dict_outputs[0], "payload.name"))
    )
    return [
        passed("first_action", "First typed output is the first action", first_action=first_action)
        if first_action_ok
        else failed("first_action", "First action proof is missing or mismatched", first_action=first_action, outputs=outputs[:2]),
        passed("typed_output_ordering", "Typed output sequence and kind ordering match", kinds=kinds)
        if ordering_ok and kinds_ok
        else failed(
            "typed_output_ordering",
            "Typed output ordering is missing or mismatched",
            expected_kinds=expected_kinds,
            sequences=sequences,
            kinds=kinds,
        ),
    ]


def verify_screenshot(evidence: dict[str, Any]) -> Check:
    screenshot = nested(evidence, "run.screenshot", {}) or {}
    sha = str(screenshot.get("sha256") or "")
    uploaded = str(screenshot.get("uploaded_sha256") or "")
    byte_count = positive_int(screenshot.get("bytes"))
    width = positive_int(screenshot.get("width"))
    height = positive_int(screenshot.get("height"))
    ok = (
        bool(sha)
        and sha == uploaded
        and screenshot.get("media_type") in {"image/png", "image/jpeg"}
        and byte_count is not None
        and width is not None
        and height is not None
    )
    return (
        passed("screenshot_hash", "Screenshot hash, media type, and dimensions are proven", screenshot=screenshot)
        if ok
        else failed("screenshot_hash", "Screenshot proof is missing or mismatched", screenshot=screenshot)
    )


def verify_terminal_and_health(evidence: dict[str, Any]) -> list[Check]:
    run = nested(evidence, "run", {}) or {}
    terminal = str(run.get("terminal_status") or run.get("status") or "")
    health = run.get("health_decision") if isinstance(run.get("health_decision"), dict) else {}
    override = health.get("override") if isinstance(health.get("override"), dict) else {}
    health_allowed = health.get("allowed") is True
    if override.get("applied") is True:
        health_check = degraded(
            "health_gate",
            "Health override was applied; this is degraded evidence, not acceptance success",
            health_decision=health,
        )
    elif health_allowed:
        health_check = passed("health_gate", "Health gate passed without override", health_decision=health)
    else:
        health_check = failed("health_gate", "Health gate pass is not proven", health_decision=health)
    return [
        passed("terminal_status", "Run terminal status succeeded", terminal_status=terminal)
        if terminal == TERMINAL_SUCCESS
        else failed(
            "terminal_status",
            "Run terminal status is missing or not succeeded",
            expected=TERMINAL_SUCCESS,
            actual=terminal,
        ),
        health_check,
    ]


def verify_cancellation(evidence: dict[str, Any]) -> Check:
    cancel = nested(evidence, "run.cancellation", {}) or {}
    post_cancel_outputs = non_negative_int(cancel.get("post_cancel_outputs"))
    ok = (
        cancel.get("cancel_status") == "cancelled"
        and cancel.get("worker_stopped") is True
        and cancel.get("capability_revoked") is True
        and post_cancel_outputs == 0
        and cancel.get("post_cancel_terminal_mutation") is False
    )
    return (
        passed("cancellation_fencing", "Cancellation fencing is proven", cancellation=cancel)
        if ok
        else failed("cancellation_fencing", "Cancellation fencing proof is missing or mismatched", cancellation=cancel)
    )


def verify(evidence: dict[str, Any]) -> dict[str, Any]:
    checks: list[Check] = []
    checks.extend(verify_commits(evidence))
    checks.append(verify_migrations(evidence))
    checks.extend(verify_runtime(evidence))
    checks.extend(verify_profile_binding(evidence))
    checks.extend(verify_viewport_and_url(evidence))
    checks.extend(verify_outputs(evidence))
    checks.append(verify_screenshot(evidence))
    checks.extend(verify_terminal_and_health(evidence))
    checks.append(verify_cancellation(evidence))
    failures = [item for item in checks if item.status == "failed"]
    degraded_items = [item for item in checks if item.status == "degraded"]
    if failures:
        status = "failed"
    elif degraded_items:
        status = "degraded"
    else:
        status = "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "status": status,
        "summary": {
            "passed": sum(1 for item in checks if item.status == "passed"),
            "failed": len(failures),
            "degraded": len(degraded_items),
        },
        "checks": [item.__dict__ for item in checks],
    }


def live_inventory(host: str) -> dict[str, Any]:
    script = r"""
set -euo pipefail
unit_exec="$(systemctl --user show cloakbrowser-browser-use-worker.service -p ExecStart --value --no-pager 2>/dev/null || true)"
working_dir="$(systemctl --user show cloakbrowser-browser-use-worker.service -p WorkingDirectory --value --no-pager 2>/dev/null || true)"
unit_active="$(systemctl --user is-active cloakbrowser-browser-use-worker.service 2>/dev/null || true)"
worker_id="$(printf '%s' "$unit_exec" | sed -n 's/.*--worker-id \([^ ]*\).*/\1/p')"
python_path="${unit_exec%% -m scripts.browser_use_worker*}"
browser_use_version=""
if [[ -x "$python_path" ]]; then
  browser_use_version="$("$python_path" -c 'import browser_use; print(browser_use.__version__)' 2>/dev/null || true)"
fi
checkout_commit="$(git -C /home/coder/vk-repos/CloakBrowser-Manager-browser-use rev-parse --short HEAD 2>/dev/null || true)"
checkout_branch="$(git -C /home/coder/vk-repos/CloakBrowser-Manager-browser-use branch --show-current 2>/dev/null || true)"
worker_commit=""
worker_branch=""
if [[ -n "$working_dir" ]]; then
  worker_commit="$(git -C "$working_dir" rev-parse --short HEAD 2>/dev/null || true)"
  worker_branch="$(git -C "$working_dir" branch --show-current 2>/dev/null || true)"
fi
manager_commit="$(docker inspect cloakbrowser-manager-vcvm --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' 2>/dev/null || true)"
if [[ "$manager_commit" == "<no value>" ]]; then manager_commit=""; fi
runtime_manifest_commit="$(docker exec cloakbrowser-manager-vcvm sh -lc '
for p in /app/runtime-manifest.json /app/build-info.json /app/.build-info.json /app/commit.txt; do
  [ -r "$p" ] || continue
  case "$p" in
    *.json) python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get(\"commit\") or d.get(\"revision\") or d.get(\"git_commit\") or \"\")" "$p" 2>/dev/null ;;
    *) head -n 1 "$p" ;;
  esac
  exit 0
done
' 2>/dev/null || true)"
if [[ -z "$manager_commit" && -n "$runtime_manifest_commit" ]]; then manager_commit="$runtime_manifest_commit"; fi
health_ok=0
if curl -fsS --max-time 5 http://127.0.0.1:18115/health >/dev/null 2>&1; then health_ok=1; fi
disk_kib="$(df -Pk /home/coder/cloakbrowser-manager 2>/dev/null | awk 'NR == 2 { print $4 }')"
python3 - "$manager_commit" "$checkout_commit" "$checkout_branch" "$worker_commit" "$worker_branch" "$working_dir" "$unit_active" "$worker_id" "$unit_exec" "$browser_use_version" "$health_ok" "$disk_kib" <<'PY'
import json, sys
manager_commit, checkout_commit, checkout_branch, worker_commit, worker_branch, working_dir, active, worker_id, unit_exec, version, health_ok, disk_kib = sys.argv[1:]
print(json.dumps({
    "schema_version": 1,
    "generated_at": "",
    "live": {
        "checkout": {
            "commit": checkout_commit,
            "branch": checkout_branch,
        },
        "manager": {
            "commit": manager_commit,
            "health_ok": health_ok == "1",
            "migrations": [],
        },
        "worker": {
            "commit": worker_commit,
            "branch": worker_branch,
            "working_directory": working_dir,
            "active": active == "active",
            "worker_id": worker_id,
            "unit_exec": unit_exec,
            "browser_use_version": version,
        },
        "vcvm": {
            "free_disk_kib": int(disk_kib) if disk_kib.isdigit() else None,
        },
    },
    "run": {},
}))
PY
"""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AcceptanceError(f"read-only SSH inventory failed for {host}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    data["generated_at"] = utc_now()
    return redact(data)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, help="Redacted JSON evidence bundle to verify")
    parser.add_argument("--live", action="store_true", help="Collect read-only SSH inventory instead of reading --evidence")
    parser.add_argument("--host", default="vcvm", help="SSH host for --live inventory")
    parser.add_argument("--expected-manager-commit", default="", help="Expected Manager commit/PR head")
    parser.add_argument("--expected-worker-commit", default="", help="Expected worker commit/PR head")
    parser.add_argument("--expected-browser-use-version", default=EXPECTED_BROWSER_USE_VERSION)
    parser.add_argument("--output-json", type=Path, help="Write verifier result JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.live:
            evidence = live_inventory(args.host)
        elif args.evidence:
            evidence = read_json(args.evidence)
        else:
            raise AcceptanceError("provide --evidence or --live")
        expected = dict(evidence.get("expected") or {})
        if args.expected_manager_commit:
            expected["manager_commit"] = args.expected_manager_commit
        if args.expected_worker_commit:
            expected["worker_commit"] = args.expected_worker_commit
        if args.expected_browser_use_version:
            expected["browser_use_version"] = args.expected_browser_use_version
        evidence["expected"] = expected
        result = verify(evidence)
        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "passed" else 1
    except AcceptanceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
