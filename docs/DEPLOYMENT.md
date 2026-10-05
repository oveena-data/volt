# Deployment

Three pieces, deployed separately:

1. **Frontend** — static Vite build on **Vercel**.
2. **Backend + Postgres** — one container + managed/self-hosted Postgres,
   anywhere that runs containers (Fly.io, Railway, a VM with Docker
   Compose, ...). Not on Vercel.
3. **Inference** — an OpenAI-compatible endpoint. Reference: Ollama serving
   `qwen3:8b` on organisation hardware (GPU strongly recommended;
   CPU-only works for small groups but at multi-second latency).

## 1. Frontend on Vercel

> **Plan check (verified Oct 2026):** Vercel's Hobby tier is limited to
> personal, non-commercial use. An organisation-run event should use a
> **Pro** team ($20/seat/mo) — do not assume the free tier qualifies, and
> get approval before enabling a paid plan.

```bash
cd frontend
npm install
npx vercel login                 # or VERCEL_TOKEN=... for CI
npx vercel link                  # create/link the project once
npx vercel env add VITE_API_BASE production   # https://api.your-domain.example
npx vercel deploy --prod
```

`vercel.json` is committed (Vite framework, SPA rewrite, security headers).
After the first deploy, set the backend's `VOLT_CORS_ORIGINS` to the exact
Vercel URL (plus any custom domain) and restart the backend.

Rollback: `npx vercel rollback` (or promote a previous deployment in the
dashboard).

## 2. Backend + Postgres

Container: `backend/Dockerfile` (uvicorn, 2 workers, non-root, healthcheck).
Migrations + challenge seeding run automatically at startup; they are
advisory-locked, so multiple instances can start concurrently.

Required environment (see `backend/.env.example` for the full list):

```
VOLT_ENV=production
VOLT_DATABASE_URL=postgresql://volt:***@db-host:5432/volt
VOLT_PROVIDER=openai_compatible
VOLT_BASE_URL=http://inference-host:11434/v1
VOLT_MODEL=qwen3:8b
VOLT_FLAG_SECRET=<openssl rand -hex 32>
VOLT_CORS_ORIGINS=https://volt-yourevent.vercel.app
VOLT_ADMIN_EMAILS=organiser@example.com
```

Production startup **fails fast** if the provider is mock, the flag secret
is weak, the dev DB password is still set, or CORS is a wildcard. Readiness
(`/api/readyz`) fails while the database or the inference endpoint is
unreachable — there is no mock fallback.

HTTPS: terminate TLS in front of the backend (Caddy, nginx, a platform's
built-in LB). The API is pure JSON over bearer tokens; no cookies, so no
CSRF surface, but keep `VOLT_CORS_ORIGINS` exact.

## 3. Inference (reference: Ollama + Qwen3 8B)

Qwen3-8B: Apache-2.0, ungated open weights; serves via Ollama, vLLM,
llama.cpp. Pinning for competitions:

- **Runtime**: pin the Ollama image (`ollama/ollama:0.12.3` in
  docker-compose.yml).
- **Model artifact**: after `ollama pull qwen3:8b`, record the digest
  (`ollama show qwen3:8b --modelfile` / `ollama list --digests`) in the
  event notes; re-pull by digest on any new host. Default tag is the
  Q4_K_M quantisation.
- **Non-thinking mode**: Qwen3 defaults to thinking mode. For VOLT use
  non-thinking. With vLLM/SGLang set
  `VOLT_EXTRA_BODY={"chat_template_kwargs":{"enable_thinking":false}}`.
  With Ollama, create a pinned variant once:

  ```
  # Modelfile
  FROM qwen3:8b
  PARAMETER temperature 0.2
  SYSTEM ""
  # non-thinking: append /no_think via template or use a 'instruct' build
  ```
  ```bash
  ollama create volt-qwen3-8b -f Modelfile
  # then VOLT_MODEL=volt-qwen3-8b
  ```
  The backend also strips any `<think>…</think>` block defensively, so a
  thinking reply can never leak reasoning into the game transcript.
- **Generation settings**: temperature/max_tokens are pinned per challenge
  version in the database; the env values are fallbacks. During a ranked
  event, do not change the model, quantisation, or any pinned challenge
  version (publish a new version after the event instead).
- **Hosted alternative**: any OpenAI-compatible host of Qwen3-8B works
  (set `VOLT_BASE_URL`/`VOLT_API_KEY`). Note that most hosted providers do
  not guarantee quantisation/version pinning — record the provider's model
  id and date in the event notes, and prefer self-hosting for ranked events.

Sizing: one concurrent inference at a time per ~`VOLT_INFERENCE_CONCURRENCY`;
see docs/LOADTEST.md for the measured relationship between concurrency,
model latency and player experience.

### Container verification status

The shipped `backend/Dockerfile` was built and run in the release
environment (sole build delta there: trusting that environment's
TLS-intercepting proxy CA, which does not apply elsewhere). Verified in
the container: dependency install from the lockfile, startup migration of
a fresh database, challenge seeding, `/api/readyz` green against a live
endpoint, registration round-trip, and the production fail-fast (mock
provider / weak secret / dev DB password each abort startup with a FATAL
log). `docker compose` end-to-end (with the real Ollama service) could not
be executed there because model downloads were blocked; compose config is
otherwise the same image + stock `postgres:16` / `ollama` images.

## Local all-in-one

`docker compose up --build` starts Postgres + Ollama (auto-pulls the model)
+ backend on :8000. Use the Vite dev server (`npm run dev`) or any static
host for the frontend.
