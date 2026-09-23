-- Q-01 / D08: hour normalization and priority-based plan selection.
--
-- Normalization: hours / 8.0 for a standard day. Plan choice: highest priority
-- wins, tie-broken by larger weight (ORDER BY priority DESC, weight DESC,
-- LIMIT 1). Dropping either formula while keeping the KPI/plan names is
-- incomplete coverage and must not be ready.
CREATE OR REPLACE FUNCTION q_out.load_norm_plan(p_uid bigint, p_date date)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE
    v_plan bigint;
    v_norm numeric;
BEGIN
    SELECT p.plan_id INTO v_plan
    FROM q_src.plan_variants AS p
    WHERE p.user_id = p_uid
    ORDER BY p.priority DESC, p.weight DESC
    LIMIT 1;

    SELECT h.hours / 8.0 INTO v_norm
    FROM q_src.hours AS h
    WHERE h.user_id = p_uid AND h.on_date = p_date;

    INSERT INTO q_out.kpi_result (kpi_id, struct_id, level_no, value_num, norm_value)
    VALUES (v_plan, 0, 0, v_norm, v_norm);

    RETURN coalesce(v_norm, 0);
END;
$$;
