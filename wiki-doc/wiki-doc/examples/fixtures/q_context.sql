-- Q-set DDL context: readable and mutable objects for fixtures q01..q11.
-- Static analysis only. PostgreSQL 15+ syntax. Never execute against a real DB.
-- Mirrors the role of examples/context.sql for the Q-manifest (cases-q.json).

CREATE SCHEMA IF NOT EXISTS q_src;
CREATE SCHEMA IF NOT EXISTS q_stg;
CREATE SCHEMA IF NOT EXISTS q_out;
CREATE SCHEMA IF NOT EXISTS q_hist;
CREATE SCHEMA IF NOT EXISTS q_meta;

CREATE TABLE q_src.clients (
    id          bigint,
    phone       text,
    is_phoned   boolean,
    created_at  timestamp
);

CREATE TABLE q_src.task_queue (
    task_id     bigint,
    user_id     bigint,
    status      text,
    priority    integer,
    is_done     boolean,
    is_phoned   boolean,
    created_at  timestamp
);

CREATE TABLE q_src.hours (
    user_id     bigint,
    on_date     date,
    hours       numeric,
    create_date date
);

CREATE TABLE q_src.row_values (
    row_id      bigint,
    ord         integer,
    value       text
);

CREATE TABLE q_src.orders (
    id          bigint,
    amount      numeric
);

CREATE TABLE q_src.order_lines (
    id          bigint,
    qty         numeric,
    price       numeric
);

CREATE TABLE q_src.order_feed (
    id          bigint,
    legacy      text,
    total       integer,
    amount      numeric
);

CREATE TABLE q_src.kpi_facts (
    kpi_id      bigint,
    struct_id   bigint,
    level_no    integer,
    fact_start_date date,
    create_date date,
    value_num   numeric
);

CREATE TABLE q_src.plan_variants (
    plan_id     bigint,
    user_id     bigint,
    priority    integer,
    weight      numeric
);

CREATE TABLE q_src.sla_raw (
    ticket_id   bigint,
    opened_at   timestamp,
    closed_at   timestamp,
    target_min  integer
);

CREATE TABLE q_meta.field_defs (
    object_name text,
    field_name  text,
    position    integer,
    def_value   text
);

-- Mutable targets.
CREATE TABLE q_hist.retro_pairs (
    kpi_id          bigint,
    fact_start_date date
);

CREATE TABLE q_out.out_a (
    id      bigint,
    total   numeric
);

CREATE TABLE q_out.out_b (
    id      bigint,
    total   numeric
);

CREATE TABLE q_out.gp_events (
    id      bigint,
    message text
);

CREATE TABLE q_out.kpi_result (
    kpi_id      bigint,
    struct_id   bigint,
    level_no    integer,
    value_num   numeric,
    norm_value  numeric
);

CREATE TABLE q_out.rr_sl (
    ticket_id   bigint,
    rr_value    numeric,
    sl_value    numeric
);

-- Mutable target used by the positional-INSERT / migration-reorder case (q08).
CREATE TABLE q_out.orders (
    id      bigint,
    legacy  text,
    total   integer
);

-- Readable helper with a known body: call effects are describable.
CREATE OR REPLACE FUNCTION q_meta.log_event(p_message text)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO q_meta.field_defs (object_name, field_name, position, def_value)
    VALUES ('log', p_message, 0, NULL);
END;
$fn$;
