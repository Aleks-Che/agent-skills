"""Generate validation_plan.json from inventory and check policy.

Combines independent SQL inventory with check policy rules to produce
a concrete list of required checks for a specific page/object.
"""
import argparse
import json
import sys
from pathlib import Path

# Add scripts directory to path for sibling module imports
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_policy import (
    load_policy, derive_required_checks, derive_section_checks,
    derive_inventory_checks, validate_check_against_policy,
    PolicyError, VALID_OBJECT_KINDS,
)


def _determine_object_kind(inventory, documented_subjects):
    """Determine object kind from inventory items and documented subjects.

    Heuristic: look at construct types in inventory items.
    """
    constructs = {item['kind'] for item in inventory.get('items', [])}

    # Check for DDL constructs that indicate object type
    if 'CREATE_VIEW' in constructs:
        return 'view'
    if 'CREATE_MATERIALIZED_VIEW' in constructs:
        return 'materialized_view'
    if 'CREATE_TABLE_AS' in constructs:
        return 'ctas'

    # Check details for object info
    for item in inventory.get('items', []):
        details = item.get('details', {})
        if details.get('object_kind'):
            return details['object_kind']

    # Default to function for PL/pgSQL bodies
    plpgsql_kinds = {'PERFORM', 'EXECUTE', 'TEMP_TABLE', 'CTE'}
    if constructs & plpgsql_kinds:
        return 'function'

    return 'function'


def _build_context(inventory, object_kind, object_key=''):
    """Build context dict for check policy evaluation."""
    items = inventory.get('items', [])

    constructs = {item['kind'] for item in items}

    has_reads = any(item.get('reads') for item in items)
    has_writes = any(item.get('writes') for item in items)
    has_calls = any(item.get('calls') for item in items)
    has_formulas = any(
        item.get('details', {}).get('has_formula')
        for item in items
    )
    has_conditions = any(
        item.get('details', {}).get('has_condition')
        for item in items
    )
    has_dynamic_sql = 'EXECUTE' in constructs
    has_unknowns = any(
        item.get('details', {}).get('dynamic')
        for item in items
    )

    return {
        'object_key': object_key,
        'object_kind': object_kind,
        'has_reads': has_reads,
        'has_writes': has_writes,
        'has_calls': has_calls,
        'has_formulas': has_formulas,
        'has_conditions': has_conditions,
        'has_dynamic_sql': has_dynamic_sql,
        'has_unknowns': has_unknowns,
        'has_date_boundaries': any(i.get('details', {}).get('has_date_boundary') for i in items),
        'returns_table': any(i.get('details', {}).get('returns_table') for i in items),
        'source_count': len({ref for item in items for ref in item.get('reads', [])}),
    }


