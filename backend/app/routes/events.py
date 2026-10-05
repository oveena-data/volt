"""Player-facing event, enrolment, leaderboard and activity routes.

Public payloads never contain transcripts, flags, prompts or filter configs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db
from ..audit import record
from ..config import settings
from ..errors import ApiError
from ..schemas import EnrollIn, parse
from ..security import require_user


def _event_payload(row, enrolled: bool | None = None) -> dict:
    out = {
        "id": str(row["id"]), "slug": row["slug"], "name": row["name"],
        "description": row["description"],
        "registration_open": row["registration_open"],
        "invite_only": row["invite_only"],
        "starts_at": row["starts_at"].isoformat(),
        "ends_at": row["ends_at"].isoformat(),
        "paused": row["paused"],
        "leaderboard_visible": row["leaderboard_visible"],
        "leaderboard_frozen": row["leaderboard_frozen_at"] is not None,
    }
    if enrolled is not None:
        out["enrolled"] = enrolled
    return out


async def list_events(request: Request) -> JSONResponse:
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        rows = await conn.fetch(
            """SELECT e.*, (en.user_id IS NOT NULL) AS enrolled
               FROM events e
               LEFT JOIN enrollments en ON en.event_id=e.id AND en.user_id=$1
               ORDER BY e.starts_at DESC""",
            user.id,
        )
    return JSONResponse({"events": [_event_payload(r, r["enrolled"]) for r in rows]})


async def enroll(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    body = await parse(request, EnrollIn)
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        ev = await conn.fetchrow("SELECT * FROM events WHERE id=$1::uuid", event_id)
        if ev is None:
            raise ApiError("unknown event", 404, code="not_found")
        already = await conn.fetchval(
            "SELECT 1 FROM enrollments WHERE user_id=$1 AND event_id=$2",
            user.id, ev["id"])
        if already:
            return JSONResponse({"ok": True, "already_enrolled": True})
        if not ev["registration_open"]:
            raise ApiError("registration for this event is closed", 403,
                           code="registration_closed")
        if datetime.now(timezone.utc) >= ev["ends_at"]:
            raise ApiError("this event has ended", 403, code="event_ended")
        invite_id = None
        if ev["invite_only"]:
            if not body.invite_code:
                raise ApiError("this event is invite-only; an invite code is required",
                               403, code="invite_required")
            async with conn.transaction():
                inv = await conn.fetchrow(
                    """SELECT id, max_uses, used_count, expires_at FROM event_invites
                       WHERE event_id=$1 AND code=$2 FOR UPDATE""",
                    ev["id"], body.invite_code.strip(),
                )
                if (inv is None
                        or inv["used_count"] >= inv["max_uses"]
                        or (inv["expires_at"] and datetime.now(timezone.utc) > inv["expires_at"])):
                    raise ApiError("invalid or exhausted invite code", 403,
                                   code="invite_invalid")
                await conn.execute(
                    "UPDATE event_invites SET used_count=used_count+1 WHERE id=$1",
                    inv["id"])
                invite_id = inv["id"]
                await conn.execute(
                    """INSERT INTO enrollments(user_id, event_id, invite_id)
                       VALUES($1,$2,$3) ON CONFLICT DO NOTHING""",
                    user.id, ev["id"], invite_id)
        else:
            await conn.execute(
                """INSERT INTO enrollments(user_id, event_id) VALUES($1,$2)
                   ON CONFLICT DO NOTHING""",
                user.id, ev["id"])
    return JSONResponse({"ok": True}, status_code=201)


async def event_detail(request: Request) -> JSONResponse:
    """Event + its challenge list for an enrolled player."""
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        ev = await conn.fetchrow("SELECT * FROM events WHERE id=$1::uuid", event_id)
        if ev is None:
            raise ApiError("unknown event", 404, code="not_found")
        enrolled = bool(await conn.fetchval(
            "SELECT 1 FROM enrollments WHERE user_id=$1 AND event_id=$2",
            user.id, ev["id"]))
        payload = _event_payload(ev, enrolled)
        payload["server_time"] = datetime.now(timezone.utc).isoformat()
        if not enrolled:
            return JSONResponse({"event": payload, "challenges": []})
        rows = await conn.fetch(
            """SELECT ec.challenge_id, ec.points, ec.enabled, ec.opens_at, ec.closes_at,
                      c.number, cv.config, cv.version,
                      s.solved_at, s.points AS solve_points, s.hints_cost
               FROM event_challenges ec
               JOIN challenges c ON c.id = ec.challenge_id
               JOIN challenge_versions cv ON cv.id = ec.version_id
               LEFT JOIN solves s ON s.user_id=$2 AND s.scope=$1::text
                                  AND s.challenge_id=ec.challenge_id
               WHERE ec.event_id=$1 ORDER BY c.number""",
            ev["id"], user.id,
        )
        now = datetime.now(timezone.utc)
        challenges = []
        for r in rows:
            cfg = r["config"]
            if isinstance(cfg, str):
                import json
                cfg = json.loads(cfg)
            open_now = (r["enabled"]
                        and (not r["opens_at"] or now >= r["opens_at"])
                        and (not r["closes_at"] or now < r["closes_at"])
                        and ev["starts_at"] <= now < ev["ends_at"])
            challenges.append({
                "challenge_id": r["challenge_id"], "number": r["number"],
                "title": cfg.get("title"), "codename": cfg.get("codename"),
                "technique": cfg.get("technique"), "briefing": cfg.get("briefing"),
                "lesson": cfg.get("lesson"), "points": r["points"],
                "version": r["version"], "available": open_now,
                "starter": cfg.get("starter"),
                "hint_costs": [h.get("cost", 0) for h in cfg.get("hints", [])],
                "solved": r["solved_at"] is not None,
                "solved_at": r["solved_at"].isoformat() if r["solved_at"] else None,
                "net_points": (max(0, r["solve_points"] - r["hints_cost"])
                               if r["solved_at"] else None),
            })
    return JSONResponse({"event": payload, "challenges": challenges})


async def practice_challenges(request: Request) -> JSONResponse:
    """Published challenges for practice mode."""
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        rows = await conn.fetch(
            """SELECT DISTINCT ON (cv.challenge_id)
                      cv.challenge_id, cv.version, cv.config, c.number, s.solved_at
               FROM challenge_versions cv
               JOIN challenges c ON c.id = cv.challenge_id
               LEFT JOIN solves s ON s.user_id=$1 AND s.scope='practice'
                                  AND s.challenge_id=cv.challenge_id
               WHERE cv.published_at IS NOT NULL
               ORDER BY cv.challenge_id, cv.version DESC""",
            user.id,
        )
        challenges = []
        for r in sorted(rows, key=lambda r: r["number"]):
            cfg = r["config"]
            if isinstance(cfg, str):
                import json
                cfg = json.loads(cfg)
            challenges.append({
                "challenge_id": r["challenge_id"], "number": r["number"],
                "title": cfg.get("title"), "codename": cfg.get("codename"),
                "technique": cfg.get("technique"), "briefing": cfg.get("briefing"),
                "lesson": cfg.get("lesson"),
                "points": cfg.get("default_points", 100),
                "version": r["version"], "available": True,
                "starter": cfg.get("starter"),
                "hint_costs": [h.get("cost", 0) for h in cfg.get("hints", [])],
                "solved": r["solved_at"] is not None,
                "solved_at": r["solved_at"].isoformat() if r["solved_at"] else None,
            })
    return JSONResponse({"challenges": challenges})


async def leaderboard(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        ev = await conn.fetchrow("SELECT * FROM events WHERE id=$1::uuid", event_id)
        if ev is None:
            raise ApiError("unknown event", 404, code="not_found")
        if not ev["leaderboard_visible"] and not user.is_admin:
            raise ApiError("the leaderboard is hidden right now", 403,
                           code="leaderboard_hidden")
        cutoff = ev["leaderboard_frozen_at"]
        rows = await conn.fetch(
            """SELECT u.display_name, s.user_id,
                      sum(greatest(0, s.points - s.hints_cost)) AS total,
                      count(*) AS solved, max(s.solved_at) AS last_solve
               FROM solves s JOIN users u ON u.id = s.user_id
               WHERE s.event_id=$1 AND s.mode='ranked'
                 AND ($2::timestamptz IS NULL OR s.solved_at <= $2)
               GROUP BY s.user_id, u.display_name
               ORDER BY total DESC, last_solve ASC""",
            ev["id"], cutoff,
        )
        entries = [
            {"rank": i + 1, "display_name": r["display_name"],
             "total": int(r["total"]), "solved": int(r["solved"]),
             "last_solve": r["last_solve"].isoformat(),
             "me": str(r["user_id"]) == user.id}
            for i, r in enumerate(rows)
        ]
    return JSONResponse({
        "entries": entries,
        "frozen_at": cutoff.isoformat() if cutoff else None,
    })


async def solve_feed(request: Request) -> JSONResponse:
    """Recent ranked solves (no transcripts, no flags). Respects freezing."""
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        user = await require_user(conn, request)
        ev = await conn.fetchrow("SELECT * FROM events WHERE id=$1::uuid", event_id)
        if ev is None:
            raise ApiError("unknown event", 404, code="not_found")
        if not ev["leaderboard_visible"] and not user.is_admin:
            raise ApiError("the leaderboard is hidden right now", 403,
                           code="leaderboard_hidden")
        cutoff = ev["leaderboard_frozen_at"]
        rows = await conn.fetch(
            """SELECT u.display_name, s.challenge_id, c.number, s.solved_at
               FROM solves s
               JOIN users u ON u.id = s.user_id
               JOIN challenges c ON c.id = s.challenge_id
               WHERE s.event_id=$1 AND s.mode='ranked'
                 AND ($2::timestamptz IS NULL OR s.solved_at <= $2)
               ORDER BY s.solved_at DESC LIMIT 25""",
            ev["id"], cutoff,
        )
    return JSONResponse({"solves": [
        {"display_name": r["display_name"], "challenge_id": r["challenge_id"],
         "number": r["number"], "solved_at": r["solved_at"].isoformat()}
        for r in rows
    ]})


async def activity(request: Request) -> JSONResponse:
    """Active-player count: players with a turn inside the activity window
    (VOLT_ACTIVITY_WINDOW_MINUTES, default 5)."""
    event_id = request.path_params["event_id"]
    async with db.pool().acquire() as conn:
        await require_user(conn, request)
        n = await conn.fetchval(
            """SELECT count(DISTINCT gs.user_id)
               FROM turns t
               JOIN conversations cv ON cv.id = t.conversation_id
               JOIN game_sessions gs ON gs.id = cv.game_session_id
               WHERE gs.event_id=$1::uuid
                 AND t.created_at > now() - make_interval(mins => $2)""",
            event_id, settings.activity_window_minutes,
        )
    return JSONResponse({"active_players": int(n),
                         "window_minutes": settings.activity_window_minutes})
