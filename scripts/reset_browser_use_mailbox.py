#!/usr/bin/env python3
"""Rotate the Browser Use test mailbox entirely on the VCVM.

Only the new address and high-level status are printed. Passwords and bearer
tokens are generated on the VCVM, persisted with mode 0600, and copied to the
VCVM Unbrowse value-source store through stdin.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API = "https://api.mail.tm"
RUNTIME = Path("/run/user/1000")
EMAIL_FILE = RUNTIME / "cbm-browser-use-email"
TOKEN_FILE = RUNTIME / "cbm-browser-use-mail-token"


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "CloakBrowser-Manager/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload


def write_secret(path: Path, value: str) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(value, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def store_value(pointer: str, value: str) -> None:
    completed = subprocess.run(
        ["unbrowse", "build", "value-source", pointer, "--from-stdin", "--json"],
        input=value.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("VCVM value-source update failed")


def delete_existing_mailbox() -> bool:
    if not TOKEN_FILE.exists():
        return False
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        return False
    status, account = request("GET", "/me", token=token)
    if status != 200 or not isinstance(account, dict) or not account.get("id"):
        return False
    delete_status, _ = request("DELETE", f"/accounts/{account['id']}", token=token)
    return delete_status in {200, 204}


def main() -> int:
    deleted = delete_existing_mailbox()
    status, domains = request("GET", "/domains?page=1")
    if isinstance(domains, dict):
        members = domains.get("hydra:member", [])
    elif isinstance(domains, list):
        members = domains
    else:
        members = []
    usable = [
        item
        for item in members
        if isinstance(item, dict) and item.get("domain") and item.get("isActive", True)
    ]
    if status != 200 or not usable:
        raise RuntimeError("mailbox domain discovery failed")

    address = f"cbm-{secrets.token_hex(8)}@{usable[0]['domain']}"
    password = secrets.token_urlsafe(36)
    create_status, _ = request(
        "POST", "/accounts", body={"address": address, "password": password}
    )
    if create_status not in {200, 201}:
        raise RuntimeError("mailbox creation failed")
    token_status, token_payload = request(
        "POST", "/token", body={"address": address, "password": password}
    )
    token = token_payload.get("token") if isinstance(token_payload, dict) else None
    if token_status != 200 or not isinstance(token, str) or not token:
        raise RuntimeError("mailbox token creation failed")

    write_secret(EMAIL_FILE, address)
    write_secret(TOKEN_FILE, token)
    store_value("keychain://com.cloakbrowser/browser-use-email-vcvm", address)
    store_value("keychain://com.cloakbrowser/browser-use-mail-password-vcvm", password)
    print(json.dumps({"ok": True, "address": address, "previous_deleted": deleted}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
