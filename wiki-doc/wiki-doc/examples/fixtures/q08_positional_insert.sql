-- Q-01 / D10: positional INSERT and a migration that reorders/extends columns.
--
-- Baseline q_out.orders is (id, legacy, total). Migration 014 inserts `amount`
-- and reorders to (id, amount, legacy, total). The INSERT below is positional
-- with no column list, so its meaning depends on the chosen schema state.
-- Documenting 3 columns or the pre-migration order/types must be a defect.
CREATE OR REPLACE FUNCTION q_out.load_positional()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    INSERT INTO q_out.orders
    SELECT s.id, s.amount, s.legacy, s.total
    FROM q_src.order_feed AS s
    WHERE s.id IS NOT NULL;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
