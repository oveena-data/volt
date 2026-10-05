"""Liveness and readiness probes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import db, providers
from ..config import settings


async def healthz(request: Request) -> JSONResponse:
    """Liveness: the process is up."""
    return JSONResponse({"ok": True})


async def readyz(request: Request) -> JSONResponse:
    """Readiness: database reachable and inference endpoint responsive.
    Production is NOT ready without real inference — no mock fallback."""
    checks: dict = {}
    ok = True
    try:
        await db.pool().fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        ok = False
    probe = await providers.probe()
    checks["inference"] = probe
    if not probe.get("ok"):
        ok = False
    checks["env"] = settings.env
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)
