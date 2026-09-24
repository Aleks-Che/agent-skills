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
- **Q-05 verifiable run preparation**: scripts/run_prepare.py with prepare,
  verify, finalize, provenance and resume subcommands. Explicit
  skill_root/project_root/wiki_root/subject, UUID, runtime hash, inventory
  and validation_plan before writer. Limitation with diagnostics on failure.
  Runtime/SQL change detection. Resume after interruption with hash verification.
  Writer/validator execution and full-chain recovery stay manual; resume
  reports `needs_action` for missing author artifacts.
- **Q-06 large object writer instructions**: doc-writer.md section on
  dividing work by operations/branches and KPI families, context slices with
  exact source_ref, structured data for counters, common KPI formulas,
  exact column types, unknown abbreviation handling, and reads → operation
  → writes graph.
- **Q-07 mutation matrix (partial)**: D01–D11 mutation annotations
  across all Q cases; tests for mutation detection, review-label/assertion
  linking, annotation fields and target resolution. Fresh bundles: 40/40
  executed mutations detected, 0 false-ready, 0 untested (`valid: true`).
  These annotations do not include semantic D12 acceptance. The six
  `D12ProseMutationTests` exercise bounded version/date/example checks;
  date membership does not prove a window's rationale. Business explanations,
  graph meaning and equivalent-prose acceptance remain open.
  q09 preserves every assignment as a visible operation SQL query; null column
  summaries require independently distinct variants and a planned unknown.
- **Bounded content claims**: `scripts/content_claims.py` uses CommonMark and
  SQL AST for marked examples, including Russian labels and fenced SQL.
  It checks routine schema/signatures, argument counts/names and supported
  literal types; literal contents are not interpreted as calls. Date checks
  consider operators; explicit version claims consider the product. Target
  version is not compatibility evidence, and explicit caveats are preserved.
  Contract: `references/content-claims.md`; 25 review tests include all four
  entry points and a fresh-hash bundle whose report contains only `ok`.

### Changed

- scripts/run_prepare.py: new module for verifiable run preparation and
  verification with runtime hash binding and provenance checks.
- scripts/wiki_doc.py: added prepare, verify-run, finalize,
  provenance and resume subcommands.
- doc-writer.md: added large object handling section.
- examples/expected/q06/page_assertions.json: separate D11 mutations for each
  INSERT target after the former D12 labels were corrected.
- docs/answer-templates.md: reports for the Q-05 preparation stages and the
  legacy/provenance distinction.
- references/regression.md: acceptance/CI provenance command pointer.
- tests/test_q05_run_prepare.py, tests/test_q05_finalize.py,
  tests/test_q05_resume.py, tests/test_q07_mutations.py: new test modules.
- scripts/content_claims.py: new module invoked alongside
  `page_claims.check_page_claims` in `validation_gate.py`,
  `build_bundle.finish`, `run_regression.check_run` and `lint.lint`.
  tests/test_q07_mutations.D12ProseMutationTests: 6 new tests for
  prose-level D12 acceptance.
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

- q09 follow-up: close the false-ready bypass where null/omitted column
  expressions disabled type checking. Single mappings remain mandatory;
  multi-variant summaries retain every INSERT/UPDATE/MERGE query and SQL
  binding. Equal expression text with different source types is not merged.
  New regressions check fresh-hash mutations, missing/swapped queries and
  independently required unknowns. Existing artifact fields are reused;
  affected bundles must be rebuilt from SQL under the current runtime hash.
- Regression runner: accept expected `blocked`/`revise` without publication,
  check the expected diagnostics, and keep mutation controls strictly ready.
  Reject unexecuted repair counts and empty suites instead of reporting completion.
- Q-05 answer templates: separate prepare/verify fields and document blocked
  finalization, including failures after publication and optional diagnostic paths.
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
