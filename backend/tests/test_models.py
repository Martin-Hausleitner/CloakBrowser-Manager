"""Tests for Pydantic models — validation, defaults, constraints."""

import pytest
from pydantic import ValidationError

from backend.models import (
    ClipboardRequest,
    LaunchResponse,
    ProfileCreate,
    ProfileHealthResponse,
    ProfileResponse,
    ProfileStatusResponse,
    ProfileUpdate,
    TagCreate,
    TaskRunCreate,
    WorkerAcpxPreflightRequest,
    WorkerProviderPreflightRequest,
    acp_agent_for_provider,
)


# ── ProfileHealthResponse ───────────────────────────────────────────────────


def test_profile_health_response_defaults_are_explicitly_unavailable():
    health = ProfileHealthResponse(profile_id="profile-1")

    assert health.state == "unavailable"
    assert health.checked_at is None
    assert health.proxy_configured is False
    assert health.proxy_reachable is None
    assert health.outbound_ip_masked is None
    assert health.proxy_latency_ms is None
    assert health.proxy_risk_score is None
    assert health.proxy_authenticity_score is None
    assert health.fingerprint_consistency_score is None
    assert health.browser_scan_score is None
    assert health.warnings == []
    assert health.blockers == []
    assert health.error_code is None
    assert health.sources == {}


@pytest.mark.parametrize(
    "state",
    ["pending", "running", "passed", "warning", "failed", "unavailable"],
)
def test_profile_health_response_accepts_documented_states(state: str):
    health = ProfileHealthResponse(profile_id="profile-1", state=state)

    assert health.state == state


def test_profile_health_response_rejects_unknown_state():
    with pytest.raises(ValidationError):
        ProfileHealthResponse(profile_id="profile-1", state="healthy")


def test_profile_health_response_rejects_out_of_range_scores():
    with pytest.raises(ValidationError):
        ProfileHealthResponse(profile_id="profile-1", proxy_risk_score=101)


def test_profile_health_response_rejects_unknown_source_state():
    with pytest.raises(ValidationError):
        ProfileHealthResponse(profile_id="profile-1", sources={"browser_scan": "trusted"})


@pytest.mark.parametrize(
    ("ready", "reason_code"),
    [
        (True, "auth_required"),
        (False, "ok"),
    ],
)
def test_acpx_preflight_requires_consistent_ready_reason(ready: bool, reason_code: str):
    with pytest.raises(ValidationError):
        WorkerAcpxPreflightRequest(
            agent="cursor",
            ready=ready,
            reason_code=reason_code,
        )


def test_provider_preflight_accepts_only_exact_public_ready_contract():
    ready = WorkerProviderPreflightRequest(
        provider="grok",
        transport="openai-compatible",
        ready=True,
        reason_code="ready",
        model_aliases=[
            "grok-build-0.1",
            "grok-build-0.1",
            "https://secret.local",
            "x" * 160,
        ],
    )

    assert ready.ready is True
    assert ready.reason_code == "ready"
    assert ready.model_aliases == ["grok-build-0.1", "x" * 96]


def test_provider_preflight_accepts_safe_dynamic_acp_provider_id():
    ready = WorkerProviderPreflightRequest(
        provider="gemini",
        transport="acp",
        ready=True,
        reason_code="ready",
        model_aliases=["gemini-2.5-pro", "https://secret.local"],
    )

    assert ready.provider == "gemini"
    assert ready.transport == "acp"
    assert ready.model_aliases == ["gemini-2.5-pro"]


@pytest.mark.parametrize(
    "provider",
    ["Gemini", "gemini/../../token", "-gemini", "g" * 65, "gemini token"],
)
def test_provider_preflight_rejects_malicious_dynamic_provider_ids(provider: str):
    with pytest.raises(ValidationError):
        WorkerProviderPreflightRequest(
            provider=provider,
            transport="acp",
            ready=False,
            reason_code="protocol_unavailable",
        )


