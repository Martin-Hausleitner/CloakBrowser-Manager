#!/usr/bin/env python3
"""Secrets-safe optional Soniox real-time STT adapter (contract/probe).

Based on official Soniox docs (no third-party SDK required):
  - Python SDK overview: https://soniox.com/docs/sdk/python-SDK
  - Realtime WebSocket: https://soniox.com/docs/api-reference/stt/websocket-api
  - Stable errors: https://soniox.com/docs/api-reference/errors
  - Temporary API keys: https://soniox.com/docs/guides/temporary-api-keys

Default mode is **contract** only: validate host/model/credential *references*
with zero network I/O. Optional ``--live`` probe requires a 0600 key-file and
an injected or built-in transport; emits metrics only (never raw keys).

Typed states:
  ready | auth_required | adapter_unavailable | budget_exhausted
  | transient_failure | policy_denied

Security:
  - never accept raw API key on argv
  - never log/report raw key material
  - key-file must be regular file mode 0600
  - only official HTTPS/WSS Soniox hosts
  - default model stt-rt-v5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Official endpoints / allowlists (current Soniox docs)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "stt-rt-v5"
DEFAULT_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
DEFAULT_API_BASE = "https://api.soniox.com"
TEMP_KEY_PATH = "/v1/auth/temporary-api-key"

ALLOWED_WS_HOSTS: frozenset[str] = frozenset({"stt-rt.soniox.com"})
ALLOWED_API_HOSTS: frozenset[str] = frozenset({"api.soniox.com"})
ALLOWED_MODELS: frozenset[str] = frozenset({"stt-rt-v5"})

DEFAULT_TIMEOUT_S = 10.0
# Finite live-probe timeout bounds (seconds). Reject non-finite, <=0, or > max.
MIN_TIMEOUT_S = 0.0  # exclusive lower bound: must be > 0
MAX_TIMEOUT_S = 60.0
MAX_KEY_BYTES = 8_192
SECRET_REF_RE = re.compile(r"^secretref-[A-Za-z0-9][A-Za-z0-9._-]{2,143}$")

# Option names that mean "raw secret on argv" even without a value pattern match.
FORBIDDEN_ARGV_FLAGS: frozenset[str] = frozenset(
    {
        "--api-key",
        "--apikey",
        "--soniox-api-key",
        "--token",
        "--access-token",
        "--secret",
        "--password",
    }
)

STATE_READY = "ready"
STATE_AUTH_REQUIRED = "auth_required"
STATE_ADAPTER_UNAVAILABLE = "adapter_unavailable"
STATE_BUDGET_EXHAUSTED = "budget_exhausted"
STATE_TRANSIENT_FAILURE = "transient_failure"
STATE_POLICY_DENIED = "policy_denied"

AdapterState = str

BUDGET_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "organization_balance_exhausted",
        "organization_monthly_budget_exhausted",
        "project_monthly_budget_exhausted",
    }
)

AUTH_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "unauthenticated",
        "temp_api_key_expired",
        "temp_api_key_invalid",
    }
)

POLICY_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "permission_denied",
        "temp_api_key_session_expired",
        "temp_api_key_usage_type_mismatch",
    }
)

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+=/]{8,}")
_API_KEY_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|soniox_api_key|token)\b"
    r"(\s*[=:]\s*)([\"']?)([^\s,;\"']+)\3"
)
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
_LONG_SECRET_RE = re.compile(r"\bsk[_-][A-Za-z0-9_]{12,}\b")
_HEXISH_RE = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
# Argv-only: dense secret-like tokens (not paths / secretref / normal options).
_ARGV_DENSE_SECRET_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{24,}$")
_ARGV_JWTISH_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}$")


# ---------------------------------------------------------------------------
# Transport interface (injected for deterministic tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportResult:
    """Metrics-safe transport outcome. Never carry raw secrets here."""

    status_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    latency_ms: int | None = None
    finished: bool = False
    timed_out: bool = False
    malformed: bool = False


class Transport(Protocol):
    def probe_session(
        self,
        *,
        url: str,
        model: str,
        api_key: str,
        timeout_s: float,
    ) -> TransportResult: ...


# ---------------------------------------------------------------------------
# Credential resolution (never returns key into public reports)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialResolution:
    ok: bool
    state: AdapterState
    source: str  # "key_file" | "secret_ref" | "none"
    api_key: str | None = None  # private; never serialize
    detail: str = ""


def validate_ws_url(url: str) -> tuple[bool, str]:
    """Return (ok, reason). Only official wss://stt-rt.soniox.com hosts."""
    raw = (url or "").strip()
    if not raw:
        return False, "origin_denied"
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "origin_denied"
    if parsed.scheme != "wss":
        return False, "origin_denied"
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_WS_HOSTS:
        return False, "origin_denied"
    if parsed.username or parsed.password:
        return False, "origin_denied"
    return True, "ok"


