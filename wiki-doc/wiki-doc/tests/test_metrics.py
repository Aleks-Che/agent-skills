"""P2-02: wiki health metrics — page, source, check, broken-link and iteration stats."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from bundle_fixture import make_bundle, seal, PAGE_ID, PACKAGE
from build_bundle import build, finish
from artifact_schema import read_json
from publish import prepare, publish, recover
from metrics import (
    compute_metrics, _page_metrics, _source_metrics, _check_metrics,
    _broken_link_metrics, _decision_accuracy, _iteration_metrics,
    _coverage_metrics, main, MetricsError,
)
from index import build_index
from wiki_store import hash_file, metadata_path


class MetricsBasicTests(unittest.TestCase):
    """Test individual metric components against a fresh published wiki."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)

    def test_page_metrics_from_index(self):
        index = build_index(self.wiki)
        pm = _page_metrics(index)
        self.assertEqual(pm['total'], 1)
        self.assertEqual(pm['managed'], 1)
        self.assertEqual(pm['legacy'], 0)
        self.assertEqual(pm['current'], 1)
        self.assertEqual(pm['changed'], 0)
        self.assertEqual(pm['missing'], 0)

    def test_page_metrics_with_legacy_page(self):
        (self.wiki / 'old.md').write_text('# legacy page\n')
        index = build_index(self.wiki)
        pm = _page_metrics(index)
        self.assertEqual(pm['total'], 2)
        self.assertEqual(pm['managed'], 1)
        self.assertEqual(pm['legacy'], 1)

    def test_page_metrics_changed_page(self):
        (self.wiki / PAGE_ID).write_text('manual edit')
        index = build_index(self.wiki)
        pm = _page_metrics(index)
        self.assertEqual(pm['changed'], 1)
        self.assertEqual(pm['current'], 0)

    def test_page_metrics_missing_page(self):
        (self.wiki / PAGE_ID).unlink()
        index = build_index(self.wiki)
        pm = _page_metrics(index)
        self.assertEqual(pm['missing'], 1)
        self.assertEqual(pm['current'], 0)

    def test_source_metrics_fresh(self):
        index = build_index(self.wiki)
        sm = _source_metrics(self.wiki, index)
        self.assertEqual(sm['outdated'], 0)
        self.assertEqual(sm['fresh'], sm['total_tracked'])
        self.assertEqual(sm['freshness_ratio'], 1.0)
        self.assertEqual(sm['by_reason'], {})

    def test_source_metrics_outdated(self):
        (self.run / 'source.sql').write_text('-- changed')
        index = build_index(self.wiki)
        sm = _source_metrics(self.wiki, index)
        self.assertEqual(sm['outdated'], 1)
        self.assertEqual(sm['freshness_ratio'], 0.0)
        self.assertEqual(sm['outdated_pages'], 1)
        self.assertEqual(sm['by_reason']['source_changed'], 1)

    def test_source_metrics_fresh_has_no_outdated_pages(self):
        index = build_index(self.wiki)
        sm = _source_metrics(self.wiki, index)
        self.assertEqual(sm['outdated_pages'], 0)

    def test_source_metrics_missing_source(self):
        (self.run / 'source.sql').unlink()
        index = build_index(self.wiki)
        sm = _source_metrics(self.wiki, index)
        self.assertEqual(sm['outdated'], 1)
        self.assertTrue(sm['by_reason'].get('source_missing', 0) >= 1
                        or sm['by_reason'].get('source_unreadable', 0) >= 1)

    def test_source_metrics_project_unavailable(self):
        self.run.rename(self.root / 'moved')
        index = build_index(self.wiki)
        sm = _source_metrics(self.wiki, index)
        self.assertEqual(sm['outdated'], 1)
        self.assertEqual(sm['by_reason'].get('project_root_unavailable', 0), 1)

    def test_check_metrics_from_decisions(self):
        decisions = _load_decisions(self.wiki)
        cm = _check_metrics(decisions)
        self.assertEqual(cm['total_decisions'], 1)
        self.assertEqual(cm['ready'], 1)
        self.assertEqual(cm['revise'], 0)
        self.assertEqual(cm['blocked'], 0)
        self.assertEqual(cm['ready_ratio'], 1.0)
        # The gate stamps a timestamp when issuing a decision.
        self.assertIsNotNone(cm['first_ready_at'])

    def test_check_metrics_first_ready_at_uses_earliest_timestamp(self):
        decisions = [
            {'decision': 'ready', 'timestamp': '2026-09-18T10:00:00Z'},
            {'decision': 'ready', 'timestamp': '2026-09-17T10:00:00Z'},
            {'decision': 'revise', 'timestamp': '2026-09-16T10:00:00Z'},
        ]
        cm = _check_metrics(decisions)
        self.assertEqual(cm['first_ready_at'], '2026-09-17T10:00:00Z')

    def test_coverage_metrics_from_decisions(self):
        decisions = [
            {'decision': 'ready', 'metrics': {'total_checks': 4, 'ok_count': 4,
                                              'defect_count': 0, 'inconclusive_count': 0,
                                              'not_applicable_count': 0}},
            {'decision': 'revise', 'metrics': {'total_checks': 4, 'ok_count': 3,
                                               'defect_count': 0, 'inconclusive_count': 1,
                                               'not_applicable_count': 0}},
        ]
        cv = _coverage_metrics(decisions)
        self.assertEqual(cv['total_checks'], 8)
        self.assertEqual(cv['resolved'], 7)
        self.assertEqual(cv['ok'], 7)
        self.assertEqual(cv['defect'], 0)
        self.assertEqual(cv['unknown'], 1)
        self.assertAlmostEqual(cv['coverage_ratio'], 7 / 8)
        self.assertEqual(cv['accuracy_ratio'], 1.0)

    def test_coverage_metrics_empty(self):
        cv = _coverage_metrics([])
        self.assertEqual(cv['total_checks'], 0)
        self.assertIsNone(cv['coverage_ratio'])
        self.assertIsNone(cv['accuracy_ratio'])

    def test_check_metrics_empty_wiki(self):
        empty = Path(self.tmp.name) / 'empty_wiki'
        empty.mkdir()
        cm = _check_metrics([])
        self.assertEqual(cm['total_decisions'], 0)
        self.assertIsNone(cm['ready_ratio'])
        self.assertIsNone(cm['first_ready_at'])

    def test_broken_link_metrics_clean(self):
        lint_result = {'issues': []}
        bl = _broken_link_metrics(lint_result)
        self.assertEqual(bl['total'], 0)
        self.assertEqual(bl['files_affected'], 0)
        self.assertEqual(bl['details'], [])

    def test_broken_link_metrics_with_issues(self):
        lint_result = {'issues': [
            {'file': 'a.md', 'code': 'link_invalid', 'message': 'broken', 'fix': 'fix it'},
            {'file': 'a.md', 'code': 'link_invalid', 'message': 'also broken', 'fix': 'fix it'},
            {'file': 'b.md', 'code': 'index_missing', 'message': 'not indexed', 'fix': 'add'},
            {'file': 'c.md', 'code': 'markdown_invalid', 'message': 'bad md', 'fix': 'fix'},
        ]}
        bl = _broken_link_metrics(lint_result)
        self.assertEqual(bl['total'], 3)  # 2 link_invalid + 1 index_missing
        self.assertEqual(bl['files_affected'], 2)  # a.md, b.md

    def test_decision_accuracy_no_mutations(self):
        da = _decision_accuracy(None)
        self.assertEqual(da['erroneous_ready'], 0)
        self.assertEqual(da['total_mutations_tested'], 0)
        self.assertIsNone(da['erroneous_ready_ratio'])

    def test_decision_accuracy_with_mutations(self):
        mutations = [
            {'decision': 'revise'},
            {'decision': 'blocked'},
            {'decision': 'ready'},  # erroneous
            {'decision': 'revise'},
        ]
        da = _decision_accuracy(mutations)
        self.assertEqual(da['erroneous_ready'], 1)
        self.assertEqual(da['total_mutations_tested'], 4)
        self.assertAlmostEqual(da['erroneous_ready_ratio'], 0.25)

    def test_decision_accuracy_nested_acceptance_file(self):
        data = {'mutations': {'total': 2, 'false_ready': 1, 'results': [
            {'decision': 'revise', 'false_ready': False},
            {'decision': 'ready', 'false_ready': True},
        ]}}
        da = _decision_accuracy(data)
        self.assertEqual(da['total_mutations_tested'], 2)
        self.assertEqual(da['erroneous_ready'], 1)

    def test_decision_accuracy_prefers_false_ready_flag(self):
        da = _decision_accuracy([{'decision': 'ready', 'false_ready': False}])
        self.assertEqual(da['erroneous_ready'], 0)
        self.assertEqual(da['total_mutations_tested'], 1)

    def test_iteration_metrics_after_publish(self):
        im = _iteration_metrics(self.wiki)
        self.assertGreaterEqual(im['total_publishes'], 1)
        self.assertGreaterEqual(im['unique_pages_published'], 1)
        self.assertIsNotNone(im['avg_iterations_per_page'])


