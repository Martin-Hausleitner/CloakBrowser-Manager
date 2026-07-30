"""E2E tests for disposable profile loader/share harness."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import database as db  # noqa: E402
from scripts.e2e.profile_share_loader import (  # noqa: E402
    FixtureOriginServer,
    run_profile_share_e2e,
)
from scripts.e2e import run_profile_share_e2e as cli  # noqa: E402


@pytest.fixture()
def e2e_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "profiles.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    catalog = tmp_path / "extension-catalog"
    catalog.mkdir()
    monkeypatch.setenv("EXTENSION_CATALOG_DIR", str(catalog))
    db.init_db()
    return tmp_path


def test_fixture_origin_server_sets_only_synthetic_cookie() -> None:
    server = FixtureOriginServer(port=0).start()
    try:
        import urllib.request

        with urllib.request.urlopen(server.origin + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
            headers = dict(response.headers.items())
        assert "cbm-profile-share-fixture-ok" in body
        set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        assert "cbm_synthetic_session=" in set_cookie
        assert "top-secret" not in set_cookie
        assert "password" not in set_cookie.lower()
        cookie = server.synthetic_cookie
        assert cookie["origin"] == server.origin
        assert cookie["domain"] == "127.0.0.1"
    finally:
        server.stop()


def test_run_profile_share_e2e_full_flow(e2e_db: Path) -> None:
    result = run_profile_share_e2e(
        work_dir=e2e_db,
        extension_catalog_dir=Path(os.environ["EXTENSION_CATALOG_DIR"]),
    )
    assert result.ok is True
    assert result.redaction_ok is True
    assert result.load_extension_arg is not None
    assert result.load_extension_arg.startswith("--load-extension=sha256:")
    assert result.extension_ids
    assert result.synthetic_cookie_names == ["cbm_synthetic_session"]
    assert result.evidence["import_cookie_origin"].startswith("http://127.0.0.1:")
    assert result.evidence["extension"]["extensions_resolved"] is True
    assert result.evidence["excluded"]["proxy_credentials"] is True
    assert result.evidence["excluded"]["passwords"] is True
    assert result.evidence["excluded"]["tokens"] is True
    assert result.evidence["excluded"]["passkeys"] is True
    assert result.evidence["excluded"]["real_cookies"] is True
    assert result.cleanup["all_deleted"] is True
    # No leftover profiles after cleanup.
    assert db.list_profiles() == []

    payload = result.to_dict()
    dumped = json.dumps(payload)
    assert "top-secret" not in dumped
    assert "alice:" not in dumped
    assert "REAL_COOKIE" not in dumped
    assert "REAL_PASSWORD" not in dumped
    assert "/tmp/" not in dumped
    assert "extension-catalog/" not in dumped


def test_cli_entrypoint_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    work = tmp_path / "cli-work"
    monkeypatch.delenv("EXTENSION_CATALOG_DIR", raising=False)
    code = cli.main(["--work-dir", str(work), "--keep-work-dir"])
    captured = capsys.readouterr()
    assert code == 0
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["schema"] == "cloakbrowser.profile-share-e2e-result.v1"
    assert "top-secret" not in captured.out
