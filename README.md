# ⚡ VOLT — Prompt-Injection CTF

VOLT is a capture-the-flag game for learning **LLM prompt injection**, themed
as a fictional electricity-grid operator. Each level is a small chat app
guarding a secret flag behind a different class of defence; players break in
by exploiting that level's specific weakness against a **real open-weights
model**.

**This release ships the production platform + Levels 1–5.** Levels 6–10 are
specced (see `FACILITATOR.md`) and the architecture carries them: versioned
challenge configs in Postgres, pluggable input/output filters, a provider
layer ready for multi-model pipelines.

| # | Level | Technique |
|---|-------|-----------|
| 1 | Substation Slang Spike | Authority impersonation |
| 2 | Customer is Always Right | Persona / role-play manipulation |
| 3 | Piece by Piece | Payload splitting across turns |
| 4 | Grammar Goblin | Unicode / character-obfuscation bypass |
| 5 | Electrifyingly Educated | Long-context instruction burial |

## Architecture

```
frontend/   React + Vite + TS SPA (bundled deps, light theme) — hosted on Vercel
backend/    Python Starlette API — auth, events, game engine, admin
            Postgres 16 (migrations in backend/migrations/)
            any OpenAI-compatible inference endpoint (reference: Ollama + Qwen3 8B)
```

Key properties:

- **Server-authoritative everything.** Identity from validated bearer
  sessions (argon2id + hashed opaque tokens); per-player flags generated
  server-side and scoped to (player, event|practice, challenge); scoring via
  transactional one-solve-per-player constraints. Browser storage holds UI
  convenience only.
- **Real model, honest game.** Wins are detected only by the player's own
  flag appearing in model output (including hex/base64/decimal/reversed/
  spaced transforms) or by explicit submission. Deterministic filters exist
  only as genuine challenge components. Production refuses to boot with mock
  inference; provider failures never count as player attempts.
- **Durable and concurrent.** All game state in Postgres; turns serialised
  per conversation; duplicate sends idempotent via client message ids; no DB
  transaction held during inference.

## Quick start (local, Docker)

```bash
cp backend/.env.example .env   # set VOLT_DB_PASSWORD, VOLT_FLAG_SECRET, etc.
docker compose up --build      # db + ollama (pulls qwen3:8b) + backend on :8000
cd frontend && npm install && npm run dev   # SPA on :5173, /api proxied
```

## Quick start (local, no Docker)

```bash
# Postgres 16 with a 'volt' database, then:
cd backend
pip install -r requirements.lock.txt
cp .env.example .env                       # point VOLT_BASE_URL at your model
python -m uvicorn app.main:app --port 8099
cd ../frontend && npm install && npm run dev
```

## Tests

```bash
cd backend
createdb volt_test   # once
python -m pytest tests/        # 42 tests, real Postgres, scripted mock model
```

Real-model calibration (run against your inference endpoint):

```bash
python -m evals.run_eval --base-url http://127.0.0.1:11434/v1 --model qwen3:8b \
    --trials-intended 20 --trials-direct 20
```

## Documentation

- `docs/DEPLOYMENT.md` — Vercel frontend, backend container, Ollama, HTTPS
- `docs/OPERATIONS.md` — runbook: health, backups, restore, rollback, limits
- `docs/API.md` — endpoint reference
- `docs/CHALLENGES.md` — level design, filters, scoring, reset semantics
- `docs/EVALUATION.md` — model selection, calibration method and status
- `docs/LOADTEST.md` — measured capacity results
- `FACILITATOR.md` — **spoilers**; keep away from players

## Safety / intended use

VOLT teaches defenders how prompt injection works. Scenarios are entirely
fictional: the model controls no real systems, has no tools, and the
"secrets" are synthetic per-player strings. Don't reuse the deliberately
vulnerable prompt patterns in real products — that's the lesson.
