-- Q-01 / D04: fixed-date formula transition with two KPI structures.
-- Branch boundary is `p_date >= DATE '2026-01-01'`, bound to p_date (on_date),
-- NOT to create_date. Divisors differ per branch (8 vs 7.2). Dropping the
-- >=2026-01-01 branch or rebinding the boundary to create_date must be a
-- technical defect tied to BOTH KPI structures.
CREATE OR REPLACE FUNCTION q_out.calc_norm(p_uid bigint, p_date date)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE
    v_norm numeric;
BEGIN
    IF p_date >= DATE '2026-01-01' THEN
        SELECT h.hours / 8.0 INTO v_norm
        FROM q_src.hours AS h
        WHERE h.user_id = p_uid AND h.on_date = p_date;
    ELSE
        SELECT h.hours / 7.2 INTO v_norm
        FROM q_src.hours AS h
        WHERE h.user_id = p_uid AND h.on_date = p_date;
    END IF;
    RETURN coalesce(v_norm, 0);
END;
$$;
