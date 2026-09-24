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

    def test_implemented_review_labels_and_explicit_d12_gap(self):
        covered = {a['id'].split('-')[0] for c in Q_IDS for a in annotations(c)
                   if a['id'].startswith('D')}
        self.assertEqual(covered, {f'D{i:02d}' for i in range(1, 12)})
        # D12 is verified by D12ContentMutationTests (text-only page mutations),
        # not annotation labels: the annotation framework only supports facts-field
        # mutations, while D12 probes compatibility/window/example page claims.
        self.assertEqual({f'D{i:02d}' for i in range(1, 13)} - covered, {'D12'})
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

    def test_mutation_review_label_has_a_corresponding_case_assertion(self):
        for case in Q_IDS:
            assertions = read_json(EXPECTED / case / 'assertions.json')['assertions']
            reviews = {a['review'] for a in assertions}
            for annotation in annotations(case):
                if not annotation['id'].startswith('D'):
                    continue
                with self.subTest(case=case, mutation=annotation['id']):
                    self.assertIn(annotation['id'].split('-')[0], reviews)


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

    def test_all_eleven_positive_controls(self):
        self.assertEqual({c for c, r in self.baselines.items() if r['publication_authorized']},
                         set(Q_IDS))

    def test_every_eligible_annotation_runs_in_both_modes(self):
        expected = {(c, a['id'], m) for c in Q_IDS
                    for a in annotations(c) for m in ('text-only', 'coherent')}
        tested = [r for r in self.result['results'] if r['status'] == 'detected']
        tested_set = {(r['case'], r['mutation'], r['mode']) for r in tested}
        self.assertEqual(tested_set, expected)
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

    def test_q09_mutations_are_detected_with_valid_baseline(self):
        rows = [r for r in self.result['results'] if r['case'] == 'q09']
        self.assertEqual(len(rows), 2 * len(annotations('q09')))
        self.assertTrue(all(r['status'] == 'detected' for r in rows), rows)
        self.assertTrue(all(not r['false_ready'] for r in rows), rows)
        self.assertTrue(self.baselines['q09']['publication_authorized'])
        self.assertEqual(self.result['untested'], 0)
        self.assertEqual(self.result['false_ready'], 0)

    def test_detects_exec_location_and_missing_metadata_read(self):
        cases = {r['mutation']: r for r in self.result['results'] if r['mode'] == 'coherent'}
        self.assertTrue(any('execute_on differs' in e
                            for e in cases['D05-deny-execute-on-master']['errors']))
        self.assertTrue(any('reads' in e
                            for e in cases['D09-remove-metadata-read']['errors']))

    def test_each_insert_target_is_removed_and_detected_separately(self):
        original = read_json(self.root / 'q06/facts.json')
        for mutation, removed, kept in (
                ('D11-exclude-first-target', 'q_out.out_a', 'q_out.out_b'),
                ('D11-exclude-second-target', 'q_out.out_b', 'q_out.out_a')):
            with self.subTest(mutation=mutation):
                row = next(r for r in self.result['results']
                           if r['mutation'] == mutation and r['mode'] == 'coherent')
                self.assertEqual(row['status'], 'detected', row)
                self.assertEqual(row['detection_layer'], 'sql', row)
                self.assertTrue(any('writes' in e for e in row['errors']), row)
                changed = read_json(Path(row['run_dir']) / 'facts.json')
                objects = {o['id']: o['schema'] + '.' + o['name']
                           for o in original['objects'] if o.get('schema')}
                before = {objects[w] for op in original['operations'] for w in op['writes']}
                after = {objects[w] for op in changed['operations'] for w in op['writes']}
                self.assertEqual(before, {removed, kept})
                self.assertEqual(after, {kept})

    def test_invisible_source_ref_edit_is_not_a_page_mutation(self):
        expected = self.root / 'invisible-expectations'
        shutil.copytree(EXPECTED / 'q06', expected / 'q06')
        annotation = {'id': 'invisible-source-ref', 'group': 'objects',
                      'selector': {'kind': 'table', 'name': 'orders'},
                      'field': 'source_refs.0.path', 'value': 'wrong/path.sql'}
        original = read_json(self.root / 'q06/facts.json')
        self.assertNotEqual(mutate(copy.deepcopy(original), annotation), original)
        path = expected / 'q06/page_assertions.json'
        assertions = read_json(path)
        assertions['mutations'] = [annotation]
        path.write_text(json.dumps(assertions), encoding='utf-8')
        report = self.root / 'invisible-report.json'
        report.write_text(json.dumps({'results': [r for r in self.records if r['case'] == 'q06']}),
                          encoding='utf-8')
        with patch('regression_mutations.finish') as finalizer:
            result = run_mutations(report, expected, self.root / 'invisible-mutations')
        self.assertTrue(result['controls'][0]['valid'], result)
        self.assertFalse(result['valid'])
        self.assertEqual(result['tested_mutations'], 0)
        self.assertEqual(result['untested'], 2)
        self.assertEqual(result['detected'], 0)
        self.assertEqual({r['status'] for r in result['results']}, {'invalid_mutation'})
        self.assertTrue(all(r['errors'] == ['Mutation does not change visible claims']
                            for r in result['results']))
        finalizer.assert_not_called()

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


