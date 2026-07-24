"""Tests for GET /api/profiles/{profile_id}/extensions endpoint."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend import database as db


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


def test_get_profile_extensions_valid(client: TestClient, tmp_path: Path):
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

    p = db.create_profile(
        "WithExt",
        launch_args=[f"--load-extension={ext_dir}"],
    )

    resp = client.get(f"/api/profiles/{p['id']}/extensions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == p["id"]
    assert len(data["extensions"]) == 1
    ext = data["extensions"][0]
    assert ext["name"] == "Sample Extension"
    assert ext["version"] == "2.0.0"
    assert ext["trust_state"] == "valid"
    assert ext["permissions"] == ["notifications"]
