"""Explicit maintenance commands for existing installations.

Editing definitions.py alone does nothing to a database that already seeded
version 1: the DB is authoritative. These commands publish the code's current
challenge definitions as NEW immutable versions, manage real game events, and
repin a named event to the latest versions, preserving users, enrolments,
solves and scores.

There is no development event and nothing is ever created implicitly: every
event is a real event an organiser asked for by name, and only the event
named on the command line is ever modified, so an active competition is never
silently re-pinned.

Usage (from backend/, with VOLT_DATABASE_URL set):

    python -m app.maintenance update [--event-slug SLUG]
        Publish a new version of every challenge whose config changed. With
        --event-slug, also repin that existing event to the latest versions
        and rotate (close) any active conversation whose pinned version
        changed so old and new prompts never mix in one conversation.

    python -m app.maintenance create-event SLUG NAME --starts ISO --ends ISO
            [--invite-only] [--closed-registration]
        Create a real game event with all published levels at their default
        points. Fails if the slug already exists.

    python -m app.maintenance delete-event SLUG --yes
        Permanently delete an event and all its gameplay data (enrolments,
        sessions, conversations, solves, flags, submissions). Use this to
        remove a retired event, for example the old development event
        ('volt-dev') from installs created before this release.

    python -m app.maintenance promote-admin EMAIL
        Explicitly grant the admin role to one existing account.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

import asyncpg

from . import db
from .challenges.definitions import ALL, NUMBERS


def _parse_ts(value: str) -> datetime:
    """ISO timestamp; a naive value is taken as UTC."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


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


async def create_event(
    conn: asyncpg.Connection, slug: str, name: str,
    starts: datetime, ends: datetime, *,
    invite_only: bool = False, registration_open: bool = True,
) -> str:
    """Create a real game event with every published level at its default
    points. Fails loudly on a duplicate slug: events are never re-created."""
    existing = await conn.fetchval("SELECT 1 FROM events WHERE slug=$1", slug)
    if existing:
        raise SystemExit(f"event '{slug}' already exists; pick another slug "
                         "or use 'update --event-slug' to repin it")
    latest = await _latest_versions(conn)
    if not latest:
        raise SystemExit("no published challenge versions yet; run 'update' first")
    ev = await conn.fetchrow(
        """INSERT INTO events(slug, name, registration_open, invite_only,
                              starts_at, ends_at)
           VALUES($1,$2,$3,$4,$5,$6) RETURNING id""",
        slug, name, registration_open, invite_only, starts, ends,
    )
    await repin_event(conn, str(ev["id"]), latest)
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


async def delete_event(conn: asyncpg.Connection, slug: str) -> int:
    """Delete one event and every piece of gameplay data scoped to it."""
    ev = await conn.fetchrow("SELECT id FROM events WHERE slug=$1", slug)
    if ev is None:
        raise SystemExit(f"no event with slug '{slug}'")
    event_id = str(ev["id"])
    async with conn.transaction():
        # Scope-keyed rows (scope is the event uuid as text)
        await conn.execute("DELETE FROM player_flags WHERE scope=$1", event_id)
        await conn.execute("DELETE FROM hint_unlocks WHERE scope=$1", event_id)
        await conn.execute("DELETE FROM flag_submissions WHERE scope=$1", event_id)
        await conn.execute("DELETE FROM solves WHERE event_id=$1::uuid", event_id)
        # conversations/messages/turns cascade from game_sessions
        await conn.execute("DELETE FROM game_sessions WHERE event_id=$1::uuid",
                           event_id)
        # enrolments, invites, event_challenges cascade from events
        await conn.execute("DELETE FROM events WHERE id=$1::uuid", event_id)
    return 1


async def cmd_update(slug: str | None) -> None:
    await db.connect()
    await db.migrate()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            published = await publish_updates(conn)
            print(f"Published new versions: {published or 'none (already current)'}")
            if slug:
                ev = await conn.fetchrow("SELECT id FROM events WHERE slug=$1", slug)
                if ev is None:
                    raise SystemExit(f"no event with slug '{slug}'; create one "
                                     "with 'create-event' first")
                latest = await _latest_versions(conn)
                changed = await repin_event(conn, str(ev["id"]), latest)
                rotated = await rotate_stale_conversations(conn, str(ev["id"]),
                                                           changed)
                print(f"Event '{slug}' repinned challenges: {changed or 'none'}")
                print(f"Rotated stale conversations: {rotated}")
    await db.close()


async def cmd_create_event(args) -> None:
    await db.connect()
    await db.migrate()
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            event_id = await create_event(
                conn, args.slug, args.name,
                _parse_ts(args.starts), _parse_ts(args.ends),
                invite_only=args.invite_only,
                registration_open=not args.closed_registration,
            )
    print(f"Event '{args.slug}' created: {event_id}")
    print("Players can now register in the app and join it.")
    await db.close()


async def cmd_delete_event(slug: str, confirmed: bool) -> None:
    if not confirmed:
        raise SystemExit("delete-event is permanent; re-run with --yes to confirm")
    await db.connect()
    await db.migrate()
    async with db.pool().acquire() as conn:
        await delete_event(conn, slug)
    print(f"Event '{slug}' and all its gameplay data deleted.")
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
    u.add_argument("--event-slug", default=None)
    c = sub.add_parser("create-event")
    c.add_argument("slug")
    c.add_argument("name")
    c.add_argument("--starts", required=True,
                   help="ISO timestamp, e.g. 2026-11-01T09:00:00+00:00")
    c.add_argument("--ends", required=True)
    c.add_argument("--invite-only", action="store_true")
    c.add_argument("--closed-registration", action="store_true")
    d = sub.add_parser("delete-event")
    d.add_argument("slug")
    d.add_argument("--yes", action="store_true")
    p = sub.add_parser("promote-admin")
    p.add_argument("email")
    args = ap.parse_args()

    if args.cmd == "update":
        asyncio.run(cmd_update(args.event_slug))
    elif args.cmd == "create-event":
        asyncio.run(cmd_create_event(args))
    elif args.cmd == "delete-event":
        asyncio.run(cmd_delete_event(args.slug, args.yes))
    elif args.cmd == "promote-admin":
        asyncio.run(cmd_promote_admin(args.email))


if __name__ == "__main__":
    main()
