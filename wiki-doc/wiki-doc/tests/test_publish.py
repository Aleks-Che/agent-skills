"""Real file replacements, process death and interprocess lock acceptance tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bundle_fixture import make_bundle, seal, PAGE_ID, PACKAGE, write_json
from artifact_schema import read_json
from publish import prepare, publish, recover, cleanup_run
from wiki_store import WikiConflict, merge_page, metadata_path


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.run_dir=self.root/'run'; self.run_dir.mkdir()
        self.wiki=self.root/'wiki'
        make_bundle(self.run_dir)
        prepare(self.run_dir,self.wiki)
        self.assertEqual(seal(self.run_dir)['decision'],'ready')

    def test_dry_run_creates_nothing_in_wiki(self):
        result=publish(self.run_dir,self.wiki,dry_run=True)
        self.assertIn(PAGE_ID,result['changes'])
        self.assertIn('.wiki-doc/index.json',result['changes'])
        self.assertIn('.wiki-doc/lineage.json',result['changes'])
        self.assertFalse(self.wiki.exists())

    def test_publish_archive_repeat(self):
        result=publish(self.run_dir,self.wiki)
        self.assertEqual((self.wiki/PAGE_ID).read_bytes(),(self.run_dir/'page.draft.md').read_bytes())
        self.assertTrue((Path(result['archive'])/'publication.json').is_file())
        self.assertTrue(publish(self.run_dir,self.wiki)['idempotent'])
        self.assertEqual((self.wiki/'index.md').read_text().count(']('),1)

    def test_faults_restore_exact_old_state(self):
        for stage in ('prepared','before_page','after_page','before_index','after_index','before_metadata','after_metadata',
                      'before_machine_index','after_machine_index','before_lineage','after_lineage'):
            with self.subTest(stage=stage):
                def fault(label):
                    if label==stage: raise RuntimeError(stage)
                with self.assertRaises(RuntimeError): publish(self.run_dir,self.wiki,fault=fault)
                self.assertFalse((self.wiki/PAGE_ID).exists())
                self.assertFalse((self.wiki/'index.md').exists())
                self.assertFalse(metadata_path(self.wiki,PAGE_ID).exists())
                self.assertFalse((self.wiki/'.wiki-doc/index.json').exists())
                self.assertFalse((self.wiki/'.wiki-doc/lineage.json').exists())

    def test_process_death_and_recovery(self):
        code='from publish import publish; import os,sys; publish(sys.argv[1],sys.argv[2],fault=lambda s: os._exit(71) if s==sys.argv[3] else None)'
        for stage in ('prepared','after_page','after_index','after_metadata','after_machine_index','after_lineage'):
            with self.subTest(stage=stage):
                result=subprocess.run([sys.executable,'-B','-c',code,str(self.run_dir),str(self.wiki),stage],cwd=PACKAGE/'scripts',capture_output=True,timeout=30)
                self.assertEqual(result.returncode,71,result.stderr.decode())
                self.assertEqual(recover(self.wiki)[0]['state'],'rolled_back')
                self.assertFalse((self.wiki/PAGE_ID).exists())
                self.assertFalse((self.wiki/'index.md').exists())
                self.assertFalse((self.wiki/'.wiki-doc/index.json').exists())
                self.assertFalse((self.wiki/'.wiki-doc/lineage.json').exists())

    def test_foreign_edit_survives_recovery(self):
        def fault(label):
            if label=='after_page':
                (self.wiki/PAGE_ID).write_text('Foreign manual edit',encoding='utf-8')
                raise RuntimeError('editor race')
        with self.assertRaises(RuntimeError): publish(self.run_dir,self.wiki,fault=fault)
        self.assertEqual((self.wiki/PAGE_ID).read_text(),'Foreign manual edit')
        self.assertEqual(recover(self.wiki)[0]['state'],'conflict')

    def test_changed_input_page_index_or_plan_rejected(self):
        for target in ('source.sql','page.draft.md','publication.json'):
            path=self.run_dir/target; old=path.read_bytes()
            path.write_bytes(old+b' ')
            with self.assertRaises((WikiConflict,ValueError)): publish(self.run_dir,self.wiki)
            path.write_bytes(old)
        self.wiki.mkdir(exist_ok=True)
        (self.wiki/'index.md').write_text('Manual index change',encoding='utf-8')
        with self.assertRaises(WikiConflict): publish(self.run_dir,self.wiki)

    def test_manual_regions_and_conflicts(self):
        base='Intro\n<!-- wiki-doc:managed begin -->\nSQL old\n<!-- wiki-doc:managed end -->\nNotes\n'
        current=base.replace('Notes','Manual notes')
        incoming=base.replace('SQL old','SQL new')
        self.assertIn('Manual notes',merge_page(base,current,incoming))
        self.assertIn('SQL new',merge_page(base,current,incoming))
        with self.assertRaises(WikiConflict): merge_page(base,current.replace('SQL old','Manual SQL'),incoming)
        with self.assertRaises(WikiConflict): merge_page(None,'unmarked legacy page','incoming')

    def test_recovery_rejects_unowned_backup_path(self):
        code='from publish import publish; import os,sys; publish(sys.argv[1],sys.argv[2],fault=lambda s: os._exit(71) if s=="after_page" else None)'
        result=subprocess.run([sys.executable,'-B','-c',code,str(self.run_dir),str(self.wiki)],cwd=PACKAGE/'scripts',capture_output=True,timeout=30)
        self.assertEqual(result.returncode,71,result.stderr.decode())
        journal=next((self.wiki/'.wiki-doc/journal').glob('*.json'))
        value=read_json(journal); value['files'][0]['backup']='index.md'; journal.write_text(json.dumps(value))
        with self.assertRaises(WikiConflict): recover(self.wiki)
        self.assertTrue((self.wiki/PAGE_ID).exists())

    def test_cleanup_refuses_unowned_run(self):
        publish(self.run_dir,self.wiki)
        with self.assertRaises(WikiConflict): cleanup_run(self.run_dir,self.wiki)
        self.assertTrue(self.run_dir.exists())

    def test_cleanup_committed_own_tmp_preserves_archive_and_journal(self):
        rid=read_json(self.run_dir/'facts.json')['run_id']
        target=self.wiki/'.tmp'/rid
        target.parent.mkdir(parents=True)
        self.run_dir.rename(target)
        prepare(target,self.wiki); seal(target)
        result=publish(target,self.wiki)
        cleanup_run(target,self.wiki)
        self.assertFalse(target.exists())
        self.assertTrue((Path(result['archive'])/'manifest.json').is_file())
        self.assertTrue(Path(result['journal']).is_file())

    def test_bom_index_and_duplicate_managed_markers(self):
        self.wiki.mkdir(); (self.wiki/'index.md').write_bytes(b'\xef\xbb\xbf# Wiki\r\n')
        prepare(self.run_dir,self.wiki); seal(self.run_dir)
        self.assertTrue(publish(self.run_dir,self.wiki)['published'])
        region='<!-- wiki-doc:managed begin -->\na\n<!-- wiki-doc:managed end -->\n'
        with self.assertRaises(WikiConflict): merge_page(None,region+region,region)


if __name__=='__main__': unittest.main()
