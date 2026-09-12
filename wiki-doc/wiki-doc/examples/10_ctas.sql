CREATE TABLE demo.event_clock AS
SELECT e.id, now() AS loaded_at, DATE '2025-01-01' AS cutoff
FROM demo_src.events AS e;

