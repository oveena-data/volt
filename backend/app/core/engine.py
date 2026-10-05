"""Game engine: per-level conversation state, metering, scoring.

Score formula (from the spec):
    Level Score = max(Min, Base - TokensSpent*TokenWeight - Attempts*AttemptPenalty)

Semantics:
  * Tokens Spent  -- cumulative prompt+completion tokens this level session.
  * Attempts      -- failed flag-extraction turns this level session.
  * Reset Level   -- clears tokens, attempts, and conversation back to the
                     original system prompt (destroys accumulated state).
  * New Chat      -- clears the conversation but, for stateful levels (L8),
                     retains persistent memory. (Distinct from Reset.)
  * High score    -- max(previous best, new) persisted per (player, level).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from . import store
from .flags import contains_flag
from .level_base import Level
from .llm import Message, chat


@dataclass
class LevelSession:
    level_id: str
    history: list[Message] = field(default_factory=list)
    tokens_spent: int = 0
    attempts: int = 0
    solved: bool = False
    last_score: int = 0


@dataclass
class PlayerSession:
    player: str
    sid: str = field(default_factory=lambda: uuid.uuid4().hex)
    levels: dict[str, LevelSession] = field(default_factory=dict)

    def level(self, level_id: str) -> LevelSession:
        if level_id not in self.levels:
            self.levels[level_id] = LevelSession(level_id=level_id)
        return self.levels[level_id]


def compute_score(level: Level, tokens_spent: int, attempts: int) -> int:
    c = level.meta.score
    raw = c.base_points - (tokens_spent * c.token_weight) - (attempts * c.attempt_penalty)
    return int(max(c.min_score, round(raw)))


@dataclass
class TurnResult:
    reply: str
    leaked: bool
    tokens_spent: int
    attempts: int
    solved: bool
    score: int | None  # populated on the turn that first solves (and replays)
    best: int | None
    blocked: bool  # True if an input filter short-circuited the model call
    meta: dict = field(default_factory=dict)  # level-specific extras (e.g. L3 fragments)


class Engine:
    def __init__(self, levels: dict[str, Level]):
        self.levels = levels
        self._sessions: dict[str, PlayerSession] = {}

    # -- session management --
    def get_session(self, sid: str | None, player: str) -> PlayerSession:
        if sid and sid in self._sessions:
            return self._sessions[sid]
        ps = PlayerSession(player=player)
        self._sessions[ps.sid] = ps
        return ps

    def reset_level(self, ps: PlayerSession, level_id: str) -> None:
        """Full reset: wipe conversation, meters, and any persistent state."""
        ps.levels[level_id] = LevelSession(level_id=level_id)
        store.memory_clear(ps.player, level_id)  # harmless for stateless levels

    def new_chat(self, ps: PlayerSession, level_id: str) -> None:
        """Clear conversation + meters but keep persistent memory (L8)."""
        ps.levels[level_id] = LevelSession(level_id=level_id)

    # -- the core turn --
    async def play_turn(self, ps: PlayerSession, level_id: str, user_text: str) -> TurnResult:
        level = self.levels[level_id]
        ls = ps.level(level_id)

        # 1. input filter (deny-list / pre-model guard)
        block = level.input_filter(user_text, ls.history)
        if block is not None:
            # A blocked attempt still counts as a (cheap) attempt + tokens.
            ls.tokens_spent += _cheap_tokens(user_text)
            ls.attempts += 1
            return TurnResult(
                reply=block, leaked=False, tokens_spent=ls.tokens_spent,
                attempts=ls.attempts, solved=ls.solved, score=None, best=None,
                blocked=True,
            )

        # 2. call the model
        messages = level.build_messages(ls.history, user_text)
        result = await chat(messages, level_id=level_id)
        raw_reply = result.text

        # 3. output filter (compliance/redaction)
        reply = level.output_filter(raw_reply)

        # 4. account tokens + persist conversation turn
        ls.tokens_spent += result.total_tokens
        ls.history.append({"role": "user", "content": user_text})
        ls.history.append({"role": "assistant", "content": raw_reply})

        # 5. leak check -- against the *player-visible* (post-filter) text,
        #    because a leak the filter removed is not a leak the player got.
        leaked = contains_flag(reply, level.flag)

        score = None
        best = None
        if leaked:
            newly = not ls.solved
            ls.solved = True
            score = compute_score(level, ls.tokens_spent, ls.attempts)
            ls.last_score = score
            best = store.record_score(ps.player, level_id, score)
            _ = newly
        else:
            ls.attempts += 1

        return TurnResult(
            reply=reply, leaked=leaked, tokens_spent=ls.tokens_spent,
            attempts=ls.attempts, solved=ls.solved, score=score, best=best,
            blocked=False, meta=_level_extras(level, ls),
        )


def _cheap_tokens(text: str) -> int:
    from .llm import estimate_tokens
    return estimate_tokens(text)


def _level_extras(level: Level, ls: LevelSession) -> dict:
    hook = getattr(level, "session_extras", None)
    if callable(hook):
        try:
            return hook(ls)
        except Exception:
            return {}
    return {}
