"""Bounded Orca IDE adapter for the Agent Browser workspace.

Invokes only allowlisted ``orca-ide`` / ``orca`` operations with argv arrays
(never ``shell=True``). Tracks terminal handles this process created, redacts
secrets from captured output, and enforces timeouts and output bounds.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

AgentCli = Literal["cursor-agent", "grok", "codex"]

ALLOWED_AGENT_CLIS: frozenset[str] = frozenset({"cursor-agent", "grok", "codex"})

# Operations the Manager may invoke. Values are fixed argv prefixes after the
# orca binary; dynamic flags are appended only from validated kwargs.
ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "status",
        "worktree.current",
        "worktree.show",
        "terminal.create",
        "terminal.read",
        "terminal.send",
        "terminal.close",
        "terminal.show",
    }
)

# Honest capability surface: Orca exposes close/stop, not pause/resume.
SUPPORTED_SESSION_ACTIONS: frozenset[str] = frozenset(
    {"start", "read", "send", "close"}
)
UNSUPPORTED_SESSION_ACTIONS: frozenset[str] = frozenset({"pause", "resume"})

DEFAULT_ORCA_BIN = "/home/coder/.local/bin/orca-ide"
DEFAULT_TIMEOUT_SECONDS = 30.0
CREATE_TIMEOUT_SECONDS = 60.0
READINESS_TIMEOUT_SECONDS = 8.0
MAX_OUTPUT_CHARS = 120_000
MAX_SEND_CHARS = 16_000
MAX_READ_LIMIT = 2_000
TERMINAL_HANDLE_RE = re.compile(r"^term_[A-Za-z0-9_-]+$")
SESSION_ID_RE = re.compile(r"^orca_[A-Za-z0-9_-]+$")
WRAPPER_SCRIPT_NAME = "orca_agent_cli.sh"
DEFAULT_WRAPPER_PATH = str(
    Path(__file__).resolve().parents[1] / "scripts" / WRAPPER_SCRIPT_NAME
)
DEFAULT_WORKTREE_SELECTOR = "path:/home/coder/vk-repos/CloakBrowser-Manager-browser-use"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-+=/]{8,})"),
    re.compile(r"(?i)(?<![A-Za-z0-9_-])(?:cbm_agent_|cbm_run_|cbm_worker_|cbm_lease_|cbm_session_)[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|token)\s*[:=]\s*([^\s\"']+)"),
    re.compile(r"(?i)(https?://)([^:@\s/]+):([^@\s/]+)@"),
    re.compile(r"(?i)(CURSOR_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|CBM_AGENT_KEY|AUTH_TOKEN)\s*=\s*([^\s\"']+)"),
)


class OrcaAdapterError(Exception):
    """Base adapter error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class OrcaOperationNotAllowed(OrcaAdapterError):
    def __init__(self, operation: str):
        super().__init__(
            "operation_not_allowed",
            f"Orca operation not allowlisted: {operation}",
            status_code=400,
        )


class OrcaTimeoutError(OrcaAdapterError):
    def __init__(self, operation: str):
        super().__init__(
            "orca_timeout",
            f"Orca operation timed out: {operation}",
            status_code=504,
        )


class OrcaSessionNotFound(OrcaAdapterError):
    def __init__(self, session_id: str):
        super().__init__(
            "orca_session_not_found",
            "Orca session not found",
            status_code=404,
        )
        self.session_id = session_id


class OrcaHandleInvalid(OrcaAdapterError):
    def __init__(self, handle: str | None = None):
        super().__init__(
            "orca_handle_invalid",
            "Terminal handle is not owned by this adapter",
            status_code=404,
        )
        self.handle = handle