@pytest.mark.parametrize("transport", ["cli", "openai-compatible"])
def test_provider_preflight_rejects_arbitrary_dynamic_direct_targets(transport: str):
    with pytest.raises(ValidationError):
        WorkerProviderPreflightRequest(
            provider="gemini",
            transport=transport,
            ready=False,
            reason_code="protocol_unavailable",
        )


@pytest.mark.parametrize(
    ("provider", "agent"),
    [
        ("codex", "codex"),
        ("claude", "claude"),
        ("cursor", "cursor"),
        ("grok", "grok-build"),
        ("opencode", "opencode"),
    ],
)
def test_acp_provider_mapping_is_exact_for_built_in_registry(
    provider: str, agent: str
):
    assert acp_agent_for_provider(provider) == agent


def test_acp_provider_mapping_does_not_include_antigravity_or_unknowns():
    assert acp_agent_for_provider("antigravity") is None
    assert acp_agent_for_provider("shell") == "shell"
    assert acp_agent_for_provider("Gemini") is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "provider": "grok",
            "transport": "openai-compatible",
            "ready": True,
            "reason_code": "auth_required",
        },
        {
            "provider": "grok",
            "transport": "openai-compatible",
            "ready": False,
            "reason_code": "ready",
        },
        {
            "provider": "antigravity",
            "transport": "acp",
            "ready": False,
            "reason_code": "protocol_unavailable",
        },
        {
            "provider": "codex",
            "transport": "cli",
            "ready": False,
            "reason_code": "protocol_unavailable",
        },
        {
            "provider": "grok",
            "transport": "cli",
            "ready": False,
            "reason_code": "protocol_unavailable",
            "raw_error": "Bearer cbm_worker_secret failed",
        },
    ],
)
def test_provider_preflight_rejects_invalid_contracts(payload: dict[str, object]):
    with pytest.raises(ValidationError):
        WorkerProviderPreflightRequest(**payload)


# ── ProfileCreate ────────────────────────────────────────────────────────────


def test_profile_create_minimal():
    p = ProfileCreate(name="Test")
    assert p.name == "Test"
    assert p.project_id == "default"
    assert p.folder_path == ""
    assert p.pinned is False
    assert p.accent_color is None
    assert p.harness == "codex"
    assert p.fingerprint_seed is None
    assert p.platform == "windows"
    assert p.screen_width == 1920
    assert p.screen_height == 1080
    assert p.humanize is False
    assert p.headless is False
    assert p.geoip is False
    assert p.human_preset == "default"


def test_profile_create_all_fields():
    p = ProfileCreate(
        name="Full",
        fingerprint_seed=42,
        proxy="http://host:8080",
        timezone="America/New_York",
        locale="en-US",
        platform="macos",
        user_agent="Mozilla/5.0",
        screen_width=2560,
        screen_height=1440,
        gpu_vendor="NVIDIA",
        gpu_renderer="RTX 3070",
        hardware_concurrency=16,
        humanize=True,
        human_preset="careful",
        headless=True,
        geoip=True,
        color_scheme="dark",
        search_engine="google",
        notes="test note",
        tags=[TagCreate(tag="work", color="#ff0000")],
    )
    assert p.platform == "macos"
    assert p.human_preset == "careful"
    assert p.color_scheme == "dark"
    assert p.search_engine == "google"
    assert len(p.tags) == 1


def test_profile_create_launch_args_default():
    p = ProfileCreate(name="Test")
    assert p.launch_args == []


def test_profile_create_rejects_untrusted_extension_launch_arg():
    with pytest.raises(ValidationError, match="manager-owned"):
        ProfileCreate(name="Test", launch_args=["--load-extension=/tmp/ext"])


