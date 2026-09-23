-- Q-01 / D11: CTE vs derived table identity and per-INSERT CTE scopes.
-- `tm` is a CTE, not a physical table, and the two `tm` names live in DIFFERENT
-- scopes (one per INSERT). Physical reads are q_src.orders and q_src.order_lines.
-- `src` in the second INSERT is a derived-table alias, not a CTE. Reporting `tm`
-- as a physical source, or excluding `q_out` targets, must be a subject defect.
CREATE OR REPLACE FUNCTION q_out.two_inserts()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO q_out.out_a (id, total)
    WITH tm AS (
        SELECT o.id, o.amount
        FROM q_src.orders AS o
        WHERE o.amount IS NOT NULL
    )
    SELECT tm.id, tm.amount FROM tm;

    INSERT INTO q_out.out_b (id, total)
    WITH tm AS (
        SELECT l.id, l.qty * l.price AS amount
        FROM q_src.order_lines AS l
    )
    SELECT s.id, s.amount
    FROM (
        SELECT tm.id, tm.amount FROM tm
    ) AS s;
END;
$$;