def validate_api_base(url: str) -> tuple[bool, str]:
    raw = (url or "").strip()
    if not raw:
        return False, "origin_denied"
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "origin_denied"
    if parsed.scheme != "https":
        return False, "origin_denied"
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_API_HOSTS:
        return False, "origin_denied"
    if parsed.username or parsed.password:
        return False, "origin_denied"
    return True, "ok"


def validate_model(model: str) -> tuple[bool, str]:
    value = (model or "").strip()
    if value not in ALLOWED_MODELS:
        return False, "model_denied"
    return True, "ok"


def validate_timeout_s(value: Any) -> tuple[bool, str]:
    """Require a finite timeout in (0, MAX_TIMEOUT_S].

    Rejects NaN, ±inf, non-numeric, <= 0, and values above MAX_TIMEOUT_S.
    """
    if isinstance(value, bool):
        return False, "timeout_invalid"
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return False, "timeout_invalid"
    if not math.isfinite(timeout):
        return False, "timeout_not_finite"
    if timeout <= MIN_TIMEOUT_S:
        return False, "timeout_not_positive"
    if timeout > MAX_TIMEOUT_S:
        return False, "timeout_too_large"
    return True, "ok"


def validate_secret_ref(secret_ref: str) -> tuple[bool, str]:
    value = (secret_ref or "").strip()
    if not value:
        return False, "missing"
    if SECRET_REF_RE.fullmatch(value) is None:
        return False, "invalid_secret_ref"
    return True, "ok"


def _argv_value_candidates(token: str) -> list[str]:
    """Return the full token and any ``--name=value`` value segment."""
    text = str(token)
    candidates = [text]
    if text.startswith("-") and "=" in text:
        candidates.append(text.split("=", 1)[1])
    return candidates


def token_looks_like_raw_secret(token: str) -> bool:
    """True when a single argv token/value resembles raw key material."""
    for candidate in _argv_value_candidates(token):
        value = candidate.strip()
        if not value:
            continue
        # Opaque secret references and filesystem paths are not raw keys.
        if SECRET_REF_RE.fullmatch(value):
            continue
        if "/" in value or value.startswith("."):
            # Paths may still embed sk_ / hex secrets — scan substrings only.
            if _LONG_SECRET_RE.search(value) or _BEARER_RE.search(value):
                return True
            if _API_KEY_ASSIGN_RE.search(value):
                return True
            continue
        if _LONG_SECRET_RE.search(value):
            return True
        if _BEARER_RE.search(value):
            return True
        if _API_KEY_ASSIGN_RE.search(value):
            return True
        if _HEXISH_RE.fullmatch(value):
            return True
        if _ARGV_JWTISH_RE.fullmatch(value):
            return True
        # Dense high-entropy-looking blob without path separators.
        if _ARGV_DENSE_SECRET_RE.fullmatch(value) and not value.startswith("--"):
            # Allow known non-secret dense tokens (model ids, hostnames without dots handled above).
            if value in ALLOWED_MODELS:
                continue
            if value.replace(".", "").isalnum() and value.count(".") >= 1:
                continue
            # Flag only when it looks key-like (mixed or long base64-ish), not pure short flags.
            if len(value) >= 32 or value.startswith("sk") or "key" in value.lower():
                return True
    return False


