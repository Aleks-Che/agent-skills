"""Prepare, verify and finalize pinned runs; inspect selected publication provenance."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import uuid

from _version import __version__
from artifact_schema import read_json, load_schemas, validate_schema
from bundle import compute_tool_versions, create_manifest, verify_manifest_hashes
from evidence import sha256_bytes, sha256_file, resolve_reference
from stage_journal import StageJournal
from wiki_store import atomic_json

PACKAGE = Path(__file__).resolve().parents[1]
RUN_CONTEXT_FILE = 'run_context.json'
LIMITATION_FILE = 'limitation.json'


def _runtime_hash(versions):
    return sha256_bytes(json.dumps(versions, sort_keys=True, separators=(',', ':')).encode())


def _hash_tree(root: Path) -> str:
    """Use the existing bundle hashes, with unambiguous file boundaries."""
    return _runtime_hash(compute_tool_versions(root))


def _result(status, *, publication_authorized=False, generation_completed=False, **values):
    return dict(status=status, publication_authorized=publication_authorized,
                generation_completed=generation_completed, **values)


def _print(result):
    print(json.dumps(result, ensure_ascii=True, indent=2))


def _active_package(path):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f'skill_root does not exist: {root}')
    if root != PACKAGE:
        raise ValueError(f'skill_root differs from executing runtime {PACKAGE}; run {root / "scripts/wiki_doc.py"} explicitly')
    return root


def _input_path(value, root):
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Input does not exist inside project_root: {path}')
    return path


def _profile_path(value, project):
    from profiles import CKR, load_profile
    if not value:
        return None
    if value in ('CKR_GP', 'ckr_gp'):
        return CKR
    path = Path(value)
    path = (project / path).resolve() if not path.is_absolute() else path.resolve()
    if path == PACKAGE / 'project-profile.md':
        path = CKR
    elif path.name == 'profile.md':
        path = path.with_suffix('.json')
    load_profile(path)
    return path


def _analysis(manifest, project, subject, dialect, version, profile):
    from sql_extract import extract_inventory
    from ddl import enrich_inventory
    from sql_types import column_catalog
    from validation_plan import generate_plan
    from check_policy import load_policy
    from profiles import detect_profile, CKR
    from identity import page_id
    roots = {'project': project}
    source = resolve_reference(manifest['sql_files'][0], roots)
    context = [resolve_reference(ref, roots) for ref in manifest.get('context_files', [])]
    migration = resolve_reference(manifest['migration_manifest'], roots) if manifest.get('migration_manifest') else None
    data = source.read_bytes()
    if sha256_bytes(data) != manifest['sql_files'][0]['sha256']:
        raise ValueError('SQL bytes changed before analysis')
    inventory = extract_inventory(data.decode('utf-8-sig'), source.relative_to(project).as_posix(),
                                  sha256_bytes(data), dialect=dialect, version=version,
                                  documented_subjects=[subject])
    inventory['run_id'] = manifest['run_id']
    enrich_inventory(inventory, context_files=context, project_root=project, migration_manifest=migration)
    # Preserve unsupported syntax as diagnostics, never as a prepared run.
    if not inventory.get('coverage_notes'):
        column_catalog(inventory, [source], context, project, migration)
    chosen = detect_profile(inventory, profile)
    selected_profile = (profile or CKR) if chosen else None
    plan = generate_plan(inventory, load_policy(), page_id=page_id(inventory['documented_subjects'][0]),
                         profile_active=bool(chosen), profile_path=selected_profile)
    return inventory, plan, selected_profile


def _context_errors(context):
    # Extend preparation metadata only; reuse manifest and evidence contracts.
    text = {'type': 'string', 'minLength': 1}
    schema = {'type': 'object', 'additionalProperties': False, 'properties': {
        'schema_version': {'const': 1}, 'status': {'const': 'prepared'},
        **{k:text for k in ('run_id', 'skill_root', 'project_root', 'wiki_root', 'subject',
                           'dialect', 'version', 'runtime_version', 'runtime_hash')},
        'profile_path': {'type': ['string', 'null']}, 'preparation_manifest': {'type': 'object'}}}
    schema['required'] = list(schema['properties'])
    errors = validate_schema(context, schema, 'run_context')
    if errors:
        return errors
    partial = copy.deepcopy(load_schemas()['manifest'])
    artifacts = partial['properties']['artifacts']
    artifacts['required'] = ['inventory', 'validation_plan']
    artifacts['properties'] = {k:artifacts['properties'][k] for k in artifacts['required']}
    errors.extend(validate_schema(context['preparation_manifest'], partial, 'preparation_manifest'))
    return errors


def _check_snapshot(manifest, run, project, runtime_before, candidate_profile):
    errors = verify_manifest_hashes(manifest, run, roots={'project': project})
    if compute_tool_versions(PACKAGE, profile_path=candidate_profile) != runtime_before:
        errors.append('Runtime changed during preparation/verification')
    if errors:
        raise ValueError('; '.join(errors))


def cmd_prepare(args: argparse.Namespace) -> int:
    run_id = str(uuid.uuid4())
    run = Path(args.output).resolve() if args.output else Path.cwd() / '.wiki-doc-runs' / run_id
    owned = False
    try:
        # Exclusive creation avoids stale successes and concurrent overwrite.
        wiki = Path(args.wiki_root).resolve()
        if run.is_relative_to(wiki):
            raise ValueError('Preparation output must be outside wiki_root')
        run.mkdir(parents=True, exist_ok=False)
        owned = True
        skill = _active_package(args.skill_root)
        project = Path(args.project_root).resolve()
        if not project.is_dir() or not wiki.is_dir():
            raise ValueError('project_root and wiki_root must be existing directories')
        if not args.sql:
            raise ValueError('SQL file is required')
        source = _input_path(args.sql, project)
        context = [_input_path(p, project) for p in args.context or []]
        migration = _input_path(args.migration_manifest, project) if args.migration_manifest else None
        profile = _profile_path(getattr(args, 'profile', None), project)
        from profiles import CKR
        candidate_profile = profile or CKR
        runtime_before = compute_tool_versions(skill, profile_path=candidate_profile)
        dialect = args.dialect.lower()
        if dialect == 'postgresql':
            dialect = 'postgres'
        if dialect not in ('postgres', 'greenplum'):
            raise ValueError(f'Unsupported dialect: {args.dialect}')
        version = args.version or ('unknown' if dialect == 'greenplum' else '15')
        if migration:
            metadata = read_json(migration)
            if metadata.get('dialect', '').lower().replace('postgresql', 'postgres') != dialect or metadata.get('version') != version:
                raise ValueError('Migration dialect/version differs from selected SQL state')
        # Ordinary manifest builder pins SQL, context and all ordered migrations.
        before = create_manifest(run_id=run_id, page_id='preparation', sql_files=[source],
                                 context_files=context, migration_manifest=migration,
                                 artifacts_dir=run, project_dir=project, tool_versions=runtime_before)
        inventory, plan, profile = _analysis(before, project, args.subject, dialect, version, profile)
        for name, artifact in (('inventory', inventory), ('validation_plan', plan)):
            atomic_json(run / (name + '.json'), artifact)
        if inventory.get('coverage_notes'):
            raise ValueError('analysis_gap: ' + '; '.join(n['reason'] for n in inventory['coverage_notes']))
        _check_snapshot(before, run, project, runtime_before, candidate_profile)
        versions = compute_tool_versions(skill, profile_path=profile)
        manifest = create_manifest(run_id=run_id, page_id=plan['page_id'], sql_files=[source],
                                   context_files=context, migration_manifest=migration,
                                   artifacts_dir=run, project_dir=project, tool_versions=versions)
        prepared = dict(schema_version=1, status='prepared', run_id=run_id, skill_root=str(skill),
                        project_root=str(project), wiki_root=str(wiki), subject=inventory['documented_subjects'][0],
                        dialect=dialect, version=version, runtime_version=__version__,
                        runtime_hash=_runtime_hash(versions), profile_path=str(profile) if profile else None,
                        preparation_manifest=manifest)
        errors = _context_errors(prepared)
        if errors:
            raise ValueError('; '.join(errors))
        _check_snapshot(before, run, project, runtime_before, candidate_profile)
        _check_snapshot(manifest, run, project, runtime_before, candidate_profile)
        # Commit marker is last: interrupted writes cannot masquerade as prepared.
        atomic_json(run / RUN_CONTEXT_FILE, prepared)
        try:
            journal = StageJournal(run)
            for stage in ('inventory', 'plan'):
                journal.record(stage, run_id=run_id, page_id=plan['page_id'], outcome='ok')
        except (OSError, ValueError):
            pass  # Run-local diagnostics are not an admission/recovery mechanism.
        _print(_result('prepared', run_id=run_id, run_dir=str(run), runtime_hash=prepared['runtime_hash'],
                       sql_sha256=before['sql_files'][0]['sha256']))
        return 0
    except Exception as exc:
        limitation = _result('limitation', schema_version=1, run_id=run_id, run_dir=str(run),
                            reason=str(exc), diagnostics=[str(exc)], diagnostic_paths=[])
        if owned:
            limitation['diagnostic_paths'] = [str(run / name) for name in
                (LIMITATION_FILE, 'inventory.json', 'validation_plan.json')
                if name == LIMITATION_FILE or (run / name).is_file()]
            try:
                atomic_json(run / LIMITATION_FILE, limitation)
            except OSError as write_error:
                limitation['diagnostics'].append(f'Cannot save limitation: {write_error}')
        _print(limitation)
        return 1


def verify_prepared(run):
    """Read-only admission shared by verify and finalize; return the checked context."""
    run = Path(run).resolve()
    context_path = run / RUN_CONTEXT_FILE
    if (run / LIMITATION_FILE).exists():
        raise ValueError('Run has a limitation; prepare a new run after resolving it')
    snapshots = {}
    context = read_json(context_path, snapshot_hashes=snapshots)
    errors = _context_errors(context)
    if errors:
        raise ValueError('; '.join(errors))
    run_id = context['run_id']
    skill = _active_package(context['skill_root'])
    project, wiki = Path(context['project_root']), Path(context['wiki_root'])
    if not project.is_absolute() or not wiki.is_absolute() or not project.is_dir() or not wiki.is_dir():
        raise ValueError('Stored roots must be absolute existing directories')
    if run.is_relative_to(wiki.resolve()):
        raise ValueError('Preparation output must be outside wiki_root')
    profile = Path(context['profile_path']) if context['profile_path'] else None
    if profile is not None and not profile.is_absolute():
        raise ValueError('Profile path must be absolute')
    versions = compute_tool_versions(skill, profile_path=profile)
    manifest = context['preparation_manifest']
    if versions != manifest['tool_versions'] or _runtime_hash(versions) != context['runtime_hash'] or context['runtime_version'] != __version__:
        raise ValueError('runtime hash/version mismatch')
    if run_id != manifest['run_id'] or manifest.get('documented_subjects') != [context['subject']]:
        raise ValueError('Run ID or subject differs from preparation manifest')
    if len(manifest['sql_files']) != 1:
        raise ValueError('A prepared run must select one SQL source')
    for name, ref in manifest['artifacts'].items():
        if ref.get('root', 'run') != 'run' or ref['path'] != name + '.json':
            raise ValueError('Preparation artifact must name its fixed run-local file')
    from profiles import CKR
    candidate_profile = profile or CKR
    runtime_before = compute_tool_versions(skill, profile_path=candidate_profile)
    _check_snapshot(manifest, run, project, runtime_before, candidate_profile)
    saved = {name:read_json(run / ref['path'], snapshot_hashes=snapshots)
             for name, ref in manifest['artifacts'].items()}
    for name, value in saved.items():
        errors = validate_schema(value, load_schemas()[name], name)
        if errors:
            raise ValueError('; '.join(errors))
        ref = manifest['artifacts'][name]
        if snapshots[(run / ref['path']).resolve()] != ref['sha256'] or value['run_id'] != run_id:
            raise ValueError(f'{name}: changed bytes or foreign run_id')
    inventory, plan, selected = _analysis(manifest, project, context['subject'], context['dialect'], context['version'], profile)
    if inventory.get('coverage_notes'):
        raise ValueError('Rebuilt inventory has analysis gaps')
    if selected != profile or inventory != saved['inventory'] or plan != saved['validation_plan'] or manifest['page_id'] != plan['page_id']:
        raise ValueError('Prepared inventory/plan/identity differs from independently rebuilt obligations')
    _check_snapshot(manifest, run, project, runtime_before, candidate_profile)
    if any(sha256_file(path) != digest for path, digest in snapshots.items()):
        raise ValueError('Preparation artifacts changed during verification')
    return context


def cmd_verify(args: argparse.Namespace) -> int:
    run = Path(args.run_dir).resolve()
    try:
        context = verify_prepared(run)
        _print(_result('verified', run_id=context['run_id'], run_dir=str(run), errors=[]))
        return 0
    except Exception as exc:
        _print(_result('invalid', run_id=None, errors=[str(exc)]))
        return 1


def _bound_bundle(run, context):
    """Require writer/validator output to extend, never replace, preparation."""
    snapshots = {}
    manifest = read_json(run / 'manifest.json', snapshot_hashes=snapshots)
    errors = validate_schema(manifest, load_schemas()['manifest'], 'manifest')
    if errors:
        raise ValueError('; '.join(errors))
    before = context['preparation_manifest']
    for name in ('run_id', 'page_id', 'documented_subjects', 'tool_versions',
                 'sql_files', 'context_files', 'migration_manifest'):
        if manifest.get(name) != before.get(name):
            raise ValueError(f'Full manifest {name} differs from preparation')
    for name, ref in before['artifacts'].items():
        if manifest['artifacts'][name] != ref:
            raise ValueError(f'Full manifest replaced prepared {name}')
    errors = verify_manifest_hashes(manifest, run, roots={'project': Path(context['project_root'])})
    if errors:
        raise ValueError('; '.join(errors))
    if any(sha256_file(path) != digest for path, digest in snapshots.items()):
        raise ValueError('Full manifest changed during admission')
    return manifest


def cmd_finalize(args: argparse.Namespace) -> int:
    """Verify authored artifacts, prepare publication, run gate and publish.

    Never invokes the deterministic reference writer or manufactures validation.
    A changed merged draft requires a fresh validator report before continuing.
    """
    run = Path(args.run_dir).resolve()
    run_id = None
    try:
        context = verify_prepared(run)
        context_hash = sha256_file(run / RUN_CONTEXT_FILE)
        run_id = context['run_id']
        project, wiki = Path(context['project_root']), Path(context['wiki_root'])
        profile = Path(context['profile_path']) if context['profile_path'] else None
        manifest = _bound_bundle(run, context)
        from validation_gate import evaluate_bundle
        from publish import prepare as prepare_publication, publish
        from page_provenance import check_provenance

        def admit():
            current = verify_prepared(run)
            if current != context or sha256_file(run / RUN_CONTEXT_FILE) != context_hash:
                raise ValueError('Prepared context changed during finalization')
            return _bound_bundle(run, context)

        def gate():
            checked = evaluate_bundle(run, roots={'project': project, 'wiki': wiki},
                                      profile_path=profile, write_decision=True)
            if checked.get('decision') != 'ready' or not checked.get('publication_authorized'):
                raise ValueError('Full gate refused: ' + '; '.join(checked.get('errors', []))
                                 + f" (decision={checked.get('decision')})")
            return checked

        # Missing or invalid writer/validator artifacts fail before publication preparation.
        gate()
        if not manifest.get('publication_plan'):
            prepared = prepare_publication(run, wiki)
            if prepared['draft_changed']:
                _print(_result('blocked', run_id=run_id, run_dir=str(run), validation_required=True,
                    errors=['Merged draft changed; validate the merged page and rebuild the full manifest']))
                return 1
            # Only bind the publication snapshot; never refresh writer evidence hashes here.
            admit()
            manifest['publication_plan'] = dict(path='publication.json', sha256=sha256_file(run / 'publication.json'))
            atomic_json(run / 'manifest.json', manifest)
        admit()
        checked = gate()
        admit()
        published = publish(run, wiki, project_root=project, profile_path=profile)
        if not published.get('published') or published.get('page_id') != manifest['page_id']:
            raise ValueError('Publisher did not confirm the selected page')
        provenance = check_provenance(wiki, [manifest['page_id']], project_root=project, expected_run_id=run_id)
        if not provenance['valid']:
            raise ValueError('Published provenance invalid: ' + '; '.join(provenance['errors']))
        admit()
        # Reuse the gate decision and publisher transaction as authorities, not stage labels.
        _print(_result('completed', run_id=run_id, run_dir=str(run), gate_decision=checked['decision'],
                       publication_authorized=True, generation_completed=True, page_id=manifest['page_id'],
                       idempotent=published.get('idempotent', False)))
        return 0
    except Exception as exc:
        _print(_result('blocked', run_id=run_id, run_dir=str(run), errors=[str(exc)]))
        return 1


def cmd_provenance(args: argparse.Namespace) -> int:
    """Strict, read-only admission of explicitly selected generated pages."""
    from page_provenance import check_provenance
    result = check_provenance(args.wiki_root, getattr(args, 'page', None),
                              project_root=getattr(args, 'project_root', None))
    _print(_result('verified' if result['valid'] else 'invalid',
                   pages=result['pages'], errors=result['errors']))
    return 0 if result['valid'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='Prepare independent obligations outside the published wiki')
    for name in ('skill-root', 'project-root', 'wiki-root', 'subject', 'sql'):
        prep.add_argument('--' + name, required=True)
    prep.add_argument('--context', nargs='*', default=[], help='DDL paths relative to project-root or absolute')
    prep.add_argument('--dialect', default='postgres')
    prep.add_argument('--version')
    prep.add_argument('--migration-manifest')
    prep.add_argument('--profile', help='Explicit profile JSON/Markdown or CKR_GP')
    prep.add_argument('--output', help='New run directory (default: cwd/.wiki-doc-runs/<run_id>)')
    verify = sub.add_parser('verify', help='Verify preparation; never authorize publication')
    verify.add_argument('--run-dir', required=True)
    finalize = sub.add_parser('finalize', help='Verify authored run, gate and publish; does not generate writer/validator output')
    finalize.add_argument('--run-dir', required=True)
    prov = sub.add_parser('provenance', help='Verify selected pages against publisher metadata, archive and committed transaction')
    prov.add_argument('--wiki-root', required=True)
    prov.add_argument('--page', nargs='+', required=True, help='Selected wiki-relative page paths; legacy/audit files are not selected implicitly')
    prov.add_argument('--project-root', help='Override current SQL project root for source freshness and links')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        return cmd_prepare(args)
    elif args.command == 'verify':
        return cmd_verify(args)
    elif args.command == 'finalize':
        return cmd_finalize(args)
    elif args.command == 'provenance':
        return cmd_provenance(args)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
