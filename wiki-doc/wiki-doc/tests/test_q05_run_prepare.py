"""Original Q-05 controls, using complete runnable skill copies and real CLI calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_q05_prepare_review import copy_runtime
from run_prepare import _hash_tree, RUN_CONTEXT_FILE, LIMITATION_FILE


class RuntimeFixture(unittest.TestCase):
    def inputs(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        skill, project, wiki = root / 'skill', root / 'project', root / 'wiki'
        copy_runtime(skill)
        project.mkdir()
        wiki.mkdir()
        (skill / 'scripts/a.py').write_text('x = 1', encoding='utf8')
        sql = project / 'test.sql'
        sql.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE sql AS $$SELECT 1;$$;', encoding='utf8')
        return root, skill, project, wiki, sql

    def cli(self, skill, *arguments):
        completed = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(skill / 'scripts/wiki_doc.py'),
                                    *map(str, arguments)], capture_output=True, encoding='utf8', timeout=60)
        self.assertFalse(completed.stderr, completed.stderr)
        return completed.returncode, json.loads(completed.stdout)

    def prepare(self, skill, project, wiki, sql, run, *, selected=None):
        return self.cli(skill, 'prepare', '--skill-root', selected or skill, '--project-root', project,
                        '--wiki-root', wiki, '--subject', 'function+demo+f+()', '--sql', sql, '--output', run)


class HashTreeTests(RuntimeFixture):
    def test_hash_is_deterministic(self):
        _, skill, _, _, _ = self.inputs()
        self.assertEqual(_hash_tree(skill), _hash_tree(skill))

    def test_hash_changes_on_file_change(self):
        _, skill, _, _, _ = self.inputs()
        before = _hash_tree(skill)
        (skill / 'scripts/a.py').write_text('x = 2', encoding='utf8')
        self.assertNotEqual(before, _hash_tree(skill))

    def test_hash_ignores_pycache(self):
        _, skill, _, _, _ = self.inputs()
        before = _hash_tree(skill)
        cache = skill / 'scripts/__pycache__'
        cache.mkdir()
        (cache / 'a.pyc').write_bytes(b'\x00')
        self.assertEqual(before, _hash_tree(skill))


class PrepareTests(RuntimeFixture):
    def test_prepare_success(self):
        root, skill, project, wiki, sql = self.inputs()
        run = root / 'run'
        code, payload = self.prepare(skill, project, wiki, sql, run)
        self.assertEqual(code, 0, payload)
        context = json.loads((run / RUN_CONTEXT_FILE).read_text())
        self.assertEqual(context['status'], 'prepared')
        self.assertEqual(context['run_id'], payload['run_id'])
        self.assertEqual(context['runtime_hash'], payload['runtime_hash'])
        self.assertEqual(context['preparation_manifest']['sql_files'][0]['sha256'], payload['sql_sha256'])
        self.assertTrue((run / 'inventory.json').is_file())
        self.assertTrue((run / 'validation_plan.json').is_file())

    def test_prepare_missing_sql_returns_limitation(self):
        root, skill, project, wiki, _ = self.inputs()
        run = root / 'run'
        code, payload = self.prepare(skill, project, wiki, project / 'missing.sql', run)
        self.assertEqual(code, 1, payload)
        self.assertTrue((run / LIMITATION_FILE).is_file())
        self.assertIn('does not exist', payload['reason'])

    def test_prepare_missing_skill_root_returns_limitation(self):
        root, skill, project, wiki, sql = self.inputs()
        run = root / 'run'
        code, payload = self.prepare(skill, project, wiki, sql, run, selected=root / 'no-skill')
        self.assertEqual(code, 1, payload)
        self.assertTrue((run / LIMITATION_FILE).is_file())


class VerifyTests(RuntimeFixture):
    def prepared(self):
        root, skill, project, wiki, sql = self.inputs()
        run = root / 'run'
        code, payload = self.prepare(skill, project, wiki, sql, run)
        self.assertEqual(code, 0, payload)
        return skill, sql, run

    def test_verify_success(self):
        skill, _, run = self.prepared()
        code, payload = self.cli(skill, 'verify-run', '--run-dir', run)
        self.assertEqual(code, 0, payload)

    def test_verify_runtime_change_detected(self):
        skill, _, run = self.prepared()
        (skill / 'scripts/a.py').write_text('x = 2', encoding='utf8')
        code, payload = self.cli(skill, 'verify-run', '--run-dir', run)
        self.assertEqual(code, 1, payload)

    def test_verify_sql_change_detected(self):
        skill, sql, run = self.prepared()
        sql.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE sql AS $$SELECT 2;$$;', encoding='utf8')
        code, payload = self.cli(skill, 'verify-run', '--run-dir', run)
        self.assertEqual(code, 1, payload)

    def test_verify_missing_run_context(self):
        root, skill, _, _, _ = self.inputs()
        code, payload = self.cli(skill, 'verify-run', '--run-dir', root / 'missing')
        self.assertEqual(code, 1, payload)


if __name__ == '__main__':
    unittest.main()
