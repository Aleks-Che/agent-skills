"""Bundle: create and verify run manifests for wiki-doc.

A manifest records all inputs and artifacts for a documentation run,
with their SHA-256 hashes. It does NOT include its own hash (no cyclic
hashing) and does NOT include decision.json (decision references manifest).

Structure:
    schema_version: 2
    run_id: UUID
    created_at: ISO datetime
    page_id: documented object identifier
    sql_files: [{path, sha256}, ...]
    context_files: [{path, sha256}, ...]
    migration_manifest: {path, sha256} or null
    artifacts: {facts, inventory, validation_plan, coverage, draft, validation}
    tool_versions: {scripts_sha256, skill_md_sha256, template_sha256, policy_sha256, profile_sha256?}
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evidence import sha256_file, sha256_bytes, resolve_reference, EvidenceError
from artifact_schema import read_json, ArtifactInputError


# ---- Artifact names (excluding manifest itself and decision) ----

ARTIFACT_NAMES = ('facts', 'inventory', 'validation_plan', 'coverage', 'draft', 'validation')

TOOL_VERSION_KEYS = ('scripts_sha256', 'skill_md_sha256', 'template_sha256', 'policy_sha256')
TOOL_VERSION_OPTIONAL = ('profile_sha256',)


# ---- Manifest creation ----

def _file_ref(path: Path, base: Path | None = None) -> dict[str, str]:
    """Create {path, sha256} for a file.

    If base is given, path is stored relative to base.
    """
    p = Path(path).resolve()
    if base:
        try:
            p = p.relative_to(Path(base).resolve())
        except ValueError:
            raise BundleError(f'{p} is outside root {base}; specify the project root')
    return {'path': p.as_posix(), 'sha256': sha256_file(Path(path))}


def _artifact_ref(path: Path, base: Path | None = None) -> dict[str, str]:
    """Create {path, sha256} for an artifact file."""
    return _file_ref(path, base)


def compute_tool_versions(
    package_dir: Path,
    *,
    profile_path: Path | None = None,
    policy_path: Path | None = None,
) -> dict[str, str]:
    """Compute SHA-256 hashes for tooling files.

    Args:
        package_dir: wiki-doc package root (contains scripts/, schemas/, references/)
        profile_path: optional profile.md file

    Returns:
        dict with scripts_sha256, skill_md_sha256, template_sha256, policy_sha256,
        and optionally profile_sha256
    """
    package_dir = Path(package_dir)
    def tree_hash(files):
        # Include paths and individual hashes so file boundaries cannot collide.
        records = [(f.relative_to(package_dir).as_posix(), sha256_file(f)) for f in sorted(files)]
        return sha256_bytes(json.dumps(records, separators=(',', ':')).encode('utf-8'))
    scripts_sha = tree_hash(list((package_dir / 'scripts').glob('*.py')) +
                            list((package_dir / 'schemas').glob('*.json')) +
                            [package_dir / 'requirements.txt'])

    skill_md = package_dir / 'SKILL.md'
    template = package_dir / 'template.md'
    policy = policy_path or package_dir / 'references' / 'check-policy.json'

    versions = {
        'scripts_sha256': scripts_sha,
        'skill_md_sha256': tree_hash([skill_md] + [package_dir / n for n in
            ('doc-writer.md', 'doc-validator.md', 'ddl-finder.md', 'rules.md',
             'references/facts.md', 'references/artifacts.md', 'references/identity.md', 'references/coverage.md',
             'references/sql-support.md', 'references/publication.md', 'references/regression.md', 'references/query.md',
             'docs/dialect-support.md')]),
        'template_sha256': tree_hash([template] + list((package_dir / 'template').glob('*.md'))),
        'policy_sha256': sha256_file(policy),
    }

    if profile_path:
        from profiles import profile_hash
        versions['profile_sha256'] = profile_hash(profile_path)

    return versions


def create_manifest(
    *,
    run_id: str | None = None,
    page_id: str,
    sql_files: list[Path],
    context_files: list[Path] | None = None,
    migration_manifest: Path | None = None,
    artifacts_dir: Path,
    tool_versions: dict[str, str],
    project_dir: Path | None = None,
    identity_registry: Path | None = None,
) -> dict[str, Any]:
    """Create a manifest dict.

    Args:
        run_id: UUID string (generated if None)
        page_id: documented object identifier
        sql_files: list of SQL/DDL file paths
        context_files: optional context file paths
        migration_manifest: optional migration manifest path
        artifacts_dir: directory containing artifact JSON files
        tool_versions: tooling hashes

    Returns:
        manifest dict ready for JSON serialization
    """
    if run_id is None:
        ids = {read_json(artifacts_dir / f'{name}.json').get('run_id')
               for name in ARTIFACT_NAMES if name != 'draft'
               and (artifacts_dir / f'{name}.json').exists()}
        if len(ids) > 1 or None in ids:
            raise BundleError('Cannot infer one run_id from artifacts')
        run_id = next(iter(ids)) if ids else str(uuid.uuid4())
    project_dir = project_dir or artifacts_dir
    if migration_manifest and migration_manifest.resolve() == (artifacts_dir / 'manifest.json').resolve():
        raise BundleError('Migration manifest must be separate from run manifest')

    manifest = {
        'schema_version': 2,
        'run_id': run_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'page_id': page_id,
        'sql_files': [_file_ref(p, project_dir) for p in sql_files],
        'revision': None,
        'tool_versions': tool_versions,
        'artifacts': {},
    }

    inv_path = artifacts_dir / 'inventory.json'
    if inv_path.is_file():
        manifest['documented_subjects'] = read_json(inv_path)['documented_subjects']
    if identity_registry:
        manifest['identity_registry'] = _file_ref(identity_registry, project_dir)
    if (artifacts_dir / 'publication.json').is_file():
        manifest['publication_plan'] = _artifact_ref(artifacts_dir / 'publication.json', artifacts_dir)

    if context_files:
        manifest['context_files'] = [_file_ref(p, project_dir) for p in context_files]

    if migration_manifest:
        manifest['migration_manifest'] = _file_ref(migration_manifest, project_dir)
        from artifact_schema import load_schemas, validate_schema
        migration = read_json(migration_manifest)
        errors = validate_schema(migration, load_schemas()['migration_manifest'], 'migration_manifest')
        if errors:
            raise BundleError('; '.join(errors))
        known = {r['path'] for r in manifest['sql_files'] + manifest.get('context_files', [])}
        for relative in migration['ordered_files']:
            source = (migration_manifest.parent / relative).resolve()
            ref = _file_ref(source, project_dir)
            if ref['path'] not in known:
                manifest.setdefault('context_files', []).append(ref)
                known.add(ref['path'])

    for name in ARTIFACT_NAMES:
        path = artifacts_dir / f'{name}.json' if name != 'draft' else artifacts_dir / 'page.draft.md'
        if path.exists():
            manifest['artifacts'][name] = _artifact_ref(path, artifacts_dir)

    return manifest


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write manifest to JSON file."""
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


