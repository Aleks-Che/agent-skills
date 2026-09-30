"""VALUES expressions remain visible and checked against target column order."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PACKAGE/'scripts'))
from build_bundle import build,finish,render
from artifact_schema import read_json
from wiki_store import atomic_json


class InsertValuesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.sql=self.root/'function.sql'
        self.ddl=self.root/'context.sql'
        self.ddl.write_text('CREATE TABLE demo.t (a integer, b numeric);',encoding='utf-8')
        self.run=self.root/'run'

    def prepare(self,statement):
        self.sql.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$\n'
                            'BEGIN\n'+statement+';\nEND;\n$$;',encoding='utf-8')
        result=build(self.sql,self.run,project_root=self.root,subject='function+demo+f+()',context=[self.ddl],version='15')
        self.assertTrue(result['publication_authorized'],result)
        self.facts=read_json(self.run/'facts.json')
        target=next(o['id'] for o in self.facts['objects'] if o['name']=='t')
        return {c['name']:c for c in self.facts['columns'] if c['object_id']==target}

    def test_values_explicit_target_order_and_cast_types(self):
        cols=self.prepare('INSERT INTO demo.t (b, a) VALUES (7.5::numeric, 2::integer)')
        self.assertEqual(cols['a']['expression'],'CAST(2 AS integer)')
        self.assertEqual(cols['b']['expression'],'CAST(7.5 AS numeric)')
        self.assertEqual(cols['a']['expression_status'],'known')
        self.assertEqual(cols['a']['type_expression'],'integer')

    def test_positional_values_use_ddl_order(self):
        cols=self.prepare('INSERT INTO demo.t VALUES (2, 7.5)')
        self.assertEqual([cols[k]['expression'] for k in ('a','b')],['2','7.5'])

    def test_multiple_rows_keep_distinct_variants(self):
        cols=self.prepare('INSERT INTO demo.t VALUES (2, 7.5), (2, 8.5)')
        self.assertEqual(cols['a']['expression'],'2')
        self.assertIsNone(cols['b']['expression'])
        self.assertEqual(cols['b']['expression_status'],'unknown')
        self.assertTrue(any(cols['b']['id'] in u['related_facts'] for u in self.facts['unknowns']))
        operation=next(o for o in self.facts['operations'] if o['kind']=='INSERT')
        self.assertIn('(2, 7.5), (2, 8.5)',operation['structure']['query'])

    def test_coherent_hiding_of_values_mapping_is_rejected(self):
        cols=self.prepare('INSERT INTO demo.t VALUES (2, 7.5)')
        for column in cols.values():
            column.pop('expression',None)
            column['expression_status']='not_applicable'
            column['type_expression']=None
        atomic_json(self.run/'facts.json',self.facts)
        page,coverage=render(self.facts,read_json(self.run/'validation_plan.json'))
        (self.run/'page.draft.md').write_text(page,encoding='utf-8')
        atomic_json(self.run/'coverage.json',coverage)
        result=finish(self.run,sql_files=[self.sql],context=[self.ddl],project_root=self.root)
        self.assertFalse(result['publication_authorized'],result)
        self.assertTrue(any('expression' in e for e in result['errors']),result)
        self.assertFalse(any('hash mismatch' in e.lower() for e in result['errors']),result)

    def test_width_mismatch_is_visible_source_finding_not_partial_mapping(self):
        cols=self.prepare('INSERT INTO demo.t VALUES (1, 2, 3)')
        self.assertTrue(all(c['expression_status']=='not_applicable' for c in cols.values()))
        inventory=read_json(self.run/'inventory.json')
        self.assertEqual(inventory['source_findings'][0]['select_width'],3)
        self.assertEqual(inventory['source_findings'][0]['target_width'],2)
        self.assertIn('VALUES',inventory['source_findings'][0]['reason'])
        self.assertIn(inventory['source_findings'][0]['reason'],(self.run/'page.draft.md').read_text(encoding='utf-8'))


if __name__=='__main__':
    unittest.main()
