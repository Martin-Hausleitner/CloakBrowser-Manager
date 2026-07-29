"""Pydantic models for profile CRUD operations."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from urllib.parse import urlparse, urlsplit, urlunsplit

SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
FOLDER_SEGMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._ -]*$"
ACCENT_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
MANAGER_OWNED_LAUNCH_ARG_PREFIXES = (
    "--remote-debugging-port",
    "--remote-debugging-address",
    "--remote-debugging-pipe",
    "--user-data-dir",
    "--proxy-server",
    "--proxy-pac-url",
    "--no-proxy-server",
    "--proxy-bypass-list",
    "--proxy-auto-detect",
    "--load-extension",
    "--disable-extensions-except",
    "--disable-web-security",
    "--no-sandbox",
)
Harness = Literal[
    "codex",
    "antigravity",
    "claude-code",
    "opencode",
    "browser-use",
    "browser-harness",
    "unbrowse",
    "stagehand",
    "acpx",
]
AcpxAgent = Literal["codex", "claude", "cursor", "grok-build", "opencode"]
ProviderId = Literal["antigravity", "codex", "claude", "cursor", "grok", "opencode"]
ProviderTransport = Literal["cli", "acp", "openai-compatible"]
ProviderReadinessReason = Literal[
    "ready",
    "auth_required",
    "protocol_unavailable",
    "proxy_unavailable",
    "model_unavailable",
]
PROVIDER_READINESS_TARGETS: tuple[tuple[str, str], ...] = (
    ("antigravity", "cli"),
    ("grok", "cli"),
    ("codex", "acp"),
    ("claude", "acp"),
    ("cursor", "acp"),
    ("grok", "acp"),
    ("opencode", "acp"),
    ("grok", "openai-compatible"),
)
ACP_PROVIDER_TO_AGENT: dict[str, AcpxAgent] = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok-build",
    "opencode": "opencode",
}
MAX_PROVIDER_MODEL_ALIASES = 16
MAX_PROVIDER_MODEL_ALIAS_LENGTH = 96
BrowserToolId = Literal["unbrowse", "stagehand", "browser-harness"]
ROUTING_BROWSER_TOOL_ORDER: tuple[BrowserToolId, ...] = (
    "unbrowse",
    "stagehand",
    "browser-harness",
)
ProfileHealthState = Literal["pending", "running", "passed", "warning", "failed", "unavailable"]
ProfileHealthSourceState = Literal["missing", "measured", "derived", "unavailable", "skipped"]
CONTROL_PLANE_API_VERSION = "cloakbrowser.io/v1"

CONTROL_PLANE_RESOURCE_KINDS = (
    "profiles",
    "projects",
    "tasks",
    "runs",
    "outputs",
    "sessions",
    "views",
    "proxies",
    "extensions",
    "accounts",
    "secret-references",
    "approvals",
    "operations",
    "boxes",
    "runtimes",
    "local-mac",
    "vcvm",
    "orca-web",
)

CONTROL_PLANE_FORBIDDEN_OPERATIONS = (
    "raw CDP socket or unrestricted DevTools domain access",
    "vault reveal, password reveal, cookie export, TOTP seed export, or provider token output",
    "raw proxy credentials or proxy URLs with userinfo",
    "free-form Chromium launch flags or manager-owned runtime flags",
    "arbitrary shell execution inside boxes or runtimes",
)


def _safe_proxy_host_port(host: str | None, port: int | None) -> str | None:
    if not host:
        return None
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{display_host}:{port}" if port is not None else display_host


def _safe_raw_proxy_host_port(value: str) -> str | None:
    if not value or any(char in value for char in "@/?#") or any(char.isspace() for char in value):
        return None
    parts = value.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
        return None
    return value


def redact_proxy_for_response(value: str | None) -> str | None:
    """Return a display-safe proxy string with credentials and URL tails removed."""
    if value is None:
        return None

    raw = str(value).strip()
    if raw == "":
        return None

    if "://" in raw:
        try:
            parsed = urlsplit(raw)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return None
        host_port = _safe_proxy_host_port(host, port)
        if host_port is None:
            return None
        return urlunsplit((parsed.scheme, host_port, "", "", ""))

    if "@" in raw:
        return _safe_raw_proxy_host_port(raw.rsplit("@", 1)[-1])

    parts = raw.split(":")
    if len(parts) == 4 and parts[0] and parts[1].isdigit():
        return f"{parts[0]}:{parts[1]}"

    return _safe_raw_proxy_host_port(raw)


def control_plane_resource_schema() -> dict[str, object]:
    """Versioned resource envelope contract shared by REST, CLI, MCP, and skills."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cloakbrowser.io/contracts/control-plane-resource-v1.json",
        "api_version": CONTROL_PLANE_API_VERSION,
        "kind": "ContractSchema",
        "metadata": {
            "id": "control-plane-resource-v1",
            "resource_version": 1,
        },
        "spec": {
            "resources": list(CONTROL_PLANE_RESOURCE_KINDS),
            "mutation_contract": {
                "client_headers_supported": ["Idempotency-Key", "If-Match"],
                "server_enforcement": {
                    "idempotency_key": False,
                    "if_match": False,
                    "notes": "Legacy v1 routes accept client headers but do not enforce global idempotency or If-Match yet.",
                },
            },
            "forbidden": list(CONTROL_PLANE_FORBIDDEN_OPERATIONS),
            "envelope": {
                "type": "object",
                "required": ["api_version", "kind", "metadata", "spec", "status", "links"],
                "properties": {
                    "api_version": {"const": CONTROL_PLANE_API_VERSION},
                    "kind": {"type": "string"},
                    "metadata": {
                        "type": "object",
                        "required": ["id", "resource_version"],
                        "properties": {
                            "id": {"type": "string"},
                            "resource_version": {"type": "integer", "minimum": 1},
                            "request_id": {"type": "string"},
                            "created_at": {"type": "string"},
                            "updated_at": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                    "spec": {"type": "object"},
                    "status": {"type": "object"},
                    "links": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": False,
            },
        },
    }


def _channel(rest: bool, cli: bool, mcp: bool, skill: bool) -> dict[str, bool]:
    return {"rest": rest, "cli": cli, "mcp": mcp, "skill": skill}


def control_plane_capabilities_payload(*, local_mac_available: bool = False) -> dict[str, object]:
    """Truthful capability discovery; unavailable targets never silently fall back."""
    unavailable = _channel(False, False, False, False)
    resources = {
        "profiles": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["list", "get", "create", "update", "delete", "launch", "stop", "status", "health", "open-links"],
            "cli_operations": ["list", "get", "create", "update", "delete", "launch", "stop", "status", "health", "extensions", "open-links"],
            "mcp_operations": [],
            "skill_operations": ["list", "get", "create", "update", "launch", "stop", "open-links"],
            "mcp_note": "discovery_schema_only",
        },
        "projects": {
            "available": _channel(True, False, False, False),
            "rest_operations": ["list", "get", "create", "update"],
            "cli_operations": [],
            "mcp_operations": [],
            "skill_operations": [],
            "mcp_note": "discovery_schema_only",
        },
        "tasks": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["list", "get", "create", "update", "messages", "events", "run"],
            "cli_operations": ["create", "run"],
            "mcp_operations": [],
            "skill_operations": ["create", "run"],
            "mcp_note": "discovery_schema_only",
        },
        "runs": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["get", "cancel", "retry-health", "override-health", "outputs"],
            "cli_operations": ["get", "cancel", "outputs"],
            "mcp_operations": [],
            "skill_operations": ["get", "cancel", "outputs"],
            "mcp_note": "discovery_schema_only",
        },
        "outputs": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["list"],
            "cli_operations": ["runs outputs"],
            "mcp_operations": [],
            "skill_operations": ["runs outputs"],
            "mcp_note": "discovery_schema_only",
        },
        "sessions": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["extension-open"],
            "cli_operations": ["open-session"],
            "mcp_operations": [],
            "skill_operations": ["open-session"],
            "mcp_note": "discovery_schema_only",
        },
        "views": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["open-links", "vnc", "cdp-live-observer"],
            "cli_operations": ["profiles open-links"],
            "mcp_operations": [],
            "skill_operations": ["profiles open-links"],
            "modes": ["vnc", "cdp-live-observer"],
            "mcp_note": "discovery_schema_only",
        },
        "proxies": {
            "available": _channel(True, False, False, False),
            "rest_operations": ["list", "ingest", "check", "create-profile"],
            "cli_operations": [],
            "mcp_operations": [],
            "skill_operations": [],
            "secrets": "reference-only",
            "mcp_note": "discovery_schema_only",
        },
        "extensions": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["catalog", "defaults", "templates", "inventory", "open-session"],
            "cli_operations": ["list", "search", "defaults", "set-defaults", "enable", "disable"],
            "mcp_operations": [],
            "skill_operations": ["list", "search", "defaults", "set-defaults", "enable", "disable"],
            "mcp_note": "discovery_schema_only",
        },
        "accounts": {
            "available": _channel(True, True, False, True),
            "rest_operations": ["list", "get", "create", "update", "history", "event", "delete"],
            "cli_operations": ["list", "get", "create", "update", "history", "event", "delete"],
            "mcp_operations": [],
            "skill_operations": ["list", "get", "create", "update", "history", "event", "delete"],
            "secrets": "reference-digest-only",
            "mcp_note": "discovery_schema_only",
        },
        "secret-references": {"available": unavailable, "reason_code": "secret_broker_not_implemented"},
        "approvals": {"available": unavailable, "reason_code": "approval_queue_pending"},
        "operations": {"available": unavailable, "reason_code": "operation_store_pending"},
        "boxes": {"available": unavailable, "reason_code": "box_resource_not_implemented"},
        "runtimes": {"available": unavailable, "reason_code": "runtime_resource_not_implemented"},
        "vcvm": {"available": unavailable, "reason_code": "vcvm_capability_resource_not_implemented"},
        "local-mac": {
            "available": unavailable,
            "reason_code": "local_mac_resource_not_implemented",
            "detected": bool(local_mac_available),
        },
        "orca-web": {"available": unavailable, "reason_code": "capability_unavailable"},
    }
    return {
        "api_version": CONTROL_PLANE_API_VERSION,
        "kind": "CapabilitySet",
        "metadata": {"id": "manager-capabilities", "resource_version": 1},
        "resources": resources,
        "mcp_contract": {
            "available": True,
            "tools": [
                "browser_inspect",
                "browser_navigate",
                "browser_click",
                "browser_fill",
                "browser_read_text",
                "control_plane_capabilities",
                "control_plane_resource_schema",
                "orca_web_capabilities",
            ],
            "manager_resource_tools": False,
            "note": "MCP parity is discovery/schema plus run-scoped browser tools; no general Manager resource MCP tools are implemented.",
        },
        "forbidden": list(CONTROL_PLANE_FORBIDDEN_OPERATIONS),
    }


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


