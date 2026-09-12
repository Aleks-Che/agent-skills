"""Regressions for false acceptance and crashes found during P0-01 review."""
import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_artifacts as fixtures

CHECKER = fixtures._schema_mod
ROOT = Path(__file__).resolve().parents[1]


def bundle():
    return {name: fixtures._fixture(name) for name in CHECKER.SCHEMA_FILES}


class ArtifactIntegrityRegressions(unittest.TestCase):
    def setUp(self):
        self.artifacts = bundle()
        self.facts = self.artifacts['facts']

    def reject(self, expected):
        errors = CHECKER.validate_artifacts(self.artifacts)
        self.assertTrue(any(expected in message for message in errors), errors)

    def test_empty_directory_is_not_a_complete_set(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(len(CHECKER.validate_artifact_set(directory)), 7)

    def test_each_required_artifact_is_required(self):
        for name in CHECKER.SCHEMA_FILES:
            with self.subTest(name=name):
                data = bundle()
                del data[name]
                self.assertIn(f'{name}: required artifact is missing', CHECKER.validate_artifacts(data))

    def test_partial_coverage_still_requires_its_facts(self):
        errors = CHECKER.validate_artifacts({'coverage': self.artifacts['coverage']}, required=['coverage'])
        self.assertTrue(any('requires facts.json' in error for error in errors), errors)

    def test_documented_object_must_exist(self):
        self.facts['documented_object_ids'] = ['missing']
        self.reject('documented_object_ids.0')

    def test_documented_object_ids_cannot_repeat_or_be_empty(self):
        for ids in ([], ['obj_1', 'obj_1']):
            with self.subTest(ids=ids):
                self.facts['documented_object_ids'] = ids
                self.reject('documented_object_ids')

    def test_each_fact_category_is_explicit_even_if_empty(self):
        for name in CHECKER.FACT_ARRAYS:
            with self.subTest(name=name):
                data = bundle()
                del data['facts'][name]
                self.assertTrue(any(f'facts: {name}' in e for e in CHECKER.validate_artifacts(data)))

    def test_global_fact_ids_cannot_collide_between_categories(self):
        self.facts['objects'][0]['id'] = 'op_001'
        self.reject('duplicate fact ID')

    def test_typed_internal_references_must_resolve(self):
        cases = [('definitions', 'object_id', 'missing'), ('columns', 'object_id', 'missing'),
                 ('operations', 'scope', 'missing'), ('operations', 'reads', ['missing']),
                 ('operations', 'writes', ['missing']), ('operations', 'calls', ['missing']),
                 ('operations', 'condition_ids', ['missing']), ('columns', 'source_columns', ['missing']),
                 ('formulas', 'operation_ids', ['missing']), ('formulas', 'column_ids', ['missing']),
                 ('formulas', 'condition_ids', ['missing']), ('conditions', 'operation_ids', ['missing']),
                 ('unknowns', 'related_facts', ['missing'])]
        for group, field, value in cases:
            with self.subTest(group=group, field=field):
                data = bundle()
                data['facts'][group][0][field] = value
                errors = CHECKER.validate_artifacts(data)
                self.assertTrue(any(f'{group}.0.{field}' in e and 'unknown reference' in e for e in errors), errors)

    def test_source_range_cannot_run_backwards(self):
        self.facts['objects'][0]['source_refs'][0]['end_line'] = 1
        self.reject('end_line: precedes')

    def test_source_ref_must_be_declared_in_inputs(self):
        self.facts['operations'][0]['source_refs'][0]['path'] = 'undeclared.sql'
        self.reject('source is absent from inputs')

    def test_source_ref_hash_must_match_declaration(self):
        self.facts['operations'][0]['source_refs'][0]['sha256'] = 'f' * 64
        self.reject('differs from inputs')

    def test_shared_source_cannot_have_conflicting_hashes(self):
        self.artifacts['manifest']['sql_files'][0]['sha256'] = 'f' * 64
        self.reject('conflicts with source')

    def test_input_paths_cannot_repeat(self):
        self.facts['inputs'].append(copy.deepcopy(self.facts['inputs'][0]))
        self.reject('duplicate source')

    def test_page_ids_must_match(self):
        self.artifacts['coverage']['page_id'] = 'other-page'
        self.reject('page_id mismatch')

    def test_documented_object_page_id_must_match_run(self):
        self.facts['objects'][0]['page_id'] = 'other-page'
        self.reject('objects.0.page_id')

    def test_context_objects_are_not_required_to_have_coverage(self):
        self.facts['objects'][1]['page_id'] = 'separate-context-page'
        self.assertNotIn('orders', self.artifacts['coverage']['entries'])
        self.assertEqual(CHECKER.validate_artifacts(self.artifacts), [])

    def test_scoped_cte_is_representable(self):
        self.facts['objects'].append({'id': 'cte_recent', 'kind': 'cte', 'name': 'recent',
                                     'scope': 'obj_1', 'physical': False,
                                     'source_refs': copy.deepcopy(self.facts['operations'][0]['source_refs'])})
        self.facts['operations'][0]['reads'].append('cte_recent')
        self.assertEqual(CHECKER.validate_artifacts(self.artifacts), [])

    def test_duplicate_check_ids_are_rejected(self):
        for artifact, field in [('validation', 'checks'), ('validation_plan', 'required_checks')]:
            with self.subTest(artifact=artifact):
                data = bundle()
                data[artifact][field].append(copy.deepcopy(data[artifact][field][0]))
                self.assertTrue(any('duplicate' in e for e in CHECKER.validate_artifacts(data)))

    def test_inventory_anchor_cannot_repeat(self):
        self.artifacts['inventory']['items'].append(copy.deepcopy(self.artifacts['inventory']['items'][0]))
        self.reject('duplicate source anchor')

    def test_plan_anchor_must_exist_in_inventory(self):
        self.artifacts['validation_plan']['required_checks'][-1]['inventory_anchor']['ordinal'] = 99
        self.reject('unknown source anchor')

    def test_inventory_references_do_not_depend_on_fact_ids(self):
        self.facts['operations'][0]['id'] = 'renamed_operation'
        self.facts['formulas'][0]['operation_ids'] = ['renamed_operation']
        self.facts['conditions'][0]['operation_ids'] = ['renamed_operation']
        self.artifacts['coverage']['entries']['renamed_operation'] = self.artifacts['coverage']['entries'].pop('op_001')
        self.assertEqual(CHECKER.validate_artifacts(self.artifacts), [])

    def test_report_plan_reference_must_resolve(self):
        self.artifacts['validation']['checks'][0]['plan_check_id'] = 'missing'
        self.reject('plan_check_id: unknown reference')

    def test_decision_check_reference_must_resolve(self):
        self.artifacts['decision']['blocking_defects'] = ['missing']
        self.reject('blocking_defects.0: unknown reference')

    def test_invalid_timestamp_is_rejected(self):
        self.artifacts['manifest']['created_at'] = 'not a date'
        self.reject('created_at')

    def test_manifest_requires_its_artifact_references(self):
        del self.artifacts['manifest']['artifacts']['facts']
        self.reject('manifest: artifacts.facts')

    def test_manifest_cannot_substitute_another_artifact_path(self):
        self.artifacts['manifest']['artifacts']['facts']['path'] = 'inventory.json'
        self.reject('artifacts.facts.path')

    def test_malformed_embedded_source_in_freeform_details_does_not_crash(self):
        self.artifacts['inventory']['items'][0]['details'] = {
            'example': {'path': [], 'sha256': 'f' * 64, 'start_line': 'bad', 'end_line': 1}}
        self.reject('malformed embedded source reference')

    def test_empty_coverage_target_does_not_count_as_reference(self):
        self.artifacts['coverage']['entries']['op_001'] = []
        self.reject('coverage: entries.op_001')

    def test_known_target_type_needs_its_own_evidence(self):
        del self.facts['columns'][0]['type_evidence']
        self.reject('type_evidence')

    def test_expression_type_needs_separate_evidence(self):
        self.facts['columns'][0]['type_expression'] = 'numeric'
        self.reject('type_expression: required field')

    def test_honest_unknown_type_is_representable_without_fabricated_evidence(self):
        self.facts['columns'][0]['type_target'] = None
        self.facts['columns'][0].pop('type_evidence')
        self.facts['definitions'][0].update(status='not_found', source_refs=[])
        self.facts['unknowns'][0].update(what='target type', related_facts=['col_001'])
        self.assertEqual(CHECKER.validate_artifacts(self.artifacts), [])

    def test_unresolved_and_inapplicable_checks_can_have_empty_evidence(self):
        for status in ('inconclusive', 'not_applicable'):
            with self.subTest(status=status):
                data = bundle()
                data['validation']['checks'][0].update(status=status, evidence=[])
                self.assertEqual(CHECKER.validate_artifacts(data), [])

    def test_resolved_checks_require_evidence(self):
        for status in ('ok', 'defect'):
            with self.subTest(status=status):
                self.artifacts['validation']['checks'][0].update(status=status, evidence=[])
                self.reject('evidence')

    def test_malformed_types_are_errors_not_tracebacks(self):
        for value in ([], {}, None, 12, True):
            with self.subTest(value=value):
                self.facts['objects'][0]['id'] = value
                self.reject('objects.0.id')
        for value in ([], None, 'text'):
            with self.subTest(root=value):
                self.assertTrue(CHECKER.validate_artifacts({'facts': value}, required=['facts']))

    def test_whitespace_only_evidence_is_invalid(self):
        self.artifacts['validation']['checks'][0]['evidence'] = ['   ']
        self.reject('evidence')

    def test_migration_order_links_to_inputs(self):
        self.facts['definitions'][0]['migration_order'] = ['missing.sql']
        self.reject('migration_order.0: unknown reference')

    def test_saved_invalid_fixtures_are_rejected(self):
        for path in (fixtures.FIXTURES_DIR / 'invalid').glob('*.json'):
            with self.subTest(path=path.name):
                name = 'facts' if path.name.startswith('facts_') else 'coverage'
                self.assertTrue(CHECKER.validate_artifacts({name: CHECKER.read_json(path)}, required=[name]))

    def test_old_report_does_not_pass_the_v2_contract(self):
        self.artifacts['validation'].pop('schema_version')
        self.reject('missing schema_version')


class ArtifactCLIRegressions(unittest.TestCase):
    def run_cli(self, *args):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/artifact_schema.py'), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertNotIn('Traceback', result.stderr)
        return result, json.loads(result.stdout or result.stderr)

    def test_complete_fixture_is_structurally_valid_but_not_publication_permission(self):
        result, data = self.run_cli(fixtures.FIXTURES_DIR / 'valid')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data['artifact_count'], 7)
        self.assertEqual(data['mode'], 'complete')
        self.assertIs(data['publication_authorized'], False)

    def test_partial_stage_is_explicit_in_output_and_ignores_unrelated_file_count(self):
        with tempfile.TemporaryDirectory() as directory:
            fixtures.write_artifacts(Path(directory), {'facts': fixtures.valid_facts(), 'unrelated': {}})
            result, data = self.run_cli(directory, '--artifacts', 'facts')
            self.assertEqual(result.returncode, 0)
            self.assertEqual(data['mode'], 'partial')
            self.assertEqual(data['artifact_count'], 1)

    def test_empty_directory_returns_contract_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result, data = self.run_cli(directory)
            self.assertEqual(result.returncode, 1)
            self.assertIs(data['valid'], False)

    def test_invalid_json_duplicate_keys_and_nonfinite_numbers_return_input_error(self):
        for raw in ('{invalid', '{"run_id":"one","run_id":"two"}', '{"schema_version":NaN}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'facts.json').write_text(raw, encoding='utf-8')
                result, data = self.run_cli(directory)
                self.assertEqual(result.returncode, 2)
                self.assertIs(data['publication_authorized'], False)

    def test_unreadable_artifact_returns_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'facts.json').mkdir()
            result, _ = self.run_cli(directory)
            self.assertEqual(result.returncode, 2)

    def test_utf8_bom_input_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'facts.json').write_text(json.dumps(fixtures.valid_facts()), encoding='utf-8-sig')
            result, _ = self.run_cli(directory, '--artifacts', 'facts')
            self.assertEqual(result.returncode, 0)

    def test_missing_schema_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(CHECKER, 'SCHEMAS_DIR', Path(directory)):
            with self.assertRaises(CHECKER.ArtifactInputError):
                CHECKER.load_schemas()

    def test_missing_dependency_produces_a_diagnostic(self):
        with patch.object(CHECKER, 'jsonschema', None), contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = CHECKER.main([str(fixtures.FIXTURES_DIR / 'valid')])
            self.assertEqual(code, 2)
            self.assertIn('requirements.txt', json.loads(stderr.getvalue())['error'])

    def test_missing_optional_datetime_checker_is_not_silent(self):
        formats = CHECKER.jsonschema.FormatChecker()
        formats.checkers.pop('date-time')
        with patch.object(CHECKER.jsonschema, 'FormatChecker', return_value=formats):
            with self.assertRaisesRegex(CHECKER.ArtifactInputError, 'date-time checker'):
                CHECKER.validate_schema(fixtures.valid_manifest(), CHECKER.load_schemas()['manifest'], 'manifest')

    def test_existing_migration_manifest_has_a_separate_cli_mode(self):
        result, data = self.run_cli('--migration-manifest', ROOT / 'examples/migrations/manifest.json')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data['mode'], 'migration_manifest')

    def test_migration_manifest_rejects_empty_and_duplicate_order(self):
        for files in ([], ['001.sql', '001.sql']):
            with self.subTest(files=files), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'manifest.json'
                path.write_text(json.dumps({'dialect': 'postgres', 'version': '15', 'ordered_files': files}), encoding='utf-8')
                result, _ = self.run_cli('--migration-manifest', path)
                self.assertEqual(result.returncode, 1)


if __name__ == '__main__':
    unittest.main()
