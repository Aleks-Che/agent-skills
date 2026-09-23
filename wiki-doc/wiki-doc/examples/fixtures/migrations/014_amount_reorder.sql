-- Migration 014: extend and reorder q_out.orders.
-- Final column order after this migration: id, amount, legacy, total.
-- The positional INSERT in q08_positional_insert.sql targets THIS order.
-- Synthetic empty-table fixture: DROP/ADD moves the old columns to the tail.
-- ADD amount alone would leave (id, legacy, total, amount), not this order.
ALTER TABLE q_out.orders DROP COLUMN legacy;
ALTER TABLE q_out.orders DROP COLUMN total;
ALTER TABLE q_out.orders ADD COLUMN amount numeric;
ALTER TABLE q_out.orders ADD COLUMN legacy text;
ALTER TABLE q_out.orders ADD COLUMN total integer;
ALTER TABLE q_out.orders ALTER COLUMN amount SET DEFAULT 0;
COMMENT ON COLUMN q_out.orders.amount IS 'Order amount in account currency';
