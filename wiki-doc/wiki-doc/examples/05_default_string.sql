CREATE OR REPLACE FUNCTION demo.fn_default_value(p_date_start varchar, p_date_end varchar)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_start date;
    v_end date;
BEGIN
    IF p_date_start = 'default' THEN
        SELECT min(d), max(d) INTO v_start, v_end
        FROM demo.queue WHERE status = 1;
    ELSE
        v_start := to_date(p_date_start, 'dd.mm.yyyy');
        v_end := to_date(p_date_end, 'dd.mm.yyyy');
    END IF;
    INSERT INTO demo.results_tmp (id, event_date)
    SELECT e.id, e.event_date FROM demo_src.events AS e
    WHERE e.event_date BETWEEN v_start AND v_end;
END;
$$;

