"""P2-02: documentation quality metrics — schema, computation and CLI."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from bundle_fixture import make_bundle, seal, PAGE_ID, PACKAGE
sys.path.insert(0, str(PACKAGE / 'scripts'))
from artifact_schema import read_json, validate_schema
from build_bundle import build, finish
from metrics import (compute_metrics, _fraction,
                     _compute_fresh_sources, _compute_full_coverage,
                     _compute_first_pass_success, _compute_blocking_defects,
                     _compute_open_unknowns, _compute_outdated_pages,
                     _compute_broken_links, _compute_erroneous_ready,
                     _compute_average_iterations, main)
from publish import prepare, publish
from wiki_store import hash_file, metadata_path, atomic_json, load_metadata
from validation_gate import evaluate_bundle
from unittest.mock import patch


METRICS_SCHEMA = read_json(PACKAGE / 'schemas' / 'metrics.schema.json')


class MetricsSchemaTests(unittest.TestCase):
    def test_schema_is_valid_json_schema(self):
        self.assertIn('properties', METRICS_SCHEMA)
        self.assertIn('required', METRICS_SCHEMA)

    def test_valid_metrics_pass_schema(self):
        result = compute_metrics(self._wiki())
        # Won't have a real wiki, so just check the structure
        self.assertEqual(result['schema_version'], 1)
        self.assertIn('generated_at', result)

    def _wiki(self):
        """Create a minimal published wiki for testing."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run = root / 'run'; run.mkdir()
        wiki = root / 'wiki'
        make_bundle(run)
        prepare(run, wiki)
        seal(run)
        publish(run, wiki)
        return wiki


class FractionTests(unittest.TestCase):
    def test_fraction_normal(self):
        self.assertEqual(_fraction(3, 4), 75.0)

    def test_fraction_zero_denominator(self):
        self.assertIsNone(_fraction(0, 0))

    def test_fraction_full(self):
        self.assertEqual(_fraction(5, 5), 100.0)


class MetricsComputationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'; self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)

    def test_compute_metrics_returns_all_fields(self):
        result = compute_metrics(self.wiki)
        required = {'schema_version', 'generated_at', 'period', 'pages', 'fresh_sources',
                     'full_coverage', 'first_pass_success', 'blocking_defects',
                     'open_unknowns', 'outdated_pages', 'broken_links',
                     'erroneous_ready', 'average_iterations'}
        self.assertEqual(required, set(result.keys()))

    def test_metrics_schema_validation(self):
        result = compute_metrics(self.wiki)
        errors = validate_schema(result, METRICS_SCHEMA, 'metrics')
        self.assertEqual(errors, [])

    def test_page_counts(self):
        result = compute_metrics(self.wiki)
        self.assertEqual(result['pages']['managed'], 1)
        self.assertEqual(result['pages']['legacy'], 0)
        self.assertEqual(result['pages']['total'], 1)

    def test_fresh_sources_current(self):
        result = compute_metrics(self.wiki)
        fs = result['fresh_sources']
        self.assertEqual(fs['numerator'], 1)
        self.assertEqual(fs['denominator'], 1)
        self.assertEqual(fs['percent'], 100.0)
        self.assertEqual(fs['stale_pages'], [])

    def test_fresh_sources_stale(self):
        # Change the source file to make it stale
        (self.run / 'source.sql').write_text('-- changed')
        result = compute_metrics(self.wiki)
        fs = result['fresh_sources']
        self.assertEqual(fs['numerator'], 0)
        self.assertEqual(fs['denominator'], 1)
        self.assertEqual(fs['percent'], 0.0)
        self.assertEqual(len(fs['stale_pages']), 1)
        self.assertEqual(fs['stale_pages'][0]['reason'], 'source_changed')

    def test_full_coverage_ok(self):
        result = compute_metrics(self.wiki)
        fc = result['full_coverage']
        self.assertEqual(fc['numerator'], 1)
        self.assertEqual(fc['denominator'], 1)
        self.assertEqual(fc['percent'], 100.0)
        self.assertEqual(fc['incomplete_pages'], [])

    def test_first_pass_success(self):
        result = compute_metrics(self.wiki)
        fps = result['first_pass_success']
        self.assertEqual(fps['denominator'], 1)
        self.assertEqual(fps['percent'], 100.0)

    def test_blocking_defects_empty(self):
        result = compute_metrics(self.wiki)
        bd = result['blocking_defects']
        self.assertEqual(bd['total'], 0)
        self.assertEqual(bd['by_page'], [])

    def test_open_unknowns_include_documented_limitations(self):
        result = compute_metrics(self.wiki)
        ou = result['open_unknowns']
        expected = read_json(self.run / 'facts.json')['unknowns']
        self.assertEqual(ou['total'], len(expected))
        self.assertEqual(ou['by_page'][0]['unknown_ids'], ['fact:' + u['id'] for u in expected])

    def test_outdated_pages_empty(self):
        result = compute_metrics(self.wiki)
        op = result['outdated_pages']
        self.assertEqual(op['total'], 0)
        self.assertEqual(op['pages'], [])

    def test_outdated_pages_detects_drift(self):
        (self.run / 'source.sql').write_text('-- changed source')
        result = compute_metrics(self.wiki)
        op = result['outdated_pages']
        self.assertEqual(op['total'], 1)
        self.assertEqual(op['pages'][0]['page_id'], PAGE_ID)
        self.assertEqual(op['pages'][0]['reason'], 'source_changed')

    def test_broken_links_empty_for_valid_wiki(self):
        result = compute_metrics(self.wiki)
        bl = result['broken_links']
        self.assertEqual(bl['total'], 0)

    def test_erroneous_ready_without_regression_is_unmeasured(self):
        result = compute_metrics(self.wiki)
        er = result['erroneous_ready']
        self.assertEqual(er['numerator'], 0)
        self.assertEqual(er['denominator'], 0)
        self.assertIsNone(er['percent'])
        self.assertFalse(er['measured'])
        self.assertEqual(er['cases'], [])

    def test_erroneous_ready_with_regression_report(self):
        report = dict(results=[
            dict(case='01', subject='test', mutation='m1', decision='ready', false_ready=True),
            dict(case='02', subject='test2', mutation='m2', decision='revise', false_ready=False),
        ])
        result = compute_metrics(self.wiki, regression_report=report)
        er = result['erroneous_ready']
        self.assertEqual(er['numerator'], 1)
        self.assertEqual(er['denominator'], 2)
        self.assertEqual(er['percent'], 50.0)
        self.assertTrue(er['measured'])
        self.assertEqual(er['cases'][0]['case_id'], '01')
        self.assertEqual(er['cases'][0]['mutation'], 'm1')

    def test_average_iterations(self):
        result = compute_metrics(self.wiki)
        self.assertIsNotNone(result['average_iterations'])
        self.assertGreaterEqual(result['average_iterations'], 1.0)

    def test_empty_wiki_metrics(self):
        empty_wiki = self.root / 'empty'; empty_wiki.mkdir()
        result = compute_metrics(empty_wiki)
        self.assertEqual(result['pages']['total'], 0)
        self.assertIsNone(result['fresh_sources']['percent'])
        self.assertIsNone(result['full_coverage']['percent'])
        self.assertIsNone(result['first_pass_success']['percent'])
        self.assertIsNone(result['average_iterations'])


class MetricsCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'; self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        seal(self.run)
        publish(self.run, self.wiki)

    def test_human_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main([str(self.wiki)])
        text = out.getvalue()
        self.assertIn('Pages:', text)
        self.assertIn('Fresh sources:', text)
        self.assertIn('Full coverage:', text)
        self.assertIn('Blocking defects:', text)

    def test_json_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main([str(self.wiki), '--json'])
        result = json.loads(out.getvalue())
        self.assertEqual(result['schema_version'], 1)
        self.assertIn('pages', result)

    def test_nonexistent_wiki(self):
        with contextlib.redirect_stderr(io.StringIO()):
            result = main([str(self.root / 'nonexistent')])
        self.assertEqual(result, 2)

    def test_regression_report_flag(self):
        report_path = self.root / 'report.json'
        report_path.write_text(json.dumps(dict(results=[
            dict(case='01', subject='test', mutation='m1', decision='ready', false_ready=True),
        ])))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main([str(self.wiki), '--json', '--regression-report', str(report_path)])
        result = json.loads(out.getvalue())
        self.assertEqual(result['erroneous_ready']['numerator'], 1)
        self.assertEqual(result['erroneous_ready']['denominator'], 1)
        self.assertTrue(result['erroneous_ready']['measured'])

    def test_empty_regression_report_is_unmeasured(self):
        report_path = self.root / 'report.json'
        report_path.write_text(json.dumps(dict(results=[])))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main([str(self.wiki), '--json', '--regression-report', str(report_path)])
        result = json.loads(out.getvalue())
        self.assertEqual(result['erroneous_ready']['denominator'], 0)
        self.assertFalse(result['erroneous_ready']['measured'])


class MultiPageMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wiki = self.root / 'wiki'

    def _publish_page(self, run_name):
        run = self.root / run_name; run.mkdir()
        make_bundle(run)
        prepare(run, self.wiki)
        seal(run)
        publish(run, self.wiki)

    def test_legacy_page_detected(self):
        self._publish_page('run1')
        # Use ASCII-compatible format matching LEGACY_KEY_RE in index.py
        (self.wiki / 'legacy.md').write_text('# Legacy page\ncanonical_key: `table+x+y`', encoding='utf-8')
        result = compute_metrics(self.wiki)
        self.assertEqual(result['pages']['legacy'], 1)
        self.assertEqual(result['pages']['total'], 2)


class StaleSourceDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'; self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        seal(self.run)
        publish(self.run, self.wiki)

    def test_missing_project_root(self):
        # Remove the project root
        import shutil
        shutil.rmtree(self.run)
        result = compute_metrics(self.wiki)
        fs = result['fresh_sources']
        self.assertEqual(fs['numerator'], 0)
        self.assertTrue(any(s['reason'] == 'project_root_unavailable' for s in fs['stale_pages']))

    def test_missing_source_file(self):
        (self.run / 'source.sql').unlink()
        result = compute_metrics(self.wiki)
        fs = result['fresh_sources']
        self.assertEqual(fs['numerator'], 0)
        self.assertTrue(any(s['reason'] == 'source_missing' for s in fs['stale_pages']))


class IterationMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)

    def _publish(self):
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)
        return compute_metrics(self.wiki)

    def test_failed_gate_then_corrected_before_first_publication(self):
        # Begin a new observed cycle with a real failing check, then repair it.
        (self.run / 'decision.json').unlink()
        (self.run / 'validation-history.json').unlink()
        original = (self.run / 'coverage.json').read_bytes()
        coverage = read_json(self.run / 'coverage.json')
        coverage['entries'] = []
        atomic_json(self.run / 'coverage.json', coverage)
        self.assertNotEqual(seal(self.run)['decision'], 'ready')
        (self.run / 'coverage.json').write_bytes(original)
        result = self._publish()
        self.assertEqual(result['first_pass_success']['numerator'], 0)
        self.assertTrue(result['first_pass_success']['measured'])
        self.assertEqual(result['average_iterations'], 2.0)
        record = load_metadata(self.wiki)[0]
        history = read_json(self.wiki / record['bundle'] / 'decision.json')['validation_history']
        self.assertNotEqual(history['attempts'][0]['decision'], 'ready')
        self.assertEqual(history['attempts'][-1]['decision'], 'ready')

    def test_successful_rechecks_are_not_correction_iterations(self):
        seal(self.run)
        result = self._publish()
        self.assertEqual(result['first_pass_success']['percent'], 100)
        self.assertEqual(result['average_iterations'], 1.0)

    def test_read_only_gate_does_not_record_attempt(self):
        before = {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()}
        self.assertTrue(evaluate_bundle(self.run)['publication_authorized'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()})

    def test_historical_decision_without_history_stays_unmeasured(self):
        (self.run / 'validation-history.json').unlink()
        decision = read_json(self.run / 'decision.json')
        decision.pop('validation_history')
        atomic_json(self.run / 'decision.json', decision)
        result = self._publish()
        self.assertFalse(result['first_pass_success']['measured'])
        self.assertEqual(result['first_pass_success']['unmeasured_pages'], [PAGE_ID])
        self.assertIsNone(result['first_pass_success']['percent'])
        self.assertIsNone(result['average_iterations'])

    def test_missing_history_does_not_fall_back_to_publication_journal(self):
        result = self._publish()
        record = load_metadata(self.wiki)[0]
        decision_path = self.wiki / record['bundle'] / 'decision.json'
        decision = read_json(decision_path)
        decision.pop('validation_history')
        atomic_json(decision_path, decision)
        result = compute_metrics(self.wiki)
        self.assertFalse(result['first_pass_success']['measured'])
        self.assertIsNone(result['average_iterations'])

    def test_corrupt_history_refuses_decision_issuance(self):
        (self.run / 'validation-history.json').write_text('[]')
        result = seal(self.run)
        self.assertFalse(result['publication_authorized'])

    def test_metrics_are_read_only(self):
        self._publish()
        before = {p: p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()}
        compute_metrics(self.wiki)
        self.assertEqual(before, {p: p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()})


class MetricsReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _wiki(self):
        run = self.root / 'run'
        run.mkdir()
        wiki = self.root / 'wiki'
        make_bundle(run)
        prepare(run, wiki)
        seal(run)
        publish(run, wiki)
        return run, wiki

    def test_multiple_stale_sources_count_as_one_page(self):
        refs = []
        for name in ('one.sql', 'two.sql'):
            source = self.root / name
            source.write_text('select 1;')
            refs.append(dict(path=name, sha256=hash_file(source)))
            source.write_text('select 2;')
        pages = [dict(page_id='p.md', status='managed', project_root=str(self.root), tracked_inputs=refs)]
        self.assertEqual(_compute_outdated_pages(self.root, pages)['total'], 1)
        fresh = _compute_fresh_sources(self.root, pages)
        self.assertEqual((fresh['numerator'], fresh['denominator']), (0, 1))

    def test_no_source_inventory_is_not_fresh(self):
        pages = [dict(page_id='p.md', status='managed', project_root=str(self.root), tracked_inputs=[])]
        self.assertEqual(_compute_fresh_sources(self.root, pages)['numerator'], 0)

    def test_corrupt_archive_is_not_silently_accepted(self):
        _, wiki = self._wiki()
        record = load_metadata(wiki)[0]
        (wiki / record['bundle'] / 'facts.json').write_text('{}')
        with self.assertRaises(ValueError):
            compute_metrics(wiki)

    def test_inflight_publication_is_not_silently_accepted(self):
        _, wiki = self._wiki()
        path = next((wiki / '.wiki-doc/journal').glob('*.json'))
        journal = read_json(path)
        journal['state'] = 'prepared'
        atomic_json(path, journal)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            compute_metrics(wiki)

    def test_changed_markdown_loses_fact_coverage(self):
        _, wiki = self._wiki()
        (wiki / PAGE_ID).write_text('# Page with no fact coverage\n', encoding='utf-8')
        result = compute_metrics(wiki)
        self.assertEqual(result['full_coverage']['numerator'], 0)
        self.assertEqual(result['full_coverage']['denominator'], 1)

    def test_broken_link_is_counted_on_current_page(self):
        _, wiki = self._wiki()
        page = wiki / PAGE_ID
        page.write_text(page.read_text(encoding='utf-8') + '\n[bad](absent.md)\n', encoding='utf-8')
        result = compute_metrics(wiki)
        self.assertEqual(result['broken_links']['total'], 1)
        self.assertEqual(result['broken_links']['by_page'][0]['page_id'], PAGE_ID)

    def test_documented_unknowns_are_counted(self):
        archive = self.root / 'bundle'
        archive.mkdir()
        atomic_json(archive / 'facts.json', dict(unknowns=[dict(id='missing_ddl')]))
        atomic_json(archive / 'validation.json', dict(checks=[
            dict(id='pending', status='inconclusive', blocking=False)]))
        result = _compute_open_unknowns(self.root, [dict(page_id='p.md', bundle='bundle')])
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['by_page'][0]['unknown_ids'], ['fact:missing_ddl', 'check:pending'])

    def test_lint_failure_cannot_become_zero_broken_links(self):
        with patch('lint.lint', side_effect=OSError('unreadable')):
            with self.assertRaises(OSError):
                _compute_broken_links(self.root)

    def test_positive_regression_report_is_not_mutation_evidence(self):
        with self.assertRaises(ValueError):
            _compute_erroneous_ready(dict(results=[dict(case='01', subject='p', decision='ready')]))

    def test_skipped_mutation_is_not_measured_success(self):
        result = _compute_erroneous_ready(dict(results=[
            dict(case='01', subject='p', mutation='m1', skipped=True),
            dict(case='01', subject='p', mutation='m2', decision='revise', false_ready=False)]))
        self.assertFalse(result['measured'])
        self.assertIsNone(result['percent'])
        self.assertEqual(result['denominator'], 2)

    def test_inconsistent_false_ready_is_rejected(self):
        with self.assertRaises(ValueError):
            _compute_erroneous_ready(dict(results=[
                dict(case='01', subject='p', mutation='m', decision='ready', false_ready=False)]))

    def test_unevaluated_mutation_is_not_a_successful_rejection(self):
        result = _compute_erroneous_ready(dict(results=[
            dict(case='01', subject='p', mutation='m', decision='blocked', false_ready=False, evaluated=False)]))
        self.assertFalse(result['measured'])
        self.assertIsNone(result['percent'])

    def test_deliberate_schema_mutation_rejection_is_measured(self):
        result = _compute_erroneous_ready(dict(results=[
            dict(case='01', subject='p', mutation='m', decision='blocked', false_ready=False,
                 input_error=True, evaluated=True)]))
        self.assertTrue(result['measured'])
        self.assertEqual(result['percent'], 0)

    def test_invalid_report_cli_returns_two(self):
        wiki = self.root / 'wiki'
        wiki.mkdir()
        report = self.root / 'report.json'
        report.write_text('[]')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(wiki), '--regression-report', str(report)]), 2)
        self.assertIn('mutation report', err.getvalue())

    def test_real_multiple_pages_and_republication(self):
        source = PACKAGE / 'examples/06_same_name_diff_schema.sql'
        context = [PACKAGE / 'examples/context.sql']
        wiki = self.root / 'wiki'
        for n, subject in enumerate(('function+core+orders_summary+()', 'function+archive+orders_summary+()',
                                     'function+core+orders_summary+()')):
            run = self.root / str(n)
            self.assertTrue(build(source, run, project_root=source.parent, subject=subject, context=context)['publication_authorized'])
            prepare(run, wiki)
            self.assertTrue(finish(run, sql_files=[source], context=context, project_root=source.parent, wiki_root=wiki)['publication_authorized'])
            publish(run, wiki, project_root=source.parent)
        result = compute_metrics(wiki)
        self.assertEqual(result['pages']['managed'], 2)
        self.assertEqual(result['full_coverage']['denominator'], 2)
        self.assertEqual(result['first_pass_success']['percent'], 100)
        self.assertEqual(result['average_iterations'], 1.0)
        expected = sum(len(read_json(wiki / r['bundle'] / 'facts.json')['unknowns']) for r in load_metadata(wiki))
        self.assertEqual(result['open_unknowns']['total'], expected)


if __name__ == '__main__':
    unittest.main()
