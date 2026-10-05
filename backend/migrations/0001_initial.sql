-- VOLT initial schema. Postgres 14+.

CREATE EXTENSION IF NOT EXISTS citext;

-- ============================ identity ============================

CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          citext NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    display_name   text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 40),
    role           text NOT NULL DEFAULT 'player' CHECK (role IN ('player', 'admin')),
    is_verified    boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
    token_hash  text PRIMARY KEY,                -- sha256 of the opaque bearer token
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz
);
CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id);

-- ============================ events ============================

CREATE TABLE events (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                 text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9\-]{1,60}$'),
    name                 text NOT NULL,
    description          text NOT NULL DEFAULT '',
    registration_open    boolean NOT NULL DEFAULT true,
    invite_only          boolean NOT NULL DEFAULT false,
    starts_at            timestamptz NOT NULL,
    ends_at              timestamptz NOT NULL,
    paused               boolean NOT NULL DEFAULT false,
    leaderboard_visible  boolean NOT NULL DEFAULT true,
    leaderboard_frozen_at timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);

CREATE TABLE event_invites (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id   uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    code       text NOT NULL UNIQUE,
    max_uses   integer NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0 AND used_count <= max_uses),
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE enrollments (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id   uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    invite_id  uuid REFERENCES event_invites(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, event_id)
);

-- ============================ challenges ============================

CREATE TABLE challenges (
    id         text PRIMARY KEY,          -- 'l1' .. 'l10'
    number     integer NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Versioned, published challenge configuration. config is the full
-- player/model definition: title, briefing, lesson, system prompt template,
-- filter parameters, hints (with costs), default points, model params.
CREATE TABLE challenge_versions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    challenge_id text NOT NULL REFERENCES challenges(id),
    version      integer NOT NULL,
    config       jsonb NOT NULL,
    published_at timestamptz,
    created_by   uuid REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (challenge_id, version)
);

-- Which challenges an event offers, pinned to a version, with event points.
CREATE TABLE event_challenges (
    event_id     uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    challenge_id text NOT NULL REFERENCES challenges(id),
    version_id   uuid NOT NULL REFERENCES challenge_versions(id),
    points       integer NOT NULL CHECK (points > 0),
    enabled      boolean NOT NULL DEFAULT true,
    opens_at     timestamptz,
    closes_at    timestamptz,
    PRIMARY KEY (event_id, challenge_id)
);

-- ============================ gameplay ============================

-- scope: 'practice' or the event uuid (as text). Keeps practice and each
-- event's ranked play fully separated.
CREATE TABLE player_flags (
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope        text NOT NULL,
    challenge_id text NOT NULL REFERENCES challenges(id),
    flag         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, scope, challenge_id)
);

CREATE TABLE game_sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_id text NOT NULL REFERENCES challenges(id),
    scope        text NOT NULL,              -- 'practice' | event uuid text
    event_id     uuid REFERENCES events(id),
    mode         text NOT NULL CHECK (mode IN ('practice', 'ranked')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, challenge_id, scope),
    CHECK ((mode = 'practice' AND scope = 'practice' AND event_id IS NULL)
        OR (mode = 'ranked' AND event_id IS NOT NULL))
);

CREATE TABLE conversations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    game_session_id uuid NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    generation      integer NOT NULL DEFAULT 1,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX conversations_one_active
    ON conversations(game_session_id) WHERE active;

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    role            text NOT NULL CHECK (role IN ('user', 'assistant', 'filter')),
    content         text NOT NULL,          -- raw (what the model saw / said)
    visible_content text NOT NULL,          -- what the player sees (post output-filter)
    in_context      boolean NOT NULL DEFAULT true,  -- include in future model context
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);

CREATE TABLE turns (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    client_msg_id     text NOT NULL,
    status            text NOT NULL CHECK (status IN ('pending', 'done', 'blocked', 'error')),
    error_kind        text,                 -- provider_timeout | provider_error | ...
    user_message_id   uuid REFERENCES messages(id),
    reply_message_id  uuid REFERENCES messages(id),
    leaked            boolean NOT NULL DEFAULT false,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    latency_ms        integer,
    model             text,
    challenge_version integer,
    created_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    UNIQUE (conversation_id, client_msg_id)
);
CREATE INDEX turns_conversation_idx ON turns(conversation_id, created_at);
CREATE INDEX turns_created_idx ON turns(created_at);

CREATE TABLE hint_unlocks (
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope        text NOT NULL,
    challenge_id text NOT NULL REFERENCES challenges(id),
    hint_index   integer NOT NULL CHECK (hint_index >= 0),
    cost         integer NOT NULL DEFAULT 0 CHECK (cost >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, scope, challenge_id, hint_index)
);

CREATE TABLE solves (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_id  text NOT NULL REFERENCES challenges(id),
    scope         text NOT NULL,
    event_id      uuid REFERENCES events(id),
    mode          text NOT NULL CHECK (mode IN ('practice', 'ranked')),
    method        text NOT NULL CHECK (method IN ('auto', 'submit')),
    points        integer NOT NULL DEFAULT 0,   -- gross challenge points at solve time
    hints_cost    integer NOT NULL DEFAULT 0,   -- deductions applied at solve time
    turn_id       uuid REFERENCES turns(id),
    solved_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, scope, challenge_id)       -- one solve per player/scope/challenge
);
CREATE INDEX solves_event_idx ON solves(event_id, solved_at);

CREATE TABLE flag_submissions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope        text NOT NULL,
    challenge_id text NOT NULL REFERENCES challenges(id),
    submitted    text NOT NULL,
    correct      boolean NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX flag_submissions_user_idx ON flag_submissions(user_id, created_at);

-- ============================ operations ============================

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    actor_id   uuid REFERENCES users(id),
    action     text NOT NULL,
    target     text NOT NULL DEFAULT '',
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_created_idx ON audit_log(created_at);
