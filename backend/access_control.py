"""Local, scope-based browser access control.

The manager remains backward compatible with its single ``AUTH_TOKEN`` until
``ACCESS_CONTROL_ENABLED=1`` is configured. In policy mode, the bootstrap token
is still an emergency/admin credential, while named people use signed sessions
and Paperclip agents use individual opaque bearer keys. Every resource check is
made against a profile's ``sandbox_id`` on the server side.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Literal

from starlette.types import Scope

if __package__:
    from . import database as db
else:  # Support `uvicorn main:app` from the backend directory.
    import database as db


PrincipalKind = Literal["bootstrap", "user", "agent", "anonymous"]
Permission = Literal["view", "interact", "operate", "automate"]

_PASSWORD_PREFIX = "scrypt"
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_LENGTH = 32
_SESSION_TTL_SECONDS = 8 * 60 * 60
BRIDGE_COOKIE_NAME = "cbm_bridge"
BRIDGE_COOKIE_PATH_LEGACY = "/api/profiles/"
BRIDGE_TTL_SECONDS = 900
BRIDGE_PATH_CLASS = "cdp-observer"
BRIDGE_KINDS = frozenset({"bootstrap", "user", "agent"})
BRIDGE_PATH_CLASSES = frozenset({BRIDGE_PATH_CLASS})
WORKER_KEY_PREFIX = "cbm_worker_"
WORKER_KEY_BYTES = 32
RUN_CAPABILITY_PREFIX = "cbm_run_"
RUN_CAPABILITY_BYTES = 32
# Bare persisted tokens: prefix + ≥1 base64url/hex/alnum char. Prefix-only mentions
# (e.g. "use the cbm_run_ prefix") do not match. Never echo the match.
_PERSISTED_CBM_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:cbm_run_|cbm_worker_|cbm_lease_)[A-Za-z0-9_-]+"
)
_OBSERVER_BRIDGE_PATH = re.compile(
    r"^/api/profiles/(?P<profile_id>[^/]+)/cdp-observer(?:/.*)?$"
)

BridgeKind = Literal["bootstrap", "user", "agent"]


@dataclass(frozen=True)
class AccessIdentity:
    kind: PrincipalKind
    id: str | None
    display_name: str
    role: str
    grants: tuple[dict[str, str], ...] = ()
    group_ids: tuple[str, ...] = ()

    @property
    def is_admin(self) -> bool:
        return self.kind == "bootstrap" or self.role == "admin"

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "display_name": self.display_name,
            "role": self.role,
            "grants": [dict(grant) for grant in self.grants],
            "group_ids": list(self.group_ids),
            "effective_grants": [dict(grant) for grant in self.grants],
        }


@dataclass(frozen=True)
class WorkerIdentity:
    """Narrowly scoped Browser-Use worker principal (never an AccessIdentity)."""

    id: str
    active: bool = True

    @property
    def kind(self) -> str:
        return "worker"


def access_control_enabled(value: object) -> bool:
    """Normalize the opt-in setting without treating arbitrary values as true."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Return a self-describing, memory-hard password hash.

    ``hashlib.scrypt`` avoids a new runtime dependency while retaining a random
    per-password salt. The plaintext never leaves this call.
    """
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        dklen=_PASSWORD_LENGTH,
    )
    return "$".join(
        (
            _PASSWORD_PREFIX,
            str(_PASSWORD_N),
            str(_PASSWORD_R),
            str(_PASSWORD_P),
            _b64encode(salt),
            _b64encode(derived),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    """Validate one password hash without leaking comparison timing."""
    try:
        prefix, n, r, p, salt_b64, digest_b64 = stored.split("$", 5)
        if prefix != _PASSWORD_PREFIX:
            return False
        expected = _b64decode(digest_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (TypeError, ValueError, UnicodeError):
        return False
    return hmac.compare_digest(derived, expected)


def generate_agent_key() -> str:
    """Create an opaque Paperclip-compatible bearer key shown exactly once."""
    return "cbm_agent_" + secrets.token_urlsafe(32)


def hash_agent_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_worker_key() -> str:
    """Create an opaque worker key: prefix + 32 random bytes as hex."""
    return WORKER_KEY_PREFIX + secrets.token_bytes(WORKER_KEY_BYTES).hex()


def hash_worker_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_valid_worker_key(key: str | None) -> bool:
    """Return True only for a strong cbm_worker_ key (32 random bytes hex)."""
    if not isinstance(key, str) or not key.startswith(WORKER_KEY_PREFIX):
        return False
    raw = key[len(WORKER_KEY_PREFIX) :]
    if len(raw) != WORKER_KEY_BYTES * 2:
        return False
    try:
        return len(bytes.fromhex(raw)) == WORKER_KEY_BYTES
    except ValueError:
        return False


def generate_run_capability_token() -> str:
    """Mint a one-time run CDP capability token (plaintext returned once)."""
    return RUN_CAPABILITY_PREFIX + secrets.token_bytes(RUN_CAPABILITY_BYTES).hex()


def hash_run_capability_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_run_capability_token(token: str | None) -> bool:
    if not isinstance(token, str) or not token.startswith(RUN_CAPABILITY_PREFIX):
        return False
    raw = token[len(RUN_CAPABILITY_PREFIX) :]
    if len(raw) != RUN_CAPABILITY_BYTES * 2:
        return False
    try:
        return len(bytes.fromhex(raw)) == RUN_CAPABILITY_BYTES
    except ValueError:
        return False


def contains_persisted_cbm_token(text: str) -> bool:
    """True when text embeds a bare cbm_run_/cbm_worker_/cbm_lease_ token.

    Conservative and boundary-aware. Does not treat a lone prefix mention as a
    token. Callers must never echo the matched token material.
    """
    if not isinstance(text, str) or not text:
        return False
    return _PERSISTED_CBM_TOKEN_RE.search(text) is not None


def resolve_worker_identity(scope: Scope) -> WorkerIdentity | None:
    """Resolve an active worker from Bearer cbm_worker_* only (never logs the key)."""
    state = scope.get("state") or {}
    cached = state.get("worker_identity")
    if isinstance(cached, WorkerIdentity):
        return cached if cached.active else None

    bearer = _bearer_token(scope)
    if not is_valid_worker_key(bearer):
        return None
    assert bearer is not None
    row = db.get_worker_identity_by_key_hash(hash_worker_key(bearer))
    if row is None or not bool(row.get("active")):
        return None
    return WorkerIdentity(id=str(row["id"]), active=True)


def create_session(user_id: str, signing_secret: str, ttl_seconds: int = _SESSION_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"v1|{user_id}|{expires_at}".encode("utf-8")
    signature = hmac.new(signing_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def verify_session(session: str, signing_secret: str) -> str | None:
    try:
        payload_b64, signature_b64 = session.split(".", 1)
        payload = _b64decode(payload_b64)
        supplied_signature = _b64decode(signature_b64)
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        version, user_id, expires_at = payload.decode("utf-8").split("|", 2)
        if version != "v1" or int(expires_at) < int(time.time()):
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    return user_id


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _cookie(scope: Scope, name: str) -> str | None:
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except (TypeError, ValueError):
        return None
    morsel = cookies.get(name)
    return morsel.value if morsel else None


def _bearer_token(scope: Scope) -> str | None:
    authorization = _header(scope, b"authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        return token or None
    return None


def bootstrap_identity() -> AccessIdentity:
    return AccessIdentity(
        kind="bootstrap",
        id=None,
        display_name="Bootstrap administrator",
        role="admin",
    )


def bridge_cookie_path(profile_id: str) -> str:
    """Per-profile observer cookie Path so multi-tab viewers do not overwrite."""
    pid = str(profile_id or "").strip()
    if not pid or "/" in pid or pid in {".", ".."}:
        raise ValueError("invalid profile_id for bridge cookie path")
    return f"/api/profiles/{pid}/cdp-observer/"


def parse_observer_bridge_path(path: str) -> str | None:
    """Return profile_id when path is under /api/profiles/{id}/cdp-observer/…"""
    match = _OBSERVER_BRIDGE_PATH.match(str(path or ""))
    if not match:
        return None
    profile_id = match.group("profile_id")
    if not profile_id or profile_id in {".", ".."}:
        return None
    return profile_id


def create_bridge_session(
    kind: BridgeKind,
    principal_id: str | None,
    signing_secret: str,
    *,
    profile_id: str,
    path_class: str = BRIDGE_PATH_CLASS,
    ttl_seconds: int = BRIDGE_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Mint a short-lived profile-bound observer bridge cookie."""
    if kind not in BRIDGE_KINDS:
        raise ValueError(f"unsupported bridge kind: {kind}")
    if path_class not in BRIDGE_PATH_CLASSES:
        raise ValueError(f"unsupported bridge path_class: {path_class}")
    pid = str(profile_id or "").strip()
    if not pid or "|" in pid or "/" in pid or "\n" in pid:
        raise ValueError("invalid profile_id")
    if kind == "bootstrap":
        principal = "-"
    else:
        if principal_id is None:
            raise ValueError("principal_id required for user/agent bridge")
        principal = str(principal_id).strip()
        if not principal or principal == "-":
            raise ValueError("invalid principal_id")
        if "|" in principal or "\n" in principal:
            raise ValueError("invalid principal_id")
    current = int(time.time() if now is None else now)
    expires_at = current + int(ttl_seconds)
    payload = (
        f"v3|bridge|{kind}|{principal}|{pid}|{path_class}|{expires_at}".encode("utf-8")
    )
    signature = hmac.new(signing_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def verify_bridge_session(
    session: str,
    signing_secret: str,
    *,
    now: int | None = None,
) -> tuple[BridgeKind, str | None, str, str] | None:
    """Return (kind, principal_id, profile_id, path_class) or None."""
    try:
        payload_b64, signature_b64 = session.split(".", 1)
        payload = _b64decode(payload_b64)
        supplied_signature = _b64decode(signature_b64)
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"), payload, hashlib.sha256
        ).digest()
        version, label, kind, principal, profile_id, path_class, expires_at = (
            payload.decode("utf-8").split("|", 6)
        )
        if version != "v3" or label != "bridge" or kind not in BRIDGE_KINDS:
            return None
        if path_class not in BRIDGE_PATH_CLASSES:
            return None
        if not profile_id or "|" in profile_id:
            return None
        if kind == "bootstrap":
            if principal != "-":
                return None
        else:
            if not principal or principal == "-":
                return None
        current = int(time.time() if now is None else now)
        if int(expires_at) <= current:
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    if kind == "bootstrap":
        return ("bootstrap", None, profile_id, path_class)
    return (kind, principal, profile_id, path_class)  # type: ignore[return-value]


