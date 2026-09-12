-- Static analysis context for examples only. PostgreSQL 15+.
CREATE SCHEMA IF NOT EXISTS demo;
CREATE SCHEMA IF NOT EXISTS demo_src;
CREATE SCHEMA IF NOT EXISTS demo_stg;
CREATE SCHEMA IF NOT EXISTS demo_audit;
CREATE SCHEMA IF NOT EXISTS demo_migration;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS archive;

CREATE TABLE demo_src.events (
    id bigint, user_id bigint, name text, value double precision,
    payload text, event_date date
);
CREATE TABLE demo_src.users (id bigint, full_name text, is_active boolean);
CREATE TABLE demo.queue (
    id bigint, status integer, d date, end_date timestamp with time zone
);
CREATE TABLE demo.summary (id bigint PRIMARY KEY, value numeric);
CREATE TABLE demo_stg.summary_tmp (id bigint, value numeric);
CREATE TABLE demo.results_tmp (id bigint, event_date date);
CREATE TABLE demo.orders (
    id bigint, price numeric(12,2), quantity integer, status text
);
CREATE TABLE demo.order_history (id bigint);
CREATE FUNCTION demo_audit.log_event(p_event text, p_id bigint)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    NULL;
END;
$$;

