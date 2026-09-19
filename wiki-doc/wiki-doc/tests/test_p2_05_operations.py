"""Tests for P2-05: unified CLI wrapper, stage journal, version."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from _version import __version__
from stage_journal import StageJournal

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'scripts'


class VersionTests(unittest.TestCase):
    def test_version_is_string(self):
        self.assertIsInstance(__version__, str)

    def test_version_format(self):
        parts = __version__.split('.')
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())

    def test_version_importable(self):
        from _version import __version__ as v
        self.assertEqual(v, __version__)


class CliWrapperTests(unittest.TestCase):
    """Test wiki_doc.py as a subprocess."""

    def _run(self, *args, check=True):
        cmd = [sys.executable, '-B', str(SCRIPTS / 'wiki_doc.py')] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PACKAGE))
        if check:
            self.assertEqual(result.returncode, 0, f'Failed: {result.stderr}')
        return result

    def test_version(self):
        result = self._run('--version')
        self.assertIn(__version__, result.stdout)

    def test_help(self):
        result = self._run('--help')
        self.assertIn('inventory', result.stdout)
        self.assertIn('publish', result.stdout)
        self.assertIn('query', result.stdout)

    def test_no_subcommand_returns_2(self):
        cmd = [sys.executable, '-B', str(SCRIPTS / 'wiki_doc.py')]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PACKAGE))
        self.assertEqual(result.returncode, 2)

    def test_identity_help(self):
        result = self._run('identity', '--help')
        self.assertIn('compute', result.stdout)
        self.assertIn('check', result.stdout)

    def test_gate_help(self):
        result = self._run('gate', '--help')
        self.assertIn('--bundle', result.stdout)

    def test_metrics_help(self):
        result = self._run('metrics', '--help')
        self.assertIn('--json', result.stdout)

    def test_query_help(self):
        result = self._run('query', '--help')
        self.assertIn('--page', result.stdout)

    def test_lint_help(self):
        result = self._run('lint', '--help')
        self.assertIn('--json', result.stdout)

    def test_publish_help(self):
        result = self._run('publish', '--help')
        self.assertIn('--dry-run', result.stdout)

    def test_bundle_help(self):
        result = self._run('bundle', '--help')
        self.assertIn('create', result.stdout)
        self.assertIn('verify', result.stdout)

    def test_inventory_help(self):
        result = self._run('inventory', '--help')
        self.assertIn('--subjects', result.stdout)

    def test_plan_help(self):
        result = self._run('plan', '--help')
        self.assertIn('--page-id', result.stdout)

    def test_regression_help(self):
        result = self._run('regression', '--help')
        self.assertIn('--mode', result.stdout)

    def test_identity_compute_delegates(self):
        result = self._run('identity', 'compute', '--kind', 'function',
                           '--schema', 'demo', '--name', 'f')
        payload = json.loads(result.stdout)
        self.assertEqual(payload['canonical_key'], 'function+demo+f+()')

    def test_gate_missing_bundle_preserves_exit_code(self):
        cmd = [sys.executable, '-B', str(SCRIPTS / 'wiki_doc.py'),
               'gate', '--bundle', str(self._missing_dir())]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PACKAGE))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def _missing_dir(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name) / 'does-not-exist'


class StageJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wiki = Path(self.tmp.name)

    def test_record_and_read(self):
        journal = StageJournal(self.wiki)
        entry = journal.record('inventory', run_id='test-run', page_id='test-page', outcome='ok')
        self.assertEqual(entry['stage'], 'inventory')
        self.assertEqual(entry['run_id'], 'test-run')
        self.assertEqual(entry['page_id'], 'test-page')
        self.assertEqual(entry['outcome'], 'ok')
        self.assertIn('timestamp', entry)

        entries = journal.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['stage'], 'inventory')

    def test_multiple_entries(self):
        journal = StageJournal(self.wiki)
        journal.record('inventory', run_id='r1')
        journal.record('plan', run_id='r1')
        journal.record('draft', run_id='r1')
        journal.record('validation', run_id='r1')
        journal.record('decision', run_id='r1')
        journal.record('publish', run_id='r1')

        entries = journal.read()
        self.assertEqual(len(entries), 6)
        stages = [e['stage'] for e in entries]
        self.assertEqual(stages, ['inventory', 'plan', 'draft', 'validation', 'decision', 'publish'])

    def test_details(self):
        journal = StageJournal(self.wiki)
        journal.record('publish', run_id='r1', details={'page': 'test', 'index_updated': True})
        entries = journal.read()
        self.assertEqual(entries[0]['details']['page'], 'test')
        self.assertTrue(entries[0]['details']['index_updated'])

    def test_invalid_stage(self):
        journal = StageJournal(self.wiki)
        with self.assertRaises(ValueError):
            journal.record('invalid_stage')

    def test_valid_stages(self):
        journal = StageJournal(self.wiki)
        for stage in ('inventory', 'plan', 'draft', 'validation', 'decision',
                      'publish', 'lint', 'query', 'regression', 'error'):
            journal.record(stage, run_id='test')
        self.assertEqual(len(journal.read()), 10)

    def test_event_id_does_not_invent_run_id(self):
        journal = StageJournal(self.wiki)
        entry = journal.record('inventory')
        self.assertIsNone(entry['run_id'])
        self.assertEqual(len(entry['event_id']), 36)

    def test_clear(self):
        journal = StageJournal(self.wiki)
        journal.record('inventory', run_id='test')
        self.assertEqual(len(journal.read()), 1)
        journal.clear()
        self.assertEqual(len(journal.read()), 0)

    def test_read_empty(self):
        journal = StageJournal(self.wiki)
        self.assertEqual(journal.read(), [])

    def test_creates_directory(self):
        nested = self.wiki / 'a' / 'b' / 'c'
        journal = StageJournal(nested)
        journal.record('inventory', run_id='test')
        self.assertTrue((nested / '.wiki-doc' / 'journal.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
