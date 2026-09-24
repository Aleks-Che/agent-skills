"""Resume real prepared runs, including interrupted publication and corrupt checkpoints."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

if __package__:
    from . import test_q05_finalize_review as fixture
else:
    import test_q05_finalize_review as fixture
import run_prepare
from artifact_schema import read_json
from bundle import create_manifest, compute_tool_versions
from wiki_store import atomic_json


class ResumeReviewTests(unittest.TestCase):
    # Reuse setup/authoring helpers, not the prior test methods.
    setUp = fixture.FinalizeReviewTests.setUp
    call = fixture.FinalizeReviewTests.call
    authored = fixture.FinalizeReviewTests.authored
    provenance = fixture.FinalizeReviewTests.provenance
    published = fixture.FinalizeReviewTests.published

    def resume(self, code=0):
        return self.call(run_prepare.cmd_resume, argparse.Namespace(run_dir=str(self.run)), code)

    def omit(self, *names):
        for name in names:
            (self.run/name).unlink(missing_ok=True)

    def partial_facts(self):
        self.authored()
        self.omit('page.draft.md', 'coverage.json', 'validation.json', 'decision.json', 'manifest.json')

    def assert_unchanged_rejection(self):
        before, wiki_before = fixture.snapshot(self.run), fixture.snapshot(self.wiki)
        result = self.resume(1)
        self.assertFalse(result['generation_completed'])
        self.assertFalse(result['publication_authorized'])
        self.assertEqual(before, fixture.snapshot(self.run))
        self.assertEqual(wiki_before, fixture.snapshot(self.wiki))
        return result

    def test_prepared_only_requires_facts_and_does_not_claim_execution(self):
        result = self.assert_unchanged_rejection()
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual(result['next_stage'], 'facts')
        self.assertNotIn('draft', result.get('completed_stages', []))

    def test_valid_facts_require_writer_output(self):
        self.partial_facts()
        result = self.assert_unchanged_rejection()
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual(result['next_stage'], 'draft')

    def test_malformed_present_facts_are_not_completed(self):
        self.partial_facts()
        for bad in ('{}', '[]', '{', '{"run_id":"a","run_id":"b"}'):
            with self.subTest(bad=bad):
                (self.run/'facts.json').write_text(bad, encoding='utf8')
                result = self.assert_unchanged_rejection()
                self.assertEqual(result['status'], 'blocked')

    def test_foreign_uuid_in_partial_facts_is_rejected(self):
        self.partial_facts()
        facts = read_json(self.run/'facts.json'); facts['run_id'] = str(uuid.uuid4())
        atomic_json(self.run/'facts.json', facts)
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_changed_partial_fact_evidence_is_rejected(self):
        self.partial_facts()
        facts = read_json(self.run/'facts.json')
        facts['objects'][0]['source_refs'][0]['start_line'] = 999
        atomic_json(self.run/'facts.json', facts)
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_missing_coverage_is_not_a_completed_writer(self):
        self.authored(); self.omit('manifest.json', 'decision.json', 'validation.json', 'coverage.json')
        result = self.assert_unchanged_rejection()
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual(result['next_stage'], 'coverage')

    def test_missing_validator_requires_action_not_ready(self):
        self.authored(); self.omit('manifest.json', 'decision.json', 'validation.json')
        result = self.assert_unchanged_rejection()
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual(result['next_stage'], 'validation')

    def test_missing_decision_and_publication_are_actually_executed(self):
        self.authored(); self.omit('decision.json')
        before = {n:(self.run/n).read_bytes() for n in ('inventory.json','validation_plan.json','facts.json','validation.json')}
        result = self.resume()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['run_id'], self.run_id)
        self.assertTrue(result['generation_completed'])
        self.assertTrue((self.run/'decision.json').is_file())
        self.assertTrue((self.run/'publication.json').is_file())
        self.assertEqual(before, {n:(self.run/n).read_bytes() for n in before})
        self.provenance()

    def test_missing_manifest_is_built_from_pinned_inputs(self):
        self.authored(); self.omit('manifest.json','decision.json')
        result = self.resume()
        self.assertEqual(result['status'], 'completed')
        manifest = read_json(self.run/'manifest.json')
        self.assertEqual(manifest['run_id'], self.run_id)
        for key in ('tool_versions','sql_files','context_files','documented_subjects'):
            self.assertEqual(manifest[key],self.context['preparation_manifest'][key])
        self.provenance()

    def test_existing_manifest_hash_mismatch_is_not_resealed(self):
        self.authored(); self.omit('decision.json')
        (self.run/'page.draft.md').write_bytes((self.run/'page.draft.md').read_bytes()+b'\nChanged\n')
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_bound_missing_coverage_is_not_delegated_as_complete(self):
        self.authored(); self.omit('coverage.json')
        fixture.prepare_publication(self.run, self.wiki)
        with patch('run_prepare.cmd_finalize', return_value=91) as finalizer:
            self.assert_unchanged_rejection()
        finalizer.assert_not_called()

    def test_changed_source_blocks_resume(self):
        self.authored(); self.source.write_text('CREATE VIEW demo.v AS SELECT 42;', encoding='utf8')
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_changed_runtime_blocks_resume(self):
        self.authored()
        context = read_json(self.run/'run_context.json'); context['runtime_hash']='0'*64
        atomic_json(self.run/'run_context.json',context)
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_malformed_publication_is_not_completed(self):
        (self.run/'publication.json').write_text('{}', encoding='utf8')
        self.assertEqual(self.assert_unchanged_rejection()['status'], 'blocked')

    def test_ready_label_does_not_override_validator_defect(self):
        self.authored()
        fixture.prepare_publication(self.run, self.wiki)
        validation = read_json(self.run/'validation.json')
        validation['checks'][0].update(status='defect', defect_code='unsupported_claim', reason='Defect found')
        atomic_json(self.run/'validation.json', validation)
        manifest=create_manifest(run_id=self.run_id,page_id=self.pid,sql_files=[self.source],context_files=[self.ddl],
            artifacts_dir=self.run,project_dir=self.project,tool_versions=compute_tool_versions(fixture.PACKAGE))
        atomic_json(self.run/'manifest.json',manifest)
        before=(self.run/'validation.json').read_bytes()
        result=self.resume(1)
        self.assertFalse(result['generation_completed'])
        self.assertEqual(before,(self.run/'validation.json').read_bytes())
        self.assertEqual({},fixture.snapshot(self.wiki))

    def test_committed_run_repeats_idempotently(self):
        self.published()
        result=self.resume()
        self.assertTrue(result['idempotent'])
        self.assertEqual((self.wiki/'index.md').read_text(encoding='utf8').count(']('),1)
        self.provenance()

    def crash_publisher(self):
        self.authored()
        fixture.prepare_publication(self.run,self.wiki)
        fixture.finish(self.run,sql_files=[self.source],context=[self.ddl],project_root=self.project,wiki_root=self.wiki)
        code='import os,sys; from publish import publish; publish(sys.argv[1],sys.argv[2],project_root=sys.argv[3],fault=lambda s: os._exit(71) if s=="after_metadata" else None)'
        result=subprocess.run([sys.executable,'-X','utf8','-B','-c',code,str(self.run),str(self.wiki),str(self.project)],
            cwd=fixture.PACKAGE/'scripts',capture_output=True,timeout=60)
        self.assertEqual(result.returncode,71,result.stderr.decode('utf8'))
        journal=read_json(self.wiki/'.wiki-doc/journal'/f'{self.run_id}.json')
        self.assertNotEqual(journal['state'],'committed')

    def test_real_process_death_recovers_and_rechecks_gate(self):
        self.crash_publisher()
        result=self.resume()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(read_json(self.wiki/'.wiki-doc/journal'/f'{self.run_id}.json')['state'],'committed')
        self.provenance()

    def test_recovery_conflict_preserves_foreign_page(self):
        self.crash_publisher()
        page=self.wiki/self.pid; page.write_text('External editor after crash',encoding='utf8')
        result=self.resume(1)
        self.assertFalse(result['generation_completed'])
        self.assertEqual(page.read_text(encoding='utf8'),'External editor after crash')

    def test_cli_resume_executes_missing_gate_and_publication(self):
        self.authored(); self.omit('decision.json')
        result=subprocess.run([sys.executable,'-X','utf8','-B',str(fixture.PACKAGE/'scripts/wiki_doc.py'),
            'resume','--run-dir',str(self.run)],capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'completed')
        self.provenance()

    def test_interruption_after_publication_prepare_binds_existing_snapshot(self):
        self.authored()
        fixture.prepare_publication(self.run,self.wiki)
        snapshot=(self.run/'publication.json').read_bytes()
        with patch('publish.prepare', side_effect=AssertionError('Must not replace saved publication snapshot')):
            result=self.resume()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(snapshot,(self.run/'publication.json').read_bytes())
        self.provenance()

    def test_existing_unbound_publication_preserves_editor_conflict(self):
        self.authored(); fixture.prepare_publication(self.run,self.wiki)
        page=self.wiki/self.pid; page.write_text('External page after snapshot',encoding='utf8')
        saved=(self.run/'publication.json').read_bytes()
        result=self.resume(1)
        self.assertFalse(result['generation_completed'])
        self.assertEqual(saved,(self.run/'publication.json').read_bytes())
        self.assertEqual(page.read_text(encoding='utf8'),'External page after snapshot')

    def test_malformed_coverage_is_not_available_writer_output(self):
        self.authored(); self.omit('manifest.json','decision.json','validation.json')
        (self.run/'coverage.json').write_text('{}',encoding='utf8')
        self.assertEqual(self.assert_unchanged_rejection()['status'],'blocked')

    def test_foreign_publication_uuid_without_manifest_is_rejected(self):
        self.authored(); fixture.prepare_publication(self.run,self.wiki)
        self.omit('manifest.json','decision.json')
        value=read_json(self.run/'publication.json'); value['run_id']=str(uuid.uuid4())
        atomic_json(self.run/'publication.json',value)
        self.assertEqual(self.assert_unchanged_rejection()['status'],'blocked')

    def test_changed_artifact_during_inspection_blocks_manifest_creation(self):
        self.authored(); self.omit('manifest.json','decision.json')
        import artifact_schema
        original=artifact_schema.validate_artifacts
        def changing(*args, **kwargs):
            errors=original(*args,**kwargs)
            path=self.run/'facts.json'; path.write_bytes(path.read_bytes()+b' ')
            return errors
        with patch('artifact_schema.validate_artifacts',side_effect=changing):
            result=self.resume(1)
        self.assertEqual(result['status'],'blocked')
        self.assertFalse((self.run/'manifest.json').exists())
        self.assertEqual({},fixture.snapshot(self.wiki))


if __name__ == '__main__':
    unittest.main()