# ---- Manifest verification ----

class BundleError(ValueError):
    """Bundle verification failure."""


def verify_manifest_hashes(
    manifest: dict[str, Any],
    run_dir: Path,
    *, roots: dict | None = None,
) -> list[str]:
    """Verify that all files referenced in manifest exist with correct hashes.

    Args:
        manifest: manifest dict
        run_dir: directory containing the run files

    Returns:
        list of error strings (empty if all valid)
    """
    errors = []
    run = Path(run_dir)
    roots = {'project': run, **(roots or {}), 'run': run}
    if not isinstance(manifest, dict):
        return ['manifest must be an object']
    for key in ('sql_files', 'context_files'):
        if not isinstance(manifest.get(key, []), list):
            errors.append(f'{key}: expected an array')
    if not isinstance(manifest.get('artifacts', {}), dict):
        errors.append('artifacts: expected an object')
    if errors:
        return errors

    def verify(ref, location, default_root):
        try:
            path = resolve_reference(ref, roots, default_root)
            return _verify_file_ref(ref, path, location)
        except (EvidenceError, OSError, KeyError, TypeError) as exc:
            return [f'{location}: {exc}']

    # Check SQL files
    for i, ref in enumerate(manifest.get('sql_files', [])):
        errors.extend(verify(ref, f'sql_files.{i}', 'project'))

    # Check context files
    for i, ref in enumerate(manifest.get('context_files', [])):
        errors.extend(verify(ref, f'context_files.{i}', 'project'))

    if manifest.get('identity_registry'):
        errors.extend(verify(manifest['identity_registry'], 'identity_registry', 'project'))

    # Check migration manifest
    mm = manifest.get('migration_manifest')
    if mm:
        errors.extend(verify(mm, 'migration_manifest', 'project'))
        if not errors:
            try:
                from artifact_schema import load_schemas, validate_schema
                path = resolve_reference(mm, roots)
                migration = read_json(path)
                schema_errors = validate_schema(migration, load_schemas()['migration_manifest'], 'migration_manifest')
                errors.extend(schema_errors)
                if not schema_errors:
                    declared = {resolve_reference(ref, roots) for ref in
                                manifest.get('sql_files', []) + manifest.get('context_files', [])}
                    for relative in migration['ordered_files']:
                        target = (path.parent / relative).resolve()
                        if not target.is_relative_to(Path(roots['project']).resolve()) or target not in declared:
                            errors.append(f'migration_manifest: ordered file not bound inside project: {relative}')
            except (ArtifactInputError, EvidenceError, OSError) as exc:
                errors.append(f'migration_manifest: {exc}')

    # Check artifacts
    if manifest.get('publication_plan'):
        ref = manifest['publication_plan']
        if ref.get('root', 'run') != 'run' or ref.get('path') != 'publication.json':
            errors.append('publication_plan must reference run/publication.json')
        else:
            errors.extend(verify(ref, 'publication_plan', 'run'))
    for name, ref in manifest.get('artifacts', {}).items():
        errors.extend(verify(ref, f'artifacts.{name}', 'run'))

    return errors


