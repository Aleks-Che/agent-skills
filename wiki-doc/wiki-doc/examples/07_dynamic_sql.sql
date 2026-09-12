CREATE OR REPLACE FUNCTION demo.fn_dynamic(p_suffix text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('TRUNCATE TABLE %I.%I', 'demo_stg', 'events_' || p_suffix);
    EXECUTE format(
        'INSERT INTO %I.%I (id, event_date) SELECT id, event_date FROM demo_src.events',
        'demo_stg', 'events_' || p_suffix
    );
END;
$$;

