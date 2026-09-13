"""Tests for identity.py — canonical key, page slug, page_id, collision detection."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'identity.py'
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location('identity', PATH)
IDENT_MOD = importlib.util.module_from_spec(SPEC)
sys.modules['identity'] = IDENT_MOD
SPEC.loader.exec_module(IDENT_MOD)


class ObjectDescriptorTests(unittest.TestCase):
    def test_create_relation(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='public', name='orders')
        self.assertEqual(desc.kind, 'table')
        self.assertEqual(desc.schema, 'public')
        self.assertEqual(desc.name, 'orders')
        self.assertIsNone(desc.arg_types)

    def test_create_routine(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['bigint'])
        self.assertEqual(desc.arg_types, ['bigint'])

    def test_create_migration(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='migrations/001_create.sql')
        self.assertEqual(desc.migration_path, 'migrations/001_create.sql')


class NormalizeIdentifierTests(unittest.TestCase):
    def test_unquoted_lowercased(self):
        self.assertEqual(IDENT_MOD.normalize_identifier('Orders'), 'orders')

    def test_quoted_preserves_case(self):
        self.assertEqual(IDENT_MOD.normalize_identifier('"MyTable"'), 'MyTable')

    def test_quoted_all_caps(self):
        self.assertEqual(IDENT_MOD.normalize_identifier('"ORDERS"'), 'ORDERS')

    def test_empty_string(self):
        self.assertEqual(IDENT_MOD.normalize_identifier(''), '')

    def test_single_char(self):
        self.assertEqual(IDENT_MOD.normalize_identifier('X'), 'x')

    def test_quoted_single_char(self):
        self.assertEqual(IDENT_MOD.normalize_identifier('"X"'), 'X')


class NormalizeTypeTests(unittest.TestCase):
    def test_int4_to_integer(self):
        self.assertEqual(IDENT_MOD.normalize_type('int4'), 'integer')

    def test_int8_to_bigint(self):
        self.assertEqual(IDENT_MOD.normalize_type('int8'), 'bigint')

    def test_bool_to_boolean(self):
        self.assertEqual(IDENT_MOD.normalize_type('bool'), 'boolean')

    def test_varchar_kept(self):
        self.assertEqual(IDENT_MOD.normalize_type('varchar'), 'character varying')

    def test_varchar_with_length(self):
        self.assertEqual(IDENT_MOD.normalize_type('varchar(255)'), 'character varying(255)')

    def test_numeric_with_precision(self):
        self.assertEqual(IDENT_MOD.normalize_type('numeric(10,2)'), 'numeric(10,2)')

    def test_timestamptz(self):
        self.assertEqual(IDENT_MOD.normalize_type('timestamptz'), 'timestamp with time zone')

    def test_already_canonical(self):
        self.assertEqual(IDENT_MOD.normalize_type('integer'), 'integer')

    def test_text_unchanged(self):
        self.assertEqual(IDENT_MOD.normalize_type('text'), 'text')

    def test_uuid_unchanged(self):
        self.assertEqual(IDENT_MOD.normalize_type('uuid'), 'uuid')

    def test_jsonb_unchanged(self):
        self.assertEqual(IDENT_MOD.normalize_type('jsonb'), 'jsonb')

    def test_float8(self):
        self.assertEqual(IDENT_MOD.normalize_type('float8'), 'double precision')

    def test_float4(self):
        self.assertEqual(IDENT_MOD.normalize_type('float4'), 'real')

    def test_empty_string(self):
        self.assertEqual(IDENT_MOD.normalize_type(''), '')

    def test_user_type_kept(self):
        # User-defined types should not be guessed
        self.assertEqual(IDENT_MOD.normalize_type('my_custom_type'), 'my_custom_type')


class CanonicalKeyRelationTests(unittest.TestCase):
    def test_table(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='public', name='orders')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'table+public+orders')

    def test_view(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='view', schema='demo', name='v_sales')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'view+demo+v_sales')

    def test_materialized_view(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='materialized_view', schema='core', name='mv_stats')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'materialized_view+core+mv_stats')

    def test_ctas(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='ctas', schema='public', name='report')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'ctas+public+report')

    def test_table_without_schema_is_unresolved(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', name='users')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)

    def test_table_quoted_schema(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='"MySchema"', name='users')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'table+MySchema+users')

    def test_table_quoted_name(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='public', name='"UserTable"')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'table+public+UserTable')

    def test_table_quoted_preserves_case(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='"Public"', name='"Orders"')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'table+Public+Orders')

    def test_table_unquoted_lowercased(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='PUBLIC', name='ORDERS')
        self.assertEqual(IDENT_MOD.canonical_key(desc), 'table+public+orders')

    def test_table_missing_name_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='public')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)


class CanonicalKeyRoutineTests(unittest.TestCase):
    def test_function_no_args(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+orders_summary+()')

    def test_function_one_arg(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                           arg_types=['bigint'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+orders_summary+(bigint)')

    def test_function_two_args(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['integer', 'numeric'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+calc+(integer,numeric)')

    def test_function_type_normalized(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['int4', 'int8', 'bool'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+calc+(integer,bigint,boolean)')

    def test_function_out_excluded(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['IN p_id bigint', 'OUT p_result integer'])
        key = IDENT_MOD.canonical_key(desc)
        # OUT excluded, only IN bigint
        self.assertEqual(key, 'function+core+calc+(bigint)')

    def test_function_inout_included(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='swap',
                                           arg_types=['INOUT p_a integer', 'INOUT p_b integer'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+swap+(integer,integer)')

    def test_function_variadic_included(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='fmt',
                                           arg_types=['VARIADIC p_args text[]'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+fmt+(text[])')

    def test_function_mixed_modes(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='proc',
                                           arg_types=['IN p_id bigint', 'INOUT p_val integer',
                                                      'VARIADIC p_args text[]', 'OUT p_res boolean'])
        key = IDENT_MOD.canonical_key(desc)
        # OUT excluded: IN bigint, INOUT integer, VARIADIC text[]
        self.assertEqual(key, 'function+core+proc+(bigint,integer,text[])')

    def test_function_no_mode_defaults_to_in(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['bigint', 'integer'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'function+core+calc+(bigint,integer)')

    def test_procedure(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='procedure', schema='core', name='do_work',
                                           arg_types=['integer'])
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'procedure+core+do_work+(integer)')

    def test_function_missing_name_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)

    def test_function_without_schema_is_unresolved(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', name='my_func')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)


class CanonicalKeyMigrationTests(unittest.TestCase):
    def test_migration_basic(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='migrations/001_create.sql')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'migration+migrations/001_create.sql+001')

    def test_migration_custom_id(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='migrations/001_create.sql',
                                           migration_id='create_tables')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'migration+migrations/001_create.sql+create_tables')

    def test_migration_underscore_separator(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='db/002_add_index.sql')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'migration+db/002_add_index.sql+002')

    def test_migration_dot_separator(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='db/003.cleanup.sql')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'migration+db/003.cleanup.sql+003')

    def test_migration_no_numeric_prefix(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration',
                                           migration_path='db/create_tables.sql')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'migration+db/create_tables.sql+create_tables')

    def test_migration_missing_path_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='migration')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)


class CanonicalKeyEdgeCases(unittest.TestCase):
    def test_unknown_kind_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='unknown_kind', name='x')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)

    def test_cte(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='cte', schema='local', name='recent')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'cte+local+recent')

    def test_temp_table(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='temp_table', schema='pg_temp', name='tmp')
        key = IDENT_MOD.canonical_key(desc)
        self.assertEqual(key, 'temp_table+pg_temp+tmp')

    def test_non_descriptor_raises(self):
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key({'kind': 'table', 'name': 'x'})


class PageSlugTests(unittest.TestCase):
    def test_function_slug(self):
        slug = IDENT_MOD.page_slug('function+core+orders_summary+()')
        self.assertEqual(slug, 'function-core-orders-summary')

    def test_table_slug(self):
        slug = IDENT_MOD.page_slug('table+public+orders')
        self.assertEqual(slug, 'table-public-orders')

    def test_function_with_args_slug(self):
        slug = IDENT_MOD.page_slug('function+core+orders_summary+(bigint)')
        self.assertEqual(slug, 'function-core-orders-summary-bigint')

    def test_migration_slug(self):
        slug = IDENT_MOD.page_slug('migration+migrations/001_create.sql+001')
        self.assertEqual(slug, 'migration-migrations-001-create-sql-001')

    def test_slug_no_leading_trailing_dash(self):
        slug = IDENT_MOD.page_slug('table+public+orders')
        self.assertFalse(slug.startswith('-'))
        self.assertFalse(slug.endswith('-'))

    def test_slug_lowercase(self):
        slug = IDENT_MOD.page_slug('table+Public+Orders')
        self.assertEqual(slug, slug.lower())

    def test_slug_no_consecutive_dashes(self):
        slug = IDENT_MOD.page_slug('function+core+my__func+()')
        self.assertNotIn('--', slug)


class PageIdTests(unittest.TestCase):
    def test_basic_page_id(self):
        pid = IDENT_MOD.page_id('function+core+calc+()')
        self.assertRegex(pid, r'^function-core-calc--[0-9a-f]{12}\.md$')

    def test_page_id_deterministic(self):
        pid1 = IDENT_MOD.page_id('function+core+calc+()')
        pid2 = IDENT_MOD.page_id('function+core+calc+()')
        self.assertEqual(pid1, pid2)

    def test_different_keys_different_ids(self):
        pid1 = IDENT_MOD.page_id('function+core+calc+()')
        pid2 = IDENT_MOD.page_id('function+core+other+()')
        self.assertNotEqual(pid1, pid2)

    def test_page_id_always_includes_hash(self):
        # Even zero-arg routines get a hash
        pid = IDENT_MOD.page_id('function+core+orders_summary+()')
        self.assertIn('--', pid)
        self.assertTrue(pid.endswith('.md'))

    def test_collision_extends_hash(self):
        # First generate a normal id
        pid1 = IDENT_MOD.page_id('function+core+calc+()')
        # Now pretend it exists
        existing = {pid1}
        pid2 = IDENT_MOD.page_id('function+core+calc+()', existing_ids=existing)
        # Same key, collision → longer hash or different
        self.assertNotEqual(pid1, pid2)
        # Both end with .md
        self.assertTrue(pid2.endswith('.md'))

    def test_collision_extends_progressively(self):
        # Generate multiple collisions
        key = 'function+core+calc+()'
        ids = set()
        for i in range(5):
            pid = IDENT_MOD.page_id(key, existing_ids=ids)
            ids.add(pid)
        # All should be unique
        self.assertEqual(len(ids), 5)
        # All should end with .md
        for pid in ids:
            self.assertTrue(pid.endswith('.md'))

    def test_table_page_id(self):
        pid = IDENT_MOD.page_id('table+public+orders')
        self.assertRegex(pid, r'^table-public-orders--[0-9a-f]{12}\.md$')

    def test_migration_page_id(self):
        pid = IDENT_MOD.page_id('migration+migrations/001_create.sql+001')
        self.assertIn('--', pid)
        self.assertTrue(pid.endswith('.md'))

    def test_hash_is_sha256_prefix(self):
        import hashlib
        key = 'function+core+calc+()'
        pid = IDENT_MOD.page_id(key)
        hash_part = pid.split('--')[1].replace('.md', '')
        full_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        self.assertEqual(hash_part, full_hash[:12])

    def test_custom_initial_hash_len(self):
        pid = IDENT_MOD.page_id('function+core+calc+()', initial_hash_len=8)
        hash_part = pid.split('--')[1].replace('.md', '')
        self.assertEqual(len(hash_part), 8)


class DetectCollisionTests(unittest.TestCase):
    def test_no_collision(self):
        existing = {'function-core-calc--abcd1234abcd.md'}
        self.assertFalse(IDENT_MOD.detect_collision(existing, 'function-core-other--5678efgh5678.md'))

    def test_collision_detected(self):
        existing = {'function-core-calc--abcd1234abcd.md'}
        self.assertTrue(IDENT_MOD.detect_collision(existing, 'function-core-calc--abcd1234abcd.md'))

    def test_empty_existing(self):
        self.assertFalse(IDENT_MOD.detect_collision(set(), 'function-core-calc--abcd1234abcd.md'))


class Scenario06Tests(unittest.TestCase):
    """Tests from example 06_same_name_diff_schema.expected.md:

    Three objects:
    - core.orders_summary() — no args
    - archive.orders_summary() — no args, different schema
    - core.orders_summary(bigint) — with arg

    All must get different canonical keys and page_ids.
    """

    def test_three_different_canonical_keys(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary')
        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='archive', name='orders_summary')
        d3 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['bigint'])
        k1 = IDENT_MOD.canonical_key(d1)
        k2 = IDENT_MOD.canonical_key(d2)
        k3 = IDENT_MOD.canonical_key(d3)
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(k2, k3)

    def test_three_different_page_ids(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary')
        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='archive', name='orders_summary')
        d3 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['bigint'])
        p1 = IDENT_MOD.page_id(IDENT_MOD.canonical_key(d1))
        p2 = IDENT_MOD.page_id(IDENT_MOD.canonical_key(d2))
        p3 = IDENT_MOD.page_id(IDENT_MOD.canonical_key(d3))
        self.assertNotEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        self.assertNotEqual(p2, p3)

    def test_rename_arg_does_not_change_key(self):
        """Renaming p_min_id while keeping type bigint must not change the key."""
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['IN p_min_id bigint'])
        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['IN p_other bigint'])
        self.assertEqual(IDENT_MOD.canonical_key(d1), IDENT_MOD.canonical_key(d2))

    def test_schema_name_changes_key(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary')
        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='archive', name='orders_summary')
        self.assertNotEqual(IDENT_MOD.canonical_key(d1), IDENT_MOD.canonical_key(d2))

    def test_arg_type_changes_key(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['bigint'])
        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['integer'])
        self.assertNotEqual(IDENT_MOD.canonical_key(d1), IDENT_MOD.canonical_key(d2))

    def test_canonical_key_values(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary')
        self.assertEqual(IDENT_MOD.canonical_key(d1), 'function+core+orders_summary+()')

        d2 = IDENT_MOD.ObjectDescriptor(kind='function', schema='archive', name='orders_summary')
        self.assertEqual(IDENT_MOD.canonical_key(d2), 'function+archive+orders_summary+()')

        d3 = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='orders_summary',
                                         arg_types=['bigint'])
        self.assertEqual(IDENT_MOD.canonical_key(d3), 'function+core+orders_summary+(bigint)')


class UndefinedIdentityTests(unittest.TestCase):
    """Undefined identity (missing schema or signature) must block publication."""

    def test_missing_schema_is_error(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema=None, name='my_func')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)

    def test_missing_name_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='table', schema='public')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)

    def test_unknown_kind_raises(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='trigger', name='trg')
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.canonical_key(desc)


class ComputeIdentityTests(unittest.TestCase):
    def test_compute_table(self):
        result = IDENT_MOD.compute_identity(kind='table', schema='public', name='orders')
        self.assertEqual(result['canonical_key'], 'table+public+orders')
        self.assertIn('table-public-orders', result['slug'])
        self.assertIn('--', result['page_id'])
        self.assertTrue(result['page_id'].endswith('.md'))
        self.assertFalse(result['collision'])

    def test_compute_function_no_args(self):
        result = IDENT_MOD.compute_identity(kind='function', schema='core', name='calc')
        self.assertEqual(result['canonical_key'], 'function+core+calc+()')
        self.assertFalse(result['collision'])

    def test_compute_function_with_args(self):
        result = IDENT_MOD.compute_identity(kind='function', schema='core', name='calc',
                                             arg_types=['int4', 'bool'])
        self.assertEqual(result['canonical_key'], 'function+core+calc+(integer,boolean)')

    def test_compute_migration(self):
        result = IDENT_MOD.compute_identity(kind='migration',
                                             migration_path='migrations/001_create.sql')
        self.assertEqual(result['canonical_key'], 'migration+migrations/001_create.sql+001')

    def test_compute_with_collision(self):
        r1 = IDENT_MOD.compute_identity(kind='function', schema='core', name='calc')
        existing = {r1['page_id']}
        r2 = IDENT_MOD.compute_identity(kind='function', schema='core', name='calc',
                                         existing_ids=existing)
        # page_id avoids collision by extending hash → different id, no collision
        self.assertNotEqual(r1['page_id'], r2['page_id'])
        self.assertFalse(r2['collision'])

    def test_compute_invalid_kind_raises(self):
        with self.assertRaises(IDENT_MOD.IdentityError):
            IDENT_MOD.compute_identity(kind='invalid', name='x')


class CliTests(unittest.TestCase):
    def test_compute_table(self):
        rc = IDENT_MOD.main(['compute', '--kind', 'table', '--schema', 'public', '--name', 'orders'])
        self.assertEqual(rc, 0)

    def test_compute_function_with_args(self):
        rc = IDENT_MOD.main(['compute', '--kind', 'function', '--schema', 'core', '--name', 'calc',
                              '--arg-types', 'int4', 'bool'])
        self.assertEqual(rc, 0)

    def test_compute_migration(self):
        rc = IDENT_MOD.main(['compute', '--kind', 'migration',
                              '--migration-path', 'migrations/001_create.sql'])
        self.assertEqual(rc, 0)

    def test_compute_invalid_kind_returns_error(self):
        with self.assertRaises(SystemExit):
            IDENT_MOD.main(['compute', '--kind', 'invalid', '--name', 'x'])

    def test_check_subcommand(self):
        rc = IDENT_MOD.main(['check', 'function+core+calc+()',
                              '--existing-ids', 'function-core-calc--abcd1234abcd.md'])
        self.assertEqual(rc, 0)

    def test_no_command_returns_2(self):
        rc = IDENT_MOD.main([])
        self.assertEqual(rc, 2)


class StableKeyTests(unittest.TestCase):
    """Verify that keys are stable across multiple calls."""

    def test_function_key_stable(self):
        desc = IDENT_MOD.ObjectDescriptor(kind='function', schema='core', name='calc',
                                           arg_types=['integer', 'bigint'])
        keys = {IDENT_MOD.canonical_key(desc) for _ in range(100)}
        self.assertEqual(len(keys), 1)

    def test_page_id_stable(self):
        key = 'function+core+calc+(integer,bigint)'
        pids = {IDENT_MOD.page_id(key) for _ in range(100)}
        self.assertEqual(len(pids), 1)

    def test_slug_stable(self):
        key = 'function+core+calc+(integer,bigint)'
        slugs = {IDENT_MOD.page_slug(key) for _ in range(100)}
        self.assertEqual(len(slugs), 1)


class QuotedIdentifierIntegrationTests(unittest.TestCase):
    """Integration tests for quoted identifiers in full pipeline."""

    def test_quoted_schema_different_from_unquoted(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='table', schema='"MySchema"', name='orders')
        d2 = IDENT_MOD.ObjectDescriptor(kind='table', schema='myschema', name='orders')
        k1 = IDENT_MOD.canonical_key(d1)
        k2 = IDENT_MOD.canonical_key(d2)
        # Quoted preserves case: MySchema vs myschema
        self.assertNotEqual(k1, k2)

    def test_quoted_name_preserves_case(self):
        d1 = IDENT_MOD.ObjectDescriptor(kind='table', schema='public', name='"Orders"')
        d2 = IDENT_MOD.ObjectDescriptor(kind='table', schema='public', name='orders')
        k1 = IDENT_MOD.canonical_key(d1)
        k2 = IDENT_MOD.canonical_key(d2)
        # Quoted: Orders (uppercase) vs orders (lowercase)
        self.assertNotEqual(k1, k2)

    def test_quoted_both_schema_and_name(self):
        d = IDENT_MOD.ObjectDescriptor(kind='table', schema='"Public"', name='"Orders"')
        key = IDENT_MOD.canonical_key(d)
        self.assertEqual(key, 'table+Public+Orders')


if __name__ == '__main__':
    unittest.main()