def _validate_profile_launch_args(value: list[str] | None) -> list[str] | None:
    """Keep browser-manager-owned Chromium settings outside profile input."""
    for launch_arg in value or []:
        blocked = _manager_owned_launch_arg(launch_arg)
        if blocked:
            raise ValueError(f"launch_args cannot set manager-owned Chromium flag: {blocked}")
    return value


def _manager_owned_launch_arg(launch_arg: str) -> str | None:
    """Return the protected flag matched by a Chromium argument, if any."""
    normalized = launch_arg.strip().lower()
    for blocked in MANAGER_OWNED_LAUNCH_ARG_PREFIXES:
        if (
            normalized == blocked
            or normalized.startswith(f"{blocked}=")
            or normalized.startswith(f"{blocked} ")
        ):
            return blocked
    return None


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
    extension_ids: list[str] = Field(default_factory=list)
    launch_args: list[str] = Field(default_factory=list)
    notes: str | None = None
    tags: list[TagCreate] | None = None

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str) -> str:
        return _validate_folder_path(value)

    @field_validator("launch_args")
    @classmethod
    def validate_launch_args(cls, value: list[str]) -> list[str]:
        return _validate_profile_launch_args(value) or []


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
    extension_ids: list[str] | None = None
    launch_args: list[str] | None = None
    notes: str | None = Field(default=None)
    tags: list[TagCreate] | None = None

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_folder_path(value)

    @field_validator("launch_args")
    @classmethod
    def validate_launch_args(cls, value: list[str] | None) -> list[str] | None:
        return _validate_profile_launch_args(value)


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
    proxy_display: str | None = None
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
    extension_ids: list[str] = []
    launch_args: list[str] = []
    notes: str | None = None
    user_data_dir: str
    created_at: str
    updated_at: str
    tags: list[TagResponse] = []
    status: str = "stopped"  # "running" | "stopped"
    vnc_ws_port: int | None = None
    cdp_url: str | None = None

    @model_validator(mode="after")
    def redact_proxy_secret(self) -> "ProfileResponse":
        self.proxy_display = self.proxy_display or redact_proxy_for_response(self.proxy)
        self.proxy = None
        return self


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
    # Flat preferred-origin fields (same shape as ProfileOpenLinksResponse).
    session_viewer_url: str | None = None
    vnc_fullscreen_url: str | None = None
    cdp_fullscreen_url: str | None = None
    live_url: str | None = None
    debug_url: str | None = None
    debugger_url: str | None = None
    websocket_url: str | None = None
    cdp_url: str | None = None
    live_metrics_url: str | None = None
    local_url: str | None = None
    cloud_url: str | None = None
    local_vnc_fullscreen_url: str | None = None
    local_cdp_fullscreen_url: str | None = None
    cloud_vnc_fullscreen_url: str | None = None
    cloud_cdp_fullscreen_url: str | None = None


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


