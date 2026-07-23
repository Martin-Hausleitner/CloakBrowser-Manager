"""Standard / prefabricated profile templates with system prompts.

Templates are first-class API resources so agents are not UI-bound.
"""

from __future__ import annotations

import hashlib
from typing import Any

from backend import extension_catalog

_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "generate-new",
        "name": "Generate New",
        "summary": "Fresh fingerprint with geo defaults and selected Comet extensions",
        "system_prompt": (
            "You are operating a fresh CloakBrowser profile. Prefer precise browser "
            "actions, verify pages visually, and never invent proxy or cookie secrets."
        ),
        "harness": "browser-use",
        "project_id": "default",
        "folder_path": "templates",
        "platform": "windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 8,
        "gpu_vendor": "Google Inc. (NVIDIA)",
        "gpu_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "geoip": True,
        "humanize": True,
        "human_preset": "default",
        "clipboard_sync": True,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "timezone": "Europe/Berlin",
        "locale": "de-DE",
        "apply_default_extensions": True,
        "quick_options": ["harness", "platform", "geoip", "humanize", "proxy"],
    },
    {
        "id": "codex-operator",
        "name": "Codex Operator",
        "summary": "Codex Computer Use–oriented desktop profile",
        "system_prompt": (
            "Prefer Codex Computer Use for host-scoped browser actions. Capture, "
            "copy, and paste only when the verified bridge reports capability."
        ),
        "harness": "codex",
        "project_id": "default",
        "folder_path": "templates/codex",
        "platform": "windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 8,
        "gpu_vendor": "Google Inc. (NVIDIA)",
        "gpu_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "geoip": True,
        "humanize": True,
        "human_preset": "careful",
        "clipboard_sync": True,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "timezone": "Europe/Berlin",
        "locale": "de-DE",
        "apply_default_extensions": True,
        "quick_options": ["harness", "geoip", "humanize"],
    },
    {
        "id": "mobile-demo",
        "name": "Mobile Demo",
        "summary": "Portrait framebuffer for VCVM mobile workspace demos",
        "system_prompt": (
            "You are on a mobile-oriented CloakBrowser surface. Keep interactions "
            "compact, respect touch-safe targets, and verify the live VNC/CDP feed."
        ),
        "harness": "browser-use",
        "project_id": "mobile",
        "folder_path": "templates",
        "platform": "macos",
        "screen_width": 390,
        "screen_height": 844,
        "hardware_concurrency": 6,
        "gpu_vendor": "Google Inc. (Apple)",
        "gpu_renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
        "geoip": True,
        "humanize": True,
        "human_preset": "default",
        "clipboard_sync": True,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "timezone": "Europe/Vienna",
        "locale": "de-AT",
        "apply_default_extensions": True,
        "quick_options": ["harness", "platform", "geoip"],
    },
    {
        "id": "proxied-auto",
        "name": "Proxied Auto",
        "summary": "Geo-aligned anti-stealth baseline for inventory proxies",
        "system_prompt": (
            "This profile must always launch with its assigned proxy. Confirm "
            "outbound IP via health / Proxy-Checker before trusting geo claims."
        ),
        "harness": "browser-use",
        "project_id": "proxied",
        "folder_path": "auto",
        "platform": "windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 8,
        "gpu_vendor": "Google Inc. (NVIDIA)",
        "gpu_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "geoip": True,
        "humanize": True,
        "human_preset": "default",
        "clipboard_sync": True,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "timezone": "Europe/Berlin",
        "locale": "de-DE",
        "apply_default_extensions": True,
        "quick_options": ["harness", "proxy", "geoip", "humanize"],
    },
    {
        "id": "stagehand-cdp",
        "name": "Stagehand CDP",
        "summary": "Production CDP primitives preference (metadata)",
        "system_prompt": (
            "Prefer Act/Observe/Extract style steps. Use CDP-direct live view when "
            "latency matters; fall back to VNC for full desktop takeover."
        ),
        "harness": "stagehand",
        "project_id": "research",
        "folder_path": "templates/stagehand",
        "platform": "windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 8,
        "gpu_vendor": "Google Inc. (NVIDIA)",
        "gpu_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        "geoip": True,
        "humanize": False,
        "human_preset": "default",
        "clipboard_sync": True,
        "color_scheme": "dark",
        "search_engine": "duckduckgo",
        "timezone": "Europe/Berlin",
        "locale": "de-DE",
        "apply_default_extensions": True,
        "quick_options": ["harness", "geoip"],
    },
]


