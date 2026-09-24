"""Column summaries must preserve SQL variants and cannot disable type checks."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES = PACKAGE / 'examples'
sys.path.insert(0, str(PACKAGE / 'scripts'))
from artifact_schema import read_json
from build_bundle import build, finish, render
from page_claims import read_claims
from wiki_store import atomic_json


class ColumnSummaryGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.cases = {c['id']: c for c in read_json(EXAMPLES / 'cases-q.json')['cases']}
        for cid in ('q08', 'q09'):
            case = cls.cases[cid]
            result = build(EXAMPLES / case['sql'], cls.root / cid, project_root=EXAMPLES,
                subject=case['subjects'][0], context=[EXAMPLES / p for p in case['context']],
                dialect=case['dialect'], version=case['version'],
                migration_manifest=EXAMPLES / case['migration_manifest'] if case.get('migration_manifest') else None)
            if not result['publication_authorized']:
                raise AssertionError(result)

    def load(self, cid):
        self.cid = cid
        self.run = self.root / self.id().split('.')[-1] / cid
        shutil.copytree(self.root / cid, self.run, dirs_exist_ok=True)
        self.facts = read_json(self.run / 'facts.json')
        self.plan = read_json(self.run / 'validation_plan.json')
        return self.facts

    def column(self, name):
        written = {oid for op in self.facts['operations'] for oid in op['writes']}
        return next(c for c in self.facts['columns'] if c['name'] == name and c['object_id'] in written)

    def check(self):
        atomic_json(self.run / 'facts.json', self.facts)
        atomic_json(self.run / 'validation_plan.json', self.plan)
        page, coverage = render(self.facts, self.plan)
        (self.run / 'page.draft.md').write_text(page, encoding='utf-8')
        atomic_json(self.run / 'coverage.json', coverage)
        case = self.cases[self.cid]
        return finish(self.run, sql_files=[EXAMPLES / case['sql']],
            context=[EXAMPLES / p for p in case['context']], project_root=EXAMPLES,
            migration_manifest=EXAMPLES / case['migration_manifest'] if case.get('migration_manifest') else None)

    def assert_refused(self, result):
        self.assertFalse(result['publication_authorized'], result)
        self.assertIn(result['decision'], ('revise', 'blocked'))
        self.assertTrue(result['errors'], result)
        self.assertFalse(any('hash mismatch' in e.lower() for e in result['errors']), result)

    def test_q09_retains_each_level_and_formula_in_visible_operation_queries(self):
        self.load('q09')
        result = self.check()
        self.assertTrue(result['publication_authorized'], result)
        claims, errors = read_claims((self.run / 'page.draft.md').read_text(encoding='utf-8'))
        self.assertEqual(errors, [])
        inserts = [op for op in self.facts['operations'] if op['kind'] == 'INSERT']
        self.assertEqual(len(inserts), 3)
        variants = [('1', 'f.value_num', '10', '100', '1'),
                    ('2', 'f.value_num * 1.5', '10', '100', '2'),
                    ('1', 'f.value_num + 10', '20', '200', '1')]
        for op, (level, formula, kpi, structure, source_level) in zip(inserts, variants):
            query = claims[(op['id'], 'structure')]['query']
            self.assertIn(f'SELECT f.kpi_id, f.struct_id, {level}, f.value_num, {formula}', query)
            self.assertIn(f'f.kpi_id = {kpi} AND f.struct_id = {structure} AND f.level_no = {source_level}', query)
        for name in ('level_no', 'norm_value'):
            column = self.column(name)
            self.assertIsNone(column['expression'])
            self.assertIsNone(column['type_expression'])
            self.assertEqual(column['expression_status'], 'unknown')
            self.assertTrue(any(column['id'] in u['related_facts'] for u in self.facts['unknowns']))
            self.assertTrue(any(c['rule_id'] == 'unknown' and c['subject'].endswith('/q_out.kpi_result/' + name)
                                for c in self.plan['required_checks']))

    def test_null_missing_or_empty_single_expression_cannot_hide_invented_type(self):
        for expression in (None, '', 'missing'):
            with self.subTest(expression=expression):
                self.load('q08')
                column = self.column('amount')
                column.update(type_expression='text', expression_status='known')
                if expression == 'missing':
                    column.pop('expression')
                else:
                    column['expression'] = expression
                result = self.check()
                self.assert_refused(result)
                self.assertTrue(any('expression type differs' in e for e in result['errors']), result)

    def test_null_single_expression_with_honest_type_is_still_rejected(self):
        self.load('q08')
        self.column('amount')['expression'] = None
        result = self.check()
        self.assert_refused(result)
        self.assertTrue(any('expression differs from SQL mapping' in e for e in result['errors']), result)

    def test_null_single_expression_and_type_cannot_replace_known_sql_with_unknown(self):
        self.load('q08')
        column = self.column('amount')
        column.update(expression=None, type_expression=None, expression_status='unknown')
        column['type_evidence'].pop('type_expression', None)
        self.facts['unknowns'].append(dict(id='unknown_erased', what='Type', reason='Erased by writer', related_facts=[column['id']]))
        self.assert_refused(self.check())

    def test_aggregate_cannot_claim_an_invented_type(self):
        self.load('q09')
        column = self.column('level_no')
        column.update(type_expression='text', expression_status='known')
        column['type_evidence']['type_expression'] = copy.deepcopy(self.column('kpi_id')['type_evidence']['type_expression'])
        result = self.check()
        self.assert_refused(result)
        self.assertTrue(any('multiple SQL mappings' in e for e in result['errors']), result)

    def test_aggregate_cannot_claim_one_branches_expression(self):
        self.load('q09')
        self.column('norm_value')['expression'] = 'f.value_num + 10'
        self.assert_refused(self.check())

    def test_each_missing_operation_query_is_rejected(self):
        for ordinal in range(3):
            with self.subTest(ordinal=ordinal):
                self.load('q09')
                inserts = [op for op in self.facts['operations'] if op['kind'] == 'INSERT']
                inserts[ordinal]['structure'].pop('query')
                result = self.check()
                self.assert_refused(result)
                self.assertTrue(any('SQL mapping variant' in e for e in result['errors']), result)

    def test_swapped_branch_queries_are_rejected_even_when_all_variants_remain(self):
        self.load('q09')
        inserts = [op for op in self.facts['operations'] if op['kind'] == 'INSERT']
        a, b = inserts[0]['structure'], inserts[1]['structure']
        a['query'], b['query'] = b['query'], a['query']
        result = self.check()
        self.assert_refused(result)
        self.assertTrue(any('structure differs from independent SQL' in e for e in result['errors']), result)

    def test_literal_projection_mutation_in_facts_and_page_is_rejected(self):
        self.load('q09')
        second = [op for op in self.facts['operations'] if op['kind'] == 'INSERT'][1]
        query = second['structure']['query']
        second['structure']['query'] = query.replace('f.struct_id, 2,', 'f.struct_id, 99,')
        self.assertNotEqual(query, second['structure']['query'])
        self.assert_refused(self.check())

    def test_aggregate_unknown_fact_cannot_be_removed(self):
        self.load('q09')
        cid = self.column('level_no')['id']
        self.facts['unknowns'] = [u for u in self.facts['unknowns'] if cid not in u['related_facts']]
        result = self.check()
        self.assert_refused(result)
        self.assertTrue(any('matching type unknown fact' in e for e in result['errors']), result)

    def test_aggregate_unknown_obligation_cannot_be_removed(self):
        self.load('q09')
        before = len(self.plan['required_checks'])
        self.plan['required_checks'] = [c for c in self.plan['required_checks']
                                       if not c['subject'].endswith('/q_out.kpi_result/level_no')]
        self.assertEqual(len(self.plan['required_checks']), before - 1)
        result = self.check()
        self.assert_refused(result)
        self.assertTrue(any('independently derived obligations' in e for e in result['errors']), result)


class MappingTypeContextTests(unittest.TestCase):
    def test_equal_expressions_respect_source_types_and_repeated_occurrences(self):
        for second_type in ('integer', 'text'):
            with self.subTest(second_type=second_type), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / 'source.sql'
                source.write_text('''CREATE FUNCTION demo.load() RETURNS void LANGUAGE plpgsql AS $$ BEGIN
                    INSERT INTO demo.target(v) SELECT s.v FROM demo.first_source s;
                    INSERT INTO demo.target(v) SELECT s.v FROM demo.second_source s;
                    END; $$;''', encoding='utf-8')
                context = root / 'context.sql'
                context.write_text('CREATE TABLE demo.target(v text); CREATE TABLE demo.first_source(v integer); '
                                   f'CREATE TABLE demo.second_source(v {second_type});', encoding='utf-8')
                run = root / 'run'
                result = build(source, run, project_root=root, context=[context], subject='function+demo+load+()')
                self.assertTrue(result['publication_authorized'], result)
                facts = read_json(run / 'facts.json')
                target = next(o['id'] for o in facts['objects'] if o['name'] == 'target')
                column = next(c for c in facts['columns'] if c['object_id'] == target)
                self.assertEqual(column['expression'], 's.v' if second_type == 'integer' else None)
                self.assertEqual(column['type_expression'], 'integer' if second_type == 'integer' else None)
                self.assertEqual(len([o for o in facts['operations'] if o['kind'] == 'INSERT']), 2)


if __name__ == '__main__':
    unittest.main()
