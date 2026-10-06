-- Level 9: MCP tool loop, player-installed servers, .txt attachments.
--
-- challenge_tools holds the MCP server manifest a player INSTALLS for a
-- challenge. Scoped like flags and memory: (user, scope, challenge), one
-- manifest per scope. An installed server is software, not conversation
-- state, so 'New chat' keeps it and only 'Reset level' uninstalls it.
--
-- The manifest is player-authored, untrusted text. It reaches the model as
-- the tool catalogue (that is the level's vulnerability), so every field is
-- length-capped at the schema layer before it is ever stored.

CREATE TABLE challenge_tools (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope        text NOT NULL,
    challenge_id text NOT NULL REFERENCES challenges(id),
    manifest     jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, scope, challenge_id)
);

-- Attachments ride on the user message that carried them. They are NEVER
-- interpolated into a prompt: the agent can only reach one by calling the
-- filesystem server's read_file, so an attachment's content always arrives
-- as a tool result. That provenance is the point of the level.
ALTER TABLE messages
    ADD COLUMN attachment_name text,
    ADD COLUMN attachment_text text;
