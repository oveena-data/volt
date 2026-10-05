"""Configuration via environment variables + optional .env file.

No pydantic-settings dependency -- stdlib only, so the backend runs with just
starlette + uvicorn (+ httpx when using a real model backend).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("'\"")
            os.environ.setdefault(key, val)


_load_dotenv()


def _get(name: str, default: str) -> str:
    return os.environ.get(f"VOLT_{name}", default)


@dataclass
class Settings:
    provider: str = _get("PROVIDER", "mock")
    base_url: str = _get("BASE_URL", "http://localhost:11434/v1")
    model: str = _get("MODEL", "llama3.1:8b")
    api_key: str = _get("API_KEY", "")
    temperature: float = float(_get("TEMPERATURE", "0.3"))
    max_tokens: int = int(_get("MAX_TOKENS", "512"))
    request_timeout: float = float(_get("REQUEST_TIMEOUT", "60"))
    db_path: str = _get("DB_PATH", "volt.db")
    cors_origins: str = _get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
