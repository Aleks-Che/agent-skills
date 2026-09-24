"""Q-07: execute mutations, retain positive controls, distinguish untested defects."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES = PACKAGE / 'examples'
EXPECTED = EXAMPLES / 'expected'
sys.path.insert(0, str(PACKAGE / 'scripts'))
from artifact_schema import read_json
from build_bundle import build, finish
from regression_mutations import mutate, run_mutations

Q_IDS = [f'q{i:02d}' for i in range(1, 12)]


def annotations(case):
    return read_json(EXPECTED / case / 'page_assertions.json')['mutations']


class MutationMatrixTests(unittest.TestCase):
    def test_all_eleven_q_cases_have_mutations(self):
        cases = read_json(EXAMPLES / 'cases-q.json')['cases']
        self.assertEqual([c['id'] for c in cases], Q_IDS)
        for case in Q_IDS:
            with self.subTest(case=case):
                self.assertTrue(annotations(case))  # Missing file must fail, not be skipped.

    def test_d01_through_d11_are_annotated_d12_remains_open(self):
        covered = {a['id'].split('-')[0] for c in Q_IDS for a in annotations(c)
                   if a['id'].startswith('D')}
        self.assertEqual(covered, {f'D{i:02d}' for i in range(1, 12)})
        # These are annotation labels, not proof that all review subcases were detected.

    def test_annotation_fields_and_unique_ids(self):
        for case in Q_IDS:
            mutations = annotations(case)
            self.assertEqual(len(mutations), len({m['id'] for m in mutations}), case)
            for m in mutations:
                with self.subTest(case=case, mutation=m['id']):
                    self.assertTrue({'group', 'selector', 'field', 'value', 'id'} <= m.keys())
                    self.assertTrue(m['id'])
                    self.assertTrue(m['field'])
                    self.assertIsInstance(m['selector'], dict)
                    self.assertIn(m['group'], {'objects', 'definitions', 'operations',
                                              'columns', 'formulas', 'conditions', 'unknowns'})

    def test_authoring_source_preserves_mutation_annotations(self):
        spec = importlib.util.spec_from_file_location('q07_authoring', EXPECTED / '_authoring.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for case in Q_IDS:
            self.assertEqual(annotations(case), module.CASES[case]['mutations'], case)


class MutationTargetTests(unittest.TestCase):
    def setUp(self):
        self.facts = {'operations': [
            {'id': 'op_1', 'kind': 'SELECT', 'reads': ['source'],
             'structure': {'limit': '1', 'parts': ['a']}},
            {'id': 'op_2', 'kind': 'SELECT', 'reads': ['other']}]}
        self.annotation = {'id': 'test', 'group': 'operations', 'selector': {'id': 'op_1'},
                           'field': 'reads', 'value': []}

    def test_ambiguous_selector_is_rejected(self):
        self.annotation['selector'] = {'kind': 'SELECT'}
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            mutate(self.facts, self.annotation)

    def test_missing_selector_target_is_rejected(self):
        self.annotation['selector'] = {'id': 'missing'}
        with self.assertRaises(ValueError):
            mutate(self.facts, self.annotation)

    def test_noop_is_rejected(self):
        self.annotation['value'] = ['source']
        with self.assertRaisesRegex(ValueError, 'change'):
            mutate(self.facts, self.annotation)

    def test_typo_does_not_create_a_new_field(self):
        self.annotation['field'] = 'raeds'
        original = copy.deepcopy(self.facts)
        with self.assertRaises(ValueError):
            mutate(self.facts, self.annotation)
        self.assertEqual(self.facts, original)

    def test_invalid_nested_path_is_rejected(self):
        for path in ('structure.no_such_key.value', 'structure.parts.-1',
                     'structure.parts.5', 'structure.parts.no_index'):
            with self.subTest(path=path):
                self.annotation['field'] = path
                with self.assertRaises(ValueError):
                    mutate(self.facts, self.annotation)

    def test_nested_field_changes_only_selected_fact(self):
        self.annotation.update(field='structure.parts.0', value='b')
        result = mutate(self.facts, self.annotation)
        self.assertEqual(result['operations'][0]['structure']['parts'], ['b'])
        self.assertEqual(result['operations'][1]['reads'], ['other'])

    def test_mutation_value_is_not_aliased_to_annotation(self):
        result = mutate(self.facts, self.annotation)
        result['operations'][0]['reads'].append('later')
        self.assertEqual(self.annotation['value'], [])


class MutationDetectionTests(unittest.TestCase):
    """Exercise real facts + visible claims + independently rebuilt SQL gate."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.cases = read_json(EXAMPLES / 'cases-q.json')['cases']
        cls.records = []
        cls.baselines = {}
        for case in cls.cases:
            run = cls.root / case['id']
            result = build(EXAMPLES / case['sql'], run, project_root=EXAMPLES,
                           subject=case['subjects'][0],
                           context=[EXAMPLES / p for p in case['context']],
                           dialect=case['dialect'], version=case['version'],
                           migration_manifest=EXAMPLES / case['migration_manifest']
                           if case.get('migration_manifest') else None)
            cls.baselines[case['id']] = result
            cls.records.append(dict(repeat=1, case=case['id'], subject=case['subjects'][0],
                                    run_dir=str(run), project_root=str(EXAMPLES),
                                    profile=result.get('profile')))
        cls.report = cls.root / 'report.json'
        cls.report.write_text(json.dumps({'results': cls.records}), encoding='utf-8')
        cls.result = run_mutations(cls.report, EXPECTED, cls.root / 'mutations')

    def test_ten_positive_controls_and_explicit_q09_limitation(self):
        self.assertEqual({c for c, r in self.baselines.items() if r['publication_authorized']},
                         set(Q_IDS) - {'q09'})
        self.assertTrue(any('expression differs from SQL mapping' in e
                            for e in self.baselines['q09']['errors']))

    def test_every_eligible_annotation_runs_in_both_modes(self):
        expected = {(c, a['id'], m) for c in Q_IDS if c != 'q09'
                    for a in annotations(c) for m in ('text-only', 'coherent')}
        tested = [r for r in self.result['results'] if r['status'] == 'detected']
        self.assertEqual({(r['case'], r['mutation'], r['mode']) for r in tested}, expected)
        self.assertEqual(len(tested), len(expected))
        for row in tested:
            with self.subTest(case=row['case'], mutation=row['mutation'], mode=row['mode']):
                self.assertFalse(row['false_ready'], row)
                self.assertNotEqual(row['decision'], 'ready', row)
                self.assertTrue(row['errors'], row)
                self.assertFalse(any('hash mismatch' in e.lower() for e in row['errors']), row)
                self.assertIn(row['detection_layer'], {'visible_claims', 'sql', 'schema'})
                if row['mode'] == 'text-only':
                    self.assertTrue(any('Page claim' in e for e in row['errors']), row)

    def test_q09_rejections_are_not_counted_as_detection(self):
        rows = [r for r in self.result['results'] if r['case'] == 'q09']
        self.assertEqual(len(rows), 2 * len(annotations('q09')))
        self.assertTrue(all(r['status'] == 'baseline_invalid' for r in rows), rows)
        self.assertFalse(self.result['valid'])
        self.assertEqual(self.result['false_ready'], 0)
        self.assertEqual(self.result['untested'], len(rows))

    def test_detects_exec_location_and_missing_metadata_read(self):
        cases = {r['mutation']: r for r in self.result['results'] if r['mode'] == 'coherent'}
        self.assertTrue(any('execute_on differs' in e
                            for e in cases['D05-deny-execute-on-master']['errors']))
        self.assertTrue(any('reads' in e
                            for e in cases['D09-remove-metadata-read']['errors']))

    def test_equivalent_markdown_presentation_is_accepted(self):
        case = next(c for c in self.cases if c['id'] == 'q02')
        run = self.root / 'q02'
        path = run / 'page.draft.md'
        original = path.read_text(encoding='utf-8')
        changed = original.replace('## Operations, dependencies and expressions',
                                   '## Expressions and data dependencies')
        changed = changed.replace('| Fact | Property | SQL value |',
                                  '| Item | Attribute | Value from SQL |')
        self.assertNotEqual(changed, original)
        path.write_text(changed, encoding='utf-8')
        result = finish(run, sql_files=[EXAMPLES / case['sql']],
                        context=[EXAMPLES / p for p in case['context']], project_root=EXAMPLES)
        self.assertTrue(result['publication_authorized'], result)
        path.write_text(original, encoding='utf-8')
        finish(run, sql_files=[EXAMPLES / case['sql']],
               context=[EXAMPLES / p for p in case['context']], project_root=EXAMPLES)

    def test_runner_does_not_pass_when_every_baseline_is_invalid(self):
        with patch('regression_mutations.check_run',
                   return_value={'valid': False, 'decision': 'blocked', 'errors': ['control failed']}), \
             patch('regression_mutations.finish') as finalizer:
            result = run_mutations(self.report, EXPECTED, self.root / 'all-blocked')
        self.assertFalse(result['valid'])
        self.assertEqual(result['tested_mutations'], 0)
        self.assertIsNone(result['false_ready_rate'])
        finalizer.assert_not_called()

    def test_generation_failure_is_not_silently_omitted(self):
        path = self.root / 'failed-generation.json'
        path.write_text(json.dumps({'results': [dict(repeat=1, case='q02', valid=False,
                                                    errors=['adapter failed'])]}), encoding='utf-8')
        result = run_mutations(path, EXPECTED, self.root / 'generation-failed')
        self.assertFalse(result['valid'])
        self.assertTrue(result['input_errors'])

    def test_bad_annotations_do_not_count_as_detected_mutations(self):
        expected = self.root / 'bad-expectations'
        shutil.copytree(EXPECTED / 'q02', expected / 'q02')
        annotation = annotations('q02')[0]
        annotation['value'] = annotation['selector']['expression']
        path = expected / 'q02/page_assertions.json'
        value = read_json(path)
        value['mutations'] = [annotation]
        path.write_text(json.dumps(value), encoding='utf-8')
        report = self.root / 'noop-report.json'
        report.write_text(json.dumps({'results': [r for r in self.records if r['case'] == 'q02']}),
                          encoding='utf-8')
        with patch('regression_mutations.finish') as finalizer:
            result = run_mutations(report, expected, self.root / 'noop-mutations')
        self.assertFalse(result['valid'])
        self.assertEqual(result['tested_mutations'], 0)
        self.assertEqual(result['untested'], 2)
        self.assertEqual({r['status'] for r in result['results']}, {'invalid_mutation'})
        finalizer.assert_not_called()

    def test_unrelated_gate_error_does_not_count_as_detection(self):
        report = self.root / 'unrelated-report.json'
        report.write_text(json.dumps({'results': [r for r in self.records if r['case'] == 'q02']}),
                          encoding='utf-8')
        with patch('regression_mutations.finish', return_value={
                'decision': 'blocked', 'publication_authorized': False,
                'errors': ['Input file hash mismatch']}):
            result = run_mutations(report, EXPECTED, self.root / 'unrelated-error')
        self.assertFalse(result['valid'])
        self.assertEqual(result['detected'], 0)
        self.assertEqual({r['status'] for r in result['results']}, {'unattributed_rejection'})


class ProfileMutationTests(unittest.TestCase):
    def test_verified_profile_rejection_is_attributed_separately(self):
        case = next(c for c in read_json(EXAMPLES / 'cases.json')['cases'] if c['id'] == '11')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / 'run'
            result = build(EXAMPLES / case['sql'], run, project_root=EXAMPLES,
                           subject=case['subjects'][0], context=[EXAMPLES / p for p in case['context']],
                           profile=PACKAGE / case['profile'])
            self.assertTrue(result['publication_authorized'], result)
            report = root / 'report.json'
            report.write_text(json.dumps({'results': [dict(repeat=1, case='11',
                subject=case['subjects'][0], run_dir=str(run), project_root=str(EXAMPLES),
                profile=result['profile'])]}), encoding='utf-8')
            measured = run_mutations(report, EXPECTED, root / 'mutations')
            self.assertTrue(measured['valid'], measured)
            self.assertEqual(measured['detected'], 2)
            coherent = next(r for r in measured['results'] if r['mode'] == 'coherent')
            self.assertEqual(coherent['detection_layer'], 'profile')
            self.assertTrue(any('Source access observations differ from verified profile' in e
                                for e in coherent['errors']), coherent)


if __name__ == '__main__':
    unittest.main()