AccountAuthState = Literal["unknown", "signed_in", "needs_2fa", "signed_out", "locked"]
AccountFactorState = Literal["unknown", "off", "enrolled", "required"]
AccountEventType = Literal[
    "observed",
    "signed_in",
    "signed_out",
    "auth_state_changed",
    "two_factor_required",
    "two_factor_enrolled",
    "passkey_enrolled",
    "secret_reference_changed",
]
_ACCOUNT_REFERENCE_PATTERN = r"^secretref-[A-Za-z0-9][A-Za-z0-9._-]{2,143}$"


def _validate_account_text(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{field_name} contains control characters")
    if (
        _AUTH_BEARER_RE.search(cleaned)
        or _SENSITIVE_ASSIGNMENT_RE.search(cleaned)
        or _PROXY_CREDENTIAL_RE.search(cleaned)
        or _HTML_DOM_TAG_RE.search(cleaned)
    ):
        raise ValueError(f"{field_name} contains forbidden secret-like content")
    return cleaned


def _validate_account_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.isoformat()


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=80, pattern=SLUG_PATTERN)
    subject_label: str = Field(min_length=1, max_length=254)
    display_name: str | None = Field(default=None, max_length=160)
    origin: str | None = Field(default=None, max_length=500)
    auth_state: AccountAuthState = "unknown"
    second_factor_state: AccountFactorState = "unknown"
    passkey_state: AccountFactorState = "unknown"
    secret_ref: str | None = Field(default=None, pattern=_ACCOUNT_REFERENCE_PATTERN)
    totp_ref: str | None = Field(default=None, pattern=_ACCOUNT_REFERENCE_PATTERN)
    last_seen_at: str | None = None

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("subject_label")
    @classmethod
    def validate_subject_label(cls, value: str) -> str:
        return _validate_account_text(value, field_name="subject_label")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_account_text(value, field_name="display_name")

    @field_validator("origin")
    @classmethod
    def normalize_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            from .origin_policy import normalize_origin
        except ImportError:  # pragma: no cover
            from origin_policy import normalize_origin
        return normalize_origin(value)

    @field_validator("last_seen_at")
    @classmethod
    def validate_last_seen_at(cls, value: str | None) -> str | None:
        return _validate_account_timestamp(value)


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)
    origin: str | None = Field(default=None, max_length=500)
    auth_state: AccountAuthState | None = None
    second_factor_state: AccountFactorState | None = None
    passkey_state: AccountFactorState | None = None
    secret_ref: str | None = Field(default=None, pattern=_ACCOUNT_REFERENCE_PATTERN)
    totp_ref: str | None = Field(default=None, pattern=_ACCOUNT_REFERENCE_PATTERN)
    last_seen_at: str | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_account_text(value, field_name="display_name")

    @field_validator("origin")
    @classmethod
    def normalize_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            from .origin_policy import normalize_origin
        except ImportError:  # pragma: no cover
            from origin_policy import normalize_origin
        return normalize_origin(value)

    @field_validator("last_seen_at")
    @classmethod
    def validate_last_seen_at(cls, value: str | None) -> str | None:
        return _validate_account_timestamp(value)

    @model_validator(mode="after")
    def require_update_field(self):
        if not self.model_fields_set:
            raise ValueError("at least one account field is required")
        return self