def _identity_from_bridge(
    kind: BridgeKind, principal_id: str | None
) -> AccessIdentity | None:
    if kind == "bootstrap":
        return bootstrap_identity()
    if kind == "user" and principal_id:
        user = db.get_access_user(principal_id)
        if user and bool(user.get("active")):
            return AccessIdentity(
                kind="user",
                id=user["id"],
                display_name=user["username"],
                role=user["role"],
                grants=tuple(user.get("effective_grants", user.get("grants", []))),
                group_ids=tuple(user.get("group_ids", [])),
            )
        return None
    if kind == "agent" and principal_id:
        agent = db.get_access_agent(principal_id)
        if agent and bool(agent.get("active")):
            return AccessIdentity(
                kind="agent",
                id=agent["id"],
                display_name=agent["display_name"],
                role="agent",
                grants=tuple(agent.get("grants", [])),
            )
        return None
    return None


def resolve_identity(scope: Scope, auth_token: str | None) -> AccessIdentity | None:
    """Return the authenticated actor from a raw ASGI scope, if any."""
    bearer = _bearer_token(scope)
    legacy_cookie = _cookie(scope, "auth_token")
    if auth_token and (
        (bearer and hmac.compare_digest(bearer, auth_token))
        or (legacy_cookie and hmac.compare_digest(legacy_cookie, auth_token))
    ):
        return bootstrap_identity()

    if auth_token:
        session = _cookie(scope, "cbm_session")
        if session:
            user_id = verify_session(session, auth_token)
            if user_id:
                user = db.get_access_user(user_id)
                if user and bool(user.get("active")):
                    return AccessIdentity(
                        kind="user",
                        id=user["id"],
                        display_name=user["username"],
                        role=user["role"],
                        grants=tuple(user.get("effective_grants", user.get("grants", []))),
                        group_ids=tuple(user.get("group_ids", [])),
                    )

        if bearer and bearer.startswith("cbm_agent_"):
            agent = db.get_access_agent_by_key_hash(hash_agent_key(bearer))
            if agent and bool(agent.get("active")):
                return AccessIdentity(
                    kind="agent",
                    id=agent["id"],
                    display_name=agent["display_name"],
                    role="agent",
                    grants=tuple(agent.get("grants", [])),
                )

        # Profile-bound observer bridge only under /api/profiles/{id}/cdp-observer/…
        path = str(scope.get("path") or "")
        path_profile_id = parse_observer_bridge_path(path)
        if path_profile_id is not None:
            bridge = _cookie(scope, BRIDGE_COOKIE_NAME)
            if bridge:
                claims = verify_bridge_session(bridge, auth_token)
                if (
                    claims is not None
                    and claims[2] == path_profile_id
                    and claims[3] == BRIDGE_PATH_CLASS
                ):
                    return _identity_from_bridge(claims[0], claims[1])
    return None


