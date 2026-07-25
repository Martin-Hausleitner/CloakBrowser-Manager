#!/usr/bin/env python3
"""Honest preflight for the VCVM host Orca bridge (no secrets printed)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ORCA_BIN = "/home/coder/.local/bin/orca-ide"
DEFAULT_WORKTREE = "path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use"
DEFAULT_AGENT_WRAPPER = (
    "/home/coder/vk-repos/CloakBrowser-Manager-browser-use/scripts/orca_agent_cli.sh"
)
REQUIRED_MOUNTS = (
    Path("/home/coder/orca"),
    Path("/home/coder/.local"),
    Path("/home/coder/.config/orca"),
)

_BACKEND_KEY = Path(__file__).resolve().parents[1] / "backend" / "orca_agent_key.py"


def _load_agent_key_module():
    spec = importlib.util.spec_from_file_location("orca_agent_key", _BACKEND_KEY)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load orca_agent_key module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_agent_key = _load_agent_key_module()
DEFAULT_AGENT_KEY_FILE = _agent_key.DEFAULT_AGENT_KEY_FILE
check_agent_key_file = _agent_key.check_agent_key_file


def check_paths(*, orca_bin: Path) -> list[str]:
    errors: list[str] = []
    for path in REQUIRED_MOUNTS:
        if not path.exists():
            errors.append(f"missing required Orca host path: {path}")
    if not orca_bin.is_file():
        errors.append("missing required Orca binary path")
    elif not os.access(orca_bin, os.X_OK):
        errors.append("Orca binary is not executable")
    return errors


def check_agent_wrapper(wrapper: str | Path | None = None) -> list[str]:
    """Host-only wrapper readiness (never mounted into the Manager container)."""
    candidate = Path(
        str(wrapper or os.environ.get("CBM_ORCA_AGENT_WRAPPER") or DEFAULT_AGENT_WRAPPER)
    ).expanduser()
    if candidate.name != "orca_agent_cli.sh":
        return ["Orca agent wrapper must be scripts/orca_agent_cli.sh"]
    if not candidate.is_file():
        return ["missing required Orca agent wrapper on the host"]
    if not os.access(candidate, os.R_OK):
        return ["Orca agent wrapper is not readable on the host"]
    if not os.access(candidate, os.X_OK):
        return ["Orca agent wrapper is not executable on the host"]
    return []


def _run_orca_json(
    orca_bin: Path,
    argv: list[str],
    *,
    timeout: float,
) -> tuple[dict | None, list[str]]:
    try:
        completed = subprocess.run(
            [str(orca_bin), *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env={**os.environ, "HOME": os.environ.get("HOME", "/home/coder")},
        )
    except subprocess.TimeoutExpired:
        return None, [f"Orca {' '.join(argv[:2])} timed out"]
    except OSError:
        return None, [f"Orca {' '.join(argv[:2])} could not be executed"]

    if completed.returncode != 0:
        return None, [f"Orca {' '.join(argv[:2])} check failed"]

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return None, [f"Orca {' '.join(argv[:2])} returned non-JSON output"]
    if not isinstance(payload, dict):
        return None, [f"Orca {' '.join(argv[:2])} returned a non-object payload"]
    return payload, []


def check_runtime(orca_bin: Path, *, timeout: float = 15.0) -> list[str]:
    payload, errors = _run_orca_json(orca_bin, ["status", "--json"], timeout=timeout)
    if errors:
        return errors
    assert payload is not None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if not isinstance(result, dict):
        return ["Orca status payload missing result"]
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    out: list[str] = []
    if not runtime.get("reachable"):
        out.append("Orca runtime is not reachable")
    state = str(runtime.get("state") or "")
    if state and state != "ready":
        out.append("Orca runtime is not ready")
    return out


def check_worktree(
    orca_bin: Path,
    worktree: str,
    *,
    timeout: float = 15.0,
) -> list[str]:
    """Require the Orca-registered vk-repos worktree (not the deploy copy)."""
    selector = (worktree or DEFAULT_WORKTREE).strip()
    if selector != DEFAULT_WORKTREE:
        return [
            "Orca worktree must be path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use"
        ]
    payload, errors = _run_orca_json(
        orca_bin,
        ["worktree", "show", "--worktree", selector, "--json"],
        timeout=timeout,
    )
    if errors:
        return ["Orca worktree show failed for the registered vk-repos checkout"]
    assert payload is not None
    if payload.get("ok") is False:
        return ["Orca worktree show failed for the registered vk-repos checkout"]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    worktree_obj = result.get("worktree") if isinstance(result.get("worktree"), dict) else result
    path = str((worktree_obj or {}).get("path") or "")
    expected = "/home/coder/vk-repos/CloakBrowser-Manager-browser-use"
    if path and path != expected:
        return ["Orca worktree path does not match the registered vk-repos checkout"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orca-bin",
        default=os.environ.get("CBM_ORCA_BIN", DEFAULT_ORCA_BIN),
        help="Path to orca-ide (never logged beyond existence checks)",
    )
    parser.add_argument(
        "--worktree",
        default=os.environ.get("CBM_ORCA_WORKTREE", DEFAULT_WORKTREE),
        help="Orca worktree selector that must already be registered",
    )
    parser.add_argument(
        "--agent-key-file",
        default=os.environ.get("CBM_AGENT_KEY_FILE", DEFAULT_AGENT_KEY_FILE),
        help="Path to the scoped cbm_agent_ key file (contents never printed)",
    )
    parser.add_argument(
        "--agent-wrapper",
        default=os.environ.get("CBM_ORCA_AGENT_WRAPPER", DEFAULT_AGENT_WRAPPER),
        help="Host path to orca_agent_cli.sh (not mounted into the Manager container)",
    )
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Skip Orca status/worktree probes (agent key/wrapper are still required)",
    )
    args = parser.parse_args(argv)

    orca_bin = Path(str(args.orca_bin)).expanduser()
    errors = check_paths(orca_bin=orca_bin)
    # Fail closed on missing/bad host wrapper + agent key before compose.
    # Contents of the key file are never printed. Neither path is mounted into
    # the Manager container; start_session still passes the host wrapper path
    # through host Orca terminal.create.
    errors.extend(check_agent_wrapper(args.agent_wrapper))
    errors.extend(check_agent_key_file(args.agent_key_file))
    if not args.skip_runtime and orca_bin.is_file() and os.access(orca_bin, os.X_OK):
        errors.extend(check_runtime(orca_bin))
        errors.extend(check_worktree(orca_bin, str(args.worktree)))

    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        print("VCVM Orca host bridge preflight failed", file=sys.stderr)
        return 78

    print("VCVM Orca host bridge preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
