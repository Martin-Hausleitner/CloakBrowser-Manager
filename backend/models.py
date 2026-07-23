"""Pydantic models for profile CRUD operations."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
FOLDER_SEGMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._ -]*$"
ACCENT_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
Harness = Literal[
    "codex",
    "antigravity",
    "claude-code",
    "opencode",
    "browser-use",
    "browser-harness",
    "unbrowse",
    "stagehand",
]
ProfileHealthState = Literal["pending", "running", "passed", "warning", "failed", "unavailable"]
ProfileHealthSourceState = Literal["missing", "measured", "derived", "unavailable", "skipped"]


def _validate_folder_path(value: str) -> str:
    if value == "":
        return value
    if len(value) > 240:
        raise ValueError("folder_path must be at most 240 characters")
    if value.startswith("/") or value.endswith("/"):
        raise ValueError("folder_path must not start or end with '/'")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("folder_path must contain friendly path segments")
    if any(re.fullmatch(FOLDER_SEGMENT_PATTERN, segment) is None for segment in segments):
        raise ValueError("folder_path must contain friendly path segments")
    return value


class ProfileCreate(BaseModel):
    name: str
    sandbox_id: str = Field(
        default="default",
        min_length=1,
        max_length=80,
        pattern=SLUG_PATTERN,
    )
    project_id: str = Field(default="default", min_length=1, max_length=80, pattern=SLUG_PATTERN)
    folder_path: str = Field(default="", max_length=240)
    pinned: bool = False
    accent_color: str | None = Field(default=None, pattern=ACCENT_COLOR_PATTERN)
    harness: Harness = "codex"
    fingerprint_seed: int | None = None  # random if not set
    proxy: str | None = None  # "http://user:pass@host:port" or null
    timezone: str | None = None  # "America/New_York"
    locale: str | None = None  # "en-US"
    platform: Literal["windows", "macos", "linux"] = "windows"
    user_agent: str | None = None
    screen_width: int = 1920
    screen_height: int = 1080
    gpu_vendor: str | None = None
    gpu_renderer: str | None = None
    hardware_concurrency: int | None = None
    humanize: bool = False
    human_preset: Literal["default", "careful"] = "default"
    headless: bool = False
    geoip: bool = False
    clipboard_sync: bool = True
    auto_launch: bool = False
    color_scheme: Literal["light", "dark", "no-preference"] | None = None
    search_engine: Literal["google", "bing", "duckduckgo"] | None = None
    launch_args: list[str] = Field(default_factory=list)
    notes: str | None = None
    tags: list[TagCreate] | None = None

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str) -> str:
        return _validate_folder_path(value)


class ProfileUpdate(BaseModel):
    name: str | None = None
    sandbox_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=SLUG_PATTERN,
    )
    project_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=SLUG_PATTERN)
    folder_path: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None
    accent_color: str | None = Field(default=None, pattern=ACCENT_COLOR_PATTERN)
    harness: Harness | None = None
    fingerprint_seed: int | None = None
    proxy: str | None = Field(default=None)
    timezone: str | None = Field(default=None)
    locale: str | None = Field(default=None)
    platform: Literal["windows", "macos", "linux"] | None = None
    user_agent: str | None = Field(default=None)
    screen_width: int | None = None
    screen_height: int | None = None
    gpu_vendor: str | None = Field(default=None)
    gpu_renderer: str | None = Field(default=None)
    hardware_concurrency: int | None = Field(default=None)
    humanize: bool | None = None
    human_preset: Literal["default", "careful"] | None = None
    headless: bool | None = None
    geoip: bool | None = None
    clipboard_sync: bool | None = None
    auto_launch: bool | None = None
    color_scheme: Literal["light", "dark", "no-preference"] | None = Field(default=None)
    search_engine: Literal["google", "bing", "duckduckgo"] | None = Field(default=None)
    launch_args: list[str] | None = None
    notes: str | None = Field(default=None)
    tags: list[TagCreate] | None = None

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_folder_path(value)


class ProfileBulkOrganize(BaseModel):
    """Safe bulk organization update. Authorization remains sandbox-scoped per profile."""

    profile_ids: list[str] = Field(min_length=1, max_length=100)
    project_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=SLUG_PATTERN)
    folder_path: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_folder_path(value)

    @field_validator("profile_ids")
    @classmethod
    def validate_profile_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            pid = str(item or "").strip()
            if not pid or len(pid) > 120:
                raise ValueError("profile_ids must contain non-empty ids up to 120 characters")
            if pid in seen:
                continue
            seen.add(pid)
            cleaned.append(pid)
        if not cleaned:
            raise ValueError("profile_ids must not be empty")
        return cleaned



class TagCreate(BaseModel):
    tag: str
    color: str | None = None  # hex color


class TagResponse(BaseModel):
    tag: str
    color: str | None = None


class ProfileResponse(BaseModel):
    id: str
    name: str
    sandbox_id: str = "default"
    project_id: str = Field(default="default", min_length=1, max_length=80, pattern=SLUG_PATTERN)
    folder_path: str = Field(default="", max_length=240)
    pinned: bool = False
    accent_color: str | None = Field(default=None, pattern=ACCENT_COLOR_PATTERN)
    harness: Harness = "codex"
    fingerprint_seed: int
    proxy: str | None = None
    timezone: str | None = None
    locale: str | None = None
    platform: str = "windows"
    user_agent: str | None = None
    screen_width: int = 1920
    screen_height: int = 1080
    gpu_vendor: str | None = None
    gpu_renderer: str | None = None
    hardware_concurrency: int | None = None
    humanize: bool = False
    human_preset: str = "default"
    headless: bool = False
    geoip: bool = False
    clipboard_sync: bool = True
    auto_launch: bool = False

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str) -> str:
        return _validate_folder_path(value)

    @field_validator("clipboard_sync", mode="before")
    @classmethod
    def coerce_clipboard_sync(cls, v: object) -> bool:
        return v if v is not None else True

    color_scheme: str | None = None
    search_engine: str | None = None
    launch_args: list[str] = []
    notes: str | None = None
    user_data_dir: str
    created_at: str
    updated_at: str
    tags: list[TagResponse] = []
    status: str = "stopped"  # "running" | "stopped"
    vnc_ws_port: int | None = None
    cdp_url: str | None = None


class SessionLinkSet(BaseModel):
    """Absolute open links for one origin (local tunnel or cloud/public)."""

    session_viewer_url: str
    vnc_fullscreen_url: str | None = None
    cdp_fullscreen_url: str | None = None
    live_url: str | None = None
    vnc_ws_url: str
    debug_url: str | None = None
    debugger_url: str | None = None
    cdp_http_url: str | None = None
    cdp_ws_url: str | None = None
    live_metrics_url: str | None = None
    launch_path: str
    stop_path: str
    status_path: str
    live_metrics_path: str | None = None


class SessionOpenLinks(BaseModel):
    """Steel-style local vs cloud open URLs for a profile session."""

    profile_id: str
    prefer: Literal["local", "cloud"] = "local"
    mode: Literal["cdp", "vnc", "shell"] = "cdp"
    open_url: str
    local: SessionLinkSet
    cloud: SessionLinkSet | None = None
    bases: dict[str, str | None] = Field(default_factory=dict)


class LaunchResponse(BaseModel):
    profile_id: str
    status: str = "running"
    vnc_ws_port: int
    display: str
    cdp_url: str | None = None
    links: SessionOpenLinks | None = None


class StatusResponse(BaseModel):
    running_count: int
    binary_version: str
    profiles_total: int


class ProfileStatusResponse(BaseModel):
    status: str  # "running" | "stopped"
    vnc_ws_port: int | None = None
    display: str | None = None
    cdp_url: str | None = None
    links: SessionOpenLinks | None = None


class ProfileHealthResponse(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    state: ProfileHealthState = "unavailable"
    checked_at: str | None = None
    proxy_configured: bool = False
    proxy_reachable: bool | None = None
    outbound_ip_masked: str | None = Field(default=None, max_length=64)
    proxy_latency_ms: float | None = Field(default=None, ge=0)
    proxy_risk_score: int | None = Field(default=None, ge=0, le=100)
    proxy_authenticity_score: int | None = Field(default=None, ge=0, le=100)
    fingerprint_consistency_score: int | None = Field(default=None, ge=0, le=100)
    browser_scan_score: int | None = Field(default=None, ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=64)
    sources: dict[str, ProfileHealthSourceState] = Field(default_factory=dict)


class ClipboardRequest(BaseModel):
    text: str = Field(max_length=1_048_576)  # 1MB max


class TaskSessionCreate(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskSessionResponse(BaseModel):
    id: str
    profile_id: str
    sandbox_id: str
    title: str | None = None
    status: Literal["active", "archived"] = "active"
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    updated_at: str
    metadata: dict[str, object] = Field(default_factory=dict)


TaskCommandKind = Literal[
    "navigate",
    "click",
    "double_click",
    "scroll",
    "type_text",
    "keypress",
    "drag",
    "move",
    "wait",
    "copy",
    "paste",
    "screenshot",
    "viewport",
    "fullscreen",
    "focus_remote",
    "focus_chat",
]


class TaskCommand(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    kind: TaskCommandKind
    scope: Literal["ui", "host"]
    args: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 20:
            raise ValueError("Command args may contain at most 20 keys")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 2_048:
            raise ValueError("Command args are too large")
        return value


class TaskMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    commands: list[TaskCommand] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: list[TaskCommand]) -> list[TaskCommand]:
        return _validate_task_commands(value)


class TaskCommandRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)
    profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    commands: list[TaskCommand] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: list[TaskCommand]) -> list[TaskCommand]:
        return _validate_task_commands(value)


def _validate_task_commands(value: list[TaskCommand]) -> list[TaskCommand]:
    if len(value) > 20:
        raise ValueError("A task message may contain at most 20 commands")
    return value


class TaskMessageResponse(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskEventResponse(BaseModel):
    id: str
    session_id: str
    type: str
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    payload: dict[str, object] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    """Bootstrap-token or named-user login request.

    The token route stays backward compatible for existing private deployments.
    When access control is enabled, an admin can additionally provision named
    users who authenticate with ``username`` and ``password``.
    """

    token: str | None = Field(default=None, min_length=1, max_length=4096)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=4096)

    @model_validator(mode="after")
    def validate_credential_shape(self):
        if self.token and (self.username or self.password):
            raise ValueError("Use either token or username/password, not both")
        if self.token:
            return self
        if self.username and self.password:
            return self
        raise ValueError("Provide a token or both username and password")


AccessPermission = Literal["view", "interact", "operate", "automate"]
AccessRole = Literal["admin", "operator", "viewer"]


class AccessGrant(BaseModel):
    sandbox_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    permission: AccessPermission


class AccessUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    password: str = Field(min_length=12, max_length=4096)
    role: AccessRole = "viewer"
    grants: list[AccessGrant] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)


class AccessUserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=4096)
    role: AccessRole | None = None
    active: bool | None = None
    grants: list[AccessGrant] | None = None
    group_ids: list[str] | None = None


class AccessUserResponse(BaseModel):
    id: str
    username: str
    role: AccessRole
    active: bool
    created_at: str
    group_ids: list[str] = Field(default_factory=list)
    grants: list[AccessGrant] = Field(default_factory=list)
    effective_grants: list[AccessGrant] = Field(default_factory=list)


class AccessGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    active: bool = True
    member_user_ids: list[str] = Field(default_factory=list)
    grants: list[AccessGrant] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Group name cannot be blank")
        return value


class AccessGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    active: bool | None = None
    member_user_ids: list[str] | None = None
    grants: list[AccessGrant] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Group name cannot be null")
        if not value.strip():
            raise ValueError("Group name cannot be blank")
        return value

    @field_validator("active")
    @classmethod
    def validate_active(cls, value: bool | None) -> bool | None:
        if value is None:
            raise ValueError("Group active state cannot be null")
        return value


class AccessGroupResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    active: bool
    created_at: str
    member_user_ids: list[str] = Field(default_factory=list)
    grants: list[AccessGrant] = Field(default_factory=list)


class AccessAgentCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    paperclip_agent_id: str | None = Field(default=None, max_length=160)
    grants: list[AccessGrant] = Field(default_factory=list)


class AccessAgentUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    paperclip_agent_id: str | None = Field(default=None, max_length=160)
    active: bool | None = None
    grants: list[AccessGrant] | None = None


class AccessAgentResponse(BaseModel):
    id: str
    display_name: str
    paperclip_agent_id: str | None = None
    active: bool
    created_at: str
    grants: list[AccessGrant] = Field(default_factory=list)


class AccessAgentCreatedResponse(AccessAgentResponse):
    api_key: str


class AccessIdentityResponse(BaseModel):
    kind: Literal["bootstrap", "user", "agent", "anonymous"]
    id: str | None = None
    display_name: str
    role: str
    grants: list[AccessGrant] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)
    effective_grants: list[AccessGrant] = Field(default_factory=list)


class ExtensionItem(BaseModel):
    id: str
    path: str
    name: str
    version: str
    manifest_version: int
    description: str
    permissions: list[str] = Field(default_factory=list)
    trust_state: Literal["valid", "untrusted_manifest", "missing_manifest", "invalid_path"]
    error: str | None = None


class ExtensionInventoryResponse(BaseModel):
    profile_id: str
    extensions: list[ExtensionItem] = Field(default_factory=list)


ProxyCheckState = Literal["missing", "passed", "warning", "failed", "unavailable"]


class ProxyInventoryIngest(BaseModel):
    """Bulk ingest of ``host:port:user:pass`` lines. Secrets never leave the server."""

    lines: list[str] = Field(min_length=1, max_length=500)


class ProxyInventoryItem(BaseModel):
    id: str
    label: str
    host_masked: str
    port: int | None = None
    username_masked: str | None = None
    has_credentials: bool = False
    active: bool = True
    check_state: ProxyCheckState = "missing"
    reachable: bool | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    authenticity_score: int | None = Field(default=None, ge=0, le=100)
    country_code: str | None = Field(default=None, max_length=2)
    timezone_hint: str | None = None
    locale_hint: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    last_checked_at: str | None = None
    created_at: str
    updated_at: str


class ProxyInventoryIngestResponse(BaseModel):
    created: int = 0
    updated: int = 0
    rejected: int = 0
    items: list[ProxyInventoryItem] = Field(default_factory=list)


class ProxyAutoProfileCreate(BaseModel):
    """Create a geo-aligned stealth profile from an inventory proxy."""

    name: str | None = Field(default=None, max_length=120)
    project_id: str = Field(default="proxied", min_length=1, max_length=80, pattern=SLUG_PATTERN)
    sandbox_id: str = Field(default="default", min_length=1, max_length=80, pattern=SLUG_PATTERN)
    harness: Harness = "browser-use"
    launch: bool = False


class ExtensionProfileSummary(BaseModel):
    id: str
    name: str
    project_id: str = "default"
    folder_path: str = ""
    sandbox_id: str = "default"
    harness: Harness = "codex"
    pinned: bool = False
    timezone: str | None = None
    locale: str | None = None
    proxy_configured: bool = False
    status: str = "stopped"
    running: bool = False


class ExtensionCatalogResponse(BaseModel):
    """One-shot bootstrap payload for a Chrome extension or agent."""

    bases: dict[str, str | None] = Field(default_factory=dict)
    endpoints: dict[str, str] = Field(default_factory=dict)
    profiles: list[ExtensionProfileSummary] = Field(default_factory=list)
    proxies: list[ProxyInventoryItem] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)


class ExtensionOpenSessionRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    launch: bool = True
    prefer: Literal["local", "cloud"] = "local"
    mode: Literal["cdp", "vnc", "shell"] = "cdp"


class ExtensionOpenSessionResponse(BaseModel):
    profile_id: str
    status: str
    launched: bool = False
    already_running: bool = False
    prefer: Literal["local", "cloud"] = "local"
    mode: Literal["cdp", "vnc", "shell"] = "cdp"
    open_url: str
    links: SessionOpenLinks
    cdp_url: str | None = None
    vnc_ws_port: int | None = None
    display: str | None = None


class ProfileOpenLinksResponse(BaseModel):
    """Flat + nested open links for Chrome extension / agent one-click actions."""

    profile_id: str
    prefer: Literal["local", "cloud"] = "local"
    mode: Literal["cdp", "vnc", "shell"] = "cdp"
    open_url: str
    local: SessionLinkSet
    cloud: SessionLinkSet | None = None
    bases: dict[str, str | None] = Field(default_factory=dict)
    session_viewer_url: str
    vnc_fullscreen_url: str
    cdp_fullscreen_url: str | None = None
    live_url: str | None = None
    debug_url: str | None = None
    debugger_url: str | None = None
    websocket_url: str
    cdp_url: str | None = None
    live_metrics_url: str | None = None
    local_url: str
    cloud_url: str | None = None
    local_vnc_fullscreen_url: str | None = None
    local_cdp_fullscreen_url: str | None = None
    cloud_vnc_fullscreen_url: str | None = None
    cloud_cdp_fullscreen_url: str | None = None


class DefaultExtensionItem(BaseModel):
    """Selectable Comet-derived catalog entry for new/template profiles."""

    id: str
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    recommended: bool = False
    default_selected: bool = False
    selectable: bool = True
    selected: bool = False
    available: bool = False
    path: str | None = None
    icon_url: str | None = None
    store_url: str | None = None


class ExtensionDefaultsResponse(BaseModel):
    source: str = "comet"
    source_label: str = "Comet"
    catalog_dir_configured: bool = False
    selected_ids: list[str] = Field(default_factory=list)
    extensions: list[DefaultExtensionItem] = Field(default_factory=list)
    items: list[DefaultExtensionItem] = Field(default_factory=list)
    count: int = 0


class ExtensionDefaultsUpdate(BaseModel):
    selected_ids: list[str] = Field(default_factory=list, max_length=64)


class ProfileTemplateSummary(BaseModel):
    id: str
    name: str
    summary: str = ""
    system_prompt: str = ""
    harness: Harness = "browser-use"
    project_id: str = "default"
    folder_path: str = ""
    platform: Literal["windows", "macos", "linux"] = "windows"
    apply_default_extensions: bool = True
    quick_options: list[str] = Field(default_factory=list)


class ProfileTemplateCreate(BaseModel):
    template_id: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=120)
    project_id: str | None = None
    harness: Harness | None = None
    proxy: str | None = None
    apply_default_extensions: bool | None = None
    launch: bool = False


class ExtensionTemplateItem(BaseModel):
    id: str
    name: str
    project_id: str = "default"
    folder_path: str = ""
    harness: Harness = "browser-use"
    geoip: bool | None = None
    screen_width: int | None = None
    screen_height: int | None = None
    create_path: str = "/api/profiles"
    from_proxy_path: str | None = None


class ExtensionTemplatesResponse(BaseModel):
    templates: list[ExtensionTemplateItem] = Field(default_factory=list)
    create_profile_path: str = "/api/profiles"
    create_from_proxy_path: str = "/api/proxies/{proxy_id}/profiles"


class LiveMetricsSample(BaseModel):
    transport: Literal["cdp", "vnc"] = "cdp"
    connection_state: Literal["connecting", "connected", "reconnecting", "failed", "idle"] = (
        "connected"
    )
    fps: float | None = Field(default=None, ge=0)
    rtt_ms: float | None = Field(default=None, ge=0)
    frames_received: int | None = Field(default=None, ge=0)
    reconnect_count: int | None = Field(default=None, ge=0)
    dropped_frames: int | None = Field(default=None, ge=0)


class LiveMetricsResponse(BaseModel):
    profile_id: str
    transport: Literal["cdp", "vnc"] | None = None
    connection_state: Literal["connecting", "connected", "reconnecting", "failed", "idle"] = "idle"
    fps: float | None = None
    rtt_ms: float | None = None
    frames_received: int | None = None
    reconnect_count: int | None = None
    dropped_frames: int | None = None
    updated_at: str | None = None
    transports: dict[str, dict[str, object]] = Field(default_factory=dict)

