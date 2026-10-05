"""Explicit maintenance commands for existing installations.

Editing definitions.py alone does nothing to a database that already seeded
version 1: the DB is authoritative. These commands publish the code's current
challenge definitions as NEW immutable versions and repin a named development
event to them, preserving users, enrolments, solves and scores.

Usage (from backend/, with VOLT_DATABASE_URL set):

    python -m app.maintenance update [--event-slug volt-dev]
        Publish a new version of every challenge whose config changed, create
        the dev event if missing, repin it to the latest versions, and rotate
        (close) any active conversation whose pinned version changed so old and
        new prompts never mix in one conversation.

    python -m app.maintenance seed-dev-event [--event-slug volt-dev]
        Create/refresh only the development event (open registration, wide
        time window). Does not publish versions, does not create admins, does
        not touch any other event.

    python -m app.maintenance promote-admin EMAIL
        Explicitly grant the admin role to one existing account.

Safety: only the named development event is ever modified. Any event whose
slug differs (for example a live competition) is left untouched, so an active
competition is never silently re-pinned.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

import asyncpg

from . import db
from .challenges.definitions import ALL, NUMBERS

DEV_SLUG = "volt-dev"


async def publish_updates(conn: asyncpg.Connection) -> list[tuple[str, int]]:
    """Publish a new version for each challenge whose code config differs from
    the latest published one. Returns (challenge_id, new_version) pairs."""
    published: list[tuple[str, int]] = []
    for cid in NUMBERS:
        await conn.execute(
            "INSERT INTO challenges(id, number) VALUES($1,$2) "
            "ON CONFLICT (id) DO NOTHING",
            cid, NUMBERS[cid],
        )
        latest = await conn.fetchrow(
            """SELECT version, config FROM challenge_versions
               WHERE challenge_id=$1 ORDER BY version DESC LIMIT 1""",
            cid,
        )
        new_cfg = ALL[cid]
        if latest is not None:
            cur = latest["config"]
            if isinstance(cur, str):
                cur = json.loads(cur)
            if cur == new_cfg:
                continue  # unchanged, nothing to publish
        row = await conn.fetchrow(
            """INSERT INTO challenge_versions(challenge_id, version, config,
                                              published_at)
               SELECT $1, coalesce(max(version),0)+1, $2::jsonb, now()
               FROM challenge_versions WHERE challenge_id=$1
               RETURNING version""",
            cid, json.dumps(new_cfg),
        )
        published.append((cid, row["version"]))
    return published


async def _latest_versions(conn: asyncpg.Connection) -> dict[str, dict]:
    rows = await conn.fetch(
        """SELECT DISTINCT ON (challenge_id) challenge_id, id, version
           FROM challenge_versions WHERE published_at IS NOT NULL
           ORDER BY challenge_id, version DESC"""
    )
    return {r["challenge_id"]: {"id": r["id"], "version": r["version"]} for r in rows}


async def ensure_dev_event(conn: asyncpg.Connection, slug: str) -> str:
    ev = await conn.fetchrow("SELECT id FROM events WHERE slug=$1", slug)
    if ev is None:
        starts = datetime.now(timezone.utc) - timedelta(days=1)
        ends = datetime.now(timezone.utc) + timedelta(days=3650)
        ev = await conn.fetchrow(
            """INSERT INTO events(slug, name, description, registration_open,
                                  invite_only, starts_at, ends_at)
               VALUES($1,$2,$3,true,false,$4,$5) RETURNING id""",
            slug, "VOLT Development Event",
            "Local development event. Open registration, all five levels.",
            starts, ends,
        )
    return str(ev["id"])


async def repin_event(conn: asyncpg.Connection, event_id: str,
                      latest: dict[str, dict]) -> list[str]:
    """Pin the event's challenges to the latest published versions. Returns the
    challenge ids whose pinned version actually changed."""
    changed: list[str] = []
    for cid, info in sorted(latest.items(), key=lambda kv: NUMBERS.get(kv[0], 99)):
        cfg = ALL.get(cid, {})
        points = cfg.get("default_points", 100)
        existing = await conn.fetchrow(
            "SELECT version_id FROM event_challenges WHERE event_id=$1::uuid AND challenge_id=$2",
            event_id, cid,
        )
        if existing is None or existing["version_id"] != info["id"]:
            changed.append(cid)
        await conn.execute(
            """INSERT INTO event_challenges(event_id, challenge_id, version_id,
                                            points, enabled)
               VALUES($1::uuid,$2,$3,$4,true)
               ON CONFLICT (event_id, challenge_id) DO UPDATE SET
                 version_id=excluded.version_id, points=excluded.points,
                 enabled=true""",
            event_id, cid, info["id"], points,
        )
    return changed


async def rotate_stale_conversations(conn: asyncpg.Connection, event_id: str,
                                     challenge_ids: list[str]) -> int:
    """Close active conversations for the given challenges in this event so a
    fresh conversation starts under the new prompt (no mixed old/new context).
    Skips conversations with a turn in flight."""
    if not challenge_ids:
        return 0
    rows = await conn.fetch(
        """SELECT c.id FROM conversations c
           JOIN game_sessions gs ON gs.id = c.game_session_id
           WHERE c.active AND gs.scope = $1 AND gs.challenge_id = ANY($2::text[])
             AND NOT EXISTS (SELECT 1 FROM turns t
                             WHERE t.conversation_id = c.id AND t.status='pending')""",
        event_id, challenge_ids,
    )
    n = 0
    for r in rows:
        await conn.execute("UPDATE conversations SET active=false WHERE id=$1", r["id"])
        n += 1
    return n


async def cmd_update(slug: str) -> None:
    await db.connect()
    await db.migrate()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            published = await publish_updates(conn)
            latest = await _latest_versions(conn)
            event_id = await ensure_dev_event(conn, slug)
            changed = await repin_event(conn, event_id, latest)
            rotated = await rotate_stale_conversations(conn, event_id, changed)
    print(f"Published new versions: {published or 'none (already current)'}")
    print(f"Dev event '{slug}' = {event_id}")
    print(f"Repinned challenges: {changed or 'none'}")
    print(f"Rotated stale conversations: {rotated}")
    await db.close()


async def cmd_seed_dev_event(slug: str) -> None:
    await db.connect()
    await db.migrate()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            latest = await _latest_versions(conn)
            if not latest:
                print("No published challenge versions yet; run 'update' first.")
                await db.close()
                return
            event_id = await ensure_dev_event(conn, slug)
            await repin_event(conn, event_id, latest)
    print(f"Dev event '{slug}' ready: {event_id}")
    print("Register a player in the app, then enrol in this event to play.")
    await db.close()


async def cmd_promote_admin(email: str) -> None:
    await db.connect()
    async with db.pool().acquire() as conn:
        res = await conn.execute(
            "UPDATE users SET role='admin' WHERE email=$1", email.lower())
    print("Promoted." if res.endswith("1") else f"No account found for {email}.")
    await db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="VOLT maintenance commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("--event-slug", default=DEV_SLUG)
    s = sub.add_parser("seed-dev-event")
    s.add_argument("--event-slug", default=DEV_SLUG)
    p = sub.add_parser("promote-admin")
    p.add_argument("email")
    args = ap.parse_args()

    if args.cmd == "update":
        asyncio.run(cmd_update(args.event_slug))
    elif args.cmd == "seed-dev-event":
        asyncio.run(cmd_seed_dev_event(args.event_slug))
    elif args.cmd == "promote-admin":
        asyncio.run(cmd_promote_admin(args.email))


if __name__ == "__main__":
    main()
