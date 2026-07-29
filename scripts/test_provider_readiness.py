from __future__ import annotations

import json
import sys
import time

import pytest

import scripts.provider_readiness as readiness
from scripts.provider_readiness import (
    CommandResult,
    ProviderReadinessResult,
    acp_agent_for_provider,
    acp_provider_result_from_agent_preflight,
    provider_targets_for_agents,
    probe_antigravity_cli,
    probe_grok_cli,
    probe_grok_openai_compatible,
    probe_provider_target,
    run_command,
)


def command_runner(outputs: dict[tuple[str, ...], CommandResult]):
    def run(command, *, timeout_seconds, output_limit_bytes, env):
        assert command[1] in {"--version", "--help"}
        assert timeout_seconds <= 2
        assert output_limit_bytes <= 65536
        assert "CBM_RUN_CAPABILITY_FILE" not in env
        return outputs[tuple(command)]

    return run


def test_provider_targets_include_exact_phase1_matrix():
    assert readiness.PROVIDER_TARGETS == (
        ("antigravity", "cli"),
        ("grok", "cli"),
        ("codex", "acp"),
        ("claude", "acp"),
        ("cursor", "acp"),
        ("grok", "acp"),
        ("opencode", "acp"),
        ("grok", "openai-compatible"),
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
def test_acp_agent_for_provider_is_exact(provider: str, agent: str):
    assert acp_agent_for_provider(provider) == agent


def test_dynamic_provider_targets_include_arbitrary_acp_agents_with_grok_alias():
    assert provider_targets_for_agents(("gemini", "pi", "grok-build", "custom.agent")) == (
        ("antigravity", "cli"),
        ("grok", "cli"),
        ("grok", "acp"),
        ("custom.agent", "acp"),
        ("gemini", "acp"),
        ("pi", "acp"),
        ("grok", "openai-compatible"),
    )
    assert acp_agent_for_provider("custom.agent", ("custom.agent",)) == "custom.agent"
    assert acp_agent_for_provider("grok", ("grok-build", "grok")) == "grok-build"


@pytest.mark.parametrize(
    ("provider", "payload", "expected_ready", "expected_reason", "expected_aliases"),
    [
        ("gemini", {"ready": True, "reason_code": "ok"}, True, "ready", []),
        ("gemini", {"ready": False, "reason_code": "auth_required"}, False, "auth_required", []),
        ("gemini", {"ready": False, "reason_code": "adapter_unavailable"}, False, "protocol_unavailable", []),
        ("grok", {"ready": True, "reason_code": "ok"}, True, "ready", ["grok-build-0.1"]),
    ],
)
def test_provider_target_accepts_discovered_acp_preflight_provider(
    provider: str,
    payload: dict[str, object],
    expected_ready: bool,
    expected_reason: str,
    expected_aliases: list[str],
):
    result = probe_provider_target(provider, "acp", acp_result=payload)

    assert result == ProviderReadinessResult(
        provider=provider,
        transport="acp",
        ready=expected_ready,
        reason_code=expected_reason,
        model_aliases=expected_aliases,
    )


@pytest.mark.parametrize(
    ("provider", "expected_aliases"),
    [
        ("codex", []),
        ("claude", []),
        ("cursor", []),
        ("grok", ["grok-build-0.1"]),
        ("opencode", []),
    ],
)
def test_acp_provider_result_derives_from_corresponding_agent_preflight(
    provider: str, expected_aliases: list[str]
):
    ready_result = acp_provider_result_from_agent_preflight(
        provider, {"ready": True, "reason_code": "ok"}
    )
    assert ready_result == ProviderReadinessResult(
        provider=provider,
        transport="acp",
        ready=True,
        reason_code="ready",
        model_aliases=expected_aliases,
    )

    auth_result = acp_provider_result_from_agent_preflight(
        provider, {"ready": False, "reason_code": "auth_required"}
    )
    assert auth_result.ready is False
    assert auth_result.reason_code == "auth_required"

    protocol_result = acp_provider_result_from_agent_preflight(
        provider, {"ready": False, "reason_code": "adapter_unavailable"}
    )
    assert protocol_result.ready is False
    assert protocol_result.reason_code == "protocol_unavailable"


def test_acp_provider_result_rejects_antigravity_without_normalized_agent():
    result = acp_provider_result_from_agent_preflight(
        "antigravity", {"ready": True, "reason_code": "ok"}
    )

    assert result == ProviderReadinessResult(
        provider="antigravity",
        transport="acp",
        ready=False,
        reason_code="protocol_unavailable",
        model_aliases=[],
    )


def test_antigravity_cli_ready_requires_noninteractive_and_model_listing_flags():
    result = probe_antigravity_cli(
        executable_resolver=lambda name: "/bin/agy",
        command_runner=command_runner(
            {
                ("/bin/agy", "--version"): CommandResult(0, b"agy 1", b""),
                ("/bin/agy", "--help"): CommandResult(
                    0,
                    b"Usage: agy --print --output-format json models list",
                    b"",
                ),
            }
        ),
    )

    assert result == ProviderReadinessResult(
        provider="antigravity",
        transport="cli",
        ready=True,
        reason_code="ready",
        model_aliases=[],
    )


def test_grok_cli_missing_binary_or_headless_options_fails_closed():
    assert probe_grok_cli(executable_resolver=lambda _name: None).reason_code == "protocol_unavailable"

    result = probe_grok_cli(
        executable_resolver=lambda name: "/bin/grok",
        command_runner=command_runner(
            {
                ("/bin/grok", "--version"): CommandResult(0, b"grok 1", b""),
                ("/bin/grok", "--help"): CommandResult(0, b"Usage: grok chat", b""),
            }
        ),
    )
    assert result.ready is False
    assert result.reason_code == "protocol_unavailable"


def test_cli_probe_timeout_or_oversize_cleans_up_and_fails_as_protocol_unavailable():
    calls: list[str] = []

    def run(command, *, timeout_seconds, output_limit_bytes, env):
        calls.append(command[1])
        if command[1] == "--version":
            raise TimeoutError("hung")
        raise AssertionError("help must not run after version timeout")

    result = probe_antigravity_cli(
        executable_resolver=lambda name: "/bin/agy",
        command_runner=run,
    )

    assert calls == ["--version"]
    assert result.ready is False
    assert result.reason_code == "protocol_unavailable"


def test_run_command_timeout_cleans_up_process_promptly():
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=0.1,
            output_limit_bytes=1024,
            env={},
        )

    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com:8317", "protocol_unavailable"),
        ("http://127.0.0.1:8317", "proxy_unavailable"),
    ],
)
def test_openai_compatible_rejects_non_loopback_or_proxy_failures(url: str, reason: str):
    def opener(_url, *, timeout_seconds, output_limit_bytes):
        raise OSError("connection refused Bearer cbm_worker_secret")

    result = probe_grok_openai_compatible(base_url=url, opener=opener)
    assert result.ready is False
    assert result.reason_code == reason
    assert result.model_aliases == []


