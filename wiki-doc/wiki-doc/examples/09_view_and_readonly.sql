CREATE OR REPLACE VIEW demo.order_totals AS
SELECT id, price * quantity AS total
FROM demo.orders
WHERE status = 'paid';

CREATE OR REPLACE FUNCTION demo.total_for(p_id bigint)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT price * quantity
    FROM demo.orders
    WHERE id = p_id AND status = 'paid';
$$;

