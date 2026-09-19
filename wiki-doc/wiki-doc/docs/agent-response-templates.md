# Agent Response Templates

Templates for wiki-doc agent responses. Use these patterns to format
user-facing output after each operation. Adapt the language to the user. Fill fields
only from actual command results/artifacts; omit unavailable values or mark them
unknown. An exit code of zero for inventory/plan is not a publication decision.
Only a successful publisher result permits the "Published" template; a gate
decision of ready or a dry-run alone does not. Keep source-code findings separate
from documentation errors. These templates do not replace the command's full evidence.

## Page Published Successfully

```
Published: <page_id>
  File: <wiki>/<page_slug>.md
  Subject: <canonical_key>
  SQL: <sql_path> (sha256:<short_hash>)
  Run: <run_id>
  Decision: ready
  Required-check coverage: <decision.metrics.coverage_percent>% [if measured]
```

## Validation Failed — Revise

```
Validation: REVISE — <page_id>
  Errors:
    - <error_1>
    - <error_2>
  Remaining checks: <n>/<total>
  Action: Fix listed errors and re-run validation.
```

## Validation Failed — Blocked

```
Validation: BLOCKED — <page_id>
  Reason: <blocking_reason>
  Coverage notes:
    - <note_1>
    - <note_2>
  Action: Resolve blocking issues. Unsupported syntax requires
  parser extension before publication is possible.
```

## Lint Report

```
Lint: <wiki_dir>
  Errors: <n>
  Warnings: <n>
  Pages: <total> (<managed> managed, <legacy> legacy)

  [if errors]
  Errors:
    - <file>: <message>
  [endif]

  [if warnings]
  Warnings:
    - <file>: <message>
  [endif]
```

## Metrics Report

```
Wiki Metrics — <wiki_dir>
  fresh_sources:        <n>/<total>
  full_coverage:        <n>/<total>
  first_pass_success:   <n>/<total> (or N/A)
  blocking_defects:     <n>
  open_unknowns:        <n>
  outdated_pages:       <n>
  broken_links:         <n>
  erroneous_ready:      <n>/<total> (or N/A)
  average_iterations:   <float> (or N/A)
```

## Query Results

```
Query: <mode> — <target>

  [page mode]
  Page: <page_id>
  Subject: <canonical_key>
  SQL: <path>
  Sources: <list>

  [dependencies mode]
  Dependencies of <canonical_key>:
    - <dep_1>
    - <dep_2>

  [consumers mode]
  Consumers of <canonical_key>:
    - <consumer_1>
    - <consumer_2>

  [affected mode]
  Affected by changes to <canonical_key>:
    - <page_1> (direct)
    - <page_2> (indirect)

  [outdated mode]
  Outdated pages:
    - <page_id>: <reason>, <path>; expected/current SHA-256: <hashes>
```

## Dry-Run Publish

```
Dry-run: <page_id>
  Proposed changes: <paths from changes: page, index.md, metadata, index.json, lineage.json>
  Published: no
  [if refused: exact error from publisher]
```

## Recovery

```
Recovery: <wiki_dir>
  Found: <n> unfinished journal entries
  Recovered: <n>
  Conflicts: <n>
  [details per entry]
```

## Error — Invalid Input

```
Error: <message>
  Expected: <expected>
  Got: <actual>
  Hint: <suggestion>
```

## Error — Missing Artifact

```
Error: Missing artifact — <artifact_name>
  Run: <run_dir>
  Required by: <operation>
  Action: Run the preceding steps to generate <artifact_name>.
```

## Version

```
wiki-doc <version>
```

The version command returns only the package version. Add Python/platform/parser
versions only if they were inspected separately. Do not invent a source-change
timestamp from Query's hash comparison or turn unmeasured metrics into zero.
