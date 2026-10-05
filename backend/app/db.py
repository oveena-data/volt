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

_pool: asyncpg.Pool | None = None


async def connect(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=30,
        )
    return _pool


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised; call db.connect() first")
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


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
