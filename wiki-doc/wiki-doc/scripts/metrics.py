"""Aggregate health metrics for a local wiki.

Computes page freshness, source freshness, decision distribution, check
coverage/defects/unknowns, broken-link counts, and publication iteration
statistics from the committed wiki state without modifying any files.

Usage:
  python scripts/metrics.py <wiki_root> [--json] [--mutation-results FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from artifact_schema import read_json, ArtifactInputError
from index import build_catalogue, IndexError as IndexError_
from query import query_outdated
from lint import lint
from wiki_store import inside, load_metadata, hash_file


class MetricsError(ValueError):
    """Invalid or inaccessible wiki state."""


# ── page metrics ──────────────────────────────────────────────────────────────

def _page_metrics(index):
    pages = index.get('pages', [])
    by_status = Counter(p['status'] for p in pages)
    by_state = Counter(p.get('page_state') for p in pages if p['status'] == 'managed')
    return {
        'total': len(pages),
        'managed': by_status.get('managed', 0),
        'legacy': by_status.get('legacy', 0),
        'current': by_state.get('current', 0),
        'changed': by_state.get('changed', 0),
        'missing': by_state.get('missing', 0),
    }


# ── source metrics ────────────────────────────────────────────────────────────

def _source_metrics(wiki_root, index):
    outdated = query_outdated(wiki_root, index=index)
    by_reason = Counter(o['reason'] for o in outdated)
    total_tracked = sum(
        len(p.get('tracked_inputs', p.get('source_files', [])))
        for p in index.get('pages', []) if p.get('status') == 'managed'
    )
    fresh = total_tracked - len(outdated)
    return {
        'total_tracked': total_tracked,
        'outdated': len(outdated),
        'fresh': fresh,
        'freshness_ratio': fresh / total_tracked if total_tracked else None,
        'outdated_pages': len({o['page_id'] for o in outdated}),
        'by_reason': dict(by_reason),
    }


# ── check / decision metrics ─────────────────────────────────────────────────

def _load_decisions(wiki_root):
    root = Path(wiki_root).resolve()
    records = load_metadata(root)
    decisions = []
    for record in records:
        archive = inside(root, record['bundle'])
        decision_path = archive / 'decision.json'
        if not decision_path.is_file():
            continue
        try:
            decision = read_json(decision_path)
            decisions.append(decision)
        except (ArtifactInputError, OSError):
            continue
    return decisions


def _check_metrics(decisions):
    by_decision = Counter(d.get('decision') for d in decisions)
    total = len(decisions)
    ready_count = by_decision.get('ready', 0)
    timestamps = []
    blocking_defect_total = 0
    blocking_inconclusive_total = 0
    for d in decisions:
        if d.get('decision') == 'ready':
            ts = d.get('timestamp') or d.get('generated_at') or d.get('issued_at')
            if ts:
                timestamps.append(ts)
            blocking_defect_total += len(d.get('blocking_defects', []))
            blocking_inconclusive_total += len(d.get('blocking_inconclusive', []))
    first_ready = min(timestamps) if timestamps else None
    return {
        'total_decisions': total,
        'ready': ready_count,
        'revise': by_decision.get('revise', 0),
        'blocked': by_decision.get('blocked', 0),
        'ready_ratio': ready_count / total if total else None,
        'first_ready_at': first_ready,
        'blocking_defect_total': blocking_defect_total,
        'blocking_inconclusive_total': blocking_inconclusive_total,
    }


# ── coverage / defect / unknown metrics ──────────────────────────────────────

def _coverage_metrics(decisions):
    """Aggregate resolved/defect/unknown check counts from archived decisions.

    Every ratio keeps an explicit numerator and denominator so a skipped or
    absent run is never counted as a success.
    """
    ok = defect = unknown = not_applicable = total_checks = 0
    for d in decisions:
        metrics = d.get('metrics') or {}
        ok += metrics.get('ok_count', 0)
        defect += metrics.get('defect_count', 0)
        unknown += metrics.get('inconclusive_count', 0)
        not_applicable += metrics.get('not_applicable_count', 0)
        total_checks += metrics.get('total_checks', 0)
    resolved = ok + defect
    applicable = resolved + unknown
    return {
        'total_checks': total_checks,
        'resolved': resolved,
        'ok': ok,
        'defect': defect,
        'unknown': unknown,
        'not_applicable': not_applicable,
        'coverage_ratio': resolved / applicable if applicable else None,
        'accuracy_ratio': ok / resolved if resolved else None,
    }


# ── broken link metrics ───────────────────────────────────────────────────────

def _broken_link_metrics(lint_result):
    link_issues = [i for i in lint_result.get('issues', [])
                   if i['code'] in ('link_invalid', 'index_missing', 'anchor_duplicate')]
    files_affected = len({i['file'] for i in link_issues})
    return {
        'total': len(link_issues),
        'files_affected': files_affected,
        'details': [{'file': i['file'], 'code': i['code'], 'message': i['message']}
                    for i in link_issues],
    }


# ── decision accuracy from mutations ──────────────────────────────────────────

def _normalize_mutation_results(data):
    """Accept a raw list or an acceptance file with nested mutation results."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get('results'), list):
            return data['results']
        mutations = data.get('mutations')
        if isinstance(mutations, dict) and isinstance(mutations.get('results'), list):
            return mutations['results']
        return [data]
    return []


