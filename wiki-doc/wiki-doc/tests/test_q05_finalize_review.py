"""Real prepared bundles and published pages at Q-05 admission boundaries."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import shutil
import unittest
import uuid
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
import run_prepare
from artifact_schema import read_json
from build_bundle import build, finish
from bundle import create_manifest, compute_tool_versions, write_manifest
from publish import prepare as prepare_publication, publish
from wiki_store import atomic_json, metadata_path


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class FinalizeReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project, self.wiki, self.run = [self.root / n for n in ('project', 'wiki', 'run')]
        self.project.mkdir(); self.wiki.mkdir()
        self.source = self.project / 'source.sql'
        self.source.write_text('CREATE VIEW demo.v AS SELECT id FROM demo.t;', encoding='utf8')
        self.ddl = self.project / 'context.sql'
        self.ddl.write_text('CREATE TABLE demo.t(id bigint);', encoding='utf8')
        args = argparse.Namespace(skill_root=str(PACKAGE), project_root=str(self.project),
            wiki_root=str(self.wiki), subject='demo.v', sql=str(self.source), context=[str(self.ddl)],
            dialect='postgres', version='15', migration_manifest=None, output=str(self.run), profile=None)
        self.args = args
        self.call(run_prepare.cmd_prepare, args, 0)
        self.context = read_json(self.run / 'run_context.json')
        self.run_id = self.context['run_id']
        self.pid = self.context['preparation_manifest']['page_id']

    def call(self, fn, args, code):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            result = fn(args)
        payload = json.loads(stream.getvalue())
        self.assertEqual(result, code, payload)
        return payload

    def authored(self):
        # Explicit deterministic fixture authoring, never implicit in finalize.
        with patch('build_bundle.uuid.uuid4', return_value=uuid.UUID(self.run_id)):
            result = build(self.source, self.run, project_root=self.project, subject='demo.v',
                           context=[self.ddl], wiki_root=self.wiki,
                           profile=self.args.profile, migration_manifest=self.args.migration_manifest)
        self.assertTrue(result['publication_authorized'], result)
        self.call(run_prepare.cmd_verify, argparse.Namespace(run_dir=str(self.run)), 0)

    def finalize(self, code=0):
        return self.call(run_prepare.cmd_finalize, argparse.Namespace(run_dir=str(self.run)), code)

    def published(self):
        self.authored()
        prepare_publication(self.run, self.wiki)
        result = finish(self.run, sql_files=[self.source], context=[self.ddl],
                        project_root=self.project, wiki_root=self.wiki)
        self.assertTrue(result['publication_authorized'], result)
        publish(self.run, self.wiki, project_root=self.project)

    def provenance(self, code=0, pages=None):
        return self.call(run_prepare.cmd_provenance,
            argparse.Namespace(wiki_root=str(self.wiki), page=pages if pages is not None else [self.pid],
                               project_root=str(self.project)), code)

    def test_finalize_publishes_same_prepared_uuid_and_preserves_obligations(self):
        self.authored()
        before = {n:(self.run / n).read_bytes() for n in ('run_context.json', 'inventory.json', 'validation_plan.json', 'facts.json', 'validation.json')}
        result = self.finalize()
        self.assertEqual(result['run_id'], self.run_id)
        self.assertTrue(result['publication_authorized'])
        self.assertTrue(result['generation_completed'])
        meta = read_json(metadata_path(self.wiki, self.pid))
        self.assertEqual(meta['run_id'], self.run_id)
        self.assertEqual(before, {n:(self.run / n).read_bytes() for n in before})
        self.provenance()

    def test_prepared_only_does_not_invent_writer_or_validator(self):
        before = snapshot(self.run)
        self.finalize(1)
        self.assertEqual(before, snapshot(self.run))
        self.assertEqual({}, snapshot(self.wiki))

    def test_changed_sql_rejected_before_overwriting_run(self):
        self.authored()
        self.source.write_text('CREATE VIEW demo.v AS SELECT 2;', encoding='utf8')
        before = snapshot(self.run)
        self.finalize(1)
        self.assertEqual(before, snapshot(self.run))
        self.assertEqual({}, snapshot(self.wiki))

    def test_changed_runtime_rejected_before_overwriting_run(self):
        self.authored()
        value = read_json(self.run / 'run_context.json'); value['runtime_hash'] = '0' * 64
        atomic_json(self.run / 'run_context.json', value)
        before = snapshot(self.run)
        self.finalize(1)
        self.assertEqual(before, snapshot(self.run))

    def test_missing_artifacts_are_not_regenerated(self):
        self.authored()
        for name in ('inventory.json', 'validation_plan.json', 'validation.json', 'facts.json', 'coverage.json'):
            with self.subTest(name=name):
                path = self.run / name; data = path.read_bytes(); path.unlink()
                before = snapshot(self.run)
                self.finalize(1)
                self.assertEqual(before, snapshot(self.run))
                path.write_bytes(data)
        self.assertEqual({}, snapshot(self.wiki))

    def test_foreign_bundle_uuid_rejected(self):
        self.authored()
        value = read_json(self.run / 'manifest.json'); value['run_id'] = str(uuid.uuid4())
        atomic_json(self.run / 'manifest.json', value)
        before = snapshot(self.run)
        self.finalize(1)
        self.assertEqual(before, snapshot(self.run))

    def test_changed_ddl_rejected(self):
        self.authored(); self.ddl.write_text('CREATE TABLE demo.t(id text);', encoding='utf8')
        before = snapshot(self.run); self.finalize(1)
        self.assertEqual(before, snapshot(self.run))

    def test_bad_draft_is_not_replaced_with_reference_page(self):
        self.authored()
        (self.run / 'page.draft.md').write_text('WRITER OUTPUT WITH MISSING CLAIMS', encoding='utf8')
        before = snapshot(self.run); self.finalize(1)
        self.assertEqual(before, snapshot(self.run))
        self.assertEqual({}, snapshot(self.wiki))

    def test_success_with_existing_publication_plan(self):
        self.published()
        self.finalize()
        self.provenance()

    def test_publisher_failure_cannot_complete(self):
        self.authored()
        with patch('publish.publish', side_effect=ValueError('publisher conflict')) as publisher:
            result = self.finalize(1)
        publisher.assert_called_once()
        self.assertFalse(result['generation_completed'])
        self.assertFalse(result['publication_authorized'])
        self.assertEqual({}, snapshot(self.wiki))

    def test_empty_metadata_does_not_verify_selected_missing_page(self):
        (self.wiki / '.wiki-doc/pages').mkdir(parents=True)
        self.provenance(1)

    def test_orphan_metadata_cannot_verify(self):
        value = dict(schema_version=1, page_id=self.pid, canonical_key=self.context['subject'],
                     page_sha256='a'*64, bundle='.wiki-doc/runs/'+self.run_id,
                     run_id=self.run_id, profile=None, source_files=[])
        atomic_json(metadata_path(self.wiki, self.pid), value)
        self.provenance(1)

    def test_page_written_outside_publisher_cannot_verify(self):
        self.published(); metadata_path(self.wiki, self.pid).unlink()
        self.provenance(1)

    def test_real_publication_verifies_read_only(self):
        self.published(); before = snapshot(self.wiki)
        self.provenance()
        self.assertEqual(before, snapshot(self.wiki))

    def test_published_page_change_is_rejected_read_only(self):
        self.published(); (self.wiki / self.pid).write_text('manual replacement', encoding='utf8')
        before = snapshot(self.wiki); self.provenance(1)
        self.assertEqual(before, snapshot(self.wiki))

    def test_metadata_identity_and_types_are_checked(self):
        self.published(); path = metadata_path(self.wiki, self.pid); original = path.read_bytes()
        for field, value in (('run_id',str(uuid.uuid4())), ('schema_version',99), ('canonical_key','view+demo+other'),
                             ('page_sha256',None), ('source_files',[]), ('bundle','../outside'), ('profile',{})):
            with self.subTest(field=field):
                meta = json.loads(original); meta[field] = value; atomic_json(path, meta)
                self.provenance(1)
                path.write_bytes(original)

    def test_missing_or_uncommitted_publication_is_rejected(self):
        self.published(); path = self.wiki / '.wiki-doc/journal' / (self.run_id + '.json')
        original = path.read_bytes(); path.unlink(); self.provenance(1)
        value = json.loads(original); value['state'] = 'metadata_replaced'; atomic_json(path, value)
        self.provenance(1)

    def test_archive_and_current_source_must_verify(self):
        self.published()
        paths = [self.wiki / '.wiki-doc/runs' / self.run_id / 'validation.json', self.ddl]
        for path in paths:
            with self.subTest(path=str(path)):
                data = path.read_bytes(); path.write_bytes(data+b' '); self.provenance(1); path.write_bytes(data)

    def test_selection_ignores_unrelated_legacy_but_rejects_selected_legacy(self):
        self.published(); (self.wiki/'audit.md').write_text('Standalone audit',encoding='utf8')
        self.provenance()
        self.provenance(1, [self.pid, 'audit.md'])

    def test_explicit_nonempty_selection_required(self):
        self.published(); self.provenance(1, [])

    def test_wrong_metadata_filename_rejected(self):
        self.published(); path = metadata_path(self.wiki, self.pid)
        path.rename(path.with_name('wrong.json')); self.provenance(1)

    def test_provenance_failure_after_publish_cannot_complete(self):
        self.authored()
        def corrupt(*args, **kwargs):
            result = publish(*args, **kwargs)
            (self.wiki / self.pid).write_text('unexpected change', encoding='utf8')
            return result
        with patch('publish.publish', side_effect=corrupt) as publisher:
            result = self.finalize(1)
        publisher.assert_called_once()
        self.assertFalse(result['generation_completed'])

    def test_resealed_validator_defect_is_not_overwritten(self):
        self.authored()
        value = read_json(self.run/'validation.json')
        value['checks'][0].update(status='defect', defect_code='unsupported_claim', reason='Writer defect found')
        atomic_json(self.run/'validation.json', value)
        manifest = create_manifest(run_id=self.run_id, page_id=self.pid, sql_files=[self.source],
            context_files=[self.ddl], artifacts_dir=self.run, project_dir=self.project,
            tool_versions=compute_tool_versions(PACKAGE))
        write_manifest(manifest, self.run/'manifest.json')
        before = (self.run/'validation.json').read_bytes()
        with patch('publish.publish') as publisher:
            self.finalize(1)
        publisher.assert_not_called()
        self.assertEqual(before, (self.run/'validation.json').read_bytes())
        self.assertEqual({}, snapshot(self.wiki))

    def test_merged_manual_text_requires_fresh_validation(self):
        self.authored()
        page = self.wiki/self.pid
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(b'Manual introduction\n' + (self.run/'page.draft.md').read_bytes())
        before = snapshot(self.wiki)
        validation = (self.run/'validation.json').read_bytes()
        result = self.finalize(1)
        self.assertTrue(result['validation_required'])
        self.assertEqual(validation, (self.run/'validation.json').read_bytes())
        self.assertEqual(before, snapshot(self.wiki))
        self.assertTrue((self.run/'page.draft.md').read_bytes().startswith(b'Manual introduction'))
        self.finalize(1)  # Stale validation/manifest cannot bless the merged bytes.
        result = finish(self.run, sql_files=[self.source], context=[self.ddl],
                        project_root=self.project, wiki_root=self.wiki)
        self.assertTrue(result['publication_authorized'], result)
        self.finalize()
        self.provenance()

    def test_provenance_checks_malformed_metadata_without_writing(self):
        self.published(); path = metadata_path(self.wiki, self.pid)
        for bad in ('[]', '{', '{"schema_version":1,"schema_version":2}'):
            with self.subTest(bad=bad):
                path.write_text(bad, encoding='utf8'); before = snapshot(self.wiki)
                self.provenance(1)
                self.assertEqual(before, snapshot(self.wiki))

    def test_resealed_page_and_metadata_without_publisher_is_rejected(self):
        self.published()
        from evidence import sha256_file
        page = self.wiki/self.pid
        page.write_bytes(page.read_bytes() + b'\nMANUAL NEW CONTENT\n')
        path = metadata_path(self.wiki, self.pid); meta = read_json(path)
        meta['page_sha256'] = sha256_file(page); atomic_json(path, meta)
        self.provenance(1)

    def test_provenance_rejects_unsafe_selection(self):
        self.published()
        for page in ('../outside.md', 'index.md', '.wiki-doc/runs/'+self.run_id+'/page.draft.md'):
            with self.subTest(page=page):
                self.provenance(1, [page])

    def test_cli_finalize_and_selected_provenance(self):
        self.authored()
        command = [sys.executable, '-X', 'utf8', '-B', str(PACKAGE/'scripts/wiki_doc.py')]
        for args in (['finalize', '--run-dir', str(self.run)],
                     ['provenance', '--wiki-root', str(self.wiki), '--page', self.pid,
                      '--project-root', str(self.project)]):
            result = subprocess.run(command+args, capture_output=True, text=True, encoding='utf8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(payload['status'], ('completed', 'verified'))

    def test_non_ready_archive_decision_cannot_verify(self):
        self.published()
        path = self.wiki/'.wiki-doc/runs'/self.run_id/'decision.json'
        value = read_json(path); value['decision'] = 'revise'; atomic_json(path, value)
        self.provenance(1)

    def reprepare(self):
        self.run = self.root/'selected-run'
        self.args.output = str(self.run)
        self.call(run_prepare.cmd_prepare, self.args, 0)
        self.context = read_json(self.run/'run_context.json')
        self.run_id = self.context['run_id']
        self.pid = self.context['preparation_manifest']['page_id']

    def test_finalize_with_selected_profile_and_reject_changed_profile(self):
        profile = self.project/'profile'
        shutil.copytree(PACKAGE/'profiles/ckr_gp', profile)
        self.args.profile = str(profile/'profile.json')
        self.reprepare(); self.authored(); self.finalize(); self.provenance()
        with (profile/'access.md').open('a', encoding='utf8') as stream:
            stream.write('\nChanged profile instructions.\n')
        before = snapshot(self.run); self.finalize(1)
        self.assertEqual(before, snapshot(self.run))
        self.provenance(1)

    def test_finalize_with_ordered_migrations_and_reject_changed_order(self):
        change = self.project/'change.sql'
        change.write_text('ALTER TABLE demo.t ADD COLUMN amount numeric;', encoding='utf8')
        migration = self.project/'migration.json'
        atomic_json(migration, dict(dialect='postgres', version='15',
            ordered_files=['context.sql', 'change.sql'], target_revision='2'))
        self.args.migration_manifest = str(migration)
        self.reprepare(); self.authored(); self.finalize(); self.provenance()
        value = read_json(migration); value['ordered_files'].reverse(); atomic_json(migration, value)
        before = snapshot(self.run); self.finalize(1)
        self.assertEqual(before, snapshot(self.run))
        self.provenance(1)


if __name__ == '__main__':
    unittest.main()
