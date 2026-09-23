"""Q-02: diagnostics replace the legacy-fallback crash.

Both paths must be safe:
  A. PostgreSQL AST rejects the input (Greenplum attribute) -> legacy fallback
     runs and must NOT raise 'Unbalanced SQL list' / exit 2. Before Q-03 the
     input stays blocked with an explicit coverage note.
  B. PostgreSQL AST succeeds but the auxiliary legacy scan fails -> the native
     result is kept, not an empty successful inventory.

Malformed inputs (ambiguous --subjects) are NOT unsupported constructs: they
keep the documented CLI exit code 2 and are never laundered into a ready
inventory. Deleting coverage_notes from a saved file does not unblock the
re-analysis gate.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'scripts'
EXAMPLES = PACKAGE / 'examples'
FIXTURES = EXAMPLES / 'fixtures'
sys.path.insert(0, str(SCRIPTS))

from sql_extract import (  # noqa: E402
    _extract_inventory_legacy, _split_at_depth_zero, _trim_trailing_closers,
    extract_inventory, extract_operations, sha256_file,
)
from sql_syntax import SubjectSelectionError
from artifact_schema import read_json
from bundle_fixture import make_bundle, seal, write_json

GP_NESTED = (FIXTURES / 'q05_gp_master_nested.sql').read_text(encoding='utf-8-sig')
GP_NESTED_SHA = sha256_file(FIXTURES / 'q05_gp_master_nested.sql')

# Minimal repro of the historical crash: GP attribute forces the fallback and
# the nested WHERE sits inside the outer FROM tail.
CRASH_MIN = """
CREATE OR REPLACE FUNCTION demo.repro()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n
    FROM (SELECT id FROM demo_src.events WHERE message = 'x') AS s;
    RETURN n;
END;
$$ LANGUAGE plpgsql EXECUTE ON MASTER;
"""

CRASH_MIN_GP_TABLE = """
CREATE TABLE demo.t (a int) DISTRIBUTED BY (a);
"""

CRASH_MIN_GP_TABLE_BAD = """
CREATE TABLE demo.t (a int) DISTRIBUTED;
"""

CRASH_UNKNOWN_GP = """
CREATE OR REPLACE FUNCTION demo.repro()
RETURNS bigint LANGUAGE plpgsql AS $$
BEGIN
    RETURN 1;
