import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bundle_fixture import make_bundle, seal, write_json, PACKAGE
from artifact_schema import read_json
from evidence import validate_evidence, EvidenceError, sha256_file, extract_lines_text
from validation_gate import evaluate_bundle, evaluate_checks
from check_policy import load_policy, _evaluate_condition
from sql_extract import extract_inventory


class FullGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        make_bundle(self.run)

    def test_complete_bundle_is_ready_and_reusable(self):
        result = evaluate_bundle(self.run)
        self.assertEqual(result['decision'], 'ready', result)
        self.assertTrue(result['publication_authorized'])

    def test_every_required_artifact_is_required(self):
        for name in ('facts', 'inventory', 'validation_plan', 'coverage', 'validation', 'decision', 'manifest'):
            path = self.run / (name + '.json')
            data = path.read_bytes()
            with self.subTest(name=name):
                path.unlink()
                result = evaluate_bundle(self.run)
                self.assertFalse(result['publication_authorized'])
                self.assertTrue(any(name in e for e in result['errors']), result)
                path.write_bytes(data)

    def test_three_general_results_are_insufficient(self):
        report = read_json(self.run / 'validation.json')
        report['checks'] = report['checks'][:3]
        write_json(self.run, 'validation', report)
        result = seal(self.run)
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue(any('exactly one' in e for e in result['errors']))

    def test_removed_operation_in_all_writer_artifacts_cannot_shrink_plan(self):
        facts = read_json(self.run / 'facts.json')
        facts['operations'] = []
        facts['formulas'] = []
        facts['conditions'] = []
        facts['unknowns'] = []
        write_json(self.run, 'facts', facts)
        coverage = read_json(self.run / 'coverage.json')
        for fid in ('op_001', 'formula_001', 'cond_001', 'unknown_001'):
            coverage['entries'].pop(fid)
        write_json(self.run, 'coverage', coverage)
        report = read_json(self.run / 'validation.json')
        report['checks'] = [c for c in report['checks'] if not c['plan_check_id'].startswith(('operation:', 'formula:', 'condition:'))]
        write_json(self.run, 'validation', report)
        result = seal(self.run)
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue(any('missing from facts' in e for e in result['errors']), result)

    def test_saved_inventory_cannot_omit_or_invent_an_operation(self):
        original = read_json(self.run / 'inventory.json')
        for mutation in ('omit', 'invent'):
            inv = copy.deepcopy(original)
            if mutation == 'omit':
                inv['items'][-1]['reads'] = []
            else:
                inv['items'][-1]['reads'] = ['demo.invented']
            write_json(self.run, 'inventory', inv)
            result = seal(self.run)
            self.assertEqual(result['decision'], 'revise', result)
            self.assertTrue(any('rebuilt' in e for e in result['errors']))

    def test_policy_defaults_and_nonexistent_override(self):
        self.assertEqual(evaluate_bundle(self.run)['decision'], 'ready')
        result = evaluate_bundle(self.run, policy_path=self.run / 'missing-policy.json')
        self.assertTrue(result['input_error'])

    def test_plan_severity_and_applicability_cannot_be_lowered(self):
        original = read_json(self.run / 'validation_plan.json')
        for field, value in (('blocking', False), ('applicable', False), ('category', 'editorial')):
            plan = copy.deepcopy(original)
            plan['required_checks'][0][field] = value
            write_json(self.run, 'validation_plan', plan)
            self.assertEqual(seal(self.run)['decision'], 'revise')

    def test_report_technical_defect_cannot_be_lowered(self):
        report = read_json(self.run / 'validation.json')
        report['checks'][0].update(status='defect', blocking=False, category='editorial')
        write_json(self.run, 'validation', report)
        result = seal(self.run)
        self.assertEqual(result['decision'], 'revise', result)
        self.assertEqual(result['decision_record']['blocking_defects'], [report['checks'][0]['id']])

    def test_required_result_cannot_be_not_applicable(self):
        report = read_json(self.run / 'validation.json')
        report['checks'][0]['status'] = 'not_applicable'
        write_json(self.run, 'validation', report)
        self.assertEqual(seal(self.run)['decision'], 'revise')

    def test_duplicate_result_cannot_replace_a_missing_result(self):
        report = read_json(self.run / 'validation.json')
        report['checks'][1]['plan_check_id'] = report['checks'][0]['plan_check_id']
        write_json(self.run, 'validation', report)
        self.assertEqual(seal(self.run)['decision'], 'revise')

    def test_bad_evidence_rejected_after_resealing(self):
        original = read_json(self.run / 'validation.json')
        for field, value in (('path', 'missing.sql'), ('end_line', 1000), ('sha256', '0' * 64)):
            report = copy.deepcopy(original)
            report['checks'][0]['evidence'][0][field] = value
            write_json(self.run, 'validation', report)
            result = seal(self.run)
            self.assertEqual(result['decision'], 'blocked', result)
        report = copy.deepcopy(original)
        report['checks'][0]['evidence'] = ['source.sql:1']
        write_json(self.run, 'validation', report)
        self.assertEqual(seal(self.run)['decision'], 'blocked')

    def test_draft_edits_and_empty_section_rejected(self):
        path = self.run / 'page.draft.md'
        path.write_text('# Header only\n', encoding='utf-8')
        self.assertEqual(evaluate_bundle(self.run)['decision'], 'blocked')
        report = read_json(self.run / 'validation.json')
        for c in report['checks']:
            c['evidence'] = c['evidence'][:1]
        write_json(self.run, 'validation', report)
        self.assertEqual(seal(self.run)['decision'], 'revise')

    def test_wrong_decision_hashes_page_run_and_value_rejected(self):
        original = read_json(self.run / 'decision.json')
        for field, value in (('validation_sha256', '0' * 64), ('manifest_sha256', '0' * 64),
                             ('page_id', 'other'), ('run_id', '1' * 8 + '-1111-1111-1111-111111111111'),
                             ('decision', 'revise')):
            record = {**original, field: value}
            write_json(self.run, 'decision', record)
            self.assertEqual(evaluate_bundle(self.run)['decision'], 'blocked')

    def test_tool_hash_changes_block(self):
        manifest = read_json(self.run / 'manifest.json')
        for field in manifest['tool_versions']:
            changed = copy.deepcopy(manifest)
            changed['tool_versions'][field] = '0' * 64
            write_json(self.run, 'manifest', changed)
            self.assertEqual(evaluate_bundle(self.run)['decision'], 'blocked')

    def test_cli_exit_codes(self):
        command = [sys.executable, '-B', str(PACKAGE / 'scripts' / 'validation_gate.py')]
        for args, expected in ((['--bundle', str(self.run), '--json'], 0),
                               ([str(self.run / 'validation.json')], 2)):
            result = subprocess.run(command + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        (self.run / 'manifest.json').write_text('{bad', encoding='utf-8')
        result = subprocess.run(command + ['--bundle', str(self.run), '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class PolicyMetricsTests(unittest.TestCase):
    def test_exact_85_and_extra_successes_do_not_change_denominator(self):
        policy = load_policy()
        required = [{'id': str(i), 'rule_id': 'section', 'applicable': True, 'blocking': False} for i in range(20)]
        checks = [{'id': str(i), 'plan_check_id': str(i), 'status': 'ok' if i < 17 else 'defect',
                   'blocking': False, 'category': 'editorial'} for i in range(20)]
        result = evaluate_checks(checks, required, policy)
        self.assertEqual(result['decision'], 'ready')
        self.assertEqual(result['metrics']['accuracy_percent'], 85)
        checks[16]['status'] = 'defect'
        checks.extend({'id': f'extra{i}', 'status': 'ok', 'blocking': False} for i in range(100))
        result = evaluate_checks(checks, required, policy)
        self.assertEqual(result['decision'], 'revise')
        self.assertEqual(result['metrics']['accuracy_percent'], 80)
        checks.append({'id': 'wrong_type', 'status': 'defect', 'blocking': False, 'category': 'editorial'})
        self.assertIn('wrong_type', evaluate_checks(checks, required, policy)['blocking_defects'])

    def test_or_and_source_threshold_evaluate_actual_context(self):
        self.assertFalse(_evaluate_condition({'_or': {'has_reads': True, 'has_calls': True}}, {}))
        self.assertTrue(_evaluate_condition({'_or': {'has_reads': True, 'has_calls': True}}, {'has_calls': True}))
        self.assertTrue(_evaluate_condition({'source_count_gte': 2}, {'source_count': 3}))


class ExtractorBoundaryTests(unittest.TestCase):
    def inventory(self, sql, subjects=None):
        return extract_inventory(sql, 'source.sql', 'a' * 64, documented_subjects=subjects)

    def test_plain_create_tagged_body_and_exact_lines(self):
        sql = '-- SELECT fake\nCREATE FUNCTION demo.f() RETURNS void AS $body1$\nBEGIN\n/* nested /* SELECT */ comment */\nPERFORM demo.a();\nPERFORM demo.b();\nEND;\n$body1$ LANGUAGE plpgsql;\n'
        inv = self.inventory(sql)
        self.assertEqual([i['source_ref']['start_line'] for i in inv['items'] if i['kind'] == 'PERFORM'], [5, 6])
        self.assertEqual(inv['coverage_notes'], [])

    def test_selected_subject_excludes_neighbours(self):
        sql = 'CREATE VIEW demo.a AS SELECT 1;\nCREATE VIEW demo.b AS SELECT 2;'
        inv = self.inventory(sql, ['demo.b'])
        self.assertEqual(len(inv['items']), 2)
        self.assertTrue(all(i['source_ref']['start_line'] == 2 for i in inv['items']))

    def test_unknown_statement_and_empty_analysis_are_not_success(self):
        for body in ('FROBNICATE demo.x;', 'TRUNCATE demo.x;', ''):
            inv = self.inventory('CREATE FUNCTION demo.f() RETURNS void AS $$BEGIN\n' + body + '\nEND;$$ LANGUAGE plpgsql;')
            self.assertTrue(inv['coverage_notes'])


class EvidenceBoundaryTests(unittest.TestCase):
    def test_sibling_prefix_and_absolute_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = parent / 'sql'
            sibling = parent / 'sql-other'
            root.mkdir()
            sibling.mkdir()
            file = sibling / 'source.sql'
            file.write_bytes(b'SELECT 1;\r\n')
            ref = {'root': 'project', 'path': '../sql-other/source.sql', 'start_line': 1, 'end_line': 1, 'sha256': sha256_file(file)}
            with self.assertRaises(EvidenceError): validate_evidence(ref, {'project': root})
            ref['path'] = str(file)
            with self.assertRaises(EvidenceError): validate_evidence(ref, {'project': parent})

    def test_lf_crlf_bom_line_contract(self):
        self.assertEqual(extract_lines_text(b'\xef\xbb\xbfa\r\nb\r\n', 1, 2), 'a\nb')
        with self.assertRaises(ValueError): extract_lines_text(b'a\n', 2, 2)


if __name__ == '__main__':
    unittest.main()
