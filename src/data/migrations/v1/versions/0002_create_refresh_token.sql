-- =============================================================================
-- 0002_create_refresh_token.sql
-- =============================================================================

CREATE TABLE refresh_token (
    token_id      SERIAL      PRIMARY KEY,
    user_id       INTEGER     NOT NULL REFERENCES "users"(user_id) ON DELETE CASCADE,
    jti           TEXT        NOT NULL UNIQUE,
    refresh_token TEXT        NOT NULL UNIQUE,
    is_revoked    BOOLEAN     NOT NULL DEFAULT FALSE,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_refresh_token_token      ON refresh_token (refresh_token);
CREATE INDEX ix_refresh_token_jti        ON refresh_token (jti);
CREATE INDEX ix_refresh_token_user_id    ON refresh_token (user_id);
CREATE INDEX ix_refresh_token_expires_at ON refresh_token (expires_at);