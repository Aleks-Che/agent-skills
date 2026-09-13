"""Check policy engine: derive required checks from object type, inventory, and policy rules."""
import json
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parent.parent / 'references' / 'check-policy.json'

VALID_OBJECT_KINDS = frozenset({
    'table', 'view', 'materialized_view', 'ctas',
    'function', 'procedure', 'migration',
    'cte', 'temp_table',
})


class PolicyError(ValueError):
    """Policy or context data is invalid."""


def load_policy(path=None, *, expected_sha256=None):
    """Load and validate check-policy.json."""
    from artifact_schema import read_json, validate_schema, ArtifactInputError
    policy_path = Path(path) if path else POLICY_PATH
    try:
        snapshots = {}
        policy = read_json(policy_path, snapshot_hashes=snapshots)
        if expected_sha256 is not None and snapshots[policy_path.resolve()] != expected_sha256:
            raise PolicyError('Policy bytes differ from the verified policy hash')
    except ArtifactInputError as exc:
        raise PolicyError(f'Cannot load check policy: {exc}') from exc
    if not isinstance(policy, dict):
        raise PolicyError('Check policy must be an object')
    if policy.get('schema_version') != 1:
        raise PolicyError(f"Unsupported check policy schema_version: {policy.get('schema_version')}")
    try:
        errors = validate_schema(policy, read_json(POLICY_PATH.parent.parent / 'schemas' / 'check_policy.schema.json'), 'policy')
    except ArtifactInputError as exc:
        raise PolicyError(str(exc)) from exc
    if errors:
        raise PolicyError('\n'.join(errors))
    _validate_policy_structure(policy)
    return policy


def _validate_policy_structure(policy):
    """Validate internal consistency of the policy."""
    errors = []
    rules_by_id = {}
    for i, rule in enumerate(policy.get('rules', [])):
        rid = rule.get('id')
        if not rid:
            errors.append(f'rules.{i}: missing id')
        elif rid in rules_by_id:
            errors.append(f'rules.{i}: duplicate rule id {rid!r}')
        else:
            rules_by_id[rid] = rule
            if rule.get('source') not in ('inventory', 'object_type', 'section', 'profile'):
                errors.append(f'rules.{i}.source: invalid value')
            if rule.get('category') not in ('technical', 'editorial'):
                errors.append(f'rules.{i}.category: invalid value')

    for group_name, group in policy.get('object_type_groups', {}).items():
        if group_name not in VALID_OBJECT_KINDS:
            errors.append(f'object_type_groups.{group_name}: unknown object kind')
        for rid in group.get('always_required', []) + group.get('conditional', []):
            if rid not in rules_by_id:
                errors.append(f'object_type_groups.{group_name}: unknown rule {rid!r}')

    for section_id, section in policy.get('section_applicability', {}).items():
        if 'block_id' not in section:
            errors.append(f'section_applicability.{section_id}: missing block_id')

    overrides = policy.get('blocking_override_rules', {})
    if 'cannot_be_editorial' not in overrides:
        errors.append('blocking_override_rules: missing cannot_be_editorial')
    if 'always_blocking_defect_codes' not in overrides:
        errors.append('blocking_override_rules: missing always_blocking_defect_codes')

    if errors:
        raise PolicyError('Invalid check policy:\n' + '\n'.join(errors))


def get_rule(policy, rule_id):
    """Get a rule by ID from the policy."""
    for rule in policy.get('rules', []):
        if rule['id'] == rule_id:
            return rule
    return None


def _evaluate_condition(condition, context):
    """Evaluate a single condition against context."""
    if not condition:
        return True

    for key, expected in condition.items():
        if key == '_or':
            choices = expected if isinstance(expected, list) else [{k: v} for k, v in expected.items()]
            if not any(_evaluate_condition(sub, context) for sub in choices):
                return False
            continue

        if key.endswith('_gte'):
            if context.get(key[:-4], 0) < expected:
                return False
            continue

        actual = context.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, bool):
            if bool(actual) != expected:
                return False
        elif isinstance(expected, (int, float)):
            if actual != expected:
                return False
        elif isinstance(expected, str):
            if str(actual) != expected:
                return False
        elif expected is None:
            if actual is not None:
                return False
        else:
            if actual != expected:
                return False

    return True


