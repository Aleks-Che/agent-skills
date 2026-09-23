import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sql_extract import extract_inventory
from ddl import reconstruct, enrich_inventory
from build_bundle import build

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class NativeInventoryTests(unittest.TestCase):
    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def example(self, number):
        p = next(EXAMPLES.glob(number + '_*.sql'))
        return self.inventory(p.read_text(encoding='utf-8-sig'), p.name)

    def test_all_examples_parse_without_hidden_gaps(self):
        for p in EXAMPLES.glob('[0-9]*.sql'):
            with self.subTest(case=p.name):
                result = self.inventory(p.read_text(encoding='utf-8-sig'),p.name)
                self.assertFalse(result['coverage_notes'],result['coverage_notes'])

    def test_two_performs_and_merge_using(self):
        result = self.example('04')
        performs = [i for i in result['items'] if i['kind']=='PERFORM']
        self.assertEqual(len(performs),2)
        self.assertNotEqual(performs[0]['anchor'], performs[1]['anchor'])
        merge = next(i for i in result['items'] if i['kind']=='MERGE')
        self.assertEqual(merge['writes'],['demo.summary'])
        self.assertEqual(merge['reads'],['demo_stg.summary_tmp'])
        self.assertEqual([b['action'] for b in merge['details']['branches']],['CMD_UPDATE','CMD_INSERT'])

    def test_scoped_ctes_match_references(self):
        result=self.example('03')
        locals_ = {i['details']['reference'] for i in result['items'] if 'reference' in i['details']}
        for item in result['items']:
            for ref in item.get('reads',[]) + item.get('writes',[]):
                if ref.startswith('@'):
                    self.assertIn(ref,locals_)
        ctes=[i for i in result['items'] if i['kind']=='CTE']
        self.assertEqual(len(ctes),2)
        self.assertTrue(all(i['details']['physical'] is False for i in ctes))

    def test_if_and_assignment_not_lost(self):
        result=self.example('05')
        self.assertEqual(sum(i['kind']=='IF' for i in result['items']),1)
        self.assertEqual(sum(i['kind']=='ASSIGN' for i in result['items']),2)
        cond=next(i for i in result['items'] if i['kind']=='IF')['details']['conditions']
        self.assertEqual(cond,["p_date_start = 'default'"])

    def test_dynamic_static_source_but_unknown_target(self):
        result=self.example('07')
        # Native analysis reveals the static source in a format() template.
        from sql_ast import analyze
        p=next(EXAMPLES.glob('07_*.sql'))
        native=analyze(p.read_text(),p.name,'a'*64,version='15')
        operation=[i for i in native['items'] if i['kind']=='EXECUTE'][1]
        self.assertEqual(operation['reads'],['demo_src.events'])
        self.assertFalse(operation.get('writes'))
        self.assertTrue(operation['details']['unresolved_parts'])
        self.assertFalse(result['coverage_notes'])

    def test_update_from_and_delete_using(self):
        result=self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE sql AS $$ UPDATE demo.a SET x=b.x FROM demo.b b WHERE a.id=b.id; DELETE FROM demo.a USING demo.b WHERE a.id=b.id; $$;')
        self.assertFalse(result['coverage_notes'])
        for item in result['items'][1:]:
            self.assertEqual(item.get('writes'),['demo.a'])
            self.assertIn('demo.b',item.get('reads',[]))

    def test_returns_and_parameter_defaults(self):
        result=self.inventory('CREATE FUNCTION demo.f(p int DEFAULT 7) RETURNS int LANGUAGE sql AS $$ SELECT p; $$;')
        details=result['items'][0]['details']
        self.assertEqual(details['parameters'][0]['default'],'7')
        self.assertEqual(details['returns'],'integer')

    def test_quoted_identity(self):
        result=self.inventory('CREATE VIEW "MySchema"."MyView" AS SELECT 1 AS x;')
        self.assertFalse(result['coverage_notes'])
        self.assertEqual(result['items'][0]['details']['schema'],'MySchema')

    def test_recursive_cte_keeps_gap(self):
        result=self.inventory('CREATE VIEW demo.v AS WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<5) SELECT n FROM x;')
        self.assertTrue(result['coverage_notes'])

    def test_unknown_plpgsql_keeps_gap(self):
        result=self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN LOOP NULL; END LOOP; END; $$;')
        self.assertTrue(any('PL/pgSQL' in n['reason'] for n in result['coverage_notes']))

    def test_parser_error_cannot_fall_back_to_ready(self):
        result=self.inventory('CREATE VIEW demo.v AS SELECT FROM;')
        self.assertTrue(result['coverage_notes'])

    def test_unresolved_call_not_assumed_builtin(self):
        result=self.inventory('CREATE VIEW demo.v AS SELECT arbitrary_function(1) AS x;')
        self.assertTrue(any('unqualified call' in n['reason'] for n in result['coverage_notes']))

    def test_cte_result_and_into_assignment_lineage(self):
        result=self.example('03')
        self.assertEqual(sum(bool(i.get('details',{}).get('result_for')) for i in result['items']),2)
        result=self.example('05')
        select=next(i for i in result['items'] if i['kind']=='SELECT' and i['details'].get('into'))
        self.assertEqual(select['details']['assignments'],[dict(target='v_start',expression='min(d)'),dict(target='v_end',expression='max(d)')])

    def test_temp_ctas_and_view_cte_output(self):
        result=self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN CREATE TEMP TABLE tmp AS SELECT id FROM demo.a; INSERT INTO demo.b SELECT id FROM tmp; END; $$;')
        self.assertFalse(result['coverage_notes'],result['coverage_notes'])
        ctas=next(i for i in result['items'] if i['kind']=='CTAS')
        self.assertTrue(ctas['writes'][0].startswith('@temp:'))
        result=self.inventory('CREATE VIEW demo.v AS WITH src AS (SELECT id FROM demo.a) SELECT id AS renamed FROM src;')
        self.assertEqual(result['items'][0]['details']['output_columns'][0]['name'],'renamed')

    def test_ctas_select_result_reaches_created_table(self):
        result=self.example('10')
        select=next(i for i in result['items'] if i['kind']=='SELECT')
        self.assertEqual(select['details']['result_for'],'demo.event_clock')

    def test_matview_with_index_and_grant(self):
        result=self.example('12')
        self.assertFalse(result['coverage_notes'], result['coverage_notes'])
        kinds={i['kind'] for i in result['items']}
        self.assertIn('CTAS', kinds)
        self.assertIn('SELECT', kinds)
        self.assertIn('CREATE', kinds)
        self.assertIn('GRANT', kinds)
        indexes=[i for i in result['items'] if i['kind']=='CREATE' and i['details'].get('structure',{}).get('index_name')]
        self.assertEqual(len(indexes), 2)
        self.assertEqual(indexes[0]['details']['structure']['index_name'],'idx_monthly_sales_month')
        self.assertTrue(indexes[0]['details']['structure']['unique'])
        self.assertEqual(indexes[1]['details']['structure']['index_name'],'idx_monthly_sales_amount')
        self.assertFalse(indexes[1]['details']['structure']['unique'])
        grants=[i for i in result['items'] if i['kind']=='GRANT']
        self.assertEqual(len(grants), 1)
        self.assertIn('select', grants[0]['details']['structure']['privileges'])
        self.assertIn('reporting_role', grants[0]['details']['structure']['grantees'])

    def test_trigger_constraint_grant_revoke(self):
        result=self.example('13')
        self.assertFalse(result['coverage_notes'], result['coverage_notes'])
        kinds={i['kind'] for i in result['items']}
        self.assertIn('CREATE', kinds)
        self.assertIn('ALTER', kinds)
        self.assertIn('GRANT', kinds)
        self.assertIn('REVOKE', kinds)
        triggers=[i for i in result['items'] if i['kind']=='CREATE' and i['details'].get('structure',{}).get('trigger_name')]
        self.assertEqual(len(triggers), 1)
        trig=triggers[0]['details']['structure']
        self.assertEqual(trig['trigger_name'],'trg_audit_orders')
        self.assertEqual(trig['timing'],'AFTER')
        self.assertIn('INSERT', trig['events'])
        self.assertIn('UPDATE', trig['events'])
        self.assertIn('DELETE', trig['events'])
        self.assertTrue(trig['for_each_row'])
        self.assertEqual(trig['function'],'demo.fn_audit_trigger')
        alters=[i for i in result['items'] if i['kind']=='ALTER']
        constraints=[]
        for a in alters:
            constraints.extend(a['details'].get('constraints', []))
        self.assertEqual(len(constraints), 1)
        self.assertEqual(constraints[0]['name'],'chk_action')
        self.assertEqual(constraints[0]['type'],'check')
        self.assertIn('expression', constraints[0])
        grants=[i for i in result['items'] if i['kind']=='GRANT']
        revokes=[i for i in result['items'] if i['kind']=='REVOKE']
        self.assertEqual(len(grants), 1)
        self.assertEqual(len(revokes), 1)

    def test_create_index_inline(self):
        result=self.inventory('CREATE INDEX idx_test ON demo.orders (amount);')
        self.assertFalse(result['coverage_notes'], result['coverage_notes'])
        creates=[i for i in result['items'] if i['kind']=='CREATE']
        self.assertEqual(len(creates), 1)
        s=creates[0]['details']['structure']
        self.assertEqual(s['index_name'],'idx_test')
        self.assertEqual(s['table'],'demo.orders')
        self.assertEqual(s['columns'],['amount'])
        self.assertFalse(s['unique'])

    def test_create_unique_index(self):
        result=self.inventory('CREATE UNIQUE INDEX idx_uniq ON demo.orders (id);')
        self.assertFalse(result['coverage_notes'])
        s=next(i for i in result['items'] if i['kind']=='CREATE')['details']['structure']
        self.assertTrue(s['unique'])

    def test_create_trigger_inline(self):
        result=self.inventory('CREATE TRIGGER trg BEFORE INSERT ON demo.t FOR EACH ROW EXECUTE FUNCTION demo.f();')
        self.assertFalse(result['coverage_notes'], result['coverage_notes'])
        creates=[i for i in result['items'] if i['kind']=='CREATE']
        self.assertEqual(len(creates), 1)
        s=creates[0]['details']['structure']
        self.assertEqual(s['trigger_name'],'trg')
        self.assertEqual(s['timing'],'BEFORE')
        self.assertEqual(s['events'],['INSERT'])
        self.assertTrue(s['for_each_row'])

    def test_grant_revoke_inline(self):
        result=self.inventory('GRANT SELECT, INSERT ON demo.t TO analyst; REVOKE ALL ON demo.t FROM PUBLIC;')
        self.assertFalse(result['coverage_notes'])
        grants=[i for i in result['items'] if i['kind']=='GRANT']
        revokes=[i for i in result['items'] if i['kind']=='REVOKE']
        self.assertEqual(len(grants), 1)
        self.assertEqual(len(revokes), 1)
        self.assertIn('select', grants[0]['details']['structure']['privileges'])
        self.assertIn('insert', grants[0]['details']['structure']['privileges'])

    def test_alter_add_check_constraint(self):
        result=self.inventory('ALTER TABLE demo.t ADD CONSTRAINT chk_val CHECK (x > 0);')
        self.assertFalse(result['coverage_notes'])
        alters=[i for i in result['items'] if i['kind']=='ALTER']
        self.assertEqual(len(alters), 1)
        constraints=alters[0]['details'].get('constraints', [])
        self.assertEqual(len(constraints), 1)
        self.assertEqual(constraints[0]['name'],'chk_val')
        self.assertEqual(constraints[0]['type'],'check')

    def test_alter_add_foreign_key(self):
        result=self.inventory('ALTER TABLE demo.child ADD CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES demo.parent(id);')
        self.assertFalse(result['coverage_notes'])
        constraints=next(i for i in result['items'] if i['kind']=='ALTER')['details']['constraints']
        self.assertEqual(len(constraints), 1)
        c=constraints[0]
        self.assertEqual(c['type'],'foreign_key')
        self.assertEqual(c['references'],'demo.parent')
        self.assertEqual(c['columns'],['parent_id'])
        self.assertEqual(c['ref_columns'],['id'])