@dataclass
class OrcaSession:
    id: str
    profile_id: str
    sandbox_id: str
    agent: AgentCli
    terminal_handle: str
    owner_key: str
    worktree_selector: str
    status: Literal["starting", "running", "closed", "error"] = "starting"
    created_at: float = field(default_factory=time.time)
    closed_at: float | None = None
    last_error: str | None = None
    read_cursor: int = 0
    capabilities: dict[str, bool] = field(
        default_factory=lambda: {
            "start": True,
            "read": True,
            "send": True,
            "close": True,
            "pause": False,
            "resume": False,
        }
    )


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def resolve_orca_bin(configured: str | None = None) -> str:
    """Resolve the orca binary path without shell expansion."""
    candidate = (configured or os.environ.get("CBM_ORCA_BIN") or DEFAULT_ORCA_BIN).strip()
    path = Path(candidate)
    if not path.is_file():
        # Fall back to PATH lookup of the basename only (still argv-safe).
        which = _which(path.name)
        if which:
            return which
        raise OrcaAdapterError(
            "orca_unavailable",
            "Orca CLI binary is not available",
            status_code=503,
        )
    return str(path.resolve())


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def validate_agent_cli(agent: str) -> AgentCli:
    if agent not in ALLOWED_AGENT_CLIS:
        raise OrcaAdapterError(
            "agent_not_allowed",
            "Agent CLI must be one of: cursor-agent, grok, codex",
            status_code=400,
        )
    return agent  # type: ignore[return-value]


def resolve_agent_wrapper(configured: str | None = None) -> Path:
    """Resolve the fixed repo wrapper script; reject unexpected names."""
    candidate = (
        configured
        or os.environ.get("CBM_ORCA_AGENT_WRAPPER")
        or DEFAULT_WRAPPER_PATH
    ).strip()
    path = Path(candidate)
    if path.name != WRAPPER_SCRIPT_NAME:
        raise OrcaAdapterError(
            "wrapper_not_allowed",
            "Orca agent wrapper must be scripts/orca_agent_cli.sh",
            status_code=500,
        )
    return path


def build_agent_launch_command(agent: str, *, wrapper: str | Path | None = None) -> str:
    """Build the terminal.create --command text for the fixed wrapper + agent.

    The agent token is validated against the allowlist before interpolation so
    the command string cannot carry arbitrary shell tokens.
    """
    agent_cli = validate_agent_cli(agent)
    wrapper_path = resolve_agent_wrapper(str(wrapper) if wrapper is not None else None)
    # Absolute path preferred; still argv-safe when Orca splits the command text.
    return f"{wrapper_path.as_posix()} {agent_cli}"


def validate_terminal_handle(handle: str) -> str:
    cleaned = str(handle or "").strip()
    if not TERMINAL_HANDLE_RE.fullmatch(cleaned):
        raise OrcaHandleInvalid(cleaned or None)
    return cleaned


def redact_secrets(text: str) -> str:
    """Redact bearer tokens, agent keys, proxy credentials, and common secrets."""
    if not isinstance(text, str) or not text:
        return ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2 and "https?://" in pattern.pattern:
            redacted = pattern.sub(r"\1[redacted]:[redacted]@", redacted)
        elif pattern.groups >= 2:
            redacted = pattern.sub(lambda m: f"{m.group(1)}[redacted]", redacted)
        else:
            redacted = pattern.sub("[redacted]", redacted)
    if len(redacted) > MAX_OUTPUT_CHARS:
        redacted = redacted[:MAX_OUTPUT_CHARS] + "\n…[truncated]"
    return redacted


def build_initial_context(*, profile_id: str, agent: AgentCli, base_url_hint: str | None = None) -> str:
    """Prompt preamble that steers the CLI toward the repo control plane."""
    base = (base_url_hint or os.environ.get("CBM_BASE_URL") or "http://127.0.0.1:18115").rstrip("/")
    return (
        f"CloakBrowser Manager Orca session context.\n"
        f"Selected profile_id={profile_id}.\n"
        f"Agent CLI={agent}.\n"
        f"You MUST steer the already-running selected profile only through the "
        f"repo-local CloakBrowser control skill "
        f"(.agents/skills/cloakbrowser-orca-control/SKILL.md).\n"
        f"For immediate inspect/navigate/click/fill/text/screenshot on that "
        f"profile, prefer scripts/cbm_browser_ctl.py.\n"
        f"For longer Browser-Use runs, use scripts/cbm_agent_ctl.py task/run "
        f"APIs (public /api/task-sessions/.../runs); those runs require a "
        f"VCVM Browser-Use worker claim — queuing a run alone is not execution proof.\n"
        f"Do not guess shell browser launches, do not call Browser-Use Cloud, "
        f"and do not invent CDP ports.\n"
        f"Auth: use CBM_AGENT_KEY_FILE (never print key contents). "
        f"Base URL: {base}\n"
        f"Confirm you will use the control skill for profile {profile_id}."
    )


