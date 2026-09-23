-- Q-set Greenplum DDL context for fixture q05 and later Q-03 work.
-- Contains ONLY Greenplum-specific attributes so that PostgreSQL AST rejects it.
-- Static analysis only. Never execute against a real DB.

CREATE SCHEMA IF NOT EXISTS q_src;
CREATE SCHEMA IF NOT EXISTS q_out;

CREATE TABLE q_src.events (
    id      bigint,
    message text
) DISTRIBUTED BY (id);

CREATE TABLE q_out.gp_events (
    id      bigint,
    message text
) DISTRIBUTED RANDOMLY;

CREATE TABLE q_out.gp_store (
    id      bigint,
    payload text
) DISTRIBUTED BY (id)
  WITH (appendonly = true, compresstype = zlib, compresslevel = 5);
