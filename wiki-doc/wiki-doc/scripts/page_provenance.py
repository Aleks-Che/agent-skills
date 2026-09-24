"""Read-only provenance admission for explicitly selected generated wiki pages."""
from pathlib import Path

from artifact_schema import read_json, validate_schema
from bundle import verify_manifest_hashes
from evidence import sha256_file, resolve_reference
from validation_gate import evaluate_bundle
from wiki_store import inside, metadata_path, indexed_pages


def _metadata_schema():
    text = {'type': 'string', 'minLength': 1}
    nullable_path = {'type': ['string', 'null'], 'minLength': 1}
    props = dict(schema_version={'const': 1}, page_id=text, canonical_key=text,
                 page_sha256={'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                 bundle=text, run_id={'type': 'string', 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},
                 profile=nullable_path, source_files={'type': 'array', 'minItems': 1, 'items': {'type': 'object'}})
    return dict(type='object', required=list(props), properties={**props, 'project_root': text, 'policy': nullable_path})


def _check_page(wiki, page_id, project_root, expected_run_id):
    if not isinstance(page_id, str) or not page_id.endswith('.md') or page_id == 'index.md':
        raise ValueError('Select a generated Markdown page, not the wiki index')
    page = inside(wiki, page_id)
    relative = page.relative_to(wiki).as_posix()
    if relative != page_id or relative.startswith(('.wiki-doc/', '.tmp/')):
        raise ValueError('Select a canonical wiki-relative page path outside service directories')
    snapshots = {}
    path = metadata_path(wiki, page_id)
    meta = read_json(path, snapshot_hashes=snapshots)
    errors = validate_schema(meta, _metadata_schema(), 'publication metadata')
    if errors:
        raise ValueError('; '.join(errors))
    run_id = meta['run_id']
    if meta['page_id'] != page_id or meta['bundle'] != '.wiki-doc/runs/' + run_id:
        raise ValueError('Metadata page/bundle identity differs from selected page')
    if expected_run_id is not None and run_id != expected_run_id:
        raise ValueError('Published run_id differs from prepared run')
    snapshots[page] = sha256_file(page)
    if snapshots[page] != meta['page_sha256']:
        raise ValueError('Published page bytes differ from metadata')
    archive = inside(wiki, meta['bundle'])
    manifest = read_json(archive / 'manifest.json', snapshot_hashes=snapshots)
    project = Path(project_root or meta.get('project_root') or archive).resolve()
    checked = evaluate_bundle(archive, roots={'wiki': wiki, 'link_project': project},
                              profile_path=meta['profile'], policy_path=meta.get('policy'))
    if checked.get('decision') != 'ready' or not checked.get('publication_authorized'):
        raise ValueError('Archived full gate refused: ' + '; '.join(checked.get('errors', [])))
    facts = read_json(archive / 'facts.json', snapshot_hashes=snapshots)
    main = next(o for o in facts['objects'] if o['id'] in facts['documented_object_ids'])
    if (manifest['run_id'] != run_id or manifest['page_id'] != page_id or
        main['canonical_key'] != meta['canonical_key'] or
        manifest['artifacts']['draft']['sha256'] != meta['page_sha256'] or
        meta['source_files'] != manifest['sql_files'] + manifest.get('context_files', [])):
        raise ValueError('Metadata differs from checked archive identity, page or SQL/DDL inputs')
    # A ready archive alone is insufficient: actual publication must have committed.
    from publish import _validate_journal, _check_plan
    plan = _check_plan(archive, wiki)
    if plan['run_id'] != run_id or plan['page_id'] != page_id:
        raise ValueError('Publication snapshot belongs to another run/page')
    journal_path = inside(wiki, '.wiki-doc/journal/' + run_id + '.json')
    journal = read_json(journal_path, snapshot_hashes=snapshots)
    _validate_journal(wiki, journal_path, journal)
    if journal.get('kind', 'publication') != 'publication' or journal['state'] != 'committed':
        raise ValueError('Publisher transaction is not committed')
    written = {e['path']: e['new_sha256'] for e in journal['files']}
    for target in (page, path):
        if written.get(target.relative_to(wiki).as_posix()) != snapshots[target]:
            raise ValueError('Page/metadata bytes differ from committed publisher transaction')
    index = inside(wiki, 'index.md')
    index_data = index.read_bytes()
    from evidence import sha256_bytes
    snapshots[index] = sha256_bytes(index_data)
    if page_id not in indexed_pages(index_data.decode('utf-8-sig')):
        raise ValueError('Published page missing from wiki index')
    # Archive evidence is historical; check current selected project inputs separately.
    for ref in manifest['sql_files'] + manifest.get('context_files', []) + [
            manifest[k] for k in ('migration_manifest', 'identity_registry') if manifest.get(k)]:
        source = resolve_reference(ref, {'project': project})
        if sha256_file(source) != ref['sha256']:
            raise ValueError('Current project input differs from published source: ' + ref['path'])
        snapshots[source] = ref['sha256']
    errors = verify_manifest_hashes(manifest, archive, roots={'project': archive})
    if errors or any(sha256_file(p) != digest for p, digest in snapshots.items()):
        raise ValueError('Provenance inputs changed during verification: ' + '; '.join(errors))


def check_provenance(wiki_root, page_ids, *, project_root=None, expected_run_id=None):
    """Missing, legacy, stale or uncommitted selected pages never verify."""
    wiki = Path(wiki_root).resolve()
    errors, checked = [], []
    if not wiki.is_dir():
        errors.append('wiki_root must be an existing directory')
    elif not isinstance(page_ids, (list, tuple)) or not page_ids:
        errors.append('Explicit nonempty --page selection is required')
    else:
        for page_id in dict.fromkeys(page_ids):
            try:
                _check_page(wiki, page_id, project_root, expected_run_id)
                checked.append(page_id)
            except Exception as exc:
                errors.append(f'{page_id}: {exc}')
    return dict(valid=not errors, pages=checked, errors=errors)
