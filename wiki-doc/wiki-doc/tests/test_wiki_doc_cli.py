"""Tests for the unified CLI wrapper wiki_doc.py."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from wiki_doc import build_parser, main, _version

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class VersionTests(unittest.TestCase):
    def test_version_returns_string(self):
        v = _version()
        self.assertIsInstance(v, str)
        self.assertRegex(v, r'^\d+\.\d+\.\d+')

    def test_version_command(self):
        rc = main(['version'])
        self.assertEqual(rc, 0)


class ParserTests(unittest.TestCase):
    def test_all_commands_registered(self):
        p = build_parser()
        sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        for name in ('inventory', 'plan', 'prepare', 'validate', 'lint', 'publish', 'recover',
                     'query', 'metrics', 'identity', 'version'):
            self.assertIn(name, sub.choices)

    def test_unknown_command_fails(self):
        with self.assertRaises(SystemExit):
            main(['nonexistent'])


class InventoryCommandTests(unittest.TestCase):
    def test_inventory_basic(self):
        sql = EXAMPLES / '01_no_target_ddl.sql'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'inventory.json'
            rc = main(['inventory', str(sql),
                        '-o', str(out)])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertIn('items', data)
            self.assertGreater(len(data['items']), 0)

    def test_inventory_to_stdout(self, capsys=None):
        sql = EXAMPLES / '01_no_target_ddl.sql'
        rc = main(['inventory', str(sql)])
        self.assertEqual(rc, 0)


class PlanCommandTests(unittest.TestCase):
    def test_plan_from_inventory(self):
        sql = EXAMPLES / '01_no_target_ddl.sql'
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / 'inventory.json'
            self.assertEqual(main(['inventory', str(sql), '-o', str(inv)]), 0)
            plan = Path(tmp) / 'plan.json'
            self.assertEqual(main(['plan', str(inv), '-o', str(plan)]), 0)
            data = json.loads(plan.read_text(encoding='utf-8'))
            self.assertIn('required_checks', data)


class IdentityCommandTests(unittest.TestCase):
    def test_identity_compute(self):
        rc = main(['identity', 'compute',
                    '--kind', 'function',
                    '--schema', 'demo',
                    '--name', 'test_fn',
                    '--arg-types', 'integer', 'text'])
        self.assertEqual(rc, 0)

    def test_identity_check(self):
        rc = main(['identity', 'check',
                    'function+demo+test_fn+(integer,text)',
                    '--existing-ids', 'some-other-page'])
        self.assertEqual(rc, 0)


class LintCommandTests(unittest.TestCase):
    def test_lint_empty_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(['lint', tmp]), 0)


class QueryCommandTests(unittest.TestCase):
    def test_query_outdated_empty_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(['query', tmp, '--mode', 'outdated']), 0)


class PublishCommandTests(unittest.TestCase):
    def test_publish_missing_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(['publish', str(Path(tmp) / 'missing'), tmp, '--dry-run'])
            self.assertEqual(rc, 1)


class MetricsCommandTests(unittest.TestCase):
    def test_metrics_empty_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(['metrics', tmp]), 0)


class ValidateCommandTests(unittest.TestCase):
    def test_validate_missing_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / 'empty'
            empty.mkdir()
            rc = main(['validate', str(empty),
                        '--root', 'project', str(EXAMPLES),
                        '--root', 'wiki', str(tmp)])
            self.assertEqual(rc, 2)


class OperationalReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.run = self.root / 'run'
        self.wiki = self.root / 'wiki'
        self.package = EXAMPLES.parent

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(a) for a in args])
        return rc, out.getvalue(), err.getvalue()

    def json_call(self, *args):
        rc, out, err = self.call(*args)
        self.assertEqual(rc, 0, (out, err))
        return json.loads(out)

    def table_bundle(self):
        from build_bundle import build
        source = self.project / 'sql' / 'table.sql'
        source.parent.mkdir()
        source.write_text('CREATE TABLE demo.t (id integer);', encoding='utf-8')
        context = self.project / 'context.sql'
        context.write_text('CREATE TABLE demo.other (value text);', encoding='utf-8')
        result = build(source, self.run, project_root=self.project, subject='table+demo+t', context=[context])
        self.assertTrue(result['publication_authorized'], result.get('errors'))
        return source, context

    def snapshot(self):
        return {p.relative_to(self.wiki).as_posix(): p.read_bytes()
                for p in self.wiki.rglob('*') if p.is_file()} if self.wiki.exists() else {}

    def test_inventory_preserves_relative_paths_context_and_run(self):
        source, context = self.table_bundle()
        original = json.loads((self.run / 'inventory.json').read_text(encoding='utf-8'))
        result = self.json_call('inventory', source, '--project-root', self.project,
                               '--run-id', original['run_id'], '--version', '15',
                               '--subjects', 'table+demo+t', '--context', context)
        self.assertEqual(result, original)
        self.assertEqual(result['inputs'][0]['path'], 'sql/table.sql')

    def test_inventory_reconstructs_ordered_migrations(self):
        result = self.json_call('inventory', EXAMPLES / '08_alter_migration.sql',
                               '--project-root', EXAMPLES, '--version', '15',
                               '--run-id', str(uuid.uuid4()),
                               '--subjects', 'migration+08_alter_migration.sql+08',
                               '--context', EXAMPLES / 'context.sql',
                               '--migration-manifest', EXAMPLES / 'migrations/manifest.json')
        self.assertFalse(result['coverage_notes'])
        state = next(i['details']['reconstruction'] for i in result['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(state['status'], 'resolved')
        amount = next(c for c in state['tables']['demo_migration.orders']['columns'] if c['name'] == 'amount')
        self.assertEqual(amount['type'], 'numeric(18, 4)')

    def test_inventory_errors_preserve_existing_output(self):
        out = self.root / 'inventory.json'
        out.write_text('keep', encoding='utf-8')
        rc, _, err = self.call('inventory', self.root / 'missing.sql', '-o', out)
        self.assertEqual(rc, 2)
        self.assertIn('error', json.loads(err))
        self.assertEqual(out.read_text(), 'keep')

    def test_plan_rejects_invalid_json_and_contract(self):
        path = self.root / 'inventory.json'
        for content in ('{', '[]', '{"run_id":"x","run_id":"y"}'):
            with self.subTest(content=content):
                path.write_text(content, encoding='utf-8')
                rc, _, err = self.call('plan', path)
                self.assertEqual(rc, 2)
                self.assertIn('error', json.loads(err))

    def test_identity_migration_does_not_require_schema_name(self):
        value = self.json_call('identity', 'compute', '--kind', 'migration',
                               '--migration-path', 'migrations/001.sql')
        self.assertTrue(value['canonical_key'].startswith('migration+'))

    def test_identity_preserves_existing_page_registry(self):
        registry = self.root / 'registry.json'
        registry.write_text(json.dumps({'old-page.md': 'table+demo+t'}), encoding='utf-8')
        result = self.json_call('identity', 'compute', '--kind', 'table', '--schema', 'demo',
                               '--name', 't', '--registry', registry)
        self.assertEqual(result['page_id'], 'old-page.md')

    def test_profile_plan_validation_publication_and_read_only_commands(self):
        from build_bundle import build, finish
        from bundle import write_manifest
        from evidence import sha256_file
        case = next(c for c in json.loads((EXAMPLES / 'cases.json').read_text())['cases'] if c['id'] == '11')
        source, profile = EXAMPLES / case['sql'], self.package / case['profile']
        context = [EXAMPLES / p for p in case['context']]
        result = build(source, self.run, project_root=EXAMPLES, subject=case['subjects'][0],
                       context=context, profile=profile, version='unknown')
        self.assertTrue(result['publication_authorized'], result.get('errors'))
        original = json.loads((self.run / 'validation_plan.json').read_text())
        policy = self.root / 'policy.json'
        policy.write_bytes((self.package / 'references/check-policy.json').read_bytes() + b'\n')
        actual = self.json_call('plan', self.run / 'inventory.json', '--page-id', original['page_id'],
                               '--subjects', case['subjects'][0], '--object-key', case['subjects'][0],
                               '--profile', profile, '--policy', policy)
        self.assertEqual(actual, original)
        self.assertTrue(any(c['source'] == 'profile' for c in actual['required_checks']))
        self.json_call('prepare', self.run, self.wiki)
        self.assertTrue(finish(self.run, sql_files=[source], context=context, project_root=EXAMPLES,
                               profile_path=profile, wiki_root=self.wiki)['publication_authorized'])
        manifest = json.loads((self.run / 'manifest.json').read_text())
        manifest['tool_versions']['policy_sha256'] = sha256_file(policy)
        write_manifest(manifest, self.run / 'manifest.json')
        checked = self.json_call('validate', self.run, '--root', 'project', EXAMPLES,
                                 '--root', 'wiki', self.wiki, '--profile', profile,
                                 '--policy', policy, '--write-decision', '--json')
        self.assertTrue(checked['publication_authorized'])
        args = ('publish', self.run, self.wiki, '--project-root', EXAMPLES,
                '--profile', profile, '--policy', policy)
        before = self.snapshot()
        dry = self.json_call(*args, '--dry-run')
        self.assertEqual(len(dry['changes']), 5)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.json_call(*args)['published'])
        self.assertTrue(self.json_call(*args)['idempotent'])
        before = self.snapshot()
        page = self.json_call('query', self.wiki, '--mode', 'page', '--target', original['page_id'], '--json')
        self.assertIn('dependencies', page)
        self.assertTrue(self.json_call('lint', self.wiki, '--project-root', EXAMPLES, '--json')['valid'])
        self.assertEqual(self.json_call('metrics', self.wiki, '--json')['pages']['managed'], 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.json_call('recover', self.wiki), [])

    def test_publish_refuses_tampering_after_validation(self):
        from build_bundle import finish
        source, context = self.table_bundle()
        self.json_call('prepare', self.run, self.wiki)
        self.assertTrue(finish(self.run, sql_files=[source], context=[context],
                               project_root=self.project, wiki_root=self.wiki)['publication_authorized'])
        for name in ('decision.json', 'page.draft.md'):
            with self.subTest(artifact=name):
                path = self.run / name
                data = path.read_bytes()
                path.write_bytes(data + b' ')
                # decision whitespace alone is not a semantic mutation; change its decision.
                if name == 'decision.json':
                    decision = json.loads(data)
                    decision['decision'] = 'revise'
                    path.write_text(json.dumps(decision), encoding='utf-8')
                rc, _, _ = self.call('publish', self.run, self.wiki, '--project-root', self.project)
                self.assertEqual(rc, 1)
                self.assertFalse((self.wiki / 'index.md').exists())
                path.write_bytes(data)

    def test_journal_records_success_and_failure_outside_wiki(self):
        self.wiki.mkdir()
        journal = self.root / 'activity' / str(uuid.uuid4())
        self.assertEqual(self.call('--journal', journal, 'lint', self.wiki, '--json')[0], 0)
        self.assertEqual(self.call('--journal', journal, 'inventory', self.root / 'missing.sql')[0], 2)
        records = [json.loads(p.read_text()) for p in journal.glob('*.json')]
        self.assertEqual({r['exit_code'] for r in records}, {0, 2})
        self.assertTrue(all(r['state'] == 'finished' and r['finished_at'] >= r['started_at'] for r in records))
        self.assertEqual(self.snapshot(), {})

    def test_journal_interruption_stays_started(self):
        journal = self.root / 'activity'
        with patch('wiki_doc.cmd_version', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.call('--journal', journal, 'version')
        record = json.loads(next(journal.glob('*.json')).read_text())
        self.assertEqual(record['state'], 'started')
        self.assertNotIn('exit_code', record)

    def test_invalid_journal_prevents_command(self):
        journal = self.root / 'file'
        journal.write_text('occupied')
        with patch('wiki_doc.cmd_version') as operation:
            rc, _, err = self.call('--journal', journal, 'version')
        operation.assert_not_called()
        self.assertEqual(rc, 2)
        self.assertFalse(json.loads(err)['command_started'])

    def test_journal_retains_delegated_argument_error(self):
        journal = self.root / 'activity'
        rc, _, _ = self.call('--journal', journal, 'identity', 'compute', '--kind', 'invalid')
        self.assertEqual(rc, 2)
        record = json.loads(next(journal.glob('*.json')).read_text())
        self.assertEqual(record['exit_code'], 2)

    def test_concurrent_journal_calls_keep_separate_events(self):
        journal = self.root / 'activity'
        command = [sys.executable, '-B', str(self.package / 'scripts/wiki_doc.py'),
                   '--journal', str(journal), 'version']
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        try:
            results = [p.communicate(timeout=30) for p in processes]
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill(); p.communicate()
        self.assertEqual([p.returncode for p in processes], [0, 0], results)
        self.assertEqual(len(list(journal.glob('*.json'))), 2)

    def test_isolated_version_works_without_optional_dependencies(self):
        from run_regression import isolate
        case = json.loads((EXAMPLES / 'cases.json').read_text())['cases'][0]
        package, _, _ = isolate(case, self.root / 'isolated', EXAMPLES)
        result = subprocess.run([sys.executable, '-S', '-B', str(package / 'scripts/wiki_doc.py'), 'version'],
                                cwd=package, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), (self.package / 'VERSION').read_text().strip())
        self.assertTrue((package / 'docs/agent-response-templates.md').is_file())

    def test_missing_input_cli_process_returns_two_without_traceback(self):
        result = subprocess.run([sys.executable, '-B', str(self.package / 'scripts/wiki_doc.py'),
                                 'inventory', str(self.root / 'absent.sql')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stderr))

    def test_metrics_reads_mutation_report_file(self):
        self.wiki.mkdir()
        report = self.root / 'mutations.json'
        report.write_text(json.dumps({'results': [dict(case='01', subject='test', mutation='m1',
                                                       decision='revise', false_ready=False)]}))
        result = self.json_call('metrics', self.wiki, '--regression-report', report, '--json')
        self.assertEqual(result['erroneous_ready']['denominator'], 1)
        self.assertEqual(result['erroneous_ready']['numerator'], 0)


if __name__ == '__main__':
    unittest.main()
