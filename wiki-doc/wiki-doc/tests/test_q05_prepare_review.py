"""Preparation admission and integrity at API and real CLI boundaries."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
import run_prepare
from artifact_schema import read_json
from evidence import sha256_file


def copy_runtime(destination):
    for directory in ('scripts', 'schemas', 'references', 'template', 'profiles', 'docs'):
        shutil.copytree(PACKAGE / directory, destination / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in PACKAGE.iterdir():
        if path.is_file() and path.suffix in ('.md', '.txt'):
            shutil.copy2(path, destination / path.name)


class PrepareReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.wiki = self.root / 'wiki'
        self.wiki.mkdir()
        self.source = self.project / 'source.sql'
        self.source.write_text('CREATE VIEW demo.v AS SELECT id FROM demo.t;', encoding='utf8')
        self.ddl = self.project / 'context.sql'
        self.ddl.write_text('CREATE TABLE demo.t(id bigint);', encoding='utf8')
        self.run = self.root / 'run'
        self.args = argparse.Namespace(skill_root=str(PACKAGE), project_root=str(self.project),
            wiki_root=str(self.wiki), subject='demo.v', sql=str(self.source), context=[str(self.ddl)],
            dialect='postgres', version='15', migration_manifest=None, output=str(self.run), profile=None)

    def prepare(self, expected=0):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = run_prepare.cmd_prepare(self.args)
        payload = json.loads(output.getvalue())
        self.assertEqual(code, expected, payload)
        return payload

    def verify(self, expected=1):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = run_prepare.cmd_verify(argparse.Namespace(run_dir=str(self.run)))
        payload = json.loads(output.getvalue())
        self.assertEqual(code, expected, payload)
        return payload

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding='utf8')

    def test_prepared_and_verified_do_not_authorize_generation(self):
        self.assertIs(self.prepare().get('publication_authorized'), False)
        result = self.verify(0)
        self.assertIs(result.get('publication_authorized'), False)
        self.assertIs(result.get('generation_completed'), False)

    def test_changed_inventory_is_rejected(self):
        self.prepare()
        self.write('inventory.json', {})
        self.verify()

    def test_changed_plan_is_rejected(self):
        self.prepare()
        value = read_json(self.run / 'validation_plan.json')
        value['required_checks'] = []
        self.write('validation_plan.json', value)
        self.verify()

    def test_foreign_run_id_is_rejected(self):
        self.prepare()
        value = read_json(self.run / 'inventory.json')
        value['run_id'] = '00000000-0000-0000-0000-000000000001'
        self.write('inventory.json', value)
        self.verify()

    def test_context_change_is_rejected(self):
        self.prepare()
        self.ddl.write_text('CREATE TABLE demo.t(id text);', encoding='utf8')
        self.verify()

    def test_invalid_json_and_contract_return_diagnostics(self):
        self.prepare()
        for data in ('{', '[]', '{}', '{"status":"prepared","status":"ready"}'):
            with self.subTest(data=data):
                (self.run / 'run_context.json').write_text(data, encoding='utf8')
                self.verify()

    def test_empty_unrelated_skill_root_is_rejected(self):
        other = self.root / 'other-skill'
        other.mkdir()
        self.args.skill_root = str(other)
        self.prepare(1)

    def test_repeat_prepare_does_not_overwrite_completed_or_failed_run(self):
        self.prepare()
        before = {p.name:p.read_bytes() for p in self.run.iterdir() if p.is_file()}
        self.prepare(1)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.run.iterdir() if p.is_file()})
        self.verify(0)

    def test_prepare_does_not_write_published_wiki_or_journal(self):
        (self.wiki / 'manual.md').write_text('manual page', encoding='utf8')
        before = {p.relative_to(self.wiki):p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()}
        self.prepare()
        self.assertEqual(before, {p.relative_to(self.wiki):p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()})

    def test_missing_wiki_is_not_created_by_failed_default_prepare(self):
        self.args.wiki_root = str(self.root / 'missing-wiki')
        self.args.output = None
        with contextlib.chdir(self.root):
            self.prepare(1)
        self.assertFalse(Path(self.args.wiki_root).exists())

    def test_analysis_gap_returns_limitation_with_saved_inventory_and_plan(self):
        self.source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ '
                               'BEGIN FOR i IN 1..3 LOOP PERFORM i; END LOOP; END; $$;', encoding='utf8')
        self.args.subject = 'demo.f'
        self.prepare(1)
        self.assertTrue(read_json(self.run / 'inventory.json')['coverage_notes'])
        self.assertTrue((self.run / 'validation_plan.json').is_file())
        self.verify()

    def test_q11_prepare_includes_independent_type_unknown_checks(self):
        fixtures = PACKAGE / 'examples/fixtures'
        shutil.copy2(fixtures / 'q11_rr_sl.sql', self.source)
        shutil.copy2(fixtures / 'q_context.sql', self.ddl)
        self.args.subject = 'q_out.load_rr_sl'
        self.prepare()
        plan = read_json(self.run / 'validation_plan.json')
        self.assertEqual(len([c for c in plan['required_checks'] if c['id'].startswith('unknown:type:')]), 2)

    def test_changed_sql_during_analysis_is_not_prepared(self):
        import validation_plan
        original = validation_plan.generate_plan
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.source.write_text('CREATE VIEW demo.v AS SELECT 2 AS id;', encoding='utf8')
            return result
        with patch('validation_plan.generate_plan', side_effect=changed):
            self.prepare(1)
        self.verify()

    def migration(self):
        self.source.write_text('CREATE VIEW demo.v AS SELECT id FROM demo.t;', encoding='utf8')
        self.args.context = []
        self.migration_file = self.project / 'change.sql'
        self.migration_file.write_text('ALTER TABLE demo.t ADD COLUMN amount numeric;', encoding='utf8')
        self.migration_manifest = self.project / 'migration.json'
        self.migration_manifest.write_text(json.dumps(dict(dialect='postgres', version='15',
            ordered_files=['context.sql', 'change.sql'], target_revision='2')), encoding='utf8')
        self.args.migration_manifest = str(self.migration_manifest)

    def test_changed_migration_file_is_rejected(self):
        self.migration()
        self.prepare()
        self.migration_file.write_text('ALTER TABLE demo.t ADD COLUMN other text;', encoding='utf8')
        self.verify()

    def test_changed_migration_manifest_is_rejected(self):
        self.migration()
        self.prepare()
        value = read_json(self.migration_manifest)
        value['target_revision'] = '3'
        self.migration_manifest.write_text(json.dumps(value), encoding='utf8')
        self.verify()

    def test_resealed_plan_omission_is_rebuilt_and_rejected(self):
        self.prepare()
        value = read_json(self.run / 'validation_plan.json')
        value['required_checks'] = [c for c in value['required_checks'] if c['rule_id'] != 'reads']
        self.write('validation_plan.json', value)
        context = read_json(self.run / 'run_context.json')
        if 'preparation_manifest' in context:
            context['preparation_manifest']['artifacts']['validation_plan']['sha256'] = sha256_file(self.run / 'validation_plan.json')
            self.write('run_context.json', context)
        self.verify()

    def test_prepare_from_selected_copy_and_instruction_change(self):
        selected = self.root / 'selected'
        copy_runtime(selected)
        def cli(command, *args):
            return subprocess.run([sys.executable, '-X', 'utf8', '-B', str(selected / 'scripts/wiki_doc.py'),
                                   command, *map(str, args)], cwd=self.root, capture_output=True, text=True,
                                  encoding='utf8', timeout=60)
        prepared = cli('prepare', '--skill-root', selected, '--project-root', self.project,
                       '--wiki-root', self.wiki, '--subject', 'demo.v', '--sql', self.source,
                       '--context', self.ddl, '--output', self.run)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        verified = cli('verify-run', '--run-dir', self.run)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        with (selected / 'docs/answer-templates.md').open('a', encoding='utf8') as stream:
            stream.write('\nChanged runtime instructions.\n')
        rejected = cli('verify-run', '--run-dir', self.run)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)

    def test_profile_obligations_and_profile_hash_are_pinned(self):
        profile = self.project / 'profile'
        shutil.copytree(PACKAGE / 'profiles/ckr_gp', profile)
        self.args.profile = str(profile / 'profile.json')
        self.prepare()
        plan = read_json(self.run / 'validation_plan.json')
        self.assertIn('profile_check', {c['rule_id'] for c in plan['required_checks']})
        context = read_json(self.run / 'run_context.json')
        self.assertIn('profile_sha256', context['preparation_manifest']['tool_versions'])
        self.verify(0)
        with (profile / 'access.md').open('a', encoding='utf8') as stream:
            stream.write('\nChanged profile instructions.\n')
        self.verify()

    def test_verify_is_read_only_even_when_it_refuses_changed_input(self):
        self.prepare()
        before = {p.relative_to(self.run):p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
        self.verify(0)
        self.ddl.write_text('CREATE TABLE demo.t(id text);', encoding='utf8')
        self.verify()
        self.assertEqual(before, {p.relative_to(self.run):p.read_bytes() for p in self.run.rglob('*') if p.is_file()})

    def test_interrupted_plan_write_has_no_prepared_commit_marker(self):
        original = run_prepare.atomic_json
        def fail(path, value):
            if path.name == 'validation_plan.json':
                raise OSError('simulated interrupted write')
            original(path, value)
        with patch('run_prepare.atomic_json', side_effect=fail):
            self.prepare(1)
        self.assertFalse((self.run / 'run_context.json').exists())
        self.verify()

    def test_resealed_foreign_run_id_is_rejected(self):
        self.prepare()
        inventory = read_json(self.run / 'inventory.json')
        inventory['run_id'] = '00000000-0000-0000-0000-000000000001'
        self.write('inventory.json', inventory)
        context = read_json(self.run / 'run_context.json')
        context['preparation_manifest']['artifacts']['inventory']['sha256'] = sha256_file(self.run / 'inventory.json')
        self.write('run_context.json', context)
        self.verify()

    def test_inputs_outside_project_are_rejected(self):
        outside = self.root / 'other.sql'
        shutil.copy2(self.source, outside)
        self.args.sql = str(outside)
        self.prepare(1)
        self.assertFalse((self.run / 'run_context.json').exists())

    def test_preparation_matches_reference_adapter_inventory_and_plan(self):
        from build_bundle import build
        fixtures = PACKAGE / 'examples/fixtures'
        shutil.copy2(fixtures / 'q11_rr_sl.sql', self.source)
        shutil.copy2(fixtures / 'q_context.sql', self.ddl)
        self.args.subject = 'q_out.load_rr_sl'
        self.prepare()
        reference = self.root / 'reference'
        result = build(self.source, reference, project_root=self.project,
                       subject=self.args.subject, context=[self.ddl], version='15')
        self.assertTrue(result['publication_authorized'], result)
        for name in ('inventory.json', 'validation_plan.json'):
            expected, actual = read_json(reference / name), read_json(self.run / name)
            expected['run_id'] = actual['run_id']
            self.assertEqual(actual, expected, name)


if __name__ == '__main__':
    unittest.main()
