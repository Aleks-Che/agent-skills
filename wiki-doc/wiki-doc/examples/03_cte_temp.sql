CREATE OR REPLACE FUNCTION demo.fn_with_cte(p_date date)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    CREATE TEMP TABLE tmp_filtered (id bigint, name text) ON COMMIT DROP;
    INSERT INTO tmp_filtered (id, name)
    WITH active_users AS (
        SELECT id, full_name FROM demo_src.users WHERE is_active = true
    ), recent_events AS (
        SELECT user_id, max(event_date) AS last_event
        FROM demo_src.events
        WHERE event_date >= p_date - INTERVAL '30 days'
        GROUP BY user_id
    )
    SELECT u.id, u.full_name
    FROM active_users AS u
    JOIN recent_events AS r ON r.user_id = u.id;
END;
$$;

