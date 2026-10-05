# ⚡ VOLT — Prompt-Injection CTF

VOLT is a capture-the-flag game for learning **LLM prompt injection**. The
assistant is always named **VOLT**; each level is a different challenge against
VOLT, which guards a synthetic secret flag behind a different class of defence.
Players break in by exploiting that level's specific weakness against a **real
open-weights model**.

Levels unlock in sequence: Level 1 is open, and clearing a level unlocks the
next. Progression is enforced on the backend from the authoritative solve
record.

**This release ships the production platform + Levels 1–5.** Levels 6–10 are
specced (see `FACILITATOR.md`) and the architecture carries them: versioned
challenge configs in Postgres, pluggable input/output filters, a provider
layer ready for multi-model pipelines.

| # | Level | Subtitle |
|---|-------|----------|
| 1 | Substation Slang Spike | Think you speak trade, do ya? |
| 2 | Customer is Always Right | Master has given VOLT a prompt!... |
| 3 | Piece by Piece | Why rush? Rome wasn't built in a day. |
| 4 | Grammar Goblin | He's green, he's mean, and he just edited your system prompt! |
| 5 | Electrifyingly Educated | Who has more aura - Edison or Stobie? |

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

## Applying an update to an existing install

Editing `definitions.py` does not change a database that already seeded the
old versions: the DB is authoritative. After pulling new code, publish the new
challenge versions and repin the development event with the maintenance
command. It preserves users, enrolments, solves and scores, and only ever
touches the named development event (an active competition is never silently
re-pinned).

```bash
cd backend
python -m app.maintenance update            # publish changed versions + repin volt-dev
python -m app.maintenance seed-dev-event     # create/refresh only the dev event
python -m app.maintenance promote-admin you@example.com   # explicit admin grant
```

### Windows / PowerShell (local install at C:\Users\Oveena\Projects\volt)

```powershell
cd C:\Users\Oveena\Projects\volt
git pull

# backend: apply the update, then run
cd backend
python -m pip install -r requirements.lock.txt
$env:VOLT_ENV = "development"
$env:VOLT_DATABASE_URL = "postgresql://volt:volt_dev_password@127.0.0.1:5432/volt"
# point at your model endpoint (Ollama shown):
$env:VOLT_PROVIDER = "openai_compatible"
$env:VOLT_BASE_URL = "http://127.0.0.1:11434/v1"
$env:VOLT_MODEL   = "qwen3:8b"
python -m app.maintenance update
# create your admin and run the API:
python -m app.maintenance promote-admin you@example.com
python -m uvicorn app.main:app --port 8099

# frontend (new terminal)
cd C:\Users\Oveena\Projects\volt\frontend
npm install
npm run dev     # http://127.0.0.1:5173, /api proxied to :8099
```

Then register a player in the app, join the **VOLT Development Event**, and
Level 1 is ready to play.

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