class AccountResponse(BaseModel):
    id: str
    profile_id: str | None = None
    profile_id_snapshot: str
    sandbox_id: str
    project_id: str
    provider: str
    subject_label: str
    display_name: str | None = None
    origin: str | None = None
    auth_state: AccountAuthState
    second_factor_state: AccountFactorState
    passkey_state: AccountFactorState
    has_secret_reference: bool = False
    has_totp_reference: bool = False
    last_seen_at: str | None = None
    row_version: int = 1
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    updated_at: str


class AccountAuthEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: AccountEventType
    auth_state: AccountAuthState | None = None
    occurred_at: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: str | None) -> str | None:
        return _validate_account_timestamp(value)


class AccountAuthEventResponse(BaseModel):
    id: str
    account_id_snapshot: str
    profile_id_snapshot: str
    sandbox_id: str
    event_type: Literal["created"] | AccountEventType
    auth_state: AccountAuthState | None = None
    actor_kind: str
    actor_id: str | None = None
    occurred_at: str
    created_at: str


class ClipboardRequest(BaseModel):
    text: str = Field(max_length=1_048_576)  # 1MB max


class AutomationLeaseAcquireResponse(BaseModel):
    lease_id: str
    token: str
    expires_at: str
    heartbeat_interval_seconds: int = 15


class AutomationLeaseHeartbeatResponse(BaseModel):
    expires_at: str
    heartbeat_interval_seconds: int = 15


