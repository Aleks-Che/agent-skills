-- Applied after baseline/orders.sql, before migrations/043_amount.sql.
ALTER TABLE demo_migration.orders DROP COLUMN legacy;
ALTER TABLE demo_migration.orders RENAME COLUMN total TO amount;
ALTER TABLE demo_migration.orders ALTER COLUMN amount TYPE numeric(12,2);
ALTER TABLE demo_migration.orders ADD COLUMN updated_on date;

