"""Gameplay routes: sessions, turns, resets, hints, flag submission.

Every route derives the player from the bearer token and checks ownership of
the game session; session ids are never trusted as authentication.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db, game, pipeline, postmortem, ratelimit
from ..errors import ApiError
from ..schemas import (HintIn, ManifestIn, MessageIn, PostmortemIn,
                       StartSessionIn, SubmitFlagIn, parse)
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
    unlocked = await game.unlocked_hints(conn, user_id, access.scope,
                                         access.challenge_id, cfg)
    # Tool-loop levels (L9) drive an extra composer control from this. Levels
    # without a tool catalogue get no `mcp` key and render exactly as before.
    mcp = None
    if cfg.get("pipeline") == "mcp_agent":
        manifest = await game.installed_manifest(
            conn, user_id, access.scope, access.challenge_id)
        mcp = pipeline.manifest_view(manifest, cfg)
    # The Level Postmortem button's state only. The debrief itself is a
    # separate GET, gated on the solve, so nothing teaching the defence sits
    # in the page before the level is beaten.
    pm = await postmortem.status(conn, user_id, access.scope,
                                 access.challenge_id, solve is not None)
    return {
        "game_session_id": str(gs["id"]),
        "challenge_id": access.challenge_id,
        "mode": access.mode,
        "event_id": access.event_id,
        "generation": gs["generation"],
        "challenge": {
            "title": cfg.get("title"), "subtitle": cfg.get("subtitle", ""),
            "overview": cfg.get("overview", ""),
            "starter": cfg.get("starter"),
            "points": access.points, "version": access.version,
            # Levels that keep no state outside the conversation hide "New
            # chat": it would do exactly what "Reset level" already does.
            "new_chat": bool(cfg.get("ui", {}).get("new_chat", True)),
            # hint texts are paid content: only costs are advertised up front
            "hint_costs": [int(h.get("cost", 0)) for h in cfg.get("hints", [])],
        },
        "hints": unlocked,
        "messages": [
            {"seq": m["seq"], "role": m["role"], "text": m["visible_content"],
             "at": m["created_at"].isoformat()}
            for m in msgs
        ],
        "tokens": stats["tokens"], "attempts": stats["attempts"],
        "solved": solve is not None,
        "solve": game.solve_payload(solve) if solve else None,
        **({"mcp": mcp} if mcp else {}),
        **({"postmortem": pm} if pm else {}),
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
    outcome = await game.play_turn(
        db.pool(), user.id, gsid, body.client_msg_id, body.text,
        attachment=body.attachment.model_dump() if body.attachment else None)
    # Deliberately no `leaked`/`solve` here: a turn response must not oracle
    # whether the reply contains the flag. The solve (and the solved banner)
    # comes only from an explicit submission at /submit. `solved` reflects a
    # previously recorded solve, so it stays truthful without leaking intel.
    payload = {
        "status": outcome.status,
        "reply": outcome.reply,
        "solved": outcome.solved,
        "tokens": outcome.tokens,
        "attempts": outcome.attempts,
        "turn_tokens": outcome.turn_tokens,
        "latency_ms": outcome.latency_ms,
        **({"extras": outcome.extras} if outcome.extras else {}),
    }
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
        # Reset wipes persistent memory for this challenge; new-chat keeps it
        # (Level 8's distinction). Harmless for levels without memory.
        await game.rotate_conversation(conn, gs["id"],
                                       wipe_memory_too=(action == "reset"))
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


async def set_tools(request: Request) -> JSONResponse:
    """Install, replace or uninstall the player's MCP server for a challenge.

    The manifest's shape is validated (names, counts, lengths); its CONTENT is
    deliberately not filtered, because a tool description is exactly what this
    level asks the player to weaponise. Returns the same view the session
    state carries, so the UI can re-render from one response.
    """
    gsid = request.path_params["gsid"]
    body = await parse(request, ManifestIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        gs = await game.owned_game_session(conn, user.id, gsid)
        access = await game.resolve_access(
            conn, user.id, gs["challenge_id"], gs["mode"],
            gs["event_id"] and str(gs["event_id"]))
        if access.config.get("pipeline") != "mcp_agent":
            raise ApiError("this level has no MCP tool servers", 400,
                           code="unsupported")
        if body.manifest is None:
            await game.uninstall_manifest(conn, user.id, access.scope,
                                          access.challenge_id)
            stored = None
        else:
            try:
                stored = pipeline.validate_manifest(body.manifest, access.config)
            except pipeline.ManifestError as e:
                raise ApiError(str(e), 422, code="invalid_manifest")
            await game.install_manifest(conn, user.id, access.scope,
                                        access.challenge_id, stored)
    return JSONResponse(pipeline.manifest_view(stored, access.config))


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


async def _postmortem_access(conn, request, gsid):
    """Shared gate: own the session, be allowed to play the level, and have
    SOLVED it. Both postmortem routes go through here, so the debrief cannot
    be read ahead of the solve by calling the other verb."""
    user = await require_user(conn, request)
    gs = await game.owned_game_session(conn, user.id, gsid)
    access = await game.resolve_access(
        conn, user.id, gs["challenge_id"], gs["mode"],
        gs["event_id"] and str(gs["event_id"]))
    if not postmortem.available(access.challenge_id):
        raise ApiError("this level has no postmortem", 404, code="not_found")
    solve = await game.scope_solved(conn, user.id, access.scope,
                                    access.challenge_id)
    if solve is None:
        raise ApiError("solve this level to unlock its postmortem", 403,
                       code="postmortem_locked")
    return user, access


def _level_number(challenge_id: str) -> int:
    try:
        return int(challenge_id[1:])
    except (ValueError, IndexError):
        return 0


async def get_postmortem(request: Request) -> JSONResponse:
    """The debrief: what broke, one decision, the fix. No model call, and the
    option verdicts stay server-side until an answer is in."""
    gsid = request.path_params["gsid"]
    async with db.pool().acquire() as conn:
        user, access = await _postmortem_access(conn, request, gsid)
        record = await postmortem.record_for(conn, user.id, access.scope,
                                             access.challenge_id)
        ev = await postmortem.evidence(conn, user.id, access.scope,
                                       access.challenge_id)
    return JSONResponse(postmortem.payload(
        access.challenge_id, _level_number(access.challenge_id),
        access.config.get("title", ""), record, ev))


async def answer_postmortem(request: Request) -> JSONResponse:
    """Record the defensive decision and award its points. The answer is
    final: a repeat post replays the stored result instead of re-scoring."""
    gsid = request.path_params["gsid"]
    body = await parse(request, PostmortemIn)
    async with db.pool().acquire() as conn:
        user, access = await _postmortem_access(conn, request, gsid)
        record = await postmortem.answer(
            conn, user.id, access.scope, access.event_id,
            access.challenge_id, body.choice)
        ev = await postmortem.evidence(conn, user.id, access.scope,
                                       access.challenge_id)
    return JSONResponse(postmortem.payload(
        access.challenge_id, _level_number(access.challenge_id),
        access.config.get("title", ""), record, ev))


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
        pms = await conn.fetch(
            """SELECT challenge_id, scope, choice, correct, points, created_at
               FROM postmortems WHERE user_id=$1 ORDER BY created_at""",
            user.id,
        )
    return JSONResponse({
        "solves": [
            {"challenge_id": r["challenge_id"], "scope": r["scope"],
             "mode": r["mode"], **game.solve_payload(dict(r))}
            for r in rows
        ],
        "postmortems": [
            {"challenge_id": r["challenge_id"], "scope": r["scope"],
             "choice": r["choice"], "correct": r["correct"],
             "points": r["points"], "at": r["created_at"].isoformat()}
            for r in pms
        ],
    })
