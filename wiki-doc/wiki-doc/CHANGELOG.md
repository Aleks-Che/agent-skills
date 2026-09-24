# Changelog

All notable changes to the wiki-doc package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — 2026-09-24

### Added

- **Q-04 partial DDL support**: catalog-guarded DROP COLUMN migration template,
  `SELECT *` / `alias.*` expansion against established DDL state,
  positional INSERT with omitted target list resolved against
  migration manifest, and named contracts for Oracle-compatibility
  functions (`last_day` / `add_months` → date under the Oracle contract)
  with honest `unknown` for server availability.
- **Greenplum storage option masking**: `orientation = ROW` parsed via
  lexical adapter without forcing pglast to accept a reserved keyword.
- New tests `tests/test_q04_ddl_star.py` (24 cases) and updated
  `tests/test_q03_greenplum.py` to cover GP-only bare storage values.
- Unknown facts for Oracle-compatibility functions emitted from
  `scripts/build_bundle.py`; availability is not implied.

### Changed

- `scripts/sql_ast.py`: PL/pgSQL guards propagated through DDL/CTAS/CALL/
  EXECUTE/CTE/RETURN; GET [STACKED] DIAGNOSTICS represented as ASSIGN
  with diagnostic kind and stacked flag; RAISE fields include level,
  message, arguments, condition, USING options and rethrow; ELSIF/ELSE
  guard captures NULL/FALSE via `(prev) IS NOT TRUE`.
- `scripts/ddl.py`: dynamic DO migration only accepted on the
  pg_attribute-guarded DROP COLUMN template; conditional DROP resolved
  against manifest baseline; `DROP VIEW IF EXISTS`, `TRUNCATE`, `UPDATE`
  accepted as data-only companions.
- `scripts/sql_types.py`: wildcard outputs expanded in inventory and
  rewritten in derived targets; positional INSERT mappings use the
  DDL/manifest column order after DROP/ADD.
- `references/facts.md`, `references/sql-support.md`,
  `docs/dialect-support.md` updated with the new fields and the
  catalog-guarded DO template.

### Fixed

- Set-operation audit: preserve every UNION/INTERSECT/EXCEPT node, ALL, operand
  order and nesting, local WITH and root ORDER BY/LIMIT/OFFSET. The full gate
  checks this structure against SQL. Output width is checked on both operands;
  left names do not establish common expression/type evidence. Direct set
  outputs retain null expression/type with unknown status. Add 15 regression
  tests, including fresh-hash mutations and false-ready controls.
- The large control now retains three physical-source wildcard gaps plus
  EXCEPTION after the pinned DDL/column catalog pass; Q-04 remains in progress.
- CTE/derived wildcard review: preserve unnamed trailing columns under partial
  CTE aliases, reject unknown output widths and excess aliases, distinguish
  CTE scope from FROM aliases and qualified physical relations, and link nested
  derived results through `result_for`. Expand to a real fixpoint without a
  ten-pass limit. Keep `source_ref` on local column evidence so bundle building
  does not crash. Positional INSERT mappings use the same scoped projections.
- Add 15 independent regression tests in `test_q04_wildcard_review.py`, including
  full gate positive/negative controls. That earlier snapshot retained
  71 wildcard gaps plus EXCEPTION after DDL enrichment/column catalog.
- Completion audit: reject unsound catalog DROP proofs (changed join/guards,
  NOT IN, LIMIT/OFFSET, early RETURN, initializers and unsafe identifiers),
  including the bypass of Greenplum distribution-column protection.
- Preserve wildcard gaps for USING/NATURAL joins and unresolved projections;
  retain quoted identifiers and apply declared VIEW/CTAS names after expansion.
- Check Oracle contract arity and correct ADD_MONTHS return type; pin acceptance
  DDL order and hashes. Add 12 regression tests in `test_q04_completion_review.py`.
- Reopen Q-04: the initial completion audit found 72 wildcard gaps plus EXCEPTION,
  including with target DDL (now three wildcard gaps). The external acceptance test verifies those
  blockers; passing it does not claim complete analysis.
