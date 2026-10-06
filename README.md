# VOLT - Prompt-Injection CTF

VOLT is a capture-the-flag game for learning **LLM prompt injection**. The
assistant is always named **VOLT**; each level is a different challenge against
VOLT, which guards a synthetic secret flag behind a different class of defence.
Players break in by exploiting that level's specific weakness against a **real
open-weights model**.

Levels unlock in sequence: Level 1 is open, and clearing a level unlocks the
next. Progression is enforced on the backend from the authoritative solve
record.

**This release ships the production platform + Levels 1-10.** Levels 1-5 are
single-model prompt-injection challenges; Levels 6-10 add multi-model and
multi-agent pipelines (validator/target, execution/compliance, persistent
memory, MCP tool loop, four-agent chain). Versioned challenge configs
live in Postgres; filters and pipelines are selected per challenge version.

| # | Level | Subtitle |
|---|-------|----------|
| 1 | Substation Slang Spike | Think you speak trade, do ya? |
| 2 | Customer is Always Right | Master has given VOLT a prompt!... |
| 3 | Piece by Piece | Why rush? Rome wasn't built in a day. |
| 4 | Grammar Goblin | He's green, he's mean, and he just edited your system prompt! |
| 5 | Electrifyingly Educated | Who has more aura - Edison or Stobie? |
| 6 | Professional Frenemy | One says yes, the other says too much. |
| 7 | Above my paygrade, unfortunately | Not even HR can get me talking. |
| 8 | Remember Me | A wolf may lose his teeth, but not his nature. |
| 9 | Oh my Human | The reviewer approves what the screen shows, not what is sent. |
| 10 | Ghost in the Grid | You shall not pass...into the system! |

Levels 1-5 are single-model. Levels 6-10 are multi-model pipelines
(`backend/app/pipeline.py`): a validator plus target (6), an execution model
behind a compliance firewall (7), persistent cross-session memory (8), an
agent loop over poisonable MCP tool servers (9), and a four-agent
threat-intel platform with provenance laundering (10). Each is several genuinely
separate inference calls with their own prompts, contexts and permissions; the
flag is interpolated only into the one component meant to hold it, which on
Level 9 is a file no prompt contains and only a tool call can reach.
Extracting the flag into the chat is necessary but not sufficient: a level is
solved only when the player **submits** that flag.

Level 9 also adds two platform features other levels can use: **.txt
attachments** (mounted on the level's simulated filesystem rather than pasted
into a prompt, so their content reaches a model only as a tool result) and a
**player-installed MCP server** whose tool manifest the player authors.

## Scoring

Score per solve = base points + efficiency bonus - hint costs
(`backend/app/scoring.py`). Base points escalate with difficulty; the bonus
pool is half the base and shrinks with every extra attempt and with model
tokens spent, so efficient solves rank higher. Levels 6-9 ship paid, in-order
hints that explain the mechanic: unlocking one deducts its cost at solve time,
so more hints unlocked means fewer points (levels 1-5 have no hints). The
leaderboard shows score, levels solved, attempts and tokens spent; ties go to
the player who spent fewer tokens, then the earlier solve.

## Architecture

```
frontend/   React + Vite + TS SPA (bundled deps, Arial, light/dark themes) — hosted on Vercel
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
- **Real model, honest game.** A solve is recorded only when the player
  submits their own flag; the flag surfacing in a reply (including hex/base64/
  decimal/reversed/spaced transforms the submit box also reconstructs) is an
  operator statistic, never an auto-win, and the turn response carries no
  leak/solve oracle. Deterministic filters exist only as genuine challenge
  components. Production refuses to boot with mock inference; provider
  failures never count as player attempts.
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

## Running a real event

There is no built-in development event: every event is a real event an
organiser creates, either in the Admin UI or on the command line.

```bash
cd backend
python -m app.maintenance update                       # publish the code's challenge versions
python -m app.maintenance create-event spring-cup "Spring Cup" \
    --starts 2026-11-01T09:00:00Z --ends 2026-11-02T18:00:00Z
python -m app.maintenance promote-admin you@example.com   # explicit admin grant
```

## Applying an update to an existing install

Editing `definitions.py` does not change a database that already seeded the
old versions: the DB is authoritative. After pulling new code, publish the new
challenge versions and repin the event you name with the maintenance command.
It preserves users, enrolments, solves and scores, and only ever touches the
named event (nothing is ever re-pinned implicitly).

```bash
cd backend
python -m app.maintenance update --event-slug spring-cup   # publish + repin that event
```

Installs created before this release carried a development event
(`volt-dev`); remove it with:

```bash
python -m app.maintenance delete-event volt-dev --yes
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
# first time: remove the old development event and create a real one
python -m app.maintenance delete-event volt-dev --yes
python -m app.maintenance create-event my-game "My Game" --starts 2026-01-01T00:00:00Z --ends 2027-01-01T00:00:00Z
# create your admin and run the API:
python -m app.maintenance promote-admin you@example.com
python -m uvicorn app.main:app --port 8099

# frontend (new terminal)
cd C:\Users\Oveena\Projects\volt\frontend
npm install
npm run dev     # http://127.0.0.1:5173, /api proxied to :8099
```

Then register a player in the app, join your event, and Level 1 is ready to
play.

## Documentation

- `docs/DEPLOYMENT.md` — Vercel frontend, backend container, Ollama, HTTPS
- `docs/OPERATIONS.md` — runbook: health, backups, restore, rollback, limits
- `docs/API.md` — endpoint reference
- `docs/CHALLENGES.md` — level design, filters, scoring, reset semantics
- `docs/EVALUATION.md` — model selection, calibration method and status
- `docs/LOADTEST.md` — measured capacity results
- `FACILITATOR.md` — **spoilers**; keep away from players

## Security & CI

- Every API response carries defence-in-depth headers (nosniff, frame deny,
  no-referrer, no-store, restrictive CSP, HSTS); CORS is an explicit origin
  allowlist and production refuses to boot with weak secrets or wildcard
  CORS.
- `.github/workflows/ci.yml` runs on every push/PR: the full backend test
  suite against a real Postgres 16, bandit static security analysis,
  pip-audit and npm audit dependency scans, a gitleaks secret scan of the
  history, and the type-checked frontend production build with a no-emoji
  source gate.
- Secrets live only in environment variables (`backend/.env.example`
  documents them); nothing secret is committed.

## Safety / intended use

VOLT teaches defenders how prompt injection works. Scenarios are entirely
fictional: the model controls no real systems, has no tools, and the
"secrets" are synthetic per-player strings. Don't reuse the deliberately
vulnerable prompt patterns in real products — that's the lesson.