def _verify_file_ref(ref: dict, path: Path, location: str) -> list[str]:
    """Verify a single file ref: existence and hash."""
    errors = []
    if not path.exists():
        errors.append(f'{location}: file not found: {path}')
        return errors
    actual = sha256_file(path)
    if actual != ref['sha256']:
        errors.append(f'{location}: sha256 mismatch: expected {ref["sha256"]}, got {actual}')
    return errors


def verify_decision_against_manifest(
    decision: dict[str, Any],
    manifest: dict[str, Any],
    run_dir: Path,
) -> list[str]:
    """Verify that a decision references the correct manifest hash.

    Args:
        decision: decision.json dict
        manifest: manifest.json dict
        run_dir: directory containing manifest.json

    Returns:
        list of error strings
    """
    errors = []
    manifest_path = run_dir / 'manifest.json'
    if not manifest_path.exists():
        errors.append('manifest.json not found in run directory')
        return errors

    actual_manifest_hash = sha256_file(manifest_path)
    declared_hash = decision.get('manifest_sha256', '')
    if declared_hash != actual_manifest_hash:
        errors.append(
            f'decision.manifest_sha256 mismatch: '
            f'expected {actual_manifest_hash}, got {declared_hash}'
        )

    # Verify run_id consistency
    if decision.get('run_id') != manifest.get('run_id'):
        errors.append(
            f'run_id mismatch: decision={decision.get("run_id")}, manifest={manifest.get("run_id")}'
        )

    if decision.get('page_id') != manifest.get('page_id'):
        errors.append('decision.page_id mismatch with manifest')
    validation = manifest.get('artifacts', {}).get('validation')
    if not validation or decision.get('validation_sha256') != validation['sha256']:
        errors.append('decision.validation_sha256 mismatch with manifest validation')

    return errors