class ProjectCreate(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    sandbox_id: str = Field(min_length=1, max_length=80, pattern=SLUG_PATTERN)
    accent_color: str | None = Field(default=None, pattern=ACCENT_COLOR_PATTERN)
    description: str | None = Field(default=None, max_length=2_000)
    default_retention: Literal["temporary", "project"] = "project"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    accent_color: str | None = Field(default=None, pattern=ACCENT_COLOR_PATTERN)
    description: str | None = Field(default=None, max_length=2_000)
    default_retention: Literal["temporary", "project"] | None = None
    archived: bool | None = None


class ProjectResponse(BaseModel):
    sandbox_id: str
    id: str
    name: str
    accent_color: str | None = None
    description: str | None = None
    default_retention: Literal["temporary", "project"] = "project"
    archived_at: str | None = None
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    updated_at: str


class TaskSessionCreate(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskSessionUpdate(BaseModel):
    row_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=120)
    workflow_state: Literal["open", "done"] | None = None
    archived: bool | None = None
    retention_class: Literal["temporary", "project"] | None = None
    metadata: dict[str, object] | None = None


class TaskSessionResponse(BaseModel):
    id: str
    profile_id: str | None = None
    sandbox_id: str
    project_id: str = "default"
    title: str | None = None
    status: Literal["active", "archived"] = "active"
    workflow_state: Literal["open", "done"] = "open"
    done_at: str | None = None
    archived_at: str | None = None
    retention_class: Literal["temporary", "project", "legacy"] = "project"
    expires_at: str | None = None
    activity_at: str
    row_version: int = 1
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
    icon_url: str | None = None
    store_url: str | None = None


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
    # Top-level flat URLs matching GET /api/profiles/{id}/open-links.
    session_viewer_url: str | None = None
    vnc_fullscreen_url: str | None = None
    cdp_fullscreen_url: str | None = None
    live_url: str | None = None
    # Relative CDP proxy path for automation (not the absolute open-links cdp_url).
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


TaskRunStatus = Literal[
    "queued",
    "health_check",
    "blocked_health",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "revoked",
]

TaskOutputKind = Literal[
    "status",
    "action",
    "observation",
    "screenshot",
    "extracted_data",
    "link",
    "metric",
    "error",
    "approval",
    "summary",
]

_TASK_RUN_MAX_ORIGINS = 64
_TASK_OUTPUT_MAX_PAYLOAD_BYTES = 8_192
_TASK_OUTPUT_MAX_DEPTH = 4
_TASK_OUTPUT_MAX_LIST_ITEMS = 20
_TASK_OUTPUT_MAX_KEYS = 32
_TASK_OUTPUT_SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "set-cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "proxy",
    "clipboard",
    "html",
    "dom",
    "base64",
    "filepath",
    "file_path",
    "path",
)
_TASK_OUTPUT_KIND_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    "status": frozenset({"status", "detail", "progress"}),
    "action": frozenset({"name", "url", "selector", "text", "step", "target"}),
    "observation": frozenset({"text", "url", "title", "note"}),
    # Screenshot artifact metadata is Manager-derived after ingest; callers send {}.
    "screenshot": frozenset(),
    "extracted_data": frozenset({"data", "fields", "label"}),
    "link": frozenset({"url", "title", "rel"}),
    "metric": frozenset({"name", "value", "unit"}),
    "error": frozenset({"code", "message", "retryable"}),
    "approval": frozenset({"prompt", "options", "required"}),
    "summary": frozenset({"text", "result", "status"}),
}
_AUTH_BEARER_RE = re.compile(
    r"(?i)(?:\bauthorization\s*:\s*bearer\b|\bbearer\s+[A-Za-z0-9\-._~+/]+=*)"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:(?:set-)?cookie|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|model[_-]?token|token)\s*[:=]"
)
_PROXY_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:https?|socks5?)://[^/\s\"']+:[^/\s\"']+@"
)
_HTML_DOM_TAG_RE = re.compile(r"(?i)</?(?:html|head|body|script|style|iframe|object|embed|svg|dom)\b|<[a-z][\s>/]")
_BASE64_PREFIX_RE = re.compile(r"(?i)\b(?:data:[a-z0-9.+-]+/[a-z0-9.+-]*;base64,|base64\s*[:,])")
_BASE64_ALPHABET_CHUNK_RE = re.compile(r"[A-Za-z0-9+/]{64,}={0,2}")
_HEX_DIGEST_RE = re.compile(r"(?i)^[a-f0-9]{64,128}$")
_MIME_TYPE_RE = re.compile(
    r"(?i)^(?:application|audio|font|image|model|multipart|text|video)/[a-z0-9.+-]+$"
)
_WINDOWS_DRIVE_RE = re.compile(r"(?i)^[a-z]:[\\/]")
_DOT_RELATIVE_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")
_HTTP_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_RELATIVE_FILE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9._-]+[\\/])+[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,16}\b"
)
_URL_FIELD_NAMES = frozenset({"url"})
_SELECTOR_FIELD_NAMES = frozenset({"selector"})
_OPAQUE_FIELD_NAMES = frozenset({"artifact_id"})


def acp_agent_for_provider(provider: str) -> AcpxAgent | None:
    """Return the reviewed ACPX agent for a normalized ACP provider id."""
    return ACP_PROVIDER_TO_AGENT.get(str(provider or "").strip())


class TaskProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ProviderId
    transport: ProviderTransport
    model_alias: str | None = Field(default=None, min_length=1, max_length=80)


class BrowserToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: BrowserToolId
    enabled: bool = True


class TaskRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["ordered-fallback"] = "ordered-fallback"
    allow_second_browser: bool = False
    max_tool_attempts: int = Field(default=3, ge=1, le=3)


class TaskRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness: Harness = "browser-use"
    agent: AcpxAgent | None = None
    task: str = Field(min_length=1, max_length=8_000)
    profile_id: str = Field(min_length=1, max_length=120)
    launch_if_stopped: bool = False
    allowed_origins: list[str] = Field(default_factory=list, max_length=_TASK_RUN_MAX_ORIGINS)
    max_steps: int = Field(default=20, ge=1, le=200)
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)
    model_alias: str | None = Field(default=None, min_length=1, max_length=80)
    provider: TaskProviderConfig | None = None
    browser_tools: list[BrowserToolConfig] = Field(default_factory=list)
    routing_policy: TaskRoutingPolicy | None = None

    @model_validator(mode="after")
    def validate_agent_for_harness(self):
        if self.harness == "acpx" and self.agent is None:
            raise ValueError("agent is required for acpx harness")
        if self.harness != "acpx" and self.agent is not None:
            raise ValueError("agent is only valid for acpx harness")
        has_provider = self.provider is not None
        has_tools = bool(self.browser_tools)
        if not has_provider and not has_tools and self.routing_policy is None:
            return self
        if self.provider is None or not self.browser_tools:
            raise ValueError("provider and non-empty browser_tools are required together")
        if self.routing_policy is None:
            self.routing_policy = TaskRoutingPolicy()
        tool_ids = [tool.id for tool in self.browser_tools]
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("browser_tools must not contain duplicate ids")
        if not any(tool.enabled for tool in self.browser_tools):
            raise ValueError("browser_tools must contain at least one enabled tool")
        if self.routing_policy.allow_second_browser:
            raise ValueError("allow_second_browser is not supported")
        if self.harness != "acpx":
            raise ValueError("routing contract is only supported for acpx harness")
        if self.provider.transport == "openai-compatible":
            if self.provider.id != "grok" or self.agent != "grok-build":
                raise ValueError(
                    "openai-compatible routing contract requires provider grok and agent grok-build"
                )
        elif self.provider.transport == "acp":
            expected_agent = acp_agent_for_provider(self.provider.id)
            if expected_agent is None or self.agent != expected_agent:
                raise ValueError(
                    "acpx routing contract requires provider and agent to match"
                )
        else:
            raise ValueError(
                "acpx routing contract requires provider over acp or openai-compatible"
            )
        if tuple(tool_ids) != ROUTING_BROWSER_TOOL_ORDER:
            raise ValueError(
                "browser_tools must be ordered as unbrowse, stagehand, browser-harness"
            )
        return self

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, value: list[str]) -> list[str]:
        try:
            from .origin_policy import normalize_origin_set
        except ImportError:  # pragma: no cover - flat uvicorn import path
            from origin_policy import normalize_origin_set

        if len(value) > _TASK_RUN_MAX_ORIGINS:
            raise ValueError(f"allowed_origins may contain at most {_TASK_RUN_MAX_ORIGINS} entries")
        # Empty is allowed at the model layer and gated by operate permission in the route.
        return list(normalize_origin_set(value))