def _evaluate_section_condition(section_rule, context):
    """Evaluate whether a section is applicable."""
    if section_rule.get('always_applicable'):
        return True
    if section_rule.get('conditional'):
        return True
    condition = section_rule.get('applicable_when', {})
    return _evaluate_condition(condition, context)


def _build_check_id(rule_id, subject, inventory_anchor=None):
    """Build a unique check ID from rule, subject, and optional anchor."""
    if inventory_anchor:
        path = inventory_anchor.get('path', '')
        construct = inventory_anchor.get('construct', '')
        ordinal = inventory_anchor.get('ordinal', 1)
        return f'{rule_id}:{construct}:{ordinal}'
    return rule_id


def derive_required_checks(policy, object_kind, context=None):
    """Derive the list of required checks for an object.

    Args:
        policy: loaded check policy
        object_kind: one of VALID_OBJECT_KINDS
        context: dict with keys like has_reads, has_writes, has_calls,
                 has_formulas, has_conditions, has_date_boundaries,
                 has_dynamic_sql, has_unknowns, source_count, profile_active

    Returns:
        list of check dicts ready for validation_plan.json
    """
    if object_kind not in VALID_OBJECT_KINDS:
        raise PolicyError(f'Unknown object kind: {object_kind!r}')

    context = context or {}
    group = policy['object_type_groups'].get(object_kind)
    if not group:
        raise PolicyError(f'No policy group for object kind: {object_kind!r}')

    object_key = context.get('object_key', f'{object_kind}+unknown')
    checks = []

    for rule_id in group['always_required']:
        rule = get_rule(policy, rule_id)
        if rule is None:
            raise PolicyError(f'Rule {rule_id!r} referenced but not defined')
        subject = rule['subject_pattern'].format(
            object_key=object_key,
            operation_kind='',
            ordinal=0,
            formula_id='',
            condition_id='',
            unknown_id='',
            section_id='',
            check_name='',
        )
        checks.append({
            'id': _build_check_id(rule_id, subject),
            'rule_id': rule_id,
            'subject': subject,
            'source': rule['source'],
            'applicable': True,
            'blocking': rule['blocking_default'],
            'category': rule['category'],
        })

    for rule_id in group['conditional']:
        rule = get_rule(policy, rule_id)
        if rule is None:
            raise PolicyError(f'Rule {rule_id!r} referenced but not defined')
        conditions = rule.get('conditions', {})
        if not _evaluate_condition(conditions, context):
            continue

        subject = rule['subject_pattern'].format(
            object_key=object_key,
            operation_kind='',
            ordinal=0,
            formula_id='',
            condition_id='',
            unknown_id='',
            section_id='',
            check_name='',
        )
        checks.append({
            'id': _build_check_id(rule_id, subject),
            'rule_id': rule_id,
            'subject': subject,
            'source': rule['source'],
            'applicable': True,
            'blocking': rule['blocking_default'],
            'category': rule['category'],
        })

    return checks


def derive_section_checks(policy, context=None):
    """Derive applicable section checks."""
    context = context or {}
    checks = []
    for section_id, section_rule in policy.get('section_applicability', {}).items():
        if _evaluate_section_condition(section_rule, context):
            checks.append({
                'id': f'section:{section_id}',
                'rule_id': 'section',
                'subject': f'{context.get("object_key", "unknown")}/section/{section_id}',
                'source': 'section',
                'applicable': True,
                'blocking': False,
                'category': 'editorial',
            })
    return checks