class MigrationTests(unittest.TestCase):
    def test_explicit_order_restores_final_columns(self):
        result=reconstruct(EXAMPLES/'migrations/manifest.json',project_root=EXAMPLES)
        self.assertEqual(result['status'],'resolved')
        columns=result['tables']['demo_migration.orders']['columns']
        self.assertEqual([c['name'] for c in columns],['id','amount','updated_on'])
        self.assertEqual(columns[1]['type'],'numeric(18, 4)')
        self.assertEqual(columns[1]['default'],'0')

    def test_mtime_has_no_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            shutil.copytree(EXAMPLES,root/'examples')
            manifest=root/'examples/migrations/manifest.json'
            before=reconstruct(manifest,project_root=root/'examples')
            for i,p in enumerate((root/'examples').rglob('*.sql')):
                os.utime(p,(1000+i,1000+i))
            self.assertEqual(before,reconstruct(manifest,project_root=root/'examples'))

    def test_no_order_is_ambiguous(self):
        self.assertEqual(reconstruct()['status'],'ambiguous')

    def test_missing_order_blocks_migration_inventory(self):
        p=EXAMPLES/'08_alter_migration.sql'
        result=extract_inventory(p.read_text(),p.name,'a'*64,version='15')
        enrich_inventory(result,project_root=EXAMPLES)
        self.assertTrue(result['coverage_notes'])

    def test_declared_order_integrates_inventory(self):
        p=EXAMPLES/'08_alter_migration.sql'
        result=extract_inventory(p.read_text(),p.name,'a'*64,version='15')
        enrich_inventory(result,project_root=EXAMPLES,migration_manifest=EXAMPLES/'migrations/manifest.json')
        self.assertFalse(result['coverage_notes'])
        self.assertEqual(result['items'][0]['details']['reconstruction']['status'],'resolved')


