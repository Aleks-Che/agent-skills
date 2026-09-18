"""Catalogue and lineage derived from integrity-checked published snapshots."""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from artifact_schema import read_json, validate_schema, validate_artifacts
from bundle import verify_manifest_hashes
from identity import canonical_key, ObjectDescriptor, IdentityError
from sql_syntax import split_top_level
from wiki_store import WikiConflict, inside, hash_file, json_bytes, load_metadata

PACKAGE = Path(__file__).resolve().parents[1]
INDEX_FILENAME, LINEAGE_FILENAME = 'index.json', 'lineage.json'
INDEX_PATH, LINEAGE_PATH = '.wiki-doc/index.json', '.wiki-doc/lineage.json'
SCOPED_OBJECT_KINDS = {'cte', 'temp_table'}
LEGACY_KEY_RE = re.compile(
    r'(?:canonical_key|Canonical key|Канонический ключ|Ключ)\s*[:|]\**\s*`?([^`\n|]+)', re.I)


class IndexError(ValueError):
    """Invalid or changing publication snapshot; no partial graph is returned."""


def _load_index_schemas():
    return {name: read_json(PACKAGE / 'schemas' / f'{name}.schema.json') for name in ('index', 'lineage')}


def _visible_pages(root):
    return sorted(p for p in root.rglob('*.md')
                  if not {'.wiki-doc', '.tmp'} & set(p.relative_to(root).parts))


def _state(root):
    """Optimistic read barrier: Query creates neither files nor locks."""
    paths = _visible_pages(root)
    for folder in ('.wiki-doc/pages', '.wiki-doc/journal'):
        paths += sorted(inside(root, folder).glob('*.json'))
    state = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        state[relative] = hash_file(inside(root, relative))
        if relative.startswith('.wiki-doc/journal/'):
            journal = read_json(path)
            if journal.get('state') not in ('committed', 'rolled_back'):
                raise IndexError('Publication is incomplete; run publish.py recover before Query')
    return state


def _archive(root, record, snapshots, preview=None):
    archive = Path(preview['run']) if preview else inside(root, record['bundle'])
    source_root = Path(preview['project']) if preview else archive
    artifacts = {name: read_json(inside(archive, name + '.json'), snapshot_hashes=snapshots)
                 for name in ('facts', 'manifest', 'decision')}
    errors = validate_artifacts(artifacts, required=('facts', 'manifest', 'decision'))
    if errors:
        raise IndexError(f"{record['page_id']}: invalid archived artifacts: {errors}")
    facts, manifest, decision = (artifacts[k] for k in ('facts', 'manifest', 'decision'))
    errors = verify_manifest_hashes(manifest, archive, roots={'project': source_root})
    if errors:
        raise IndexError(f"{record['page_id']}: archive integrity: {'; '.join(errors)}")
    for ref in list(manifest['artifacts'].values()) + ([manifest['publication_plan']] if manifest.get('publication_plan') else []):
        snapshots[inside(archive, ref['path'])] = ref['sha256']
    for ref in manifest['sql_files'] + manifest.get('context_files', []) + [
            manifest[k] for k in ('migration_manifest', 'identity_registry') if manifest.get(k)]:
        snapshots[inside(source_root, ref['path'])] = ref['sha256']
    documented = [o for o in facts['objects'] if o['id'] in facts['documented_object_ids']]
    if len(documented) != 1:
        raise IndexError('Published page must have exactly one documented identity')
    main = documented[0]
    if (main.get('canonical_key') != record['canonical_key'] or main.get('page_id') != record['page_id'] or
            manifest['page_id'] != record['page_id'] or manifest['run_id'] != record['run_id'] or
            record['source_files'] != manifest['sql_files'] + manifest.get('context_files', []) or
            manifest['artifacts']['draft']['sha256'] != record['page_sha256'] or
            decision['decision'] != 'ready' or decision['manifest_sha256'] != hash_file(archive / 'manifest.json') or
            decision['validation_sha256'] != manifest['artifacts']['validation']['sha256']):
        raise IndexError(f"{record['page_id']}: metadata/decision differs from its archived bundle")
    # Historical integrity is independent of the currently installed tooling and live SQL.
    return facts, main, manifest