@pytest.mark.parametrize(
    "launch_arg",
    [
        "--remote-debugging-port=9222",
        "--remote-debugging-address=0.0.0.0",
        "--remote-debugging-pipe",
        "--user-data-dir=/tmp/unmanaged-profile",
        "--proxy-server=http://proxy.invalid:8080",
        "--proxy-pac-url=https://proxy.invalid/config.pac",
        "--no-proxy-server",
        "--proxy-bypass-list=*",
        "--proxy-auto-detect",
        "--disable-web-security",
        "--no-sandbox",
    ],
)
@pytest.mark.parametrize("profile_model", [ProfileCreate, ProfileUpdate])
def test_profile_models_reject_manager_owned_launch_args(
    launch_arg: str, profile_model: type[ProfileCreate] | type[ProfileUpdate]
):
    """Profiles cannot override runtime, network, or browser-security ownership."""
    fields = {"launch_args": [launch_arg]}
    if profile_model is ProfileCreate:
        fields["name"] = "Unsafe"
    with pytest.raises(ValidationError, match="manager-owned"):
        profile_model(**fields)


def test_profile_create_organization_fields():
    p = ProfileCreate(
        name="Organized",
        project_id="client.alpha_1",
        folder_path="research/phase-1",
        pinned=True,
        accent_color="#1A2B3C",
        harness="opencode",
    )

    assert p.project_id == "client.alpha_1"
    assert p.folder_path == "research/phase-1"
    assert p.pinned is True
    assert p.accent_color == "#1A2B3C"
    assert p.harness == "opencode"


def test_profile_update_launch_args():
    p = ProfileUpdate(launch_args=["--flag"])
    dumped = p.model_dump(exclude_unset=True)
    assert dumped == {"launch_args": ["--flag"]}


def test_profile_create_invalid_platform():
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", platform="android")


def test_profile_create_invalid_human_preset():
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", human_preset="fast")


def test_profile_create_invalid_color_scheme():
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", color_scheme="auto")

def test_profile_create_invalid_search_engine():
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", search_engine="yahoo")


@pytest.mark.parametrize("project_id", ["", "-bad", "bad space", "a" * 81])
def test_profile_create_invalid_project_id(project_id: str):
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", project_id=project_id)


@pytest.mark.parametrize(
    "folder_path",
    ["/leading", "trailing/", "two//segments", ".", "..", "safe/../unsafe", "a" * 241],
)
def test_profile_create_invalid_folder_path(folder_path: str):
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", folder_path=folder_path)


@pytest.mark.parametrize("accent_color", ["#abc", "112233", "#GG0011", "#11223344"])
def test_profile_create_invalid_accent_color(accent_color: str):
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", accent_color=accent_color)


def test_profile_create_invalid_harness():
    with pytest.raises(ValidationError):
        ProfileCreate(name="Bad", harness="selenium")


@pytest.mark.parametrize(
    "harness",
    ["browser-use", "browser-harness", "unbrowse", "stagehand", "codex", "acpx"],
)
def test_profile_create_accepts_callable_browser_harnesses(harness: str):
    profile = ProfileCreate(name="Harness", harness=harness)
    assert profile.harness == harness


@pytest.mark.parametrize("agent", ["codex", "claude", "cursor", "grok-build", "opencode"])
def test_acpx_task_run_requires_supported_agent(agent: str):
    run = TaskRunCreate(
        harness="acpx",
        agent=agent,
        task="Inspect the managed browser",
        profile_id="profile-1",
    )
    assert run.agent == agent


def test_acpx_task_run_rejects_missing_or_unrelated_agent():
    with pytest.raises(ValidationError, match="agent is required"):
        TaskRunCreate(harness="acpx", task="Inspect", profile_id="profile-1")
    with pytest.raises(ValidationError, match="only valid for acpx"):
        TaskRunCreate(
            harness="browser-use",
            agent="codex",
            task="Inspect",
            profile_id="profile-1",
        )


