CREATE TYPE severity_level_enum AS ENUM ('LOW', 'MEDIUM', 'HIGH');

-- 2. Add the column (nullable to allow backfill before constraining)
ALTER TABLE dispute_type
    ADD COLUMN severity_level severity_level_enum NULL;

-- 3. Backfill existing rows
UPDATE dispute_type SET severity_level = 'HIGH'   WHERE dispute_type_id IN (1, 2, 4, 6);
UPDATE dispute_type SET severity_level = 'MEDIUM'  WHERE dispute_type_id IN (3, 5, 7);
UPDATE dispute_type SET severity_level = 'LOW'     WHERE dispute_type_id IN (8, 9);

-- 4. Add index
CREATE INDEX ix_dispute_type_severity_level ON dispute_type (severity_level);
