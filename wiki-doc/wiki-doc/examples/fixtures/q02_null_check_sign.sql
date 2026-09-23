-- Q-01 / D02: NULL-check polarity is a load-bearing fact.
-- The WHERE predicate is `phone IS NOT NULL`. Replacing it with `IS NULL`
-- (in text only, or coherently in facts+text) must be rejected against SQL.
CREATE OR REPLACE FUNCTION q_out.count_phoned_clients()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    SELECT count(*) INTO n
    FROM q_src.clients AS c
    WHERE c.phone IS NOT NULL
      AND c.is_phoned IS NOT TRUE;
    RETURN n;
END;
$$;
