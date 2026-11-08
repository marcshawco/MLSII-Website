-- Shared AI memory (Honcho-style) schema. Idempotent: safe to re-run.
-- Apply with: python scripts/migrate.py

CREATE EXTENSION IF NOT EXISTS vector;

-- Peers: every participant, human or AI.
CREATE TABLE IF NOT EXISTS peers (
    id           text PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('human', 'assistant', 'system')),
    display_name text NOT NULL
);

INSERT INTO peers (id, kind, display_name) VALUES
    ('marcus', 'human', 'Marcus'),
    ('claude', 'assistant', 'Claude'),
    ('gemini', 'assistant', 'Gemini'),
    ('grok',   'assistant', 'Grok'),
    ('muse',   'assistant', 'Muse'),
    ('dreamer', 'system',   'Dreamer (nightly consolidation)')
ON CONFLICT (id) DO NOTHING;

-- OAuth (single owner, many MCP clients). Secrets are stored as sha256 hashes.
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id                  text PRIMARY KEY,
    client_name                text NOT NULL,
    redirect_uris              jsonb NOT NULL,
    token_endpoint_auth_method text NOT NULL DEFAULT 'none',
    created_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash             text PRIMARY KEY,
    client_id             text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    redirect_uri          text NOT NULL,
    code_challenge        text NOT NULL,
    code_challenge_method text NOT NULL,
    scope                 text NOT NULL,
    peer_id               text NOT NULL REFERENCES peers (id),
    expires_at            timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id                 bigserial PRIMARY KEY,
    access_hash        text NOT NULL UNIQUE,
    refresh_hash       text UNIQUE,
    client_id          text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    peer_id            text NOT NULL REFERENCES peers (id),
    scope              text NOT NULL,
    access_expires_at  timestamptz NOT NULL,
    refresh_expires_at timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_used_at       timestamptz,
    revoked_at         timestamptz
);
CREATE INDEX IF NOT EXISTS oauth_tokens_peer_idx ON oauth_tokens (peer_id) WHERE revoked_at IS NULL;

-- Failed owner-password attempts, for a simple per-IP lockout.
CREATE TABLE IF NOT EXISTS auth_failures (
    id bigserial PRIMARY KEY,
    ip text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auth_failures_ip_idx ON auth_failures (ip, at);

-- Sessions an assistant chose to log, and their messages.
CREATE TABLE IF NOT EXISTS sessions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    peer_id    text NOT NULL REFERENCES peers (id),
    title      text NOT NULL DEFAULT '',
    summary    text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_created_idx ON sessions (created_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id         bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    peer_id    text NOT NULL REFERENCES peers (id),
    content    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_session_idx ON messages (session_id, id);

-- Conclusions: the memory itself.
--   explicit  = stated directly
--   deductive = inferred from premises
--   inductive = pattern across sessions (written by the dream)
CREATE TABLE IF NOT EXISTS conclusions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_peer     text NOT NULL DEFAULT 'marcus' REFERENCES peers (id),
    observer_peer    text NOT NULL REFERENCES peers (id),
    kind             text NOT NULL CHECK (kind IN ('explicit', 'deductive', 'inductive')),
    content          text NOT NULL,
    premises         jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence       real NOT NULL DEFAULT 0.8,
    status           text NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'superseded', 'pending_review', 'retracted')),
    superseded_by    uuid REFERENCES conclusions (id) ON DELETE SET NULL,
    reinforced_count integer NOT NULL DEFAULT 0,
    flag_reason      text,
    session_id       uuid REFERENCES sessions (id) ON DELETE SET NULL,
    embedding        vector(1536),
    embedding_model  text,
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conclusions_embedding_idx
    ON conclusions USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS conclusions_tsv_idx ON conclusions USING gin (tsv);
CREATE INDEX IF NOT EXISTS conclusions_status_idx ON conclusions (status, created_at DESC);

-- Peer card: compact identity summary, rebuilt by the dream or edited by Marcus.
CREATE TABLE IF NOT EXISTS peer_cards (
    peer_id    text PRIMARY KEY REFERENCES peers (id),
    card       jsonb NOT NULL DEFAULT '[]'::jsonb,
    version    integer NOT NULL DEFAULT 0,
    updated_by text NOT NULL DEFAULT 'marcus',
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS peer_card_history (
    id         bigserial PRIMARY KEY,
    peer_id    text NOT NULL REFERENCES peers (id),
    card       jsonb NOT NULL,
    version    integer NOT NULL,
    updated_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Lightweight retry queue for reasoning work that failed inline.
CREATE TABLE IF NOT EXISTS jobs (
    id         bigserial PRIMARY KEY,
    kind       text NOT NULL,
    payload    jsonb NOT NULL,
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    attempts   integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_pending_idx ON jobs (status, id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS dream_runs (
    id          bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats       jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes       text NOT NULL DEFAULT ''
);
