# Changelog

All notable changes to the wiki-doc skill are documented in this file.

## [1.0.0] — 2026-09-19

### Added — P0: Gate foundation

- **P0-01**: Versioned artifact contracts (8 JSON Schemas), cross-artifact
  link validation, unique fact IDs, run_id/page_id consistency.
- **P0-02**: Check policy engine (`check_policy.py`) — rule catalogue, object
  groups, applicability conditions and blocking rules.
- **P0-03**: Independent SQL inventory (`sql_extract.py`) and validation
  plan generator (`validation_plan.py`).
- **P0-04**: Verifiable evidence (`evidence.py`) and bundle manifest
  (`bundle.py`) with SHA-256 hashes and root containment.
- **P0-05**: Unified validation gate (`validation_gate.py`) — bundle mode,
  schema validation, inventory/plan re-verification, coverage check.

### Added — P1: Critical paths mechanized

- **P1-01**: Reproducible object identity (`identity.py`) — canonical key,
  page slug, SHA-256 suffix, collision detection.
- **P1-02**: Full Markdown coverage check (`coverage_gate.py`) — CommonMark
  parsing, section/fragment validation, link resolution.
- **P1-03**: Extended SQL analysis (`sql_ast.py`) — pglast-based AST for
  PostgreSQL/PLpgSQL, MERGE, CTE, dynamic EXECUTE, migration reconstruction.
- **P1-04**: Machine-readable expectations and regression (`run_regression.py`,
  `regression_mutations.py`) — initially 11 scenarios, 14 subjects, 3 repeats,
  28 mutations; extended in P2-03 to 13 scenarios, 17 subjects and 38 mutations.
- **P1-05**: Pluggable profile (`profiles/ckr_gp/`), separated history,
  link fixes.
- **P1-06**: Executable lint (`lint.py`) — read-only wiki check for links,
  provenance, coverage, profile consistency.
- **P1-07**: Safe local publication (`publish.py`) — verified bytes, manual
  blocks, dry-run, inter-process lock, journal, recovery.

### Added — P2: Extensions

- **P2-01**: Machine index and dependency graph (`index.py`, `query.py`) —
  `index.json`, `lineage.json`, page/dependencies/consumers/affected/outdated.
- **P2-02**: Quality metrics (`metrics.py`) — 9 metrics: fresh_sources,
  full_coverage, first_pass_success, blocking_defects, open_unknowns,
  outdated_pages, broken_links, erroneous_ready, average_iterations.
- **P2-03**: Model extensions — triggers, indexes, constraints, GRANT/REVOKE
  in AST; examples 12 and 13. Regex extraction alone does not establish coverage.
- **P2-04**: Dialect support matrix (`docs/dialect-support.md`) — honest
  PostgreSQL/Greenplum/other breakdown, test-derived status.
- **P2-05**: Operational wrapper (`wiki_doc.py`), VERSION, CHANGELOG,
  opt-in stage journal, agent response templates, operational documentation.

### Fixed during P2-05 acceptance

- Inventory and plan retain the core CLI's project paths, run identity, context,
  migration order, selected subjects and profile obligations, with consistent errors.
- Prepare/recover and publication with explicit profile/policy are exposed by the
  wrapper; migration identity and existing page registries retain their core semantics.
- Stage events record start, completion and exit code without authorizing publication.
- Isolated adapters receive VERSION, CHANGELOG and the linked operational references.
- Response templates distinguish gate readiness, dry-run and actual publication.
