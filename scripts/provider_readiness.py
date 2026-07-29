"""Pure provider readiness probes for worker-side preflight reporting."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROVIDER_TARGETS: tuple[tuple[str, str], ...] = (
    ("antigravity", "cli"),
    ("grok", "cli"),
    ("codex", "acp"),
    ("claude", "acp"),
    ("cursor", "acp"),
    ("grok", "acp"),
    ("opencode", "acp"),
    ("grok", "openai-compatible"),
)
ACP_PROVIDER_TO_AGENT: dict[str, str] = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok-build",
    "opencode": "opencode",
}
REQUIRED_GROK_MODEL = "grok-build-0.1"
MAX_MODEL_ALIASES = 16
MAX_MODEL_ALIAS_LENGTH = 96
DEFAULT_OPENAI_COMPATIBLE_URL = "http://127.0.0.1:8317"
CLI_TIMEOUT_SECONDS = 2.0
HTTP_TIMEOUT_SECONDS = 2.0
OUTPUT_LIMIT_BYTES = 65_536


@dataclass(frozen=True)
class ProviderReadinessResult:
    provider: str
    transport: str
    ready: bool
    reason_code: str
    model_aliases: list[str] = field(default_factory=list)

    def public_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "transport": self.transport,
            "ready": self.ready,
            "reason_code": self.reason_code,
            "model_aliases": list(self.model_aliases),
        }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[..., CommandResult]
ExecutableResolver = Callable[[str], str | None]
HttpOpener = Callable[..., bytes]


def unavailable(provider: str, transport: str, reason_code: str) -> ProviderReadinessResult:
    return ProviderReadinessResult(
        provider=provider,
        transport=transport,
        ready=False,
        reason_code=reason_code,
        model_aliases=[],
    )


def ready(
    provider: str,
    transport: str,
    *,
    model_aliases: list[str] | None = None,
) -> ProviderReadinessResult:
    return ProviderReadinessResult(
        provider=provider,
        transport=transport,
        ready=True,
        reason_code="ready",
        model_aliases=sanitize_model_aliases(model_aliases or []),
    )


def sanitize_model_aliases(values: list[str]) -> list[str]:
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
        alias = alias[:MAX_MODEL_ALIAS_LENGTH]
        if alias in seen:
            continue
        aliases.append(alias)
        seen.add(alias)
        if len(aliases) >= MAX_MODEL_ALIASES:
            break
    return aliases


def probe_antigravity_cli(
    *,
    executable_resolver: ExecutableResolver = shutil.which,
    command_runner: CommandRunner | None = None,
) -> ProviderReadinessResult:
    executable = executable_resolver("agy")
    if not executable:
        return unavailable("antigravity", "cli", "protocol_unavailable")
    help_text = _probe_cli_help(executable, command_runner=command_runner)
    if help_text is None:
        return unavailable("antigravity", "cli", "protocol_unavailable")
    lowered = help_text.lower()
    has_model_listing = "models" in lowered and ("list" in lowered or "ls" in lowered)
    if "--print" in lowered and "--output-format" in lowered and has_model_listing:
        return ready("antigravity", "cli")
    return unavailable("antigravity", "cli", "protocol_unavailable")


def probe_grok_cli(
    *,
    executable_resolver: ExecutableResolver = shutil.which,
    command_runner: CommandRunner | None = None,
) -> ProviderReadinessResult:
    executable = executable_resolver("grok")
    if not executable:
        return unavailable("grok", "cli", "protocol_unavailable")
    help_text = _probe_cli_help(executable, command_runner=command_runner)
    if help_text is None:
        return unavailable("grok", "cli", "protocol_unavailable")
    lowered = help_text.lower()
    has_single_output = any(
        flag in lowered for flag in ("--prompt", "--single", "--headless", "--print")
    )
    has_model_option = "--model" in lowered or " -m" in lowered
    if has_single_output and has_model_option:
        return ready("grok", "cli")
    return unavailable("grok", "cli", "protocol_unavailable")


def acp_agent_for_provider(provider: str) -> str | None:
    return ACP_PROVIDER_TO_AGENT.get(str(provider or "").strip())


def acp_provider_result_from_agent_preflight(
    provider: str,
    result: dict[str, Any] | None,
) -> ProviderReadinessResult:
    if acp_agent_for_provider(provider) is None:
        return unavailable(provider, "acp", "protocol_unavailable")
    if not result:
        return unavailable(provider, "acp", "protocol_unavailable")
    if bool(result.get("ready")):
        aliases = [REQUIRED_GROK_MODEL] if provider == "grok" else []
        return ready(provider, "acp", model_aliases=aliases)
    reason = str(result.get("reason_code") or "")
    if reason == "auth_required":
        return unavailable(provider, "acp", "auth_required")
    return unavailable(provider, "acp", "protocol_unavailable")


def probe_grok_openai_compatible(
    *,
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    opener: HttpOpener | None = None,
) -> ProviderReadinessResult:
    parsed = urlparse(str(base_url or "").strip())
    if not _is_loopback_http(parsed):
        return unavailable("grok", "openai-compatible", "protocol_unavailable")
    url = urljoin(base_url.rstrip("/") + "/", "v1/models")
    try:
        body = (opener or _http_get)(
            url,
            timeout_seconds=HTTP_TIMEOUT_SECONDS,
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
        )
    except Exception:  # noqa: BLE001 - publish only public reason codes
        return unavailable("grok", "openai-compatible", "proxy_unavailable")
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
        aliases = _extract_model_ids(payload)
    except Exception:  # noqa: BLE001 - malformed proxy/schema is public proxy failure
        return unavailable("grok", "openai-compatible", "proxy_unavailable")
    aliases = sanitize_model_aliases(aliases)
    if REQUIRED_GROK_MODEL not in aliases:
        return unavailable("grok", "openai-compatible", "model_unavailable")
    return ready("grok", "openai-compatible", model_aliases=aliases)


def probe_provider_target(
    provider: str,
    transport: str,
    *,
    acp_result: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> ProviderReadinessResult:
    if (provider, transport) == ("antigravity", "cli"):
        return probe_antigravity_cli()
    if (provider, transport) == ("grok", "cli"):
        return probe_grok_cli()
    if transport == "acp":
        return acp_provider_result_from_agent_preflight(provider, acp_result)
    if (provider, transport) == ("grok", "openai-compatible"):
        return probe_grok_openai_compatible(
            base_url=base_url or DEFAULT_OPENAI_COMPATIBLE_URL,
        )
    return unavailable(provider, transport, "protocol_unavailable")


def _probe_cli_help(
    executable: str,
    *,
    command_runner: CommandRunner | None = None,
) -> str | None:
    runner = command_runner or run_command
    env = _minimal_env()
    try:
        version = runner(
            [executable, "--version"],
            timeout_seconds=CLI_TIMEOUT_SECONDS,
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            env=env,
        )
        if version.returncode != 0 or _too_large(version):
            return None
        help_result = runner(
            [executable, "--help"],
            timeout_seconds=CLI_TIMEOUT_SECONDS,
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            env=env,
        )
        if help_result.returncode != 0 or _too_large(help_result):
            return None
    except Exception:  # noqa: BLE001 - readiness reports only public reason codes
        return None
    return (help_result.stdout + b"\n" + help_result.stderr).decode(
        "utf-8", errors="replace"
    )


def run_command(
    command: list[str],
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
    env: dict[str, str],
) -> CommandResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise TimeoutError("provider CLI probe timed out") from exc
    if len(stdout) + len(stderr) > output_limit_bytes:
        return CommandResult(process.returncode or 1, stdout[:output_limit_bytes], b"")
    return CommandResult(process.returncode or 0, stdout, stderr)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, OSError):
        try:
            process.terminate()
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, OSError):
            try:
                process.kill()
            except ProcessLookupError:
                return
        process.wait(timeout=1)


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _too_large(result: CommandResult) -> bool:
    return len(result.stdout) + len(result.stderr) > OUTPUT_LIMIT_BYTES


def _is_loopback_http(parsed) -> bool:
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _http_get(
    url: str,
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> bytes:
    if not _is_loopback_http(urlparse(url)):
        raise ValueError("provider proxy URL must be loopback")
    request = Request(url, method="GET")
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:
        status = int(response.getcode()) if hasattr(response, "getcode") else 200
        if 300 <= status < 400:
            raise ValueError("provider proxy redirects are not allowed")
        body = response.read(output_limit_bytes + 1)
    if len(body) > output_limit_bytes:
        raise ValueError("provider proxy response exceeded size limit")
    return body


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _extract_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    if payload.get("object") != "list":
        raise ValueError("model response object must be list")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("model response data must be a list")
    ids: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("model response rows must be objects")
        if "object" in item and item.get("object") != "model":
            raise ValueError("model response row object must be model")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model response row id must be a non-empty string")
        ids.append(model_id)
    return ids
