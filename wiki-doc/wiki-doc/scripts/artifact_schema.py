"""Check v2 artifact structure and references; never authorize publication."""
import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / 'schemas'
SCHEMA_FILES = {name: f'{name}.schema.json' for name in (
    'facts', 'coverage', 'validation', 'validation_plan', 'manifest', 'inventory', 'decision')}
FACT_ARRAYS = ('objects', 'definitions', 'operations', 'columns', 'formulas', 'conditions', 'unknowns')


class ArtifactInputError(ValueError):
    """An input file or local schema installation cannot be read."""


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key {key!r}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'non-JSON number {value}')


def read_json(path, *, snapshot_hashes=None):
    try:
        import hashlib
        data = Path(path).read_bytes()
        if snapshot_hashes is not None:
            snapshot_hashes[Path(path).resolve()] = hashlib.sha256(data).hexdigest()
        return json.loads(data.decode('utf-8-sig'),
                          object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ArtifactInputError(f'{path}: invalid JSON or unreadable input: {exc}') from exc


def load_schemas():
    if jsonschema is None:
        raise ArtifactInputError('Install dependencies: python -m pip install -r requirements.txt')
    schemas = {}
    for name, filename in {**SCHEMA_FILES, 'migration_manifest': 'migration_manifest.schema.json'}.items():
        schema = read_json(SCHEMAS_DIR / filename)
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise ArtifactInputError(f'{filename}: invalid schema: {exc.message}') from exc
        schemas[name] = schema
    return schemas


def validate_schema(data, schema, artifact_name):
    if jsonschema is None:
        raise ArtifactInputError('Install dependencies: python -m pip install -r requirements.txt')
    formats = jsonschema.FormatChecker()
    if 'date-time' not in formats.checkers:
        raise ArtifactInputError('date-time checker is unavailable: python -m pip install -r requirements.txt')
    validator = jsonschema.Draft202012Validator(schema, format_checker=formats)
    errors = []
    for error in validator.iter_errors(data):
        path = '.'.join(map(str, error.absolute_path))
        if error.validator == 'required' and isinstance(error.instance, dict):
            for field in error.validator_value:
                if field not in error.instance:
                    message = f"{artifact_name}: {path + '.' if path else ''}{field}: required field is missing"
                    if message not in errors:
                        errors.append(message)
        else:
            errors.append(f"{artifact_name}: {path or '<root>'}: {error.message}")
    return errors


def check_schema_version(data, artifact_name):
    if not isinstance(data, dict):
        return [f'{artifact_name}: <root>: expected an object']
    if 'schema_version' not in data:
        return [f'{artifact_name}: missing schema_version; legacy input is diagnostic only']
    if type(data['schema_version']) is not int or data['schema_version'] != 2:
        return [f"{artifact_name}: schema_version is {data['schema_version']!r}, expected 2; no automatic upgrade"]
    return []


def check_unique_ids(data, artifact_name, id_paths):
    errors = []
    for array_path, id_field in id_paths:
        obj = data
        for part in array_path.split('.'):
            obj = obj.get(part) if isinstance(obj, dict) else None
        if not isinstance(obj, list):
            continue
        seen = {}
        for index, item in enumerate(obj):
            value = item.get(id_field) if isinstance(item, dict) else None
            if not isinstance(value, str):
                continue
            if value in seen:
                errors.append(f'{artifact_name}: {array_path}.{index}.{id_field}: duplicate {value!r} (first at {seen[value]})')
            else:
                seen[value] = index
    return errors


def _reference(errors, value, valid_ids, location):
    if value not in valid_ids:
        errors.append(f'{location}: unknown reference {value!r}')


def _references(errors, values, valid_ids, location):
    for index, value in enumerate(values):
        _reference(errors, value, valid_ids, f'{location}.{index}')


def check_fact_references(data, facts_data, artifact_name):
    if facts_data is None:
        return [f'{artifact_name}: requires facts.json for reference validation']
    fact_ids = {item['id'] for group in FACT_ARRAYS for item in facts_data[group]}
    errors = []
    if artifact_name == 'coverage':
        for fact_id in data['entries']:
            _reference(errors, fact_id, fact_ids, f'coverage: entries.{fact_id}')
    return errors


def _facts_integrity(facts):
    errors, all_ids = [], {}
    groups = {name: {item['id'] for item in facts[name]} for name in FACT_ARRAYS}
    for name in FACT_ARRAYS:
        for index, item in enumerate(facts[name]):
            location = f'facts: {name}.{index}'
            if item['id'] in all_ids:
                errors.append(f"{location}.id: duplicate fact ID {item['id']!r}; first at {all_ids[item['id']]}")
            else:
                all_ids[item['id']] = location
    _references(errors, facts['documented_object_ids'], groups['objects'], 'facts: documented_object_ids')
    for name in FACT_ARRAYS:
        for index, item in enumerate(facts[name]):
            base = f'facts: {name}.{index}'
            for field in ('object_id', 'scope'):
                if item.get(field) is not None:
                    _reference(errors, item[field], groups['objects'], f'{base}.{field}')
            for field, target in (('reads', 'objects'), ('writes', 'objects'), ('calls', 'objects'),
                                  ('condition_ids', 'conditions'), ('operation_ids', 'operations'),
                                  ('source_columns', 'columns'), ('column_ids', 'columns')):
                _references(errors, item.get(field, []), groups[target], f'{base}.{field}')
            _references(errors, item.get('related_facts', []), all_ids, f'{base}.related_facts')
            _references(errors, item.get('migration_order') or [],
                        {ref['path'] for ref in facts['inputs']}, f'{base}.migration_order')
    return errors


def check_run_consistency(artifacts):
    errors = []
    for field in ('run_id', 'page_id'):
        values = {name: data[field] for name, data in artifacts.items() if field in data}
        if len(set(values.values())) > 1:
            errors.append(f'{field} mismatch across artifacts: ' + ', '.join(f'{k}={v}' for k, v in values.items()))
    facts = artifacts.get('facts')
    pages = [data['page_id'] for data in artifacts.values() if 'page_id' in data]
    if facts and pages:
        for index, obj in enumerate(facts['objects']):
            if obj['id'] in facts['documented_object_ids'] and obj.get('page_id') not in (None, pages[0]):
                errors.append(f'facts: objects.{index}.page_id: does not match documented page {pages[0]!r}')
    return errors


def _source_integrity(data, name):
    """Internal declarations only: filesystem existence and byte hashes are P0-04."""
    errors, inputs = [], {}
    for index, ref in enumerate(data.get('inputs', [])):
        key = (ref.get('root', 'project'), ref['path'])
        if key in inputs:
            errors.append(f"{name}: inputs.{index}.path: duplicate source {ref['path']!r}")
        inputs[key] = ref['sha256']

    def visit(value, location):
        if isinstance(value, dict):
            if {'path', 'start_line', 'end_line', 'sha256'} <= value.keys():
                if (type(value['start_line']) is not int or type(value['end_line']) is not int
                        or not isinstance(value['path'], str) or not isinstance(value['sha256'], str)):
                    errors.append(f'{location}: malformed embedded source reference')
                    return
                if value['end_line'] < value['start_line']:
                    errors.append(f'{location}.end_line: precedes start_line')
                if name in ('facts', 'inventory'):
                    source_key = (value.get('root', 'project'), value['path'])
                    if source_key not in inputs:
                        errors.append(f'{location}.path: source is absent from inputs')
                    elif value['sha256'] != inputs[source_key]:
                        errors.append(f'{location}.sha256: differs from inputs')
            for key, child in value.items():
                visit(child, f'{location}.{key}')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{location}.{index}')
    visit(data, name + ':')
    return errors


def _anchor_key(anchor):
    return tuple(anchor[key] for key in ('path', 'object_or_scope', 'construct', 'ordinal'))


def _linked_artifacts(artifacts):
    errors = []
    facts, inventory = artifacts.get('facts'), artifacts.get('inventory')
    plan, report = artifacts.get('validation_plan'), artifacts.get('validation')
    if 'coverage' in artifacts:
        errors.extend(check_fact_references(artifacts['coverage'], facts, 'coverage'))
    anchors = set()
    if inventory:
        for index, item in enumerate(inventory['items']):
            key = _anchor_key({'path': item['source_ref']['path'], **item['anchor']})
            if key in anchors:
                errors.append(f'inventory: items.{index}.anchor: duplicate source anchor')
            anchors.add(key)
    if plan:
        for index, check in enumerate(plan['required_checks']):
            if check['source'] == 'inventory':
                if not inventory:
                    errors.append(f'validation_plan: required_checks.{index}: requires inventory.json')
                elif _anchor_key(check['inventory_anchor']) not in anchors:
                    errors.append(f'validation_plan: required_checks.{index}.inventory_anchor: unknown source anchor')
    if report:
        plan_ids = {item['id'] for item in plan['required_checks']} if plan else set()
        fact_ids = {item['id'] for group in FACT_ARRAYS for item in facts[group]} if facts else set()
        for index, check in enumerate(report['checks']):
            if 'plan_check_id' in check:
                _reference(errors, check['plan_check_id'], plan_ids, f'validation: checks.{index}.plan_check_id')
            _references(errors, check.get('fact_ids', []), fact_ids, f'validation: checks.{index}.fact_ids')
    if 'decision' in artifacts:
        decision = artifacts['decision']
        ids = {check['id'] for check in report['checks']} if report else set()
        for field in ('blocking_defects', 'blocking_inconclusive'):
            _references(errors, decision.get(field, []), ids, f'decision: {field}')
    if 'manifest' in artifacts:
        for name, ref in artifacts['manifest']['artifacts'].items():
            expected = 'page.draft.md' if name == 'draft' else f'{name}.json'
            normalized = ref['path'].replace('\\', '/')
            if normalized.startswith('./'):
                normalized = normalized[2:]
            if normalized != expected:
                errors.append(f'manifest: artifacts.{name}.path: must reference {expected!r} in this run directory')
            if ref.get('root', 'run') != 'run':
                errors.append(f'manifest: artifacts.{name}.root: must reference the run root')
    return errors


def validate_artifacts(artifacts, required=None):
    """Full set by default; partial-stage requirements must be explicit."""
    required = set(SCHEMA_FILES if required is None else required)
    if not required or required - SCHEMA_FILES.keys():
        raise ArtifactInputError('Required artifact names must be a nonempty known selection')
    schemas = load_schemas()
    errors = [f'{name}: required artifact is missing' for name in sorted(required - artifacts.keys())]
    for name, data in artifacts.items():
        if name not in SCHEMA_FILES:
            errors.append(f'{name}: unknown artifact')
            continue
        errors.extend(check_schema_version(data, name))
        errors.extend(validate_schema(data, schemas[name], name))
    if errors:
        return errors  # Do not traverse malformed data as if its schema passed.
    if 'facts' in artifacts:
        errors.extend(_facts_integrity(artifacts['facts']))
    for name, data in artifacts.items():
        paths = {'validation': [('checks', 'id')], 'validation_plan': [('required_checks', 'id')]}.get(name, [])
        errors.extend(check_unique_ids(data, name, paths))
        errors.extend(_source_integrity(data, name))
    errors.extend(check_run_consistency(artifacts))
    errors.extend(_linked_artifacts(artifacts))
    sources = {}
    for name, data in artifacts.items():
        for field in ('inputs', 'sql_files', 'context_files'):
            for index, ref in enumerate(data.get(field, [])):
                previous = sources.setdefault((ref.get('root', 'project'), ref['path']), ref['sha256'])
                if previous != ref['sha256']:
                    errors.append(f'{name}: {field}.{index}.sha256: conflicts with source in another artifact')
    return errors


def read_artifact_set(artifacts_dir, *, snapshot_hashes=None):
    directory = Path(artifacts_dir)
    if not directory.is_dir():
        raise ArtifactInputError(f'Not a directory: {directory}')
    return {name: read_json(directory / f'{name}.json', snapshot_hashes=snapshot_hashes) for name in SCHEMA_FILES
            if (directory / f'{name}.json').exists()}


def validate_artifact_set(artifacts_dir, required=None):
    return validate_artifacts(read_artifact_set(artifacts_dir), required)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts_dir', nargs='?', help='Directory containing artifact JSON files')
    parser.add_argument('--artifacts', nargs='+', choices=SCHEMA_FILES,
                        help='Explicit partial-stage requirements; other present artifacts are also validated')
    parser.add_argument('--migration-manifest', help='Validate migration ordering separately')
    args = parser.parse_args(argv)
    if bool(args.artifacts_dir) == bool(args.migration_manifest) or (args.migration_manifest and args.artifacts):
        parser.error('Choose an artifacts directory or --migration-manifest (without --artifacts)')
    try:
        if args.migration_manifest:
            errors = validate_schema(read_json(args.migration_manifest), load_schemas()['migration_manifest'], 'migration_manifest')
            count, mode = 1, 'migration_manifest'
        else:
            artifacts = read_artifact_set(args.artifacts_dir)
            errors = validate_artifacts(artifacts, args.artifacts)
            count, mode = len(artifacts), 'partial' if args.artifacts else 'complete'
    except ArtifactInputError as exc:
        print(json.dumps({'valid': False, 'error': str(exc), 'publication_authorized': False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({'valid': not errors, 'errors': errors, 'artifact_count': count,
                      'mode': mode, 'publication_authorized': False}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
