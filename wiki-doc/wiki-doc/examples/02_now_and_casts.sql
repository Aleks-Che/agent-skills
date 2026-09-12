CREATE TABLE demo.events_log (
    id bigint,
    event_name varchar(64),
    event_value numeric(18,4),
    created_at timestamp with time zone,
    payload jsonb
);

INSERT INTO demo.events_log (id, event_name, event_value, created_at, payload)
SELECT e.id, e.name::varchar(16), e.value::numeric(12,2), now(), e.payload::jsonb
FROM demo_src.events AS e;