- `scripts/sql_gp.py` masks `WITH (orientation = ROW)` lexically so
  PostgreSQL def_arg grammar does not reject the parse view.
- Wildcard-gap coverage notes are recomputed on expansion; an
  unexpanded wildcard is the only reason the gap stays open.

## [1.0.0] — 2026-09-19

### Added

- **Unified CLI wrapper** (`scripts/wiki_doc.py`): single entry point for all
  subcommands — inventory, plan, validate, gate, lint, publish, query, metrics,
  identity, index, bundle, regression. Each subcommand delegates to the
  corresponding module; existing standalone scripts remain available.
- **Package version** (`scripts/_version.py`): `__version__ = '1.0.0'`,
  accessible via `--version`.
- **Stage journal** (`scripts/stage_journal.py`): append-only JSONL diagnostics
  with event IDs, actual run/page IDs, timestamps and outcomes. CLI decision
  issuance, publication and recovery are instrumented; other stages are available
  through the explicit API. Separate from the transaction recovery journal.
- **Answer templates** (`docs/answer-templates.md`): structured templates for
  generation success, revision, blocked, lint and query responses.
- **Concurrent mode documentation** (`docs/concurrent-mode.md`): explicit
  description of single-process and multi-process behaviour, lock semantics,
  recovery procedures and limitations.
- **Dialect support matrix** (`docs/dialect-support.md`): full matrix of
  PostgreSQL/Greenplum/other dialect support, version gating, unverified gaps.
- **Metrics** (`scripts/metrics.py`): wiki health metrics — source freshness,
  coverage, defects, broken links, decision accuracy, iteration stats.
- **Index and lineage** (`scripts/index.py`, `scripts/query.py`): machine-readable
  catalogue and directed graph for dependency queries.
- **Model extensions**: TRIGGER, INDEX, CONSTRAINT (CREATE/ALTER), GRANT/REVOKE
  with `extension_version: 1` in inventory/facts v2.

### Changed

- `validation_gate.py`: bundle mode now requires manifest, rebuilds inventory
  independently, verifies dialect consistency, checks coverage_notes.
- `SKILL.md`: updated with dialect support reference, metrics section, query
  section, index/lineage integration.
- `references/sql-support.md`: extended with dialect support reference.

### Fixed

- CLI prepare, dry-run and read-only gate checks no longer write a stage journal
  or create the destination wiki. Inapplicable mutation flags are rejected.
- Concurrent journal writes are serialized, flushed and fsynced; crash-truncated
  JSON/UTF-8 tails do not consume the next event or prevent reading valid events.
- Equivalent resolved Windows DOS/UNC paths compare consistently when file
  replacement leaves an extended path prefix; outside-root targets stay rejected.
- Operational instructions are fingerprinted and included in isolated runtimes.
- Response templates and concurrency documentation describe actual API results,
  lock scope, recovery behaviour and journal coverage.
- MERGE version gating: MERGE inside EXECUTE now uses the same version check
  as direct MERGE (previously bypassed at version 14/unknown).
- Migration manifest dialect check: accepts both `postgres` and `postgresql`
  (case-insensitive), matching the SQL inventory behaviour.

### Security

- Gate blocks publication when dialect is unsupported or MERGE version is
  unconfirmed. No bypasses introduced by the unified CLI.

## Implementation history before package versioning

### Added (P0–P2-03)

- Versioned contracts and artifact connectivity (P0-01).
- Check policy catalogue with applicability and criticality (P0-02).
- Independent SQL inventory and validation plan (P0-03).
- Verifiable evidence and bundle binding (P0-04).
- Unified gate with negative admission tests (P0-05).
- Reproducible object identity with collision handling (P1-01).
- Full coverage verification with CommonMark parsing (P1-02).
- Extended independent SQL analysis — AST-based PostgreSQL parser (P1-03).
- Machine-readable expectations and regression harness (P1-04).
- Core, pluggable profile and history separation (P1-05).
- Executable lint (P1-06).
- Safe local publication with recovery (P1-07).
- Index, graph and query (P2-01).
- Health metrics (P2-02).
- Model extensions — triggers, indexes, constraints, access rules (P2-03).