def test_task_run_routing_contract_defaults_policy_when_present():
    run = TaskRunCreate(
        harness="acpx",
        agent="grok-build",
        task="Inspect",
        profile_id="profile-1",
        provider={"id": "grok", "transport": "acp"},
        browser_tools=[
            {"id": "unbrowse"},
            {"id": "stagehand"},
            {"id": "browser-harness"},
        ],
    )

    assert run.provider.model_dump(exclude_none=True) == {"id": "grok", "transport": "acp"}
    assert [tool.model_dump() for tool in run.browser_tools] == [
        {"id": "unbrowse", "enabled": True},
        {"id": "stagehand", "enabled": True},
        {"id": "browser-harness", "enabled": True},
    ]
    assert run.routing_policy.model_dump() == {
        "mode": "ordered-fallback",
        "allow_second_browser": False,
        "max_tool_attempts": 3,
    }


@pytest.mark.parametrize(
    ("provider", "agent"),
    [
        ("codex", "codex"),
        ("claude", "claude"),
        ("cursor", "cursor"),
        ("grok", "grok-build"),
        ("opencode", "opencode"),
    ],
)
def test_task_run_routing_contract_accepts_exact_acp_provider_agent_mapping(
    provider: str, agent: str
):
    run = TaskRunCreate(
        harness="acpx",
        agent=agent,
        task="Inspect",
        profile_id="profile-1",
        provider={"id": provider, "transport": "acp"},
        browser_tools=[
            {"id": "unbrowse"},
            {"id": "stagehand"},
            {"id": "browser-harness"},
        ],
    )

    assert run.provider is not None
    assert run.provider.id == provider
    assert run.agent == agent


def test_task_run_routing_contract_accepts_dynamic_acp_provider_agent_mapping():
    run = TaskRunCreate(
        harness="acpx",
        agent="gemini",
        task="Inspect",
        profile_id="profile-1",
        provider={"id": "gemini", "transport": "acp"},
        browser_tools=[
            {"id": "unbrowse"},
            {"id": "stagehand"},
            {"id": "browser-harness"},
        ],
    )

    assert run.provider is not None
    assert run.provider.id == "gemini"
    assert run.agent == "gemini"


def test_task_run_routing_contract_rejects_dynamic_acp_provider_agent_mismatch():
    with pytest.raises(ValidationError, match="provider and agent to match"):
        TaskRunCreate(
            harness="acpx",
            agent="codex",
            task="Inspect",
            profile_id="profile-1",
            provider={"id": "gemini", "transport": "acp"},
            browser_tools=[
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        )


@pytest.mark.parametrize(
    ("provider", "agent"),
    [
        ("codex", "grok-build"),
        ("claude", "codex"),
        ("cursor", "claude"),
        ("grok", "cursor"),
        ("opencode", "grok-build"),
    ],
)
def test_task_run_routing_contract_rejects_mismatched_acp_provider_agent_mapping(
    provider: str, agent: str
):
    with pytest.raises(ValidationError, match="provider and agent to match"):
        TaskRunCreate(
            harness="acpx",
            agent=agent,
            task="Inspect",
            profile_id="profile-1",
            provider={"id": provider, "transport": "acp"},
            browser_tools=[
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        )


def test_task_run_routing_contract_allows_legacy_acpx_without_new_fields():
    run = TaskRunCreate(
        harness="acpx",
        agent="grok-build",
        task="Inspect",
        profile_id="profile-1",
    )

    assert run.provider is None
    assert run.browser_tools == []
    assert run.routing_policy is None


def test_task_run_routing_contract_rejects_max_tool_attempts_out_of_range():
    for max_tool_attempts in (0, 4):
        with pytest.raises(ValidationError):
            TaskRunCreate(
                harness="acpx",
                agent="grok-build",
                task="Inspect",
                profile_id="profile-1",
                provider={"id": "grok", "transport": "acp"},
                browser_tools=[
                    {"id": "unbrowse"},
                    {"id": "stagehand"},
                    {"id": "browser-harness"},
                ],
                routing_policy={"max_tool_attempts": max_tool_attempts},
            )


def test_task_run_routing_contract_accepts_openai_compatible_grok_build_combo():
    run = TaskRunCreate(
        harness="acpx",
        agent="grok-build",
        task="Inspect",
        profile_id="profile-1",
        provider={
            "id": "grok",
            "transport": "openai-compatible",
            "model_alias": "grok-build-0.1",
        },
        browser_tools=[
            {"id": "unbrowse"},
            {"id": "stagehand"},
            {"id": "browser-harness"},
        ],
        routing_policy={"max_tool_attempts": 2},
    )

    assert run.provider.model_dump(exclude_none=True) == {
        "id": "grok",
        "transport": "openai-compatible",
        "model_alias": "grok-build-0.1",
    }
    assert run.routing_policy is not None
    assert run.routing_policy.max_tool_attempts == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"harness": "browser-use", "agent": None},
        {"agent": "codex"},
        {"provider": {"id": "antigravity", "transport": "openai-compatible"}},
        {"provider": {"id": "grok", "transport": "openai-compatible"}, "browser_tools": []},
    ],
)
def test_task_run_openai_compatible_rejects_mismatched_combinations(
    overrides: dict,
):
    fields = {
        "harness": "acpx",
        "agent": "grok-build",
        "task": "Inspect",
        "profile_id": "profile-1",
        "provider": {
            "id": "grok",
            "transport": "openai-compatible",
            "model_alias": "grok-build-0.1",
        },
        "browser_tools": [
            {"id": "unbrowse"},
            {"id": "stagehand"},
            {"id": "browser-harness"},
        ],
        "routing_policy": {"max_tool_attempts": 2},
    }
    fields.update(overrides)

    with pytest.raises(ValidationError):
        TaskRunCreate(**fields)


