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
  scripts/cbm_agent_ctl.py tasks run <session_id> --profile-id <id> --harness acpx --agent grok-build --task "Read title" --allowed-origin https://example.com
  scripts/cbm_agent_ctl.py tasks run <session_id> --profile-id <id> --harness unbrowse --task "Read title" --allowed-origin https://example.com
  scripts/cbm_agent_ctl.py runs get <run_id>
  scripts/cbm_agent_ctl.py runs cancel <run_id>
  scripts/cbm_agent_ctl.py worktree-audit --root /path/to/repo
  scripts/cbm_agent_ctl.py health
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.project_state import (  # noqa: E402
    PROJECT_STATE_MODES,
    atomic_write_project_state,
    build_project_state,
    default_project_state_path,
)
from backend.models import (  # noqa: E402
    CONTROL_PLANE_API_VERSION,
)
from scripts.cbm_worktree_audit import AuditConfig, audit_repository  # noqa: E402

_SENSITIVE_RESOURCE_FIELDS = {
    "proxy_url",
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "totp_seed",
    "provider_locator",
    "cdp_url",
    "cdp_ws_url",
    "cdp_http_url",
    "debugger_url",
}
_RESOURCE_URL_RE = re.compile(r"\b(?:https?|wss?|socks5?|socks5h)://[^\s<>)\"']+")
_RESOURCE_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_RESOURCE_TOKEN_RE = re.compile(r"\bcbm_(?:worker|run|agent|lease)_[A-Za-z0-9_-]+\b")
_RESOURCE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)"
    r"(\s*[:=]\s*)[^\s,;&]+"
)
_SECRET_QUERY_KEYS = {
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


def _is_sensitive_resource_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(
        normalized == field
        or normalized.startswith(f"{field}_")
        or normalized.endswith(f"_{field}")
        for field in _SENSITIVE_RESOURCE_FIELDS
    )


def _redact_resource_text(value: str) -> str:
    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urllib.parse.urlsplit(raw)
            netloc = parsed.netloc
            if "@" in netloc:
                netloc = f"[REDACTED]@{netloc.rsplit('@', 1)[1]}"
            query = [
                (key, "[REDACTED]" if key.lower() in _SECRET_QUERY_KEYS else item)
                for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            ]
            return urllib.parse.urlunsplit(
                (parsed.scheme, netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
            )
        except ValueError:
            return "[REDACTED_URL]"

    redacted = _RESOURCE_URL_RE.sub(redact_url, value)
    redacted = _RESOURCE_BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _RESOURCE_TOKEN_RE.sub("[REDACTED]", redacted)
    return _RESOURCE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        redacted,
    )


def _strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_sensitive(item)
            for key, item in value.items()
            if not _is_sensitive_resource_key(key)
        }
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_resource_text(value)
    return value


