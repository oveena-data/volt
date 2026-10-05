# Operations runbook

## Health

- `GET /api/healthz` — liveness (process up).
- `GET /api/readyz` — readiness: checks Postgres and the inference endpoint.
  Returns 503 (and should be removed from the LB) when either is down.
  **There is no mock fallback in production** — a dead model endpoint means
  the game is down and says so.

## Structured logs

JSON lines on stdout. Provider errors log status + truncated body; player
flags and full prompts are never logged.

## Backups & restore

Everything durable is in Postgres.

```bash
# backup (run on a schedule; retain per your event policy)
pg_dump --format=custom --no-owner "$VOLT_DATABASE_URL" > volt-$(date +%F).dump

# restore to a fresh database
createdb volt_restore
pg_restore --no-owner -d volt_restore volt-2026-10-05.dump
# point VOLT_DATABASE_URL at the restored DB and restart the backend
```

Restore drill before any ranked event: restore the latest dump to a scratch
database and run `SELECT count(*) FROM solves;` sanity checks.

## Rollback

- **Backend**: redeploy the previous container tag. Migrations are
  forward-only; a code rollback across a migration boundary requires
  restoring the matching DB backup (above). Tag images with the git SHA.
- **Frontend**: `npx vercel rollback`.
- **Challenge content**: never edit a published version. Publish a new
  version (admin API) and repin after the event; ranked events keep the
  version they pinned at setup.

## During an event

| Action | How |
|---|---|
| Pause/resume gameplay | Admin UI → event → Pause (blocks ranked turns with 423; practice unaffected) |
| Freeze leaderboard | Admin UI → Freeze board (gameplay continues; standings cut off at freeze time) |
| Hide leaderboard | Admin UI → Hide board |
| Disable one level | Admin UI → Manage → untick Enabled |
| Add players to invite-only event | Admin UI → Generate invite codes |
| Watch load | Admin UI → Manage → per-level stats (p50/p95 latency, errors, tokens, queue depth) |
| Export results | Admin UI → Export CSV (audited) |

## Limits & scaling

- Per-user turn rate: `VOLT_RATE_LIMIT_TURNS_PER_MINUTE` (token bucket,
  in-memory **per backend instance** — with N instances the effective
  per-user ceiling is N×rate; the shared inference semaphore remains the
  hard backstop on model load).
- Shared inference: `VOLT_INFERENCE_CONCURRENCY` simultaneous model calls,
  `VOLT_INFERENCE_QUEUE_MAX` waiting; beyond that players get a clean 429
  with a retry hint. Size concurrency from docs/LOADTEST.md.
- Input bound: `VOLT_MAX_MESSAGE_CHARS` (default 12000 — large enough for
  Level 5's intended oversized payloads); `VOLT_MAX_CONVERSATION_TURNS`
  per conversation.
- A turn left `pending` by a crashed instance is reaped on the next request
  after `VOLT_REQUEST_TIMEOUT + 30s` and retried safely by the client.

## Data handling & retention

- Transcripts are readable only by their owner (and by admins via the
  database for incident review — API access is owner-only). Public payloads
  (leaderboard, feed, activity) never contain transcripts or flags.
- Flags are synthetic per-player strings with no external value. Submitted
  flag guesses are stored for audit.
- Suggested retention: export results after the event, then
  `TRUNCATE messages, turns, conversations` (keeps solves/scores/audit) or
  drop the event entirely with `DELETE FROM events WHERE id=...` (cascades).
- `audit_log` records every admin mutation (actor, action, details, time).

## Secrets

`VOLT_FLAG_SECRET`, DB credentials and any provider API key come from the
environment/secret store only. `.env` files are gitignored; `.env.example`
carries no real values. Rotating `VOLT_FLAG_SECRET` does not invalidate
existing flags (flags are stored, not re-derived).

## Incident quick refs

- **Model endpoint down** → readiness goes 503; players see "backend
  unavailable, not counted as an attempt". Fix inference; nothing to clean
  up — errored turns never charge attempts.
- **Runaway player** → rate limit already bounds them; for abuse, remove
  their enrolment (admin UI) or revoke sessions:
  `UPDATE auth_sessions SET revoked_at=now() WHERE user_id='...'`.
- **Wrong points configured mid-event** → fix in admin UI; already-recorded
  solves keep the points captured at solve time (document any manual
  adjustment in the audit log via a deliberate re-set).
