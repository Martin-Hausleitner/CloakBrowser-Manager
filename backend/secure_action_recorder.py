"""Reference-only secure action recorder ingestion contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import MutableMapping
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if __package__:
    from .origin_policy import is_top_level_origin_allowed, normalize_origin
else:  # Support direct backend imports.
    from origin_policy import is_top_level_origin_allowed, normalize_origin


RecorderActionKind = Literal[
    "navigate",
    "click",
    "fill_secret_reference",
    "machine_identity_reference",
    "submit",
    "observe",
    "wait",
]
VaultProvider = Literal["vaultwarden", "infisical", "gopass"]
VaultPurpose = Literal["human_credentials", "machine_identity", "local_mode"]

SECRET_REFERENCE_PATTERN = r"^secretref-[A-Za-z0-9][A-Za-z0-9._-]{2,143}$"
_SIGNATURE_PATTERN = r"^sha256=[0-9a-f]{64}$"
_SAFE_SELECTOR_PATTERN = re.compile(r"^[^<>\r\n]{1,500}$")
_AUTH_BEARER_RE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|cookie|totp|otp|api[_-]?key)\s*[:=]"
)
_PROXY_CREDENTIAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@")
_FORBIDDEN_RAW_FIELDS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "cookie",
    "cookies",
    "totp",
    "otp",
    "totp_seed",
    "secret",
    "secrets",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "value",
    "raw_value",
}
MAX_BATCH_AGE_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 60
NONCE_TTL_SECONDS = 600
MAX_NONCE_CACHE_ENTRIES = 4096


class RecorderBatchError(ValueError):
    """Sanitized recorder-batch validation error."""


def _reject_secret_like_text(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{field_name} contains control characters")
    if (
        _AUTH_BEARER_RE.search(cleaned)
        or _SENSITIVE_ASSIGNMENT_RE.search(cleaned)
        or _PROXY_CREDENTIAL_RE.search(cleaned)
    ):
        raise ValueError(f"{field_name} contains forbidden secret-like content")
    return cleaned


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.isoformat()


def _validate_loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ValueError("loopback callback must use http")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("loopback callback must target loopback")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("loopback callback must not include credentials")
    if parsed.port is None or not (1024 <= parsed.port <= 65535):
        raise ValueError("loopback callback must use an explicit high port")
    if not parsed.path or parsed.path == "/":
        raise ValueError("loopback callback path is required")
    if parsed.query or parsed.fragment:
        raise ValueError("loopback callback must not include query or fragment")
    return value


class VaultProviderMetadata(BaseModel):
    """Provider locator metadata. All sensitive material is represented by references."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider: VaultProvider
    purpose: VaultPurpose
    item_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    collection_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    project_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    identity_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    store_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    entry_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    environment: str | None = Field(default=None, min_length=1, max_length=80)
    loopback_callback_url: str | None = Field(default=None, max_length=500)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_secret_like_text(value, field_name="environment")

    @field_validator("loopback_callback_url")
    @classmethod
    def validate_loopback_callback_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_loopback_url(value)

    @model_validator(mode="before")
    @classmethod
    def reject_raw_secret_field_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = _FORBIDDEN_RAW_FIELDS.intersection(str(key).lower() for key in value)
            if forbidden:
                raise ValueError("vault metadata contains forbidden raw secret fields")
        return value

    @model_validator(mode="after")
    def validate_provider_contract(self):
        if self.provider == "vaultwarden":
            if self.purpose != "human_credentials" or self.item_ref is None:
                raise ValueError("vaultwarden metadata requires human credential item_ref")
            if self.project_ref or self.identity_ref or self.store_ref or self.entry_ref:
                raise ValueError("vaultwarden metadata contains fields for another provider")
        elif self.provider == "infisical":
            if (
                self.purpose != "machine_identity"
                or self.project_ref is None
                or self.identity_ref is None
                or self.environment is None
            ):
                raise ValueError("infisical metadata requires machine identity references")
            if self.item_ref or self.collection_ref or self.store_ref or self.entry_ref:
                raise ValueError("infisical metadata contains fields for another provider")
        elif self.provider == "gopass":
            if (
                self.purpose != "local_mode"
                or self.store_ref is None
                or self.entry_ref is None
                or self.loopback_callback_url is None
            ):
                raise ValueError("gopass metadata requires local loopback references")
            if self.item_ref or self.collection_ref or self.project_ref or self.identity_ref:
                raise ValueError("gopass metadata contains fields for another provider")
        return self


class SecureActionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    sequence: int = Field(ge=1, le=10_000)
    kind: RecorderActionKind
    origin: str = Field(max_length=500)
    selector: str | None = Field(default=None, max_length=500)
    target: str | None = Field(default=None, max_length=500)
    secret_ref: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    vault: VaultProviderMetadata | None = None

    @field_validator("origin")
    @classmethod
    def normalize_action_origin(cls, value: str) -> str:
        return normalize_origin(value)

    @field_validator("selector", "target")
    @classmethod
    def validate_text_locator(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        cleaned = _reject_secret_like_text(value, field_name=info.field_name)
        if info.field_name == "selector" and _SAFE_SELECTOR_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("selector contains forbidden characters")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def reject_raw_secret_field_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = _FORBIDDEN_RAW_FIELDS.intersection(str(key).lower() for key in value)
            if forbidden:
                raise ValueError("action contains forbidden raw secret fields")
        return value

    @model_validator(mode="after")
    def require_reference_for_secret_actions(self):
        if self.kind in {"fill_secret_reference", "machine_identity_reference"}:
            if self.secret_ref is None or self.vault is None:
                raise ValueError("secret actions require secret_ref and vault metadata")
        return self


class SecureActionRecorderBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    recorder_id: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    signed_at: str
    actions: list[SecureActionRecord] = Field(min_length=1, max_length=100)
    signature: str | None = Field(default=None, pattern=_SIGNATURE_PATTERN)

    @field_validator("signed_at")
    @classmethod
    def validate_signed_at(cls, value: str) -> str:
        return _validate_timestamp(value)

    @model_validator(mode="before")
    @classmethod
    def reject_raw_secret_field_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = _FORBIDDEN_RAW_FIELDS.intersection(str(key).lower() for key in value)
            if forbidden:
                raise ValueError("recorder batch contains forbidden raw secret fields")
        return value

    @model_validator(mode="after")
    def validate_sequence_and_signature(self):
        if self.signature is None:
            raise RecorderBatchError("recorder batch signature is required")
        sequences = [action.sequence for action in self.actions]
        if len(set(sequences)) != len(sequences):
            raise ValueError("action sequences must be unique")
        if sequences != sorted(sequences):
            raise ValueError("action sequences must be ascending")
        return self


def canonical_recorder_signature_payload(
    value: dict[str, Any],
    *,
    run_id: str,
    worker_id: str,
    profile_id: str,
) -> bytes:
    unsigned = dict(value)
    unsigned.pop("signature", None)
    signed = {
        "context": {
            "profile_id": profile_id,
            "run_id": run_id,
            "worker_id": worker_id,
        },
        "batch": unsigned,
    }
    return json.dumps(signed, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _verify_signature(
    *,
    raw_body: bytes,
    batch: SecureActionRecorderBatch,
    worker_key: str,
    run_id: str,
    worker_id: str,
    profile_id: str,
) -> None:
    try:
        raw = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecorderBatchError("invalid recorder batch") from exc
    if not isinstance(raw, dict):
        raise RecorderBatchError("invalid recorder batch")
    expected = hmac.new(
        worker_key.encode("utf-8"),
        canonical_recorder_signature_payload(
            raw,
            run_id=run_id,
            worker_id=worker_id,
            profile_id=profile_id,
        ),
        hashlib.sha256,
    ).hexdigest()
    provided = str(batch.signature or "").removeprefix("sha256=")
    if not hmac.compare_digest(provided, expected):
        raise RecorderBatchError("invalid recorder batch signature")


def _nonce_digest(*, run_id: str, recorder_id: str, nonce: str) -> str:
    value = f"{run_id}\0{recorder_id}\0{nonce}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _validate_freshness(batch: SecureActionRecorderBatch, *, now: datetime) -> None:
    signed_at = datetime.fromisoformat(batch.signed_at)
    age_seconds = (now - signed_at).total_seconds()
    if age_seconds > MAX_BATCH_AGE_SECONDS or age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise RecorderBatchError("recorder batch is outside freshness window")


def _remember_nonce(
    nonce_cache: MutableMapping[str, float],
    *,
    digest: str,
    now: datetime,
) -> None:
    now_epoch = now.timestamp()
    cutoff = now_epoch - NONCE_TTL_SECONDS
    for cached_digest, seen_at in list(nonce_cache.items()):
        if float(seen_at) < cutoff:
            nonce_cache.pop(cached_digest, None)
    if digest in nonce_cache:
        raise RecorderBatchError("recorder nonce replay")
    while len(nonce_cache) >= MAX_NONCE_CACHE_ENTRIES:
        oldest = next(iter(nonce_cache))
        nonce_cache.pop(oldest, None)
    nonce_cache[digest] = now_epoch


def ingest_secure_action_recorder_batch(
    *,
    run_id: str,
    worker_id: str,
    profile_id: str,
    raw_body: bytes,
    worker_key: str,
    allowed_origins: list[str],
    nonce_cache: MutableMapping[str, float],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a signed recorder batch and return a redacted task-output payload."""

    try:
        raw = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecorderBatchError("invalid recorder batch") from exc
    if not isinstance(raw, dict):
        raise RecorderBatchError("invalid recorder batch")
    try:
        batch = SecureActionRecorderBatch.model_validate(raw)
    except RecorderBatchError:
        raise
    except Exception as exc:
        raise RecorderBatchError("invalid recorder batch") from exc

    current_time = now or datetime.now(timezone.utc)
    _verify_signature(
        raw_body=raw_body,
        batch=batch,
        worker_key=worker_key,
        run_id=run_id,
        worker_id=worker_id,
        profile_id=profile_id,
    )
    _validate_freshness(batch, now=current_time)

    for action in batch.actions:
        if not is_top_level_origin_allowed(action.origin, allowed_origins):
            raise RecorderBatchError("recorder origin is outside run policy")

    digest = _nonce_digest(run_id=run_id, recorder_id=batch.recorder_id, nonce=batch.nonce)
    _remember_nonce(nonce_cache, digest=digest, now=current_time)

    vault_providers = sorted(
        {action.vault.provider for action in batch.actions if action.vault is not None}
    )
    origins = sorted({action.origin for action in batch.actions})
    secret_reference_count = sum(1 for action in batch.actions if action.secret_ref is not None)
    return {
        "idempotency_key": f"recorder-batch-{digest}",
        "summary": f"Recorded {len(batch.actions)} secure action(s)",
        "payload": {
            "recorder_id": batch.recorder_id,
            "nonce_digest": digest,
            "action_count": len(batch.actions),
            "origins": origins,
            "vault_providers": vault_providers,
            "secret_reference_count": secret_reference_count,
            "signed_at": batch.signed_at,
        },
    }
