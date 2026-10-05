-- Levels 6-10: multi-model pipelines + persistent memory.
--
-- challenge_memory backs Level 8 (persistent memory poisoning). Items are
-- scoped like flags: (user, scope, challenge). source_conversation_id records
-- which conversation wrote the item, so an item is only ACTIVE in later
-- conversations (delayed activation) and never self-triggers in the session
-- that stored it. 'Reset level' deletes a challenge's memory; 'New chat'
-- keeps it (the distinction the level teaches).

CREATE TABLE challenge_memory (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope                  text NOT NULL,
    challenge_id           text NOT NULL REFERENCES challenges(id),
    source_conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content                text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX challenge_memory_lookup_idx
    ON challenge_memory(user_id, scope, challenge_id, created_at);