def argv_contains_raw_secret(argv: Sequence[str]) -> bool:
    """Scan every argv token (flags, =values, positionals, unknowns) for secrets."""
    for raw in argv:
        token = str(raw)
        flag_name = token.split("=", 1)[0]
        if flag_name in FORBIDDEN_ARGV_FLAGS:
            return True
        if token_looks_like_raw_secret(token):
            return True
    return False


def emit_raw_key_on_argv_denied() -> None:
    """Print redacted structured policy error; never echo raw argv."""
    print(
        json.dumps(
            {
                "state": STATE_POLICY_DENIED,
                "ready": False,
                "detail": "raw_key_on_argv_denied",
                "provider": "soniox",
            },
            sort_keys=True,
        ),
        file=sys.stdout,
    )
    raise SystemExit(2)


def resolve_key_file(path: str | Path) -> CredentialResolution:
    """Load API key from a 0600 regular file. Never raise with key content."""
    try:
        key_path = Path(path).expanduser()
    except Exception:
        return CredentialResolution(
            ok=False,
            state=STATE_AUTH_REQUIRED,
            source="none",
            detail="key_file_unreadable",
        )
    if key_path.is_symlink():
        return CredentialResolution(
            ok=False,
            state=STATE_POLICY_DENIED,
            source="key_file",
            detail="key_file_symlink_denied",
        )
    if not key_path.exists():
        return CredentialResolution(
            ok=False,
            state=STATE_AUTH_REQUIRED,
            source="none",
            detail="key_file_missing",
        )
    try:
        st = key_path.lstat()
    except OSError:
        return CredentialResolution(
            ok=False,
            state=STATE_AUTH_REQUIRED,
            source="none",
            detail="key_file_unreadable",
        )
    if not stat.S_ISREG(st.st_mode):
        return CredentialResolution(
            ok=False,
            state=STATE_POLICY_DENIED,
            source="key_file",
            detail="key_file_not_regular",
        )
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        return CredentialResolution(
            ok=False,
            state=STATE_POLICY_DENIED,
            source="key_file",
            detail="key_file_mode_not_0600",
        )
    if st.st_size > MAX_KEY_BYTES:
        return CredentialResolution(
            ok=False,
            state=STATE_POLICY_DENIED,
            source="key_file",
            detail="key_file_too_large",
        )
    try:
        raw = key_path.read_text(encoding="utf-8")
    except OSError:
        return CredentialResolution(
            ok=False,
            state=STATE_AUTH_REQUIRED,
            source="none",
            detail="key_file_unreadable",
        )
    api_key = raw.strip()
    if not api_key:
        return CredentialResolution(
            ok=False,
            state=STATE_AUTH_REQUIRED,
            source="key_file",
            detail="key_file_empty",
        )
    return CredentialResolution(
        ok=True,
        state=STATE_READY,
        source="key_file",
        api_key=api_key,
        detail="ok",
    )