def _is_erroneous_ready(record):
    if 'false_ready' in record:
        return bool(record['false_ready'])
    return record.get('decision') == 'ready'


def _decision_accuracy(mutation_results=None):
    if mutation_results is None:
        return {'erroneous_ready': 0, 'total_mutations_tested': 0,
                'erroneous_ready_ratio': None}
    results = _normalize_mutation_results(mutation_results)
    total = len(results)
    erroneous = sum(1 for m in results if _is_erroneous_ready(m))
    return {
        'erroneous_ready': erroneous,
        'total_mutations_tested': total,
        'erroneous_ready_ratio': erroneous / total if total else None,
    }


# ── iteration metrics ─────────────────────────────────────────────────────────

def _iteration_metrics(wiki_root):
    root = Path(wiki_root).resolve()
    journal_dir = inside(root, '.wiki-doc/journal')
    if not journal_dir.is_dir():
        return {'total_publishes': 0, 'unique_pages_published': 0,
                'avg_iterations_per_page': None}

    # Build run_id → page_id mapping from metadata
    run_to_page = {}
    for record in load_metadata(root):
        run_to_page[record['run_id']] = record['page_id']

    journals = list(journal_dir.glob('*.json'))
    committed = 0
    page_ids = set()
    for path in journals:
        try:
            data = read_json(path)
        except (ArtifactInputError, OSError):
            continue
        if data.get('state') not in ('committed', 'rolled_back'):
            continue
        if data.get('kind') == 'catalogue':
            continue  # catalogue rebuilds don't count as page publishes
        committed += 1
        run_id = data.get('run_id')
        page_id = data.get('page_id') or run_to_page.get(run_id)
        if page_id:
            page_ids.add(page_id)
    unique = len(page_ids)
    return {
        'total_publishes': committed,
        'unique_pages_published': unique,
        'avg_iterations_per_page': committed / unique if unique else None,
    }


# ── public API ────────────────────────────────────────────────────────────────

def compute_metrics(wiki_root, *, mutation_results=None, project_root=None):
    """Compute all wiki health metrics.

    Args:
        wiki_root: path to the wiki root directory
        mutation_results: optional list of result dicts, or an acceptance
                          file containing nested mutation results, from
                          regression mutation testing
        project_root: optional project root for lint source checking

    Returns:
        dict matching metrics.schema.json
    """
    root = Path(wiki_root).resolve()
    if not root.is_dir():
        raise MetricsError(f'Wiki directory does not exist: {root}')

    index, _ = build_catalogue(root)
    lint_result = lint(root, project_root=project_root)
    decisions = _load_decisions(root)

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'pages': _page_metrics(index),
        'sources': _source_metrics(root, index),
        'checks': _check_metrics(decisions),
        'coverage': _coverage_metrics(decisions),
        'broken_links': _broken_link_metrics(lint_result),
        'decision_accuracy': _decision_accuracy(mutation_results),
        'iteration_stats': _iteration_metrics(root),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wiki', help='Wiki root directory')
    parser.add_argument('--json', action='store_true', help='Emit JSON output')
    parser.add_argument('--project-root', help='Project root for source checking')
    parser.add_argument('--mutation-results',
                        help='JSON list or acceptance file with mutation results')
    args = parser.parse_args(argv)

    try:
        mutations = None
        if args.mutation_results:
            mutations = read_json(args.mutation_results)
        result = compute_metrics(args.wiki, mutation_results=mutations,
                                 project_root=args.project_root)
    except (MetricsError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        pm, sm, cm = result['pages'], result['sources'], result['checks']
        bl, it, cv = result['broken_links'], result['iteration_stats'], result['coverage']
        print(f"Pages: {pm['total']} total ({pm['managed']} managed, {pm['legacy']} legacy)")
        print(f"  current={pm['current']} changed={pm['changed']} missing={pm['missing']}")
        ratio = sm['freshness_ratio']
        print(f"Sources: {sm['fresh']}/{sm['total_tracked']} fresh"
              + (f" ({ratio:.0%})" if ratio is not None else "")
              + f", {sm['outdated_pages']} pages outdated")
        if sm['by_reason']:
            for reason, count in sm['by_reason'].items():
                print(f"  {reason}: {count}")
        print(f"Decisions: {cm['total_decisions']} total"
              + (f", {cm['ready']} ready ({cm['ready_ratio']:.0%})" if cm['ready_ratio'] is not None else ""))
        if cv['total_checks']:
            cov = cv['coverage_ratio']
            acc = cv['accuracy_ratio']
            print(f"Coverage: {cv['resolved']}/{cv['resolved'] + cv['unknown']} resolved"
                  + (f" ({cov:.0%})" if cov is not None else "")
                  + f", {cv['defect']} defects, {cv['unknown']} unknown"
                  + (f", accuracy {acc:.0%}" if acc is not None else ""))
        print(f"Broken links: {bl['total']} in {bl['files_affected']} files")
        print(f"Publishes: {it['total_publishes']} ({it['unique_pages_published']} unique pages)")
        da = result['decision_accuracy']
        if da['total_mutations_tested']:
            print(f"Mutation accuracy: {da['erroneous_ready']}/{da['total_mutations_tested']} erroneous ready")
    return 0


if __name__ == '__main__':
    sys.exit(main())
