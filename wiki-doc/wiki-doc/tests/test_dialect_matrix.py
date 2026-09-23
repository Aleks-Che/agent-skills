"""Tests for P2-04: dialect support matrix.

Verifies dialect/version propagation through the pipeline:
- sql_ast / sql_extract: dialect notes, MERGE version gating
- ddl: migration manifest dialect check
- validation_gate: blocks on unsupported dialect coverage_notes
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sql_extract import extract_inventory
from sql_ast import analyze
from ddl import reconstruct
from build_bundle import build
from check_policy import load_policy
from validation_plan import generate_plan
from validation_gate import evaluate_bundle
from bundle_fixture import make_bundle, seal, write_json, PAGE_ID
from artifact_schema import read_json
from evidence import sha256_file


EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'

MERGE_SQL = '''
CREATE FUNCTION demo.merge_fn() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    MERGE INTO demo.target t
    USING demo_src.source s ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET val = s.val
    WHEN NOT MATCHED THEN INSERT (id, val) VALUES (s.id, s.val);
END;
$$;
'''

SIMPLE_SELECT_SQL = 'CREATE FUNCTION demo.f() RETURNS int LANGUAGE sql AS $$ SELECT 1; $$;'


def dynamic_merge_sql(format_call=False):
    command = ('MERGE INTO demo.target t USING demo_src.source s ON t.id = s.id '
               'WHEN MATCHED THEN UPDATE SET val = s.val')
    expression = ("format('" + command.replace('demo.target', '%I.%I') + "', 'demo', 'target')"
                  if format_call else "'" + command + "'")
    return ('CREATE FUNCTION demo.merge_fn() RETURNS void LANGUAGE plpgsql AS $$ '
            'BEGIN EXECUTE ' + expression + '; END; $$;')


class DialectInventoryTests(unittest.TestCase):
    """Verify dialect metadata propagation in inventory."""

    def _inv(self, sql=SIMPLE_SELECT_SQL, dialect='postgres', version='15'):
        return extract_inventory(sql, 'test.sql',
                                hashlib.sha256(sql.encode()).hexdigest(),
                                dialect=dialect, version=version)

    def test_postgres_dialect_no_note(self):
        inv = self._inv()
        self.assertEqual(inv['dialect']['name'], 'postgres')
        self.assertEqual(inv['dialect']['version'], '15')
        notes = [n for n in inv['coverage_notes'] if 'dialect' in n['reason'].lower()]
        self.assertEqual(notes, [])

    def test_postgresql_alias_no_note(self):
        inv = self._inv(dialect='postgresql')
        self.assertEqual(inv['dialect']['name'], 'postgresql')
        notes = [n for n in inv['coverage_notes'] if 'dialect' in n['reason'].lower()]
        self.assertEqual(notes, [])

    def test_mysql_dialect_creates_note(self):
        inv = self._inv(dialect='mysql')
        self.assertEqual(inv['dialect']['name'], 'mysql')
        notes = [n for n in inv['coverage_notes'] if 'Unsupported dialect' in n['reason']]
        self.assertTrue(notes, 'Expected unsupported dialect note for mysql')

    def test_greenplum_dialect_creates_note(self):
        inv = self._inv(dialect='greenplum')
        self.assertEqual(inv['dialect']['name'], 'greenplum')
        notes = [n for n in inv['coverage_notes'] if 'Unsupported dialect' in n['reason']]
        self.assertTrue(notes, 'Expected unsupported dialect note for greenplum')

    def test_unknown_dialect_creates_note(self):
        for dialect in ('oracle', 'mssql', 'sqlite', 'mariadb', 'unknown'):
            with self.subTest(dialect=dialect):
                inv = self._inv(dialect=dialect)
                self.assertTrue(any('Unsupported dialect: ' + dialect in n['reason']
                                    for n in inv['coverage_notes']))

    def test_version_stored_in_inventory(self):
        for v in ('14', '15', '16', '17', 'unknown'):
            with self.subTest(version=v):
                inv = self._inv(version=v)
                self.assertEqual(inv['dialect']['version'], v)

    def test_greenplum_specific_syntax_blocks_analysis(self):
        """GP-only clauses are not parseable by libpg_query under any declared dialect."""
        clauses = (
            'CREATE TABLE demo.t (a int) DISTRIBUTED BY (a);',
            'CREATE FUNCTION demo.f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE SQL EXECUTE ON MASTER;',
        )
        for dialect in ('greenplum', 'postgres'):
            for sql in clauses:
                with self.subTest(dialect=dialect, sql=sql):
                    inv = extract_inventory(sql, 'gp.sql',
                                            hashlib.sha256(sql.encode()).hexdigest(),
                                            dialect=dialect, version='unknown')
                    reasons = [n['reason'] for n in inv['coverage_notes']]
                    self.assertTrue(any('analysis failed' in r.lower() for r in reasons),
                                    f'Expected AST parse failure, got {reasons}')


class MergeVersionGatingTests(unittest.TestCase):
    """Verify MERGE requires version >= 15."""

    def _notes(self, version):
        inv = extract_inventory(MERGE_SQL, 'merge.sql',
                                hashlib.sha256(MERGE_SQL.encode()).hexdigest(),
                                version=version)
        return inv['coverage_notes']

    def test_merge_allowed_at_15(self):
        notes = self._notes('15')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertEqual(merge_notes, [])

    def test_merge_allowed_at_16(self):
        notes = self._notes('16')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertEqual(merge_notes, [])

    def test_merge_blocked_at_14(self):
        notes = self._notes('14')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertTrue(merge_notes, 'Expected MERGE version note for v14')
        self.assertIn('15', merge_notes[0]['reason'])

    def test_merge_blocked_at_13(self):
        notes = self._notes('13')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertTrue(merge_notes)

    def test_merge_blocked_at_unknown(self):
        notes = self._notes('unknown')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertTrue(merge_notes, 'Expected MERGE version note for unknown')

    def test_merge_blocked_at_12(self):
        notes = self._notes('12')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertTrue(merge_notes)

    def test_merge_allowed_at_17(self):
        notes = self._notes('17')
        merge_notes = [n for n in notes if 'MERGE' in n['reason']]
        self.assertEqual(merge_notes, [])

    def test_dynamic_merge_version_gating(self):
        for format_call in (False, True):
            for version in ('14', 'unknown', '15', '16.3', '17'):
                with self.subTest(format=format_call, version=version):
                    sql = dynamic_merge_sql(format_call)
                    inv = extract_inventory(sql, 'dynamic.sql', hashlib.sha256(sql.encode()).hexdigest(),
                                            version=version)
                    execute = next(i for i in inv['items'] if i['kind'] == 'EXECUTE')
                    self.assertEqual(execute['details']['command_kind'], 'MERGE')
                    if version in ('14', 'unknown'):
                        self.assertTrue(any('MERGE requires' in n['reason'] for n in inv['coverage_notes']))
                    else:
                        self.assertEqual(inv['coverage_notes'], [])


class AstDialectTests(unittest.TestCase):
    """Verify dialect handling directly in sql_ast.analyze."""

    def _analyze(self, sql=SIMPLE_SELECT_SQL, dialect='postgres', version='15'):
        return analyze(sql, 'test.sql', hashlib.sha256(sql.encode()).hexdigest(),
                       dialect=dialect, version=version)

    def test_ast_dialect_field(self):
        result = self._analyze()
        self.assertEqual(result['dialect']['name'], 'postgres')
        self.assertEqual(result['dialect']['version'], '15')

    def test_ast_unsupported_dialect_note(self):
        result = self._analyze(dialect='mssql')
        notes = [n for n in result['coverage_notes'] if 'Unsupported dialect' in n['reason']]
        self.assertTrue(notes)
        self.assertIn('mssql', notes[0]['reason'])

    def test_ast_merge_version_note(self):
        result = self._analyze(sql=MERGE_SQL, version='14')
        notes = [n for n in result['coverage_notes'] if 'MERGE' in n['reason']]
        self.assertTrue(notes)

    def test_ast_merge_no_note_at_15(self):
        result = self._analyze(sql=MERGE_SQL, version='15')
        notes = [n for n in result['coverage_notes'] if 'MERGE' in n['reason']]
        self.assertEqual(notes, [])

    def test_ast_dialect_case_insensitive(self):
        for d in ('Postgres', 'POSTGRES', 'PostgreSQL', 'POSTGRESQL'):
            with self.subTest(dialect=d):
                result = self._analyze(dialect=d)
                notes = [n for n in result['coverage_notes'] if 'Unsupported dialect' in n['reason']]
                self.assertEqual(notes, [], f'{d} should be accepted')


class MigrationDialectTests(unittest.TestCase):
    """Verify migration manifest dialect check."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(EXAMPLES / 'migrations', self.root / 'migrations')
        shutil.copytree(EXAMPLES / 'baseline', self.root / 'baseline')
        shutil.copy2(EXAMPLES / '08_alter_migration.sql', self.root)
        self.manifest_path = self.root / 'migrations' / 'manifest.json'

    def reconstruct_with_dialect(self, dialect):
        manifest = read_json(self.manifest_path)
        manifest['dialect'] = dialect
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        return reconstruct(self.manifest_path, project_root=self.root)

    def test_migration_unsupported_dialect(self):
        result = self.reconstruct_with_dialect('mysql')
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['inputs'], [])

    def test_migration_postgres_accepted(self):
        result = self.reconstruct_with_dialect('postgres')
        self.assertEqual(result['status'], 'resolved')

    def test_migration_postgresql_accepted(self):
        result = self.reconstruct_with_dialect('postgresql')
        self.assertEqual(result['status'], 'resolved')

    def test_migration_dialect_case_insensitive(self):
        for dialect in ('Postgres', 'POSTGRES', 'PostgreSQL', 'POSTGRESQL'):
            with self.subTest(dialect=dialect):
                self.assertEqual(self.reconstruct_with_dialect(dialect)['status'], 'resolved')

    def test_column_comment_in_ordered_migration(self):
        source = self.root / 'comments.sql'
        source.write_text("CREATE TABLE demo.t (id int); COMMENT ON COLUMN demo.t.id IS 'Identifier';",
                          encoding='utf-8')
        manifest = {'dialect': 'postgres', 'version': '15', 'ordered_files': ['../comments.sql']}
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        result = reconstruct(self.manifest_path, project_root=self.root)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['tables']['demo.t']['columns'][0]['comment'], 'Identifier')


