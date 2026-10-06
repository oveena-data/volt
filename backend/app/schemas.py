"""Request schemas (pydantic v2) + a parse helper that yields safe 422s."""

from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator
from starlette.requests import Request

from .config import settings
from .errors import ApiError


async def parse(request: Request, model: type[BaseModel]):
    try:
        data = await request.json()
    except Exception:
        raise ApiError("request body must be JSON", 400, code="invalid_json")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
        raise ApiError(f"invalid request: {details}", 422, code="validation_error")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=40)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("display name cannot be blank")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class DisplayNameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)


class EnrollIn(BaseModel):
    invite_code: str | None = Field(default=None, max_length=120)


class StartSessionIn(BaseModel):
    challenge_id: str = Field(pattern=r"^l\d{1,2}$")
    mode: str = Field(pattern=r"^(practice|ranked)$")
    event_id: str | None = None


class AttachmentIn(BaseModel):
    """A .txt file attached to one message. Text only, and bounded: it is
    mounted on a challenge's filesystem server, not pasted into a prompt."""
    name: str = Field(min_length=5, max_length=60,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._\-]*\.txt$")
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def bound_text(cls, v: str) -> str:
        if len(v) > settings.max_attachment_chars:
            raise ValueError(
                f"attachment exceeds the {settings.max_attachment_chars}-"
                "character limit"
            )
        # A character cap alone is not a size cap: astral-plane characters
        # cost up to 4 bytes each, so bound the encoded size too.
        if len(v.encode("utf-8")) > settings.max_attachment_bytes:
            raise ValueError(
                f"attachment exceeds the {settings.max_attachment_bytes}-byte "
                "limit"
            )
        if not v.strip():
            raise ValueError("attachment is empty")
        return v


class MessageIn(BaseModel):
    client_msg_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w\-]+$")
    text: str = Field(min_length=1)
    attachment: AttachmentIn | None = None

    @field_validator("text")
    @classmethod
    def bound_text(cls, v: str) -> str:
        if len(v) > settings.max_message_chars:
            raise ValueError(
                f"message exceeds the {settings.max_message_chars}-character limit"
            )
        if not v.strip():
            raise ValueError("message is empty")
        return v


class ManifestIn(BaseModel):
    """Install or replace the player's MCP server for a challenge. The
    manifest's SHAPE is bounded here; its content is deliberately not
    filtered, since the tool description is the level's attack surface.
    Pass manifest: null to uninstall."""
    manifest: dict | None = None


class SubmitFlagIn(BaseModel):
    flag: str = Field(min_length=4, max_length=2000)


class HintIn(BaseModel):
    hint_index: int = Field(ge=0, le=10)


# ---- admin ----

class EventIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,60}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    registration_open: bool = True
    invite_only: bool = False
    starts_at: str  # ISO 8601
    ends_at: str


class EventPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    registration_open: bool | None = None
    invite_only: bool | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    paused: bool | None = None
    leaderboard_visible: bool | None = None


class EventChallengeIn(BaseModel):
    challenge_id: str = Field(pattern=r"^l\d{1,2}$")
    points: int = Field(gt=0, le=100000)
    enabled: bool = True
    version: int | None = Field(default=None, ge=1)
    opens_at: str | None = None
    closes_at: str | None = None


class InviteIn(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=10000)
    expires_at: str | None = None
    count: int = Field(default=1, ge=1, le=200)


class FreezeIn(BaseModel):
    frozen: bool


class PublishVersionIn(BaseModel):
    challenge_id: str = Field(pattern=r"^l\d{1,2}$")
    config: dict
