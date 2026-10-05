"""Authentication primitives: password hashing, opaque session tokens, guards.

Identity is always derived from a validated bearer token -> auth_sessions row
-> users row. Client-supplied names/ids are never trusted as authentication.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from starlette.requests import Request

from .config import settings
from .errors import ApiError

_hasher = PasswordHasher()  # argon2id, library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_session_token() -> tuple[str, str]:
    """Return (token, token_hash). The raw token is shown to the client once;
    only its sha256 is stored."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class CurrentUser:
    id: str
    email: str
    display_name: str
    role: str
    is_verified: bool
    token_hash: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def create_session(conn: asyncpg.Connection, user_id: str) -> str:
    token, token_hash = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    await conn.execute(
        "INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES($1,$2,$3)",
        token_hash,
        user_id,
        expires,
    )
    return token


async def resolve_user(conn: asyncpg.Connection, request: Request) -> CurrentUser | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    row = await conn.fetchrow(
        """SELECT u.id, u.email, u.display_name, u.role, u.is_verified, s.token_hash
           FROM auth_sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = $1 AND s.revoked_at IS NULL AND s.expires_at > now()""",
        hash_token(token),
    )
    if row is None:
        return None
    return CurrentUser(
        id=str(row["id"]),
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        is_verified=row["is_verified"],
        token_hash=row["token_hash"],
    )


async def require_user(conn: asyncpg.Connection, request: Request) -> CurrentUser:
    user = await resolve_user(conn, request)
    if user is None:
        raise ApiError("authentication required", 401, code="unauthenticated")
    return user


async def require_admin(conn: asyncpg.Connection, request: Request) -> CurrentUser:
    user = await require_user(conn, request)
    if not user.is_admin:
        raise ApiError("administrator role required", 403, code="forbidden")
    return user
