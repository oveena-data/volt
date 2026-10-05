"""Game engine: sessions, conversations, turns, solves, hints, submissions.

Key properties:
  * All state is in Postgres — survives refreshes, restarts, multiple
    backend instances.
  * Turns within a conversation are serialised: the active conversation row
    is locked while a turn is claimed; a second concurrent message gets 409.
  * Duplicate requests are idempotent via (conversation_id, client_msg_id):
    a finished turn replays its stored result, an in-flight one returns 409.
  * No database transaction is held across the inference call: a turn is
    claimed (tx1), the model is called, then the result lands (tx2). A turn
    left 'pending' by a crash is reaped as an error after a grace period.
  * Provider failures mark the turn 'error' and take the user message out of
    future model context; they are never counted as player attempts.
  * Output filtering: the model-facing transcript keeps the RAW assistant
    text (content), the player sees visible_content. For levels 1-5 the two
    are identical; for future redacting levels the raw text intentionally
    stays in context (the model already knows its own secret — redaction
    protects the player-visible channel, not the model from itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from . import providers
from .challenges.filters import apply_input_filters, apply_output_filters
from .challenges.seed import load_published_version
from .config import settings
from .errors import ApiError
from .flags import contains_flag, get_or_create_flag, submission_matches

PRACTICE = "practice"


# --------------------------------------------------------------------------
# access / availability
# --------------------------------------------------------------------------

@dataclass
class ChallengeAccess:
    challenge_id: str
    scope: str                 # 'practice' | event uuid text
    event_id: str | None
    mode: str                  # 'practice' | 'ranked'
    version: int
    config: dict
    points: int                # gross points for a ranked solve (practice: informational)


async def resolve_access(
    conn: asyncpg.Connection, user_id: str, challenge_id: str,
    mode: str, event_id: str | None,
) -> ChallengeAccess:
    """Validate that this player may play this challenge now, and resolve the
    pinned config version + points. Raises ApiError otherwise."""
    if mode == PRACTICE:
        loaded = await load_published_version(conn, challenge_id)
        if loaded is None:
            raise ApiError("unknown or unpublished challenge", 404, code="not_found")
        version, cfg = loaded
        return ChallengeAccess(challenge_id, PRACTICE, None, PRACTICE,
                               version, cfg, cfg.get("default_points", 100))

    if not event_id:
        raise ApiError("event_id is required for ranked play", 422,
                       code="validation_error")
    row = await conn.fetchrow(
        """SELECT e.id, e.starts_at, e.ends_at, e.paused,
                  ec.points, ec.enabled, ec.opens_at, ec.closes_at,
                  cv.version, cv.config, cv.published_at,
                  (en.user_id IS NOT NULL) AS enrolled
           FROM events e
           JOIN event_challenges ec ON ec.event_id = e.id AND ec.challenge_id = $2
           JOIN challenge_versions cv ON cv.id = ec.version_id
           LEFT JOIN enrollments en ON en.event_id = e.id AND en.user_id = $3
           WHERE e.id = $1""",
        event_id, challenge_id, user_id,
    )
    if row is None:
        raise ApiError("challenge is not part of this event", 404, code="not_found")
    if not row["enrolled"]:
        raise ApiError("you are not enrolled in this event", 403, code="not_enrolled")
    now = datetime.now(timezone.utc)
    if now < row["starts_at"]:
        raise ApiError("this event has not started yet", 403, code="event_not_started")
    if now >= row["ends_at"]:
        raise ApiError("this event has ended", 403, code="event_ended")
    if row["paused"]:
        raise ApiError("gameplay is paused by the organisers", 423, code="event_paused")
    if not row["enabled"] or row["published_at"] is None:
        raise ApiError("this challenge is not currently available", 403,
                       code="challenge_unavailable")
    if row["opens_at"] and now < row["opens_at"]:
        raise ApiError("this challenge has not opened yet", 403,
                       code="challenge_unavailable")
    if row["closes_at"] and now >= row["closes_at"]:
        raise ApiError("this challenge has closed", 403, code="challenge_unavailable")
    cfg = row["config"]
    if isinstance(cfg, str):
        import json
        cfg = json.loads(cfg)
    return ChallengeAccess(challenge_id, str(event_id), str(event_id), "ranked",
                           row["version"], cfg, row["points"])


# --------------------------------------------------------------------------
# sessions & conversations
# --------------------------------------------------------------------------

async def get_or_create_game_session(
    conn: asyncpg.Connection, user_id: str, access: ChallengeAccess
) -> dict:
    row = await conn.fetchrow(
        """INSERT INTO game_sessions(user_id, challenge_id, scope, event_id, mode)
           VALUES($1,$2,$3,$4,$5)
           ON CONFLICT (user_id, challenge_id, scope)
           DO UPDATE SET user_id = game_sessions.user_id
           RETURNING id, user_id, challenge_id, scope, event_id, mode""",
        user_id, access.challenge_id, access.scope, access.event_id, access.mode,
    )
    gs = dict(row)
    conv = await conn.fetchrow(
        "SELECT id, generation FROM conversations WHERE game_session_id=$1 AND active",
        gs["id"],
    )
    if conv is None:
        conv = await conn.fetchrow(
            """INSERT INTO conversations(game_session_id) VALUES($1)
               RETURNING id, generation""",
            gs["id"],
        )
    gs["conversation_id"] = str(conv["id"])
    gs["generation"] = conv["generation"]
    return gs


async def owned_game_session(
    conn: asyncpg.Connection, user_id: str, game_session_id: str
) -> dict:
    """Ownership check for every player-specific resource."""
    try:
        row = await conn.fetchrow(
            """SELECT gs.*, c.id AS conversation_id, c.generation
               FROM game_sessions gs
               JOIN conversations c ON c.game_session_id = gs.id AND c.active
               WHERE gs.id = $1""",
            game_session_id,
        )
    except asyncpg.exceptions.DataError:
        raise ApiError("unknown game session", 404, code="not_found")
    if row is None:
        raise ApiError("unknown game session", 404, code="not_found")
    if str(row["user_id"]) != str(user_id):
        # Same response as not-found: no cross-player resource probing.
        raise ApiError("unknown game session", 404, code="not_found")
    return dict(row)


async def rotate_conversation(conn: asyncpg.Connection, game_session_id: str) -> dict:
    """Deactivate the active conversation and start a fresh one (used by both
    reset and new-chat; levels 6+ will differ on persistent memory)."""
    async with conn.transaction():
        old = await conn.fetchrow(
            """SELECT id, generation FROM conversations
               WHERE game_session_id=$1 AND active FOR UPDATE""",
            game_session_id,
        )
        gen = 1
        if old is not None:
            pending = await conn.fetchval(
                "SELECT 1 FROM turns WHERE conversation_id=$1 AND status='pending'",
                old["id"],
            )
            if pending:
                raise ApiError("a turn is still in flight; wait for it to finish",
                               409, code="busy")
            await conn.execute("UPDATE conversations SET active=false WHERE id=$1",
                               old["id"])
            gen = old["generation"] + 1
        row = await conn.fetchrow(
            """INSERT INTO conversations(game_session_id, generation)
               VALUES($1,$2) RETURNING id, generation""",
            game_session_id, gen,
        )
        return dict(row)


# --------------------------------------------------------------------------
# stats helpers
# --------------------------------------------------------------------------

async def conversation_stats(conn: asyncpg.Connection, conversation_id: str) -> dict:
    row = await conn.fetchrow(
        """SELECT coalesce(sum(prompt_tokens + completion_tokens),0) AS tokens,
                  count(*) FILTER (WHERE status IN ('done','blocked')
                                    AND NOT leaked) AS attempts
           FROM turns WHERE conversation_id=$1""",
        conversation_id,
    )
    return {"tokens": int(row["tokens"]), "attempts": int(row["attempts"])}


async def scope_solved(conn: asyncpg.Connection, user_id: str, scope: str,
                       challenge_id: str) -> dict | None:
    row = await conn.fetchrow(
        """SELECT points, hints_cost, solved_at, method FROM solves
           WHERE user_id=$1 AND scope=$2 AND challenge_id=$3""",
        user_id, scope, challenge_id,
    )
    return dict(row) if row else None


async def _record_solve(
    conn: asyncpg.Connection, user_id: str, access: ChallengeAccess,
    method: str, turn_id: str | None,
) -> dict:
    """Atomic: one solve per (user, scope, challenge); hint costs are captured
    at solve time inside the same transaction."""
    async with conn.transaction():
        hints_cost = await conn.fetchval(
            """SELECT coalesce(sum(cost),0) FROM hint_unlocks
               WHERE user_id=$1 AND scope=$2 AND challenge_id=$3""",
            user_id, access.scope, access.challenge_id,
        )
        row = await conn.fetchrow(
            """INSERT INTO solves(user_id, challenge_id, scope, event_id, mode,
                                  method, points, hints_cost, turn_id)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (user_id, scope, challenge_id) DO NOTHING
               RETURNING points, hints_cost, solved_at, method""",
            user_id, access.challenge_id, access.scope, access.event_id,
            access.mode, method, access.points, int(hints_cost), turn_id,
        )
    if row is None:  # already solved earlier — keep the original record
        return await scope_solved(conn, user_id, access.scope, access.challenge_id)
    return dict(row)


# --------------------------------------------------------------------------
# the turn
# --------------------------------------------------------------------------

@dataclass
class TurnOutcome:
    status: str                  # done | blocked | error | replay
    reply: str = ""
    leaked: bool = False
    solved: bool = False
    solve: dict | None = None
    error_kind: str | None = None
    error_message: str | None = None
    tokens: int = 0              # conversation cumulative
    attempts: int = 0            # conversation cumulative
    turn_tokens: int = 0
    latency_ms: int | None = None
    extras: dict = field(default_factory=dict)


def _stale_cutoff() -> datetime:
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(seconds=settings.request_timeout + 30)


async def play_turn(
    pool: asyncpg.Pool, user_id: str, game_session_id: str,
    client_msg_id: str, text: str,
) -> TurnOutcome:
    # ---------- tx1: validate, claim the turn ----------
    async with pool.acquire() as conn:
        gs = await owned_game_session(conn, user_id, game_session_id)
        access = await resolve_access(conn, user_id, gs["challenge_id"],
                                      gs["mode"], gs["event_id"] and str(gs["event_id"]))
        async with conn.transaction():
            conv = await conn.fetchrow(
                "SELECT id FROM conversations WHERE id=$1 AND active FOR UPDATE",
                gs["conversation_id"],
            )
            if conv is None:
                raise ApiError("conversation was reset; reload", 409, code="conflict")

            existing = await conn.fetchrow(
                "SELECT * FROM turns WHERE conversation_id=$1 AND client_msg_id=$2",
                gs["conversation_id"], client_msg_id,
            )
            retry_turn_id = None
            if existing is not None:
                if existing["status"] in ("done", "blocked"):
                    return await _replay(conn, gs, access, existing)
                if existing["status"] == "pending":
                    if existing["created_at"] > _stale_cutoff():
                        raise ApiError("this message is still being processed",
                                       409, code="in_progress")
                    await conn.execute(
                        """UPDATE turns SET status='error', error_kind='stale',
                           finished_at=now() WHERE id=$1""", existing["id"])
                # status == error (or just reaped): retry re-uses the turn row
                retry_turn_id = existing["id"]

            other_pending = await conn.fetchval(
                """SELECT 1 FROM turns WHERE conversation_id=$1 AND status='pending'
                   AND client_msg_id <> $2""",
                gs["conversation_id"], client_msg_id,
            )
            if other_pending:
                raise ApiError("another message in this conversation is still "
                               "being processed", 409, code="busy")

            n_turns = await conn.fetchval(
                "SELECT count(*) FROM turns WHERE conversation_id=$1 AND status<>'error'",
                gs["conversation_id"],
            )
            if int(n_turns) >= settings.max_conversation_turns:
                raise ApiError(
                    f"conversation limit of {settings.max_conversation_turns} turns "
                    "reached — reset or start a new chat", 409, code="turn_limit",
                )

            if retry_turn_id is not None:
                turn = await conn.fetchrow(
                    """UPDATE turns SET status='pending', error_kind=NULL,
                       created_at=now(), finished_at=NULL
                       WHERE id=$1 RETURNING id, user_message_id""",
                    retry_turn_id,
                )
                user_msg_id = turn["user_message_id"]
                await conn.execute(
                    "UPDATE messages SET in_context=true WHERE id=$1", user_msg_id)
                turn_id = turn["id"]
            else:
                seq = await conn.fetchval(
                    "SELECT coalesce(max(seq),0)+1 FROM messages WHERE conversation_id=$1",
                    gs["conversation_id"],
                )
                user_msg_id = await conn.fetchval(
                    """INSERT INTO messages(conversation_id, seq, role, content,
                                            visible_content)
                       VALUES($1,$2,'user',$3,$3) RETURNING id""",
                    gs["conversation_id"], seq, text,
                )
                turn_id = await conn.fetchval(
                    """INSERT INTO turns(conversation_id, client_msg_id, status,
                                         user_message_id, challenge_version)
                       VALUES($1,$2,'pending',$3,$4) RETURNING id""",
                    gs["conversation_id"], client_msg_id, user_msg_id, access.version,
                )

        # ---------- input filters (no model call if blocked) ----------
        filters = access.config.get("filters", {})
        verdict = apply_input_filters(filters.get("input", []), text)
        if verdict.blocked:
            async with conn.transaction():
                seq = await conn.fetchval(
                    "SELECT coalesce(max(seq),0)+1 FROM messages WHERE conversation_id=$1",
                    gs["conversation_id"],
                )
                reply_id = await conn.fetchval(
                    """INSERT INTO messages(conversation_id, seq, role, content,
                                            visible_content, in_context)
                       VALUES($1,$2,'filter',$3,$3,false) RETURNING id""",
                    gs["conversation_id"], seq, verdict.message,
                )
                # the model never saw the blocked text: keep it out of context
                await conn.execute(
                    "UPDATE messages SET in_context=false WHERE id=$1", user_msg_id)
                await conn.execute(
                    """UPDATE turns SET status='blocked', reply_message_id=$2,
                       prompt_tokens=$3, finished_at=now() WHERE id=$1""",
                    turn_id, reply_id, providers.estimate_tokens(text),
                )
            stats = await conversation_stats(conn, gs["conversation_id"])
            solved = await scope_solved(conn, user_id, access.scope, access.challenge_id)
            return TurnOutcome(status="blocked", reply=verdict.message,
                               tokens=stats["tokens"], attempts=stats["attempts"],
                               solved=solved is not None,
                               extras=await _extras(conn, access, gs))

        # ---------- build model context ----------
        flag = await get_or_create_flag(conn, user_id, access.scope, access.challenge_id)
        system_prompt = access.config["system_prompt"].replace("{flag}", flag)
        history = await conn.fetch(
            """SELECT role, content FROM messages
               WHERE conversation_id=$1 AND in_context AND role IN ('user','assistant')
               ORDER BY seq""",
            gs["conversation_id"],
        )
    # pool connection released here — nothing is held during inference

    msgs = [{"role": "system", "content": system_prompt}]
    msgs += [{"role": r["role"], "content": r["content"]} for r in history]
    mp = access.config.get("model_params", {})
    try:
        result = await providers.generate(
            msgs,
            temperature=mp.get("temperature"),
            max_tokens=mp.get("max_tokens"),
        )
    except providers.QueueFullError as e:
        await _fail_turn(pool, turn_id, user_msg_id, "queue_full")
        raise ApiError(str(e), 429, code="queue_full")
    except providers.ProviderError as e:
        await _fail_turn(pool, turn_id, user_msg_id, e.kind)
        async with pool.acquire() as conn:
            stats = await conversation_stats(conn, gs["conversation_id"])
        return TurnOutcome(status="error", error_kind=e.kind,
                           error_message="The model backend is unavailable right now. "
                                         "This did not count as an attempt — retry "
                                         "the same message.",
                           tokens=stats["tokens"], attempts=stats["attempts"])

    visible = apply_output_filters(filters.get("output", []), result.text)
    leaked = contains_flag(visible, flag)

    # ---------- tx2: persist outcome ----------
    async with pool.acquire() as conn:
        async with conn.transaction():
            seq = await conn.fetchval(
                "SELECT coalesce(max(seq),0)+1 FROM messages WHERE conversation_id=$1",
                gs["conversation_id"],
            )
            reply_id = await conn.fetchval(
                """INSERT INTO messages(conversation_id, seq, role, content,
                                        visible_content)
                   VALUES($1,$2,'assistant',$3,$4) RETURNING id""",
                gs["conversation_id"], seq, result.text, visible,
            )
            await conn.execute(
                """UPDATE turns SET status='done', reply_message_id=$2, leaked=$3,
                       prompt_tokens=$4, completion_tokens=$5, latency_ms=$6,
                       model=$7, finished_at=now()
                   WHERE id=$1""",
                turn_id, reply_id, leaked, result.prompt_tokens,
                result.completion_tokens, result.latency_ms, result.model,
            )
        solve = None
        if leaked:
            solve = await _record_solve(conn, user_id, access, "auto", turn_id)
        stats = await conversation_stats(conn, gs["conversation_id"])
        solved = await scope_solved(conn, user_id, access.scope, access.challenge_id)
        return TurnOutcome(
            status="done", reply=visible, leaked=leaked,
            solved=solved is not None, solve=solve,
            tokens=stats["tokens"], attempts=stats["attempts"],
            turn_tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=result.latency_ms,
            extras=await _extras(conn, access, gs),
        )


async def _fail_turn(pool: asyncpg.Pool, turn_id, user_msg_id, kind: str) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE turns SET status='error', error_kind=$2, finished_at=now()
                   WHERE id=$1""", turn_id, kind)
            await conn.execute(
                "UPDATE messages SET in_context=false WHERE id=$1", user_msg_id)


