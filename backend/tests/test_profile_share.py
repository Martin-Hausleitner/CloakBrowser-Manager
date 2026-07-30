"""TDD tests for safe disposable profile share/export/import/clone.

Security contract:
- only synthetic cookies on local fixture origins
- never copy real cookies, passwords, tokens, passkeys, or proxy credentials
- export/import/clone require explicit opt-in
- extension IDs and safe launch args transfer; manager-owned flags do not
- disposable cleanup removes DB row and user_data_dir
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import database as db
from backend import profile_share


FIXTURE_ORIGIN = "http://127.0.0.1:18765"
SYNTHETIC_COOKIE = {
    "name": "cbm_synthetic_session",
    "value": "fixture-token-aabbccdd",
    "domain": "127.0.0.1",
    "path": "/",
    "secure": False,
    "httpOnly": False,
    "sameSite": "Lax",
    "origin": FIXTURE_ORIGIN,
}


@pytest.fixture()
def catalog_ext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install one catalog-owned extension path for launch-arg resolution."""
    ext_id = "fjcjfaeimhopmpnoemigapegahhjnbkl"
    ext_dir = tmp_path / "extension-catalog" / ext_id
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "CloakBrowser Profile Sync",
                "version": "1.0.0",
                "manifest_version": 3,
                "permissions": ["storage"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EXTENSION_CATALOG_DIR", str(tmp_path / "extension-catalog"))
    return ext_id


@pytest.fixture()
def source_profile(tmp_db: Path, catalog_ext: str) -> dict:
    profile = db.create_profile(
        name="share-source",
        fingerprint_seed=4242,
        platform="linux",
        extension_ids=[catalog_ext],
        launch_args=["--lang=en-US"],
        proxy="http://alice:top-secret@proxy.example:8080",
        notes="disposable e2e source",
    )
    # Simulate a Chromium user-data tree without reading real cookie DBs.
    user_data = Path(profile["user_data_dir"])
    default = user_data / "Default"
    default.mkdir(parents=True)
    (default / "Cookies").write_bytes(b"REAL_COOKIE_DB_MUST_NEVER_BE_READ")
    (default / "Login Data").write_bytes(b"REAL_PASSWORD_STORE")
    (default / "Web Data").write_bytes(b"REAL_AUTOFILL")
    return profile


def test_fixture_origin_binding_accepts_only_local_origins() -> None:
    assert profile_share.is_fixture_origin(FIXTURE_ORIGIN) is True
    assert profile_share.is_fixture_origin("http://localhost:9000") is True
    assert profile_share.is_fixture_origin("https://example.com") is False
    assert profile_share.is_fixture_origin("http://evil.example") is False
    assert profile_share.is_fixture_origin("http://127.0.0.1:18765/path") is False


def test_validate_synthetic_cookie_rejects_real_looking_secrets() -> None:
    ok = profile_share.validate_synthetic_cookie(SYNTHETIC_COOKIE)
    assert ok["name"] == "cbm_synthetic_session"
    assert ok["origin"] == FIXTURE_ORIGIN

    with pytest.raises(profile_share.ProfileShareError, match="fixture origin"):
        profile_share.validate_synthetic_cookie(
            {**SYNTHETIC_COOKIE, "origin": "https://accounts.google.com"}
        )

    with pytest.raises(profile_share.ProfileShareError, match="synthetic"):
        profile_share.validate_synthetic_cookie(
            {**SYNTHETIC_COOKIE, "name": "session", "value": "real-session"}
        )

    with pytest.raises(profile_share.ProfileShareError, match="forbidden"):
        profile_share.validate_synthetic_cookie(
            {
                **SYNTHETIC_COOKIE,
                "name": "cbm_synthetic_jwt",
                "value": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
            }
        )


def test_export_requires_opt_in(source_profile: dict) -> None:
    denied = profile_share.export_profile_share(source_profile, opt_in=False)
    assert denied["schema"] == profile_share.PROFILE_SHARE_SCHEMA
    assert denied["opt_in"] is False
    assert denied["metadata"] == {}
    assert denied["synthetic_cookies"] == []
    assert denied["excluded"]["proxy_credentials"] is True
    assert denied["excluded"]["passwords"] is True
    assert denied["excluded"]["tokens"] is True
    assert denied["excluded"]["passkeys"] is True
    assert denied["excluded"]["real_cookies"] is True

    # Even with cookies provided, opt_in=False must not transfer them.
    denied2 = profile_share.export_profile_share(
        source_profile,
        opt_in=False,
        synthetic_cookies=[SYNTHETIC_COOKIE],
    )
    assert denied2["synthetic_cookies"] == []


def test_export_opt_in_transfers_extension_ids_and_safe_launch_args(
    source_profile: dict, catalog_ext: str
) -> None:
    payload = profile_share.export_profile_share(
        source_profile,
        opt_in=True,
        synthetic_cookies=[SYNTHETIC_COOKIE],
    )
    assert payload["opt_in"] is True
    assert payload["metadata"]["extension_ids"] == [catalog_ext]
    assert payload["metadata"]["launch_args"] == ["--lang=en-US"]
    assert payload["metadata"]["fingerprint_seed"] == 4242
    assert payload["metadata"]["platform"] == "linux"
    assert "proxy" not in payload["metadata"]
    assert "proxy_url" not in payload["metadata"]
    assert payload["metadata"].get("proxy_display") is None
    assert payload["synthetic_cookies"] == [
        profile_share.validate_synthetic_cookie(SYNTHETIC_COOKIE)
    ]
    # Must never embed raw proxy credentials or Chromium store paths.
    dumped = json.dumps(payload)
    assert "top-secret" not in dumped
    assert "alice" not in dumped
    assert "Cookies" not in dumped
    assert "Login Data" not in dumped


def test_export_strips_manager_owned_launch_args(source_profile: dict) -> None:
    source_profile = {
        **source_profile,
        "launch_args": [
            "--lang=en-US",
            "--load-extension=/evil/path",
            "--remote-debugging-port=9222",
            "--proxy-server=http://u:p@host:1",
        ],
    }
    payload = profile_share.export_profile_share(source_profile, opt_in=True)
    assert payload["metadata"]["launch_args"] == ["--lang=en-US"]


def test_export_never_reads_chromium_cookie_or_password_stores(
    source_profile: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []
    real_open = Path.open

    def tracking_open(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        reads.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    profile_share.export_profile_share(
        source_profile,
        opt_in=True,
        synthetic_cookies=[SYNTHETIC_COOKIE],
    )
    sensitive = [path for path in reads if "Cookies" in path or "Login Data" in path]
    assert sensitive == []


def test_import_requires_opt_in(source_profile: dict, catalog_ext: str) -> None:
    payload = profile_share.export_profile_share(
        source_profile,
        opt_in=True,
        synthetic_cookies=[SYNTHETIC_COOKIE],
    )
    with pytest.raises(profile_share.ProfileShareError, match="opt-in"):
        profile_share.import_profile_share(payload, opt_in=False)

    created = profile_share.import_profile_share(payload, opt_in=True)
    assert created["id"] != source_profile["id"]
    assert created["extension_ids"] == [catalog_ext]
    assert created["launch_args"] == ["--lang=en-US"]
    assert created["fingerprint_seed"] == 4242
    assert created["proxy"] is None
    assert created["name"].startswith("share-import-")
    # Synthetic cookies land in a sidecar file, never Chromium Cookies DB.
    sidecar = Path(created["user_data_dir"]) / profile_share.SYNTHETIC_COOKIE_SIDECAR
    assert sidecar.is_file()
    cookies = json.loads(sidecar.read_text(encoding="utf-8"))
    assert cookies[0]["origin"] == FIXTURE_ORIGIN
    assert cookies[0]["name"] == "cbm_synthetic_session"
    # Chromium cookie DB must not be fabricated from real stores.
    assert not (Path(created["user_data_dir"]) / "Default" / "Cookies").exists()


def test_clone_profile_semantics(source_profile: dict, catalog_ext: str) -> None:
    with pytest.raises(profile_share.ProfileShareError, match="opt-in"):
        profile_share.clone_profile(source_profile["id"], opt_in=False)

    cloned = profile_share.clone_profile(
        source_profile["id"],
        opt_in=True,
        synthetic_cookies=[SYNTHETIC_COOKIE],
    )
    assert cloned["id"] != source_profile["id"]
    assert cloned["extension_ids"] == [catalog_ext]
    assert cloned["fingerprint_seed"] == 4242
    assert cloned["proxy"] is None
    assert "top-secret" not in json.dumps(cloned)


def test_extension_launch_evidence_includes_load_extension(
    source_profile: dict, catalog_ext: str
) -> None:
    evidence = profile_share.build_extension_launch_evidence(source_profile)
    assert evidence["extension_ids"] == [catalog_ext]
    assert evidence["extensions_resolved"] is True
    assert evidence["load_extension_arg"] is not None
    assert evidence["load_extension_arg"].startswith("--load-extension=sha256:")
    assert evidence["resolved_path_count"] == 1
    assert len(evidence["resolved_path_digests"]) == 1
    # Redacted evidence must not embed absolute host paths.
    dumped = json.dumps(evidence)
    assert "/tmp/" not in dumped
    assert "extension-catalog" not in dumped

    raw = profile_share.build_extension_launch_evidence(
        source_profile, include_raw_paths=True
    )
    assert any(catalog_ext in path for path in raw["resolved_paths"])
    assert raw["load_extension_arg_raw"].startswith("--load-extension=")

    args = evidence["launch_args"]
    assert any(arg.startswith("--load-extension=sha256:") for arg in args)
    assert not any(arg.startswith("--remote-debugging-port") for arg in args)
    assert "proxy" not in evidence
    assert evidence["redacted"] is True


def test_redact_share_payload_strips_secrets() -> None:
    dirty = {
        "schema": profile_share.PROFILE_SHARE_SCHEMA,
        "opt_in": True,
        "metadata": {
            "name": "x",
            "proxy": "http://alice:top-secret@proxy.example:8080",
            "password": "hunter2",
            "token": "sk-live-abc",
            "passkey": "pk-material",
            "extension_ids": ["abc"],
        },
        "synthetic_cookies": [SYNTHETIC_COOKIE],
        "extra_secret": "should-go",
    }
    clean = profile_share.redact_share_payload(dirty)
    dumped = json.dumps(clean)
    assert "top-secret" not in dumped
    assert "hunter2" not in dumped
    assert "sk-live-abc" not in dumped
    assert "pk-material" not in dumped
    assert "should-go" not in dumped
    assert clean["metadata"]["extension_ids"] == ["abc"]
    assert "proxy" not in clean["metadata"]


def test_cleanup_disposable_profile(source_profile: dict) -> None:
    user_data = Path(source_profile["user_data_dir"])
    assert user_data.exists()
    result = profile_share.cleanup_disposable_profile(source_profile["id"])
    assert result["deleted"] is True
    assert result["user_data_removed"] is True
    assert db.get_profile(source_profile["id"]) is None
    assert not user_data.exists()


def test_reject_cookie_domain_mismatch() -> None:
    with pytest.raises(profile_share.ProfileShareError, match="domain"):
        profile_share.validate_synthetic_cookie(
            {
                **SYNTHETIC_COOKIE,
                "domain": "example.com",
                "origin": FIXTURE_ORIGIN,
            }
        )