class TaskHealthSnapshot(BaseModel):
    state: str
    checked_at: str | None = None
    proxy_configured: bool
    proxy_reachable: bool | None = None
    measured_authenticity_score: int | None = None
    inferred_authenticity_score: int | None = None
    measured_authenticity_source: Literal["browser_signals", "proxychecker"] | None = None
    reasons: list[str] = Field(default_factory=list)
    measurement_error: bool
    policy_version: str
    outbound_ip_masked: str | None = None


class TaskHealthDecision(BaseModel):
    allowed: bool
    waiting: bool
    failed_reasons: list[str] = Field(default_factory=list)
    non_overridable_reasons: list[str] = Field(default_factory=list)
    policy_version: str


class TaskHealthOverride(BaseModel):
    applied: bool
    reason: str | None = None
    actor_kind: str | None = None
    actor_id: str | None = None
    applied_at: str | None = None
    failed_reasons: list[str] = Field(default_factory=list)
    non_overridable_reasons: list[str] = Field(default_factory=list)
    policy_version: str | None = None


class TaskRunResponse(BaseModel):
    id: str
    task_session_id: str
    task_message_id: str
    profile_id: str | None = None
    profile_id_snapshot: str
    sandbox_id: str
    harness: Harness
    agent: AcpxAgent | None = None
    status: TaskRunStatus
    launch_if_stopped: bool = False
    allowed_origins: list[str] = Field(default_factory=list)
    viewport_revision: str | None = None
    launch_evidence: dict[str, object] = Field(default_factory=dict)
    max_steps: int
    timeout_seconds: int
    model_alias: str | None = None
    provider: TaskProviderConfig | None = None
    browser_tools: list[BrowserToolConfig] = Field(default_factory=list)
    routing_policy: TaskRoutingPolicy | None = None
    deadline_at: str
    health_snapshot: TaskHealthSnapshot
    health_decision: TaskHealthDecision
    health_override: TaskHealthOverride | None = None
    retry_count: int = 0
    first_action_sequence: int | None = None
    first_action_at: str | None = None
    claimed_by: str | None = None
    claim_expires_at: str | None = None
    worker_id: str | None = None
    claim_eligible_at: str | None = None
    cancelled_at: str | None = None
    lease_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    queued_at: str | None = None
    created_by_kind: str
    created_by_id: str | None = None
    created_at: str
    updated_at: str


class WorkerClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_session_id: str
    task: str
    profile_id: str | None = None
    sandbox_id: str
    harness: Harness
    agent: AcpxAgent | None = None
    status: TaskRunStatus
    allowed_origins: list[str] = Field(default_factory=list)
    viewport_revision: str | None = None
    max_steps: int
    timeout_seconds: int
    model_alias: str | None = None
    provider: TaskProviderConfig | None = None
    browser_tools: list[BrowserToolConfig] = Field(default_factory=list)
    routing_policy: TaskRoutingPolicy | None = None
    deadline_at: str
    claim_expires_at: str | None = None
    worker_id: str | None = None
    launch_if_stopped: bool = False


class WorkerHeartbeatResponse(BaseModel):
    claim_expires_at: str
    lease_expires_at: str
    cancel_requested: bool = False
    heartbeat_interval_seconds: int = 15


class TaskHarnessPresenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness: Harness
    worker_seen_recently: bool
    state: Literal["polling", "stale", "unavailable"]
    last_seen_at: str | None = None
    reason: str | None = None


AcpxPreflightReason = Literal[
    "ok",
    "auth_required",
    "adapter_unavailable",
    "version_mismatch",
    "mcp_unavailable",
    "protocol_error",
    "internal_error",
]


class WorkerAcpxPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AcpxAgent
    ready: bool
    reason_code: AcpxPreflightReason

    @model_validator(mode="after")
    def validate_ready_reason(self):
        if self.ready and self.reason_code != "ok":
            raise ValueError("ready preflight requires reason_code=ok")
        if not self.ready and self.reason_code == "ok":
            raise ValueError("failed preflight requires a failure reason")
        return self


