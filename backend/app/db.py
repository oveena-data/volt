"""Database access: asyncpg pool + a small forward-only SQL migration runner.

Migrations live in backend/migrations/NNNN_name.sql and are applied in order
inside a transaction each, recorded in schema_migrations. Rollbacks are
performed by restoring from backup (see docs/RUNBOOK.md), not by down-files.
"""

from __future__ import annotations

import logging
import os
import re

import asyncpg

from .config import settings

log = logging.getLogger("volt.db")

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")

# One pool per event loop: each app instance (and each test lifecycle) owns
# its own pool, mirroring multi-instance deployments.
_pools: dict[int, asyncpg.Pool] = {}


def _loop_key() -> int:
    import asyncio
    return id(asyncio.get_running_loop())


async def connect(dsn: str | None = None) -> asyncpg.Pool:
    key = _loop_key()
    if key not in _pools:
        _pools[key] = await asyncpg.create_pool(
            dsn or settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=30,
        )
    return _pools[key]


def pool() -> asyncpg.Pool:
    key = _loop_key()
    if key not in _pools:
        raise RuntimeError("database pool not initialised; call db.connect() first")
    return _pools[key]


async def close() -> None:
    key = _loop_key()
    p = _pools.pop(key, None)
    if p is not None:
        await p.close()


def _migration_files() -> list[tuple[int, str, str]]:
    out = []
    for name in sorted(os.listdir(MIGRATIONS_DIR)):
        m = re.match(r"^(\d{4})_[\w\-]+\.sql$", name)
        if not m:
            continue
        with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
            out.append((int(m.group(1)), name, fh.read()))
    return out


async def migrate(p: asyncpg.Pool | None = None) -> list[str]:
    """Apply pending migrations; returns the list applied."""
    p = p or pool()
    applied: list[str] = []
    async with p.acquire() as conn:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version    integer PRIMARY KEY,
                   name       text NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        # Serialise concurrent migrators (multi-instance startup).
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(727270001)")
            done = {
                r["version"]
                for r in await conn.fetch("SELECT version FROM schema_migrations")
            }
            for version, name, sql in _migration_files():
                if version in done:
                    continue
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES($1, $2)",
                    version,
                    name,
                )
                applied.append(name)
                log.info("applied migration %s", name)
    return applied