def _key(obj):
    if obj.get('kind') in SCOPED_OBJECT_KINDS:
        return None
    if obj.get('canonical_key'):
        return obj['canonical_key']
    if not obj.get('schema') or not obj.get('name'):
        return None
    kind, args = obj['kind'], None
    if kind in ('function', 'procedure'):
        signature = obj.get('signature')
        if not signature or not signature.startswith('(') or not signature.endswith(')'):
            return None
        args = split_top_level(signature[1:-1]) if signature[1:-1].strip() else []
    # facts identifiers have already been normalized by the extractor.
    quoted = lambda s: '"' + s.replace('"', '""') + '"'
    try:
        return canonical_key(ObjectDescriptor(kind=kind, schema=quoted(obj['schema']),
                                              name=quoted(obj['name']), arg_types=args))
    except IdentityError:
        return None


def _target(obj, owner, nodes):
    kind = obj['kind']
    if kind in SCOPED_OBJECT_KINDS:
        prefix = '@cte:' if kind == 'cte' else '@temp:'
        local = obj.get('canonical_key') or obj['id']
        return prefix + quote(owner, safe='') + ':' + quote(local, safe=''), 'scoped', []
    key = _key(obj)
    candidates = sorted(k for k, n in nodes.items() if n['status'] == 'managed' and
                        n.get('schema') == obj.get('schema') and n.get('name') == obj.get('name') and
                        (n['kind'] == kind or {n['kind'], kind} <= {'table', 'view', 'materialized_view', 'ctas'}))
    if key in nodes and nodes[key]['status'] == 'managed':
        return key, 'published', []
    if kind in ('table', 'view', 'materialized_view', 'ctas') and len(candidates) == 1:
        return candidates[0], 'published', []
    if key:
        return key, 'undocumented', []
    label = json.dumps([kind, obj.get('schema'), obj.get('name'), obj.get('signature')], separators=(',', ':'))
    # Missing routine signature never silently chooses one overload.
    return '@external:' + quote(label, safe=''), 'unresolved', candidates


def _graph(bundles, pages, generated_at):
    nodes = {}
    for record, facts, main, manifest in bundles:
        key = record['canonical_key']
        if key in nodes:
            raise IndexError(f'Duplicate published canonical key: {key}')
        nodes[key] = dict(page_id=record['page_id'], object_id=main['id'], kind=main['kind'],
                          schema=main.get('schema'), name=main['name'], signature=main.get('signature'),
                          physical=main.get('physical', True), run_id=record['run_id'], status='managed')
    for page in pages:
        if page['status'] == 'legacy':
            key = '@legacy:' + quote(page['page_id'], safe='')
            nodes[key] = dict(page_id=page['page_id'], kind='legacy', physical=False, status='legacy')
    edges, gaps = [], []
    for record, facts, main, manifest in bundles:
        owner = record['canonical_key']
        objects = {o['id']: o for o in facts['objects']}
        source_refs = {r['path']: r['sha256'] for r in manifest['sql_files'] + manifest.get('context_files', [])}
        for op in facts['operations']:
            evidence = []
            for ref in op['source_refs']:
                if source_refs.get(ref['path']) != ref['sha256']:
                    raise IndexError(f"{record['page_id']}: evidence is not bound to archived input")
                evidence.append({**ref, 'path': record['bundle'] + '/' + ref['path'], 'root': 'wiki'})
            base = dict(page_id=record['page_id'], run_id=record['run_id'], operation_id=op['id'], evidence=evidence)
            for relation, field in (('read', 'reads'), ('write', 'writes'), ('call', 'calls')):
                for oid in op.get(field, []):
                    target, resolution, candidates = _target(objects[oid], owner, nodes)
                    edges.append(dict(base, **{'from': owner, 'to': target, 'kind': relation,
                                               'resolution': resolution, 'candidates': candidates}))
            if op.get('dynamic'):
                gaps.append(dict(base, reason='Runtime identifiers are unresolved', dynamic=op['dynamic']))
    edges.sort(key=lambda e: (e['from'], e['to'], e['kind'], e['operation_id']))
    return dict(schema_version=1, generated_at=generated_at, nodes=dict(sorted(nodes.items())), edges=edges, gaps=gaps)