def _resource_id(payload: dict[str, Any]) -> str:
    for key in ("id", "account_id", "profile_id", "session_id", "run_id", "proxy_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _resource_version(payload: dict[str, Any]) -> int:
    for key in ("resource_version", "row_version", "version"):
        value = payload.get(key)
        if isinstance(value, int) and value >= 1:
            return value
    return 1


def _resource_spec(payload: dict[str, Any]) -> dict[str, Any]:
    return _strip_sensitive({
        key: value
        for key, value in payload.items()
        if not _is_sensitive_resource_key(key)
        and key
        not in {
            "id",
            "profile_id",
            "session_id",
            "run_id",
            "created_at",
            "updated_at",
            "resource_version",
            "row_version",
            "links",
            "status",
        }
    })


def resource_envelope(
    kind: str,
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the versioned public resource envelope without secret-like fields."""
    metadata: dict[str, Any] = {
        "id": _resource_id(payload),
        "resource_version": _resource_version(payload),
    }
    if request_id:
        metadata["request_id"] = request_id
    if isinstance(payload.get("created_at"), str):
        metadata["created_at"] = payload["created_at"]
    if isinstance(payload.get("updated_at"), str):
        metadata["updated_at"] = payload["updated_at"]
    return {
        "api_version": CONTROL_PLANE_API_VERSION,
        "kind": kind,
        "metadata": metadata,
        "spec": _resource_spec(payload),
        "status": _strip_sensitive(payload.get("status", {}))
        if isinstance(payload.get("status"), dict)
        else {},
        "links": _strip_sensitive(payload.get("links", []))
        if isinstance(payload.get("links"), list)
        else [],
    }


def _request_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if getattr(args, "idempotency_key", None):
        options["idempotency_key"] = args.idempotency_key
    if getattr(args, "if_version", None) is not None:
        options["if_version"] = args.if_version
    return options


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
    idempotency_key: str | None = None,
    if_version: int | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None
    headers = {
        "Accept": "application/json",
        **_auth_header(),
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if if_version is not None:
        headers["If-Match"] = str(if_version)
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


def _git_output(args: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Unable to read git state: git {' '.join(args)}") from exc


def _preferred_repo_remote(worktree: Path) -> str:
    remotes = set(_git_output(["remote"], cwd=worktree).splitlines())
    remote = "fork" if "fork" in remotes else "origin"
    return _git_output(["config", "--get", f"remote.{remote}.url"], cwd=worktree)


def _current_unmerged_files(worktree: Path) -> list[str]:
    raw = _git_output(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
    )
    paths: list[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return paths


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


def cmd_api_version(args: argparse.Namespace) -> None:
    _print(
        {
            "api_version": CONTROL_PLANE_API_VERSION,
            "kind": "ApiVersion",
            "metadata": {"id": "cloakbrowser-manager-api", "resource_version": 1},
        },
        True,
    )


def cmd_api_capabilities(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/v2/capabilities"), True)


def cmd_api_schema(args: argparse.Namespace) -> None:
    _print(_request("GET", "/api/v2/schemas/control-plane-resource-v1"), True)


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
    _print(_request("POST", "/api/profiles", body=body, **_request_options(args)), args.json)


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
    _print(
        _request("PUT", f"/api/profiles/{args.profile_id}", body=body, **_request_options(args)),
        args.json,
    )


def cmd_profiles_delete(args: argparse.Namespace) -> None:
    _print(_request("DELETE", f"/api/profiles/{args.profile_id}", **_request_options(args)), args.json)


def cmd_profiles_launch(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/profiles/{args.profile_id}/launch", **_request_options(args)), args.json)


def cmd_profiles_stop(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/profiles/{args.profile_id}/stop", **_request_options(args)), args.json)


def cmd_profiles_status(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/profiles/{args.profile_id}/status"), args.json)


def cmd_profiles_health(args: argparse.Namespace) -> None:
    if args.run:
        _print(
            _request(
                "POST",
                f"/api/profiles/{args.profile_id}/health/run",
                **_request_options(args),
            ),
            args.json,
        )
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


def cmd_accounts_list(args: argparse.Namespace) -> None:
    query = {
        key: value
        for key, value in {
            "profile_id": args.profile_id,
            "provider": args.provider,
            "auth_state": args.auth_state,
        }.items()
        if value is not None
    }
    _print(_request("GET", "/api/accounts", query=query or None), args.json)


def cmd_accounts_get(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/accounts/{args.account_id}"), args.json)


def cmd_accounts_create(args: argparse.Namespace) -> None:
    body = {
        key: value
        for key, value in {
            "profile_id": args.profile_id,
            "provider": args.provider,
            "subject_label": args.subject_label,
            "display_name": args.display_name,
            "origin": args.origin,
            "auth_state": args.auth_state,
            "second_factor_state": args.second_factor_state,
            "passkey_state": args.passkey_state,
            "secret_ref": args.secret_ref,
            "totp_ref": args.totp_ref,
            "last_seen_at": args.last_seen_at,
        }.items()
        if value is not None
    }
    _print(
        _request("POST", "/api/accounts", body=body, **_request_options(args)),
        args.json,
    )


def cmd_accounts_update(args: argparse.Namespace) -> None:
    body = {
        key: value
        for key, value in {
            "display_name": args.display_name,
            "origin": args.origin,
            "auth_state": args.auth_state,
            "second_factor_state": args.second_factor_state,
            "passkey_state": args.passkey_state,
            "secret_ref": args.secret_ref,
            "totp_ref": args.totp_ref,
            "last_seen_at": args.last_seen_at,
        }.items()
        if value is not None
    }
    if not body:
        raise SystemExit("No update fields provided")
    _print(
        _request(
            "PUT",
            f"/api/accounts/{args.account_id}",
            body=body,
            **_request_options(args),
        ),
        args.json,
    )


def cmd_accounts_history(args: argparse.Namespace) -> None:
    _print(
        _request(
            "GET",
            f"/api/accounts/{args.account_id}/events",
            query={"limit": str(args.limit)},
        ),
        args.json,
    )


def cmd_accounts_event(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"event_type": args.event_type}
    if args.auth_state is not None:
        body["auth_state"] = args.auth_state
    if args.occurred_at is not None:
        body["occurred_at"] = args.occurred_at
    _print(
        _request(
            "POST",
            f"/api/accounts/{args.account_id}/events",
            body=body,
            **_request_options(args),
        ),
        args.json,
    )


def cmd_accounts_delete(args: argparse.Namespace) -> None:
    _print(
        _request(
            "DELETE",
            f"/api/accounts/{args.account_id}",
            **_request_options(args),
        ),
        args.json,
    )


def cmd_open_session(args: argparse.Namespace) -> None:
    body = {
        "profile_id": args.profile_id,
        "launch": not args.no_launch,
        "prefer": args.prefer,
        "mode": args.mode,
    }
    _print(_request("POST", "/api/extension/sessions/open", body=body, **_request_options(args)), args.json)


def cmd_tasks_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"profile_id": args.profile_id}
    if args.title:
        body["title"] = args.title
    _print(_request("POST", "/api/task-sessions", body=body, **_request_options(args)), args.json)


def cmd_tasks_run(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "harness": args.harness,
        "task": args.task,
        "profile_id": args.profile_id,
        "launch_if_stopped": bool(args.launch_if_stopped),
        "allowed_origins": list(args.allowed_origin or []),
        "max_steps": args.max_steps,
        "timeout_seconds": args.timeout_seconds,
    }
    if args.harness == "acpx":
        if not args.agent:
            raise SystemExit("--agent is required with --harness acpx")
        body["agent"] = args.agent
    elif args.agent:
        raise SystemExit("--agent is only valid with --harness acpx")
    if args.model_alias:
        body["model_alias"] = args.model_alias
    _print(
        _request(
            "POST",
            f"/api/task-sessions/{args.session_id}/runs",
            body=body,
            **_request_options(args),
        ),
        args.json,
    )


def cmd_runs_get(args: argparse.Namespace) -> None:
    _print(_request("GET", f"/api/task-runs/{args.run_id}"), args.json)


def cmd_runs_cancel(args: argparse.Namespace) -> None:
    _print(_request("POST", f"/api/task-runs/{args.run_id}/cancel", **_request_options(args)), args.json)


def cmd_runs_retry_health(args: argparse.Namespace) -> None:
    _print(
        _request("POST", f"/api/task-runs/{args.run_id}/retry-health", **_request_options(args)),
        args.json,
    )


def cmd_runs_override_health(args: argparse.Namespace) -> None:
    _print(
        _request(
            "POST",
            f"/api/task-runs/{args.run_id}/override-health",
            body={"reason": args.reason},
            **_request_options(args),
        ),
        args.json,
    )


def cmd_runs_outputs(args: argparse.Namespace) -> None:
    query: dict[str, str] = {}
    if args.after_sequence is not None:
        query["after_sequence"] = str(args.after_sequence)
    _print(
        _request("GET", f"/api/task-runs/{args.run_id}/outputs", query=query or None),
        args.json,
    )


def cmd_project_state(args: argparse.Namespace) -> None:
    worktree = Path(_git_output(["rev-parse", "--show-toplevel"])).resolve()
    state = build_project_state(
        repo=_preferred_repo_remote(worktree),
        branch=_git_output(["branch", "--show-current"], cwd=worktree),
        worktree=str(worktree),
        mode=args.mode,
        owner=args.owner,
        active_ticket=args.active_ticket,
        completed_receipts=list(args.completed_receipt or []),
        unmerged_files=_current_unmerged_files(worktree),
        next_safe_step=args.next_safe_step,
        forbidden_actions=list(args.forbidden_action or []),
        stop_condition=args.stop_condition,
    )
    atomic_write_project_state(default_project_state_path(worktree), state)
    _print(state, True)


def cmd_worktree_audit(args: argparse.Namespace) -> None:
    payload = audit_repository(
        AuditConfig(
            root=Path(args.root),
            target_ref=args.target_ref,
            retention_days=args.retention_days,
            oversized_gib=args.oversized_gib,
            warn_free_gib=args.warn_free_gib,
            block_worktree_free_gib=args.block_worktree_free_gib,
            block_release_free_gib=args.block_release_free_gib,
        )
    )
    _print(payload, True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Always print JSON")
    parser.add_argument(
        "--idempotency-key",
        help="Client retry-correlation header; global server enforcement is pending",
    )
    parser.add_argument(
        "--if-version",
        type=int,
        help="Client If-Match header; global server enforcement is pending",
    )
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

    api = sub.add_parser("api", help="Versioned control-plane contract")
    apisub = api.add_subparsers(dest="api_command", required=True)

    av = apisub.add_parser("version", help="Print the control-plane API version")
    av.set_defaults(func=cmd_api_version)

    ac = apisub.add_parser("capabilities", help="Discover Manager-reported resources")
    ac.set_defaults(func=cmd_api_capabilities)

    asc = apisub.add_parser("schema", help="Fetch the resource-envelope schema")
    asc.set_defaults(func=cmd_api_schema)

    p_project_state = sub.add_parser(
        "project-state",
        help="Emit and atomically persist a strict ProjectStateV1 handoff receipt",
    )
    p_project_state.add_argument("--mode", choices=PROJECT_STATE_MODES, required=True)
    p_project_state.add_argument("--owner", required=True)
    p_project_state.add_argument("--active-ticket", required=True)
    p_project_state.add_argument(
        "--completed-receipt",
        action="append",
        default=[],
        help="Completed receipt or evidence line; repeat for more than one",
    )
    p_project_state.add_argument("--next-safe-step", required=True)
    p_project_state.add_argument(
        "--forbidden-action",
        action="append",
        default=[],
        help="Forbidden action for the next operator; repeat for more than one",
    )
    p_project_state.add_argument("--stop-condition", required=True)
    p_project_state.set_defaults(func=cmd_project_state)

    p_worktree_audit = sub.add_parser(
        "worktree-audit",
        help="Emit a read-only JSON receipt for git worktree cleanup safety",
    )
    p_worktree_audit.add_argument("--root", default=".", help="Any path inside the git repository")
    p_worktree_audit.add_argument(
        "--target-ref",
        help="Merge target; defaults to origin/main, main, origin/master, master",
    )
    p_worktree_audit.add_argument("--retention-days", type=int, default=7)
    p_worktree_audit.add_argument("--oversized-gib", type=float, default=32.0)
    p_worktree_audit.add_argument("--warn-free-gib", type=float, default=16.0)
    p_worktree_audit.add_argument("--block-worktree-free-gib", type=float, default=12.0)
    p_worktree_audit.add_argument("--block-release-free-gib", type=float, default=8.0)
    p_worktree_audit.set_defaults(func=cmd_worktree_audit)

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

    accounts = sub.add_parser("accounts", help="Profile-linked account metadata and auth history")
    asub = accounts.add_subparsers(dest="accounts_command", required=True)

    account_auth_states = ["unknown", "signed_in", "needs_2fa", "signed_out", "locked"]
    account_factor_states = ["unknown", "off", "enrolled", "required"]

    al = asub.add_parser("list", help="List visible account metadata")
    al.add_argument("--profile-id")
    al.add_argument("--provider")
    al.add_argument("--auth-state", choices=account_auth_states)
    al.set_defaults(func=cmd_accounts_list)

    ag = asub.add_parser("get", help="Get one account metadata record")
    ag.add_argument("account_id")
    ag.set_defaults(func=cmd_accounts_get)

    ac = asub.add_parser("create", help="Create metadata-only account linkage")
    ac.add_argument("--profile-id", required=True)
    ac.add_argument("--provider", required=True)
    ac.add_argument("--subject-label", required=True)
    ac.add_argument("--display-name")
    ac.add_argument("--origin")
    ac.add_argument("--auth-state", choices=account_auth_states)
    ac.add_argument("--second-factor-state", choices=account_factor_states)
    ac.add_argument("--passkey-state", choices=account_factor_states)
    ac.add_argument("--secret-ref", help="Opaque secret reference id; never a secret value")
    ac.add_argument("--totp-ref", help="Opaque TOTP reference id; never a seed or code")
    ac.add_argument("--last-seen-at")
    ac.set_defaults(func=cmd_accounts_create)

    au = asub.add_parser("update", help="Update metadata-only account state")
    au.add_argument("account_id")
    au.add_argument("--display-name")
    au.add_argument("--origin")
    au.add_argument("--auth-state", choices=account_auth_states)
    au.add_argument("--second-factor-state", choices=account_factor_states)
    au.add_argument("--passkey-state", choices=account_factor_states)
    au.add_argument("--secret-ref", help="Opaque secret reference id; never a secret value")
    au.add_argument("--totp-ref", help="Opaque TOTP reference id; never a seed or code")
    au.add_argument("--last-seen-at")
    au.set_defaults(func=cmd_accounts_update)

    ah = asub.add_parser("history", help="List append-only account auth history")
    ah.add_argument("account_id")
    ah.add_argument("--limit", type=int, default=100)
    ah.set_defaults(func=cmd_accounts_history)

    ae = asub.add_parser("event", help="Append one typed auth history event")
    ae.add_argument("account_id")
    ae.add_argument(
        "--type",
        "--event-type",
        dest="event_type",
        choices=[
            "observed",
            "signed_in",
            "signed_out",
            "auth_state_changed",
            "two_factor_required",
            "two_factor_enrolled",
            "passkey_enrolled",
            "secret_reference_changed",
        ],
        required=True,
    )
    ae.add_argument("--auth-state", choices=account_auth_states)
    ae.add_argument("--occurred-at")
    ae.set_defaults(func=cmd_accounts_event)

    ad = asub.add_parser("delete", help="Delete account metadata while preserving history")
    ad.add_argument("account_id")
    ad.set_defaults(func=cmd_accounts_delete)

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

    tr = tsub.add_parser("run", help="Queue a managed Browser Use or ACPX run")
    tr.add_argument("session_id")
    tr.add_argument("--profile-id", required=True)
    tr.add_argument("--task", required=True)
    tr.add_argument(
        "--harness",
        choices=["browser-use", "acpx", "unbrowse", "stagehand"],
        default="browser-use",
        help="Managed worker backend",
    )
    tr.add_argument(
        "--agent",
        choices=["codex", "claude", "cursor", "grok-build", "opencode"],
        help="ACPX adapter; valid only with --harness acpx",
    )
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

    runs = sub.add_parser("runs", help="Inspect or cancel managed browser runs")
    rsub = runs.add_subparsers(dest="runs_command", required=True)

    rg = rsub.add_parser("get", help="Inspect one run")
    rg.add_argument("run_id")
    rg.set_defaults(func=cmd_runs_get)

    rc = rsub.add_parser("cancel", help="Cancel one run")
    rc.add_argument("run_id")
    rc.set_defaults(func=cmd_runs_cancel)

    rrh = rsub.add_parser("retry-health", help="Refresh the health gate for one blocked run")
    rrh.add_argument("run_id")
    rrh.set_defaults(func=cmd_runs_retry_health)

    roh = rsub.add_parser(
        "override-health",
        help="Apply an explicit audited override to an overridable health block",
    )
    roh.add_argument("run_id")
    roh.add_argument("--reason", required=True)
    roh.set_defaults(func=cmd_runs_override_health)

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