def build_argv(
    operation: str,
    *,
    orca_bin: str,
    terminal: str | None = None,
    worktree: str | None = None,
    command: str | None = None,
    title: str | None = None,
    text: str | None = None,
    enter: bool = False,
    cursor: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """Assemble an allowlisted argv list. Raises if the operation is unknown."""
    if operation not in ALLOWED_OPERATIONS:
        raise OrcaOperationNotAllowed(operation)

    argv: list[str] = [orca_bin]
    if operation == "status":
        argv.extend(["status", "--json"])
        return argv
    if operation == "worktree.current":
        argv.extend(["worktree", "current", "--json"])
        return argv
    if operation == "worktree.show":
        argv.extend(["worktree", "show", "--json"])
        if worktree:
            argv.extend(["--worktree", worktree])
        return argv
    if operation == "terminal.create":
        argv.extend(["terminal", "create", "--json"])
        if worktree:
            argv.extend(["--worktree", worktree])
        if title:
            argv.extend(["--title", title])
        if command:
            argv.extend(["--command", command])
        return argv
    if operation == "terminal.read":
        argv.extend(["terminal", "read", "--json"])
        if terminal:
            argv.extend(["--terminal", validate_terminal_handle(terminal)])
        if cursor is not None:
            argv.extend(["--cursor", str(int(cursor))])
        if limit is not None:
            argv.extend(["--limit", str(int(limit))])
        return argv
    if operation == "terminal.send":
        argv.extend(["terminal", "send", "--json"])
        if terminal:
            argv.extend(["--terminal", validate_terminal_handle(terminal)])
        if text is not None:
            argv.extend(["--text", text])
        if enter:
            argv.append("--enter")
        return argv
    if operation == "terminal.close":
        argv.extend(["terminal", "close", "--json"])
        if terminal:
            argv.extend(["--terminal", validate_terminal_handle(terminal)])
        return argv
    if operation == "terminal.show":
        argv.extend(["terminal", "show", "--json"])
        if terminal:
            argv.extend(["--terminal", validate_terminal_handle(terminal)])
        return argv
    raise OrcaOperationNotAllowed(operation)


def default_runner(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def _parse_json_payload(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OrcaAdapterError(
            "orca_invalid_json",
            "Orca returned non-JSON output",
            status_code=502,
        ) from exc
    if not isinstance(payload, dict):
        raise OrcaAdapterError(
            "orca_invalid_json",
            "Orca returned a non-object JSON payload",
            status_code=502,
        )
    return payload


def _extract_terminal_handle(payload: Mapping[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, dict):
        terminal = result.get("terminal")
        if isinstance(terminal, dict) and terminal.get("handle"):
            return validate_terminal_handle(str(terminal["handle"]))
        if result.get("handle"):
            return validate_terminal_handle(str(result["handle"]))
    if payload.get("handle"):
        return validate_terminal_handle(str(payload["handle"]))
    raise OrcaAdapterError(
        "orca_missing_handle",
        "Orca create did not return a terminal handle",
        status_code=502,
    )


def _extract_output_text(payload: Mapping[str, Any]) -> tuple[str, int | None]:
    result = payload.get("result")
    if not isinstance(result, dict):
        result = payload
    terminal = result.get("terminal")
    output_source = terminal if isinstance(terminal, dict) else result
    chunks: list[str] = []
    next_cursor: int | None = None
    if isinstance(output_source.get("output"), str):
        chunks.append(output_source["output"])
    elif isinstance(output_source.get("text"), str):
        chunks.append(output_source["text"])
    elif isinstance(output_source.get("preview"), str):
        chunks.append(output_source["preview"])
    lines = output_source.get("tail") or output_source.get("lines") or output_source.get("rows")
    if isinstance(lines, list):
        for line in lines:
            if isinstance(line, str):
                chunks.append(line)
            elif isinstance(line, dict):
                text = line.get("text") or line.get("content") or line.get("line")
                if isinstance(text, str):
                    chunks.append(text)
    if "nextCursor" in output_source and output_source["nextCursor"] is not None:
        try:
            next_cursor = int(output_source["nextCursor"])
        except (TypeError, ValueError):
            next_cursor = None
    elif "next_cursor" in output_source and output_source["next_cursor"] is not None:
        try:
            next_cursor = int(output_source["next_cursor"])
        except (TypeError, ValueError):
            next_cursor = None
    return "\n".join(chunks), next_cursor


class OrcaAdapter:
    """Process-local session registry backed by allowlisted Orca CLI calls."""

    def __init__(
        self,
        *,
        orca_bin: str | None = None,
        runner: Runner | None = None,
        worktree_selector: str | None = None,
        base_url_hint: str | None = None,
        agent_wrapper: str | None = None,
        agent_key_file: str | None = None,
        probe_runtime: bool | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._orca_bin_override = orca_bin
        self._runner = runner or default_runner
        self._worktree_selector = worktree_selector
        self._base_url_hint = base_url_hint
        self._agent_wrapper = agent_wrapper
        self._agent_key_file = agent_key_file
        # Tests that inject a fake binary skip live Orca probes by default.
        if probe_runtime is None:
            self._probe_runtime = orca_bin is None
        else:
            self._probe_runtime = probe_runtime
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._sessions: dict[str, OrcaSession] = {}
        self._handles: set[str] = set()

    def _resolved_worktree(self) -> str:
        return (
            self._worktree_selector
            or os.environ.get("CBM_ORCA_WORKTREE")
            or DEFAULT_WORKTREE_SELECTOR
        )

    def _check_status_ready(self) -> list[str]:
        try:
            payload = self.invoke("status", timeout=READINESS_TIMEOUT_SECONDS)
        except OrcaAdapterError:
            return ["Orca runtime status check failed"]
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        notes: list[str] = []
        if not runtime.get("reachable"):
            notes.append("Orca runtime is not reachable")
        state = str(runtime.get("state") or "")
        if state and state != "ready":
            notes.append("Orca runtime is not ready")
        return notes

    def _check_worktree_ready(self) -> list[str]:
        selector = self._resolved_worktree()
        try:
            payload = self.invoke(
                "worktree.show",
                worktree=selector,
                timeout=READINESS_TIMEOUT_SECONDS,
            )
        except OrcaAdapterError:
            return ["configured Orca worktree is not registered"]
        if payload.get("ok") is False:
            return ["configured Orca worktree is not registered"]
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        worktree_obj = result.get("worktree") if isinstance(result.get("worktree"), dict) else result
        path = str((worktree_obj or {}).get("path") or "")
        expected = "/home/coder/vk-repos/CloakBrowser-Manager-browser-use"
        if path and path != expected and selector == DEFAULT_WORKTREE_SELECTOR:
            return ["configured Orca worktree path does not match the registered checkout"]
        return []

    def capabilities(self) -> dict[str, Any]:
        """Container-side readiness only (no host-only Path probes).

        The Manager container deliberately does not mount the host wrapper or
        scoped agent key. ``start_session`` still sends the fixed host wrapper
        path through host Orca ``terminal.create``. Wrapper/key readiness is
        enforced fail-closed by ``scripts/vcvm_orca_preflight.py`` before
        compose — not by container-local filesystem checks here.
        """
        baseline_notes = [
            "pause/resume are not exposed because Orca terminal APIs do not support them",
            "only terminal handles created by this adapter may be read/sent/closed",
            "close is owner-only; admins cannot close another owner's Orca session",
            "available reflects container-reachable Orca binary, status, and registered worktree only",
            "host wrapper and agent key readiness are enforced by vcvm_orca_preflight before compose; they are not mounted into the Manager container",
        ]
        problems: list[str] = []

        try:
            if not self._orca_bin_override:
                resolve_orca_bin(None)
        except OrcaAdapterError:
            problems.append("Orca CLI binary is not available")

        if self._probe_runtime and "Orca CLI binary is not available" not in problems:
            problems.extend(self._check_status_ready())
            # Still attempt worktree show when status is merely "not ready" so notes stay complete.
            problems.extend(self._check_worktree_ready())

        available = not problems
        notes = baseline_notes + problems
        return {
            "available": available,
            "orca_bin": self._orca_bin_override or os.environ.get("CBM_ORCA_BIN") or DEFAULT_ORCA_BIN,
            "agents": sorted(ALLOWED_AGENT_CLIS),
            "operations": sorted(ALLOWED_OPERATIONS),
            "actions": {
                "start": True,
                "read": True,
                "send": True,
                "close": True,
                "pause": False,
                "resume": False,
            },
            "notes": notes,
        }

    def _bin(self) -> str:
        # Explicit overrides (tests / injected runners) skip existence checks so
        # argv assembly stays deterministic without requiring a real binary.
        if self._orca_bin_override:
            return self._orca_bin_override.strip()
        return resolve_orca_bin(None)

    def invoke(
        self,
        operation: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        argv = build_argv(operation, orca_bin=self._bin(), **kwargs)
        # Defense in depth: never allow a shell string argv.
        if any(not isinstance(part, str) for part in argv):
            raise OrcaAdapterError("invalid_argv", "argv must be strings", status_code=500)
        bound = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        try:
            completed = self._runner(argv, bound)
        except subprocess.TimeoutExpired as exc:
            raise OrcaTimeoutError(operation) from exc
        raw_stdout = completed.stdout or ""
        raw_stderr = completed.stderr or ""
        payload = _parse_json_payload(raw_stdout) if raw_stdout.strip().startswith("{") else {}
        stderr = redact_secrets(raw_stderr)
        if completed.returncode != 0:
            message = ""
            if isinstance(payload.get("error"), dict):
                message = str(payload["error"].get("message") or "")
            message = message or stderr or f"Orca {operation} failed"
            raise OrcaAdapterError(
                "orca_command_failed",
                redact_secrets(message)[:500],
                status_code=502,
            )
        if payload.get("ok") is False:
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            message = str((err or {}).get("message") or f"Orca {operation} failed")
            raise OrcaAdapterError(
                "orca_command_failed",
                redact_secrets(message)[:500],
                status_code=502,
            )
        return payload

    def start_session(
        self,
        *,
        profile_id: str,
        sandbox_id: str,
        agent: str,
        owner_key: str,
        prompt: str | None = None,
        worktree_selector: str | None = None,
    ) -> OrcaSession:
        agent_cli = validate_agent_cli(agent)
        selector = (
            worktree_selector
            or self._worktree_selector
            or os.environ.get("CBM_ORCA_WORKTREE")
            or DEFAULT_WORKTREE_SELECTOR
        )
        title = f"cbm-{agent_cli}-{profile_id[:12]}"
        launch_command = build_agent_launch_command(
            agent_cli,
            wrapper=self._agent_wrapper,
        )
        create_payload = self.invoke(
            "terminal.create",
            timeout=CREATE_TIMEOUT_SECONDS,
            worktree=selector,
            command=launch_command,
            title=title,
        )
        handle = _extract_terminal_handle(create_payload)
        session_id = f"orca_{secrets.token_hex(12)}"
        session = OrcaSession(
            id=session_id,
            profile_id=profile_id,
            sandbox_id=sandbox_id,
            agent=agent_cli,
            terminal_handle=handle,
            owner_key=owner_key,
            worktree_selector=selector,
            status="running",
        )
        with self._lock:
            self._sessions[session_id] = session
            self._handles.add(handle)

        context = build_initial_context(
            profile_id=profile_id,
            agent=agent_cli,
            base_url_hint=self._base_url_hint,
        )
        if prompt and prompt.strip():
            context = f"{context}\n\nOperator prompt:\n{prompt.strip()[:MAX_SEND_CHARS]}"
        try:
            self.invoke(
                "terminal.send",
                terminal=handle,
                text=context,
                enter=True,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except OrcaAdapterError as exc:
            session.status = "error"
            session.last_error = exc.message
        return session

    def get_session(self, session_id: str, *, owner_key: str | None = None) -> OrcaSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise OrcaSessionNotFound(session_id)
            if owner_key is not None and session.owner_key != owner_key:
                raise OrcaSessionNotFound(session_id)
            return session

    def assert_owned_handle(self, handle: str) -> str:
        cleaned = validate_terminal_handle(handle)
        with self._lock:
            if cleaned not in self._handles:
                raise OrcaHandleInvalid(cleaned)
        return cleaned

    def read_output(
        self,
        session_id: str,
        *,
        owner_key: str,
        cursor: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id, owner_key=owner_key)
        handle = self.assert_owned_handle(session.terminal_handle)
        read_cursor = session.read_cursor if cursor is None else max(0, int(cursor))
        read_limit = MAX_READ_LIMIT if limit is None else max(1, min(int(limit), MAX_READ_LIMIT))
        payload = self.invoke(
            "terminal.read",
            terminal=handle,
            cursor=read_cursor,
            limit=read_limit,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        text, next_cursor = _extract_output_text(payload)
        text = redact_secrets(text)
        if next_cursor is not None:
            session.read_cursor = next_cursor
        else:
            session.read_cursor = read_cursor + max(1, text.count("\n") + (1 if text else 0))
        return {
            "session_id": session.id,
            "terminal_handle": handle,
            "cursor": read_cursor,
            "next_cursor": session.read_cursor,
            "output": text,
            "status": session.status,
            "capabilities": dict(session.capabilities),
        }

    def send_input(
        self,
        session_id: str,
        *,
        owner_key: str,
        text: str,
        enter: bool = True,
    ) -> dict[str, Any]:
        session = self.get_session(session_id, owner_key=owner_key)
        if session.status == "closed":
            raise OrcaAdapterError(
                "orca_session_closed",
                "Orca session is closed",
                status_code=409,
            )
        handle = self.assert_owned_handle(session.terminal_handle)
        cleaned = str(text or "")
        if len(cleaned) > MAX_SEND_CHARS:
            raise OrcaAdapterError(
                "send_too_large",
                f"Send text exceeds {MAX_SEND_CHARS} characters",
                status_code=400,
            )
        self.invoke(
            "terminal.send",
            terminal=handle,
            text=cleaned,
            enter=enter,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        return {
            "session_id": session.id,
            "ok": True,
            "status": session.status,
            "capabilities": dict(session.capabilities),
        }

    def close_session(self, session_id: str, *, owner_key: str) -> OrcaSession:
        session = self.get_session(session_id, owner_key=owner_key)
        handle = self.assert_owned_handle(session.terminal_handle)
        try:
            self.invoke(
                "terminal.close",
                terminal=handle,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except OrcaAdapterError as exc:
            # Treat missing tabs as already closed so stop is idempotent.
            if "tab_not_found" not in exc.message and "not found" not in exc.message.lower():
                session.status = "error"
                session.last_error = exc.message
                raise
        session.status = "closed"
        session.closed_at = self._clock()
        with self._lock:
            self._handles.discard(handle)
        return session

    def public_session(self, session: OrcaSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "profile_id": session.profile_id,
            "sandbox_id": session.sandbox_id,
            "agent": session.agent,
            "terminal_handle": session.terminal_handle,
            "status": session.status,
            "created_at": session.created_at,
            "closed_at": session.closed_at,
            "last_error": session.last_error,
            "capabilities": dict(session.capabilities),
            "connection": {
                "runtime": "orca",
                "worktree": session.worktree_selector,
                "owned": True,
            },
        }


# Process-wide adapter used by FastAPI routes.
orca_adapter = OrcaAdapter()
