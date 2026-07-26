"""Focused tests for Comet extension defaults and profile templates."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def admin_client(app_client, monkeypatch: pytest.MonkeyPatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "test-admin-token-0123456789abcdef")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    app_client.cookies.set("auth_token", "test-admin-token-0123456789abcdef")
    return app_client


def test_extension_defaults_lists_comet_catalog(admin_client, tmp_path, monkeypatch):
    from backend import extension_catalog
    from backend import main

    monkeypatch.setattr(extension_catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(extension_catalog, "defaults_path", lambda: tmp_path / "extension-defaults.json")
    monkeypatch.setattr(extension_catalog, "catalog_dir", lambda: tmp_path / "missing-catalog")

    response = admin_client.get("/api/extension/defaults")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "comet"
    assert payload["count"] >= 3
    assert payload["extensions"]
    assert payload["items"]
    assert all("password" not in json.dumps(item).lower() for item in payload["extensions"])
    assert any(item["id"] == "ddkjiahejlhfcafbddmgiahcphecmpfh" for item in payload["extensions"])
    assert "defaults" in main.session_links.catalog_endpoint_map()


def test_extension_defaults_put_persists_selection(admin_client, tmp_path, monkeypatch):
    from backend import extension_catalog

    monkeypatch.setattr(extension_catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(extension_catalog, "defaults_path", lambda: tmp_path / "extension-defaults.json")
    monkeypatch.setattr(extension_catalog, "catalog_dir", lambda: tmp_path / "missing-catalog")

    listed = admin_client.get("/api/extension/defaults").json()
    chosen = [listed["extensions"][0]["id"], listed["extensions"][1]["id"]]
    response = admin_client.put("/api/extension/defaults", json={"selected_ids": chosen})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selected_ids"] == chosen
    assert {item["id"] for item in payload["extensions"] if item["selected"]} == set(chosen)
    assert json.loads((tmp_path / "extension-defaults.json").read_text())["selected_ids"] == chosen


def test_catalog_does_not_resolve_symlinked_extension_outside_catalog(tmp_path, monkeypatch):
    from backend import extension_catalog

    catalog_root = tmp_path / "extension-catalog"
    outside_extension = tmp_path / "outside-extension"
    outside_extension.mkdir()
    (outside_extension / "manifest.json").write_text("{}", encoding="utf-8")
    catalog_root.mkdir()
    (catalog_root / "catalog-extension").symlink_to(outside_extension, target_is_directory=True)
    config_path = tmp_path / "extension-catalog.json"
    config_path.write_text(
        json.dumps({"extensions": [{"id": "catalog-extension", "name": "Catalog extension"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(extension_catalog, "catalog_dir", lambda: catalog_root)
    monkeypatch.setenv("EXTENSION_CATALOG_CONFIG", str(config_path))

    item = extension_catalog.list_catalog_extensions()[0]
    assert item["available"] is False
    assert item["path"] is None


def test_profile_templates_create_applies_defaults(admin_client, tmp_path, monkeypatch):
    from backend import extension_catalog
    from backend import profile_templates

    catalog_root = tmp_path / "extension-catalog" / "ddkjiahejlhfcafbddmgiahcphecmpfh" / "1.0_0"
    catalog_root.mkdir(parents=True)
    (catalog_root / "manifest.json").write_text(
        json.dumps({"name": "uBlock Origin Lite", "version": "1.0", "manifest_version": 3}),
        encoding="utf-8",
    )
    monkeypatch.setattr(extension_catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(extension_catalog, "defaults_path", lambda: tmp_path / "extension-defaults.json")
    monkeypatch.setattr(extension_catalog, "catalog_dir", lambda: tmp_path / "extension-catalog")
    extension_catalog.save_selected_ids(["ddkjiahejlhfcafbddmgiahcphecmpfh"])

    listed = admin_client.get("/api/profile-templates")
    assert listed.status_code == 200
    assert any(item["id"] == "generate-new" for item in listed.json())

    created = admin_client.post(
        "/api/profile-templates/generate-new/profiles",
        json={"template_id": "generate-new", "name": "Demo Generated"},
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["name"] == "Demo Generated"
    assert profile.get("extension_ids") == ["ddkjiahejlhfcafbddmgiahcphecmpfh"]
    assert profile.get("launch_args") == []
    fields = profile_templates.build_profile_fields("codex-operator")
    assert fields["harness"] == "codex"
    assert "System prompt" in (fields.get("notes") or "")
