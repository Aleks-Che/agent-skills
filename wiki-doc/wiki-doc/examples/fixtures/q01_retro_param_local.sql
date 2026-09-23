-- Q-01 / D01: parameter reachability through a local variable.
-- p_retro is NOT unused: it flows into v_retro, which gates a historical
-- DELETE/INSERT pair. The history window is bounded by fact_start_date against
-- a fixed date and is NOT limited to the call period.
CREATE OR REPLACE FUNCTION q_hist.apply_retro(p_retro boolean)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    v_retro boolean := p_retro;
    n       bigint;
BEGIN
    IF v_retro THEN
        DELETE FROM q_hist.retro_pairs AS r
        WHERE r.fact_start_date < DATE '2026-01-01';

        INSERT INTO q_hist.retro_pairs (kpi_id, fact_start_date)
        SELECT k.kpi_id, k.fact_start_date
        FROM q_src.kpi_facts AS k
        WHERE k.fact_start_date < DATE '2026-01-01';

        GET DIAGNOSTICS n = ROW_COUNT;
    ELSE
        n := 0;
    END IF;
    RETURN n;
END;
$$;
