CREATE OR REPLACE FUNCTION demo.load_missing()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO demo.missing_target (id, loaded_at)
    SELECT e.id, now()
    FROM demo_src.events AS e;
END;
$$;

