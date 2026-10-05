"""Admin APIs. Every route requires the admin role; every mutation is audited.

The LLM has no path into any of these: administrative actions happen only
through these authenticated endpoints.
"""

from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from .. import db, providers, scoring
from ..audit import record
from ..errors import ApiError
from ..schemas import (EventChallengeIn, EventIn, EventPatchIn, FreezeIn,
                       InviteIn, PublishVersionIn, parse)
from ..security import require_admin


def _ts(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(f"{field} must be an ISO-8601 timestamp", 422,
                       code="validation_error")


async def create_event(request: Request) -> JSONResponse:
    body = await parse(request, EventIn)
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        starts, ends = _ts(body.starts_at, "starts_at"), _ts(body.ends_at, "ends_at")
        if ends <= starts:
            raise ApiError("ends_at must be after starts_at", 422,
                           code="validation_error")
        try:
            row = await conn.fetchrow(
                """INSERT INTO events(slug, name, description, registration_open,
                                      invite_only, starts_at, ends_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
                body.slug, body.name, body.description, body.registration_open,
                body.invite_only, starts, ends,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise ApiError("slug already in use", 409, code="conflict")
            raise
        await record(conn, admin.id, "event.create", str(row["id"]),
                     body.model_dump())
    return JSONResponse({"id": str(row["id"])}, status_code=201)


async def patch_event(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    body = await parse(request, EventPatchIn)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise ApiError("nothing to update", 422, code="validation_error")
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        ev = await conn.fetchrow("SELECT id FROM events WHERE id=$1::uuid", event_id)
        if ev is None:
            raise ApiError("unknown event", 404, code="not_found")
        # Column names come from this fixed whitelist only; values are bound
        # parameters. Unknown keys cannot reach the SQL string.
        allowed = {"name", "description", "registration_open", "invite_only",
                   "starts_at", "ends_at", "paused", "leaderboard_visible"}
        sets, args = [], []
        for key, val in updates.items():
            if key not in allowed:
                raise ApiError(f"field '{key}' cannot be updated", 422,
                               code="validation_error")
            if key in ("starts_at", "ends_at"):
                val = _ts(val, key)
            args.append(val)
            sets.append(f"{key}=${len(args)}")
        args.append(ev["id"])
        await conn.execute(
            f"UPDATE events SET {', '.join(sets)} WHERE id=${len(args)}",  # nosec B608: columns whitelisted above, values parameterised
            *args)
        await record(conn, admin.id, "event.update", event_id, updates)
    return JSONResponse({"ok": True})


async def set_event_challenge(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    body = await parse(request, EventChallengeIn)
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        if body.version is None:
            ver = await conn.fetchrow(
                """SELECT id, version FROM challenge_versions
                   WHERE challenge_id=$1 AND published_at IS NOT NULL
                   ORDER BY version DESC LIMIT 1""", body.challenge_id)
        else:
            ver = await conn.fetchrow(
                """SELECT id, version FROM challenge_versions
                   WHERE challenge_id=$1 AND version=$2 AND published_at IS NOT NULL""",
                body.challenge_id, body.version)
        if ver is None:
            raise ApiError("no published version for this challenge", 404,
                           code="not_found")
        opens = _ts(body.opens_at, "opens_at") if body.opens_at else None
        closes = _ts(body.closes_at, "closes_at") if body.closes_at else None
        await conn.execute(
            """INSERT INTO event_challenges(event_id, challenge_id, version_id,
                                            points, enabled, opens_at, closes_at)
               VALUES($1::uuid,$2,$3,$4,$5,$6,$7)
               ON CONFLICT (event_id, challenge_id) DO UPDATE SET
                 version_id=excluded.version_id, points=excluded.points,
                 enabled=excluded.enabled, opens_at=excluded.opens_at,
                 closes_at=excluded.closes_at""",
            event_id, body.challenge_id, ver["id"], body.points, body.enabled,
            opens, closes,
        )
        await record(conn, admin.id, "event.challenge.set", event_id,
                     {**body.model_dump(), "pinned_version": ver["version"]})
    return JSONResponse({"ok": True, "pinned_version": ver["version"]})


async def create_invites(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    body = await parse(request, InviteIn)
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        expires = _ts(body.expires_at, "expires_at") if body.expires_at else None
        codes = []
        for _ in range(body.count):
            code = f"VOLT-{secrets.token_urlsafe(9)}"
            await conn.execute(
                """INSERT INTO event_invites(event_id, code, max_uses, expires_at)
                   VALUES($1::uuid,$2,$3,$4)""",
                event_id, code, body.max_uses, expires)
            codes.append(code)
        await record(conn, admin.id, "event.invites.create", event_id,
                     {"count": body.count, "max_uses": body.max_uses})
    return JSONResponse({"codes": codes}, status_code=201)


async def list_enrollments(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        await require_admin(conn, request)
        rows = await conn.fetch(
            """SELECT u.id, u.email, u.display_name, u.role, en.created_at
               FROM enrollments en JOIN users u ON u.id=en.user_id
               WHERE en.event_id=$1::uuid ORDER BY en.created_at""",
            event_id)
    return JSONResponse({"enrollments": [
        {"user_id": str(r["id"]), "email": r["email"],
         "display_name": r["display_name"], "role": r["role"],
         "enrolled_at": r["created_at"].isoformat()} for r in rows]})


async def remove_enrollment(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    user_id = request.path_params["user_id"]
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        n = await conn.execute(
            "DELETE FROM enrollments WHERE event_id=$1::uuid AND user_id=$2::uuid",
            event_id, user_id)
        await record(conn, admin.id, "event.enrollment.remove", event_id,
                     {"user_id": user_id})
    return JSONResponse({"ok": True, "removed": n.endswith("1")})


async def freeze_leaderboard(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    body = await parse(request, FreezeIn)
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        if body.frozen:
            await conn.execute(
                """UPDATE events SET leaderboard_frozen_at=now()
                   WHERE id=$1::uuid AND leaderboard_frozen_at IS NULL""", event_id)
        else:
            await conn.execute(
                "UPDATE events SET leaderboard_frozen_at=NULL WHERE id=$1::uuid",
                event_id)
        await record(conn, admin.id, "event.leaderboard.freeze", event_id,
                     {"frozen": body.frozen})
    return JSONResponse({"ok": True})


async def list_challenges(request: Request) -> JSONResponse:
    async with db.pool().acquire() as conn:
        await require_admin(conn, request)
        rows = await conn.fetch(
            """SELECT cv.challenge_id, c.number, cv.version, cv.published_at,
                      cv.created_at, cv.config
               FROM challenge_versions cv JOIN challenges c ON c.id=cv.challenge_id
               ORDER BY c.number, cv.version""")
    out = []
    for r in rows:
        cfg = r["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        out.append({
            "challenge_id": r["challenge_id"], "number": r["number"],
            "version": r["version"],
            "published": r["published_at"] is not None,
            "created_at": r["created_at"].isoformat(),
            "title": cfg.get("title"),
            "default_points": cfg.get("default_points"),
        })
    return JSONResponse({"versions": out})


async def publish_version(request: Request) -> JSONResponse:
    """Publish a new immutable version of a challenge's config."""
    body = await parse(request, PublishVersionIn)
    required = {"title", "briefing", "lesson", "system_prompt", "default_points",
                "hints", "filters", "model_params"}
    missing = required - set(body.config)
    if missing:
        raise ApiError(f"config missing keys: {', '.join(sorted(missing))}", 422,
                       code="validation_error")
    if "{flag}" not in body.config["system_prompt"]:
        raise ApiError("system_prompt must contain the {flag} placeholder", 422,
                       code="validation_error")
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        row = await conn.fetchrow(
            """INSERT INTO challenge_versions(challenge_id, version, config,
                                              published_at, created_by)
               SELECT $1, coalesce(max(version),0)+1, $2::jsonb, now(), $3
               FROM challenge_versions WHERE challenge_id=$1
               RETURNING version""",
            body.challenge_id, json.dumps(body.config), admin.id,
        )
        await record(conn, admin.id, "challenge.publish", body.challenge_id,
                     {"version": row["version"]})
    return JSONResponse({"challenge_id": body.challenge_id,
                         "version": row["version"]}, status_code=201)


async def event_stats(request: Request) -> JSONResponse:
    """Per-level gameplay stats + operator inference metrics."""
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        await require_admin(conn, request)
        per_level = await conn.fetch(
            """SELECT gs.challenge_id,
                      count(DISTINCT gs.user_id) AS starts,
                      count(t.id) FILTER (WHERE t.status='done') AS model_turns,
                      count(t.id) FILTER (WHERE t.status='blocked') AS blocked_turns,
                      count(t.id) FILTER (WHERE t.status='error') AS error_turns,
                      count(t.id) FILTER (WHERE t.status IN ('done','blocked')
                                           AND NOT t.leaked) AS attempts,
                      coalesce(sum(t.prompt_tokens + t.completion_tokens),0) AS tokens,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY t.latency_ms)
                          FILTER (WHERE t.latency_ms IS NOT NULL) AS p50_latency,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY t.latency_ms)
                          FILTER (WHERE t.latency_ms IS NOT NULL) AS p95_latency
               FROM game_sessions gs
               LEFT JOIN conversations c ON c.game_session_id = gs.id
               LEFT JOIN turns t ON t.conversation_id = c.id
               WHERE gs.event_id=$1::uuid
               GROUP BY gs.challenge_id ORDER BY gs.challenge_id""",
            event_id)
        solves = await conn.fetch(
            """SELECT s.challenge_id, count(*) AS solves,
                      count(*) FILTER (WHERE s.method='auto') AS auto_solves,
                      percentile_cont(0.5) WITHIN GROUP
                          (ORDER BY extract(epoch FROM s.solved_at - gs.created_at))
                          AS median_time_to_solve_s
               FROM solves s
               JOIN game_sessions gs ON gs.user_id = s.user_id
                    AND gs.challenge_id = s.challenge_id AND gs.scope = s.scope
               WHERE s.event_id=$1::uuid AND s.mode='ranked'
               GROUP BY s.challenge_id""",
            event_id)
        hints = await conn.fetch(
            """SELECT challenge_id, count(*) AS hints_unlocked,
                      coalesce(sum(cost),0) AS hint_cost_total
               FROM hint_unlocks WHERE scope=$1 GROUP BY challenge_id""",
            event_id)
    solve_map = {r["challenge_id"]: r for r in solves}
    hint_map = {r["challenge_id"]: r for r in hints}
    levels = []
    for r in per_level:
        cid = r["challenge_id"]
        s, h = solve_map.get(cid), hint_map.get(cid)
        levels.append({
            "challenge_id": cid, "starts": int(r["starts"]),
            "model_turns": int(r["model_turns"]),
            "blocked_turns": int(r["blocked_turns"]),
            "error_turns": int(r["error_turns"]), "attempts": int(r["attempts"]),
            "tokens": int(r["tokens"]),
            "p50_latency_ms": (float(r["p50_latency"])
                               if r["p50_latency"] is not None else None),
            "p95_latency_ms": (float(r["p95_latency"])
                               if r["p95_latency"] is not None else None),
            "solves": int(s["solves"]) if s else 0,
            "auto_solves": int(s["auto_solves"]) if s else 0,
            "median_time_to_solve_s": (float(s["median_time_to_solve_s"])
                                       if s and s["median_time_to_solve_s"] else None),
            "hints_unlocked": int(h["hints_unlocked"]) if h else 0,
            "hint_cost_total": int(h["hint_cost_total"]) if h else 0,
        })
    return JSONResponse({"levels": levels,
                         "inference_queue_depth": providers.queue_depth()})


async def export_results(request: Request) -> PlainTextResponse:
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        admin = await require_admin(conn, request)
        rows = await conn.fetch(
            """SELECT u.display_name, u.email, s.challenge_id, s.points,
                      s.bonus, s.hints_cost, s.attempts, s.tokens_spent,
                      s.method, s.solved_at
               FROM solves s JOIN users u ON u.id=s.user_id
               WHERE s.event_id=$1::uuid AND s.mode='ranked'
               ORDER BY s.solved_at""",
            event_id)
        await record(conn, admin.id, "event.export", event_id, {"rows": len(rows)})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["display_name", "email", "challenge_id", "points", "bonus",
                "hints_cost", "net_points", "attempts", "tokens_spent",
                "method", "solved_at"])
    for r in rows:
        w.writerow([r["display_name"], r["email"], r["challenge_id"], r["points"],
                    r["bonus"], r["hints_cost"],
                    scoring.net_score(r["points"], r["bonus"], r["hints_cost"]),
                    r["attempts"], r["tokens_spent"],
                    r["method"], r["solved_at"].isoformat()])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


async def audit_trail(request: Request) -> JSONResponse:
    async with db.pool().acquire() as conn:
        await require_admin(conn, request)
        rows = await conn.fetch(
            """SELECT a.id, a.action, a.target, a.details, a.created_at,
                      u.email AS actor
               FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id
               ORDER BY a.id DESC LIMIT 200""")
    return JSONResponse({"entries": [
        {"id": r["id"], "actor": r["actor"], "action": r["action"],
         "target": r["target"],
         "details": r["details"] if isinstance(r["details"], dict)
                    else json.loads(r["details"]),
         "at": r["created_at"].isoformat()} for r in rows]})