def identity_from_scope(scope: Scope, auth_token: str | None, enabled: bool) -> AccessIdentity | None:
    """Use middleware state when available and preserve legacy open behavior."""
    state = scope.get("state") or {}
    identity = state.get("access_identity")
    if isinstance(identity, AccessIdentity):
        return identity
    resolved = resolve_identity(scope, auth_token)
    if resolved:
        return resolved
    # An installation without any configured authentication remains a local,
    # owner-operated dashboard.  A legacy installation *with* AUTH_TOKEN must
    # still pass the token middleware; returning an anonymous administrator in
    # that case would make this helper unsafe if it were ever used outside the
    # middleware path.
    if not enabled and not auth_token:
        return AccessIdentity(
            kind="anonymous",
            id=None,
            display_name="Local legacy access",
            role="admin",
        )
    return None


def has_permission(identity: AccessIdentity, sandbox_id: str, permission: Permission) -> bool:
    """Evaluate a single policy action without trusting the frontend."""
    if identity.is_admin:
        return True

    for grant in identity.grants:
        if grant.get("sandbox_id") != sandbox_id:
            continue
        granted = grant.get("permission")
        if permission == "view" and granted in {"view", "interact", "operate", "automate"}:
            return True
        if permission == "interact" and granted in {"interact", "operate"}:
            return True
        if permission == "operate" and granted == "operate":
            return True
        if permission == "automate" and granted == "automate":
            return True
    return False


def can_access_profile(identity: AccessIdentity, profile: dict[str, Any], permission: Permission) -> bool:
    return has_permission(identity, str(profile.get("sandbox_id") or "default"), permission)