class GateDialectTests(unittest.TestCase):
    """Verify gate blocks on dialect-related coverage_notes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        make_bundle(self.run)

    def test_valid_bundle_ready(self):
        result = evaluate_bundle(self.run)
        self.assertEqual(result['decision'], 'ready')

    def test_unsupported_dialect_in_inventory_blocks_gate(self):
        for dialect in ('mysql', 'greenplum', 'oracle', 'mssql', 'sqlite'):
            with self.subTest(dialect=dialect):
                self.rebuild_inventory(dialect=dialect)
                self.assert_analysis_block(seal(self.run), 'Unsupported dialect: ' + dialect)

    def test_merge_version_unknown_blocks_gate(self):
        source = self.run / 'merge.sql'
        source.write_text(MERGE_SQL, encoding='utf-8')
        for version in ('14', 'unknown'):
            with self.subTest(version=version):
                result = build(source, self.run / version, project_root=self.run,
                               subject='demo.merge_fn', version=version)
                self.assert_analysis_block(result, 'MERGE requires a confirmed PostgreSQL version >= 15')

    def test_merge_supported_version_ready(self):
        source = self.run / 'merge.sql'
        source.write_text(MERGE_SQL, encoding='utf-8')
        result = build(source, self.run / 'merge', project_root=self.run,
                       subject='demo.merge_fn', version='15')
        self.assertEqual(result['decision'], 'ready', result)
        self.assertTrue(result['publication_authorized'])

    def test_dynamic_merge_gate_uses_source_version(self):
        source = self.run / 'dynamic.sql'
        for format_call in (False, True):
            source.write_text(dynamic_merge_sql(format_call), encoding='utf-8')
            for version in ('14', 'unknown', '15'):
                with self.subTest(format=format_call, version=version):
                    result = build(source, self.run / f'dynamic-{format_call}-{version}',
                                   project_root=self.run, subject='demo.merge_fn', version=version)
                    if version == '15':
                        self.assertTrue(result['publication_authorized'], result)
                    else:
                        self.assert_analysis_block(result, 'MERGE requires a confirmed PostgreSQL version >= 15')

    def test_unknown_version_without_merge_remains_ready(self):
        self.rebuild_inventory(version='unknown')
        result = seal(self.run)
        self.assertEqual(result['decision'], 'ready', result)
        self.assertTrue(result['publication_authorized'])

    def test_removed_dialect_note_is_rebuilt_by_gate(self):
        self.rebuild_inventory(dialect='mysql')
        inv = read_json(self.run / 'inventory.json')
        inv['coverage_notes'] = []
        write_json(self.run, 'inventory', inv)
        write_json(self.run, 'validation_plan', generate_plan(inv, load_policy(), page_id=PAGE_ID))
        self.assert_analysis_block(seal(self.run), 'Unsupported dialect: mysql')

    def rebuild_inventory(self, dialect='postgres', version='15'):
        source = self.run / 'source.sql'
        facts = read_json(self.run / 'facts.json')
        inv = extract_inventory(source.read_text(encoding='utf-8-sig'), 'source.sql', sha256_file(source),
                                dialect=dialect, version=version, documented_subjects=['core.calc'])
        inv['run_id'] = facts['run_id']
        facts['dialect'] = inv['dialect']
        write_json(self.run, 'facts', facts)
        write_json(self.run, 'inventory', inv)
        write_json(self.run, 'validation_plan', generate_plan(inv, load_policy(), page_id=PAGE_ID))

    def assert_analysis_block(self, result, reason):
        self.assertEqual(result['decision'], 'blocked', result)
        self.assertFalse(result['publication_authorized'])
        self.assertFalse(result['input_error'], result)
        self.assertEqual(result['errors'], ['analysis gap: ' + reason])


class DialectExampleTests(unittest.TestCase):
    """Verify all 12 examples parse with their declared dialect/version."""

    def test_all_examples_parse_cleanly(self):
        cases = json.loads((EXAMPLES / 'cases.json').read_text(encoding='utf-8'))
        for case in cases['cases']:
            with self.subTest(case=case['id']):
                sql_path = EXAMPLES / case['sql']
                sql_text = sql_path.read_text(encoding='utf-8-sig')
                version = case.get('version', 'unknown')
                inv = extract_inventory(sql_text, case['sql'],
                                        hashlib.sha256(sql_text.encode()).hexdigest(),
                                        dialect=case.get('dialect', 'postgres'), version=version)
                self.assertEqual(inv['dialect']['name'], case.get('dialect', 'postgres'))
                self.assertEqual(inv['dialect']['version'], version)
                self.assertEqual(inv['coverage_notes'], [])
                self.assertTrue(any(i['kind'] == 'DECLARATION' for i in inv['items']))

    def test_case_11_unknown_version(self):
        p = EXAMPLES / '11_audit_access.sql'
        sql_text = p.read_text(encoding='utf-8-sig')
        inv = extract_inventory(sql_text, '11_audit_access.sql',
                                hashlib.sha256(sql_text.encode()).hexdigest(),
                                version='unknown')
        self.assertEqual(inv['dialect']['version'], 'unknown')
        self.assertEqual(inv['dialect']['name'], 'postgres')
        self.assertEqual(inv['coverage_notes'], [])


    def test_case_04_merge_at_15(self):
        p = EXAMPLES / '04_update_perform.sql'
        sql_text = p.read_text(encoding='utf-8-sig')
        inv = extract_inventory(sql_text, '04_update_perform.sql',
                                hashlib.sha256(sql_text.encode()).hexdigest(),
                                version='15')
        self.assertEqual(inv['coverage_notes'], [])
        self.assertTrue(any(i['kind'] == 'MERGE' for i in inv['items']))


class MatrixConstructTests(unittest.TestCase):
    """Pin the bounded behavior of matrix rows without a numbered SQL example."""

    def inventory(self, sql):
        inv = extract_inventory(sql, 'matrix.sql', hashlib.sha256(sql.encode()).hexdigest(), version='15')
        self.assertEqual(inv['coverage_notes'], [])
        return inv

    def test_procedure_and_call(self):
        inv = self.inventory('CREATE PROCEDURE demo.p(IN p int) LANGUAGE sql AS $$ CALL demo.other(p); $$;')
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')['details']
        self.assertEqual(declaration['object_kind'], 'procedure')
        self.assertEqual(declaration['input_types'], ['integer'])
        call = next(i for i in inv['items'] if i['kind'] == 'CALL')
        self.assertEqual(call['calls'], ['demo.other'])
        checks = generate_plan(inv, load_policy())['required_checks']
        self.assertTrue(any(c['rule_id'] == 'operation'
                            and c.get('inventory_anchor', {}).get('construct') == 'CALL' for c in checks))

    def test_materialized_view_outputs_and_dependencies(self):
        inv = self.inventory('CREATE MATERIALIZED VIEW demo.mv AS SELECT id FROM demo.t;')
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')['details']
        self.assertEqual(declaration['object_kind'], 'materialized_view')
        self.assertEqual(declaration['output_columns'], [{'name': 'id', 'expression': 'id'}])
        query = next(i for i in inv['items'] if i['kind'] == 'SELECT')
        self.assertEqual(query['reads'], ['demo.t'])
        self.assertEqual(query['details']['result_for'], 'demo.mv')

    def test_constant_execute_retains_template_and_source(self):
        command = 'INSERT INTO demo.t (id) SELECT id FROM demo.s'
        inv = self.inventory("CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN EXECUTE '"
                             + command + "'; END; $$;")
        operation = next(i for i in inv['items'] if i['kind'] == 'EXECUTE')
        self.assertEqual(operation['details']['template'], command)
        self.assertEqual(operation['details']['command_kind'], 'INSERT')
        self.assertEqual(operation['reads'], ['demo.s'])

if __name__ == '__main__':
    unittest.main()
