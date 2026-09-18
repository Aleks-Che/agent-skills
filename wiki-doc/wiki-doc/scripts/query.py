"""Read-only Query over the wiki index and lineage graph.

Supports reverse dependency lookups, transitive impact, and outdated page
detection without modifying any wiki state.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from artifact_schema import read_json, ArtifactInputError
from index import (
    build_index, build_lineage, build_catalogue,
)
from wiki_store import WikiConflict, inside, hash_file


class QueryError(ValueError):
    """Query lookup failure."""


def load_index(wiki_root: Path | str) -> dict[str, Any]:
    root = Path(wiki_root).resolve()
    if not root.is_dir():
        raise QueryError(f'Wiki directory does not exist: {root}')
    return build_index(root)


def load_lineage(wiki_root: Path | str) -> dict[str, Any]:
    root = Path(wiki_root).resolve()
    if not root.is_dir():
        raise QueryError(f'Wiki directory does not exist: {root}')
    return build_lineage(root)


def _page_id_to_canonical_keys(index: dict[str, Any], page_id: str) -> set[str]:
    keys: set[str] = set()
    for entry in index.get('pages', []):
        if entry['page_id'] == page_id:
            key = entry.get('canonical_key')
            if key:
                keys.add(key)
    return keys


def _edges_out(lineage: dict[str, Any], from_key: str) -> list[dict[str, Any]]:
    return [e for e in lineage.get('edges', []) if e['from'] == from_key]


def _edges_in(lineage: dict[str, Any], to_key: str) -> list[dict[str, Any]]:
    return [e for e in lineage.get('edges', []) if e['to'] == to_key]


def query_dependencies(lineage: dict[str, Any], from_key: str) -> dict[str, Any]:
    out = _edges_out(lineage, from_key)
    return {
        'reads': sorted({e['to'] for e in out if e['kind'] == 'read'}),
        'writes': sorted({e['to'] for e in out if e['kind'] == 'write'}),
        'calls': sorted({e['to'] for e in out if e['kind'] == 'call'}),
        'edges': out,
        'gaps': [g for g in lineage.get('gaps', []) if g['page_id'] == lineage['nodes'].get(from_key, {}).get('page_id')],
    }


def query_consumers(lineage: dict[str, Any], to_key: str) -> dict[str, Any]:
    incoming = _edges_in(lineage, to_key)
    return {
        'read_by': sorted({e['from'] for e in incoming if e['kind'] == 'read'}),
        'written_by': sorted({e['from'] for e in incoming if e['kind'] == 'write'}),
        'called_by': sorted({e['from'] for e in incoming if e['kind'] == 'call'}),
        'edges': incoming,
        'possible_consumers': [e for e in lineage.get('edges', []) if to_key in e.get('candidates', [])],
    }


def _transverse_closure(lineage: dict[str, Any], seed: str,
                         direction: str, max_depth: int) -> list[dict[str, Any]]:
    """BFS in `direction` ('downstream' = consumers, 'upstream' = dependencies)."""
    if max_depth < 0:
        raise QueryError('max_depth must be nonnegative')
    visited = {(seed, False)}
    queue = deque([(seed, 0, False)])
    results: list[dict[str, Any]] = []
    while queue:
        current, depth, possible = queue.popleft()
        if depth >= max_depth:
            continue
        neighbors = []
        for edge in lineage.get('edges', []):
            if direction == 'upstream' and edge['from'] == current:
                neighbors.append((edge['to'], edge, False))
            if direction == 'downstream':
                if edge['to'] == current:
                    neighbors.append((edge['from'], edge, False))
                elif current in edge.get('candidates', []):
                    neighbors.append((edge['from'], edge, True))
                # A changed producer can alter its output and all downstream readers.
                if edge['from'] == current and edge['kind'] == 'write':
                    neighbors.append((edge['to'], edge, False))
        for neighbor, edge, uncertain in neighbors:
            possibility = possible or uncertain
            if neighbor == seed or (neighbor, possibility) in visited:
                continue
            visited.add((neighbor, possibility))
            results.append({
                'node': neighbor,
                'page_id': lineage['nodes'].get(neighbor, {}).get('page_id'),
                'via': current,
                'relation': edge['kind'],
                'depth': depth + 1,
                'possible': possibility,
                'edge': edge,
            })
            queue.append((neighbor, depth + 1, possibility))
    return sorted(results, key=lambda r: (r['depth'], r['node']))


def query_affected(lineage: dict[str, Any], seed_key: str, *,
                   max_depth: int = 32) -> list[dict[str, Any]]:
    return _transverse_closure(lineage, seed_key, 'downstream', max_depth)


def query_dependencies_chain(lineage: dict[str, Any], seed_key: str, *,
                             max_depth: int = 32) -> list[dict[str, Any]]:
    return _transverse_closure(lineage, seed_key, 'upstream', max_depth)


def _current_sha_for_source(project_root: Path | None, ref: dict[str, Any]) -> str | None:
    if project_root is None:
        return None
    source = (project_root / ref['path']).resolve()
    if not source.is_relative_to(project_root.resolve()):
        return None
    if not source.exists():
        return None
    return hash_file(source)


def query_outdated(wiki_root: Path | str, *, index=None) -> list[dict[str, Any]]:
    """Find managed pages whose committed source files no longer match disk bytes."""
    root = Path(wiki_root).resolve()
    index = load_index(root) if index is None else index
    outdated: list[dict[str, Any]] = []
    for entry in index.get('pages', []):
        if entry.get('status') != 'managed':
            continue
        project_root_raw = entry.get('project_root')
        project_root = Path(project_root_raw).resolve() if project_root_raw else None
        for ref in entry.get('tracked_inputs', entry.get('source_files', [])):
            try:
                current = _current_sha_for_source(project_root, ref)
                reason = 'project_root_unavailable' if project_root is None or not project_root.is_dir() else ('source_missing' if current is None else 'source_changed')
            except OSError:
                current, reason = None, 'source_unreadable'
            if current is None or current != ref['sha256']:
                outdated.append({
                    'page_id': entry['page_id'],
                    'canonical_key': entry.get('canonical_key'),
                    'path': ref['path'],
                    'expected_sha256': ref['sha256'],
                    'current_sha256': current,
                    'source_missing': current is None,
                    'reason': reason,
                })
    return outdated


def query_page(wiki_root: Path | str, page_id: str, *, max_depth=32) -> dict[str, Any]:
    """Return every dependency, consumer, and outdated-source flag for one page."""
    root = Path(wiki_root).resolve()
    index, lineage = build_catalogue(root)
    page = next((p for p in index['pages'] if p['page_id'] == page_id), None)
    if page is None:
        raise QueryError(f'Page not found in index: {page_id}')
    keys = _page_id_to_canonical_keys(index, page_id)
    if page['status'] == 'legacy':
        keys = set()
    dependencies: dict[str, dict[str, list[str]]] = {}
    consumers: dict[str, dict[str, list[str]]] = {}
    affected: list[dict[str, Any]] = []
    for key in sorted(keys):
        dependencies[key] = query_dependencies(lineage, key)
        consumers[key] = query_consumers(lineage, key)
        affected.extend(query_affected(lineage, key, max_depth=max_depth))
    outdated = [o for o in query_outdated(root, index=index) if o['page_id'] == page_id]
    return {
        'page_id': page_id,
        'page': page,
        'status': page['status'],
        'lineage_verified': page['status'] == 'managed',
        'canonical_keys': sorted(keys),
        'dependencies': dependencies,
        'consumers': consumers,
        'affected': sorted(affected, key=lambda r: (r['depth'], r['node'])),
        'outdated_sources': outdated,
        'max_depth': max_depth,
        'limitations': _limitations(lineage),
    }


def _limitations(lineage):
    return dict(legacy_pages=[n['page_id'] for n in lineage['nodes'].values() if n.get('status') == 'legacy'],
                unresolved_edges=[e for e in lineage['edges'] if e.get('resolution') == 'unresolved'],
                dynamic_gaps=lineage.get('gaps', []),
                evidence_scope='Archived operation anchors; inspect the linked SQL snapshot for full statement context')


def _known(lineage, key):
    if key not in lineage['nodes'] and not any(key in (e['from'], e['to']) or key in e.get('candidates', []) for e in lineage['edges']):
        raise QueryError(f'Object not found in published lineage: {key}')


def _print_human(result: Any) -> None:
    # JSON is also the lossless readable report: never omit consumers, gaps or evidence.
    print(json.dumps(result, ensure_ascii=True, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wiki', help='Wiki root directory')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--page', help='Full Query for one page (page_id)')
    mode.add_argument('--dependencies', help='Show what this canonical_key reads/writes/calls')
    mode.add_argument('--consumers', help='Show who reads/writes/calls this canonical_key')
    mode.add_argument('--affected', help='Show transitive cascade downstream from this key')
    mode.add_argument('--outdated', action='store_true', help='List pages whose sources drifted from disk')
    parser.add_argument('--json', action='store_true', help='Emit JSON output')
    parser.add_argument('--max-depth', type=int, default=32, help='BFS depth limit for cascades')
    args = parser.parse_args(argv)
    try:
        if args.max_depth < 0:
            raise QueryError('max_depth must be nonnegative')
        if args.page:
            result = query_page(args.wiki, args.page, max_depth=args.max_depth)
        elif args.dependencies:
            lineage = load_lineage(args.wiki)
            _known(lineage, args.dependencies)
            result = dict(canonical_key=args.dependencies, **query_dependencies(lineage, args.dependencies), limitations=_limitations(lineage))
        elif args.consumers:
            lineage = load_lineage(args.wiki)
            _known(lineage, args.consumers)
            result = dict(canonical_key=args.consumers, **query_consumers(lineage, args.consumers), limitations=_limitations(lineage))
        elif args.affected:
            lineage = load_lineage(args.wiki)
            _known(lineage, args.affected)
            result = dict(canonical_key=args.affected, affected=query_affected(lineage, args.affected, max_depth=args.max_depth),
                          max_depth=args.max_depth, limitations=_limitations(lineage))
        else:
            result = query_outdated(args.wiki)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
