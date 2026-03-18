-- 0024_add_roles_and_seed_admin.sql
-- Replaces the user_role enum column approach with a proper roles table + user_roles join table.
-- Rolls back the enum column if it was previously applied.

-- 1. Drop enum column from users if it exists (from previous version of this migration)
ALTER TABLE users DROP COLUMN IF EXISTS role;

-- 2. Drop the enum type if it exists
DROP TYPE IF EXISTS user_role;

-- 3. Create the roles lookup table
CREATE TABLE IF NOT EXISTS roles (
    role_id   SERIAL       PRIMARY KEY,
    role_name VARCHAR(50)  NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_roles_role_name ON roles (role_name);

-- 4. Seed the two roles
INSERT INTO roles (role_name) VALUES ('admin'), ('finance_associate')
ON CONFLICT (role_name) DO NOTHING;

-- 5. Create the user_roles join table (one role per user enforced by UNIQUE on user_id)
CREATE TABLE IF NOT EXISTS user_roles (
    user_role_id SERIAL      PRIMARY KEY,
    user_id      INTEGER     NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role_id      INTEGER     NOT NULL REFERENCES roles(role_id) ON DELETE RESTRICT,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_roles_user_id UNIQUE (user_id)   -- one role per user
);

CREATE INDEX IF NOT EXISTS ix_user_roles_user_id  ON user_roles (user_id);
CREATE INDEX IF NOT EXISTS ix_user_roles_role_id  ON user_roles (role_id);