def _sanitize_provider_model_aliases(values: list[str]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        alias = raw.strip()
        if not alias:
            continue
        lowered = alias.lower()
        if "://" in lowered or "token" in lowered or "secret" in lowered or "bearer" in lowered:
            continue
        alias = alias[:MAX_PROVIDER_MODEL_ALIAS_LENGTH]
        if alias in seen:
            continue
        aliases.append(alias)
        seen.add(alias)
        if len(aliases) >= MAX_PROVIDER_MODEL_ALIASES:
            break
    return aliases


class WorkerProviderPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    transport: ProviderTransport
    ready: bool
    reason_code: ProviderReadinessReason
    model_aliases: list[str] = Field(default_factory=list)

    @field_validator("model_aliases", mode="before")
    @classmethod
    def sanitize_model_aliases(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("model_aliases must be a list")
        return _sanitize_provider_model_aliases(value)

    @model_validator(mode="after")
    def validate_ready_reason_and_target(self):
        target = (self.provider, self.transport)
        if target not in PROVIDER_READINESS_TARGETS:
            raise ValueError("unsupported provider transport target")
        if self.ready and self.reason_code != "ready":
            raise ValueError("ready provider preflight requires reason_code=ready")
        if not self.ready and self.reason_code == "ready":
            raise ValueError("failed provider preflight requires a failure reason")
        if not self.ready:
            self.model_aliases = []
        return self


class TaskHarnessAgentPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AcpxAgent
    ready: bool
    state: Literal["ready", "failed", "stale", "unavailable"]
    reason_code: str
    checked_at: str | None = None


class TaskHarnessPreflightsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness: Literal["acpx"]
    agents: list[TaskHarnessAgentPreflightResponse] = Field(default_factory=list)


class ProviderReadinessTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    transport: ProviderTransport
    ready: bool
    state: Literal["ready", "failed", "stale", "unavailable"]
    reason_code: ProviderReadinessReason
    checked_at: str | None = None
    model_aliases: list[str] = Field(default_factory=list)


class ProviderReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderReadinessTargetResponse] = Field(default_factory=list)


class WorkerCapabilityResponse(BaseModel):
    token: str
    cdp_url: str
    headers: dict[str, str]
    expires_at: str
    profile_id: str
    run_id: str
    harness: Harness
    agent: AcpxAgent | None = None
    allowed_origins: list[str] = Field(default_factory=list)
    viewport_revision: str | None = None
    launch_evidence: dict[str, object] = Field(default_factory=dict)
    provider: TaskProviderConfig | None = None
    browser_tools: list[BrowserToolConfig] = Field(default_factory=list)
    routing_policy: TaskRoutingPolicy | None = None


class WorkerFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class TaskRunHealthOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must be a non-empty string")
        return cleaned


def _looks_like_http_url(value: str) -> bool:
    try:
        parts = urlparse(value)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return True


def _looks_like_relative_browser_url(value: str) -> bool:
    if not value.startswith("/") or value.startswith("//"):
        return False
    if any(ch.isspace() for ch in value):
        return False
    if "\\" in value or ".." in value.split("/"):
        return False
    return True


def _looks_like_filesystem_path(value: str) -> bool:
    if _MIME_TYPE_RE.fullmatch(value):
        return False
    scrubbed = _HTTP_URL_IN_TEXT_RE.sub(" ", value).strip()
    if not scrubbed:
        return False
    if scrubbed.startswith("/") or "file:" in scrubbed.lower():
        return True
    if scrubbed.startswith("\\\\"):
        return True
    if _WINDOWS_DRIVE_RE.match(scrubbed):
        return True
    if _DOT_RELATIVE_RE.search(scrubbed):
        return True
    if _RELATIVE_FILE_PATH_RE.search(scrubbed):
        return True
    return False


def _looks_like_base64_blob(value: str) -> bool:
    if _BASE64_PREFIX_RE.search(value):
        return True
    for match in _BASE64_ALPHABET_CHUNK_RE.finditer(value):
        chunk = match.group(0)
        if "=" in chunk or "+" in chunk or "/" in chunk:
            return True
        # Pure hex digests (e.g. sha256) are allowed; other long alnum blobs are not.
        if _HEX_DIGEST_RE.fullmatch(chunk) is None:
            return True
    return False


def _reject_sensitive_common(value: str) -> None:
    if _AUTH_BEARER_RE.search(value):
        raise ValueError("text contains rejected sensitive content")
    if _SENSITIVE_ASSIGNMENT_RE.search(value):
        raise ValueError("text contains rejected sensitive content")
    if _PROXY_CREDENTIAL_RE.search(value):
        raise ValueError("text contains rejected sensitive content")
    try:
        from . import access_control as access
    except ImportError:  # pragma: no cover - flat uvicorn import path
        import access_control as access
    if access.contains_persisted_cbm_token(value):
        raise ValueError("text contains rejected sensitive content")
    if _HTML_DOM_TAG_RE.search(value):
        raise ValueError("text contains rejected markup")
    if _looks_like_base64_blob(value):
        raise ValueError("text contains rejected binary content")


def _reject_unsafe_url_value(value: str) -> None:
    lower = value.lower()
    if lower.startswith("file:"):
        raise ValueError("text contains rejected filesystem path")
    if _looks_like_http_url(value) or _looks_like_relative_browser_url(value):
        return
    raise ValueError("text contains rejected filesystem path")


def _reject_unsafe_text(value: str, *, field_name: str | None = None) -> None:
    """Reject credential-like, path, HTML, or binary text without echoing it."""
    _reject_sensitive_common(value)
    field = (field_name or "").lower()
    if field in _OPAQUE_FIELD_NAMES:
        return
    if field in _URL_FIELD_NAMES:
        _reject_unsafe_url_value(value)
        return
    if field in _SELECTOR_FIELD_NAMES:
        return
    if _looks_like_filesystem_path(value):
        raise ValueError("text contains rejected filesystem path")


def _reject_sensitive_output_key(key: str) -> None:
    key_lower = key.lower()
    if any(part in key_lower for part in _TASK_OUTPUT_SENSITIVE_KEY_PARTS):
        raise ValueError("payload contains a rejected key")


def _validate_output_payload_value(
    value: object,
    *,
    depth: int,
    field_name: str | None = None,
) -> object:
    if depth > _TASK_OUTPUT_MAX_DEPTH:
        raise ValueError("payload exceeds maximum nesting depth")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 2_048:
            raise ValueError("payload string values are too large")
        _reject_unsafe_text(value, field_name=field_name)
        return value
    if isinstance(value, list):
        if len(value) > _TASK_OUTPUT_MAX_LIST_ITEMS:
            raise ValueError("payload lists are too large")
        return [
            _validate_output_payload_value(item, depth=depth + 1, field_name=field_name)
            for item in value
        ]
    if isinstance(value, dict):
        if len(value) > _TASK_OUTPUT_MAX_KEYS:
            raise ValueError("payload objects have too many keys")
        cleaned: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("payload object keys must be strings")
            _reject_sensitive_output_key(raw_key)
            cleaned[raw_key] = _validate_output_payload_value(
                raw_value,
                depth=depth + 1,
                field_name=raw_key,
            )
        return cleaned
    raise ValueError("payload values must be JSON scalars, lists, or objects")


class TaskOutputCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)
    kind: TaskOutputKind
    summary: str = Field(min_length=1, max_length=500)
    payload: dict[str, object] = Field(default_factory=dict)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned != value:
            raise ValueError("idempotency_key must be a non-empty trimmed string")
        return cleaned

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("summary must be a non-empty string")
        _reject_unsafe_text(cleaned)
        return cleaned

    @model_validator(mode="after")
    def validate_payload_shape(self):
        allowed = _TASK_OUTPUT_KIND_PAYLOAD_KEYS[self.kind]
        unknown = set(self.payload) - allowed
        if unknown:
            raise ValueError("payload contains keys that are not allowlisted for this kind")
        for key in self.payload:
            _reject_sensitive_output_key(key)
        cleaned = _validate_output_payload_value(self.payload, depth=0)
        if not isinstance(cleaned, dict):
            raise ValueError("payload must be an object")
        encoded = json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _TASK_OUTPUT_MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds maximum size")
        if self.kind == "screenshot" and cleaned:
            # Reject any caller-supplied screenshot artifact fields (Task6 two-phase).
            raise ValueError("screenshot payload must be empty")
        self.payload = cleaned
        return self


