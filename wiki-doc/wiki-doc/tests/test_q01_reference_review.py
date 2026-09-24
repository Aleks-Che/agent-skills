"""Reference expectations must keep SQL obligations and survive re-authoring."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES = PACKAGE / 'examples'
EXPECTED = EXAMPLES / 'expected'
sys.path.insert(0, str(PACKAGE / 'scripts'))
from build_bundle import build, finish, render
from check_policy import load_policy
from run_regression import check_expected
from sql_extract import extract_inventory
from validation_plan import generate_plan


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


class AuthoringTests(unittest.TestCase):
    def test_all_q_oracles_match_authoring_source(self):
        spec = importlib.util.spec_from_file_location('q_authoring_review', EXPECTED / '_authoring.py')
        authoring = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(authoring)
        with tempfile.TemporaryDirectory() as temporary:
            authoring.OUT = Path(temporary)
            for case_id, case in authoring.CASES.items():
                authoring.write_case(case_id, case)
                for path in (authoring.OUT / case_id).glob('*.json'):
                    with self.subTest(case=case_id, file=path.name):
                        self.assertEqual(path.read_bytes(), (EXPECTED / case_id / path.name).read_bytes())

    def test_q09_plain_projection_remains_in_branch_oracle(self):
        assertions = read(EXPECTED / 'q09/assertions.json')['assertions']
        formulas = next(a for a in assertions if a['id'] == 'q09-A3')['formulas_by_pair_level']
        self.assertEqual(formulas['10/100/1'], 'f.value_num')


class DateGuardTests(unittest.TestCase):
    def test_if_and_elsif_temporal_syntax_require_date_check(self):
        for guard in ("d >= DATE '2026-01-01'", "d >= CAST('2026-01-01' AS date)",
                      "d >= '2026-01-01'::timestamp", "d + INTERVAL '1 day' > d"):
            for keyword in ('IF', 'ELSIF'):
                with self.subTest(guard=guard, keyword=keyword):
                    prefix = 'IF false THEN RETURN 0; ' if keyword == 'ELSIF' else ''
                    sql = ('CREATE FUNCTION demo.f(d date) RETURNS int LANGUAGE plpgsql AS $$ BEGIN '
                           + prefix + keyword + ' ' + guard + ' THEN RETURN 1; END IF; RETURN 2; END; $$;')
                    inv = extract_inventory(sql, 'source.sql', 'a'*64)
                    self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
                    plan = generate_plan(inv, load_policy())
                    self.assertIn('date_boundary', {c['rule_id'] for c in plan['required_checks']})

    def test_date_words_in_comments_and_text_are_not_temporal_guards(self):
        sql = """CREATE FUNCTION demo.f() RETURNS int LANGUAGE plpgsql AS $$ BEGIN
        IF 'DATE TIMESTAMP INTERVAL' = 'literal' /* DATE '2026-01-01' */ THEN
          RETURN 1;
        END IF; RETURN 2; END; $$;"""
        inv = extract_inventory(sql, 'source.sql', 'a'*64)
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        self.assertNotIn('date_boundary', {c['rule_id'] for c in generate_plan(inv, load_policy())['required_checks']})


class ReferenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name) / 'run'

    def bundle(self, case_id):
        self.case = next(c for c in read(EXAMPLES / 'cases-q.json')['cases'] if c['id'] == case_id)
        self.source = EXAMPLES / self.case['sql']
        self.context = [EXAMPLES / p for p in self.case['context']]
        result = build(self.source, self.run, project_root=EXAMPLES, context=self.context,
                       subject=self.case['subjects'][0], dialect=self.case['dialect'],
                       version=self.case['version'],
                       migration_manifest=EXAMPLES / self.case['migration_manifest'] if self.case.get('migration_manifest') else None)
        self.assertTrue(result['publication_authorized'], result)
        self.facts = read(self.run / 'facts.json')
        self.plan = read(self.run / 'validation_plan.json')

    def rerender_and_finish(self):
        (self.run / 'facts.json').write_text(json.dumps(self.facts), encoding='utf8')
        (self.run / 'validation_plan.json').write_text(json.dumps(self.plan), encoding='utf8')
        page, coverage = render(self.facts, self.plan)
        (self.run / 'page.draft.md').write_text(page, encoding='utf8')
        (self.run / 'coverage.json').write_text(json.dumps(coverage), encoding='utf8')
        result = finish(self.run, sql_files=[self.source], context=self.context, project_root=EXAMPLES)
        self.assertFalse(any('hash mismatch' in e.lower() for e in result['errors']), result)
        return result

    def test_q04_date_rule_cannot_be_removed_from_plan(self):
        self.bundle('q04')
        self.assertIn('date_boundary', {c['rule_id'] for c in self.plan['required_checks']})
        self.plan['required_checks'] = [c for c in self.plan['required_checks'] if c['rule_id'] != 'date_boundary']
        result = self.rerender_and_finish()
        self.assertFalse(result['publication_authorized'], result)
        self.assertTrue(any('independently derived obligations' in e for e in result['errors']), result)

    def test_q04_changed_date_in_facts_and_page_is_rejected(self):
        self.bundle('q04')
        condition = next(c for c in self.facts['conditions'] if '2026-01-01' in c['expression'])
        condition['expression'] = condition['expression'].replace('2026-01-01', '2027-01-01')
        result = self.rerender_and_finish()
        self.assertFalse(result['publication_authorized'], result)

    def test_q11_type_unknowns_are_required_and_column_bound(self):
        self.bundle('q11')
        checks = [c for c in self.plan['required_checks'] if c['id'].startswith('unknown:type:')]
        self.assertEqual(len(checks), 2)
        self.assertEqual(len(self.facts['unknowns']), 2)
        original = copy.deepcopy(self.facts)
        for mutation in ('remove', 'unrelated'):
            with self.subTest(mutation=mutation):
                self.facts = copy.deepcopy(original)
                if mutation == 'remove':
                    self.facts['unknowns'] = []
                else:
                    for unknown in self.facts['unknowns']:
                        unknown['related_facts'] = [self.facts['documented_object_ids'][0]]
                result = self.rerender_and_finish()
                self.assertFalse(result['publication_authorized'], result)
                self.assertTrue(any('matching type unknown fact' in e for e in result['errors']), result)

    def test_q11_type_unknown_rule_cannot_be_removed(self):
        self.bundle('q11')
        self.plan['required_checks'] = [c for c in self.plan['required_checks'] if c['rule_id'] != 'unknown']
        result = self.rerender_and_finish()
        self.assertFalse(result['publication_authorized'], result)
        self.assertTrue(any('independently derived obligations' in e for e in result['errors']), result)

    def test_q11_mutation_selectors_match_canonical_formulas(self):
        self.bundle('q11')
        for mutation in read(EXPECTED / 'q11/page_assertions.json')['mutations']:
            candidates = [f for f in self.facts[mutation['group']]
                          if all(f.get(k) == v for k, v in mutation['selector'].items())]
            self.assertTrue(candidates, mutation['id'])

    def test_q05_oracle_keeps_unknown_server_version_and_resolved_helper(self):
        self.bundle('q05')
        expected = read(EXPECTED / 'q05/facts.json')['subjects'][self.case['subjects'][0]]
        self.assertEqual(check_expected(self.facts, expected), [])
        self.assertEqual(self.facts['unknowns'], [])
        self.assertTrue(any(e['call'] == 'q_meta.log_event'
                            for op in self.facts['operations']
                            for e in op.get('structure', {}).get('confirmed_call_effects', [])))
        self.facts['dialect']['version'] = '5'
        self.assertIn('dialect.version', check_expected(self.facts, expected))

    def test_q08_oracle_checks_migrated_types_and_positional_expressions(self):
        self.bundle('q08')
        expected = read(EXPECTED / 'q08/facts.json')['subjects'][self.case['subjects'][0]]
        self.assertEqual(check_expected(self.facts, expected), [])
        original = copy.deepcopy(self.facts)
        for field, value in (('type_target', 'text'), ('expression', 's.total')):
            with self.subTest(field=field):
                self.facts = copy.deepcopy(original)
                column = next(c for c in self.facts['columns'] if c['name'] == 'amount'
                              and c['object_id'] in {o['id'] for o in self.facts['objects'] if o['name'] == 'orders'})
                column[field] = value
                self.assertIn('column q_out.orders.amount', check_expected(self.facts, expected))


if __name__ == '__main__':
    unittest.main()