async def _replay(conn, gs, access, turn_row) -> TurnOutcome:
    """Idempotent replay of a finished turn."""
    reply = ""
    if turn_row["reply_message_id"]:
        reply = await conn.fetchval(
            "SELECT visible_content FROM messages WHERE id=$1",
            turn_row["reply_message_id"]) or ""
    stats = await conversation_stats(conn, gs["conversation_id"])
    solved = await scope_solved(conn, str(gs["user_id"]), access.scope,
                                access.challenge_id)
    return TurnOutcome(
        status="blocked" if turn_row["status"] == "blocked" else "done",
        reply=reply, leaked=turn_row["leaked"], solved=solved is not None,
        tokens=stats["tokens"], attempts=stats["attempts"],
        latency_ms=turn_row["latency_ms"],
        extras=await _extras(conn, access, gs),
    )


async def _extras(conn, access: ChallengeAccess, gs) -> dict:
    """Level-specific UI extras. L3 shows accumulated fragment state."""
    if not access.config.get("ui", {}).get("show_fragments"):
        return {}
    import re
    define = re.compile(
        r"\b(?:define|let|set)\s+([a-zA-Z_]\w*)\s*(?:=|:|\bto\b|\bbe\b|\bmeans\b)\s*(.+)",
        re.IGNORECASE,
    )
    rows = await conn.fetch(
        """SELECT content FROM messages
           WHERE conversation_id=$1 AND role='user' AND in_context ORDER BY seq""",
        gs["conversation_id"],
    )
    frags: dict[str, str] = {}
    for r in rows:
        for line in r["content"].splitlines():
            m = define.search(line.strip())
            if m:
                frags[m.group(1)] = m.group(2).strip().strip("\"'")[:80]
    return {"fragments": [{"name": k, "value": v} for k, v in frags.items()]}


