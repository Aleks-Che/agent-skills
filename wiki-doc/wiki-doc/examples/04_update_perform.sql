CREATE OR REPLACE FUNCTION demo.refresh_summary(p_id bigint, p_status integer)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    PERFORM demo_audit.log_event('start', p_id);
    UPDATE demo.queue
    SET status = p_status, end_date = now()
    WHERE id = p_id;

    MERGE INTO demo.summary AS t
    USING demo_stg.summary_tmp AS s ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET value = s.value
    WHEN NOT MATCHED THEN INSERT (id, value) VALUES (s.id, s.value);

    PERFORM demo_audit.log_event('end', p_id);
END;
$$;

