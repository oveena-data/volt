"""VOLT backend configuration.

All settings come from environment variables (optionally via a .env file next
to the backend package). Secrets must never be committed; see .env.example.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _load_dotenv() -> None:
    path = os.environ.get(
        "VOLT_ENV_FILE",
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    )
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


_load_dotenv()


def _get(name: str, default: str) -> str:
    return os.environ.get(f"VOLT_{name}", default)


def _get_int(name: str, default: int) -> int:
    return int(_get(name, str(default)))


def _get_float(name: str, default: float) -> float:
    return float(_get(name, str(default)))


def _get_bool(name: str, default: bool) -> bool:
    return _get(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- environment ---
    env: str = _get("ENV", "development")  # development | test | production

    # --- database ---
    database_url: str = _get(
        "DATABASE_URL",
        "postgresql://volt:volt_dev_password@127.0.0.1:5432/volt",
    )
    db_pool_min: int = _get_int("DB_POOL_MIN", 2)
    db_pool_max: int = _get_int("DB_POOL_MAX", 10)

    # --- auth ---
    session_ttl_hours: int = _get_int("SESSION_TTL_HOURS", 24 * 7)
    # When no SMTP is configured accounts are auto-verified at registration.
    require_email_verification: bool = _get_bool("REQUIRE_EMAIL_VERIFICATION", False)
    # Bootstrap: emails listed here get the admin role at registration.
    admin_emails: str = _get("ADMIN_EMAILS", "")

    # --- inference provider ---
    provider: str = _get("PROVIDER", "openai_compatible")  # openai_compatible | mock
    base_url: str = _get("BASE_URL", "http://127.0.0.1:11434/v1")
    model: str = _get("MODEL", "qwen3:8b")
    api_key: str = _get("API_KEY", "")
    temperature: float = _get_float("TEMPERATURE", 0.2)
    top_p: float = _get_float("TOP_P", 0.9)
    max_tokens: int = _get_int("MAX_TOKENS", 400)
    request_timeout: float = _get_float("REQUEST_TIMEOUT", 90)
    inference_concurrency: int = _get_int("INFERENCE_CONCURRENCY", 2)
    inference_queue_max: int = _get_int("INFERENCE_QUEUE_MAX", 16)

    # --- gameplay limits ---
    max_message_chars: int = _get_int("MAX_MESSAGE_CHARS", 12000)  # L5 needs volume
    max_conversation_turns: int = _get_int("MAX_CONVERSATION_TURNS", 60)
    # L9 attachments: text/plain only, mounted on the challenge filesystem
    max_attachment_chars: int = _get_int("MAX_ATTACHMENT_CHARS", 8000)
    max_attachment_bytes: int = _get_int("MAX_ATTACHMENT_BYTES", 24576)
    max_attachments_per_conversation: int = _get_int(
        "MAX_ATTACHMENTS_PER_CONVERSATION", 5)
    rate_limit_turns_per_minute: int = _get_int("RATE_LIMIT_TURNS_PER_MINUTE", 12)
    rate_limit_burst: int = _get_int("RATE_LIMIT_BURST", 5)
    activity_window_minutes: int = _get_int("ACTIVITY_WINDOW_MINUTES", 5)

    # --- web ---
    cors_origins: str = _get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    # Secret for deriving per-player flags; MUST be set in production.
    flag_secret: str = _get("FLAG_SECRET", "")

    extra: dict = field(default_factory=dict)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    def validate_production(self) -> list[str]:
        """Return a list of fatal misconfigurations for production."""
        problems: list[str] = []
        if self.env != "production":
            return problems
        if self.provider != "openai_compatible":
            problems.append(
                "VOLT_PROVIDER must be 'openai_compatible' in production; "
                "mock inference is never allowed outside tests."
            )
        if not self.flag_secret or len(self.flag_secret) < 32:
            problems.append("VOLT_FLAG_SECRET must be set (>= 32 chars) in production.")
        if "volt_dev_password" in self.database_url:
            problems.append("VOLT_DATABASE_URL still uses the development password.")
        if "*" in self.cors_list:
            problems.append("VOLT_CORS_ORIGINS must list explicit origins, not '*'.")
        return problems


settings = Settings()
