"""Tests for sql_extract.py and validation_plan.py — independent SQL analysis."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'

# Ensure scripts directory is in path for sibling imports
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Load sql_extract
_SQL_PATH = SCRIPTS_DIR / 'sql_extract.py'
_SQL_SPEC = importlib.util.spec_from_file_location('sql_extract', _SQL_PATH)
SQL_MOD = importlib.util.module_from_spec(_SQL_SPEC)
_SQL_SPEC.loader.exec_module(SQL_MOD)

# Load check_policy first (dependency of validation_plan)
_CP_PATH = SCRIPTS_DIR / 'check_policy.py'
_CP_SPEC = importlib.util.spec_from_file_location('check_policy', _CP_PATH)
CP_MOD = importlib.util.module_from_spec(_CP_SPEC)
_CP_SPEC.loader.exec_module(CP_MOD)

# Load validation_plan (uses check_policy)
_VP_PATH = SCRIPTS_DIR / 'validation_plan.py'
_VP_SPEC = importlib.util.spec_from_file_location('validation_plan', _VP_PATH)
VP_MOD = importlib.util.module_from_spec(_VP_SPEC)
_VP_SPEC.loader.exec_module(VP_MOD)

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / 'examples'
POLICY_PATH = Path(__file__).resolve().parents[1] / 'references' / 'check-policy.json'


class ExtractObjectsTests(unittest.TestCase):
    def test_extract_function(self):
        sql = '''
CREATE OR REPLACE FUNCTION demo.load_missing()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO demo.missing_target (id, loaded_at)
    SELECT e.id, now() FROM demo_src.events AS e;
END;
$$;
'''
        objects = SQL_MOD.extract_objects(sql, 'test.sql', 'a' * 64)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, 'load_missing')
        self.assertEqual(objects[0].kind, 'function')
        self.assertEqual(objects[0].schema, 'demo')

    def test_extract_view(self):
        sql = '''
CREATE OR REPLACE VIEW demo.order_totals AS
SELECT id, price * quantity AS total FROM demo.orders;
'''
        objects = SQL_MOD.extract_objects(sql, 'test.sql', 'a' * 64)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, 'order_totals')
        self.assertEqual(objects[0].kind, 'view')

    def test_extract_two_objects(self):
        sql = '''
CREATE OR REPLACE VIEW demo.v1 AS SELECT 1;

CREATE OR REPLACE FUNCTION demo.f1()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    PERFORM demo.f2();
END;
$$;
'''
        objects = SQL_MOD.extract_objects(sql, 'test.sql', 'a' * 64)
        self.assertEqual(len(objects), 2)
        names = {o.name for o in objects}
        self.assertEqual(names, {'v1', 'f1'})


class ExtractOperationsTests(unittest.TestCase):
    def test_select_operation(self):
        sql = 'SELECT id, name FROM demo_src.users WHERE active = true;'
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        select_items = [i for i in items if i.kind == 'SELECT']
        self.assertGreater(len(select_items), 0)
        self.assertEqual(select_items[0].anchor['object_or_scope'], 'fn+test')

    def test_insert_with_reads(self):
        sql = '''
INSERT INTO demo.target (id, name)
SELECT id, name FROM demo_src.source;
'''
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        insert_items = [i for i in items if i.kind == 'INSERT']
        self.assertEqual(len(insert_items), 1)
        self.assertIn('demo.target', insert_items[0].writes)

    def test_update_with_where(self):
        sql = 'UPDATE demo.queue SET status = 1 WHERE id = 42;'
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        update_items = [i for i in items if i.kind == 'UPDATE']
        self.assertEqual(len(update_items), 1)
        self.assertIn('demo.queue', update_items[0].writes)

    def test_delete_operation(self):
        sql = 'DELETE FROM demo.old_data WHERE created < now() - interval \'1 year\';'
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        delete_items = [i for i in items if i.kind == 'DELETE']
        self.assertEqual(len(delete_items), 1)

    def test_merge_with_using(self):
        sql = '''
MERGE INTO demo.summary AS t
USING demo_stg.summary_tmp AS s ON t.id = s.id
WHEN MATCHED THEN UPDATE SET value = s.value
WHEN NOT MATCHED THEN INSERT (id, value) VALUES (s.id, s.value);
'''
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        merge_items = [i for i in items if i.kind == 'MERGE']
        self.assertEqual(len(merge_items), 1)
        self.assertIn('demo.summary', merge_items[0].writes)

    def test_perform_call(self):
        sql = 'PERFORM demo_audit.log_event(\'start\', p_id);'
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        perform_items = [i for i in items if i.kind == 'PERFORM']
        self.assertEqual(len(perform_items), 1)
        self.assertIn('demo_audit.log_event', perform_items[0].calls)

    def test_execute_dynamic(self):
        sql = "EXECUTE format('TRUNCATE TABLE %I.%I', 'demo_stg', 'events');"
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        exec_items = [i for i in items if i.kind == 'EXECUTE']
        self.assertEqual(len(exec_items), 1)
        self.assertTrue(exec_items[0].details.get('dynamic'))

    def test_cte_extraction(self):
        sql = '''
WITH active_users AS (
    SELECT id, name FROM demo_src.users WHERE active = true
)
SELECT * FROM active_users;
'''
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        cte_items = [i for i in items if i.kind == 'CTE']
        self.assertEqual(len(cte_items), 1)
        self.assertEqual(cte_items[0].details['cte_name'], 'active_users')

    def test_temp_table_extraction(self):
        sql = 'CREATE TEMP TABLE tmp_filtered (id bigint, name text) ON COMMIT DROP;'
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        temp_items = [i for i in items if i.kind == 'TEMP_TABLE']
        self.assertEqual(len(temp_items), 1)
        self.assertEqual(temp_items[0].details['table_name'], 'tmp_filtered')

    def test_sql_in_comment_not_extracted(self):
        sql = '''
-- SELECT id FROM demo.ignore_this;
SELECT real_col FROM demo.real_table;
'''
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        select_items = [i for i in items if i.kind == 'SELECT']
        # Should only find the real SELECT
        self.assertEqual(len(select_items), 1)

    def test_sql_in_string_not_extracted(self):
        sql = """
