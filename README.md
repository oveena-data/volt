# ⚡ VOLT — Prompt-Injection Grid

A self-hosted capture-the-flag range for learning **LLM prompt-injection**,
themed as an electricity-grid control system. Each level is a small app
guarding a secret flag behind a different class of defence; you break in by
finding the weakness. The range scores you on efficiency (tokens + attempts),
auto-detects successful extractions, and keeps a leaderboard.

**This build ships the game engine + Levels 1–5.** The remaining levels (6–10)
are specced and the framework is built to carry them — see `FACILITATOR.md`.

```
┌─────────────────────────────────────────────────────────┐
│  L1  Substation Slang Spike     authority impersonation   │
│  L2  Customer is Always Right   persona / role-play       │
│  L3  Piece by Piece             payload splitting          │
│  L4  Grammar Goblin             Unicode / encoding bypass  │
│  L5  Electrifyingly Educated    long-context dilution      │
└─────────────────────────────────────────────────────────┘
```

## What makes it a real teaching tool

- **Scoring that rewards good tradecraft.** `Score = max(100, 1000 −
  tokens·0.5 − attempts·10)`. Verbose or brute-force attempts score less; you
  can replay a cleared level to trim tokens and climb the board (best score is
  kept).
- **Reset vs New chat are distinct.** *Reset* wipes the conversation, the
  meters, and any persistent state. *New chat* clears the conversation but keeps
  persistent memory — the difference becomes a mechanic in later levels.
- **Transform-aware flag detection.** A leak counts even if it's smuggled out as
  hex/decimal bytes, base64, or char-separated text — so later levels can be won
  by *encoding* the secret past an output filter.
- **Runs against a real open model or fully offline.** Point it at any
  OpenAI-compatible endpoint (local Ollama, Groq free tier, OpenRouter free
  models, vLLM, llama.cpp). With no key at all it runs a deterministic **mock**
  model so the whole range is instantly playable and gradable.

---

## Quick start (zero API key, offline-friendly)

Requires Python 3.10+.

```bash
cd backend
python -m pip install -r requirements.txt      # starlette + uvicorn (+ httpx)
python -m uvicorn app.main:app --port 8099
# open http://127.0.0.1:8099
```

That's it — the default `VOLT_PROVIDER=mock` needs no key and no network. The
backend also serves the frontend at `/`, so there's nothing separate to build.

> The frontend is a single-page React app loaded from a CDN (no npm build step).
> If you're on a locked-down network see **Offline frontend** below.

## Play against a real open model

Copy `.env.example` to `.env` and set the provider. Examples:

**Local Ollama (free, open weights):**
```ini
VOLT_PROVIDER=openai_compatible
VOLT_BASE_URL=http://localhost:11434/v1
VOLT_MODEL=llama3.1:8b
VOLT_API_KEY=
```
```bash
ollama serve &           # then: ollama pull llama3.1:8b
```

**Groq (free tier, open models like Llama 3):**
```ini
VOLT_PROVIDER=openai_compatible
VOLT_BASE_URL=https://api.groq.com/openai/v1
VOLT_MODEL=llama-3.1-8b-instant
VOLT_API_KEY=gsk_...
```

**OpenRouter (has free open models):**
```ini
VOLT_PROVIDER=openai_compatible
VOLT_BASE_URL=https://openrouter.ai/api/v1
VOLT_MODEL=meta-llama/llama-3.1-8b-instruct:free
VOLT_API_KEY=sk-or-...
```

Any endpoint that speaks the OpenAI `/chat/completions` schema works. When
`usage` is returned it's used for exact token scoring; otherwise tokens are
estimated.

---

## How it's built

```
backend/
  app/
    config.py            env/.env settings (stdlib only)
    main.py              Starlette ASGI app + routes, serves the SPA
    core/
      llm.py             provider adapter: openai_compatible | mock, token accounting
      level_base.py      Level framework: system prompt, input/output filters, mock policy
      engine.py          sessions, metering, score formula, reset/new-chat
      flags.py           transform-aware flag detection
      store.py           SQLite: high scores + (for L8) persistent memory
    levels/
      level1.py … level5.py  registry.py
  static/
    index.html app.jsx   single-page React frontend (CDN, no build)
  tests/                 facilitator tests (contain solutions — not player-facing)
    test_levels.py         L1–3
    test_levels_4_5.py     L4–5
FACILITATOR.md           facilitator guide + per-level solutions (keep private)
```

We build on **Starlette** (FastAPI's own foundation) rather than FastAPI so the
range self-hosts with the smallest possible dependency set. If you prefer
FastAPI or a Vite/React toolchain, both drop in cleanly — the engine and levels
are framework-agnostic.

### Adding a level
Subclass `Level` in `app/levels/`, give it `meta`, `flag`, `system_prompt`,
optional `input_filter`/`output_filter`, and a `mock_policy` so it's playable
offline; register it in `registry.py`. The engine handles the rest (metering,
scoring, resets, leak detection).

## Run the tests

```bash
cd backend
VOLT_PROVIDER=mock python -m unittest discover -s tests -v
```

Covers the score formula, high-score-as-max, encoded flag detection, and each
level's intended exploit succeeding while the *wrong* technique fails.

## Offline frontend
The page pulls React + Babel from `unpkg.com`. To run with no internet, download
these three files into `backend/static/vendor/` and repoint the `<script>` tags
in `index.html`:
`react.production.min.js`, `react-dom.production.min.js`, `@babel/standalone/babel.min.js`.

## Safety / intended use
VOLT exists to teach defenders how prompt injection works against LLM apps. The
flags are fake and the "secrets" are toys. Don't point it at production models or
reuse the vulnerable patterns in real systems — that's the whole lesson.
