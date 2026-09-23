"""Q-03: explicit bounded Greenplum dialect adapter.

Covers the accepted subset only:
- EXECUTE ON {MASTER,ANY,ALL SEGMENTS} is a declaration attribute, never a dynamic EXECUTE;
- DISTRIBUTED BY / RANDOMLY / REPLICATED and WITH storage parameters;
- lexical masking preserves byte layout and original hashes;
- unknown GP extensions stay blocked with a localized reason;
- postgres/other dialect rules are unchanged;
- removing or replacing MASTER/DISTRIBUTED in facts is detected by the gate.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'scripts'
EXAMPLES = PACKAGE / 'examples'
FIXTURES = EXAMPLES / 'fixtures'
sys.path.insert(0, str(SCRIPTS))

from artifact_schema import load_schemas, read_json, validate_schema  # noqa: E402
from build_bundle import build  # noqa: E402
from bundle import create_manifest, compute_tool_versions, write_manifest  # noqa: E402
from ddl import catalog, reconstruct  # noqa: E402
from evidence import sha256_file  # noqa: E402
from sql_ast import analyze  # noqa: E402
from sql_extract import extract_inventory  # noqa: E402
from sql_gp import mask_greenplum, prepare  # noqa: E402
from validation_gate import evaluate_bundle  # noqa: E402

GP_FUNCTION = """
CREATE FUNCTION demo.f() RETURNS int AS $q$ SELECT 1; $q$ LANGUAGE SQL EXECUTE ON MASTER;
"""
GP_TABLE = """
CREATE TABLE demo.t (a int) WITH (appendonly = true, compresstype = zlib, compresslevel = 5) DISTRIBUTED BY (a);
"""
GP_TABLE_RANDOMLY = 'CREATE TABLE demo.t (a int) DISTRIBUTED RANDOMLY;'
GP_UNKNOWN_TARGET = """
CREATE FUNCTION demo.f() RETURNS int AS $q$ SELECT 1; $q$ LANGUAGE SQL EXECUTE ON COORDINATOR;
"""
GP_UNKNOWN_DISTRIBUTED = 'CREATE TABLE demo.t (a int) DISTRIBUTED;'
GP_GRANT = """
CREATE FUNCTION demo.f() RETURNS int AS $q$ SELECT 1; $q$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA demo TO reader;
"""


def inventory_of(sql, dialect='greenplum', version='6.25.3', path='t.sql'):
    return extract_inventory(sql, path, hashlib.sha256(sql.encode()).hexdigest(),
                             dialect=dialect, version=version)


class GreenplumMaskingTests(unittest.TestCase):
    """Lexical adapter invariants: byte layout, line mapping, no deletion."""

    def test_mask_preserves_utf8_byte_length_and_line_count(self):
        text = 'CREATE TABLE demo.t ("колонка" int) DISTRIBUTED BY ("колонка");\n-- note\n'
        result = mask_greenplum(text)
        self.assertEqual(len(result['masked_text'].encode('utf-8')),
                         len(text.encode('utf-8')))
        self.assertEqual(result['masked_text'].count('\n'), text.count('\n'))
        self.assertIn('-- note', result['masked_text'])

    def test_mask_never_touches_dollar_bodies_or_strings(self):
        text = ("CREATE FUNCTION demo.f() RETURNS text AS $q$\n"
                "  SELECT 'DISTRIBUTED BY (x) EXECUTE ON MASTER';\n"
                "$q$ LANGUAGE SQL EXECUTE ON MASTER;")
        result = mask_greenplum(text)
        self.assertEqual(len(result['constructs']), 1)
        self.assertEqual(result['constructs'][0]['target'], 'MASTER')
        self.assertEqual(result['constructs'][0]['start_line'], 3)
        self.assertIn("DISTRIBUTED BY (x) EXECUTE ON MASTER", result['masked_text'])
        self.assertNotIn('$q$ LANGUAGE SQL EXECUTE ON MASTER;', result['masked_text'])

    def test_grant_execute_on_all_is_not_a_gp_attribute(self):
        result = mask_greenplum(GP_GRANT)
        self.assertEqual(result['constructs'], [])
        self.assertEqual(result['notes'], [])
        self.assertIn('GRANT EXECUTE ON ALL FUNCTIONS', result['masked_text'])

    def test_unknown_execute_target_is_kept_and_reported(self):
        result = mask_greenplum(GP_UNKNOWN_TARGET)
        self.assertEqual(result['constructs'], [])
        self.assertEqual(len(result['notes']), 1)
        self.assertIn('EXECUTE ON target: COORDINATOR', result['notes'][0][1])
        self.assertIn('EXECUTE ON COORDINATOR', result['masked_text'])

    def test_unknown_distributed_clause_is_kept_and_reported(self):
        result = mask_greenplum(GP_UNKNOWN_DISTRIBUTED)
        self.assertEqual(result['constructs'], [])
        self.assertEqual(len(result['notes']), 1)
        self.assertIn('DISTRIBUTED', result['notes'][0][1])
        self.assertIn('DISTRIBUTED;', result['masked_text'])

    def test_prepare_is_identity_for_other_dialects(self):
        for dialect in ('postgres', 'postgresql', 'mysql', 'oracle'):
            with self.subTest(dialect=dialect):
                text, constructs, notes = prepare(GP_TABLE, dialect)
                self.assertEqual(text, GP_TABLE)
                self.assertEqual(constructs, [])
                self.assertEqual(notes, [])


class GreenplumInventoryTests(unittest.TestCase):
    """Attributes land on the declaration/table, never as dynamic EXECUTE."""

    def test_execute_on_master_is_declaration_attribute(self):
        inv = inventory_of(GP_FUNCTION)
        self.assertEqual(inv['coverage_notes'], [])
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details']['execute_on'], 'MASTER')
        self.assertFalse([i for i in inv['items'] if i['kind'] == 'EXECUTE'],
                         'the attribute must not become a body-level EXECUTE')

    def test_execute_on_any_and_all(self):
        for target in ('ANY', 'ALL SEGMENTS'):
            with self.subTest(target=target):
                sql = GP_FUNCTION.replace('MASTER', target)
                inv = inventory_of(sql)
                declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
                self.assertEqual(declaration['details']['execute_on'], target)

    def test_distributed_table_attributes(self):
        inv = inventory_of(GP_TABLE)
        self.assertEqual(inv['coverage_notes'], [])
        create = next(i for i in inv['items'] if i['kind'] == 'CREATE')
        self.assertEqual(create['details']['distributed'], {'mode': 'BY', 'columns': ['a']})
        self.assertEqual(create['details']['storage_parameters'],
                         {'appendonly': 'true', 'compresstype': 'zlib', 'compresslevel': '5'})

    def test_distributed_randomly(self):
        inv = inventory_of(GP_TABLE_RANDOMLY)
        self.assertEqual(inv['coverage_notes'], [])
        create = next(i for i in inv['items'] if i['kind'] == 'CREATE')
        self.assertEqual(create['details']['distributed'], {'mode': 'RANDOMLY'})

    def test_q05_master_nested_is_fully_parsed(self):
        text = (FIXTURES / 'q05_gp_master_nested.sql').read_text(encoding='utf-8-sig')
        sha = sha256_file(FIXTURES / 'q05_gp_master_nested.sql')
        inv = extract_inventory(text, 'q05.sql', sha, dialect='greenplum', version='unknown')
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual(inv['dialect'], {'name': 'greenplum', 'version': 'unknown'})
        self.assertEqual(inv['inputs'], [{'path': 'q05.sql', 'sha256': sha}])
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details']['execute_on'], 'MASTER')
        self.assertFalse([i for i in inv['items'] if i['kind'] == 'EXECUTE'])
        reads = {r for i in inv['items'] for r in i.get('reads', [])}
        self.assertIn('q_src.events', reads)
        kinds = [i['kind'] for i in inv['items']]
        self.assertIn('DELETE', kinds)
        self.assertIn('PERFORM', kinds)
        self.assertIn('RETURN', kinds)
        self.assertGreaterEqual(kinds.count('SELECT'), 3, 'nested derived tables preserved')

    def test_plain_postgres_sql_with_greenplum_dialect_needs_no_unsupported_note(self):
        inv = inventory_of(GP_FUNCTION.replace(' EXECUTE ON MASTER', ''),
                           dialect='greenplum', version='6.25.3')
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual(inv['dialect']['name'], 'greenplum')

    def test_postgres_dialect_still_rejects_gp_syntax(self):
        for sql in (GP_FUNCTION, GP_TABLE):
            with self.subTest(sql=sql):
                inv = inventory_of(sql, dialect='postgres', version='15')
                reasons = ' | '.join(n['reason'] for n in inv['coverage_notes'])
                self.assertRegex(reasons, r'(?i)analysis failed')

    def test_unknown_gp_extension_stays_blocked_with_localized_reason(self):
        for sql, fragment in ((GP_UNKNOWN_TARGET, 'COORDINATOR'),
                              (GP_UNKNOWN_DISTRIBUTED, 'DISTRIBUTED'),
                              ('CREATE EXTERNAL TABLE demo.x (a int) LOCATION (\'gpfdist://x\');',
                               'EXTERNAL')):
            with self.subTest(sql=sql):
                inv = inventory_of(sql)
                reasons = ' | '.join(n['reason'] for n in inv['coverage_notes'])
                self.assertTrue(inv['coverage_notes'], sql)
                self.assertRegex(reasons, r'(?i)(unrecognized|analysis failed|syntax error)')
                self.assertTrue(fragment.lower() in reasons.lower(), reasons)

    def test_original_input_hash_and_source_ref_keep_original_bytes(self):
        text = (FIXTURES / 'q05_gp_master_nested.sql').read_text(encoding='utf-8-sig')
        sha = sha256_file(FIXTURES / 'q05_gp_master_nested.sql')
        inv = extract_inventory(text, 'q05.sql', sha, dialect='greenplum', version='unknown')
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['source_ref']['sha256'], sha)
        lines = text.splitlines()
        first = lines[declaration['source_ref']['start_line'] - 1]
        self.assertIn('CREATE OR REPLACE FUNCTION', first)

    def test_ast_analysis_field_keeps_engine_provenance(self):
        result = analyze(GP_FUNCTION, 't.sql', '0' * 64, dialect='greenplum', version='6.25.3')
        self.assertEqual(result['dialect'], {'name': 'greenplum', 'version': '6.25.3'})
        declaration = next(i for i in result['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details']['execute_on'], 'MASTER')


class GreenplumDdlTests(unittest.TestCase):
    """The same adapter serves context DDL and ordered migrations."""

    def test_gp_context_catalog_records_distribution(self):
        result = catalog([FIXTURES / 'q_context_gp.sql'], PACKAGE, dialect='greenplum')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['tables']['q_src.events']['distributed'],
                         {'mode': 'BY', 'columns': ['id']})
        self.assertEqual(result['tables']['q_out.gp_events']['distributed'],
                         {'mode': 'RANDOMLY'})
        self.assertEqual(result['tables']['q_out.gp_store']['distributed'],
                         {'mode': 'BY', 'columns': ['id']})
        self.assertEqual(result['tables']['q_out.gp_store']['storage_parameters'],
                         {'appendonly': 'true', 'compresstype': 'zlib', 'compresslevel': '5'})

    def test_postgres_context_parse_still_rejects_gp_ddl(self):
        result = catalog([FIXTURES / 'q_context_gp.sql'], PACKAGE, dialect='postgres')
        self.assertTrue(any('DDL parse failed' in e for e in result['errors']))
        self.assertTrue(any('DISTRIBUTED' in n['reason'] for n in result['coverage_notes']))

    def test_greenplum_migration_manifest_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'gp.sql').write_text(
                'CREATE TABLE demo.t (a int) DISTRIBUTED BY (a);', encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(
                {'dialect': 'greenplum', 'version': '6.25.3', 'ordered_files': ['gp.sql']}))
            result = reconstruct(manifest, project_root=root)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['tables']['demo.t']['columns'][0]['name'], 'a')

    def test_mysql_migration_manifest_still_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.sql').write_text('CREATE TABLE demo.t (a int);', encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(
                {'dialect': 'mysql', 'version': '8', 'ordered_files': ['a.sql']}))
            result = reconstruct(manifest, project_root=root)
        self.assertEqual(result['status'], 'unsupported')


class GreenplumGateTests(unittest.TestCase):
    """Full reference gate: positive GP examples and attribute-loss mutations."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build_gp_function(self, version='6.25.3'):
        project = self.root / 'project'
        project.mkdir(exist_ok=True)
        source = project / 'gp.sql'
        source.write_text(GP_FUNCTION, encoding='utf-8')
        run = self.root / ('run-' + version.replace('.', '_'))
        return build(source, run, project_root=project, subject='demo.f',
                     dialect='greenplum', version=version)

    def build_gp_table(self):
        project = self.root / 'project-table'
        project.mkdir(exist_ok=True)
        source = project / 'gp_table.sql'
        source.write_text(GP_TABLE, encoding='utf-8')
        run = self.root / 'run-table'
        return build(source, run, project_root=project, subject='demo.t',
                     dialect='greenplum', version='6.25.3'), run

    def reseal(self, run, project):
        facts = read_json(run / 'facts.json')
        sql_files = sorted(project.glob('*.sql'))
        manifest = create_manifest(
            run_id=facts['run_id'], page_id=facts['objects'][0]['page_id'],
            sql_files=sql_files, artifacts_dir=run, project_dir=project,
            tool_versions=compute_tool_versions(PACKAGE))
        write_manifest(manifest, run / 'manifest.json')
        return evaluate_bundle(run, roots={'project': project}, write_decision=True)

    def test_gp_function_with_master_full_gate_ready(self):
        result = self.build_gp_function()
        self.assertEqual(result['decision'], 'ready', result)
        self.assertTrue(result['publication_authorized'])
        facts = read_json(Path(result['run_dir']) / 'facts.json')
        self.assertEqual(facts['objects'][0]['execute_on'], 'MASTER')
        errors = validate_schema(facts, load_schemas()['facts'], 'facts')
        self.assertEqual(errors, [])

    def test_gp_table_with_distribution_full_gate_ready(self):
        result, run = self.build_gp_table()
        self.assertEqual(result['decision'], 'ready', result)
        facts = read_json(run / 'facts.json')
        create = next(o for o in facts['operations'] if o['kind'] == 'CREATE')
        self.assertEqual(create['structure']['distributed'], {'mode': 'BY', 'columns': ['a']})
        self.assertEqual(create['structure']['storage_parameters']['compresstype'], 'zlib')

    def test_removing_execute_on_from_facts_is_detected(self):
        result = self.build_gp_function()
        run = Path(result['run_dir'])
        project = run.parent / 'project'
        facts = read_json(run / 'facts.json')
        facts['objects'][0].pop('execute_on')
        facts['objects'][0].pop('gp_extension_version')
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertFalse(result['publication_authorized'])
        self.assertTrue(any('execute_on' in e for e in result['errors']), result['errors'])

    def test_replacing_execute_on_in_facts_is_detected(self):
        result = self.build_gp_function()
        run = Path(result['run_dir'])
        project = run.parent / 'project'
        facts = read_json(run / 'facts.json')
        facts['objects'][0]['execute_on'] = 'ANY'
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('execute_on' in e for e in result['errors']), result['errors'])

    def test_inventing_execute_on_without_sql_attribute_is_detected(self):
        project = self.root / 'project-plain'
        project.mkdir()
        source = project / 'gp.sql'
        source.write_text(GP_FUNCTION.replace(' EXECUTE ON MASTER', ''), encoding='utf-8')
        run = self.root / 'run-plain'
        result = build(source, run, project_root=project, subject='demo.f',
                       dialect='greenplum', version='6.25.3')
        self.assertEqual(result['decision'], 'ready', result)
        facts = read_json(run / 'facts.json')
        self.assertNotIn('execute_on', facts['objects'][0])
        facts['objects'][0].update(execute_on='MASTER', gp_extension_version=1)
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('execute_on' in e for e in result['errors']), result['errors'])

    def test_removing_distributed_from_facts_is_detected(self):
        result, run = self.build_gp_table()
        project = run.parent / 'project-table'
        facts = read_json(run / 'facts.json')
        create = next(o for o in facts['operations'] if o['kind'] == 'CREATE')
        create['structure'].pop('distributed')
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('structure' in e for e in result['errors']), result['errors'])

    def test_replacing_distributed_columns_in_facts_is_detected(self):
        result, run = self.build_gp_table()
        project = run.parent / 'project-table'
        facts = read_json(run / 'facts.json')
        create = next(o for o in facts['operations'] if o['kind'] == 'CREATE')
        create['structure']['distributed'] = {'mode': 'RANDOMLY'}
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('structure' in e for e in result['errors']), result['errors'])

    def test_replacing_master_in_page_claims_is_detected(self):
        result = self.build_gp_function()
        run = Path(result['run_dir'])
        project = run.parent / 'project'
        draft = run / 'page.draft.md'
        text = draft.read_text(encoding='utf-8')
        self.assertIn('MASTER', text)
        draft.write_text(text.replace('MASTER', 'ANY'), encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('claim' in e.lower() or 'draft' in e.lower() or 'execute_on' in e
                            for e in result['errors']), result['errors'])

    def test_gate_reanalysis_uses_the_same_greenplum_adapter(self):
        """Rebuilding inventory in the gate must restore the GP attribute."""
        result = self.build_gp_function()
        run = Path(result['run_dir'])
        project = run.parent / 'project'
        inv = read_json(run / 'inventory.json')
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
        declaration['details'].pop('execute_on')
        declaration['details'].pop('gp_extension_version')
        (run / 'inventory.json').write_text(json.dumps(inv, indent=2) + '\n', encoding='utf-8')
        facts = read_json(run / 'facts.json')
        facts['objects'][0].pop('execute_on')
        facts['objects'][0].pop('gp_extension_version')
        facts['dialect'] = inv['dialect']
        (run / 'facts.json').write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
        result = self.reseal(run, project)
        self.assertIn(result['decision'], ('blocked', 'revise'), result)
        self.assertTrue(any('inventory' in e or 'execute_on' in e for e in result['errors']),
                        result['errors'])

    def test_postgres_bundle_positive_control_stays_ready(self):
        project = self.root / 'project-pg'
        project.mkdir()
        source = project / 'pg.sql'
        source.write_text('CREATE FUNCTION demo.f() RETURNS int AS $q$ SELECT 1; $q$ LANGUAGE SQL;',
                          encoding='utf-8')
        run = self.root / 'run-pg'
        result = build(source, run, project_root=project, subject='demo.f',
                       dialect='postgres', version='15')
        self.assertEqual(result['decision'], 'ready', result)
        facts = read_json(run / 'facts.json')
        self.assertNotIn('execute_on', facts['objects'][0])


if __name__ == '__main__':
    unittest.main()