class ClaimTableMutationTests(unittest.TestCase):
    """Mechanical claims checks, not D12 prose/window/call-example acceptance.

    Restoring the original bytes proves the positive control, not acceptance
    of a different but semantically equivalent explanation.
    """
    CASES = ('q01', 'q05')

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.cases = read_json(EXAMPLES / 'cases-q.json')['cases']
        cls.runs = {}
        for case_id in cls.CASES:
            case = next(c for c in cls.cases if c['id'] == case_id)
            run = cls.root / case_id
            result = build(EXAMPLES / case['sql'], run, project_root=EXAMPLES,
                           subject=case['subjects'][0],
                           context=[EXAMPLES / p for p in case['context']],
                           dialect=case['dialect'], version=case['version'])
            assert result['publication_authorized'], result
            cls.runs[case_id] = run

    def _finish(self, case_id):
        case = next(c for c in self.cases if c['id'] == case_id)
        return finish(self.runs[case_id],
                      sql_files=[EXAMPLES / case['sql']],
                      context=[EXAMPLES / p for p in case['context']],
                      project_root=EXAMPLES)

    def _mutate_and_restore(self, case_id, mutate_fn):
        path = self.runs[case_id] / 'page.draft.md'
        original = path.read_text(encoding='utf-8')
        changed = mutate_fn(original)
        self.assertNotEqual(changed, original, f'page pattern not found for {case_id}')
        path.write_text(changed, encoding='utf-8')
        try:
            result = self._finish(case_id)
            self.assertFalse(result['publication_authorized'],
                             f'false claim must not pass gate: {result}')
            self.assertTrue(any('Page claim' in e or 'Unsupported page claim' in e
                                for e in result.get('errors', [])), result)
            self.assertFalse(any('hash mismatch' in e.lower() for e in result.get('errors', [])), result)
        finally:
            path.write_text(original, encoding='utf-8')
            restored = self._finish(case_id)
            self.assertTrue(restored['publication_authorized'],
                            f'restored positive control must pass gate: {restored}')

    @staticmethod
    def _add_unsupported_claim(text):
        """Insert a claim row that does not exist in facts (compatibility)."""
        marker = '| --- | --- | --- |'
        addition = marker + '\n| obj_1 | compatibility | ` "PostgreSQL >=9.4" ` |'
        return text.replace(marker, addition, 1)

    @staticmethod
    def _flip_condition_expression(text):
        """Change an SQL condition claim; this is not an explanation of its cause."""
        import re
        match = re.search(r'\| (cond_\d+) \| expression \| ` ("[^`"]*") ` \|', text)
        if not match:
            return text
        old_val = match.group(2)
        new_val = '"mutated_window_boundary"'
        return text.replace(match.group(0), match.group(0).replace(old_val, new_val), 1)

    @staticmethod
    def _corrupt_object_field(text):
        """Change an object-name claim; this does not mutate a call example."""
        import re
        match = re.search(r'\| (obj_\d+) \| name \| ` ("[^`"]*") ` \|', text)
        if not match:
            return text
        old_val = match.group(2)
        new_val = '"mutated_wrong_example"'
        return text.replace(match.group(0), match.group(0).replace(old_val, new_val), 1)

    def test_unsupported_claim_property_is_rejected(self):
        self._mutate_and_restore('q05', self._add_unsupported_claim)

    def test_changed_condition_claim_is_rejected(self):
        self._mutate_and_restore('q01', self._flip_condition_expression)

    def test_changed_object_name_claim_is_rejected(self):
        self._mutate_and_restore('q05', self._corrupt_object_field)


if __name__ == '__main__':
    unittest.main()