def resolve_credentials(
    *,
    secret_ref: str | None = None,
    key_file: str | Path | None = None,
    require_material: bool = False,
) -> CredentialResolution:
    """Resolve secret reference and/or key-file.

    ``require_material=True`` (live probe) needs a readable key-file; secret
    references alone are not enough without a vault resolver.
    """
    if key_file:
        return resolve_key_file(key_file)

    if secret_ref is not None and str(secret_ref).strip():
        ok, reason = validate_secret_ref(str(secret_ref))
        if not ok:
            return CredentialResolution(
                ok=False,
                state=STATE_POLICY_DENIED,
                source="secret_ref",
                detail=reason,
            )
        if require_material:
            return CredentialResolution(
                ok=False,
                state=STATE_AUTH_REQUIRED,
                source="secret_ref",
                detail="secret_ref_no_material_for_live",
            )
        return CredentialResolution(
            ok=True,
            state=STATE_READY,
            source="secret_ref",
            api_key=None,
            detail="ok",
        )

    return CredentialResolution(
        ok=False,
        state=STATE_AUTH_REQUIRED,
        source="none",
        detail="credentials_missing",
    )


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact_text(text: str) -> str:
    """Strip bearer tokens, key assignments, userinfo, and long secret-like runs."""
    if not text:
        return text
    out = _URL_USERINFO_RE.sub(r"\1[REDACTED]:[REDACTED]@", text)
    out = _BEARER_RE.sub("Bearer [REDACTED]", out)
    out = _API_KEY_ASSIGN_RE.sub(r"\1\2\3[REDACTED]\3", out)
    out = _LONG_SECRET_RE.sub("[REDACTED]", out)
    out = _HEXISH_RE.sub("[REDACTED]", out)
    return out


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


# ---------------------------------------------------------------------------
# Error mapping (branch on error_type / status, not message text)
# ---------------------------------------------------------------------------


def map_transport_result(result: TransportResult) -> AdapterState:
    """Map a TransportResult to a typed adapter state."""
    if result.timed_out:
        return STATE_TRANSIENT_FAILURE
    if result.malformed:
        return STATE_ADAPTER_UNAVAILABLE

    error_type = (result.error_type or "").strip().lower()
    code = result.status_code

    if error_type in BUDGET_ERROR_TYPES or code == 402:
        return STATE_BUDGET_EXHAUSTED
    if error_type in AUTH_ERROR_TYPES or code == 401:
        return STATE_AUTH_REQUIRED
    if error_type in POLICY_ERROR_TYPES or code == 403:
        return STATE_POLICY_DENIED
    if code is not None and code >= 500:
        return STATE_TRANSIENT_FAILURE
    if code is not None and code == 429:
        return STATE_TRANSIENT_FAILURE
    if code is not None and 400 <= code < 500:
        # invalid_request / model_not_available etc. — client contract issue
        if error_type in {"model_not_available", "invalid_request"}:
            return STATE_POLICY_DENIED
        return STATE_ADAPTER_UNAVAILABLE

    if result.finished or code in (None, 200, 201):
        # Success frames may omit HTTP status (WS) or use 200.
        if error_type:
            # Unexpected error_type with success-ish code
            return STATE_ADAPTER_UNAVAILABLE
        return STATE_READY

    return STATE_ADAPTER_UNAVAILABLE


# ---------------------------------------------------------------------------
# Built-in live transport (stdlib HTTPS temporary-key probe; metrics only)
# ---------------------------------------------------------------------------


