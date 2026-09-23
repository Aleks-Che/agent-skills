-- Q-01 / D08 + Q-07 unknown control: RR and SL formulas.
--
-- rr_value  = share of tickets closed within target_min
--             (in-target count / total count). NOT a plain average of minutes.
-- sl_value  = 1 - rr_value (complement), computed from the SAME two counts.
-- `rr` as a business abbreviation is NOT decoded here; any expanded name would
-- be an unverified claim and must be rejected or explicitly limited.
CREATE OR REPLACE FUNCTION q_out.load_rr_sl()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    INSERT INTO q_out.rr_sl (ticket_id, rr_value, sl_value)
    SELECT
        s.ticket_id,
        CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0
             THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min))
                  / count(*)::numeric
             ELSE 0 END AS rr_value,
        1 - CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0
                 THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min))
                      / count(*)::numeric
                 ELSE 0 END AS sl_value
    FROM q_src.sla_raw AS s
    GROUP BY s.ticket_id, s.target_min, s.opened_at, s.closed_at;

    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
