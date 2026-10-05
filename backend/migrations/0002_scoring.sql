-- Scoring v2: effort captured at solve time.
--
-- attempts / tokens_spent: the player's cumulative attempts and model tokens
-- on that challenge in that scope at the moment it was solved (measured
-- across every conversation, so resets cannot launder effort).
-- bonus: the efficiency bonus awarded at solve time (see app/scoring.py).

ALTER TABLE solves
    ADD COLUMN attempts     integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    ADD COLUMN tokens_spent integer NOT NULL DEFAULT 0 CHECK (tokens_spent >= 0),
    ADD COLUMN bonus        integer NOT NULL DEFAULT 0 CHECK (bonus >= 0);

-- Leaderboards aggregate live effort across an event's turns.
CREATE INDEX IF NOT EXISTS game_sessions_event_idx
    ON game_sessions(event_id) WHERE event_id IS NOT NULL;
