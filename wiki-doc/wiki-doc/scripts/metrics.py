"""Read-only documentation metrics for the current published wiki.

Snapshot ratios use all current managed pages. Validation history covers the
current published run of each page; repeated publication is not a correction.
The safety ratio covers only the supplied, complete mutation report.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema import read_json, validate_schema
from wiki_store import inside, load_metadata

PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PACKAGE / 'schemas' / 'metrics.schema.json'


def _fraction(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 2) if denominator else None


def _decision(wiki_root, record):
    decision = read_json(inside(wiki_root, record['bundle']) / 'decision.json')
    if not isinstance(decision, dict):
        raise ValueError(f"{record['page_id']}: invalid decision")
    return decision


def _compute_outdated_pages(wiki_root: Path, index_pages: list[dict]) -> dict[str, Any]:
    """Count pages, not individual changed input files."""
    from query import query_outdated
    by_page = {}
    for issue in query_outdated(wiki_root, index={'pages': index_pages}):
        by_page.setdefault(issue['page_id'], {k: issue[k] for k in
                           ('page_id', 'reason', 'path', 'source_missing')})
    for entry in index_pages:
        if entry.get('status') == 'managed' and not entry.get('tracked_inputs', entry.get('source_files')):
            by_page[entry['page_id']] = dict(page_id=entry['page_id'], reason='source_inventory_missing')
    pages = list(by_page.values())
    return dict(total=len(pages), pages=pages)


def _compute_fresh_sources(wiki_root: Path, index_pages: list[dict]) -> dict[str, Any]:
    stale = _compute_outdated_pages(wiki_root, index_pages)['pages']
    denominator = sum(p.get('status') == 'managed' for p in index_pages)
    fresh = denominator - len(stale)
    return dict(numerator=fresh, denominator=denominator, percent=_fraction(fresh, denominator),
                stale_pages=[{k: v for k, v in p.items() if k != 'source_missing'} for p in stale])


def _compute_full_coverage(wiki_root: Path, records: list[dict]) -> dict[str, Any]:
    """Recheck facts against current Markdown, not the gate's check-resolution ratio."""
    from coverage_gate import validate_coverage
    from check_policy import load_policy
    incomplete = []
    for record in records:
        archive = inside(wiki_root, record['bundle'])
        page = inside(wiki_root, record['page_id'])
        if not page.is_file():
            incomplete.append(dict(page_id=record['page_id'], coverage_percent=0))
            continue
        result = validate_coverage(
            read_json(archive / 'coverage.json'), page.read_text(encoding='utf-8-sig'),
            read_json(archive / 'facts.json'), read_json(archive / 'validation_plan.json'),
            load_policy(record.get('policy')),
            wiki_root=wiki_root, link_roots=[Path(record['project_root'])] if record.get('project_root') else [])
        percent = result.to_dict()['coverage_percent']
        if not result.valid or percent != 100:
            incomplete.append(dict(page_id=record['page_id'], coverage_percent=percent))
    ok = len(records) - len(incomplete)
    return dict(numerator=ok, denominator=len(records), percent=_fraction(ok, len(records)),
                incomplete_pages=incomplete)


def _iteration_observations(wiki_root, records):
    from validation_history import validate_history
    observations, unmeasured = [], []
    for record in records:
        decision = _decision(wiki_root, record)
        history = decision.get('validation_history')
        if history is None:
            unmeasured.append(record['page_id'])
            continue
        validate_history(history, record['run_id'], record['page_id'])
        attempts = history['attempts']
        if attempts[-1]['decision'] != decision.get('decision'):
            raise ValueError(f"{record['page_id']}: history does not end with the published decision")
        if not history['complete']:
            unmeasured.append(record['page_id'])
            continue
        # Successful rechecks (e.g. after prepare) do not count as corrections.
        failures = sum(a['decision'] != 'ready' for a in attempts)
        observations.append(dict(page_id=record['page_id'], iterations=failures + 1,
                                 first_pass=attempts[0]['decision'] == 'ready',
                                 start=attempts[0]['recorded_at'], end=attempts[-1]['recorded_at']))
    return observations, unmeasured


