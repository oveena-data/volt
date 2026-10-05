"""Structured API errors. Internals are logged, never leaked to clients."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("volt.errors")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, *, code: str = "bad_request",
                 extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.extra = extra or {}


def api_error_response(exc: ApiError) -> JSONResponse:
    body = {"error": {"code": exc.code, "message": exc.message, **exc.extra}}
    return JSONResponse(body, status_code=exc.status)


async def handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return api_error_response(exc)


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path,
                  exc_info=exc)
    return JSONResponse(
        {"error": {"code": "internal", "message": "internal server error"}},
        status_code=500,
    )