def test_task_run_routing_contract_rejects_non_supported_grok_transports():
    for transport in ("cli",):
        with pytest.raises(ValidationError):
            TaskRunCreate(
                harness="acpx",
                agent="grok-build",
                task="Inspect",
                profile_id="profile-1",
                provider={"id": "grok", "transport": transport},
                browser_tools=[
                    {"id": "unbrowse"},
                    {"id": "stagehand"},
                    {"id": "browser-harness"},
                ],
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": {"id": "grok", "transport": "acp"}},
        {"browser_tools": [{"id": "unbrowse"}]},
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [],
        },
        {
            "agent": "codex",
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        },
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [{"id": "unbrowse"}, {"id": "unbrowse"}],
        },
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse", "enabled": False},
                {"id": "stagehand", "enabled": False},
                {"id": "browser-harness", "enabled": False},
            ],
        },
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [{"id": "unbrowse"}],
        },
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "stagehand"},
                {"id": "unbrowse"},
                {"id": "browser-harness"},
            ],
        },
        {
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
            "routing_policy": {"allow_second_browser": True},
        },
        {
            "harness": "browser-use",
            "agent": None,
            "provider": {"id": "grok", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        },
        {
            "provider": {"id": "antigravity", "transport": "acp"},
            "browser_tools": [
                {"id": "unbrowse"},
                {"id": "stagehand"},
                {"id": "browser-harness"},
            ],
        },
    ],
)
def test_task_run_routing_contract_rejects_invalid_task1_shapes(overrides: dict):
    fields = {
        "harness": "acpx",
        "agent": "grok-build",
        "task": "Inspect",
        "profile_id": "profile-1",
    }
    fields.update(overrides)

    with pytest.raises(ValidationError):
        TaskRunCreate(**fields)


# ── ProfileUpdate ────────────────────────────────────────────────────────────


def test_profile_update_all_optional():
    p = ProfileUpdate()
    assert p.name is None
    assert p.platform is None


def test_profile_update_exclude_unset():
    p = ProfileUpdate(name="New Name")
    dumped = p.model_dump(exclude_unset=True)
    assert dumped == {"name": "New Name"}


def test_profile_update_organization_fields_exclude_unset():
    p = ProfileUpdate(
        project_id="project-2",
        folder_path="ops/on-call",
        pinned=True,
        accent_color="#ABCDEF",
        harness="browser-use",
    )
    dumped = p.model_dump(exclude_unset=True)

    assert dumped == {
        "project_id": "project-2",
        "folder_path": "ops/on-call",
        "pinned": True,
        "accent_color": "#ABCDEF",
        "harness": "browser-use",
    }


