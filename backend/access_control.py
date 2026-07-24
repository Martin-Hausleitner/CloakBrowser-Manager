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