def generate_plan(inventory, policy, page_id=None, object_kind=None,
                  documented_subjects=None, object_key=None, profile_active=False, profile_path=None):
    """Generate validation_plan.json from inventory and policy.

    Args:
        inventory: dict matching inventory.schema.json v2
        policy: loaded check-policy.json
        page_id: page identifier
        object_kind: override object kind detection
        documented_subjects: list of documented object names
        object_key: canonical object key

    Returns:
        dict matching validation_plan.schema.json v2
    """
    from artifact_schema import validate_schema, load_schemas
    errors = validate_schema(inventory, load_schemas()['inventory'], 'inventory')
    if errors:
        raise ValueError('\n'.join(errors))
    subjects = documented_subjects or inventory['documented_subjects']
    selected = [i for i in inventory['items'] if i['anchor']['object_or_scope'] in subjects]
    if not selected:
        raise ValueError('Selected subjects do not match independent inventory scopes')
    inventory = {**inventory, 'items': selected, 'documented_subjects': subjects}
    declarations = [i for i in selected if i['kind'] == 'DECLARATION']
    if len(subjects) != 1 or len(declarations) > 1:
        raise ValueError('Generate one plan per independently selected declaration')
    primary_subject = subjects[0]
    if object_key is not None and object_key != primary_subject:
        raise ValueError('Object key differs from the selected declaration')
    object_key = primary_subject
    derived_kind = _determine_object_kind(inventory, subjects)
    if object_kind is not None and object_kind != derived_kind:
        raise ValueError('Object kind differs from the selected declaration')
    object_kind = derived_kind

    # Build context
    context = _build_context(inventory, object_kind, object_key)
    context['profile_active'] = profile_active

    # Derive checks from policy
    policy_checks = [c for c in derive_required_checks(policy, object_kind, context)
                     if c['source'] not in ('inventory', 'profile') and c['rule_id'] != 'section']
    if profile_active:
        from profiles import load_profile
        from sql_syntax import mask_sql
        import re
        profile = load_profile(profile_path)
        for ordinal, item in enumerate(inventory.get('items', []), 1):
            subjects = [f'{field}/{name}' for field in ('reads', 'writes', 'calls') for name in item.get(field, [])]
            fields = item.get('details', {})
            text = ' '.join(fields.get('formulas',[]) + fields.get('conditions',[]) +
                            [c.get('expression','') for c in fields.get('columns',[])])
            code = mask_sql(text, mask_identifiers=False)[0]
            subjects += ['feature/' + name for name in profile['features'] if re.search(r'\b' + re.escape(name) + r'\b', code, re.I)]
            for subject in sorted(set(subjects)):
                policy_checks.append({'id': f'profile_check:{ordinal}:{subject}', 'rule_id': 'profile_check',
                                      'subject': f'{object_key}/profile/{ordinal}/{subject}',
                                      'source': 'profile', 'applicable': True, 'blocking': True,
                                      'category': 'technical'})

    # Derive checks from inventory items
    inventory_checks = derive_inventory_checks(
        policy, inventory.get('items', []), object_key
    )

    import hashlib
    for note in inventory.get('coverage_notes', []):
        ref = note['source_ref']
        owner = next((item for item in selected if item['source_ref']['path'] == ref['path']), selected[0])
        anchor = {'path': owner['source_ref']['path'], **owner['anchor']}
        digest = hashlib.sha256(json.dumps(note, sort_keys=True).encode()).hexdigest()[:16]
        inventory_checks.append({'id': 'analysis_gap:' + digest, 'rule_id': 'analysis_gap',
                                 'subject': note['reason'], 'source': 'inventory', 'inventory_anchor': anchor,
                                 'applicable': True, 'blocking': True, 'category': 'technical'})

    # Derive section checks
    section_checks = derive_section_checks(policy, context)

    # Merge all checks
    all_checks = []
    seen_ids = set()

    for check in policy_checks + inventory_checks + section_checks:
        check_id = check['id']
        if check_id not in seen_ids:
            # Add path to inventory_anchor if present
            if 'inventory_anchor' in check:
                anchor = check['inventory_anchor']
                if not anchor.get('path'):
                    inputs = inventory.get('inputs', [])
                    if inputs:
                        anchor['path'] = inputs[0]['path']

            all_checks.append(check)
            seen_ids.add(check_id)

    # Validate checks against policy
    errors = []
    for check in all_checks:
        check_errors = validate_check_against_policy(policy, check)
        errors.extend(check_errors)

    if errors:
        raise PolicyError('\n'.join(errors))

    # Get source hashes
    source_hashes = {}
    for inp in inventory.get('inputs', []):
        source_hashes[inp['path']] = inp['sha256']

    plan = {
        'schema_version': 2,
        'run_id': inventory.get('run_id', '00000000-0000-0000-0000-000000000000'),
        'page_id': page_id or object_key,
        'required_checks': all_checks,
    }

    errors = validate_schema(plan, load_schemas()['validation_plan'], 'validation_plan')
    if errors:
        raise ValueError('\n'.join(errors))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', help='Path to inventory.json')
    parser.add_argument('--policy', help='Path to check-policy.json')
    parser.add_argument('--page-id', help='Page identifier')
    parser.add_argument('--kind', choices=sorted(VALID_OBJECT_KINDS),
                        help='Object kind override')
    parser.add_argument('--subjects', nargs='*', help='Documented subjects')
    parser.add_argument('--object-key', help='Canonical object key')
    parser.add_argument('--profile-active', action='store_true', help='Include CKR_GP profile obligations')
    parser.add_argument('--profile', help='Explicit profile JSON/Markdown path')
    parser.add_argument('-o', '--output', type=Path, help='Write UTF-8 plan JSON instead of stdout')

    args = parser.parse_args(argv)

    try:
        from artifact_schema import read_json
        inventory = read_json(args.inventory)
        policy = load_policy(args.policy)

        plan = generate_plan(
            inventory, policy,
            page_id=args.page_id,
            object_kind=args.kind,
            documented_subjects=args.subjects,
            object_key=args.object_key,
            profile_active=args.profile_active or bool(args.profile), profile_path=args.profile,
        )

        payload = json.dumps(plan, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(payload + '\n', encoding='utf-8')
        else:
            print(payload)

    except (OSError, json.JSONDecodeError, PolicyError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
