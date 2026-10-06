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
| POST | /game/sessions/{gsid}/message | client_msg_id, text, attachment? | one turn. Statuses: `done`, `blocked` (filter), 502 `error` (provider — not an attempt, retry same client_msg_id), 409 `busy`/`in_progress`/`turn_limit`, 429 `rate_limited`/`queue_full`, 423 `event_paused` |
| POST | /game/sessions/{gsid}/reset | — | destroys conversation + accumulated level state; also wipes L8 memory and uninstalls an L9 MCP server; solves/hints kept |
| POST | /game/sessions/{gsid}/new-chat | — | clears the conversation but keeps out-of-conversation level state (L8 persistent memory, an L9 installed server) |
| PUT | /game/sessions/{gsid}/tools | manifest (object, or null to uninstall) | installs/replaces the player's MCP server on a tool-loop level. 422 `invalid_manifest` with the reason; 400 `unsupported` on levels with no tool catalogue. Returns the same `mcp` view the session state carries |
| POST | /game/sessions/{gsid}/hints | hint_index | in-order unlock; cost deducted only in ranked |
| POST | /game/sessions/{gsid}/submit | flag | validates against the caller's own flag (transform-aware) |
| GET | /me/progress | — | own solves across scopes |

### Attachments (tool-loop levels)

`attachment` on a turn is `{"name": "<something>.txt", "text": "..."}`: text
only, `.txt` only, and bounded three ways — `MAX_ATTACHMENT_CHARS` (8000),
`MAX_ATTACHMENT_BYTES` (24576, which binds separately since an astral-plane
character costs 4 bytes) and `MAX_ATTACHMENTS_PER_CONVERSATION` (5, returning
409 `attachment_limit`). The session's `mcp.limits` reports all three so a
client can reject a file before uploading it.

An attachment is stored on the message that carried it and mounted on the
level's simulated filesystem; it is **never** interpolated into a prompt, so a
model can reach its content only by calling a file-reading tool. Levels
without file tools never see it.

### The `mcp` session field

Present only on levels whose config sets `pipeline: "mcp_agent"`. Carries
`connected` (trusted servers), `installed` (the player's server or null),
`limits` and `template`. Every tool is reported twice: `summary` is what a
person approving the server is shown (the description's first line, truncated),
`model_sees` is the full description the model is handed. Clients render both;
the difference is the vulnerability the level teaches.

A turn's `extras.mcp` carries `steps` (the loop as it ran), `inbound` (calls
that reached the player's own server, arguments verbatim), `dlp_blocked` and
`files_visible`.

`steps` is the organisation's audit view and reports the SHAPE of each call
only: argument names with the size of each value, and for a successful call
how much came back. It never carries a value. Redacting on content cannot be
made safe here, because a flag halves into two short strings and any rule that
lets some values through lets a player split it across two parameters, or two
steps, and rejoin the halves by eye.

The challenge block also carries `new_chat`: false on levels that keep no
state outside the conversation, where "New chat" would do exactly what "Reset
level" does.

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
