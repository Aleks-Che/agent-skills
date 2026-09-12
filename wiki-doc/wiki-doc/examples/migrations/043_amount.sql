ALTER TABLE demo_migration.orders ALTER COLUMN amount TYPE numeric(18,4);
ALTER TABLE demo_migration.orders ALTER COLUMN amount SET DEFAULT 0;
COMMENT ON COLUMN demo_migration.orders.amount IS 'Amount after schema migration';

