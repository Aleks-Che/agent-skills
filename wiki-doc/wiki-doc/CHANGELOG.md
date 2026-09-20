# Changelog

All notable changes to the wiki-doc package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
