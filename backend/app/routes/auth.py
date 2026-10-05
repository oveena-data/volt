"""Auth routes: register, login, logout, me, display name."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db
from ..audit import record
from ..config import settings
from ..errors import ApiError
from ..schemas import DisplayNameIn, LoginIn, RegisterIn, parse
from ..security import (create_session, hash_password, require_user,
                        verify_password)


def _user_payload(row) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "is_verified": row["is_verified"],
    }


async def register(request: Request) -> JSONResponse:
    body = await parse(request, RegisterIn)
    email = body.email.lower()
    role = "admin" if email in settings.admin_email_list else "player"
    verified = not settings.require_email_verification
    async with db.pool().acquire() as conn:
        existing = await conn.fetchval("SELECT 1 FROM users WHERE email=$1", email)
        if existing:
            raise ApiError("an account with this email already exists", 409,
                           code="email_taken")
        row = await conn.fetchrow(
            """INSERT INTO users(email, password_hash, display_name, role, is_verified)
               VALUES($1,$2,$3,$4,$5)
               RETURNING id, email, display_name, role, is_verified""",
            email, hash_password(body.password), body.display_name, role, verified,
        )
        token = await create_session(conn, row["id"])
    return JSONResponse({"token": token, "user": _user_payload(row)}, status_code=201)


async def login(request: Request) -> JSONResponse:
    body = await parse(request, LoginIn)
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, email, password_hash, display_name, role, is_verified
               FROM users WHERE email=$1""",
            body.email.lower(),
        )
        if row is None or not verify_password(row["password_hash"], body.password):
            raise ApiError("invalid email or password", 401, code="invalid_credentials")
        token = await create_session(conn, row["id"])
    return JSONResponse({"token": token, "user": _user_payload(row)})


async def logout(request: Request) -> JSONResponse:
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        await conn.execute(
            "UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=$1",
            user.token_hash,
        )
    return JSONResponse({"ok": True})


async def me(request: Request) -> JSONResponse:
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        return JSONResponse({
            "id": user.id, "email": user.email, "display_name": user.display_name,
            "role": user.role, "is_verified": user.is_verified,
        })


async def update_display_name(request: Request) -> JSONResponse:
    body = await parse(request, DisplayNameIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        await conn.execute(
            "UPDATE users SET display_name=$2 WHERE id=$1", user.id, body.display_name)
        await record(conn, user.id, "user.display_name", user.id,
                     {"display_name": body.display_name})
    return JSONResponse({"ok": True, "display_name": body.display_name})
