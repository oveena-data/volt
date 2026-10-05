"""Seed challenges + version 1 configs on first startup (idempotent)."""

from __future__ import annotations

import json

import asyncpg

from .definitions import ALL, NUMBERS


async def seed_challenges(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(727270002)")
            for cid, number in NUMBERS.items():
                await conn.execute(
                    "INSERT INTO challenges(id, number) VALUES($1,$2) "
                    "ON CONFLICT (id) DO NOTHING",
                    cid, number,
                )
                exists = await conn.fetchval(
                    "SELECT 1 FROM challenge_versions WHERE challenge_id=$1 AND version=1",
                    cid,
                )
                if not exists:
                    await conn.execute(
                        """INSERT INTO challenge_versions
                           (challenge_id, version, config, published_at)
                           VALUES($1, 1, $2::jsonb, now())""",
                        cid, json.dumps(ALL[cid]),
                    )


async def load_published_version(
    conn: asyncpg.Connection, challenge_id: str, version: int | None = None
) -> tuple[int, dict] | None:
    """Latest published version (or a specific one)."""
    if version is None:
        row = await conn.fetchrow(
            """SELECT version, config FROM challenge_versions
               WHERE challenge_id=$1 AND published_at IS NOT NULL
               ORDER BY version DESC LIMIT 1""",
            challenge_id,
        )
    else:
        row = await conn.fetchrow(
            """SELECT version, config FROM challenge_versions
               WHERE challenge_id=$1 AND version=$2 AND published_at IS NOT NULL""",
            challenge_id, version,
        )
    if row is None:
        return None
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return row["version"], cfg
