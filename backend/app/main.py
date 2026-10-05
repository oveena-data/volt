"""VOLT API (Starlette ASGI app).

Why Starlette and not FastAPI? VOLT is meant to run self-hosted with zero
friction. Starlette is FastAPI's foundation and ships in far more environments;
building directly on it means the whole game runs with no package install at
all beyond uvicorn. Request bodies are validated by hand (they're tiny).

Routes:
  GET  /api/health
  GET  /api/levels?sid=...          -> player-facing level list (no flags)
  POST /api/session                 -> create or resume a player session
  POST /api/levels/{lid}/message    -> play a turn
  POST /api/levels/{lid}/reset      -> full reset (wipes everything)
  POST /api/levels/{lid}/new-chat   -> clear chat, keep persistent memory
  GET  /api/leaderboard
  GET  /api/me?sid=...
  GET  /                            -> serves the single-page frontend
"""

from __future__ import annotations

import contextlib
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .config import settings
from .core import store
from .core.engine import Engine
from .levels.registry import LEVELS, ORDERED

engine = Engine(LEVELS)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
_FRONTEND = os.path.join(_STATIC_DIR, "index.html")


# ---------- helpers ----------

def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=status)


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _player_level_view(lid: str, scores: dict[str, int]) -> dict:
    m = LEVELS[lid].meta
    return {
        "id": m.id,
        "number": m.number,
        "title": m.title,
        "codename": m.codename,
        "technique": m.technique,
        "briefing": m.briefing,
        "lesson": m.lesson,
        "hint": m.hint,
        "base_points": m.score.base_points,
        "best": scores.get(m.id, 0),
        "cleared": scores.get(m.id, 0) > 0,
        "starter": m.starter,
    }


def _session_or_none(sid: str | None):
    return engine._sessions.get(sid) if sid else None  # noqa: SLF001


# ---------- routes ----------

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "provider": settings.provider, "levels": len(LEVELS)})


async def create_session(request: Request) -> JSONResponse:
    data = await _body(request)
    player = (data.get("player") or "").strip() or "anon"
    ps = engine.get_session(data.get("sid"), player)
    ps.player = player
    return JSONResponse({"sid": ps.sid, "player": ps.player})


async def list_levels(request: Request) -> JSONResponse:
    sid = request.query_params.get("sid")
    ps = _session_or_none(sid)
    scores = store.player_scores(ps.player) if ps else {}
    return JSONResponse(
        {
            "levels": [_player_level_view(l.meta.id, scores) for l in ORDERED],
            "total": sum(scores.values()),
        }
    )


async def post_message(request: Request) -> JSONResponse:
    lid = request.path_params["lid"]
    if lid not in LEVELS:
        return _err("Unknown level", 404)
    data = await _body(request)
    sid = data.get("sid")
    text = (data.get("text") or "").strip()
    if not text:
        return _err("Empty message")
    ps = _session_or_none(sid)
    if ps is None:
        return _err("Unknown session; create one first")
    result = await engine.play_turn(ps, lid, text)
    return JSONResponse(
        {
            "reply": result.reply,
            "leaked": result.leaked,
            "blocked": result.blocked,
            "tokens_spent": result.tokens_spent,
            "attempts": result.attempts,
            "solved": result.solved,
            "score": result.score,
            "best": result.best,
            "meta": result.meta,
        }
    )


async def reset_level(request: Request) -> JSONResponse:
    lid = request.path_params["lid"]
    if lid not in LEVELS:
        return _err("Unknown level", 404)
    data = await _body(request)
    ps = _session_or_none(data.get("sid"))
    if ps is None:
        return _err("Unknown session")
    engine.reset_level(ps, lid)
    return JSONResponse({"ok": True, "tokens_spent": 0, "attempts": 0})


async def new_chat(request: Request) -> JSONResponse:
    lid = request.path_params["lid"]
    if lid not in LEVELS:
        return _err("Unknown level", 404)
    data = await _body(request)
    ps = _session_or_none(data.get("sid"))
    if ps is None:
        return _err("Unknown session")
    engine.new_chat(ps, lid)
    return JSONResponse({"ok": True, "tokens_spent": 0, "attempts": 0})


async def get_leaderboard(request: Request) -> JSONResponse:
    return JSONResponse({"entries": store.leaderboard()})


async def me(request: Request) -> JSONResponse:
    sid = request.query_params.get("sid")
    ps = _session_or_none(sid)
    if ps is None:
        return _err("Unknown session")
    scores = store.player_scores(ps.player)
    return JSONResponse({"player": ps.player, "total": sum(scores.values()), "scores": scores})


async def index(request: Request):
    if os.path.exists(_FRONTEND):
        return FileResponse(_FRONTEND)
    return JSONResponse({"detail": "frontend not built; see static/index.html"}, status_code=404)


async def static_asset(request: Request):
    """Serve files from static/ by name (e.g. /app.jsx). Path-traversal safe."""
    name = request.path_params["asset"]
    if "/" in name or ".." in name:
        return _err("Not found", 404)
    path = os.path.join(_STATIC_DIR, name)
    if os.path.exists(path) and os.path.isfile(path):
        return FileResponse(path)
    return _err("Not found", 404)


def _startup() -> None:
    store.init_db()


@contextlib.asynccontextmanager
async def _lifespan(app):
    _startup()
    yield


routes = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/session", create_session, methods=["POST"]),
    Route("/api/levels", list_levels, methods=["GET"]),
    Route("/api/levels/{lid}/message", post_message, methods=["POST"]),
    Route("/api/levels/{lid}/reset", reset_level, methods=["POST"]),
    Route("/api/levels/{lid}/new-chat", new_chat, methods=["POST"]),
    Route("/api/leaderboard", get_leaderboard, methods=["GET"]),
    Route("/api/me", me, methods=["GET"]),
    Route("/", index, methods=["GET"]),
    Route("/{asset:str}", static_asset, methods=["GET"]),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list + ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

app = Starlette(routes=routes, middleware=middleware, lifespan=_lifespan)
