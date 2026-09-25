"""Regression success follows the oracle, without authorizing negative cases."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bundle_fixture import PACKAGE, make_bundle, read_json, seal, write_json
from regression_mutations import run_mutations
from run_regression import check_run, run_suite


SUBJECT = 'function+core+calc+()'


class RegressionDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        make_bundle(self.run)
        self.expected = self.root / 'expected'
        self.expected.mkdir()
        write_json(self.expected, 'facts', {'subjects': {SUBJECT: {
            'declaration': {'canonical_key': SUBJECT},
            'operations': {'SELECT': 1},
            'reads': ['demo.orders'], 'writes': [], 'calls': [],
            'formulas': ['sum(amount * 1.1)'], 'conditions': ['amount > 0']}}})
        write_json(self.expected, 'checks', {'required_rules': ['identity', 'formula']})
        # The hand-authored fixture is prose, not a generated claims-v1 page.
        write_json(self.expected, 'page_assertions', {'contract': 'independent-test-fixture'})
        self.expect('ready')

    def expect(self, decision, **extra):
        write_json(self.expected, 'decision', {'schema_version': 1, 'decision': decision, **extra})

    def check(self):
        return check_run(self.run, self.run, self.expected, SUBJECT)

    def mark(self, status):
        report = read_json(self.run / 'validation.json')
        check = next(c for c in report['checks'] if c['plan_check_id'] == 'identity')
        check['status'] = status
        check['reason'] = 'Independent review is pending.' if status == 'inconclusive' else 'Identity needs correction.'
        if status == 'defect':
            check['defect_code'] = 'wrong_identity'
        write_json(self.run, 'validation', report)
        return seal(self.run)

    def test_real_ready_bundle_passes(self):
        result = self.check()
        self.assertTrue(result['valid'], result)
        self.assertTrue(result['publication_authorized'])

    def test_real_blocked_bundle_matches_negative_oracle(self):
        gate = self.mark('inconclusive')
        self.assertEqual(gate['decision'], 'blocked', gate)
        self.expect('blocked')
        result = self.check()
        self.assertTrue(result['valid'], result)
        self.assertFalse(result['publication_authorized'])

    def test_real_revise_bundle_matches_negative_oracle(self):
        gate = self.mark('defect')
        self.assertEqual(gate['decision'], 'revise', gate)
        self.expect('revise')
        result = self.check()
        self.assertTrue(result['valid'], result)
        self.assertFalse(result['publication_authorized'])

    def test_unexpected_ready_and_refusal_fail(self):
        self.expect('blocked')
        self.assertFalse(self.check()['valid'])
        self.mark('inconclusive')
        self.expect('ready')
        self.assertFalse(self.check()['valid'])

    def test_negative_oracle_does_not_hide_stale_source(self):
        self.expect('blocked')
        with (self.run / 'source.sql').open('a', encoding='utf-8') as stream:
            stream.write('\n-- modified after validation\n')
        result = self.check()
        self.assertFalse(result['valid'], result)
        self.assertTrue(result['gate_errors'])

    def test_refusal_diagnostics_must_match_and_are_preserved(self):
        gate = dict(decision='blocked', publication_authorized=False,
                    input_error=False, errors=['analysis gap: Unsupported dialect: mysql'])
        self.expect('blocked', errors=gate['errors'])
        with patch('run_regression.evaluate_bundle', return_value=gate):
            result = self.check()
            self.assertTrue(result['valid'], result)
            self.assertEqual(result['gate_errors'], gate['errors'])
            self.expect('blocked', errors=['a different reason'])
            self.assertFalse(self.check()['valid'])

    def test_input_errors_and_invalid_authorization_never_pass(self):
        for decision, authorized, input_error in (
                ('blocked', False, True), ('blocked', True, False), ('ready', False, False)):
            with self.subTest(decision=decision, authorized=authorized, input_error=input_error):
                self.expect(decision)
                with patch('run_regression.evaluate_bundle', return_value=dict(
                        decision=decision, publication_authorized=authorized,
                        input_error=input_error, errors=[])):
                    self.assertFalse(self.check()['valid'])

    def test_invalid_decision_oracle_is_rejected(self):
        for decision, extra in (('unknown', {}), ('ready', {'errors': ['unexpected']}),
                                ('blocked', {'errors': 'not a list'})):
            with self.subTest(decision=decision, extra=extra):
                self.expect(decision, **extra)
                with self.assertRaises(ValueError):
                    self.check()

    def test_successful_negative_case_is_not_a_mutation_positive_control(self):
        self.mark('inconclusive')
        self.expect('blocked')
        self.assertTrue(self.check()['valid'])
        write_json(self.expected, 'page_assertions', {
            'contract': 'independent-test-fixture', 'mutations': [{
                'id': 'formula', 'group': 'formulas', 'selector': {'id': 'formula_001'},
                'field': 'expression', 'value': 'sum(amount * 9)'}]})
        report = self.root / 'report.json'
        report.write_text(json.dumps({'results': [dict(repeat=1, case='expected',
            subject=SUBJECT, run_dir=str(self.run), project_root=str(self.run))]}), encoding='utf-8')
        with patch('regression_mutations.finish') as finalizer:
            result = run_mutations(report, self.root, self.root / 'mutations')
        self.assertFalse(result['valid'])
        self.assertEqual(result['tested_mutations'], 0)
        self.assertEqual(result['untested'], 2)
        self.assertEqual({r['status'] for r in result['results']}, {'baseline_invalid'})
        finalizer.assert_not_called()


class RegressionMeasurementTests(unittest.TestCase):
    def test_unexecuted_repairs_cannot_be_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'runs'
            for count in (-1, 1, 3):
                with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'not implemented'):
                    run_suite(PACKAGE / 'examples/cases.json', output, iterations=count)
            self.assertFalse(output.exists())

    def test_empty_manifest_cannot_claim_full_agent_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for cases in ([], [{'id': 'empty', 'subjects': []}]):
                manifest = root / 'cases.json'
                manifest.write_text(json.dumps({'cases': cases}), encoding='utf-8')
                with self.subTest(cases=cases), self.assertRaisesRegex(ValueError, 'documented subjects'):
                    run_suite(manifest, root / 'output', mode='adapter', adapter=['unused'], model='test')
            self.assertFalse((root / 'output').exists())

    def test_case_filter_selects_only_requested_ids(self):
        import sys
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'runs'
            report = run_suite(PACKAGE / 'examples/cases-q.json', output, mode='adapter',
                               adapter=[sys.executable, '-B',
                                        str(PACKAGE / 'scripts/regression_adapter.py'), '{request}'],
                               model='deterministic-reference-v1', repeats=1,
                               cases_filter={'q01'})
            self.assertEqual({row['case'] for row in report['results']}, {'q01'})
            self.assertEqual(report['cases'], 1)
            self.assertTrue(report['valid'], report)

    def test_case_filter_without_matches_cannot_claim_a_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'documented subjects'):
                run_suite(PACKAGE / 'examples/cases-q.json', root / 'output',
                          mode='adapter', adapter=['unused'], model='test',
                          repeats=1, cases_filter={'no-such-case'})
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
