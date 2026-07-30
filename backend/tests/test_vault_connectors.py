"""TDD tests for provider-neutral local vault connectors.

Agents and MCP surfaces must receive references, capabilities, and status only —
never raw secret values. Discovery is install-probe only; optional version probes
do not require real accounts.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from backend.vault_connectors import (
    FORBIDDEN_AGENT_OPERATIONS,
    LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
    SECRET_REFERENCE_PATTERN,
    ConnectorStatus,
    FakeVaultConnector,
    LocalVaultConnectorService,
    SecretReference,
    clear_discovery_cache,
    discover_local_vault_connectors,
    public_connector_payload,
    validate_absolute_executable,
)
from backend.vault_connectors import discovery as discovery_mod
from backend.vault_connectors.contract import (
    PasskeyHandoffMode,
    assert_agent_safe_payload,
    is_valid_secret_ref,
)


@pytest.fixture(autouse=True)
def _reset_vault_discovery_cache():
    clear_discovery_cache()
    yield
    clear_discovery_cache()


def test_contract_version_is_stable():
    assert LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION == "local-vault-connector-v1"


def test_reveal_is_forbidden_on_agent_surface():
    assert "reveal" in FORBIDDEN_AGENT_OPERATIONS
    assert "export_raw" in FORBIDDEN_AGENT_OPERATIONS
    assert "authorize_use" not in FORBIDDEN_AGENT_OPERATIONS


def test_secret_ref_pattern_rejects_raw_looking_values():
    assert is_valid_secret_ref("secretref-login-example-com-1")
    assert not is_valid_secret_ref("password=hunter2")
    assert not is_valid_secret_ref("Bearer abc.def.ghi")
    assert SECRET_REFERENCE_PATTERN.fullmatch("secretref-a1") is None  # too short after prefix rules


def test_discover_reports_not_installed_when_binaries_missing(monkeypatch):
    monkeypatch.setattr(
        "backend.vault_connectors.discovery.which",
        lambda name: None,
    )
    results = discover_local_vault_connectors(run_probes=False)
    by_id = {item.provider_id: item for item in results}
    assert set(by_id) >= {
        "bitwarden-cli",
        "vaultwarden",
        "keepassxc",
        "gopass",
    }
    for provider_id in ("bitwarden-cli", "vaultwarden", "keepassxc", "gopass"):
        status = by_id[provider_id]
        assert status.installed is False
        assert status.status == ConnectorStatus.NOT_INSTALLED
        assert status.reason_code == "binary_not_found"
        payload = public_connector_payload(status)
        assert_agent_safe_payload(payload)
        assert "password" not in json.dumps(payload).lower() or payload.get("capabilities")


def test_discover_marks_bitwarden_cli_installed_without_account(tmp_path, monkeypatch):
    fake_bw = tmp_path / "bw"
    fake_bw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bw.chmod(0o755)

    def fake_which(name: str) -> str | None:
        if name == "bw":
            return str(fake_bw)
        return None

    monkeypatch.setattr("backend.vault_connectors.discovery.which", fake_which)
    results = discover_local_vault_connectors(run_probes=False)
    bw = next(item for item in results if item.provider_id == "bitwarden-cli")
    assert bw.installed is True
    assert bw.status == ConnectorStatus.INSTALLED
    assert bw.reason_code == "installed_unauthenticated"
    assert "list_references" in bw.capabilities
    assert "reveal" not in bw.capabilities
    assert bw.passkey_mode == PasskeyHandoffMode.USER_PRESENCE_HANDOFF


def test_optional_probe_records_version_without_secrets(tmp_path, monkeypatch):
    fake_bw = tmp_path / "bw"
    fake_bw.write_text("#!/bin/sh\necho '2026.2.0'\n", encoding="utf-8")
    fake_bw.chmod(0o755)

    monkeypatch.setattr(
        "backend.vault_connectors.discovery.which",
        lambda name: str(fake_bw) if name == "bw" else None,
    )
    results = discover_local_vault_connectors(run_probes=True)
    bw = next(item for item in results if item.provider_id == "bitwarden-cli")
    assert bw.installed is True
    assert bw.probe_version == "2026.2.0"
    assert bw.status in {ConnectorStatus.INSTALLED, ConnectorStatus.READY}
    payload = public_connector_payload(bw)
    assert_agent_safe_payload(payload)
    blob = json.dumps(payload)
    assert "password" not in blob.lower() or "capabilities" in blob
    assert "session" not in blob.lower() or "reason_code" in blob


def test_keepassxc_and_gopass_discovery_names(tmp_path, monkeypatch):
    keepass = tmp_path / "keepassxc-cli"
    gopass = tmp_path / "gopass"
    keepass.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gopass.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    keepass.chmod(0o755)
    gopass.chmod(0o755)

    def fake_which(name: str) -> str | None:
        mapping = {
            "keepassxc-cli": str(keepass),
            "keepassxc": None,
            "gopass": str(gopass),
        }
        return mapping.get(name)

    monkeypatch.setattr("backend.vault_connectors.discovery.which", fake_which)
    results = discover_local_vault_connectors(run_probes=False)
    by_id = {item.provider_id: item for item in results}
    assert by_id["keepassxc"].installed is True
    assert by_id["gopass"].installed is True
    assert by_id["keepassxc"].passkey_mode == PasskeyHandoffMode.USER_PRESENCE_HANDOFF
    assert by_id["gopass"].passkey_mode == PasskeyHandoffMode.UNSUPPORTED


def test_fake_provider_e2e_lists_refs_never_values():
    fake = FakeVaultConnector()
    health = fake.health()
    assert health.status == ConnectorStatus.READY
    assert health.provider_id == "fake"

    refs = fake.list_references()
    assert refs
    for ref in refs:
        assert isinstance(ref, SecretReference)
        assert is_valid_secret_ref(ref.secret_ref)
        public = ref.public_payload()
        assert_agent_safe_payload(public)
        assert "value" not in public
        assert "raw_value" not in public
        assert "password" not in public  # classification uses kind only
        blob = json.dumps(public)
        assert "FAKE_NOT_FOR_AGENTS" not in blob
        assert "FAKE_TOTP_SEED" not in blob
        if ref.kind.value == "passkey":
            assert ref.passkey_handoff is not None
            assert ref.passkey_handoff.mode == PasskeyHandoffMode.USER_PRESENCE_HANDOFF
            assert ref.passkey_handoff.required is True
            assert ref.raw_value is None

    # Agent surface must not expose a reveal path.
    assert not hasattr(fake, "reveal")
    assert "reveal" not in fake.agent_operations()


def test_service_aggregates_discovery_and_fake_for_agents(monkeypatch):
    monkeypatch.setattr(
        "backend.vault_connectors.discovery.which",
        lambda name: None,
    )
    service = LocalVaultConnectorService(include_fake=True, run_probes=False)
    snapshot = service.agent_snapshot()
    assert snapshot["contract_version"] == LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION
    assert snapshot["reveal_available"] is False
    assert "reveal" in snapshot["forbidden_operations"]
    connectors = {item["provider_id"]: item for item in snapshot["connectors"]}
    assert "fake" in connectors
    assert connectors["fake"]["status"] == "ready"
    assert "secret_references" in snapshot
    for ref in snapshot["secret_references"]:
        assert is_valid_secret_ref(ref["secret_ref"])
        assert "value" not in ref
        if ref.get("kind") == "passkey":
            assert ref["passkey_handoff"]["mode"] == "user_presence_handoff"
    assert_agent_safe_payload(snapshot)


def test_assert_agent_safe_payload_rejects_secret_like_fields():
    with pytest.raises(ValueError, match="forbidden"):
        assert_agent_safe_payload({"password": "nope"})
    with pytest.raises(ValueError, match="forbidden"):
        assert_agent_safe_payload({"items": [{"token": "abc"}]})
    with pytest.raises(ValueError, match="forbidden"):
        assert_agent_safe_payload({"note": "password=hunter2"})


@pytest.fixture()
def client_access(tmp_db, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock

    from starlette.testclient import TestClient

    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client


def test_control_plane_capabilities_include_vault_connector_discovery():
    from backend.models import control_plane_capabilities_payload

    payload = control_plane_capabilities_payload(local_mac_available=False)
    secret = payload["resources"]["secret-references"]
    assert secret["available"]["rest"] is True
    assert secret["available"]["mcp"] is True
    assert secret["reveal"] is False
    assert secret["secrets"] == "reference-only"
    assert "discover" in secret["rest_operations"]
    assert "status" in secret["rest_operations"]
    assert "reveal" not in secret["rest_operations"]
    assert secret["contract"] == LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION
    assert "connectors" in secret
    assert isinstance(secret["connectors"], list)
    forbidden = " ".join(payload["forbidden"]).lower()
    assert "vault reveal" in forbidden


def test_api_vault_connectors_returns_status_only(client_access):
    headers = {"Authorization": "Bearer bootstrap-test-secret"}
    response = client_access.get("/api/v2/vault-connectors", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "LocalVaultConnectorSet"
    assert body["spec"]["reveal_available"] is False
    assert body["spec"]["contract_version"] == LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION
    blob = json.dumps(body)
    for banned in ("hunter2", "sk-live-", "BEGIN PRIVATE", "otpauth://"):
        assert banned not in blob
    assert_agent_safe_payload(body["spec"])


def test_json_contract_file_exists_and_matches_version():
    root = Path(__file__).resolve().parents[2]
    contract_path = root / "docs" / "contracts" / "local-vault-connector-v1.json"
    assert contract_path.is_file()
    data = json.loads(contract_path.read_text(encoding="utf-8"))
    assert data["metadata"]["id"] == "local-vault-connector-v1"
    assert "reveal" in " ".join(data["spec"]["forbidden_operations"]).lower() or any(
        op == "reveal" for op in data["spec"]["forbidden_operations"]
    )
    assert "bitwarden-cli" in data["spec"]["providers"]
    assert "keepassxc" in data["spec"]["providers"]
    assert "gopass" in data["spec"]["providers"]
    assert data["spec"]["passkey_policy"]["default_mode"] == "user_presence_handoff"


def test_validate_absolute_executable_rejects_relative_and_non_files(tmp_path):
    assert validate_absolute_executable(None) is None
    assert validate_absolute_executable("bw") is None
    assert validate_absolute_executable(str(tmp_path / "missing")) is None
    not_exec = tmp_path / "not-exec"
    not_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    assert validate_absolute_executable(str(not_exec)) is None
    ok = tmp_path / "ok-bin"
    ok.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ok.chmod(0o755)
    resolved = validate_absolute_executable(str(ok))
    assert resolved is not None
    assert Path(resolved).is_absolute()
    assert Path(resolved).is_file()


def test_discovery_cache_avoids_repeat_which_and_probes(tmp_path, monkeypatch):
    fake_bw = tmp_path / "bw"
    fake_bw.write_text("#!/bin/sh\necho '1.2.3'\n", encoding="utf-8")
    fake_bw.chmod(0o755)

    which_calls: list[str] = []
    probe_calls: list[str] = []

    def counting_which(name: str) -> str | None:
        which_calls.append(name)
        if name == "bw":
            return str(fake_bw)
        return None

    def counting_probe(executable: str) -> tuple[str | None, str]:
        probe_calls.append(executable)
        assert Path(executable).is_absolute()
        return "1.2.3", "probe_ok"

    monkeypatch.setattr(discovery_mod, "which", counting_which)
    monkeypatch.setattr(discovery_mod, "_run_version_probe", counting_probe)

    first = discover_local_vault_connectors(run_probes=True)
    second = discover_local_vault_connectors(run_probes=True)
    assert first == second
    assert which_calls  # first discovery resolved PATH
    first_which = list(which_calls)
    first_probes = list(probe_calls)
    assert first_probes  # probe ran once for installed bw
    # Second call must be served from cache — no additional which/probe work.
    assert which_calls == first_which
    assert probe_calls == first_probes

    clear_discovery_cache()
    discover_local_vault_connectors(run_probes=True)
    assert len(which_calls) > len(first_which)
    assert len(probe_calls) > len(first_probes)


def test_discovery_probes_use_validated_absolute_path(tmp_path, monkeypatch):
    fake_bw = tmp_path / "bw"
    fake_bw.write_text("#!/bin/sh\necho '9.9.9'\n", encoding="utf-8")
    fake_bw.chmod(0o755)
    seen: list[str] = []

    monkeypatch.setattr(
        discovery_mod,
        "which",
        lambda name: str(fake_bw) if name == "bw" else None,
    )

    real_probe = discovery_mod._run_version_probe

    def wrapping_probe(executable: str) -> tuple[str | None, str]:
        seen.append(executable)
        return real_probe(executable)

    monkeypatch.setattr(discovery_mod, "_run_version_probe", wrapping_probe)
    results = discover_local_vault_connectors(run_probes=True, use_cache=False)
    bw = next(item for item in results if item.provider_id == "bitwarden-cli")
    assert bw.probe_version == "9.9.9"
    assert seen
    assert all(Path(path).is_absolute() for path in seen)
    assert all(Path(path).name == "bw" for path in seen)


@pytest.mark.asyncio
async def test_agent_snapshot_async_keeps_event_loop_responsive(monkeypatch):
    """Slow discovery must not monopolize the event loop when offloaded."""
    clear_discovery_cache()
    started = time.perf_counter()
    ticks: list[float] = []

    def slow_snapshot(self) -> dict:
        time.sleep(0.15)
        return {
            "contract_version": LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
            "reveal_available": False,
            "forbidden_operations": sorted(FORBIDDEN_AGENT_OPERATIONS),
            "connectors": [],
            "secret_references": [],
            "passkey_policy": {"default_mode": "user_presence_handoff", "note": "test"},
        }

    monkeypatch.setattr(LocalVaultConnectorService, "agent_snapshot", slow_snapshot)
    service = LocalVaultConnectorService(include_fake=False, run_probes=False)

    async def ticker() -> None:
        for _ in range(3):
            await asyncio.sleep(0.02)
            ticks.append(time.perf_counter() - started)

    snap_task = asyncio.create_task(service.agent_snapshot_async())
    tick_task = asyncio.create_task(ticker())
    snapshot, _ = await asyncio.gather(snap_task, tick_task)
    assert snapshot["reveal_available"] is False
    # Ticks should complete while the slow snapshot is still running off-thread.
    assert ticks, "event loop did not process concurrent awaits during snapshot"
    assert ticks[0] < 0.15


def test_api_vault_connectors_offloads_snapshot(client_access, monkeypatch):
    headers = {"Authorization": "Bearer bootstrap-test-secret"}
    called: list[str] = []

    async def tracking_snapshot(self):
        called.append("async")
        return {
            "contract_version": LOCAL_VAULT_CONNECTOR_CONTRACT_VERSION,
            "reveal_available": False,
            "forbidden_operations": sorted(FORBIDDEN_AGENT_OPERATIONS),
            "connectors": [],
            "secret_references": [],
            "passkey_policy": {
                "default_mode": "user_presence_handoff",
                "note": "Device-bound passkeys require human user-presence handoff; no export.",
            },
        }

    monkeypatch.setattr(
        LocalVaultConnectorService,
        "agent_snapshot_async",
        tracking_snapshot,
    )
    response = client_access.get("/api/v2/vault-connectors", headers=headers)
    assert response.status_code == 200, response.text
    assert called == ["async"]
    assert response.json()["spec"]["reveal_available"] is False
