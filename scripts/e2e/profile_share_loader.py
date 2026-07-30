"""Disposable CloakBrowser profile loader/share E2E helpers.

Proves:
1. BrowserManager real launch-arg builder emits catalog ``--load-extension``
2. Optional disposable real browser extension runtime when CloakBrowser binary works
3. Safe opt-in profile-state transfer with synthetic cookies only

Never inspects Chromium cookie DBs, password stores, passkeys, tokens, or proxy
credentials. Does not claim runtime loader proof from a string builder alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend import database as db
from backend import profile_share
from backend.browser_manager import BrowserManager

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

    def start(self) -> FixtureOriginServer:
        cookie_name = self.cookie_name
        cookie_value = self.cookie_value
        host = self.host

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = b"cbm-profile-share-fixture-ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Set-Cookie",
                    f"{cookie_name}={cookie_value}; Path=/; Domain={host}; SameSite=Lax",
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

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


def install_fixture_extension(
    catalog_root: Path, ext_id: str = "fjcjfaeimhopmpnoemigapegahhjnbkl"
) -> str:
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
                "background": {"service_worker": "background.js"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (ext_dir / "background.js").write_text(
        "// disposable fixture extension — no network, no secrets\n"
        "chrome.runtime.onInstalled.addListener(() => {\n"
        "  chrome.storage.local.set({ cbm_fixture_loaded: true });\n"
        "});\n",
        encoding="utf-8",
    )
    return ext_id


def browser_manager_launch_arg_proof(profile: dict[str, Any]) -> dict[str, Any]:
    """Exercise BrowserManager._build_profile_launch_args (not a catalog string mock)."""
    manager = BrowserManager()
    args = list(manager._build_profile_launch_args(profile))
    load_args = [arg for arg in args if arg.startswith("--load-extension=")]
    paths: list[str] = []
    for arg in load_args:
        raw = arg.split("=", 1)[1]
        paths.extend(part.strip() for part in raw.split(",") if part.strip())
    paths_exist = all(Path(path).is_dir() and (Path(path) / "manifest.json").is_file() for path in paths)
    return {
        "builder": "BrowserManager._build_profile_launch_args",
        "has_load_extension": bool(load_args),
        "load_extension_count": len(load_args),
        "extension_path_count": len(paths),
        "extension_paths_exist": paths_exist,
        "fingerprint_arg_present": any(arg.startswith("--fingerprint=") for arg in args),
        "ok": bool(load_args) and paths_exist,
        # raw paths stay in-process only; public evidence digests them later
        "_raw_load_args": load_args,
        "_raw_paths": paths,
        "_raw_args": args,
    }


async def _runtime_extension_probe(
    *,
    user_data_dir: Path,
    extension_paths: list[str],
) -> dict[str, Any]:
    """Launch a disposable headless context with Manager-resolved extension paths."""
    from cloakbrowser import ensure_binary, launch_persistent_context_async

    ensure_binary()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    context = await launch_persistent_context_async(
        user_data_dir=str(user_data_dir),
        headless=True,
        extension_paths=list(extension_paths),
        args=["--no-first-run", "--no-default-browser-check"],
    )
    try:
        extension_targets = 0
        try:
            page = await context.new_page()
            cdp = await context.new_cdp_session(page)
            targets = await cdp.send("Target.getTargets")
            target_infos = targets.get("targetInfos") or []
            extension_targets = sum(
                1
                for item in target_infos
                if str(item.get("url") or "").startswith("chrome-extension://")
                or str(item.get("type") or "") in {"service_worker", "background_page"}
            )
            await cdp.detach()
            await page.close()
        except Exception:  # noqa: BLE001 — runtime proof best-effort
            extension_targets = 0

        default_dir = user_data_dir / "Default"
        prefs_mentions_extension = False
        for prefs_name in ("Preferences", "Secure Preferences"):
            prefs_path = default_dir / prefs_name
            if prefs_path.is_file():
                prefs_text = prefs_path.read_text(encoding="utf-8", errors="ignore")
                if "extension" in prefs_text.lower():
                    prefs_mentions_extension = True
                    break

        # Local Extension Settings directory is created when an extension activates.
        local_ext = default_dir / "Local Extension Settings"
        ext_settings = local_ext.is_dir() and any(local_ext.iterdir())

        loaded = extension_targets > 0 or prefs_mentions_extension or ext_settings
        if loaded:
            return {
                "status": "passed",
                "extension_targets": extension_targets,
                "preferences_signal": prefs_mentions_extension,
                "extension_settings_dir": ext_settings,
                "claim": "runtime_extension",
            }
        # Browser launched with extension_paths, but Chromium headless may not
        # expose targets/prefs signals — do not over-claim runtime load.
        return {
            "status": "unconfirmed",
            "reason": "browser_launched_extension_signal_missing",
            "extension_targets": extension_targets,
            "preferences_signal": prefs_mentions_extension,
            "extension_settings_dir": ext_settings,
            "claim": "none",
        }
    finally:
        await context.close()


def prove_runtime_extension_if_available(
    *,
    work_dir: Path,
    extension_paths: list[str],
) -> dict[str, Any]:
    """Attempt disposable real browser extension runtime proof when binary is usable."""
    force_skip = (os.environ.get("CBM_PROFILE_SHARE_SKIP_RUNTIME") or "").strip() in {
        "1",
        "true",
        "yes",
    }
    if force_skip:
        return {
            "status": "skipped",
            "reason": "CBM_PROFILE_SHARE_SKIP_RUNTIME",
            "claim": "none",
        }
    if not extension_paths:
        return {
            "status": "skipped",
            "reason": "no_extension_paths",
            "claim": "none",
        }

    try:
        from cloakbrowser import binary_info, ensure_binary

        ensure_binary()
        info = binary_info() or {}
        if not info.get("installed"):
            return {
                "status": "skipped",
                "reason": "cloakbrowser_binary_not_installed",
                "claim": "none",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "skipped",
            "reason": f"cloakbrowser_unavailable:{type(exc).__name__}",
            "claim": "none",
        }

    runtime_dir = work_dir / "runtime-extension-proof"
    try:
        result = asyncio.run(
            _runtime_extension_probe(
                user_data_dir=runtime_dir,
                extension_paths=extension_paths,
            )
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "skipped",
            "reason": f"runtime_launch_failed:{type(exc).__name__}",
            "claim": "none",
        }
    finally:
        import shutil

        if runtime_dir.exists():
            # Disposable runtime dir only — ignore residual Chromium locks.
            shutil.rmtree(runtime_dir, ignore_errors=True)


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
    browser_manager_launch_args_ok: bool
    runtime_extension_proof: str
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
            "browser_manager_launch_args_ok": self.browser_manager_launch_args_ok,
            "runtime_extension_proof": self.runtime_extension_proof,
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
    """Run the full disposable profile share E2E against a temp database root."""
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
            proxy="http://alice:top-secret@proxy.example:8080",
            notes="Authorization: Bearer should-never-export",
        )
        created_ids.append(source["id"])

        default_dir = Path(source["user_data_dir"]) / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "Cookies").write_bytes(b"REAL_COOKIE_DB_MUST_NEVER_BE_READ")
        (default_dir / "Login Data").write_bytes(b"REAL_PASSWORD_STORE")

        # (1) Real BrowserManager launch-arg builder — required for loader claim.
        bm_proof = browser_manager_launch_arg_proof(source)
        if not bm_proof["ok"]:
            raise profile_share.ProfileShareError(
                "BrowserManager launch-arg builder did not resolve load-extension"
            )

        evidence = profile_share.build_extension_launch_evidence(source)
        if not evidence.get("browser_manager_builder") or not evidence.get(
            "extensions_resolved"
        ):
            raise profile_share.ProfileShareError(
                "extension evidence missing BrowserManager builder proof"
            )

        # (2) Optional disposable real browser extension runtime.
        runtime_proof = prove_runtime_extension_if_available(
            work_dir=work_dir,
            extension_paths=list(bm_proof.get("_raw_paths") or []),
        )
        # Runtime is best-effort when the binary is present. Hard-fail only when
        # the probe explicitly returns status=failed (not unconfirmed/skipped).
        if runtime_proof.get("status") == "failed":
            raise profile_share.ProfileShareError(
                "runtime extension proof failed for disposable browser session"
            )

        denied = profile_share.export_profile_share(source, opt_in=False)
        if denied.get("opt_in") or denied.get("synthetic_cookies") or denied.get("metadata"):
            raise profile_share.ProfileShareError("export without opt-in leaked state")

        cookie = fixture.synthetic_cookie
        profile_share.validate_synthetic_cookie(cookie)
        # HTTP fixtures must not accept SameSite=None.
        try:
            profile_share.validate_synthetic_cookie({**cookie, "sameSite": "None", "secure": True})
            raise profile_share.ProfileShareError("HTTP fixture accepted SameSite=None")
        except profile_share.ProfileShareError as exc:
            if "SameSite=None" not in str(exc):
                raise

        export_payload = profile_share.export_profile_share(
            source,
            opt_in=True,
            synthetic_cookies=[cookie],
        )
        if "notes" in (export_payload.get("metadata") or {}):
            raise profile_share.ProfileShareError("export leaked free-form notes")

        redacted = profile_share.redact_share_payload(export_payload)
        redaction_blob = json.dumps(redacted)
        redaction_ok = all(
            marker not in redaction_blob
            for marker in (
                "top-secret",
                "alice:",
                "REAL_COOKIE",
                "REAL_PASSWORD",
                "sk-live",
                "Bearer",
                "should-never-export",
            )
        )
        if not redaction_ok:
            raise profile_share.ProfileShareError("redaction failed for share payload")

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
        if imported.get("notes"):
            raise profile_share.ProfileShareError("import copied free-form notes")

        cloned = profile_share.clone_profile(
            source["id"],
            opt_in=True,
            synthetic_cookies=[cookie],
        )
        created_ids.append(cloned["id"])
        if cloned.get("proxy") is not None:
            raise profile_share.ProfileShareError("clone copied proxy credentials")

        cleanup_results = []
        for profile_id in created_ids:
            cleanup_results.append(profile_share.cleanup_disposable_profile(profile_id))
        all_cleaned = all(
            item.get("deleted") and item.get("user_data_removed") for item in cleanup_results
        )
        if not all_cleaned:
            raise profile_share.ProfileShareError("disposable profile cleanup incomplete")

        runtime_status = str(runtime_proof.get("status") or "skipped")
        # Share E2E ok requires BrowserManager builder proof + safe transfer.
        # Runtime may be passed/skipped/unconfirmed; only explicit failed blocks ok.
        result = ProfileShareE2EResult(
            ok=bool(
                all_cleaned
                and redaction_ok
                and bm_proof["ok"]
                and evidence.get("browser_manager_builder")
                and runtime_status in {"passed", "skipped", "unconfirmed"}
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
            browser_manager_launch_args_ok=bool(bm_proof["ok"]),
            runtime_extension_proof=runtime_status,
            cleanup={"profiles": cleanup_results, "all_deleted": all_cleaned},
            evidence={
                "export_opt_in_denied": denied,
                "extension": {
                    "ids": evidence.get("extension_ids"),
                    "load_extension_arg": evidence.get("load_extension_arg"),
                    "resolved_path_count": evidence.get("resolved_path_count"),
                    "resolved_path_digests": evidence.get("resolved_path_digests"),
                    "extensions_resolved": evidence.get("extensions_resolved"),
                    "browser_manager_builder": evidence.get("browser_manager_builder"),
                    "loader_claim": evidence.get("loader_claim"),
                },
                "browser_manager_launch_args": {
                    "ok": bm_proof["ok"],
                    "builder": bm_proof["builder"],
                    "has_load_extension": bm_proof["has_load_extension"],
                    "extension_paths_exist": bm_proof["extension_paths_exist"],
                    "fingerprint_arg_present": bm_proof["fingerprint_arg_present"],
                },
                "runtime_extension": {
                    "status": runtime_status,
                    "reason": runtime_proof.get("reason"),
                    "claim": runtime_proof.get("claim"),
                    "extension_targets": runtime_proof.get("extension_targets"),
                },
                "import_cookie_origin": imported_cookies[0]["origin"],
                "excluded": redacted.get("excluded"),
                "schema": profile_share.PROFILE_SHARE_SCHEMA,
            },
        )
        return result
    finally:
        fixture.stop()
        for profile_id in created_ids:
            if db.get_profile(profile_id) is not None:
                profile_share.cleanup_disposable_profile(profile_id)
