# Release status — VOLT v1 (Levels 1–5)

Status taxonomy: **Implemented** (code exists) → **Tested** (execution
verified) → **Model-validated** (verified against the selected real model)
→ **Deployment-ready** (assets + available checks done) → **Deployed** →
**Deployment-verified**.

| Component | Status |
|---|---|
| Backend (auth, events, engine, scoring, admin, reliability) | Tested — 43 pytest cases against real Postgres |
| Challenge content L1–L5 (versioned configs, filters, hints) | Tested (engine + filter units + stub-model flows); **model-validated: NO — pending** |
| Evaluation harness | Tested end-to-end against a stub endpoint; real-model runs pending (see docs/EVALUATION.md) |
| Frontend (player + admin) | Tested — production build + 21 Playwright browser checks against the live backend |
| Backend container image | Tested — built & run, in-container migration/readiness/registration + production fail-fast verified |
| docker-compose stack | Deployment-ready (compose not executable in the build env: model downloads blocked) |
| Vercel frontend | Deployment-ready (`vercel.json`, build verified; no Vercel credentials were available to deploy) |
| Load capacity | Tested — 25 concurrent active players, 0 errors (docs/LOADTEST.md; inference latency simulated at 1.2 s) |
| Production deployment | **Not executed** — no hosting credentials/accounts available in the build environment |

## What remains before a ranked event

1. Serve Qwen3-8B (or fallback) and run the calibration commands in
   docs/EVALUATION.md; iterate challenge versions until all five levels
   meet targets. **Do not run a ranked event before this.**
2. Deploy backend + Postgres (docs/DEPLOYMENT.md §2), frontend to Vercel
   (§1 — Pro plan for an organisation event), set CORS + admin emails.
3. On the deployed app, run the post-deploy verification pass:
   register/login/logout, enrol (invite + open), play and solve each level,
   flag submission, hint deduction, leaderboard + freeze, pause, refresh/
   restart persistence, cross-user 404s, /api/readyz behaviour with the
   model stopped.
