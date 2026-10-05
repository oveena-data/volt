"""SQLite persistence: player high scores + the Level 8 memory store.

Kept tiny and dependency-free. Sessions (live conversation state, token/attempt
meters) live in memory in the engine; only durable things land here:
  * best score per (player, level)
  * poisoned memory rows for Level 8 (persists across sessions by design)
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from ..config import settings

_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


@contextmanager
def db():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    global _initialized
    with db():
        pass  # ensure connectable
    _create_schema()
    _initialized = True


def _ensure() -> None:
    """Lazily create the schema the first time any query runs, so the app is
    robust whether or not the ASGI lifespan startup fired."""
    global _initialized
    if not _initialized:
        _create_schema()
        _initialized = True


def _create_schema() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scores (
                player    TEXT NOT NULL,
                level_id  TEXT NOT NULL,
                best      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (player, level_id)
            );

            CREATE TABLE IF NOT EXISTS memory_store (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                player    TEXT NOT NULL,
                level_id  TEXT NOT NULL,
                kind      TEXT NOT NULL,     -- e.g. 'user_preference'
                content   TEXT NOT NULL,
                created   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )


# ---- scores ----

def record_score(player: str, level_id: str, score: int) -> int:
    """Store max(existing, score). Returns the stored best."""
    _ensure()
    with db() as conn:
        row = conn.execute(
            "SELECT best FROM scores WHERE player=? AND level_id=?",
            (player, level_id),
        ).fetchone()
        best = max(score, row["best"] if row else 0)
        conn.execute(
            "INSERT INTO scores(player, level_id, best) VALUES(?,?,?) "
            "ON CONFLICT(player, level_id) DO UPDATE SET best=excluded.best",
            (player, level_id, best),
        )
        return best


def player_scores(player: str) -> dict[str, int]:
    _ensure()
    with db() as conn:
        rows = conn.execute(
            "SELECT level_id, best FROM scores WHERE player=?", (player,)
        ).fetchall()
    return {r["level_id"]: r["best"] for r in rows}


def leaderboard(limit: int = 20) -> list[dict]:
    _ensure()
    with db() as conn:
        rows = conn.execute(
            "SELECT player, SUM(best) AS total, COUNT(*) AS cleared "
            "FROM scores GROUP BY player ORDER BY total DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"player": r["player"], "total": r["total"], "cleared": r["cleared"]}
        for r in rows
    ]


# ---- Level 8 memory store (used later; defined now so schema is stable) ----

def memory_add(player: str, level_id: str, kind: str, content: str) -> None:
    _ensure()
    with db() as conn:
        conn.execute(
            "INSERT INTO memory_store(player, level_id, kind, content) VALUES(?,?,?,?)",
            (player, level_id, kind, content),
        )


def memory_get(player: str, level_id: str) -> list[dict]:
    _ensure()
    with db() as conn:
        rows = conn.execute(
            "SELECT kind, content FROM memory_store WHERE player=? AND level_id=? "
            "ORDER BY id",
            (player, level_id),
        ).fetchall()
    return [{"kind": r["kind"], "content": r["content"]} for r in rows]


def memory_clear(player: str, level_id: str) -> None:
    _ensure()
    with db() as conn:
        conn.execute(
            "DELETE FROM memory_store WHERE player=? AND level_id=?",
            (player, level_id),
        )