class ModelExtensionBuildTests(unittest.TestCase):
    """P2-03: index/trigger/constraint/grant survive the full build and gate."""

    def _build(self, sql, subject):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        return build(EXAMPLES/sql, root/'run', project_root=EXAMPLES, subject=subject,
                     context=[EXAMPLES/'context.sql'])

    def _structures(self, result):
        facts=json.loads((Path(result['run_dir'])/'facts.json').read_text(encoding='utf-8'))
        return facts, [op.get('structure',{}) for op in facts['operations']]

    def test_matview_index_and_grant_build_ready(self):
        result=self._build('12_matview_index_grant.sql','materialized_view+demo+monthly_sales')
        self.assertEqual(result['decision'],'ready',result.get('errors'))
        facts,structures=self._structures(result)
        self.assertEqual({s.get('index_name') for s in structures if 'index_name' in s},
                         {'idx_monthly_sales_month','idx_monthly_sales_amount'})
        grant=next(s for s in structures if 'privileges' in s)
        self.assertEqual(grant['privileges'],['select'])
        self.assertEqual(grant['grantees'],['reporting_role'])

    def test_trigger_constraint_grant_build_ready(self):
        result=self._build('13_trigger_audit.sql','table+demo+audit_log')
        self.assertEqual(result['decision'],'ready',result.get('errors'))
        facts,structures=self._structures(result)
        self.assertFalse(any(s.get('trigger_name') for s in structures))
        trigger_result=self._build('13_trigger_audit.sql','table+demo_src+orders')
        self.assertEqual(trigger_result['decision'],'ready',trigger_result.get('errors'))
        _,trigger_structures=self._structures(trigger_result)
        trigger=next(s for s in trigger_structures if s.get('trigger_name'))
        self.assertEqual(trigger['timing'],'AFTER')
        self.assertEqual(trigger['events'],['INSERT','DELETE','UPDATE'])
        self.assertEqual(trigger['function'],'demo.fn_audit_trigger')
        constraints=next(s['constraints'] for s in structures if s.get('constraints'))
        self.assertEqual(constraints[0]['type'],'check')
        revoke=next(o for o in facts['operations'] if o['kind']=='REVOKE')
        self.assertEqual(revoke['structure']['grantees'],['PUBLIC'])


if __name__=='__main__': unittest.main()