def list_templates() -> list[dict[str, Any]]:
    return [dict(item) for item in _TEMPLATES]


def get_template(template_id: str) -> dict[str, Any] | None:
    for item in _TEMPLATES:
        if item["id"] == template_id:
            return dict(item)
    return None


def generate_profile_name(template_id: str, *, project_id: str | None = None) -> str:
    template = get_template(template_id) or {"name": "Profile"}
    label = str(template.get("name") or "Profile")
    digest = hashlib.sha1(f"{template_id}:{project_id or ''}".encode("utf-8")).hexdigest()[:4]
    # Include a short random-ish suffix from time-independent digest + counter material
    suffix = digest.upper()
    project = (project_id or str(template.get("project_id") or "default")).strip() or "default"
    return f"{label} · {project}-{suffix}"


def build_profile_fields(
    template_id: str,
    *,
    overrides: dict[str, Any] | None = None,
    apply_extensions: bool | None = None,
) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        raise KeyError(template_id)
    overrides = dict(overrides or {})
    name = overrides.pop("name", None) or generate_profile_name(
        template_id, project_id=overrides.get("project_id") or template.get("project_id")
    )
    fields = {
        "name": name,
        "sandbox_id": overrides.get("sandbox_id") or "default",
        "project_id": overrides.get("project_id") or template["project_id"],
        "folder_path": overrides.get("folder_path") if "folder_path" in overrides else template["folder_path"],
        "pinned": bool(overrides.get("pinned", False)),
        "accent_color": overrides.get("accent_color", "#6366f1"),
        "harness": overrides.get("harness") or template["harness"],
        "platform": overrides.get("platform") or template["platform"],
        "screen_width": int(overrides.get("screen_width") or template["screen_width"]),
        "screen_height": int(overrides.get("screen_height") or template["screen_height"]),
        "hardware_concurrency": overrides.get(
            "hardware_concurrency", template.get("hardware_concurrency")
        ),
        "gpu_vendor": overrides.get("gpu_vendor", template.get("gpu_vendor")),
        "gpu_renderer": overrides.get("gpu_renderer", template.get("gpu_renderer")),
        "geoip": bool(overrides.get("geoip", template.get("geoip", False))),
        "humanize": bool(overrides.get("humanize", template.get("humanize", False))),
        "human_preset": overrides.get("human_preset") or template.get("human_preset") or "default",
        "clipboard_sync": bool(overrides.get("clipboard_sync", template.get("clipboard_sync", True))),
        "color_scheme": overrides.get("color_scheme", template.get("color_scheme")),
        "search_engine": overrides.get("search_engine", template.get("search_engine")),
        "timezone": overrides.get("timezone", template.get("timezone")),
        "locale": overrides.get("locale", template.get("locale")),
        "proxy": overrides.get("proxy"),
        "notes": overrides.get(
            "notes",
            f"Template `{template_id}`.\n\nSystem prompt:\n{template.get('system_prompt')}",
        ),
        "tags": overrides.get(
            "tags",
            [
                {"tag": "template", "color": "#6366f1"},
                {"tag": template_id, "color": "#22c55e"},
            ],
        ),
        "launch_args": list(overrides.get("launch_args") or []),
    }
    should_apply = (
        template.get("apply_default_extensions", True)
        if apply_extensions is None
        else bool(apply_extensions)
    )
    if should_apply:
        fields["launch_args"] = extension_catalog.merge_launch_args_with_defaults(
            fields["launch_args"]
        )
    seed_material = f"{template_id}:{fields['project_id']}:{fields['platform']}:{fields['timezone']}"
    fields["fingerprint_seed"] = (
        int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16) % 2_147_483_647
    )
    fields["system_prompt"] = template.get("system_prompt")
    fields["template_id"] = template_id
    return fields
