"""Level Postmortem engine: breach debrief, one scored decision, the fix.

Unlocked by SOLVING a level, so it can never spoil an unsolved one or
undercut the paid hints on levels 6-9. Three properties worth keeping:

  * No inference. The debrief is authored copy in
    challenges/postmortems.py plus deterministic evidence drawn from the
    player's own winning turn. It costs nothing per player, reads the same
    for everyone, and nothing the player typed can steer it.
  * The answer is final. `postmortems` has one row per
    (player, scope, challenge) and the first INSERT wins, so the award
    cannot be farmed by re-answering. A repeat post replays the stored
    result instead of erroring.
  * Option feedback is withheld until an answer is in, the same discipline
    as hint text: the pre-answer payload carries labels only, never the
    verdicts or the notes, so the correct option cannot be read off the
    wire.
"""

from __future__ import annotations

import re

import asyncpg

from .challenges import postmortems as content
from .errors import ApiError
from .scoring import POSTMORTEM_POINTS

EVIDENCE_CHARS = 400


def available(challenge_id: str) -> bool:
    return challenge_id in content.ALL


# --------------------------------------------------------------------------
# evidence: the player's own attempt
# --------------------------------------------------------------------------

def _signals(spec: list[dict], text: str) -> list[str]:
    """Which of this level's techniques the player's message actually shows.
    Deterministic, display-only, and never used to decide anything."""
    out: list[str] = []
    for s in spec:
        try:
            if re.search(s["pattern"], text, re.IGNORECASE):
                out.append(s["label"])
        except re.error:  # a bad authored pattern must not break the debrief
            continue
    return out


async def evidence(
    conn: asyncpg.Connection, user_id: str, scope: str, challenge_id: str
) -> dict | None:
    """The message that beat the level: the player's own text from the turn
    that leaked their flag, falling back to their most recent delivered turn.
    Blocked turns are skipped - the model never saw them."""
    row = await conn.fetchrow(
        """SELECT m.content, m.created_at, t.leaked
           FROM turns t
           JOIN messages m ON m.id = t.user_message_id
           JOIN conversations c ON c.id = t.conversation_id
           JOIN game_sessions gs ON gs.id = c.game_session_id
           WHERE gs.user_id=$1 AND gs.scope=$2 AND gs.challenge_id=$3
             AND t.status='done'
           ORDER BY t.leaked DESC, t.created_at DESC
           LIMIT 1""",
        user_id, scope, challenge_id,
    )
    if row is None:
        return None
    full = row["content"]
    pm = content.ALL[challenge_id]
    return {
        # The excerpt is the player's own text, shown back only to them, and
        # rendered as text by the client (never as markup).
        "text": full[:EVIDENCE_CHARS],
        "truncated": len(full) > EVIDENCE_CHARS,
        "length": len(full),
        "at": row["created_at"].isoformat(),
        "winning": bool(row["leaked"]),
        "signals": _signals(pm.get("signals", []), full),
    }


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------

def _options(pm: dict, answered: bool) -> list[dict]:
    """Pre-answer: labels only. Post-answer: the full strengths-and-limits
    note for every option, which is the teaching half of the exercise."""
    out = []
    for o in pm["question"]["options"]:
        item = {"key": o["key"], "label": o["label"]}
        if answered:
            item["verdict"] = o["verdict"]
            item["note"] = o["note"]
        out.append(item)
    return out


def payload(challenge_id: str, number: int, title: str, record: dict | None,
            ev: dict | None) -> dict:
    pm = content.ALL[challenge_id]
    answered = record is not None
    out = {
        "challenge_id": challenge_id,
        "number": number,
        "title": title,
        "headline": pm["headline"],
        "breach": pm["breach"],
        "standards": pm["standards"],
        "evidence": ev,
        "question": {"stem": pm["question"]["stem"],
                     "options": _options(pm, answered)},
        "award": POSTMORTEM_POINTS,
        "answered": answered,
        "choice": record["choice"] if answered else None,
        "correct": bool(record["correct"]) if answered else None,
        "points": int(record["points"]) if answered else 0,
    }
    if answered:
        out["answer_key"] = content.answer_key(challenge_id)
        out["fix"] = pm["fix"]
    return out


async def status(conn: asyncpg.Connection, user_id: str, scope: str,
                 challenge_id: str, solved: bool) -> dict | None:
    """The small summary the session payload and the level list carry, so the
    UI can render the button and its state without shipping the debrief."""
    if not available(challenge_id):
        return None
    row = await record_for(conn, user_id, scope, challenge_id)
    return {
        "available": solved,
        "answered": row is not None,
        "correct": bool(row["correct"]) if row else None,
        "points": int(row["points"]) if row else 0,
        "award": POSTMORTEM_POINTS,
    }


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

async def record_for(
    conn: asyncpg.Connection, user_id: str, scope: str, challenge_id: str
) -> dict | None:
    row = await conn.fetchrow(
        """SELECT choice, correct, points, created_at FROM postmortems
           WHERE user_id=$1 AND scope=$2 AND challenge_id=$3""",
        user_id, scope, challenge_id,
    )
    return dict(row) if row else None


async def answer(
    conn: asyncpg.Connection, user_id: str, scope: str, event_id: str | None,
    challenge_id: str, choice: str,
) -> dict:
    """Record the one decision. The primary key makes it final: a second post
    replays the stored row rather than re-scoring, so the award is paid at
    most once per (player, scope, challenge)."""
    pm = content.ALL[challenge_id]
    keys = {o["key"] for o in pm["question"]["options"]}
    if choice not in keys:
        raise ApiError("that is not one of the options", 422,
                       code="validation_error")
    correct = choice == content.answer_key(challenge_id)
    points = POSTMORTEM_POINTS if correct else 0
    await conn.execute(
        """INSERT INTO postmortems(user_id, scope, challenge_id, event_id,
                                   choice, correct, points)
           VALUES($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (user_id, scope, challenge_id) DO NOTHING""",
        user_id, scope, challenge_id, event_id, choice, correct, points,
    )
    return await record_for(conn, user_id, scope, challenge_id)


async def scope_points(
    conn: asyncpg.Connection, user_id: str, scope: str
) -> int:
    return int(await conn.fetchval(
        "SELECT coalesce(sum(points),0) FROM postmortems "
        "WHERE user_id=$1 AND scope=$2",
        user_id, scope,
    ))