def test_openai_compatible_extracts_bounded_model_ids_and_requires_grok_build():
    payload = {
        "object": "list",
        "data": [
            {"id": "grok-build-0.1", "object": "model", "created": 1},
            {"id": "grok-build-0.1", "object": "model"},
            {"id": "grok-preview"},
            {"id": "x" * 200, "object": "model"},
        ]
    }

    result = probe_grok_openai_compatible(
        opener=lambda url, *, timeout_seconds, output_limit_bytes: json.dumps(payload).encode(),
    )

    assert result.ready is True
    assert result.reason_code == "ready"
    assert result.model_aliases == ["grok-build-0.1", "grok-preview", "x" * 96]

    missing = probe_grok_openai_compatible(
        opener=lambda url, *, timeout_seconds, output_limit_bytes: (
            b'{"object":"list","data":[{"id":"other","object":"model"}]}'
        ),
    )
    assert missing.ready is False
    assert missing.reason_code == "model_unavailable"


def test_provider_target_probes_configured_openai_compatible_base_url():
    seen: list[str] = []

    def opener(url, *, timeout_seconds, output_limit_bytes):
        seen.append(url)
        return b'{"object":"list","data":[{"id":"grok-build-0.1","object":"model"}]}'

    original = readiness._http_get
    readiness._http_get = opener
    try:
        result = probe_provider_target(
            "grok",
            "openai-compatible",
            base_url="http://127.0.0.1:9321",
        )
    finally:
        readiness._http_get = original

    assert result.ready is True
    assert seen == ["http://127.0.0.1:9321/v1/models"]


@pytest.mark.parametrize(
    "payload",
    [
        ["grok-build-0.1"],
        {"object": "not-list", "data": [{"id": "grok-build-0.1", "object": "model"}]},
        {"object": "list", "data": ["grok-build-0.1"]},
        {"object": "list", "data": [{"id": "", "object": "model"}]},
        {"object": "list", "data": [{"id": "grok-build-0.1", "object": "not-model"}]},
    ],
)
def test_openai_compatible_rejects_non_exact_model_list_schema(payload):
    result = probe_grok_openai_compatible(
        opener=lambda url, *, timeout_seconds, output_limit_bytes: json.dumps(payload).encode(),
    )

    assert result.ready is False
    assert result.reason_code == "proxy_unavailable"


def test_openai_compatible_malformed_json_is_proxy_unavailable():
    result = probe_grok_openai_compatible(
        opener=lambda url, *, timeout_seconds, output_limit_bytes: b'{"data":',
    )

    assert result.ready is False
    assert result.reason_code == "proxy_unavailable"


def test_http_get_uses_no_proxy_opener_and_rejects_redirects(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"object":"list","data":[]}'

    class FakeOpener:
        def open(self, request, *, timeout):
            assert request.full_url == "http://127.0.0.1:8317/v1/models"
            assert timeout == 2
            return FakeResponse()

    captured_handlers = []

    def fake_build_opener(*handlers):
        captured_handlers.extend(handlers)
        return FakeOpener()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setattr(readiness, "build_opener", fake_build_opener, raising=False)

    body = readiness._http_get(
        "http://127.0.0.1:8317/v1/models",
        timeout_seconds=2,
        output_limit_bytes=1024,
    )

    assert body == b'{"object":"list","data":[]}'
    assert any(type(handler).__name__ == "ProxyHandler" for handler in captured_handlers)
    redirect_handler = next(
        handler
        for handler in captured_handlers
        if type(handler).__name__ == "_NoRedirectHandler"
    )
    assert redirect_handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:1") is None
