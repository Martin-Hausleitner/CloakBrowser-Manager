#!/usr/bin/env python3
"""Compile redacted extension recordings into Browser Use replay contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse


RECORDING_SCHEMA = "cloakbrowser.secure-action-recording.v1"
REPLAY_SCHEMA = "cloakbrowser.browser-use-replay.v1"
ALLOWED_ACTIONS = {"navigate", "click", "fill"}
SECRET_REF_RE = re.compile(r"^secretref-[A-Za-z0-9][A-Za-z0-9._-]{2,143}$")
FORBIDDEN_KEYS = {
    "password",
    "passwd",
    "token",
    "cookie",
    "cookies",
    "authorization",
    "raw_value",
    "value",
    "otp",
    "totp",
    "passkey",
    "cvc",
    "cvv",
}
SECRET_TEXT_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer|bearer\s+[a-z0-9._~+/=-]{8,}|(?:password|token|secret|cookie|otp|totp|api[_-]?key)\s*[:=])"
)


class RecordingCompileError(ValueError):
    """Sanitized compiler error."""


def _reject_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise RecordingCompileError("recording contains a forbidden raw field")
            _reject_forbidden(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_forbidden(item)
        return
    if isinstance(value, str) and SECRET_TEXT_RE.search(value):
        raise RecordingCompileError("recording contains forbidden secret-like text")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RecordingCompileError("recording step URL is invalid")
    default_port = (parsed.scheme == "http" and parsed.port in {None, 80}) or (
        parsed.scheme == "https" and parsed.port in {None, 443}
    )
    authority = parsed.hostname if default_port else f"{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{authority}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def compile_recording(flow: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(flow, dict) or flow.get("schema") != RECORDING_SCHEMA:
        raise RecordingCompileError("unsupported recording schema")
    _reject_forbidden(flow)
    raw_steps = flow.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 500:
        raise RecordingCompileError("recording steps must contain 1 to 500 items")

    steps: list[dict[str, Any]] = []
    origins: set[str] = set()
    secret_refs: set[str] = set()
    prompt_lines = [
        "Replay this operator-recorded flow through the Manager-owned Browser Use profile.",
        "Stay within the allowed origins and stop for any unresolved human input.",
    ]
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise RecordingCompileError("recording step must be an object")
        action = str(raw.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise RecordingCompileError("recording action is unsupported")
        url = str(raw.get("url") or "")
        origin = _origin(url)
        origins.add(origin)
        step: dict[str, Any] = {
            "id": f"step-{index:03d}",
            "action": action,
            "url": url,
        }
        if action == "navigate":
            prompt_lines.append(f"{index}. Navigate to {url}")
        else:
            selector = str(raw.get("selector") or "").strip()
            if not selector or len(selector) > 240:
                raise RecordingCompileError("recording selector is invalid")
            step["selector"] = selector
            if action == "click":
                prompt_lines.append(f"{index}. Click {selector}")
            else:
                secret_ref = raw.get("secretRef")
                value_length = int(raw.get("valueLength") or 0)
                if secret_ref is not None:
                    secret_ref = str(secret_ref)
                    if SECRET_REF_RE.fullmatch(secret_ref) is None:
                        raise RecordingCompileError("recording secret reference is invalid")
                    step["secretRef"] = secret_ref
                    secret_refs.add(secret_ref)
                    prompt_lines.append(
                        f"{index}. Fill {selector} using the origin-bound secret reference {secret_ref}"
                    )
                else:
                    step["requiresHumanInput"] = True
                    step["valueLength"] = max(0, min(value_length, 10_000))
                    prompt_lines.append(
                        f"{index}. Request human input for {selector}; do not infer or recover the recorded value"
                    )
        steps.append(step)

    contract: dict[str, Any] = {
        "schema": REPLAY_SCHEMA,
        "sourceRecordingId": str(flow.get("id") or ""),
        "allowedOrigins": sorted(origins),
        "secretRefs": sorted(secret_refs),
        "steps": steps,
        "prompt": "\n".join(prompt_lines),
    }
    contract["sha256"] = hashlib.sha256(_canonical_json(contract).encode("utf-8")).hexdigest()
    return contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="secure_recording_compiler")
    parser.add_argument("recording", help="Path to a stopped redacted recording JSON")
    args = parser.parse_args(argv)
    try:
        with open(args.recording, encoding="utf-8") as handle:
            result = compile_recording(json.load(handle))
    except Exception as exc:  # noqa: BLE001 - sanitized CLI failure.
        print(str(exc) if isinstance(exc, RecordingCompileError) else "recording compile failed", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

