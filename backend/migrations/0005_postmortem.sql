-- Level Postmortem: the short defence debrief a player unlocks by solving a
-- level. One scored decision per (player, scope, challenge), recorded here.
--
-- Shaped like `solves`: scope keeps practice and each event separate, and
-- event_id is carried so the leaderboard can aggregate ranked awards with a
-- plain join. The primary key makes the answer final: the first INSERT wins
-- and later posts are idempotent replays, so the 200 points cannot be farmed
-- by re-answering.
--
-- The debrief CONTENT is not stored here. It lives in
-- app/challenges/postmortems.py and is served from code: it is teaching
-- copy with no bearing on game balance, so it needs no per-event version
-- pinning and a typo fix needs no migration.

CREATE TABLE postmortems (
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope        text NOT NULL,
    challenge_id text NOT NULL REFERENCES challenges(id),
    event_id     uuid REFERENCES events(id),
    choice       text NOT NULL,
    correct      boolean NOT NULL,
    points       integer NOT NULL DEFAULT 0 CHECK (points >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, scope, challenge_id)
);

CREATE INDEX postmortems_event_idx ON postmortems(event_id, created_at);