def _load_decisions(wiki_root):
    """Helper to load decisions from a wiki."""
    from wiki_store import load_metadata, inside
    root = Path(wiki_root).resolve()
    records = load_metadata(root)
    decisions = []
    for record in records:
        archive = inside(root, record['bundle'])
        dp = archive / 'decision.json'
        if dp.is_file():
            decisions.append(read_json(dp))
    return decisions


class MetricsIntegrationTests(unittest.TestCase):
    """Test compute_metrics end-to-end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)

    def test_compute_metrics_returns_valid_structure(self):
        result = compute_metrics(self.wiki)
        self.assertEqual(result['schema_version'], 1)
        self.assertIn('generated_at', result)
        self.assertIn('pages', result)
        self.assertIn('sources', result)
        self.assertIn('checks', result)
        self.assertIn('coverage', result)
        self.assertIn('broken_links', result)
        self.assertIn('decision_accuracy', result)
        self.assertIn('iteration_stats', result)

    def test_compute_metrics_coverage_from_published_decision(self):
        result = compute_metrics(self.wiki)
        self.assertGreater(result['coverage']['total_checks'], 0)
        self.assertEqual(result['coverage']['unknown'], 0)
        self.assertEqual(result['coverage']['coverage_ratio'], 1.0)
        self.assertIsNotNone(result['checks']['first_ready_at'])

    def test_compute_metrics_on_empty_wiki(self):
        empty = Path(self.tmp.name) / 'empty'
        empty.mkdir()
        result = compute_metrics(empty)
        self.assertEqual(result['pages']['total'], 0)
        self.assertEqual(result['checks']['total_decisions'], 0)
        self.assertEqual(result['coverage']['total_checks'], 0)
        self.assertIsNone(result['coverage']['coverage_ratio'])

    def test_compute_metrics_with_mutations(self):
        mutations = [{'decision': 'revise'}, {'decision': 'blocked'}]
        result = compute_metrics(self.wiki, mutation_results=mutations)
        self.assertEqual(result['decision_accuracy']['total_mutations_tested'], 2)
        self.assertEqual(result['decision_accuracy']['erroneous_ready'], 0)

    def test_compute_metrics_reflects_source_drift(self):
        (self.run / 'source.sql').write_text('-- drift')
        result = compute_metrics(self.wiki)
        self.assertEqual(result['sources']['outdated'], 1)
        self.assertNotEqual(result['sources']['freshness_ratio'], 1.0)

    def test_compute_metrics_reflects_manual_edit(self):
        (self.wiki / PAGE_ID).write_text('manual edit')
        result = compute_metrics(self.wiki)
        self.assertEqual(result['pages']['changed'], 1)

    def test_compute_metrics_nonexistent_dir(self):
        with self.assertRaises(MetricsError):
            compute_metrics(Path(self.tmp.name) / 'nope')


class MetricsCLITests(unittest.TestCase):
    """Test CLI interface."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)

    def test_cli_json_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = main([str(self.wiki), '--json'])
        self.assertEqual(result, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['pages']['managed'], 1)

    def test_cli_human_output(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = main([str(self.wiki)])
        self.assertEqual(result, 0)
        self.assertIn('Pages:', out.getvalue())
        self.assertIn('Sources:', out.getvalue())
        self.assertIn('Decisions:', out.getvalue())
        self.assertIn('Coverage:', out.getvalue())

    def test_cli_nonexistent_wiki(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = main([str(self.root / 'nope')])
        self.assertEqual(result, 1)
        self.assertIn('error', json.loads(err.getvalue()))

    def test_cli_with_mutation_results(self):
        mutation_file = self.root / 'mutations.json'
        mutation_file.write_text(json.dumps([
            {'decision': 'revise'},
            {'decision': 'blocked'},
        ]))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = main([str(self.wiki), '--json', '--mutation-results', str(mutation_file)])
        self.assertEqual(result, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data['decision_accuracy']['total_mutations_tested'], 2)
        self.assertEqual(data['decision_accuracy']['erroneous_ready'], 0)

    def test_cli_erroneous_ready_shows_in_human_output(self):
        mutation_file = self.root / 'mutations.json'
        mutation_file.write_text(json.dumps([
            {'decision': 'ready'},
            {'decision': 'ready'},
            {'decision': 'revise'},
        ]))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = main([str(self.wiki), '--mutation-results', str(mutation_file)])
        self.assertEqual(result, 0)
        self.assertIn('2/3 erroneous ready', out.getvalue())

    def test_cli_consumes_p1_acceptance_mutations(self):
        acceptance = PACKAGE.parent / 'P1-ACCEPTANCE.json'
        if not acceptance.is_file():
            self.skipTest('P1-ACCEPTANCE.json not present')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = main([str(self.wiki), '--json', '--mutation-results', str(acceptance)])
        self.assertEqual(result, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data['decision_accuracy']['total_mutations_tested'], 28)
        self.assertEqual(data['decision_accuracy']['erroneous_ready'], 0)


class MetricsSchemaValidationTests(unittest.TestCase):
    """Verify output validates against the metrics schema."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        seal(self.run)
        publish(self.run, self.wiki)

    def test_schema_validates(self):
        import sys
        sys.path.insert(0, str(PACKAGE / 'scripts'))
        from artifact_schema import validate_schema, load_schemas
        result = compute_metrics(self.wiki)
        schemas = load_schemas()
        metrics_schema = read_json(PACKAGE / 'schemas' / 'metrics.schema.json')
        errors = validate_schema(result, metrics_schema, 'metrics')
        self.assertEqual(errors, [], f'Schema validation errors: {errors}')

    def test_schema_validates_with_mutations(self):
        result = compute_metrics(self.wiki, mutation_results=[
            {'decision': 'revise'}, {'decision': 'ready'}
        ])
        metrics_schema = read_json(PACKAGE / 'schemas' / 'metrics.schema.json')
        from artifact_schema import validate_schema
        errors = validate_schema(result, metrics_schema, 'metrics')
        self.assertEqual(errors, [], f'Schema validation errors: {errors}')


class MetricsMultiPageTests(unittest.TestCase):
    """Test metrics with multiple published pages."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wiki = self.root / 'wiki'

        # Publish first page
        run1 = self.root / 'run1'
        run1.mkdir()
        make_bundle(run1)
        prepare(run1, self.wiki)
        self.assertEqual(seal(run1)['decision'], 'ready')
        publish(run1, self.wiki)

        # Publish second page (different SQL subject)
        run2 = self.root / 'run2'
        run2.mkdir()
        source2 = run2 / 'source.sql'
        source2.write_text(
            'CREATE TABLE demo.items (id integer, name text);\n'
            'CREATE FUNCTION demo.get_item(p_id integer) RETURNS text\n'
            'LANGUAGE sql AS $$ SELECT name FROM demo.items WHERE id = p_id; $$;\n'
        )
        result = build(source2, run2, project_root=self.root, subject='demo.get_item')
        self.assertEqual(result['decision'], 'ready', result)
        prepare(run2, self.wiki)
        result = finish(run2, sql_files=[source2], context=[], project_root=self.root,
                        wiki_root=self.wiki)
        self.assertEqual(result['decision'], 'ready', result)
        publish(run2, self.wiki, project_root=self.root)

    def test_multi_page_metrics(self):
        result = compute_metrics(self.wiki)
        self.assertEqual(result['pages']['total'], 2)
        self.assertEqual(result['pages']['managed'], 2)
        self.assertEqual(result['checks']['total_decisions'], 2)
        self.assertEqual(result['checks']['ready'], 2)
        self.assertEqual(result['checks']['ready_ratio'], 1.0)
        self.assertGreaterEqual(result['iteration_stats']['total_publishes'], 2)
        self.assertGreaterEqual(result['iteration_stats']['unique_pages_published'], 2)


class MetricsEdgeCaseTests(unittest.TestCase):
    """Edge cases and error handling."""

    def test_empty_wiki_no_pages(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = Path(tmp.name) / 'empty_wiki'
        empty.mkdir()
        result = compute_metrics(empty)
        self.assertEqual(result['pages']['total'], 0)
        self.assertEqual(result['sources']['freshness_ratio'], None)
        self.assertEqual(result['checks']['ready_ratio'], None)

    def test_decision_accuracy_all_correct(self):
        da = _decision_accuracy([{'decision': 'revise'}, {'decision': 'blocked'}])
        self.assertEqual(da['erroneous_ready'], 0)
        self.assertEqual(da['erroneous_ready_ratio'], 0.0)

    def test_decision_accuracy_all_erroneous(self):
        da = _decision_accuracy([{'decision': 'ready'}, {'decision': 'ready'}])
        self.assertEqual(da['erroneous_ready'], 2)
        self.assertEqual(da['erroneous_ready_ratio'], 1.0)


if __name__ == '__main__':
    unittest.main()
