"""Observable content-gate decisions with positive controls and independent SQL."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES = PACKAGE / 'examples'
sys.path.insert(0, str(PACKAGE / 'scripts'))
from artifact_schema import read_json
from build_bundle import build, finish
from bundle import create_manifest, write_manifest, compute_tool_versions
from content_claims import check_content_claims as check
from lint import lint
from publish import prepare, publish
from run_regression import check_run
from validation_gate import evaluate_bundle
from wiki_store import atomic_json


def routine(schema='demo', name='f', kind='function', parameters=None):
    return dict(id='obj_1', schema=schema, name=name, kind=kind,
                parameters=parameters if parameters is not None else [dict(name='p', type='text', mode='i', default=None)])


class ContentClaimReviewTests(unittest.TestCase):
    def setUp(self):
        self.facts = dict(dialect=dict(name='postgres', version='15'), objects=[routine()],
                          conditions=[dict(id='cond_1', expression="r.d < DATE '2026-01-01'")])

    def test_compatibility_requires_evidence_even_with_known_target_version(self):
        for version in ('15', 'unknown'):
            self.facts['dialect']['version'] = version
            for text in ('Compatible with PostgreSQL >=15.', 'Compatible with PostgreSQL 15.',
                         'Подтверждена совместимость с PostgreSQL 15.'):
                with self.subTest(version=version, text=text):
                    self.assertTrue(check(self.facts, text))

    def test_explicit_compatibility_caveats_are_not_positive_claims(self):
        for text in ('Compatibility with PostgreSQL >=9.4 is not established.',
                     'Not compatible with PostgreSQL 9.4.', 'Совместимость с PostgreSQL 15 не подтверждена.'):
            with self.subTest(text=text):
                self.assertEqual(check(self.facts, text), [])

    def test_target_version_requires_exact_product_and_version(self):
        self.assertEqual(check(self.facts, 'Target: PostgreSQL version 15.'), [])
        for text in ('Target: Greenplum version 15.', 'Target: PostgreSQL version 1.', 'Target: PostgreSQL >=15.'):
            with self.subTest(text=text):
                self.assertTrue(check(self.facts, text))

    def test_rendering_preserves_emphasis_and_normal_tables(self):
        for text in ('Compatible with **PostgreSQL** 15.',
                     '| Description |\n| --- |\n| Compatible with PostgreSQL 15. |'):
            with self.subTest(text=text):
                self.assertTrue(check(self.facts, text))

    def test_comments_and_unlabelled_code_are_not_affirmative_prose(self):
        for text in ('<!-- Compatible with PostgreSQL 15. -->', '~~~text\nCompatible with PostgreSQL 15.\n~~~',
                     '    Compatible with PostgreSQL 15.\n'):
            with self.subTest(text=text):
                self.assertEqual(check(self.facts, text), [])

    def test_window_direction_and_inclusiveness_must_match(self):
        self.assertEqual(check(self.facts, 'History covers records before 2026-01-01.'), [])
        for text in ('History covers records after 2026-01-01.', 'History covers records since 2026-01-01.',
                     'History covers records up to 2026-01-01.', 'History is limited to the year 2025.'):
            with self.subTest(text=text):
                self.assertTrue(check(self.facts, text))

    def test_window_without_date_evidence_does_not_silently_pass(self):
        self.facts['conditions'] = []
        self.assertTrue(check(self.facts, 'History covers records before 2026-01-01.'))

    def test_reversed_sql_operands_preserve_boundary(self):
        self.facts['conditions'][0]['expression'] = "DATE '2026-01-01' > r.d"
        self.assertEqual(check(self.facts, 'History covers records before 2026-01-01.'), [])

    def test_or_and_negation_do_not_prove_an_individual_bound(self):
        for expr in ("NOT (r.d < DATE '2026-01-01')", "r.d < DATE '2026-01-01' OR r.id = 1"):
            with self.subTest(expr=expr):
                self.facts['conditions'][0]['expression'] = expr
                self.assertTrue(check(self.facts, 'History covers records before 2026-01-01.'))

    def test_explicit_condition_reference_cannot_borrow_another_date(self):
        self.facts['conditions'].append(dict(id='cond_2', expression="s.d > DATE '2027-01-01'"))
        self.assertTrue(check(self.facts, 'Window cond_1: after 2027-01-01.'))
        self.assertEqual(check(self.facts, 'Window cond_2: after 2027-01-01.'), [])

    def test_unrelated_dates_and_caveats_are_not_window_claims(self):
        self.assertEqual(check(self.facts, 'Release notes were updated after 2025.'), [])
        self.assertEqual(check(self.facts, 'History is not limited to 2025.'), [])

    def test_call_schema_arity_type_and_syntax_are_checked(self):
        for code in ("wrong.f('x')", 'demo.f(1,2,3)', 'demo.f(42)', 'demo.f(', 'unknown(42)',
                     "demo.f('x') FILTER (WHERE true)", "demo.f('x') OVER ()",
                     "SELECT demo.f('x') WHERE unknown(1)"):
            with self.subTest(code=code):
                self.assertTrue(check(self.facts, f'Example: `{code}`'))

    def test_literal_contents_are_not_parsed_as_calls(self):
        for code in ("demo.f('unknown(42)')", "demo.f('a,b(c)')", "demo.f('it''s (text)')", "demo.f($$unknown(42)$$)"):
            with self.subTest(code=code):
                self.assertEqual(check(self.facts, f'Example: `{code}`'), [])

    def test_fences_indents_headings_and_russian_call_examples_are_checked(self):
        for text in ('Пример вызова:\n\n```sql\nSELECT demo.f(1,2,3);\n```',
                     'Example:\n\n~~~sql\nSELECT demo.f(1,2,3);\n~~~',
                     'Example:\n\n    SELECT demo.f(1,2,3);\n',
                     '## Examples\n\n```sql\nSELECT demo.f(1,2,3);\n```',
                     'Пример вызова: SELECT demo.f(1,2,3);'):
            with self.subTest(text=text):
                self.assertTrue(check(self.facts, text))

    def test_explicit_casts_and_named_arguments_keep_valid_examples(self):
        for code in ("demo.f(p => 'x')", "demo.f('x'::text)", 'demo.f(42::text)'):
            with self.subTest(code=code):
                self.assertEqual(check(self.facts, f'Example: `{code}`'), [])
        self.assertTrue(check(self.facts, "Example: `demo.f(wrong => 'x')`"))

    def test_defaults_output_parameters_and_variadic_inputs(self):
        self.facts['objects'][0]['parameters'] = [dict(name='p', type='integer', mode='i', default='1'),
                                                dict(name='result', type='text', mode='o', default=None)]
        self.assertEqual(check(self.facts, 'Example: `demo.f()`'), [])
        self.facts['objects'][0]['parameters'] = [dict(name='p', type='integer[]', mode='v', default=None)]
        for code in ('demo.f()', 'demo.f(1,2,3)'):
            self.assertEqual(check(self.facts, f'Example: `{code}`'), [])

    def test_quoted_identifiers_keep_case_and_schema(self):
        self.facts['objects'] = [routine(schema='Demo', name='F')]
        self.assertEqual(check(self.facts, '''Example: `"Demo"."F"('x')`'''), [])
        self.assertTrue(check(self.facts, "Example: `demo.f('x')`"))

    def test_procedure_requires_call_and_functions_require_select(self):
        self.facts['objects'] = [routine(kind='procedure')]
        self.assertEqual(check(self.facts, "Example: `CALL demo.f('x')`"), [])
        self.assertTrue(check(self.facts, "Example: `SELECT demo.f('x')`"))

    def test_overloads_and_unqualified_schema_ambiguity(self):
        second = routine()
        second['parameters'][0]['type'] = 'integer'
        self.facts['objects'].append(second)
        self.assertEqual(check(self.facts, 'Example: `demo.f(1)`'), [])
        self.assertTrue(check(self.facts, 'Example: `demo.f(NULL)`'))
        self.facts['objects'].append(routine(schema='other'))
        self.assertTrue(check(self.facts, "Example: `f('x')`"))

    def test_unestablished_routine_signature_is_not_inferred_from_name(self):
        self.facts['objects'][0].pop('parameters')
        self.assertTrue(check(self.facts, "Example: `demo.f('x')`"))

    def test_invalid_literal_cast_is_not_type_evidence(self):
        self.facts['objects'][0]['parameters'][0]['type'] = 'integer'
        self.assertEqual(check(self.facts, "Example: `demo.f('42'::integer)`"), [])
        for code in ("demo.f('bad'::integer)", "demo.f('999999999999999'::integer)"):
            self.assertTrue(check(self.facts, f'Example: `{code}`'))


class ContentEntryPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.case = next(c for c in read_json(EXAMPLES / 'cases-q.json')['cases'] if c['id'] == 'q05')
        cls.kwargs = dict(sql_files=[EXAMPLES / cls.case['sql']], context=[EXAMPLES / p for p in cls.case['context']], project_root=EXAMPLES)
        result = build(cls.kwargs['sql_files'][0], cls.root / 'baseline', project_root=EXAMPLES,
                       context=cls.kwargs['context'], subject=cls.case['subjects'][0], dialect='greenplum', version='unknown')
        if not result['publication_authorized']:
            raise AssertionError(result)

    def setUp(self):
        self.run = self.root / self.id().split('.')[-1]
        shutil.copytree(self.root / 'baseline', self.run)

    def insert(self, path, prose):
        page = path.read_text(encoding='utf-8')
        path.write_text(page.replace('<!-- wiki-doc:managed end -->', prose+'\n<!-- wiki-doc:managed end -->'), encoding='utf-8')

    def test_finish_rejects_bad_example_and_accepts_corrected_equivalent(self):
        path = self.run / 'page.draft.md'
        original = path.read_text(encoding='utf-8')
        self.insert(path, 'Пример вызова:\n\n~~~sql\nSELECT q_out.gp_master_probe(1,2,3);\n~~~')
        result = finish(self.run, **self.kwargs)
        self.assertFalse(result['publication_authorized'], result)
        self.assertTrue(any('Wrong call example' in e for e in result['errors']), result)
        path.write_text(original, encoding='utf-8')
        self.insert(path, "Пример вызова:\n\n~~~sql\nSELECT q_out.gp_master_probe(p_msg => 'x');\n~~~")
        result = finish(self.run, **self.kwargs)
        self.assertTrue(result['publication_authorized'], result)

    def test_gate_checks_prose_even_with_fresh_hashes_and_all_ok_report(self):
        self.insert(self.run / 'page.draft.md', "Example: `wrong.gp_master_probe('x')`")
        finish(self.run, **self.kwargs)
        report = read_json(self.run / 'validation.json')
        for item in report['checks']:
            item['status'] = 'ok'
            item.pop('defect_code', None)
        atomic_json(self.run / 'validation.json', report)
        manifest = create_manifest(run_id=report['run_id'], page_id=report['page_id'], artifacts_dir=self.run,
                                   project_dir=EXAMPLES, sql_files=self.kwargs['sql_files'], context_files=self.kwargs['context'],
                                   tool_versions=compute_tool_versions(PACKAGE))
        write_manifest(manifest, self.run / 'manifest.json')
        result = evaluate_bundle(self.run, roots={'project': EXAMPLES}, write_decision=True)
        self.assertFalse(result['publication_authorized'], result)
        self.assertTrue(any('Wrong call example' in e for e in result['errors']), result)
        self.assertFalse(any('hash mismatch' in e.lower() for e in result['errors']), result)

    def test_regression_checks_actual_prose(self):
        expected = EXAMPLES / 'expected/q05'
        self.assertTrue(check_run(self.run, EXAMPLES, expected, self.case['subjects'][0])['valid'])
        self.insert(self.run / 'page.draft.md', 'Compatible with PostgreSQL 9.4.')
        finish(self.run, **self.kwargs)
        result = check_run(self.run, EXAMPLES, expected, self.case['subjects'][0])
        self.assertFalse(result['valid'], result)
        self.assertTrue(any('Unverified compatibility' in e for e in result['errors']), result)

    def test_lint_checks_published_page_not_only_archived_draft(self):
        wiki = self.root / (self.run.name + '-wiki')
        prepare(self.run, wiki)
        result = finish(self.run, **self.kwargs, wiki_root=wiki)
        self.assertTrue(result['publication_authorized'], result)
        publish(self.run, wiki, project_root=EXAMPLES)
        self.assertTrue(lint(wiki, project_root=EXAMPLES)['valid'])
        page = wiki / read_json(self.run / 'facts.json')['objects'][0]['page_id']
        self.insert(page, 'Example: `q_out.gp_master_probe(1,2,3)`')
        result = lint(wiki, project_root=EXAMPLES)
        self.assertTrue(any(i['code'] == 'page_claim_invalid' and 'Wrong call example' in i['message'] for i in result['issues']), result)


if __name__ == '__main__':
    unittest.main()
