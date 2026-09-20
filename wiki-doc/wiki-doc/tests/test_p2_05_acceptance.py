"""Operational contracts at the real CLI, filesystem and process boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bundle_fixture import PACKAGE, PAGE_ID, make_bundle, seal
from artifact_schema import read_json
from publish import prepare, publish
from stage_journal import StageJournal
from wiki_store import inside, WikiConflict


class OperationsAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        self.run_id = read_json(self.run / 'facts.json')['run_id']

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, '-B', str(PACKAGE / 'scripts/wiki_doc.py'),
                                 *map(str, args)], cwd=self.root, capture_output=True, text=True,
                                encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip().startswith(('{', '[')) else result

    def prepared(self):
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')

    def snapshot(self):
        return {p.relative_to(self.wiki).as_posix(): p.read_bytes()
                for p in self.wiki.rglob('*') if p.is_file()} if self.wiki.exists() else None

    def test_inventory_and_plan_delegate_real_inputs_from_other_cwd(self):
        inv = self.cli('inventory', self.run / 'source.sql', '--subjects', 'core.calc',
                       '--version', '15', '--run-id', self.run_id, '--project-root', self.run)
        self.assertEqual(inv, read_json(self.run / 'inventory.json'))
        plan = self.cli('plan', self.run / 'inventory.json', '--page-id', PAGE_ID)
        self.assertEqual(plan, read_json(self.run / 'validation_plan.json'))

    def test_coverage_pass_does_not_bypass_full_gate_or_publisher(self):
        self.prepared()
        coverage = self.cli('validate', '--bundle', self.run, '--json')
        self.assertTrue(coverage['valid'])
        self.assertFalse(coverage['publication_authorized'])
        self.assertTrue(self.cli('gate', '--bundle', self.run, '--json')['publication_authorized'])
        source = self.run / 'source.sql'
        source.write_bytes(source.read_bytes() + b'\n-- changed after validation\n')
        denied = self.cli('gate', '--bundle', self.run, '--json', code=1)
        self.assertFalse(denied['publication_authorized'])
        refused = self.cli('publish', 'publish', self.run, '--wiki', self.wiki, code=1)
        self.assertFalse(refused['published'])
        self.assertFalse((self.wiki / PAGE_ID).exists())

    def test_prepare_does_not_create_wiki(self):
        result = self.cli('publish', 'prepare', self.run, '--wiki', self.wiki)
        self.assertTrue(result['prepared'])
        self.assertTrue((self.run / 'publication.json').exists())
        self.assertFalse(self.wiki.exists())

    def test_dry_run_does_not_create_wiki_or_journal(self):
        self.prepared()
        result = self.cli('publish', 'publish', self.run, '--wiki', self.wiki, '--dry-run')
        self.assertTrue(result['dry_run'])
        self.assertFalse(self.wiki.exists())

    def test_failed_dry_run_does_not_create_wiki(self):
        self.cli('publish', 'publish', self.root / 'missing', '--wiki', self.wiki, '--dry-run', code=1)
        self.assertFalse(self.wiki.exists())

    def test_dry_run_preserves_existing_wiki_bytes(self):
        self.prepared()
        publish(self.run, self.wiki)
        self.prepared()
        before = self.snapshot()
        self.cli('publish', 'publish', self.run, '--wiki', self.wiki, '--dry-run')
        self.assertEqual(self.snapshot(), before)

    def test_inapplicable_mutation_flags_are_rejected(self):
        for mode in ('prepare', 'recover'):
            for flag in ('--dry-run', '--cleanup-run'):
                with self.subTest(mode=mode, flag=flag):
                    self.cli('publish', mode, self.run, '--wiki', self.wiki, flag, code=2)
                    self.assertFalse(self.wiki.exists())

    def test_gate_recheck_does_not_modify_wiki(self):
        self.cli('gate', '--bundle', self.run, '--root', 'wiki', self.wiki, '--json')
        self.assertFalse(self.wiki.exists())

    def test_gate_and_publish_journal_keep_actual_identity(self):
        self.prepared()
        self.cli('gate', '--bundle', self.run, '--root', 'wiki', self.wiki, '--write-decision', '--json')
        self.cli('publish', 'publish', self.run, '--wiki', self.wiki)
        result = self.cli('publish', 'publish', self.run, '--wiki', self.wiki)
        self.assertTrue(result['idempotent'])
        entries = StageJournal(self.wiki).read()
        self.assertTrue({'validation', 'decision', 'publish'} <= {e['stage'] for e in entries})
        self.assertTrue(all(e['run_id'] == self.run_id and e['page_id'] == PAGE_ID for e in entries), entries)
        published = [e for e in entries if e['stage'] == 'publish']
        self.assertTrue(published[-1]['details']['idempotent'])

    def test_error_journal_keeps_identity_and_refusal(self):
        self.prepared()
        (self.run / 'source.sql').write_text('-- changed', encoding='utf-8')
        self.cli('publish', 'publish', self.run, '--wiki', self.wiki, code=1)
        entry = StageJournal(self.wiki).read()[-1]
        self.assertEqual(entry['stage'], 'error')
        self.assertEqual(entry['run_id'], self.run_id)
        self.assertEqual(entry['page_id'], PAGE_ID)

    def test_recovery_records_the_recovered_transaction(self):
        self.prepared()
        code = ('import os,sys; from publish import publish; '
                'publish(sys.argv[1],sys.argv[2],fault=lambda s: os._exit(71) if s=="after_page" else None)')
        process = subprocess.run([sys.executable, '-B', '-c', code, str(self.run), str(self.wiki)],
                                 cwd=PACKAGE / 'scripts', capture_output=True, timeout=45)
        self.assertEqual(process.returncode, 71, process.stderr)
        result = self.cli('publish', 'recover', '--wiki', self.wiki)
        self.assertEqual(result[0]['state'], 'rolled_back')
        entry = StageJournal(self.wiki).read()[-1]
        self.assertEqual(entry['run_id'], self.run_id)
        self.assertEqual(entry['outcome'], 'rolled_back')
        self.assertEqual(entry['details']['mode'], 'recover')
        self.assertFalse((self.wiki / PAGE_ID).exists())

    def test_failed_journal_does_not_change_gate_result(self):
        import validation_gate
        import publish as publisher
        self.prepared()
        with patch.object(StageJournal, 'record', side_effect=OSError('journal unavailable')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = validation_gate.main(['--bundle', str(self.run), '--root', 'wiki', str(self.wiki),
                                               '--write-decision', '--json'])
        self.assertEqual(result, 0)
        self.assertTrue(json.loads(output.getvalue())['publication_authorized'])
        with patch.object(StageJournal, 'record', side_effect=OSError('journal unavailable')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = publisher.main(['publish', str(self.run), '--wiki', str(self.wiki)])
        self.assertEqual(result, 0)
        self.assertTrue(json.loads(output.getvalue())['published'])
        self.assertTrue((self.wiki / PAGE_ID).is_file())


class JournalConcurrencyTests(unittest.TestCase):
    def test_concurrent_large_unicode_records_are_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            code = ('import sys; from stage_journal import StageJournal; j=StageJournal(sys.argv[1]); '
                    '[j.record("publish",run_id=sys.argv[2],details={"sequence":n,"text":"\\u044f"*32768}) '
                    'for n in range(12)]')
            processes = [subprocess.Popen([sys.executable, '-B', '-c', code, temp, f'run-{n}'],
                         cwd=PACKAGE / 'scripts', stdout=subprocess.PIPE, stderr=subprocess.PIPE) for n in range(3)]
            try:
                for process in processes:
                    out, err = process.communicate(timeout=60)
                    self.assertEqual(process.returncode, 0, (out, err))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            path = Path(temp) / '.wiki-doc/journal.jsonl'
            entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(entries), 36)
            self.assertEqual({(e['run_id'], e['details']['sequence']) for e in entries},
                             {(f'run-{n}', i) for n in range(3) for i in range(12)})
            self.assertTrue(all(e['details']['text'] == '\u044f' * 32768 for e in entries))

    def test_incomplete_tail_does_not_consume_next_event(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = StageJournal(temp)
            journal.record('plan', run_id='run')
            path = Path(temp) / '.wiki-doc/journal.jsonl'
            with path.open('ab') as stream:
                stream.write(b'{"stage":"unfinished\xd1')
            journal.record('decision', run_id='run')
            self.assertEqual([e['stage'] for e in journal.read()], ['plan', 'decision'])


@unittest.skipUnless(os.name == 'nt', 'Windows path namespace handling')
class WindowsPathRaceTests(unittest.TestCase):
    def test_resolved_dos_and_unc_namespaces_are_equivalent(self):
        for plain, extended in ((Path('C:/wiki'), Path('\\\\?\\C:/wiki')),
                                (Path('//server/share/wiki'), Path('\\\\?\\UNC\\server\\share\\wiki'))):
            for root, resolved in ((plain, extended), (extended, plain)):
                with self.subTest(root=str(root), resolved=str(resolved)):
                    with patch.object(Path, 'resolve', side_effect=[root, resolved / 'index.md']):
                        self.assertEqual(inside(root, 'index.md'), root / 'index.md')

    def test_file_replacement_during_resolve_keeps_wiki_path(self):
        import ntpath
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target = root / 'index.md'
            target.write_text('# Wiki', encoding='utf-8')
            original = ntpath._getfinalpathname
            calls = []
            def racing(path):
                if str(path) == str(target):
                    calls.append(path)
                    if len(calls) == 2:
                        error = OSError('Replacement between Windows path probes')
                        error.winerror = 2
                        raise error
                return original(path)
            with patch.object(ntpath, '_getfinalpathname', side_effect=racing):
                self.assertEqual(inside(root, 'index.md'), target)
            self.assertGreaterEqual(len(calls), 2)

    def test_extended_resolved_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'wiki'
            root.mkdir()
            outside = root.parent / 'outside.md'
            original = Path.resolve
            def redirected(path, *args, **kwargs):
                if path == root / 'index.md':
                    return Path('\\\\?\\' + str(outside))
                return original(path, *args, **kwargs)
            with patch.object(Path, 'resolve', redirected):
                with self.assertRaises(WikiConflict):
                    inside(root, 'index.md')


if __name__ == '__main__':
    unittest.main()
