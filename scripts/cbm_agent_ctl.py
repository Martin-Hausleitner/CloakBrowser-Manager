#!/usr/bin/env python3
"""Agent/CLI control plane for CloakBrowser Manager.

External agents (Codex, Antigravity, harnesses, extensions) should drive the
stack through this CLI or the same HTTP paths — not through UI clicks.

Auth:
  export CBM_BASE_URL=http://127.0.0.1:18115
  export CBM_AGENT_KEY=cbm_agent_...   # preferred for agents
  # or: export CBM_AGENT_KEY_FILE=/home/coder/.config/cloakbrowser/orca-agent-key
  # or: export CBM_ADMIN_TOKEN=...     # bootstrap admin only

Examples:
  scripts/cbm_agent_ctl.py whoami
  scripts/cbm_agent_ctl.py profiles list
  scripts/cbm_agent_ctl.py profiles create --name demo --sandbox agents --harness codex
  scripts/cbm_agent_ctl.py profiles launch <id>
  scripts/cbm_agent_ctl.py profiles open-links <id> --mode cdp
  scripts/cbm_agent_ctl.py profiles open-links <id> --mode vnc
  scripts/cbm_agent_ctl.py profiles status <id>
  scripts/cbm_agent_ctl.py profiles stop <id>
  scripts/cbm_agent_ctl.py tasks create --profile-id <id> --title "demo"
  scripts/cbm_agent_ctl.py tasks run <session_id> --profile-id <id> --task "Read title" --allowed-origin https://example.com
  scripts/cbm_agent_ctl.py runs get <run_id>
  scripts/cbm_agent_ctl.py runs cancel <run_id>
  scripts/cbm_agent_ctl.py health
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _base_url() -> str:
    return (_env("CBM_BASE_URL", "http://127.0.0.1:18115") or "").rstrip("/")


def _read_agent_key_file(path: str) -> str:
    key_path = path.strip()
    if not key_path:
        raise SystemExit("CBM_AGENT_KEY_FILE is empty")
    try:
        raw = open(key_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise SystemExit("Unable to read CBM_AGENT_KEY_FILE") from exc
    key = raw.strip()
    if not key:
        raise SystemExit("CBM_AGENT_KEY_FILE is empty")
    return key


def _auth_header() -> dict[str, str]:
    agent_key = _env("CBM_AGENT_KEY")
    if not agent_key:
        key_file = _env("CBM_AGENT_KEY_FILE")
        if key_file:
            agent_key = _read_agent_key_file(key_file)
    if agent_key:
        return {"Authorization": f"Bearer {agent_key}"}
    admin = _env("CBM_ADMIN_TOKEN") or _env("AUTH_TOKEN")
    if admin:
        return {"Authorization": f"Bearer {admin}"}
    raise SystemExit(
        "Set CBM_AGENT_KEY or CBM_AGENT_KEY_FILE (preferred) or CBM_ADMIN_TOKEN/AUTH_TOKEN."
    )


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None
    headers = {
        "Accept": "application/json",
        **_auth_header(),
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if not raw:
                return {"ok": True, "status": resp.status}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pass
        raise SystemExit(f"HTTP {exc.code} {method} {path}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed for {_base_url()}{path}: {exc}") from exc


def _print(payload: Any, as_json: bool) -> None:
    if as_json or not isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_health(_: argparse.Namespace) -> None:
    url = f"{_base_url()}/health"
    with urllib.request.urlopen(url, timeout=15) as resp:
        print(resp.read().decode("utf-8"))


def cmd_whoami(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/access/me"), args.json)


def cmd_status(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/status"), args.json)


def cmd_sandboxes(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/access/sandboxes"), args.json)


def cmd_catalog(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/extension/catalog"), args.json)


def cmd_profiles_list(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/profiles"), args.json)


def cmd_profiles_get(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/profiles/{args.profile_id}"), args.json)


def cmd_profiles_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "name": args.name,
        "sandbox_id": args.sandbox,
        "harness": args.harness,
        "project_id": args.project,
        "folder_path": args.folder,
        "pinned": args.pinned,
        "geoip": args.geoip,
    }
    if args.timezone:
        body["timezone"] = args.timezone
    if args.locale:
        body["locale"] = args.locale
    if args.proxy:
        body["proxy"] = args.proxy
    if args.platform:
        body["platform"] = args.platform
    if args.extension_ids is not None:
        body["extension_ids"] = list(args.extension_ids)
    _print(_request("POST", "/api/profiles", body=body), args.json)


def cmd_profiles_update(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {}
    mapping = {
        "name": args.name,
        "sandbox_id": args.sandbox_id,
        "harness": args.harness,
        "project_id": args.project_id,
        "folder_path": args.folder_path,
        "timezone": args.timezone,
        "locale": args.locale,
        "proxy": args.proxy,
        "platform": args.platform,
    }
    for key, value in mapping.items():
        if value is not None:
            body[key] = value
    if args.pinned is not None:
        body["pinned"] = args.pinned
    if args.geoip is not None:
        body["geoip"] = args.geoip
    if args.extension_ids is not None:
        body["extension_ids"] = list(args.extension_ids)
    if not body:
        raise SystemExit("No update fields provided")
    _print(_request("PUT", f"/api/profiles/{args.profile_id}", body=body), args.json)


def cmd_profiles_delete(args: argparse.Namespace) -> None:
    _print(_request("DELETE", f"/api/profiles/{args.profile_id}"), args.json)


def cmd_profiles_launch(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/profiles/{args.profile_id}/launch"), args.json)


def cmd_profiles_stop(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/profiles/{args.profile_id}/stop"), args.json)


def cmd_profiles_status(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/profiles/{args.profile_id}/status"), args.json)


def cmd_profiles_health(args: argparse.Namespace) -> None:
    if args.run:
        _print(_request("POST", f"/api/profiles/{args.profile_id}/health/run"), args.json)
        return
    _print(_request("GET", f"/api/profiles/{args.profile_id}/health"), args.json)


def cmd_profiles_extensions(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/profiles/{args.profile_id}/extensions"), args.json)


def cmd_profiles_open_links(args: argparse.Namespace) -> None:
    query = {"prefer": args.prefer, "mode": args.mode}
    payload = _request("GET", f"/api/profiles/{args.profile_id}/open-links", query=query)
    if args.field:
        value = payload.get(args.field)
        if value is None:
            raise SystemExit(f"Field {args.field!r} missing from open-links payload")
        print(value)
        return
    _print(payload, args.json)


def cmd_open_session(args: argparse.Namespace) -> None:
    body = {
        "profile_id": args.profile_id,
        "launch": not args.no_launch,
        "prefer": args.prefer,
        "mode": args.mode,
    }
    _print(_request("POST", "/api/extension/sessions/open", body=body), args.json)


def cmd_tasks_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"profile_id": args.profile_id}
    if args.title:
        body["title"] = args.title
    _print(_request("POST", "/api/task-sessions", body=body), args.json)


def cmd_tasks_run(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "harness": "browser-use",
        "task": args.task,
        "profile_id": args.profile_id,
        "launch_if_stopped": bool(args.launch_if_stopped),
        "allowed_origins": list(args.allowed_origin or []),
        "max_steps": args.max_steps,
        "timeout_seconds": args.timeout_seconds,
    }
    if args.model_alias:
        body["model_alias"] = args.model_alias
    _print(
        _request("POST", f"/api/task-sessions/{args.session_id}/runs", body=body),
        args.json,
    )


def cmd_runs_get(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/task-runs/{args.run_id}"), args.json)


def cmd_runs_cancel(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/task-runs/{args.run_id}/cancel"), args.json)


def cmd_runs_outputs(args: argparse.Namespace) -> None:
    query: dict[str, str] = {}
    if args.after_sequence is not None:
        query["after_sequence"] = str(args.after_sequence)
    _print(
        _request("GET", f"/api/task-runs/{args.run_id}/outputs", query=query or None),
        args.json,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Always print JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="Unauthenticated liveness")
    p_health.set_defaults(func=cmd_health)

    p_whoami = sub.add_parser("whoami", help="Show authenticated identity")
    p_whoami.set_defaults(func=cmd_whoami)

    p_status = sub.add_parser("status", help="Manager status")
    p_status.set_defaults(func=cmd_status)

    p_sandboxes = sub.add_parser("sandboxes", help="List visible sandboxes")
    p_sandboxes.set_defaults(func=cmd_sandboxes)

    p_catalog = sub.add_parser("catalog", help="Extension/agent catalog")
    p_catalog.set_defaults(func=cmd_catalog)

    profiles = sub.add_parser("profiles", help="Profile control plane")
    psub = profiles.add_subparsers(dest="profiles_command", required=True)

    pl = psub.add_parser("list", help="List visible profiles")
    pl.set_defaults(func=cmd_profiles_list)

    pg = psub.add_parser("get", help="Get one profile")
    pg.add_argument("profile_id")
    pg.set_defaults(func=cmd_profiles_get)

    pc = psub.add_parser("create", help="Create profile in an operable sandbox")
    pc.add_argument("--name", required=True)
    pc.add_argument("--sandbox", default="default")
    pc.add_argument("--harness", default="codex")
    pc.add_argument("--project", default="default")
    pc.add_argument("--folder", default="")
    pc.add_argument("--timezone")
    pc.add_argument("--locale")
    pc.add_argument("--proxy")
    pc.add_argument("--platform", choices=["windows", "macos", "linux"])
    pc.add_argument(
        "--extension-id",
        dest="extension_ids",
        action="append",
        default=None,
        help="Trusted catalog extension id; repeat to assign more than one",
    )
    pc.add_argument("--pinned", action="store_true")
    pc.add_argument("--geoip", action="store_true")
    pc.set_defaults(func=cmd_profiles_create)

    pu = psub.add_parser("update", help="Update profile details")
    pu.add_argument("profile_id")
    pu.add_argument("--name")
    pu.add_argument("--sandbox-id")
    pu.add_argument("--harness")
    pu.add_argument("--project-id")
    pu.add_argument("--folder-path")
    pu.add_argument("--timezone")
    pu.add_argument("--locale")
    pu.add_argument("--proxy")
    pu.add_argument("--platform", choices=["windows", "macos", "linux"])
    pu.add_argument(
        "--extension-id",
        dest="extension_ids",
        action="append",
        default=None,
        help="Replace assigned trusted catalog extension ids; repeat for more than one",
    )
    pu.add_argument("--pinned", type=lambda v: str(v).lower() in {"1", "true", "yes"}, default=None)
    pu.add_argument("--geoip", type=lambda v: str(v).lower() in {"1", "true", "yes"}, default=None)
    pu.set_defaults(func=cmd_profiles_update)

    pd = psub.add_parser("delete", help="Delete profile")
    pd.add_argument("profile_id")
    pd.set_defaults(func=cmd_profiles_delete)

    pla = psub.add_parser("launch", help="Launch profile")
    pla.add_argument("profile_id")
    pla.set_defaults(func=cmd_profiles_launch)

    pst = psub.add_parser("stop", help="Stop profile")
    pst.add_argument("profile_id")
    pst.set_defaults(func=cmd_profiles_stop)

    pss = psub.add_parser("status", help="Profile runtime status + links")
    pss.add_argument("profile_id")
    pss.set_defaults(func=cmd_profiles_status)

    ph = psub.add_parser("health", help="Read or rerun profile health")
    ph.add_argument("profile_id")
    ph.add_argument("--run", action="store_true", help="Force a health probe")
    ph.set_defaults(func=cmd_profiles_health)

    pe = psub.add_parser("extensions", help="Read-only extension inventory")
    pe.add_argument("profile_id")
    pe.set_defaults(func=cmd_profiles_extensions)

    po = psub.add_parser("open-links", help="Steel-style VNC/CDP open URLs")
    po.add_argument("profile_id")
    po.add_argument("--prefer", choices=["local", "cloud"], default="local")
    po.add_argument("--mode", choices=["cdp", "vnc", "shell"], default="cdp")
    po.add_argument(
        "--field",
        help="Print one field only (cdp_fullscreen_url, vnc_fullscreen_url, websocket_url, cdp_url, …)",
    )
    po.set_defaults(func=cmd_profiles_open_links)

    p_open = sub.add_parser("open-session", help="Launch (optional) + return open links")
    p_open.add_argument("profile_id")
    p_open.add_argument("--prefer", choices=["local", "cloud"], default="local")
    p_open.add_argument("--mode", choices=["cdp", "vnc", "shell"], default="cdp")
    p_open.add_argument("--no-launch", action="store_true")
    p_open.set_defaults(func=cmd_open_session)

    tasks = sub.add_parser("tasks", help="Task sessions and Browser-Use runs")
    tsub = tasks.add_subparsers(dest="tasks_command", required=True)

    tc = tsub.add_parser("create", help="Create a task session for a fixed profile")
    tc.add_argument("--profile-id", required=True)
    tc.add_argument("--title")
    tc.set_defaults(func=cmd_tasks_create)

    tr = tsub.add_parser("run", help="Queue a Browser-Use run on a task session")
    tr.add_argument("session_id")
    tr.add_argument("--profile-id", required=True)
    tr.add_argument("--task", required=True)
    tr.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Repeatable https://host origin; automate callers should supply at least one",
    )
    tr.add_argument("--launch-if-stopped", action="store_true")
    tr.add_argument("--max-steps", type=int, default=20)
    tr.add_argument("--timeout-seconds", type=int, default=300)
    tr.add_argument("--model-alias")
    tr.set_defaults(func=cmd_tasks_run)

    runs = sub.add_parser("runs", help="Inspect or cancel Browser-Use runs")
    rsub = runs.add_subparsers(dest="runs_command", required=True)

    rg = rsub.add_parser("get", help="Inspect one run")
    rg.add_argument("run_id")
    rg.set_defaults(func=cmd_runs_get)

    rc = rsub.add_parser("cancel", help="Cancel one run")
    rc.add_argument("run_id")
    rc.set_defaults(func=cmd_runs_cancel)

    ro = rsub.add_parser("outputs", help="List typed outputs for a run")
    ro.add_argument("run_id")
    ro.add_argument("--after-sequence", type=int)
    ro.set_defaults(func=cmd_runs_outputs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
