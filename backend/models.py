"""Pydantic models for profile CRUD operations."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProfileCreate(BaseModel):
    name: str
    sandbox_id: str = Field(
        default="default",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
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


class ProfileUpdate(BaseModel):
    name: str | None = None
    sandbox_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
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


class LaunchResponse(BaseModel):
    profile_id: str
    status: str = "running"
    vnc_ws_port: int
    display: str
    cdp_url: str | None = None


class StatusResponse(BaseModel):
    running_count: int
    binary_version: str
    profiles_total: int


class ProfileStatusResponse(BaseModel):
    status: str  # "running" | "stopped"
    vnc_ws_port: int | None = None
    display: str | None = None
    cdp_url: str | None = None


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


class AccessUserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=4096)
    role: AccessRole | None = None
    active: bool | None = None
    grants: list[AccessGrant] | None = None


class AccessUserResponse(BaseModel):
    id: str
    username: str
    role: AccessRole
    active: bool
    created_at: str
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
