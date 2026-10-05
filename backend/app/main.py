"""VOLT API application (Starlette ASGI).

Production invariants enforced at startup:
  * provider must be a real inference endpoint (mock refuses to boot)
  * flag secret / credentials / CORS must be production-grade
"""

from __future__ import annotations

import contextlib
import logging
import sys

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from . import db
from .challenges.seed import seed_challenges
from .config import settings
from .errors import ApiError, handle_api_error, handle_unexpected
from .routes import admin, auth, events, game, health

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
           '"msg":"%(message)s"}',
    stream=sys.stdout,
)
log = logging.getLogger("volt")


@contextlib.asynccontextmanager
async def lifespan(app):
    problems = settings.validate_production()
    if problems:
        for p in problems:
            log.error("FATAL config: %s", p)
        raise RuntimeError("refusing to start with production misconfiguration: "
                           + "; ".join(problems))
    await db.connect()
    applied = await db.migrate()
    if applied:
        log.info("migrations applied: %s", ", ".join(applied))
    await seed_challenges(db.pool())
    log.info("VOLT backend ready (env=%s provider=%s model=%s)",
             settings.env, settings.provider, settings.model)
    yield
    from . import providers
    await providers.close()
    await db.close()


routes = [
    # health
    Route("/api/healthz", health.healthz, methods=["GET"]),
    Route("/api/readyz", health.readyz, methods=["GET"]),
    # auth
    Route("/api/auth/register", auth.register, methods=["POST"]),
    Route("/api/auth/login", auth.login, methods=["POST"]),
    Route("/api/auth/logout", auth.logout, methods=["POST"]),
    Route("/api/auth/me", auth.me, methods=["GET"]),
    Route("/api/auth/display-name", auth.update_display_name, methods=["PATCH"]),
    # events & challenges
    Route("/api/events", events.list_events, methods=["GET"]),
    Route("/api/events/{event_id}/enroll", events.enroll, methods=["POST"]),
    Route("/api/events/{event_id}", events.event_detail, methods=["GET"]),
    Route("/api/events/{event_id}/leaderboard", events.leaderboard, methods=["GET"]),
    Route("/api/events/{event_id}/feed", events.solve_feed, methods=["GET"]),
    Route("/api/events/{event_id}/activity", events.activity, methods=["GET"]),
    Route("/api/practice/challenges", events.practice_challenges, methods=["GET"]),
    # gameplay
    Route("/api/game/sessions", game.start_session, methods=["POST"]),
    Route("/api/game/sessions/{gsid}", game.get_session, methods=["GET"]),
    Route("/api/game/sessions/{gsid}/message", game.post_message, methods=["POST"]),
    Route("/api/game/sessions/{gsid}/reset", game.reset_level, methods=["POST"]),
    Route("/api/game/sessions/{gsid}/new-chat", game.new_chat, methods=["POST"]),
    Route("/api/game/sessions/{gsid}/hints", game.unlock_hint, methods=["POST"]),
    Route("/api/game/sessions/{gsid}/submit", game.submit_flag, methods=["POST"]),
    Route("/api/me/progress", game.my_progress, methods=["GET"]),
    # admin
    Route("/api/admin/events", admin.create_event, methods=["POST"]),
    Route("/api/admin/events/{event_id}", admin.patch_event, methods=["PATCH"]),
    Route("/api/admin/events/{event_id}/challenges", admin.set_event_challenge,
          methods=["POST"]),
    Route("/api/admin/events/{event_id}/invites", admin.create_invites,
          methods=["POST"]),
    Route("/api/admin/events/{event_id}/enrollments", admin.list_enrollments,
          methods=["GET"]),
    Route("/api/admin/events/{event_id}/enrollments/{user_id}",
          admin.remove_enrollment, methods=["DELETE"]),
    Route("/api/admin/events/{event_id}/freeze", admin.freeze_leaderboard,
          methods=["POST"]),
    Route("/api/admin/events/{event_id}/stats", admin.event_stats, methods=["GET"]),
    Route("/api/admin/events/{event_id}/export", admin.export_results,
          methods=["GET"]),
    Route("/api/admin/challenges", admin.list_challenges, methods=["GET"]),
    Route("/api/admin/challenges/publish", admin.publish_version, methods=["POST"]),
    Route("/api/admin/audit", admin.audit_trail, methods=["GET"]),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    ),
]

exception_handlers = {
    ApiError: handle_api_error,
    Exception: handle_unexpected,
}

app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan,
                exception_handlers=exception_handlers)