def build_catalogue(wiki_root, *, records=None, previews=None):
    """Build a consistent pair; records is a publisher-only proposed metadata overlay."""
    root = Path(wiki_root).resolve()
    if not root.is_dir() and records is None:
        raise IndexError(f'Wiki directory does not exist: {root}')
    before = _state(root) if records is None else None
    records = load_metadata(root) if records is None else records
    snapshots, bundles, pages = {}, [], []
    for record in records:
        facts, main, manifest = _archive(root, record, snapshots, (previews or {}).get(record['page_id']))
        entry = {k: record.get(k) for k in ('page_id', 'canonical_key', 'run_id', 'bundle', 'page_sha256',
                                           'source_files', 'profile', 'project_root', 'policy')}
        entry.update(status='managed', object_id=main['id'], kind=main['kind'], schema=main.get('schema'),
                     name=main['name'], signature=main.get('signature'),
                     page_state='current' if hash_file(inside(root, record['page_id'])) == record['page_sha256'] else
                     ('changed' if inside(root, record['page_id']).exists() else 'missing'))
        entry['tracked_inputs'] = manifest['sql_files'] + manifest.get('context_files', []) + [
            manifest[k] for k in ('migration_manifest', 'identity_registry') if manifest.get(k)]
        pages.append(entry)
        bundles.append((record, facts, main, manifest))
    managed_ids = {p['page_id'] for p in pages}
    for path in _visible_pages(root):
        pid = path.relative_to(root).as_posix()
        if pid in managed_ids or pid == 'index.md':
            continue
        content = inside(root, pid).read_text(encoding='utf-8-sig')
        match = LEGACY_KEY_RE.search(content)
        pages.append(dict(page_id=pid, status='legacy', canonical_key=match[1].strip() if match else None))
    stamp = datetime.now(timezone.utc).isoformat()
    index = dict(schema_version=1, generated_at=stamp, pages=sorted(pages, key=lambda p: p['page_id']))
    lineage = _graph(bundles, pages, stamp)
    schemas = _load_index_schemas()
    for name, artifact in (('index', index), ('lineage', lineage)):
        errors = validate_schema(artifact, schemas[name], name)
        if errors:
            raise IndexError('; '.join(errors))
    if any(hash_file(path) != sha for path, sha in snapshots.items()) or before is not None and _state(root) != before:
        raise IndexError('Wiki changed during Query; retry against a stable publication')
    return index, lineage


def build_index(wiki_root):
    return build_catalogue(wiki_root)[0]


def build_lineage(wiki_root):
    return build_catalogue(wiki_root)[1]


def rebuild_after_publish(wiki_root):
    """Explicit rebuild uses the same lock and recoverable transaction as publication."""
    from publish import index_lock, _recover_locked
    root = Path(wiki_root).resolve()
    if not root.is_dir():
        raise IndexError(f'Wiki directory does not exist: {root}')
    with index_lock(root):
        recovered = _recover_locked(root)
        if any(r['state'] == 'conflict' for r in recovered):
            raise WikiConflict('Unresolved recovery conflict')
        return rebuild_locked(root)


def rebuild_locked(root):
    from publish import _commit_files
    index, lineage = build_catalogue(root)
    _commit_files(root, str(uuid.uuid4()), ((inside(root, INDEX_PATH), json_bytes(index)),
                  (inside(root, LINEAGE_PATH), json_bytes(lineage))), kind='catalogue')
    return dict(index_path=INDEX_PATH, lineage_path=LINEAGE_PATH, pages=len(index['pages']),
                nodes=len(lineage['nodes']), edges=len(lineage['edges']))


def write_index(wiki_root, index=None):
    if index is not None:
        raise IndexError('Caller-provided index is not a published snapshot; rebuild both artifacts')
    rebuild_after_publish(wiki_root)
    return inside(Path(wiki_root).resolve(), INDEX_PATH)


def write_lineage(wiki_root, lineage=None):
    if lineage is not None:
        raise IndexError('Caller-provided lineage is not a published snapshot; rebuild both artifacts')
    rebuild_after_publish(wiki_root)
    return inside(Path(wiki_root).resolve(), LINEAGE_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wiki')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--lineage', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.write:
            result = rebuild_after_publish(args.wiki)
        else:
            index, lineage = build_catalogue(args.wiki)
            result = (lineage if args.lineage else index) if args.json else dict(
                pages=len(index['pages']), nodes=len(lineage['nodes']), edges=len(lineage['edges']))
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