def reverify_bundle(
    run_dir: Path,
    *,
    require_decision: bool = True,
    roots: dict | None = None,
) -> dict[str, Any]:
    """Re-verify an entire run bundle.

    Args:
        run_dir: directory containing all run artifacts
        require_decision: if True, decision.json must exist and be valid

    Returns:
        {valid: bool, errors: [...], manifest: dict, decision: dict|None}
    """
    run = Path(run_dir)
    errors = []

    # Load manifest
    manifest_path = run / 'manifest.json'
    if not manifest_path.exists():
        return {'valid': False, 'errors': ['manifest.json not found'], 'manifest': None, 'decision': None}
    try:
        manifest = read_json(manifest_path)
    except ArtifactInputError as exc:
        return {'valid': False, 'errors': [f'manifest.json: {exc}'], 'manifest': None, 'decision': None}

    # Verify manifest file hashes
    from artifact_schema import load_schemas, validate_schema
    # Partial bundles may be inspected with --no-decision, but never authorize publication.
    if not isinstance(manifest, dict):
        return {'valid': False, 'errors': ['manifest must be an object'], 'publication_authorized': False}
    if require_decision:
        schema_errors = validate_schema(manifest, load_schemas()['manifest'], 'manifest')
        if schema_errors:
            return {'valid': False, 'errors': schema_errors, 'publication_authorized': False,
                    'manifest': manifest, 'decision': None}
    errors.extend(verify_manifest_hashes(manifest, run, roots=roots))

    # Load and verify decision
    decision = None
    decision_path = run / 'decision.json'
    if decision_path.exists():
        try:
            decision = read_json(decision_path)
            schema_errors = validate_schema(decision, load_schemas()['decision'], 'decision')
            errors.extend(schema_errors)
            if not schema_errors:
                errors.extend(verify_decision_against_manifest(decision, manifest, run))
        except ArtifactInputError as exc:
            errors.append(f'decision.json: {exc}')
    elif require_decision:
        errors.append('decision.json not found (required)')

    return {
        'valid': not errors,
        'errors': errors,
        'manifest': manifest,
        'decision': decision,
        'publication_authorized': False,
    }


# ---- Bundle storage ----

