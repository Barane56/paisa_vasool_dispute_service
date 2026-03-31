-- =============================================================================
-- 0001_create_users.sql
-- =============================================================================

CREATE TABLE users (
    user_id       SERIAL       PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    email         VARCHAR(150) NOT NULL UNIQUE,
    password_hash TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX ix_users_email ON users (email);
