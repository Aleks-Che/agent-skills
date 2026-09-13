"""Tests for check_policy.py — check catalog, derivation, and blocking rules."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'check_policy.py'
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location('check_policy', PATH)
POLICY_MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY_MOD)

POLICY_PATH = Path(__file__).resolve().parents[1] / 'references' / 'check-policy.json'


class LoadPolicyTests(unittest.TestCase):
    def test_load_real_policy(self):
        policy = POLICY_MOD.load_policy(POLICY_PATH)
        self.assertEqual(policy['schema_version'], 1)
        self.assertGreater(len(policy['rules']), 0)
        self.assertGreater(len(policy['object_type_groups']), 0)

    def test_missing_file_raises(self):
        with self.assertRaises(POLICY_MOD.PolicyError):
            POLICY_MOD.load_policy('/nonexistent/policy.json')

    def test_invalid_schema_version_raises(self):
        import tempfile
        import os
        policy = {'schema_version': 99, 'rules': [
            {'id': 'test', 'description': 'test', 'category': 'technical',
             'blocking_default': True, 'subject_pattern': '{object_key}', 'source': 'object_type'}
        ], 'object_type_groups': {},
            'section_applicability': {},
            'blocking_override_rules': {
                'cannot_be_editorial': [],
                'always_blocking_defect_codes': []
            }}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(policy, f)
            tmp_path = f.name
        try:
            with self.assertRaises(POLICY_MOD.PolicyError):
                POLICY_MOD.load_policy(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_duplicate_rule_id_raises(self):
        policy = {
            'schema_version': 1,
            'rules': [
                {'id': 'x', 'description': 'a', 'category': 'technical',
                 'blocking_default': True, 'subject_pattern': '{object_key}', 'source': 'object_type'},
                {'id': 'x', 'description': 'b', 'category': 'technical',
                 'blocking_default': True, 'subject_pattern': '{object_key}', 'source': 'object_type'},
            ],
            'object_type_groups': {},
            'section_applicability': {},
            'blocking_override_rules': {
                'cannot_be_editorial': [],
                'always_blocking_defect_codes': []
            }
        }
        with self.assertRaises(POLICY_MOD.PolicyError):
            POLICY_MOD._validate_policy_structure(policy)


class GetRuleTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_find_existing_rule(self):
        rule = POLICY_MOD.get_rule(self.policy, 'identity')
        self.assertIsNotNone(rule)
        self.assertEqual(rule['category'], 'technical')

    def test_missing_rule_returns_none(self):
        rule = POLICY_MOD.get_rule(self.policy, 'nonexistent_rule')
        self.assertIsNone(rule)


class DeriveChecksTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_function_always_has_identity_and_signature(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+my_func'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('identity', check_ids)
        self.assertIn('signature', check_ids)
        self.assertIn('sql_registry', check_ids)
        self.assertIn('registry_document', check_ids)

    def test_table_always_has_columns(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'table', {
            'object_key': 'table+public+my_table'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('columns', check_ids)
        self.assertNotIn('signature', check_ids)

    def test_view_always_has_reads(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'view', {
            'object_key': 'view+public+my_view'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('reads', check_ids)
        self.assertIn('columns', check_ids)

    def test_migration_has_migration_order(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'migration', {
            'object_key': 'migration+001_create'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('migration_order', check_ids)
        self.assertNotIn('signature', check_ids)
        self.assertNotIn('columns', check_ids)

    def test_conditional_reads_applied_when_context_true(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+reader',
            'has_reads': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('reads', check_ids)

    def test_conditional_reads_skipped_when_context_false(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+no_reader',
            'has_reads': False,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertNotIn('reads', check_ids)

    def test_conditional_writes_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+writer',
            'has_writes': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('writes', check_ids)

    def test_conditional_calls_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+caller',
            'has_calls': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('calls', check_ids)

    def test_conditional_formulas_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+calculator',
            'has_formulas': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('formula', check_ids)

    def test_conditional_conditions_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+conditional',
            'has_conditions': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('condition', check_ids)

    def test_conditional_dynamic_sql_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+dynamic',
            'has_dynamic_sql': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('dynamic_sql', check_ids)

    def test_conditional_unknowns_applied(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+uncertain',
            'has_unknowns': True,
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertIn('unknown', check_ids)

    def test_unknown_object_kind_raises(self):
        with self.assertRaises(POLICY_MOD.PolicyError):
            POLICY_MOD.derive_required_checks(self.policy, 'unknown_kind', {})

    def test_all_object_types_have_identity(self):
        for kind in ('table', 'view', 'materialized_view', 'ctas',
                     'function', 'procedure', 'migration'):
            with self.subTest(kind=kind):
                checks = POLICY_MOD.derive_required_checks(self.policy, kind, {
                    'object_key': f'{kind}+test'
                })
                check_ids = {c['rule_id'] for c in checks}
                self.assertIn('identity', check_ids)
                self.assertIn('sql_registry', check_ids)
                self.assertIn('registry_document', check_ids)

    def test_table_has_no_signature(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'table', {
            'object_key': 'table+public+t'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertNotIn('signature', check_ids)

    def test_function_has_no_columns(self):
        checks = POLICY_MOD.derive_required_checks(self.policy, 'function', {
            'object_key': 'fn+public+f'
        })
        check_ids = {c['rule_id'] for c in checks}
        self.assertNotIn('columns', check_ids)


class SectionChecksTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_header_always_applicable(self):
        checks = POLICY_MOD.derive_section_checks(self.policy, {
            'object_key': 'fn+test'
        })
        check_ids = [c['id'] for c in checks]
        self.assertIn('section:header_purpose', check_ids)

    def test_schema_signature_for_function(self):
        checks = POLICY_MOD.derive_section_checks(self.policy, {
            'object_key': 'fn+test',
            'object_kind': 'function',
        })
        check_ids = [c['id'] for c in checks]
        self.assertIn('section:schema_signature', check_ids)

    def test_entities_for_table(self):
        checks = POLICY_MOD.derive_section_checks(self.policy, {
            'object_key': 'table+test',
            'object_kind': 'table',
        })
        check_ids = [c['id'] for c in checks]
        self.assertIn('section:entities', check_ids)


class InventoryChecksTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_select_operation_creates_check(self):
        items = [
            {'kind': 'SELECT', 'anchor': {'path': 'test.sql', 'object_or_scope': 'fn', 'construct': 'SELECT', 'ordinal': 1}},
        ]
        checks = POLICY_MOD.derive_inventory_checks(self.policy, items, 'fn+test')
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['rule_id'], 'operation')
        self.assertEqual(checks[0]['inventory_anchor'], items[0]['anchor'])

    def test_multiple_operations_create_separate_checks(self):
        items = [
            {'kind': 'SELECT', 'anchor': {'path': 'test.sql', 'object_or_scope': 'fn', 'construct': 'SELECT', 'ordinal': 1}},
            {'kind': 'INSERT', 'anchor': {'path': 'test.sql', 'object_or_scope': 'fn', 'construct': 'INSERT', 'ordinal': 1}},
            {'kind': 'SELECT', 'anchor': {'path': 'test.sql', 'object_or_scope': 'fn', 'construct': 'SELECT', 'ordinal': 2}},
        ]
        checks = POLICY_MOD.derive_inventory_checks(self.policy, items, 'fn+test')
        self.assertEqual(len(checks), 3)
        check_ids = [c['id'] for c in checks]
        self.assertEqual(len(set(check_ids)), 3)
        self.assertEqual([c['inventory_anchor'] for c in checks], [i['anchor'] for i in items])


class BlockingOverrideTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_wrong_type_is_always_blocking(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'wrong_type', False)
        self.assertTrue(result)

    def test_wrong_formula_is_always_blocking(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'wrong_formula', False)
        self.assertTrue(result)

    def test_missing_operation_is_always_blocking(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'missing_operation', False)
        self.assertTrue(result)

    def test_invented_operation_is_always_blocking(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'invented_operation', False)
        self.assertTrue(result)

    def test_unknown_defect_respects_declaration(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'typo_in_description', False)
        self.assertFalse(result)

    def test_unknown_defect_respects_blocking_declaration(self):
        result = POLICY_MOD.is_defect_blocking(self.policy, 'typo_in_description', True)
        self.assertTrue(result)


class ValidateCheckTests(unittest.TestCase):
    def setUp(self):
        self.policy = POLICY_MOD.load_policy(POLICY_PATH)

    def test_valid_check_passes(self):
        check = {
            'id': 'identity',
            'rule_id': 'identity',
            'subject': 'fn+test',
            'source': 'object_type',
            'applicable': True,
            'blocking': True,
            'category': 'technical',
        }
        errors = POLICY_MOD.validate_check_against_policy(self.policy, check)
        self.assertEqual(errors, [])

    def test_unknown_rule_id_fails(self):
        check = {
            'id': 'bad',
            'rule_id': 'nonexistent',
            'subject': 'fn+test',
            'source': 'object_type',
            'applicable': True,
            'blocking': True,
            'category': 'technical',
        }
        errors = POLICY_MOD.validate_check_against_policy(self.policy, check)
        self.assertGreater(len(errors), 0)

    def test_inventory_without_anchor_fails(self):
        check = {
            'id': 'op_1',
            'rule_id': 'operation',
            'subject': 'fn+test/SELECT/1',
            'source': 'inventory',
            'applicable': True,
            'blocking': True,
            'category': 'technical',
        }
        errors = POLICY_MOD.validate_check_against_policy(self.policy, check)
        self.assertGreater(len(errors), 0)

    def test_non_inventory_with_anchor_fails(self):
        check = {
            'id': 'identity',
            'rule_id': 'identity',
            'subject': 'fn+test',
            'source': 'object_type',
            'inventory_anchor': {'path': 'test.sql', 'object_or_scope': 'fn', 'construct': 'SELECT', 'ordinal': 1},
            'applicable': True,
            'blocking': True,
            'category': 'technical',
        }
        errors = POLICY_MOD.validate_check_against_policy(self.policy, check)
        self.assertGreater(len(errors), 0)


if __name__ == '__main__':
    unittest.main()
