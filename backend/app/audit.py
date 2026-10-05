"""Administrative audit records."""

from __future__ import annotations

import json

import asyncpg


async def record(conn: asyncpg.Connection, actor_id: str | None, action: str,
                 target: str = "", details: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO audit_log(actor_id, action, target, details) VALUES($1,$2,$3,$4::jsonb)",
        actor_id, action, target, json.dumps(details or {}),
    )
