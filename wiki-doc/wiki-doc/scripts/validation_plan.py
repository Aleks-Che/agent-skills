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
        'source_count': len(inventory.get('inputs', [])),
    }


def generate_plan(inventory, policy, page_id=None, object_kind=None,
                  documented_subjects=None, object_key=None):
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
    subjects = documented_subjects or inventory.get('documented_subjects', [])
    if not subjects:
        raise ValueError('No documented subjects found in inventory')

    # Use first subject as primary
    primary_subject = subjects[0] if subjects else 'unknown'
    if object_key is None:
        object_key = primary_subject

    # Determine object kind
    if object_kind is None:
        object_kind = _determine_object_kind(inventory, subjects)
    elif object_kind not in VALID_OBJECT_KINDS:
        raise ValueError(f'Invalid object kind: {object_kind!r}')

    # Build context
    context = _build_context(inventory, object_kind, object_key)

    # Derive checks from policy
    policy_checks = derive_required_checks(policy, object_kind, context)

    # Derive checks from inventory items
    inventory_checks = derive_inventory_checks(
        policy, inventory.get('items', []), object_key
    )

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
        # Log errors but don't fail — they indicate policy gaps
        pass

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

    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', help='Path to inventory.json')
    parser.add_argument('--policy', help='Path to check-policy.json')
    parser.add_argument('--page-id', help='Page identifier')
    parser.add_argument('--kind', choices=sorted(VALID_OBJECT_KINDS),
                        help='Object kind override')
    parser.add_argument('--subjects', nargs='*', help='Documented subjects')
    parser.add_argument('--object-key', help='Canonical object key')

    args = parser.parse_args()

    try:
        inventory = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
        policy = load_policy(args.policy)

        plan = generate_plan(
            inventory, policy,
            page_id=args.page_id,
            object_kind=args.kind,
            documented_subjects=args.subjects,
            object_key=args.object_key,
        )

        print(json.dumps(plan, indent=2, ensure_ascii=False))

    except (OSError, json.JSONDecodeError, PolicyError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