def save_bundle(run_dir: Path, dest: Path, *, roots=None, policy_path=None, profile_path=None) -> Path:
    """Copy verified bundle to long-term storage.

    Args:
        run_dir: source run directory
        dest: destination directory (e.g., .wiki-doc/runs/<run_id>/)

    Returns:
        destination path
    """
    import shutil
    from validation_gate import evaluate_bundle
    run_dir, dest = Path(run_dir).resolve(), Path(dest).resolve()
    if dest == run_dir or dest.is_relative_to(run_dir):
        raise BundleError('Archive destination must be outside the source run')
    if dest.exists():
        raise BundleError('Archive destination already exists')
    result = evaluate_bundle(run_dir, roots=roots, policy_path=policy_path, profile_path=profile_path)
    if not result['publication_authorized']:
        raise BundleError('Only a verified ready bundle can be archived: ' + '; '.join(result['errors']))
    # Copy only the verified run files, not arbitrary caches, symlinks or unrelated files.
    dest.mkdir(parents=True)
    for filename in ('manifest.json', 'decision.json', 'page.draft.md') + tuple(name + '.json' for name in ARTIFACT_NAMES if name != 'draft'):
        shutil.copy2(run_dir / filename, dest / filename)
    source_roots = {'project': run_dir, **(roots or {}), 'run': run_dir}
    manifest = read_json(run_dir / 'manifest.json')
    if manifest.get('publication_plan'):
        shutil.copy2(run_dir / 'publication.json', dest / 'publication.json')
    refs = manifest['sql_files'] + manifest.get('context_files', [])
    if manifest.get('migration_manifest'):
        refs.append(manifest['migration_manifest'])
    if manifest.get('identity_registry'):
        refs.append(manifest['identity_registry'])
    for ref in refs:
        if ref.get('root', 'project') != 'project':
            raise BundleError('SQL/context snapshots require project-relative references')
        source = resolve_reference(ref, source_roots)
        target = (dest / ref['path']).resolve()
        if not target.is_relative_to(dest):
            raise BundleError('Archive reference escapes destination')
        if target.exists() and sha256_file(target) != ref['sha256']:
            raise BundleError(f'Archive path collision: {ref["path"]}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Source evidence is copied into the archive; reader-facing SQL links still
    # resolve against the published wiki and its original SQL project.
    archive_roots = {**(roots or {}), 'project': dest,
                     'link_project': source_roots.get('link_project', source_roots['project'])}
    checked = evaluate_bundle(dest, roots=archive_roots, policy_path=policy_path, profile_path=profile_path)
    if not checked['publication_authorized']:
        raise BundleError('Archived copy failed verification: ' + '; '.join(checked['errors']))
    return dest


# ---- CLI ----

def _cli_create(args):
    """CLI: create manifest for a run directory."""
    run = Path(args.run_dir)
    if not run.is_dir():
        print(f'ERROR: Not a directory: {run}', file=sys.stderr)
        return 2

    # Discover files
    sql_files = sorted(p for p in run.glob('*.sql') if not p.name.startswith('context'))
    if args.sql:
        sql_files = [Path(s) for s in args.sql]
    context_files = [Path(p) for p in args.context] if args.context else (sorted(run.glob('context*.sql')) if not args.no_context else [])
    migration_manifest = Path(args.migration_manifest) if args.migration_manifest else None

    package_dir = Path(args.package_dir) if args.package_dir else Path(__file__).resolve().parent.parent
    profile_path = Path(args.profile) if args.profile else None

    tool_versions = compute_tool_versions(package_dir, profile_path=profile_path,
                                         policy_path=Path(args.policy) if args.policy else None)

    manifest = create_manifest(
        run_id=args.run_id,
        page_id=args.page_id,
        sql_files=sql_files,
        context_files=context_files if context_files else None,
        migration_manifest=migration_manifest,
        artifacts_dir=run,
        tool_versions=tool_versions,
        project_dir=Path(args.project_root) if args.project_root else None,
        identity_registry=Path(args.identity_registry) if args.identity_registry else None,
    )

    output = Path(args.output) if args.output else run / 'manifest.json'
    write_manifest(manifest, output)
    print(f'Manifest written to {output} (run_id={manifest["run_id"]})')
    return 0


def _cli_verify(args):
    """CLI: verify a run bundle."""
    result = reverify_bundle(Path(args.run_dir), require_decision=not args.no_decision,
                             roots=dict(args.root or []))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['valid'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')

    create = sub.add_parser('create', help='Create manifest for a run directory')
    create.add_argument('run_dir', help='Run directory with artifacts')
    create.add_argument('--page-id', required=True, help='Documented object identifier')
    create.add_argument('--run-id', help='UUID (generated if omitted)')
    create.add_argument('--sql', nargs='+', help='SQL file paths (auto-discovered if omitted)')
    create.add_argument('--no-context', action='store_true', help='Skip context file discovery')
    create.add_argument('--identity-registry', help='Project-relative snapshot of existing page paths/keys and explicit legacy aliases')
    create.add_argument('--context', nargs='+', help='Explicit context/DDL files')
    create.add_argument('--migration-manifest', help='Separate migration-order manifest path')
    create.add_argument('--project-root', help='Root for SQL and context references (default: run_dir)')
    create.add_argument('--policy', help='Policy used for this run')
    create.add_argument('--package-dir', help='wiki-doc package root')
    create.add_argument('--profile', help='Profile file path')
    create.add_argument('--output', help='Output path (default: run_dir/manifest.json)')

    verify = sub.add_parser('verify', help='Verify a run bundle')
    verify.add_argument('run_dir', help='Run directory with manifest and artifacts')
    verify.add_argument('--no-decision', action='store_true', help='Do not require decision.json')
    verify.add_argument('--root', nargs=2, action='append', metavar=('NAME', 'PATH'))

    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            return _cli_create(args)
        elif args.command == 'verify':
            return _cli_verify(args)
    except (ArtifactInputError, BundleError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