INSERT INTO demo.log (msg) VALUES ('SELECT * FROM fake;');
SELECT id FROM demo.real_table;
"""
        items, notes = SQL_MOD.extract_operations(sql, 'test.sql', 'a' * 64, 'fn+test')
        select_items = [i for i in items if i.kind == 'SELECT']
        self.assertEqual(len(select_items), 1)


class ExtractInventoryTests(unittest.TestCase):
    def test_example_01(self):
        sql_path = EXAMPLES_DIR / '01_no_target_ddl.sql'
        sql = sql_path.read_text(encoding='utf-8')
        sha = SQL_MOD.sha256_file(sql_path)
        result = SQL_MOD.extract_inventory(sql, str(sql_path), sha)
        self.assertEqual(result['schema_version'], 2)
        self.assertGreater(len(result['items']), 0)
        kinds = {i['kind'] for i in result['items']}
        self.assertIn('INSERT', kinds)
        self.assertIn('SELECT', kinds)

    def test_example_04_perform_merge(self):
        sql_path = EXAMPLES_DIR / '04_update_perform.sql'
        sql = sql_path.read_text(encoding='utf-8')
        sha = SQL_MOD.sha256_file(sql_path)
        result = SQL_MOD.extract_inventory(sql, str(sql_path), sha)
        kinds = {i['kind'] for i in result['items']}
        self.assertIn('PERFORM', kinds)
        self.assertIn('UPDATE', kinds)
        self.assertIn('MERGE', kinds)

    def test_example_07_dynamic(self):
        sql_path = EXAMPLES_DIR / '07_dynamic_sql.sql'
        sql = sql_path.read_text(encoding='utf-8')
        sha = SQL_MOD.sha256_file(sql_path)
        result = SQL_MOD.extract_inventory(sql, str(sql_path), sha)
        kinds = {i['kind'] for i in result['items']}
        self.assertIn('EXECUTE', kinds)

    def test_example_09_view(self):
        sql_path = EXAMPLES_DIR / '09_view_and_readonly.sql'
        sql = sql_path.read_text(encoding='utf-8')
        sha = SQL_MOD.sha256_file(sql_path)
        result = SQL_MOD.extract_inventory(sql, str(sql_path), sha)
        kinds = {i['kind'] for i in result['items']}
        self.assertIn('SELECT', kinds)


class GeneratePlanTests(unittest.TestCase):
    def setUp(self):
        self.policy = CP_MOD.load_policy(POLICY_PATH)

    def test_plan_has_required_checks(self):
        inventory = {
            'schema_version': 2,
            'run_id': 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            'dialect': {'name': 'postgres', 'version': 'unknown'},
            'items': [
                {
                    'anchor': {'object_or_scope': 'fn+test', 'construct': 'SELECT', 'ordinal': 1},
                    'kind': 'SELECT',
                    'source_ref': {'path': 'test.sql', 'start_line': 5, 'end_line': 5, 'sha256': 'a' * 64},
                    'reads': ['demo_src.events'],
                }
            ],
            'inputs': [{'path': 'test.sql', 'sha256': 'a' * 64}],
            'documented_subjects': ['fn+test'],
        }
        plan = VP_MOD.generate_plan(inventory, self.policy, page_id='fn+test')
        self.assertEqual(plan['schema_version'], 2)
        self.assertGreater(len(plan['required_checks']), 0)
        check_ids = {c['rule_id'] for c in plan['required_checks']}
        self.assertIn('identity', check_ids)
        self.assertIn('operation', check_ids)

    def test_plan_for_table_has_columns_check(self):
        inventory = SQL_MOD.extract_inventory('CREATE TABLE demo.t (id int);', 'test.sql', 'a' * 64)
        plan = VP_MOD.generate_plan(inventory, self.policy, page_id='table+test',
                                     object_kind='table')
        check_ids = {c['rule_id'] for c in plan['required_checks']}
        self.assertIn('columns', check_ids)
        self.assertNotIn('signature', check_ids)

    def test_plan_preserves_run_id(self):
        inventory = SQL_MOD.extract_inventory('CREATE VIEW demo.v AS SELECT 1;', 'test.sql', 'a' * 64)
        inventory['run_id'] = 'b1b2c3d4-e5f6-7890-abcd-ef1234567890'
        plan = VP_MOD.generate_plan(inventory, self.policy)
        self.assertEqual(plan['run_id'], 'b1b2c3d4-e5f6-7890-abcd-ef1234567890')


class CommandLineTests(unittest.TestCase):
    def test_sql_extract_cli(self):
        sql_path = EXAMPLES_DIR / '01_no_target_ddl.sql'
        import subprocess
        result = subprocess.run(
            ['python', str(_SQL_PATH), str(sql_path)],
            capture_output=True, text=True, cwd=str(SCRIPTS_DIR.parent)
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output['schema_version'], 2)


if __name__ == '__main__':
    unittest.main()
