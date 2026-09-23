-- Q-01 / D07 + D08: KPI levels and structures are counted per pair, not flat.
--
-- 2 KPI/structure pairs (kpi 10 / struct 100 and kpi 20 / struct 200), each with
-- levels (1, 2) and (1), respectively -> 3 calculation branches, not a fixed
-- number of output data rows. Claiming the same levels for EVERY KPI, or 3
-- pairs, must be rejected. Struct 100 level 2 uses value_num * 1.5; struct 200
-- level 1 uses value_num + 10. Losing one
-- of those formulas while keeping the KPI names is incomplete coverage (D08).
CREATE OR REPLACE FUNCTION q_out.load_kpi_levels()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    INSERT INTO q_out.kpi_result (kpi_id, struct_id, level_no, value_num, norm_value)
    SELECT f.kpi_id, f.struct_id, 1, f.value_num, f.value_num
    FROM q_src.kpi_facts AS f
    WHERE f.kpi_id = 10 AND f.struct_id = 100 AND f.level_no = 1;

    INSERT INTO q_out.kpi_result (kpi_id, struct_id, level_no, value_num, norm_value)
    SELECT f.kpi_id, f.struct_id, 2, f.value_num, f.value_num * 1.5
    FROM q_src.kpi_facts AS f
    WHERE f.kpi_id = 10 AND f.struct_id = 100 AND f.level_no = 2;

    INSERT INTO q_out.kpi_result (kpi_id, struct_id, level_no, value_num, norm_value)
    SELECT f.kpi_id, f.struct_id, 1, f.value_num, f.value_num + 10
    FROM q_src.kpi_facts AS f
    WHERE f.kpi_id = 20 AND f.struct_id = 200 AND f.level_no = 1;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
