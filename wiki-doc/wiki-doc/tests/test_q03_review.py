"""Independent Q-03 review regressions: ownership, grammar and semantic gate."""
import json
from pathlib import Path
import tempfile
import unittest

import test_q03_greenplum as gp


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class AdapterReviewTests(unittest.TestCase):
    def test_attributes_do_not_leak_between_functions_on_one_line(self):
        sql = gp.GP_FUNCTION.strip() + ' ' + gp.GP_FUNCTION.strip().replace('demo.f', 'demo.g').replace('MASTER', 'ANY')
        inv = gp.inventory_of(sql)
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual({i['details']['name']: i['details'].get('execute_on') for i in inv['items'] if i['kind']=='DECLARATION'}, {'f':'MASTER', 'g':'ANY'})

    def test_attributes_do_not_leak_to_function_without_attribute(self):
        sql = gp.GP_FUNCTION.strip() + ' ' + gp.GP_FUNCTION.strip().replace('demo.f', 'demo.g').replace(' EXECUTE ON MASTER', '')
        inv = gp.inventory_of(sql)
        declaration = next(i for i in inv['items'] if i['kind']=='DECLARATION' and i['details']['name']=='g')
        self.assertNotIn('execute_on', declaration['details'])

    def test_attributes_do_not_leak_between_tables_on_one_line(self):
        inv = gp.inventory_of('CREATE TABLE demo.t(a int) DISTRIBUTED BY(a); CREATE TABLE demo.u(b int) DISTRIBUTED RANDOMLY;')
        self.assertEqual([i['details']['distributed'] for i in inv['items'] if i['kind']=='CREATE'], [{'mode':'BY','columns':['a']}, {'mode':'RANDOMLY'}])

    def test_utf8_crlf_quoted_distribution_and_next_statement(self):
        sql = 'CREATE TABLE demo.t("ключ" int) DISTRIBUTED BY("ключ"); CREATE TABLE demo.u(b int) DISTRIBUTED RANDOMLY;\r\n'
        result = gp.mask_greenplum(sql)
        self.assertEqual(result['notes'], [])
        self.assertEqual(result['masked_text'][-2:], '\r\n')
        self.assertEqual(len(sql.encode()), len(result['masked_text'].encode()))
        inv = gp.inventory_of(sql)
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual([i['details']['distributed'] for i in inv['items'] if i['kind']=='CREATE'], [{'mode':'BY','columns':['"ключ"']}, {'mode':'RANDOMLY'}])

    def test_invalid_execute_position_is_not_erased(self):
        for sql in ['CREATE VIEW demo.v AS SELECT 1 AS a EXECUTE ON MASTER;',
                    'CREATE FUNCTION demo.f() RETURNS EXECUTE ON MASTER int LANGUAGE SQL AS $$SELECT 1$$;']:
            with self.subTest(sql=sql):
                result = gp.mask_greenplum(sql)
                self.assertEqual(result['masked_text'], sql)
                self.assertTrue(result['notes'])
                self.assertTrue(gp.inventory_of(sql)['coverage_notes'])

    def test_invalid_distribution_is_not_erased(self):
        for sql in ['CREATE TABLE demo.t(a int DISTRIBUTED BY(a));',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a + 1);',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a int_ops);',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a,);',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a,a);',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(missing);',
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a) WITH(appendonly=true);',
                    'CREATE MATERIALIZED VIEW demo.v AS SELECT 1 AS a DISTRIBUTED BY(a);']:
            with self.subTest(sql=sql):
                result = gp.mask_greenplum(sql)
                self.assertEqual(result['masked_text'], sql)
                self.assertTrue(result['notes'])
                self.assertTrue(gp.inventory_of(sql)['coverage_notes'])

    def test_duplicate_clauses_are_not_erased(self):
        for sql in [gp.GP_FUNCTION.replace('MASTER;', 'MASTER EXECUTE ON ANY;'),
                    'CREATE TABLE demo.t(a int) DISTRIBUTED BY(a) DISTRIBUTED RANDOMLY;']:
            with self.subTest(sql=sql):
                result = gp.mask_greenplum(sql)
                self.assertEqual(result['masked_text'], sql)
                self.assertTrue(result['notes'])

    def test_all_requires_segments(self):
        self.assertTrue(gp.inventory_of(gp.GP_FUNCTION.replace('MASTER', 'ALL'))['coverage_notes'])
        inv = gp.inventory_of(gp.GP_FUNCTION.replace('MASTER', 'ALL SEGMENTS'))
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual(next(i for i in inv['items'] if i['kind']=='DECLARATION')['details']['execute_on'], 'ALL SEGMENTS')

    def test_grant_and_revoke_preserved(self):
        for sql in ['GRANT EXECUTE ON FUNCTION demo.f() TO reader;',
                    'REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION demo.f() FROM reader;',
                    'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA demo FROM reader;']:
            result = gp.mask_greenplum(sql)
            self.assertEqual(result, dict(masked_text=sql, constructs=[], notes=[]))

    def test_identifier_boundaries_preserved(self):
        for sql in ['CREATE TABLE demo.t(foo$distributed int);', 'CREATE VIEW demo.v AS SELECT 1 AS x$execute;']:
            result = gp.mask_greenplum(sql)
            self.assertEqual(result, dict(masked_text=sql, constructs=[], notes=[]))
        for mode in ['RANDOMLY_extra', 'BY_extra(a)', 'REPLICATED_extra']:
            sql = f'CREATE TABLE demo.t(a int) DISTRIBUTED {mode};'
            self.assertEqual(gp.mask_greenplum(sql)['masked_text'], sql)
            self.assertTrue(gp.mask_greenplum(sql)['notes'])

    def test_duplicate_storage_parameter_blocks(self):
        sql = 'CREATE TABLE demo.t(a int) WITH(appendonly=true, appendonly=false) DISTRIBUTED BY(a);'
        inv = gp.inventory_of(sql)
        self.assertTrue(any('Duplicate Greenplum storage' in n['reason'] for n in inv['coverage_notes']))

    def test_ctas_preserves_attributes(self):
        inv = gp.inventory_of('CREATE TABLE demo.t WITH(appendonly=true) AS SELECT 1::int AS a DISTRIBUTED BY(a);')
        self.assertEqual(inv['coverage_notes'], [])
        details = next(i for i in inv['items'] if i['kind']=='CTAS')['details']
        self.assertEqual(details['distributed'], {'mode':'BY','columns':['a']})
        self.assertEqual(details['storage_parameters'], {'appendonly':'true'})

    def test_gp_merge_does_not_use_postgres_version_threshold(self):
        sql = 'CREATE FUNCTION demo.f() RETURNS void LANGUAGE SQL AS $$ MERGE INTO demo.t AS t USING demo.s AS s ON t.a=s.a WHEN MATCHED THEN UPDATE SET a=s.a; $$;'
        for version in ['unknown', '6.25.3', '15', '99']:
            inv = gp.inventory_of(sql, version=version)
            self.assertTrue(any('MERGE compatibility with Greenplum' in n['reason'] for n in inv['coverage_notes']))

    def test_dynamic_gp_merge_does_not_use_postgres_version_threshold(self):
        sql = "CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN EXECUTE 'MERGE INTO demo.t USING demo.s ON true WHEN MATCHED THEN DELETE'; END $$;"
        inv = gp.inventory_of(sql, version='15')
        self.assertTrue(any('MERGE compatibility with Greenplum' in n['reason'] for n in inv['coverage_notes']))


class DdlReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def migration(self, sql):
        (self.root/'source.sql').write_text(sql, encoding='utf8')
        dump(self.root/'manifest.json', dict(dialect='greenplum', version='6', ordered_files=['source.sql']))
        return gp.reconstruct(self.root/'manifest.json', project_root=self.root)

    def test_migration_keeps_distribution_and_storage(self):
        result = self.migration(gp.GP_TABLE)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['tables']['demo.t']['distributed'], {'mode':'BY', 'columns':['a']})
        self.assertEqual(result['tables']['demo.t']['storage_parameters']['compresstype'], 'zlib')

    def test_migration_rename_updates_distribution(self):
        result = self.migration(gp.GP_TABLE + 'ALTER TABLE demo.t RENAME COLUMN a TO b;')
        self.assertEqual(result['status'], 'resolved', result)
        self.assertEqual(result['tables']['demo.t']['distributed'], {'mode':'BY','columns':['"b"']})

    def test_migration_distribution_change_stays_unsupported(self):
        for change in ['DROP COLUMN a', 'ALTER COLUMN a TYPE bigint']:
            result = self.migration(gp.GP_TABLE + f'ALTER TABLE demo.t {change};')
            self.assertEqual(result['status'], 'unsupported', result)

    def test_migration_invalid_clause_stays_unsupported(self):
        result = self.migration('CREATE TABLE demo.t(a int) DISTRIBUTED BY(a+1);')
        self.assertEqual(result['status'], 'unsupported')

    def test_context_conflicting_distribution_blocks(self):
        self.check_conflict('DISTRIBUTED BY(a)', 'DISTRIBUTED RANDOMLY')

    def test_context_conflicting_storage_blocks(self):
        self.check_conflict('WITH(appendonly=true)', 'WITH(appendonly=false)')

    def test_column_only_context_cannot_erase_gp_attributes(self):
        paths = [self.root/'plain.sql', self.root/'gp.sql']
        paths[0].write_text('CREATE TABLE demo.t(a int);', encoding='utf8')
        paths[1].write_text('CREATE TABLE demo.t(a int) DISTRIBUTED BY(a);', encoding='utf8')
        for order in [paths, list(reversed(paths))]:
            result = gp.catalog(order, self.root, dialect='greenplum')
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['tables']['demo.t']['distributed'], {'mode':'BY','columns':['a']})
            self.assertEqual(result['tables']['demo.t']['source_ref']['path'], 'gp.sql')

    def test_unicode_context_offsets(self):
        source = self.root/'gp.sql'
        source.write_text('CREATE TABLE demo.t("ключ" int) DISTRIBUTED BY("ключ"); CREATE TABLE demo.u(b int) DISTRIBUTED RANDOMLY;', encoding='utf8')
        result = gp.catalog([source], self.root, dialect='greenplum')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['tables']['demo.u']['distributed'], {'mode':'RANDOMLY'})

    def check_conflict(self, first, second):
        paths = [self.root/'a.sql', self.root/'b.sql']
        for path, suffix in zip(paths, [first,second]):
            path.write_text(f'CREATE TABLE demo.t(a int) {suffix};', encoding='utf8')
        result = gp.catalog(paths, self.root, dialect='greenplum')
        self.assertTrue(any('Conflicting unordered definitions' in e for e in result['errors']))


class GateReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.run = self.root/'run'

    def build(self, sql, subject, context=None, **kwargs):
        source = self.project/'source.sql'
        source.write_text(sql, encoding='utf8')
        self.context = []
        if context:
            path = self.project/'context.sql'
            path.write_text(context, encoding='utf8')
            self.context.append(path)
        result = gp.build(source, self.run, project_root=self.project, subject=subject, context=self.context,
                          dialect='greenplum', **kwargs)
        self.assertEqual(result['decision'], 'ready', result)
        return gp.read_json(self.run/'facts.json')

    def reseal(self):
        # Refresh evidence as well as manifest: failures must be semantic.
        validation = gp.read_json(self.run/'validation.json')
        for check in validation['checks']:
            for ref in check['evidence']:
                if ref['root']=='run' and ref['path']=='page.draft.md':
                    ref['sha256'] = gp.sha256_file(self.run/'page.draft.md')
                    ref['end_line'] = len((self.run/'page.draft.md').read_text(encoding='utf8').splitlines())
        dump(self.run/'validation.json', validation)
        facts = gp.read_json(self.run/'facts.json')
        manifest = gp.create_manifest(run_id=facts['run_id'], page_id=facts['objects'][0]['page_id'],
                    sql_files=[self.project/'source.sql'], context_files=self.context, artifacts_dir=self.run,
                    project_dir=self.project, tool_versions=gp.compute_tool_versions(gp.PACKAGE))
        gp.write_manifest(manifest, self.run/'manifest.json')
        result = gp.evaluate_bundle(self.run, roots={'project':self.project}, write_decision=True)
        self.assertFalse(result['publication_authorized'], result)
        self.assertFalse(any('hash' in e.lower() or 'sha256' in e.lower() for e in result['errors']), result)
        return result

    def test_default_gp_version_stays_unknown(self):
        facts = self.build(gp.GP_FUNCTION, 'demo.f')
        self.assertEqual(facts['dialect'], dict(name='greenplum',version='unknown'))

    def test_gp_extension_requires_known_version(self):
        facts = self.build(gp.GP_FUNCTION, 'demo.f')
        self.assertEqual(facts['objects'][0]['gp_extension_version'], 1)
        for version in [None, 2]:
            if version is None:
                facts['objects'][0].pop('gp_extension_version')
            else:
                facts['objects'][0]['gp_extension_version'] = version
            self.assertTrue(gp.validate_schema(facts, gp.load_schemas()['facts'], 'facts'))

    def test_distributed_replicated_full_gate(self):
        facts = self.build('CREATE TABLE demo.t(a int) DISTRIBUTED REPLICATED;', 'demo.t')
        self.assertEqual(next(o for o in facts['operations'] if o['kind']=='CREATE')['structure']['distributed'], {'mode':'REPLICATED'})

    def test_all_segments_full_gate(self):
        facts = self.build(gp.GP_FUNCTION.replace('MASTER','ALL SEGMENTS'), 'demo.f')
        self.assertEqual(facts['objects'][0]['execute_on'], 'ALL SEGMENTS')

    def test_selected_function_on_same_line_full_gate(self):
        facts = self.build(gp.GP_FUNCTION.strip()+' '+gp.GP_FUNCTION.strip().replace('demo.f','demo.g').replace('MASTER','ANY'), 'demo.g')
        self.assertEqual(facts['objects'][0]['execute_on'], 'ANY')

    def test_selected_table_on_same_line_full_gate(self):
        facts = self.build('CREATE TABLE demo.t(a int) DISTRIBUTED BY(a); CREATE TABLE demo.u(b int) DISTRIBUTED RANDOMLY;', 'demo.u')
        self.assertEqual(next(o for o in facts['operations'] if o['kind']=='CREATE')['structure']['distributed'], {'mode':'RANDOMLY'})

    def test_ctas_full_gate(self):
        facts = self.build('CREATE TABLE demo.t WITH(appendonly=true) AS SELECT 1::int AS a DISTRIBUTED BY(a);', 'demo.t')
        self.assertEqual(facts['definitions'][0]['distributed'], {'mode':'BY','columns':['a']})
        self.assertEqual(next(o for o in facts['operations'] if o['kind']=='CTAS')['structure']['storage_parameters'], {'appendonly':'true'})

    def test_q05_with_gp_context_full_gate(self):
        context = (gp.FIXTURES/'q_context_gp.sql').read_text(encoding='utf8')+'\n'+(gp.FIXTURES/'q_context.sql').read_text(encoding='utf8')
        self.build((gp.FIXTURES/'q05_gp_master_nested.sql').read_text(encoding='utf8'), 'q_out.gp_master_probe', context=context)

    def test_master_page_mutation_is_semantic(self):
        self.build(gp.GP_FUNCTION,'demo.f')
        page = self.run/'page.draft.md'
        page.write_text(page.read_text(encoding='utf8').replace('MASTER','ANY'), encoding='utf8')
        result = self.reseal()
        self.assertTrue(any('claim' in e.lower() for e in result['errors']), result)

    def test_distribution_page_mutation_is_semantic(self):
        self.build(gp.GP_TABLE,'demo.t')
        page = self.run/'page.draft.md'
        page.write_text(page.read_text(encoding='utf8').replace('"BY"','"RANDOMLY"'), encoding='utf8')
        result = self.reseal()
        self.assertTrue(any('claim' in e.lower() for e in result['errors']), result)

    def test_consistent_forged_facts_inventory_page_rejected(self):
        facts = self.build(gp.GP_FUNCTION, 'demo.f')
        facts['objects'][0]['execute_on'] = 'ANY'
        dump(self.run/'facts.json', facts)
        inv = gp.read_json(self.run/'inventory.json')
        next(i for i in inv['items'] if i['kind']=='DECLARATION')['details']['execute_on'] = 'ANY'
        dump(self.run/'inventory.json', inv)
        page = self.run/'page.draft.md'
        page.write_text(page.read_text(encoding='utf8').replace('MASTER','ANY'), encoding='utf8')
        result = self.reseal()
        self.assertTrue(any('inventory' in e.lower() or 'execute_on differs' in e for e in result['errors']), result)

    def test_definition_distribution_loss_rejected(self):
        facts = self.build(gp.GP_TABLE,'demo.t')
        next(d for d in facts['definitions'] if 'distributed' in d).pop('distributed')
        dump(self.run/'facts.json', facts)
        result = self.reseal()
        self.assertTrue(any('definition distributed differs' in e for e in result['errors']), result)

    def test_definition_storage_loss_rejected(self):
        facts = self.build(gp.GP_TABLE,'demo.t')
        next(d for d in facts['definitions'] if 'storage_parameters' in d).pop('storage_parameters')
        dump(self.run/'facts.json', facts)
        result = self.reseal()
        self.assertTrue(any('definition storage_parameters differs' in e for e in result['errors']), result)
