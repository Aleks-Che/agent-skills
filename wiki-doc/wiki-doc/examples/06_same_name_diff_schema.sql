CREATE OR REPLACE FUNCTION core.orders_summary()
RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT count(*) FROM demo.orders;
$$;

CREATE OR REPLACE FUNCTION archive.orders_summary()
RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT count(*) FROM demo.order_history;
$$;

CREATE OR REPLACE FUNCTION core.orders_summary(p_min_id bigint)
RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT count(*) FROM demo.orders WHERE id >= p_min_id;
$$;

