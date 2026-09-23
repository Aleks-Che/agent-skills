-- Q-01 / D05 + crash reproducer: Greenplum declaration attribute AFTER the body
-- and a nested derived table whose inner WHERE sits inside the outer FROM tail.
--
-- `EXECUTE ON MASTER` is a declaration attribute, not a dynamic EXECUTE of the
-- body. Removing or denying it must be detected in inventory and document.
--
-- The nested `(SELECT ... WHERE ...)` is the trigger of the legacy-fallback
-- crash: the FROM/USING tail splitter cuts at the inner WHERE and then raises
-- 'Unbalanced SQL list' (exit 2). Diagnostics must replace the crash.
CREATE OR REPLACE FUNCTION q_out.gp_master_probe(p_msg text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM q_out.gp_events AS g
    WHERE g.id IN (
        SELECT e.id FROM (
            SELECT id FROM q_src.events WHERE message = p_msg
        ) AS e
    );

    SELECT count(*) INTO n
    FROM (
        SELECT id FROM q_src.events WHERE message IS NOT NULL
    ) AS s;

    PERFORM q_meta.log_event('gp_master_probe done');
    RETURN n;
END;
$$ LANGUAGE plpgsql EXECUTE ON MASTER;