class LiveTempKeyTransport:
    """Optional live probe via POST /v1/auth/temporary-api-key.

    Validates long-lived key material against the official API host without
    opening a WebSocket stream. Response body is never stored; only status,
    error_type, request_id, and latency are retained.
    """

    def __init__(self, *, api_base: str = DEFAULT_API_BASE) -> None:
        ok, _ = validate_api_base(api_base)
        if not ok:
            raise ValueError("api_base origin denied")
        self.api_base = api_base.rstrip("/")

    def probe_session(
        self,
        *,
        url: str,
        model: str,
        api_key: str,
        timeout_s: float,
    ) -> TransportResult:
        # url/model validated by caller; we still re-check api host.
        ok, _ = validate_api_base(self.api_base)
        if not ok:
            return TransportResult(malformed=False, status_code=None, error_type="origin_denied")

        endpoint = f"{self.api_base}{TEMP_KEY_PATH}"
        # Defense in depth: only https to allowlisted Soniox API hosts.
        if urlparse(endpoint).scheme != "https" or urlparse(endpoint).hostname not in ALLOWED_API_HOSTS:
            return TransportResult(status_code=None, error_type="origin_denied")
        payload = json.dumps(
            {
                "usage_type": "transcribe_websocket",
                "expires_in_seconds": 60,
                "single_use": True,
            }
        ).encode("utf-8")
        req = Request(  # noqa: S310 - scheme/host re-validated against ALLOWED_API_HOSTS
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "cbm-soniox-stt-adapter/1.0",
            },
        )
        started = time.monotonic()
        try:
            with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - host allowlisted
                body = resp.read(65_536)
                latency = int((time.monotonic() - started) * 1000)
                status = getattr(resp, "status", None) or resp.getcode()
                request_id = None
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return TransportResult(
                        status_code=int(status) if status else None,
                        malformed=True,
                        latency_ms=latency,
                    )
                if not isinstance(data, dict):
                    return TransportResult(
                        status_code=int(status) if status else None,
                        malformed=True,
                        latency_ms=latency,
                    )
                request_id = _safe_str(data.get("request_id") or data.get("id"))
                # Success: temporary key issued. Do NOT retain api_key field.
                return TransportResult(
                    status_code=int(status) if status else 200,
                    finished=True,
                    latency_ms=latency,
                    request_id=request_id,
                )
        except HTTPError as exc:
            latency = int((time.monotonic() - started) * 1000)
            error_type = None
            request_id = None
            message = None
            try:
                raw = exc.read(65_536)
                data = json.loads(raw.decode("utf-8") or "{}")
                if isinstance(data, dict):
                    error_type = _safe_str(data.get("error_type"))
                    request_id = _safe_str(data.get("request_id"))
                    message = _safe_str(data.get("message") or data.get("error_message"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                error_type = error_type
            return TransportResult(
                status_code=int(exc.code),
                error_type=error_type,
                error_message=redact_text(message) if message else None,
                request_id=request_id,
                latency_ms=latency,
            )
        except TimeoutError:
            latency = int((time.monotonic() - started) * 1000)
            return TransportResult(timed_out=True, latency_ms=latency)
        except URLError as exc:
            latency = int((time.monotonic() - started) * 1000)
            reason = str(getattr(exc, "reason", exc))
            if "timed out" in reason.lower() or "timeout" in reason.lower():
                return TransportResult(timed_out=True, latency_ms=latency)
            return TransportResult(
                status_code=None,
                error_type="service_unavailable",
                error_message=redact_text(reason)[:200],
                latency_ms=latency,
            )
        except Exception as exc:  # noqa: BLE001 - boundary must stay metrics-safe
            latency = int((time.monotonic() - started) * 1000)
            return TransportResult(
                malformed=True,
                error_message=redact_text(str(exc))[:200],
                latency_ms=latency,
            )


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return redact_text(text)[:200]


# ---------------------------------------------------------------------------
# Public probe API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterReport:
    state: AdapterState
    ready: bool
    mode: str  # "contract" | "live"
    model: str
    ws_host: str
    network_used: bool
    credential_source: str
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "provider": "soniox",
            "surface": "stt-rt",
            "state": self.state,
            "ready": self.ready,
            "mode": self.mode,
            "model": self.model,
            "ws_host": self.ws_host,
            "network_used": self.network_used,
            "credential_source": self.credential_source,
            "detail": self.detail,
            "metrics": _redact_value(self.metrics),
        }
        return _redact_value(payload)  # type: ignore[return-value]


def _ws_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def probe(
    *,
    secret_ref: str | None = None,
    key_file: str | Path | None = None,
    model: str = DEFAULT_MODEL,
    ws_url: str = DEFAULT_WS_URL,
    api_base: str = DEFAULT_API_BASE,
    live: bool = False,
    transport: Transport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    allow_builtin_live: bool = False,
) -> AdapterReport:
    """Run contract validation and optional live probe.

    Default (``live=False``): no network, even if a transport is injected.
    Live requires key-file material and either an injected transport or
    ``allow_builtin_live=True`` (CLI ``--live`` path).
    """
    model_value = (model or DEFAULT_MODEL).strip()
    ws = (ws_url or DEFAULT_WS_URL).strip()
    host = _ws_host(ws)

    ok_timeout, timeout_reason = validate_timeout_s(timeout_s)
    if not ok_timeout:
        return AdapterReport(
            state=STATE_POLICY_DENIED,
            ready=False,
            mode="live" if live else "contract",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source="none",
            detail=timeout_reason,
            metrics={},
        )
    timeout_value = float(timeout_s)

    # Origin / model policy first (fail closed before touching secrets deeply).
    ok_ws, ws_reason = validate_ws_url(ws)
    if not ok_ws:
        return AdapterReport(
            state=STATE_POLICY_DENIED,
            ready=False,
            mode="live" if live else "contract",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source="none",
            detail=ws_reason,
            metrics={},
        )

    ok_model, model_reason = validate_model(model_value)
    if not ok_model:
        return AdapterReport(
            state=STATE_POLICY_DENIED,
            ready=False,
            mode="live" if live else "contract",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source="none",
            detail=model_reason,
            metrics={},
        )

    if live:
        ok_api, api_reason = validate_api_base(api_base)
        if not ok_api:
            return AdapterReport(
                state=STATE_POLICY_DENIED,
                ready=False,
                mode="live",
                model=model_value,
                ws_host=host,
                network_used=False,
                credential_source="none",
                detail=api_reason,
                metrics={},
            )

    creds = resolve_credentials(
        secret_ref=secret_ref,
        key_file=key_file,
        require_material=bool(live),
    )
    if not creds.ok:
        return AdapterReport(
            state=creds.state,
            ready=False,
            mode="live" if live else "contract",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source=creds.source,
            detail=creds.detail,
            metrics={},
        )

    if not live:
        # Contract / no-network default: success without transport calls.
        return AdapterReport(
            state=STATE_READY,
            ready=True,
            mode="contract",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source=creds.source,
            detail="contract_ok",
            metrics={
                "latency_ms": None,
                "status_code": None,
                "error_type": None,
                "request_id": None,
            },
        )

    # Live path
    active: Transport | None = transport
    if active is None:
        if not allow_builtin_live:
            return AdapterReport(
                state=STATE_ADAPTER_UNAVAILABLE,
                ready=False,
                mode="live",
                model=model_value,
                ws_host=host,
                network_used=False,
                credential_source=creds.source,
                detail="live_transport_unavailable",
                metrics={},
            )
        try:
            active = LiveTempKeyTransport(api_base=api_base)
        except ValueError:
            return AdapterReport(
                state=STATE_POLICY_DENIED,
                ready=False,
                mode="live",
                model=model_value,
                ws_host=host,
                network_used=False,
                credential_source=creds.source,
                detail="origin_denied",
                metrics={},
            )

    if not creds.api_key:
        return AdapterReport(
            state=STATE_AUTH_REQUIRED,
            ready=False,
            mode="live",
            model=model_value,
            ws_host=host,
            network_used=False,
            credential_source=creds.source,
            detail="credentials_missing_material",
            metrics={},
        )

    result = active.probe_session(
        url=ws,
        model=model_value,
        api_key=creds.api_key,
        timeout_s=timeout_value,
    )
    state = map_transport_result(result)
    metrics = {
        "latency_ms": result.latency_ms,
        "status_code": result.status_code,
        "error_type": result.error_type,
        "request_id": result.request_id,
        "timed_out": result.timed_out,
        "malformed": result.malformed,
        "finished": result.finished,
    }
    # Intentionally omit error_message from public metrics to reduce leakage.
    return AdapterReport(
        state=state,
        ready=(state == STATE_READY),
        mode="live",
        model=model_value,
        ws_host=host,
        network_used=True,
        credential_source=creds.source,
        detail=state,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Report I/O
# ---------------------------------------------------------------------------


def write_report(path: Path | str, report: AdapterReport) -> None:
    """Write metrics-only JSON report with mode 0600."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_public_dict()
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    # Best-effort O_NOFOLLOW when available.
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(out), flags | nofollow, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(out, 0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soniox_stt_adapter",
        description=(
            "Soniox STT adapter contract/probe. Default: no network. "
            "Credentials via --key-file (0600) or --secret-ref only; "
            "never pass raw API keys on argv."
        ),
    )
    parser.add_argument(
        "--secret-ref",
        default=None,
        help="Opaque secret reference (secretref-...); never a raw key",
    )
    parser.add_argument(
        "--key-file",
        default=None,
        help="Path to API key file (must be mode 0600 regular file)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Realtime STT model (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--ws-url",
        default=DEFAULT_WS_URL,
        help="WebSocket URL (official wss host only)",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help="HTTPS API base for optional live temporary-key probe",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live probe (requires --key-file; metrics only)",
    )
    parser.add_argument(
        "--timeout",
        type=str,
        default=str(DEFAULT_TIMEOUT_S),
        help=(
            f"Live probe timeout seconds (default {DEFAULT_TIMEOUT_S}; "
            f"must be finite, > 0, and <= {MAX_TIMEOUT_S})"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for metrics-only JSON report (mode 0600)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Scan every token before argparse so raw secrets never appear in parse errors.
    if argv_contains_raw_secret(argv):
        emit_raw_key_on_argv_denied()

    parser = build_parser()
    # Replace argparse error path so unrecognized options never echo raw tokens.
    def _parser_error(message: str) -> None:  # noqa: ARG001
        print(
            json.dumps(
                {
                    "state": STATE_POLICY_DENIED,
                    "ready": False,
                    "detail": "argv_parse_denied",
                    "provider": "soniox",
                },
                sort_keys=True,
            ),
            file=sys.stdout,
        )
        raise SystemExit(2)

    parser.error = _parser_error  # type: ignore[method-assign]
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code == 0:
            # --help success path
            raise
        raise SystemExit(code) from None

    ok_timeout, timeout_reason = validate_timeout_s(args.timeout)
    if not ok_timeout:
        report = AdapterReport(
            state=STATE_POLICY_DENIED,
            ready=False,
            mode="live" if args.live else "contract",
            model=str(args.model),
            ws_host=_ws_host(str(args.ws_url)),
            network_used=False,
            credential_source="none",
            detail=timeout_reason,
            metrics={},
        )
        _emit(report, args.output)
        return 1
    timeout_s = float(args.timeout)

    if args.live and not args.key_file:
        report = AdapterReport(
            state=STATE_AUTH_REQUIRED,
            ready=False,
            mode="live",
            model=str(args.model),
            ws_host=_ws_host(str(args.ws_url)),
            network_used=False,
            credential_source="none",
            detail="live_requires_key_file",
            metrics={},
        )
        _emit(report, args.output)
        return 1

    report = probe(
        secret_ref=args.secret_ref,
        key_file=args.key_file,
        model=str(args.model),
        ws_url=str(args.ws_url),
        api_base=str(args.api_base),
        live=bool(args.live),
        timeout_s=timeout_s,
        allow_builtin_live=bool(args.live),
    )
    _emit(report, args.output)
    return 0 if report.ready else 1


def _emit(report: AdapterReport, output: str | None) -> None:
    payload = report.to_public_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    # Final belt-and-suspenders: never print anything that still looks like the key.
    text = redact_text(text)
    print(text)
    if output:
        write_report(output, report)


if __name__ == "__main__":
    sys.exit(main())