def _compute_first_pass_success(wiki_root: Path, records: list[dict]) -> dict[str, Any]:
    observations, unmeasured = _iteration_observations(wiki_root, records)
    numerator = sum(o['first_pass'] for o in observations)
    measured = bool(records) and not unmeasured
    return dict(numerator=numerator, denominator=len(records),
                percent=_fraction(numerator, len(records)) if measured else None,
                measured=measured, measured_pages=len(observations), unmeasured_pages=unmeasured,
                multi_pass_pages=[dict(page_id=o['page_id'], iterations=o['iterations'])
                                  for o in observations if not o['first_pass']])


def _compute_average_iterations(wiki_root: Path, records: list[dict]) -> float | None:
    observations, unmeasured = _iteration_observations(wiki_root, records)
    if not observations or unmeasured:
        return None
    return round(sum(o['iterations'] for o in observations) / len(observations), 2)


def _compute_blocking_defects(wiki_root: Path, records: list[dict]) -> dict[str, Any]:
    by_page = []
    for record in records:
        decision = _decision(wiki_root, record)
        defects = decision.get('blocking_defects')
        if not isinstance(defects, list):
            raise ValueError(f"{record['page_id']}: blocking defects were not recorded")
        if defects:
            by_page.append(dict(page_id=record['page_id'], count=len(defects), defect_ids=defects))
    return dict(total=sum(p['count'] for p in by_page), by_page=by_page)


def _compute_open_unknowns(wiki_root: Path, records: list[dict]) -> dict[str, Any]:
    """Include documented data unknowns as well as unresolved validation checks."""
    by_page = []
    for record in records:
        archive = inside(wiki_root, record['bundle'])
        facts = read_json(archive / 'facts.json')
        validation = read_json(archive / 'validation.json')
        unknowns = (['fact:' + u['id'] for u in facts['unknowns']] +
                    ['check:' + c['id'] for c in validation['checks'] if c['status'] == 'inconclusive'])
        if unknowns:
            by_page.append(dict(page_id=record['page_id'], count=len(unknowns), unknown_ids=unknowns))
    return dict(total=sum(p['count'] for p in by_page), by_page=by_page)


def _compute_broken_links(wiki_root: Path) -> dict[str, Any]:
    from lint import lint
    result = lint(str(wiki_root))
    by_page = {}
    for issue in result['issues']:
        if issue['code'] == 'link_invalid':
            page_id = Path(issue['file']).relative_to(wiki_root).as_posix()
            by_page.setdefault(page_id, []).append(issue['message'])
    return dict(total=sum(map(len, by_page.values())),
                by_page=[dict(page_id=pid, count=len(links), links=links) for pid, links in by_page.items()])


def _compute_erroneous_ready(regression_report: dict[str, Any] | None) -> dict[str, Any]:
    """Accept mutation reports only; skipped or failed evaluations are unmeasured."""
    if regression_report is None:
        return dict(numerator=0, denominator=0, percent=None, measured=False, cases=[])
    if not isinstance(regression_report, dict) or not isinstance(regression_report.get('results'), list):
        raise ValueError('Expected a mutation report with a results array')
    results = regression_report['results']
    cases, evaluated = [], 0
    seen = set()
    for row in results:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
                                               for k in ('case', 'subject', 'mutation')):
            raise ValueError('Expected labeled mutation results, not a positive regression report')
        if row.get('mode') is not None and not isinstance(row['mode'], str):
            raise ValueError('Mutation mode must be a string')
        identity = tuple(row.get(k) for k in ('case', 'subject', 'mutation', 'mode'))
        if identity in seen:
            raise ValueError('Duplicate mutation result')
        seen.add(identity)
        if row.get('skipped') or row.get('error') or row.get('evaluated') is False:
            continue
        # input_error is a completed gate verdict too: a deliberately invalid
        # artifact can correctly be rejected by schema validation.
        if row.get('decision') not in ('ready', 'revise', 'blocked') or type(row.get('false_ready')) is not bool:
            raise ValueError('Mutation result is missing an evaluated decision/false_ready flag')
        if row['false_ready'] != (row['decision'] == 'ready'):
            raise ValueError('Mutation decision disagrees with false_ready')
        evaluated += 1
        if row['false_ready']:
            cases.append(dict(case_id=row['case'], page_id=row['subject'],
                              mutation=row['mutation'], decision=row['decision']))
    if 'mutations' in regression_report and regression_report['mutations'] != len(results):
        raise ValueError('Mutation report count differs from its results')
    measured = bool(results) and evaluated == len(results)
    return dict(numerator=len(cases), denominator=len(results),
                percent=_fraction(len(cases), len(results)) if measured else None,
                measured=measured, cases=cases)