END;
$$ LANGUAGE plpgsql EXECUTE ON COORDINATOR;
"""


class DepthAwareSplitTests(unittest.TestCase):
    def test_nested_where_is_not_a_boundary(self):
        tail = "(SELECT id FROM demo_src.events WHERE message = 'x') AS s"
        self.assertEqual(_split_at_depth_zero(tail, ('WHERE', 'GROUP', 'ORDER')), len(tail))

    def test_top_level_where_is_still_a_boundary(self):
        tail = 'demo.orders AS o WHERE o.id > 0'
        self.assertEqual(_split_at_depth_zero(tail, ('WHERE', 'GROUP', 'ORDER')),
                         tail.index('WHERE'))

    def test_nested_then_top_level_boundary(self):
        tail = '(SELECT 1 FROM t WHERE x) AS s ORDER BY 1'
        self.assertEqual(_split_at_depth_zero(tail, ('WHERE', 'GROUP', 'ORDER')),
                         tail.index('ORDER'))


class TrailingCloserTests(unittest.TestCase):
    def test_overshoot_closer_is_trimmed(self):
        self.assertEqual(_trim_trailing_closers('(SELECT 1 FROM t) AS s )'),
                         '(SELECT 1 FROM t) AS s')

    def test_balanced_text_is_untouched(self):
        self.assertEqual(_trim_trailing_closers('(SELECT 1 FROM t) AS s'),
                         '(SELECT 1 FROM t) AS s')

    def test_only_trailing_closers_are_trimmed(self):
        self.assertEqual(_trim_trailing_closers(') AS s'), ') AS s')
        self.assertEqual(_trim_trailing_closers('a AS s ))'), 'a AS s')


class LegacyFallbackSafetyTests(unittest.TestCase):
    def test_unbalanced_list_no_longer_escapes(self):
        body = "SELECT count(*) INTO n FROM (SELECT id FROM t WHERE x = 1) AS s;"
        items, notes = extract_operations(body, 'x.sql', '0' * 64, 'scope')
        self.assertTrue(items)
        self.assertFalse([n for n in notes if n.reason == CRASH_ERROR])
        self.assertFalse([n for n in notes if 'split failed' in n.reason])

    def test_malformed_source_list_becomes_a_blocking_note(self):
        body = 'SELECT 1 FROM (SELECT 2 FROM t WHERE x'
        items, notes = extract_operations(body, 'x.sql', '0' * 64, 'scope')
        joined = ' | '.join(n.reason for n in notes)
        self.assertNotEqual(joined, '')
        self.assertTrue(any('split failed' in n.reason or 'Unbalanced' in n.reason
                            or 'Unclosed' in n.reason for n in notes), joined)

    def test_legacy_on_gp_nested_input_does_not_raise(self):
        inventory = _extract_inventory_legacy(
            GP_NESTED, 'fixtures/q05_gp_master_nested.sql', GP_NESTED_SHA,
            dialect='greenplum', version='unknown')
        self.assertEqual(inventory['schema_version'], 2)
        self.assertTrue(inventory['items'])
        reasons = ' | '.join(n['reason'] for n in inventory['coverage_notes'])
        self.assertIn('Unsupported dialect: greenplum', reasons)

    def test_legacy_on_minimal_repro_does_not_raise(self):
        inventory = _extract_inventory_legacy(
            CRASH_MIN, 'repro.sql', '1' * 64, dialect='greenplum', version='unknown')
        self.assertTrue(inventory['items'])
        kinds = {i['kind'] for i in inventory['items']}
        self.assertIn('SELECT', kinds)

    def test_auxiliary_value_error_after_ast_failure_is_a_gap(self):
        with patch('sql_extract._extract_inventory_legacy', side_effect=ValueError('list failed')):
            inventory = extract_inventory(CRASH_MIN, 'repro.sql', '6' * 64,
                                          dialect='postgres')
        self.assertEqual(inventory['items'][0]['kind'], 'ANALYSIS_GAP')
        reasons = [note['reason'] for note in inventory['coverage_notes']]
        self.assertTrue(any('PostgreSQL AST analysis failed:' in reason for reason in reasons))
        self.assertIn('Legacy analysis failed: list failed', reasons)

    def test_native_subject_error_is_not_reinterpreted_by_legacy(self):
        with patch('sql_extract._extract_inventory_legacy') as legacy:
            with self.assertRaises(SubjectSelectionError):
                extract_inventory('CREATE VIEW demo.v AS SELECT 1;', 'x.sql', '7' * 64,
                                  documented_subjects=['nope.missing'])
        legacy.assert_not_called()

    def test_native_quoted_subject_does_not_depend_on_legacy_selection(self):
        inventory = extract_inventory('CREATE VIEW "demo"."v" AS SELECT 1;', 'x.sql', '8' * 64,
                                      documented_subjects=['demo.v'])
        self.assertEqual(inventory['coverage_notes'], [])
        self.assertEqual(inventory['documented_subjects'], ['view+demo+v'])


class DualPathTests(unittest.TestCase):
    def test_path_a_ast_failure_falls_back_without_crash(self):
        # postgres: the GP attribute is still a parse failure (dialect rules unchanged).
        # greenplum: an unknown EXECUTE ON target keeps the AST failure path alive.
        cases = (('postgres', CRASH_MIN), ('greenplum', CRASH_UNKNOWN_GP))
        for dialect, sql in cases:
            inventory = extract_inventory(sql, 'repro.sql', '2' * 64,
                                         dialect=dialect, version='unknown')
            reasons = ' | '.join(n['reason'] for n in inventory['coverage_notes'])
            self.assertTrue(inventory['coverage_notes'], dialect)
            self.assertRegex(reasons, r'(?i)analysis failed')
            self.assertFalse(any('Unbalanced' in n['reason']
                                 for n in inventory['coverage_notes']), reasons)

    def test_path_a_greenplum_attribute_is_handled_by_adapter_not_fallback(self):
        inventory = extract_inventory(CRASH_MIN, 'repro.sql', '2' * 64,
                                      dialect='greenplum', version='6.25.3')
        self.assertEqual(inventory['coverage_notes'], [])
        declaration = next(i for i in inventory['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details']['execute_on'], 'MASTER')
        self.assertFalse([i for i in inventory['items'] if i['kind'] == 'EXECUTE'])

    def test_path_a_gp_table_context_parses_with_adapter(self):
        inventory = extract_inventory(CRASH_MIN_GP_TABLE, 't.sql', '3' * 64,
                                     dialect='greenplum', version='6.25.3')
        self.assertEqual(inventory['coverage_notes'], [])
        create = next(i for i in inventory['items'] if i['kind'] == 'CREATE')
        self.assertEqual(create['details']['distributed'], {'mode': 'BY', 'columns': ['a']})

    def test_path_a_unknown_gp_table_extension_stays_blocked(self):
        inventory = extract_inventory(CRASH_MIN_GP_TABLE_BAD, 't.sql', '3' * 64,
                                     dialect='greenplum', version='6.25.3')
        self.assertTrue(inventory['coverage_notes'])
        self.assertFalse(any(n['reason'] == '' for n in inventory['coverage_notes']))
        self.assertTrue(any('DISTRIBUTED' in n['reason'] for n in inventory['coverage_notes']))

    def test_path_b_native_success_survives_legacy_failure(self):
        """AST succeeds; a broken auxiliary legacy scan must not discard native."""
        source = (EXAMPLES / 'fixtures' / 'q02_null_check_sign.sql').read_text(encoding='utf-8-sig')
        real = _extract_inventory_legacy

        def exploding(*args, **kwargs):
            raise RuntimeError('auxiliary legacy scan exploded')

        import sql_extract
        sql_extract._extract_inventory_legacy = exploding
        try:
            inventory = extract_inventory(source, 'q02.sql', '4' * 64,
                                          dialect='postgres', version='15')
        finally:
            sql_extract._extract_inventory_legacy = real
        self.assertEqual(inventory['schema_version'], 2)
        kinds = {i['kind'] for i in inventory['items']}
        self.assertIn('DECLARATION', kinds)
        self.assertIn('SELECT', kinds)
        self.assertEqual(inventory['coverage_notes'][-1], {
            'source_ref': {'path': 'q02.sql', 'sha256': '4' * 64,
                           'start_line': 1, 'end_line': len(source.splitlines())},
            'reason': 'Legacy analysis failed: auxiliary legacy scan exploded',
        })

    def test_path_b_value_error_from_legacy_still_reports_input_errors(self):
        source = (EXAMPLES / 'fixtures' / 'q02_null_check_sign.sql').read_text(encoding='utf-8-sig')
        with self.assertRaises(SubjectSelectionError):
            extract_inventory(source, 'q02.sql', '5' * 64, dialect='postgres',
                              version='15', documented_subjects=['nope.missing'])
        with patch('sql_extract.extract_operations', side_effect=ValueError('operation scan failed')):
            inventory = extract_inventory(source, 'q02.sql', '5' * 64, version='15')
        self.assertTrue(any(i['kind'] == 'SELECT' for i in inventory['items']))
        note = next(n for n in inventory['coverage_notes']
                    if n['reason'] == 'Legacy operation scan failed: operation scan failed')
        self.assertEqual(note['source_ref']['path'], 'q02.sql')
        self.assertEqual(note['source_ref']['sha256'], '5' * 64)
        self.assertLessEqual(note['source_ref']['start_line'], note['source_ref']['end_line'])


CRASH_ERROR = 'Unbalanced SQL list'


def crashed(output):
    """True only for the historical undiagnosed crash, not for a local note."""
    try:
        payload = json.loads(output)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get('error') == CRASH_ERROR


class CliContractTests(unittest.TestCase):
    def run_cli(self, sql_path, extra=()):
        command = [sys.executable, '-B', str(SCRIPTS / 'sql_extract.py'),
                   str(sql_path), *extra]
        return subprocess.run(command, cwd=str(PACKAGE), capture_output=True,
                              text=True, timeout=120)

    def test_q05_cli_no_undiagnosed_crash(self):
        completed = self.run_cli(FIXTURES / 'q05_gp_master_nested.sql',
                                 ('--dialect', 'greenplum', '--version', 'unknown'))
        self.assertFalse(crashed(completed.stderr), completed.stderr)
        self.assertFalse(crashed(completed.stdout))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['coverage_notes'], [])
        self.assertTrue(payload['items'])
        declaration = next(i for i in payload['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details']['execute_on'], 'MASTER')
        self.assertFalse([i for i in payload['items'] if i['kind'] == 'EXECUTE'])

    def test_q05_cli_blocks_with_an_explicit_reason(self):
        completed = self.run_cli(FIXTURES / 'q05_gp_master_nested.sql',
                                 ('--dialect', 'greenplum', '--version', 'unknown'))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['documented_subjects'],
                         ['function+q_out+gp_master_probe+(text)'])
        reads = {r for i in payload['items'] for r in i.get('reads', [])}
        self.assertIn('q_src.events', reads)

    def test_gp_distributed_context_file_does_not_crash_cli(self):
        completed = self.run_cli(FIXTURES / 'q05_gp_master_nested.sql',
                                 ('--dialect', 'greenplum',
                                  '--context', str(FIXTURES / 'q_context_gp.sql')))
        self.assertFalse(crashed(completed.stderr), completed.stderr)
        self.assertFalse(crashed(completed.stdout))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse([n for n in payload['coverage_notes'] if 'DDL parse failed' in n['reason']])
        declaration = next(i for i in payload['items'] if i['kind'] == 'DECLARATION')
        tables = declaration['details']['context_tables']
        self.assertEqual(tables['q_src.events']['distributed'],
                         {'mode': 'BY', 'columns': ['id']})

    def test_unknown_gp_context_file_yields_localized_ddl_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'gp.sql'
            shutil.copy2(FIXTURES / 'q05_gp_master_nested.sql', source)
            broken = root / 'ctx.sql'
            broken.write_text('CREATE TABLE demo.x (a int) DISTRIBUTED BY (a+\n', encoding='utf-8')
            completed = self.run_cli(source, ('--dialect', 'greenplum',
                                              '--context', str(broken),
                                              '--project-root', str(root)))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        reasons = ' | '.join(n['reason'] for n in payload['coverage_notes'])
        self.assertRegex(reasons, r'(?i)(DDL parse failed|DISTRIBUTED)')

    def test_broken_sql_does_not_get_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'broken.sql'
            broken.write_text('CREATE VIEW demo.v AS SELECT FROM;', encoding='utf-8')
            completed = self.run_cli(broken, ('--dialect', 'postgres'))
            self.assertFalse(crashed(completed.stderr))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload['coverage_notes'],
                            'broken SQL must keep blocking coverage notes')

    def test_malformed_input_keeps_documented_exit_code_2(self):
        completed = self.run_cli(FIXTURES / 'q02_null_check_sign.sql',
                                 ('--dialect', 'postgres', '--subjects', 'nope.missing'))
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertIn("Subject 'nope.missing': expected one declaration, found 0", payload['error'])
        self.assertNotEqual(payload['error'], CRASH_ERROR)

    def test_removing_coverage_notes_does_not_unblock_gate(self):
        """Reseal forged artifacts: refusal must come from real source reanalysis."""
        for mutation in ('greenplum_nested', 'broken_sql'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                make_bundle(run)  # Mandatory positive ready control.
                source = run / 'source.sql'
                text = source.read_text(encoding='utf-8')
                if mutation == 'greenplum_nested':
                    text = text.replace('FROM demo.orders',
                                        'FROM (SELECT amount FROM demo.orders WHERE amount > 0) AS s')
                    text = text.replace('$$;', '$$ EXECUTE ON MASTER;')
                else:
                    text = text.replace('sum(amount * 1.1)', 'FROM')
                source.write_text(text, encoding='utf-8')
                digest = sha256_file(source)
                fresh = extract_inventory(source.read_bytes().decode('utf-8-sig'), 'source.sql', digest,
                                          documented_subjects=['core.calc'])
                self.assertTrue(fresh['coverage_notes'])

                def rehash(value):
                    if isinstance(value, dict):
                        if value.get('path') == 'source.sql' and 'sha256' in value:
                            value['sha256'] = digest
                        for child in value.values():
                            rehash(child)
                    elif isinstance(value, list):
                        for child in value:
                            rehash(child)

                # Keep the claimed complete inventory/plan/validation consistent,
                # remove analysis notes, and update ALL evidence and bundle hashes.
                for name in ('facts', 'inventory', 'validation_plan', 'validation'):
                    data = read_json(run / f'{name}.json')
                    rehash(data)
                    if name == 'inventory':
                        data['coverage_notes'] = []
                    write_json(run, name, data)
                result = seal(run)
                self.assertEqual(result['decision'], 'blocked', result)
                self.assertFalse(result['publication_authorized'], result)
                self.assertFalse(result['input_error'], result)
                self.assertEqual(result['errors'],
                                 ['analysis gap: ' + note['reason'] for note in fresh['coverage_notes']])

    def test_native_auxiliary_failure_blocks_a_previously_ready_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            make_bundle(run)
            with patch('sql_extract._extract_inventory_legacy', side_effect=RuntimeError('scan failed')):
                result = seal(run)
        self.assertEqual(result['decision'], 'blocked', result)
        self.assertFalse(result['publication_authorized'], result)
        self.assertFalse(result['input_error'], result)
        self.assertEqual(result['errors'], ['analysis gap: Legacy analysis failed: scan failed'])

    def test_context_diagnostic_survives_without_a_declaration(self):
        from ddl import enrich_inventory
        inventory = extract_inventory('SELECT FROM;', 'broken.sql', '9' * 64)
        inventory['items'] = [i for i in inventory['items'] if i['kind'] != 'DECLARATION']
        enriched = enrich_inventory(inventory, context_files=[FIXTURES / 'q_context_gp.sql'],
                                    project_root=PACKAGE)
        self.assertTrue(any(n['source_ref']['path'] == 'examples/fixtures/q_context_gp.sql'
                            and 'DDL parse failed' in n['reason'] for n in enriched['coverage_notes']))

    def test_invalid_context_encoding_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.sql'
            source.write_text('CREATE VIEW demo.v AS SELECT 1;', encoding='utf-8')
            context = root / 'context.sql'
            context.write_bytes(b'\xff')
            completed = self.run_cli(source, ('--context', str(context), '--project-root', str(root)))
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn('decode', json.loads(completed.stderr)['error'])

    def test_ambiguous_subject_keeps_exit_code_2(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'overloaded.sql'
            source.write_text('CREATE FUNCTION demo.f(a int) RETURNS int LANGUAGE sql AS $$ SELECT a; $$;\n'
                              'CREATE FUNCTION demo.f(a text) RETURNS text LANGUAGE sql AS $$ SELECT a; $$;',
                              encoding='utf-8')
            completed = self.run_cli(source, ('--subjects', 'demo.f'))
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn('expected one declaration, found 2', json.loads(completed.stderr)['error'])


if __name__ == '__main__':
    unittest.main()
