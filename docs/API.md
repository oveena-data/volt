# VOLT API reference

Base: `/api`. Auth: `Authorization: Bearer <token>` (from register/login).
Errors: `{"error": {"code", "message"}}` with appropriate HTTP status.
All request bodies are JSON, validated server-side.

## Health
| Method | Path | Notes |
|---|---|---|
| GET | /healthz | liveness |
| GET | /readyz | readiness (DB + inference); 503 when not ready |

## Auth
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | /auth/register | email, password (≥10), display_name | 201 → token + user |
| POST | /auth/login | email, password | token + user |
| POST | /auth/logout | — | revokes the presented token |
| GET | /auth/me | — | current user |
| PATCH | /auth/display-name | display_name | |

## Events & challenges (player)
| Method | Path | Notes |
|---|---|---|
| GET | /events | all events + own enrolment state |
| POST | /events/{id}/enroll | body `{invite_code?}`; 403 codes: `registration_closed`, `invite_required`, `invite_invalid`, `event_ended` |
| GET | /events/{id} | event + challenge list (enrolled only) + `server_time` |
| GET | /events/{id}/leaderboard | respects freeze + visibility |
| GET | /events/{id}/feed | recent ranked solves (no transcripts/flags) |
| GET | /events/{id}/activity | active players in the last N minutes |
| GET | /practice/challenges | published challenges for practice |

## Gameplay
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | /game/sessions | challenge_id, mode (`practice`\|`ranked`), event_id? | creates/returns the session + full state (transcript, meters, hints, solve) |
| GET | /game/sessions/{gsid} | — | full state (owner only; others 404) |
| POST | /game/sessions/{gsid}/message | client_msg_id, text | one turn. Statuses: `done`, `blocked` (filter), 502 `error` (provider — not an attempt, retry same client_msg_id), 409 `busy`/`in_progress`/`turn_limit`, 429 `rate_limited`/`queue_full`, 423 `event_paused` |
| POST | /game/sessions/{gsid}/reset | — | destroys conversation + accumulated level state; solves/hints kept |
| POST | /game/sessions/{gsid}/new-chat | — | clears conversation (levels 6+: will keep persistent memory) |
| POST | /game/sessions/{gsid}/hints | hint_index | in-order unlock; cost deducted only in ranked |
| POST | /game/sessions/{gsid}/submit | flag | validates against the caller's own flag (transform-aware) |
| GET | /me/progress | — | own solves across scopes |

Idempotency: resending the same `client_msg_id` replays a finished turn's
stored result, returns 409 `in_progress` while it runs, and re-executes
after a provider error.

## Admin (role=admin; every mutation audited)
| Method | Path | Notes |
|---|---|---|
| POST | /admin/events | create event |
| PATCH | /admin/events/{id} | name/description/times/registration/invite_only/paused/leaderboard_visible |
| POST | /admin/events/{id}/challenges | pin challenge: points, enabled, version?, opens_at?, closes_at? |
| POST | /admin/events/{id}/invites | generate codes (count, max_uses, expires_at?) |
| GET | /admin/events/{id}/enrollments | list |
| DELETE | /admin/events/{id}/enrollments/{user_id} | remove |
| POST | /admin/events/{id}/freeze | `{frozen: bool}` |
| GET | /admin/events/{id}/stats | per-level starts/solves/attempts/hints/time-to-solve + latency percentiles, error counts, tokens, queue depth |
| GET | /admin/events/{id}/export | ranked results CSV |
| GET | /admin/challenges | all versions |
| POST | /admin/challenges/publish | new immutable config version (requires `{flag}` placeholder) |
| GET | /admin/audit | last 200 audit entries |