def compute_metrics(wiki_root: Path | str, *, regression_report: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(wiki_root).resolve()
    if not root.is_dir():
        raise ValueError(f'Wiki directory does not exist: {root}')
    from index import build_index, _state
    # Archive integrity and in-flight publications must be checked, never bypassed.
    before = _state(root)
    index_pages = build_index(root)['pages']
    records = load_metadata(root)
    observations, _ = _iteration_observations(root, records)
    legacy_count = sum(p['status'] == 'legacy' for p in index_pages)
    result = dict(
        schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        period=dict(wiki='current_published_snapshot', validation='current_published_runs',
                    validation_start=min((o['start'] for o in observations), default=None),
                    validation_end=max((o['end'] for o in observations), default=None),
                    regression='supplied_mutation_report'),
        pages=dict(managed=len(records), legacy=legacy_count, total=len(records) + legacy_count),
        fresh_sources=_compute_fresh_sources(root, index_pages),
        full_coverage=_compute_full_coverage(root, records),
        first_pass_success=_compute_first_pass_success(root, records),
        blocking_defects=_compute_blocking_defects(root, records),
        open_unknowns=_compute_open_unknowns(root, records),
        outdated_pages=_compute_outdated_pages(root, index_pages),
        broken_links=_compute_broken_links(root),
        erroneous_ready=_compute_erroneous_ready(regression_report),
        average_iterations=_compute_average_iterations(root, records))
    # Recheck archives as well as wiki files before returning a combined snapshot.
    if _state(root) != before or build_index(root)['pages'] != index_pages:
        raise ValueError('Wiki changed during metrics computation; retry on a stable snapshot')
    errors = validate_schema(result, read_json(SCHEMA_PATH), 'metrics')
    if errors:
        raise ValueError('Metrics result violates schemas/metrics.schema.json: ' + '; '.join(errors))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wiki', help='Wiki root directory')
    parser.add_argument('--json', action='store_true', help='Emit full JSON output')
    parser.add_argument('--regression-report', type=Path, help='mutation-report.json from regression_mutations.py')
    args = parser.parse_args(argv)
    try:
        report = read_json(args.regression_report) if args.regression_report else None
        result = compute_metrics(args.wiki, regression_report=report)
        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            pages = result['pages']
            print(f"Pages: {pages['total']} ({pages['managed']} managed, {pages['legacy']} legacy)")
            for key, label in [('fresh_sources', 'Fresh sources'), ('full_coverage', 'Full coverage'),
                               ('first_pass_success', 'First-pass success'), ('erroneous_ready', 'Erroneous ready')]:
                metric = result[key]
                value = (f"{metric['numerator']}/{metric['denominator']} ({metric['percent']}%)"
                         if metric['percent'] is not None else 'not measured')
                print(f'{label}: {value}')
            for key, label in [('blocking_defects', 'Blocking defects'), ('open_unknowns', 'Open unknowns'),
                               ('outdated_pages', 'Outdated pages'), ('broken_links', 'Broken links')]:
                print(f"{label}: {result[key]['total']}")
            print(f"Average iterations: {result['average_iterations'] if result['average_iterations'] is not None else 'not measured'}")
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
