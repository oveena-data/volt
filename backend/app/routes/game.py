"""Gameplay routes: sessions, turns, resets, hints, flag submission.

Every route derives the player from the bearer token and checks ownership of
the game session; session ids are never trusted as authentication.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db, game, ratelimit
from ..errors import ApiError
from ..schemas import HintIn, MessageIn, StartSessionIn, SubmitFlagIn, parse
from ..security import require_user


async def start_session(request: Request) -> JSONResponse:
    body = await parse(request, StartSessionIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        access = await game.resolve_access(conn, user.id, body.challenge_id,
                                           body.mode, body.event_id)
        gs = await game.get_or_create_game_session(conn, user.id, access)
        state = await _session_state(conn, user.id, gs, access)
    return JSONResponse(state)


async def _session_state(conn, user_id: str, gs: dict, access) -> dict:
    stats = await game.conversation_stats(conn, gs["conversation_id"])
    msgs = await conn.fetch(
        """SELECT seq, role, visible_content, created_at FROM messages
           WHERE conversation_id=$1 ORDER BY seq""",
        gs["conversation_id"],
    )
    solve = await game.scope_solved(conn, user_id, access.scope, access.challenge_id)
    cfg = access.config
    return {
        "game_session_id": str(gs["id"]),
        "challenge_id": access.challenge_id,
        "mode": access.mode,
        "event_id": access.event_id,
        "generation": gs["generation"],
        "challenge": {
            "title": cfg.get("title"), "subtitle": cfg.get("subtitle", ""),
            "starter": cfg.get("starter"),
            "points": access.points, "version": access.version,
        },
        "messages": [
            {"seq": m["seq"], "role": m["role"], "text": m["visible_content"],
             "at": m["created_at"].isoformat()}
            for m in msgs
        ],
        "tokens": stats["tokens"], "attempts": stats["attempts"],
        "solved": solve is not None,
        "solve": game.solve_payload(solve) if solve else None,
    }


async def get_session(request: Request) -> JSONResponse:
    gsid = request.path_params["gsid"]
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        gs = await game.owned_game_session(conn, user.id, gsid)
        access = await game.resolve_access(
            conn, user.id, gs["challenge_id"], gs["mode"],
            gs["event_id"] and str(gs["event_id"]))
        gs["id"] = gsid
        state = await _session_state(conn, user.id, gs, access)
    return JSONResponse(state)


async def post_message(request: Request) -> JSONResponse:
    gsid = request.path_params["gsid"]
    body = await parse(request, MessageIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
    ratelimit.check_turn_rate(user.id)
    outcome = await game.play_turn(db.pool(), user.id, gsid,
                                   body.client_msg_id, body.text)
    payload = {
        "status": outcome.status,
        "reply": outcome.reply,
        "leaked": outcome.leaked,
        "solved": outcome.solved,
        "tokens": outcome.tokens,
        "attempts": outcome.attempts,
        "turn_tokens": outcome.turn_tokens,
        "latency_ms": outcome.latency_ms,
        **({"extras": outcome.extras} if outcome.extras else {}),
    }
    if outcome.solve:
        payload["solve"] = game.solve_payload(outcome.solve)
    if outcome.status == "error":
        payload["error_kind"] = outcome.error_kind
        payload["error_message"] = outcome.error_message
        return JSONResponse(payload, status_code=502)
    return JSONResponse(payload)


async def _rotate(request: Request, action: str) -> JSONResponse:
    gsid = request.path_params["gsid"]
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        gs = await game.owned_game_session(conn, user.id, gsid)
        await game.rotate_conversation(conn, gs["id"])
        access = await game.resolve_access(
            conn, user.id, gs["challenge_id"], gs["mode"],
            gs["event_id"] and str(gs["event_id"]))
        gs2 = await game.owned_game_session(conn, user.id, gsid)
        gs2["id"] = gsid
        state = await _session_state(conn, user.id, gs2, access)
    return JSONResponse(state)


async def reset_level(request: Request) -> JSONResponse:
    """Full reset: destroys the conversation and all accumulated in-level
    state (L3 fragments die here). Ranked solves, competition history and
    hint deductions are never erased by a reset."""
    return await _rotate(request, "reset")


async def new_chat(request: Request) -> JSONResponse:
    """Clears the conversation. Identical to reset for levels 1-5; from
    level 8 on it will preserve persistent memory where reset wipes it."""
    return await _rotate(request, "new_chat")


async def unlock_hint(request: Request) -> JSONResponse:
    gsid = request.path_params["gsid"]
    body = await parse(request, HintIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        gs = await game.owned_game_session(conn, user.id, gsid)
        access = await game.resolve_access(
            conn, user.id, gs["challenge_id"], gs["mode"],
            gs["event_id"] and str(gs["event_id"]))
        hint = await game.unlock_hint(conn, user.id, access, body.hint_index)
    return JSONResponse(hint)


async def submit_flag(request: Request) -> JSONResponse:
    gsid = request.path_params["gsid"]
    body = await parse(request, SubmitFlagIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        gs = await game.owned_game_session(conn, user.id, gsid)
        access = await game.resolve_access(
            conn, user.id, gs["challenge_id"], gs["mode"],
            gs["event_id"] and str(gs["event_id"]))
        result = await game.submit_flag(conn, user.id, access, body.flag)
    payload = {"correct": result["correct"]}
    if result["solve"]:
        payload["solve"] = game.solve_payload(result["solve"])
    return JSONResponse(payload)


async def my_progress(request: Request) -> JSONResponse:
    """Player progress across practice + events."""
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        rows = await conn.fetch(
            """SELECT challenge_id, scope, mode, points, hints_cost, bonus,
                      attempts, tokens_spent, solved_at, method
               FROM solves WHERE user_id=$1 ORDER BY solved_at""",
            user.id,
        )
    return JSONResponse({"solves": [
        {"challenge_id": r["challenge_id"], "scope": r["scope"], "mode": r["mode"],
         **game.solve_payload(dict(r))}
        for r in rows
    ]})
