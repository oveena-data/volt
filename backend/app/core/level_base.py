"""Level framework.

A VOLT level is a self-contained prompt-injection challenge. Each level
defines:

* ``meta``          -- player-facing title, technique, lesson, scoring config
* ``flag``          -- the secret the player is trying to extract
* ``system_prompt`` -- the real guardrail text, used when playing against a
                       genuine model (VOLT_PROVIDER=openai_compatible)
* ``input_filter``  -- optional pre-model deny-list (returns a block message or None)
* ``output_filter`` -- optional post-model redaction/compliance check
* ``mock_policy``   -- a deterministic stand-in model so the level is playable
                       and testable offline. The mock encodes the *same*
                       vulnerability the system prompt is meant to tempt a real
                       model into, so solutions transfer.

The engine owns conversation state and scoring; levels own behaviour.
Flag-detection (did this response leak the secret?) is centralised in the
engine via ``contains_flag`` so transformed/encoded leaks still count where a
level intends them to (e.g. Level 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm import Message, MockPolicy, register_mock_policy


@dataclass
class ScoreConfig:
    base_points: int = 1000
    token_weight: float = 0.5
    attempt_penalty: int = 10
    min_score: int = 100


@dataclass
class LevelMeta:
    id: str
    number: int
    title: str
    codename: str
    technique: str
    briefing: str  # player-facing scenario / what the app does
    lesson: str
    hint: str
    score: ScoreConfig = field(default_factory=ScoreConfig)
    starter: str | None = None  # optional sample payload the UI can insert


class Level:
    meta: LevelMeta
    flag: str = "CTF{placeholder}"
    system_prompt: str = ""

    # --- real-model playability hooks (no-ops by default) ---
    def input_filter(self, user_text: str, history: list[Message]) -> str | None:
        """Return a block message to short-circuit, or None to allow."""
        return None

    def output_filter(self, model_text: str) -> str:
        """Transform/redact the model output before the player sees it."""
        return model_text

    def build_messages(self, history: list[Message], user_text: str) -> list[Message]:
        """Assemble the message list sent to the model for this turn."""
        msgs: list[Message] = [{"role": "system", "content": self.system_prompt}]
        msgs.extend(history)
        msgs.append({"role": "user", "content": user_text})
        return msgs

    # --- mock provider hook ---
    mock_policy: MockPolicy | None = None

    def register(self) -> None:
        if self.mock_policy is not None:
            register_mock_policy(self.meta.id, self.mock_policy)