def derive_inventory_checks(policy, inventory_items, object_key=''):
    """Preserve independent anchors and expand every concrete expression."""
    import hashlib
    checks = []
    operation_kinds = {'SELECT','INSERT','UPDATE','DELETE','MERGE','DDL','PERFORM','CALL','EXECUTE','RETURN','OTHER','CTE','TEMP_TABLE'}
    for item in inventory_items:
        kind, details = item['kind'], item.get('details', {})
        anchor = {'path': item.get('source_ref', {}).get('path', item['anchor'].get('path', '')),
                  **{k: item['anchor'][k] for k in ('object_or_scope','construct','ordinal')}}
        features = [('operation', 0)] if kind in operation_kinds else []
        for group, flag, rule in (('formulas','has_formula','formula'), ('conditions','has_condition','condition')):
            expressions = details.get(group, [None] if details.get(flag) else [])
            features.extend((rule, i) for i in range(1, len(expressions) + 1))
        if details.get('dynamic'):
            features.append(('unknown', 1))
        for rule_id, index in features:
            rule = get_rule(policy, rule_id)
            if not rule:
                raise PolicyError(f'Missing rule {rule_id!r}')
            identity = json.dumps([anchor, rule_id, index], sort_keys=True, separators=(',', ':'))
            digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
            checks.append({'id': f'{rule_id}:{kind}:{digest}', 'rule_id': rule_id,
                           'subject': f'{anchor["path"]}/{anchor["object_or_scope"]}/{kind}/{anchor["ordinal"]}/{rule_id}/{index}',
                           'source': 'inventory', 'inventory_anchor': anchor.copy(),
                           'applicable': True, 'blocking': rule['blocking_default'], 'category': rule['category']})
    return checks


def is_defect_blocking(policy, defect_code, declared_blocking):
    """Determine if a defect should be blocking regardless of agent declaration.

    Returns True if the defect is in the always-blocking list,
    or if it's a technical defect that cannot be downgraded to editorial.
    """
    overrides = policy.get('blocking_override_rules', {})

    if defect_code in set(overrides.get('always_blocking_defect_codes', [])):
        return True

    if defect_code in set(overrides.get('cannot_be_editorial', [])):
        return True

    return declared_blocking


def validate_check_against_policy(policy, check, object_kind=None):
    """Validate a single check against the policy.

    Returns list of error strings (empty if valid).
    """
    errors = []
    rule_id = check.get('rule_id')
    rule = get_rule(policy, rule_id)

    if rule is None:
        errors.append(f'check {check.get("id")}: unknown rule_id {rule_id!r}')
        return errors

    if check.get('category') != rule['category']:
        errors.append(f'check {check.get("id")}: invalid category')

    if check.get('source') != rule['source']:
        errors.append(f'check {check.get("id")}: invalid source')

    if check.get('source') == 'inventory' and 'inventory_anchor' not in check:
        errors.append(f'check {check.get("id")}: inventory source requires inventory_anchor')

    if check.get('source') != 'inventory' and 'inventory_anchor' in check:
        errors.append(f'check {check.get("id")}: non-inventory source must not have inventory_anchor')

    if rule['blocking_default'] and check.get('blocking') is not True:
        errors.append(f'check {check.get("id")}: policy blocking cannot be lowered')
    if check.get('applicable') is not True:
        errors.append(f'check {check.get("id")}: required obligation cannot be made inapplicable')
    return errors


def main():
    """CLI entry point for check policy operations."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')

    show_p = sub.add_parser('show', help='Show the loaded policy summary')
    show_p.add_argument('--policy', help='Path to check-policy.json')

    derive_p = sub.add_parser('derive', help='Derive required checks for an object')
    derive_p.add_argument('--policy', help='Path to check-policy.json')
    derive_p.add_argument('--kind', required=True, choices=sorted(VALID_OBJECT_KINDS),
                          help='Object kind')
    derive_p.add_argument('--context', help='JSON context file or inline JSON')
    derive_p.add_argument('--inventory', help='JSON file with inventory items')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        policy = load_policy(args.policy)

        if args.command == 'show':
            rules = policy.get('rules', [])
            groups = policy.get('object_type_groups', {})
            sections = policy.get('section_applicability', {})
            print(json.dumps({
                'rule_count': len(rules),
                'rules': [r['id'] for r in rules],
                'object_types': list(groups.keys()),
                'sections': list(sections.keys()),
            }, indent=2, ensure_ascii=False))

        elif args.command == 'derive':
            context = {}
            if args.context:
                if args.context.startswith('{'):
                    context = json.loads(args.context)
                else:
                    context = json.loads(Path(args.context).read_text(encoding='utf-8'))

            checks = derive_required_checks(policy, args.kind, context)

            if args.inventory:
                inventory_items = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
                checks.extend(derive_inventory_checks(policy, inventory_items,
                                                       context.get('object_key', '')))

            section_checks = derive_section_checks(policy, context)
            checks.extend(section_checks)

            print(json.dumps(checks, indent=2, ensure_ascii=False))

    except PolicyError as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
