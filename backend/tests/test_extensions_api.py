"""Tests for GET /api/profiles/{profile_id}/extensions endpoint."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend import database as db
from backend import extension_catalog


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_manager.db")
    db.init_db()
    with TestClient(app) as c:
        yield c


def test_get_profile_extensions_not_found(client: TestClient):
    resp = client.get("/api/profiles/non-existent-id/extensions")
    assert resp.status_code == 404


def test_get_profile_extensions_empty(client: TestClient):
    p = db.create_profile("NoExt")
    resp = client.get(f"/api/profiles/{p['id']}/extensions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == p["id"]
    assert data["extensions"] == []


def test_get_profile_extensions_valid(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ext_dir = tmp_path / "sample_ext"
    ext_dir.mkdir()
    manifest_data = {
        "name": "Sample Extension",
        "version": "2.0.0",
        "manifest_version": 3,
        "description": "Sample extension",
        "permissions": ["notifications"],
    }
    (ext_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    monkeypatch.setattr(
        extension_catalog,
        "list_catalog_extensions",
        lambda **_kwargs: [
            {
                "id": "sample-extension",
                "path": str(ext_dir),
                "icon_url": "https://example.invalid/sample.png",
                "store_url": "https://chromewebstore.google.com/detail/sample-extension",
            }
        ],
    )
    p = db.create_profile("WithExt", extension_ids=["sample-extension"])

    resp = client.get(f"/api/profiles/{p['id']}/extensions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == p["id"]
    assert len(data["extensions"]) == 1
    ext = data["extensions"][0]
    assert ext["id"] == "sample-extension"
    assert ext["name"] == "Sample Extension"
    assert ext["version"] == "2.0.0"
    assert ext["trust_state"] == "valid"
    assert ext["permissions"] == ["notifications"]
    assert ext["icon_url"] == "https://example.invalid/sample.png"
    assert ext["store_url"] == "https://chromewebstore.google.com/detail/sample-extension"