class TaskOutputResponse(BaseModel):
    id: str
    run_id: str
    sequence: int
    idempotency_key: str
    kind: TaskOutputKind
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: str
    artifact_expired: bool = False


# ── Orca agent browser workspace ─────────────────────────────────────────────

OrcaAgentCli = Literal["cursor-agent", "grok", "agy", "codex"]
OrcaSessionStatus = Literal["starting", "running", "closed", "error"]


class OrcaSessionStartRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    agent: OrcaAgentCli
    prompt: str | None = Field(default=None, max_length=16000)


class OrcaSessionSendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    enter: bool = True


class OrcaSessionCapabilities(BaseModel):
    start: bool = True
    read: bool = True
    send: bool = True
    close: bool = True
    pause: bool = False
    resume: bool = False


class OrcaCapabilitiesResponse(BaseModel):
    available: bool
    orca_bin: str
    agents: list[str]
    operations: list[str]
    actions: OrcaSessionCapabilities
    notes: list[str] = Field(default_factory=list)


class OrcaSessionResponse(BaseModel):
    id: str
    profile_id: str
    sandbox_id: str
    agent: OrcaAgentCli
    terminal_handle: str
    status: OrcaSessionStatus
    created_at: float
    closed_at: float | None = None
    last_error: str | None = None
    capabilities: OrcaSessionCapabilities
    connection: dict[str, object] = Field(default_factory=dict)


class OrcaSessionOutputResponse(BaseModel):
    session_id: str
    terminal_handle: str
    cursor: int
    next_cursor: int
    output: str
    status: OrcaSessionStatus
    capabilities: OrcaSessionCapabilities


class OrcaSessionSendResponse(BaseModel):
    session_id: str
    ok: bool
    status: OrcaSessionStatus
    capabilities: OrcaSessionCapabilities