# --------------------------------------------------------------------------
# hints & submissions
# --------------------------------------------------------------------------

async def unlock_hint(
    conn: asyncpg.Connection, user_id: str, access: ChallengeAccess, hint_index: int
) -> dict:
    hints = access.config.get("hints", [])
    if hint_index >= len(hints):
        raise ApiError("no such hint", 404, code="not_found")
    if hint_index > 0:
        prev = await conn.fetchval(
            """SELECT 1 FROM hint_unlocks WHERE user_id=$1 AND scope=$2
               AND challenge_id=$3 AND hint_index=$4""",
            user_id, access.scope, access.challenge_id, hint_index - 1,
        )
        if not prev:
            raise ApiError("unlock hints in order", 409, code="conflict")
    # hint deductions only ever apply to ranked play
    cost = int(hints[hint_index].get("cost", 0)) if access.mode == "ranked" else 0
    await conn.execute(
        """INSERT INTO hint_unlocks(user_id, scope, challenge_id, hint_index, cost)
           VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""",
        user_id, access.scope, access.challenge_id, hint_index, cost,
    )
    return {"hint_index": hint_index, "text": hints[hint_index]["text"], "cost": cost}


async def unlocked_hints(
    conn: asyncpg.Connection, user_id: str, scope: str, challenge_id: str, config: dict
) -> list[dict]:
    rows = await conn.fetch(
        """SELECT hint_index, cost FROM hint_unlocks
           WHERE user_id=$1 AND scope=$2 AND challenge_id=$3 ORDER BY hint_index""",
        user_id, scope, challenge_id,
    )
    hints = config.get("hints", [])
    return [
        {"hint_index": r["hint_index"], "cost": r["cost"],
         "text": hints[r["hint_index"]]["text"] if r["hint_index"] < len(hints) else ""}
        for r in rows
    ]


async def submit_flag(
    conn: asyncpg.Connection, user_id: str, access: ChallengeAccess, submitted: str
) -> dict:
    flag_row = await conn.fetchrow(
        "SELECT flag FROM player_flags WHERE user_id=$1 AND scope=$2 AND challenge_id=$3",
        user_id, access.scope, access.challenge_id,
    )
    # No flag issued yet (player never played a turn) -> nothing can match.
    correct = bool(flag_row and submission_matches(submitted, flag_row["flag"]))
    await conn.execute(
        """INSERT INTO flag_submissions(user_id, scope, challenge_id, submitted, correct)
           VALUES($1,$2,$3,$4,$5)""",
        user_id, access.scope, access.challenge_id, submitted[:2000], correct,
    )
    solve = None
    if correct:
        solve = await _record_solve(conn, user_id, access, "submit", None)
    return {"correct": correct, "solve": solve}