def test_profile_update_invalid_platform():
    with pytest.raises(ValidationError):
        ProfileUpdate(platform="android")


# ── TagCreate ────────────────────────────────────────────────────────────────


def test_tag_create_minimal():
    t = TagCreate(tag="work")
    assert t.tag == "work"
    assert t.color is None


def test_tag_create_with_color():
    t = TagCreate(tag="personal", color="#00ff00")
    assert t.color == "#00ff00"


# ── ClipboardRequest ─────────────────────────────────────────────────────────


def test_clipboard_request_valid():
    c = ClipboardRequest(text="hello world")
    assert c.text == "hello world"


def test_clipboard_request_max_length():
    with pytest.raises(ValidationError):
        ClipboardRequest(text="x" * 1_048_577)


def test_clipboard_request_at_limit():
    c = ClipboardRequest(text="x" * 1_048_576)
    assert len(c.text) == 1_048_576


# ── LaunchResponse ──────────────────────────────────────────────────────────


def test_launch_response_with_cdp_url():
    r = LaunchResponse(
        profile_id="abc", vnc_ws_port=6100, display=":100",
        cdp_url="/api/profiles/abc/cdp",
    )
    assert r.cdp_url == "/api/profiles/abc/cdp"


def test_launch_response_cdp_url_default_none():
    r = LaunchResponse(profile_id="abc", vnc_ws_port=6100, display=":100")
    assert r.cdp_url is None


# ── ProfileStatusResponse ──────────────────────────────────────────────────


def test_profile_status_response_cdp_url():
    r = ProfileStatusResponse(
        status="running", vnc_ws_port=6100, display=":100",
        cdp_url="/api/profiles/abc/cdp",
    )
    assert r.cdp_url == "/api/profiles/abc/cdp"


def test_profile_status_response_cdp_url_stopped():
    r = ProfileStatusResponse(status="stopped")
    assert r.cdp_url is None


# ── ProfileResponse ────────────────────────────────────────────────────────


def test_profile_response_cdp_url():
    r = ProfileResponse(
        id="abc", name="Test", fingerprint_seed=12345,
        user_data_dir="/data/profiles/abc",
        created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        status="running", cdp_url="/api/profiles/abc/cdp",
    )
    assert r.cdp_url == "/api/profiles/abc/cdp"


def test_profile_response_cdp_url_default_none():
    r = ProfileResponse(
        id="abc", name="Test", fingerprint_seed=12345,
        user_data_dir="/data/profiles/abc",
        created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
    )
    assert r.cdp_url is None


def test_profile_response_redacts_proxy_credentials():
    r = ProfileResponse(
        id="abc",
        name="Test",
        fingerprint_seed=12345,
        proxy="http://proxy-user:top-secret@proxy.test:8080",
        user_data_dir="/data/profiles/abc",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )

    dumped = r.model_dump()
    assert dumped["proxy"] is None
    assert dumped["proxy_display"] == "http://proxy.test:8080"
    assert "proxy-user" not in str(dumped)
    assert "top-secret" not in str(dumped)


def test_profile_response_removes_proxy_path_query_and_fragment():
    r = ProfileResponse(
        id="abc",
        name="Test",
        fingerprint_seed=12345,
        proxy="https://proxy-user:top-secret@proxy.test:8443/session/top-secret?token=query-secret#secret-fragment",
        user_data_dir="/data/profiles/abc",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )

    dumped = r.model_dump()
    assert dumped["proxy"] is None
    assert dumped["proxy_display"] == "https://proxy.test:8443"
    serialized = str(dumped)
    assert "proxy-user" not in serialized
    assert "top-secret" not in serialized
    assert "query-secret" not in serialized
    assert "secret-fragment" not in serialized
