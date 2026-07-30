"""Disposable CloakBrowser profile loader/share E2E helpers.

Proves extension loading evidence and safe opt-in profile-state transfer using
only synthetic cookies on a local fixture origin. Never inspects Chromium
cookie DBs, password stores, passkeys, tokens, or proxy credentials.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend import database as db
from backend import profile_share

DEFAULT_FIXTURE_HOST = "127.0.0.1"
DEFAULT_SYNTHETIC_COOKIE_NAME = "cbm_synthetic_session"
DEFAULT_SYNTHETIC_COOKIE_VALUE = "fixture-token-aabbccdd"


@dataclass
class FixtureOriginServer:
    """Tiny local HTTP origin that only sets a synthetic session cookie."""

    host: str = DEFAULT_FIXTURE_HOST
    port: int = 0
    cookie_name: str = DEFAULT_SYNTHETIC_COOKIE_NAME
    cookie_value: str = DEFAULT_SYNTHETIC_COOKIE_VALUE
    _httpd: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def synthetic_cookie(self) -> dict[str, Any]:
        return {
            "name": self.cookie_name,
            "value": self.cookie_value,
            "domain": self.host,
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "sameSite": "Lax",
            "origin": self.origin,
        }

    def start(self) -> "FixtureOriginServer":
        cookie_name = self.cookie_name
        cookie_value = self.cookie_value
        host = self.host

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b"cbm-profile-share-fixture-ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                # Synthetic only — never real session material.
                self.send_header(
                    "Set-Cookie",
                    f"{cookie_name}={cookie_value}; Path=/; Domain={host}; SameSite=Lax",
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        # Bind ephemeral port when port=0.
        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _free_port(host: str = DEFAULT_FIXTURE_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def install_fixture_extension(catalog_root: Path, ext_id: str = "fjcjfaeimhopmpnoemigapegahhjnbkl") -> str:
    """Write a minimal MV3 extension under a catalog root for load-extension proof."""
    ext_dir = catalog_root / ext_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "CloakBrowser Profile Sync Fixture",
                "version": "0.0.1-e2e",
                "manifest_version": 3,
                "description": "Disposable extension fixture for profile share E2E",
                "permissions": ["storage"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (ext_dir / "background.js").write_text(
        "// disposable fixture extension — no network, no secrets\n",
        encoding="utf-8",
    )
    return ext_id


@dataclass
class ProfileShareE2EResult:
    ok: bool
    source_profile_id: str
    imported_profile_id: str
    cloned_profile_id: str
    fixture_origin: str
    extension_ids: list[str]
    load_extension_arg: str | None
    launch_args: list[str]
    synthetic_cookie_names: list[str]
    redaction_ok: bool
    cleanup: dict[str, Any]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source_profile_id": self.source_profile_id,
            "imported_profile_id": self.imported_profile_id,
            "cloned_profile_id": self.cloned_profile_id,
            "fixture_origin": self.fixture_origin,
            "extension_ids": self.extension_ids,
            "load_extension_arg": self.load_extension_arg,
            "launch_args": self.launch_args,
            "synthetic_cookie_names": self.synthetic_cookie_names,
            "redaction_ok": self.redaction_ok,
            "cleanup": self.cleanup,
            "evidence": self.evidence,
            "schema": "cloakbrowser.profile-share-e2e-result.v1",
        }


def run_profile_share_e2e(
    *,
    work_dir: Path,
    extension_catalog_dir: Path | None = None,
    fixture_host: str = DEFAULT_FIXTURE_HOST,
) -> ProfileShareE2EResult:
    """Run the full disposable profile share E2E against a temp database root.

    Caller must have pointed ``backend.database.DATA_DIR`` / ``DB_PATH`` at a
    disposable location and called ``db.init_db()`` first (or pass a fresh work_dir
    used by the CLI entrypoint).
    """
    catalog_dir = extension_catalog_dir or (work_dir / "extension-catalog")
    ext_id = install_fixture_extension(catalog_dir)

    fixture = FixtureOriginServer(host=fixture_host, port=_free_port(fixture_host)).start()
    created_ids: list[str] = []
    try:
        source = db.create_profile(
            name="e2e-share-source",
            fingerprint_seed=9001,
            platform="linux",
            extension_ids=[ext_id],
            launch_args=["--lang=en-US"],
            # Intentional secret-like proxy — must never appear in share payloads.
            proxy="http://alice:top-secret@proxy.example:8080",
            notes="disposable profile share e2e source",
        )
        created_ids.append(source["id"])

        # Plant forbidden Chromium stores; share path must never open them.
        default_dir = Path(source["user_data_dir"]) / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "Cookies").write_bytes(b"REAL_COOKIE_DB_MUST_NEVER_BE_READ")
        (default_dir / "Login Data").write_bytes(b"REAL_PASSWORD_STORE")

        # In-process assertion uses raw paths; public result keeps digests only.
        evidence_raw = profile_share.build_extension_launch_evidence(
            source, include_raw_paths=True
        )
        evidence = profile_share.build_extension_launch_evidence(source)
        if not evidence_raw.get("load_extension_arg_raw") or not evidence.get(
            "extensions_resolved"
        ):
            raise profile_share.ProfileShareError(
                "extension load arg missing for fixture catalog id"
            )
        if not any(
            arg.startswith("--load-extension=")
            for arg in (evidence_raw.get("launch_args") or [])
        ):
            raise profile_share.ProfileShareError("launch args missing --load-extension")

        denied = profile_share.export_profile_share(source, opt_in=False)
        if denied.get("opt_in") or denied.get("synthetic_cookies") or denied.get("metadata"):
            raise profile_share.ProfileShareError("export without opt-in leaked state")

        cookie = fixture.synthetic_cookie
        profile_share.validate_synthetic_cookie(cookie)

        export_payload = profile_share.export_profile_share(
            source,
            opt_in=True,
            synthetic_cookies=[cookie],
        )
        redacted = profile_share.redact_share_payload(export_payload)
        redaction_blob = json.dumps(redacted)
        redaction_ok = all(
            marker not in redaction_blob
            for marker in ("top-secret", "alice:", "REAL_COOKIE", "REAL_PASSWORD", "sk-live")
        )
        if not redaction_ok:
            raise profile_share.ProfileShareError("redaction failed for share payload")

        # Origin binding: cookie domain host must equal fixture origin host.
        exported_cookie = redacted["synthetic_cookies"][0]
        fixture_host_name = urlparse(fixture.origin).hostname
        if exported_cookie["domain"] != fixture_host_name:
            raise profile_share.ProfileShareError("cookie domain not bound to fixture origin")
        if exported_cookie["origin"] != fixture.origin:
            raise profile_share.ProfileShareError("cookie origin binding mismatch")

        imported = profile_share.import_profile_share(redacted, opt_in=True)
        created_ids.append(imported["id"])
        imported_cookies = profile_share.load_synthetic_cookies(imported["user_data_dir"])
        if not imported_cookies or imported_cookies[0]["name"] != cookie["name"]:
            raise profile_share.ProfileShareError("imported synthetic cookie sidecar missing")
        if (Path(imported["user_data_dir"]) / "Default" / "Cookies").exists():
            raise profile_share.ProfileShareError("import fabricated Chromium Cookies DB")

        cloned = profile_share.clone_profile(
            source["id"],
            opt_in=True,
            synthetic_cookies=[cookie],
        )
        created_ids.append(cloned["id"])
        if cloned.get("proxy") is not None:
            raise profile_share.ProfileShareError("clone copied proxy credentials")

        cleanup_results = []
        for profile_id in list(created_ids):
            cleanup_results.append(profile_share.cleanup_disposable_profile(profile_id))
        all_cleaned = all(item.get("deleted") for item in cleanup_results)

        result = ProfileShareE2EResult(
            ok=bool(
                all_cleaned
                and redaction_ok
                and evidence.get("extensions_resolved")
                and evidence.get("load_extension_arg")
            ),
            source_profile_id=source["id"],
            imported_profile_id=imported["id"],
            cloned_profile_id=cloned["id"],
            fixture_origin=fixture.origin,
            extension_ids=list(evidence.get("extension_ids") or []),
            load_extension_arg=evidence.get("load_extension_arg"),
            launch_args=list(evidence.get("launch_args") or []),
            synthetic_cookie_names=[cookie["name"]],
            redaction_ok=redaction_ok,
            cleanup={"profiles": cleanup_results, "all_deleted": all_cleaned},
            evidence={
                "export_opt_in_denied": denied,
                "extension": {
                    "ids": evidence.get("extension_ids"),
                    "load_extension_arg": evidence.get("load_extension_arg"),
                    "resolved_path_count": evidence.get("resolved_path_count"),
                    "resolved_path_digests": evidence.get("resolved_path_digests"),
                    "extensions_resolved": evidence.get("extensions_resolved"),
                },
                "import_cookie_origin": imported_cookies[0]["origin"],
                "excluded": redacted.get("excluded"),
                "schema": profile_share.PROFILE_SHARE_SCHEMA,
            },
        )
        return result
    finally:
        fixture.stop()
        # Best-effort leftover cleanup if a mid-run failure skipped normal cleanup.
        for profile_id in created_ids:
            if db.get_profile(profile_id) is not None:
                profile_share.cleanup_disposable_profile(profile_id)
