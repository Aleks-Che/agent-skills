"""Cross-component P1 acceptance on real bundles and local wiki files."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bundle_fixture import PACKAGE,make_bundle,seal,PAGE_ID
from artifact_schema import read_json
from build_bundle import build,finish,render
from lint import lint
from page_claims import check_page_claims
from profiles import detect_profile,load_profile,classify_access,CKR
from publish import publish,prepare
from run_regression import check_expected,isolate
from wiki_store import atomic_json,atomic_bytes,metadata_path

EXAMPLES=PACKAGE/'examples'


class ProfilesTests(unittest.TestCase):
    def test_presence_does_not_activate(self):
        self.assertIsNone(detect_profile(dict(items=[dict(kind='DECLARATION',details=dict(schema='demo'))])))
    def test_confirmed_identifier_activates(self):
        self.assertEqual(detect_profile(dict(items=[dict(details=dict(schema='s_gp_p1024_dmr_svd_kb_ckr_demo_gp_core'))]))['id'],'CKR_GP')
    def test_audit_call_does_not_authorize_read(self):
        profile=load_profile(); source='s_gp_p1024_dmr_svd_kb_ckr_demo_gp_core'
        target='s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.logs'
        self.assertEqual(classify_access(profile,source,target,'calls')['status'],'allowed')
        self.assertEqual(classify_access(profile,source,target,'reads')['status'],'unconfirmed')
    def test_bvd_cannot_read_svd(self):
        self.assertEqual(classify_access(load_profile(),'s_gp_p1024_src_bvd_foo_gp_core','s_gp_p1024_dmr_svd_bar_gp_apt.t','reads')['status'],'forbidden')


class LintTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.run_dir=self.root/'run'; self.run_dir.mkdir(); self.wiki=self.root/'wiki'
        make_bundle(self.run_dir); prepare(self.run_dir,self.wiki); seal(self.run_dir); publish(self.run_dir,self.wiki)
    def codes(self): return {i['code'] for i in lint(self.wiki)['issues']}
    def test_valid_is_read_only(self):
        before={p:p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()}
        self.assertTrue(lint(self.wiki)['valid'])
        self.assertEqual(before,{p:p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()})
    def test_broken_link_anchor_index_source_and_coverage(self):
        page=self.wiki/PAGE_ID
        page.write_text('## Calculation {#header_purpose}\n[bad](missing.md#x)\n',encoding='utf-8')
        (self.wiki/'index.md').write_text('# Wiki\n',encoding='utf-8')
        (self.run_dir/'source.sql').write_text('-- changed\n',encoding='utf-8')
        self.assertTrue({'link_invalid','index_missing','source_stale','coverage_invalid','page_changed'}<=self.codes())
    def test_legacy_is_warning_not_automatic_semantic_defect(self):
        (self.wiki/'standalone.md').write_text('# A useful standalone legacy note\nText\n',encoding='utf-8')
        result=lint(self.wiki)
        self.assertTrue(result['valid'],result)
        self.assertIn('legacy_metadata_missing',self.codes())
    def test_duplicate_key(self):
        original=read_json(metadata_path(self.wiki,PAGE_ID)); original['page_id']='duplicate.md'
        (self.wiki/'duplicate.md').write_bytes((self.wiki/PAGE_ID).read_bytes())
        atomic_json(metadata_path(self.wiki,'duplicate.md'),original)
        self.assertIn('identity_duplicate',self.codes())


class RegressionAcceptanceTests(unittest.TestCase):
    def test_package_markdown_links_survive_resource_moves(self):
        from coverage_gate import MarkdownDocument
        from urllib.parse import urlsplit,unquote
        # Runtime Markdown is independent of optional search tools and ignored caches.
        paths=list(PACKAGE.glob('*.md'))
        for folder in ('docs','examples','history','profiles','references','template','tests','scripts'):
            paths.extend((PACKAGE/folder).rglob('*.md'))
        for path in paths:
            for link in MarkdownDocument(path.read_text(encoding='utf-8-sig')).links:
                url=urlsplit(link)
                if url.scheme or url.netloc: continue
                target=(path.parent/unquote(url.path)).resolve() if url.path else path
                self.assertTrue(target.exists(),f'{path}: {link}')
                if url.fragment and target.suffix=='.md':
                    self.assertTrue(MarkdownDocument(target.read_text(encoding='utf-8-sig')).target(unquote(url.fragment)),f'{path}: {link}')

    def test_isolation_excludes_oracles_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            case=read_json(EXAMPLES/'cases.json')['cases'][0]
            package,project,out=isolate(case,Path(temp)/'work',EXAMPLES)
            self.assertFalse(list(Path(temp).rglob('*.expected.md')))
            self.assertFalse(list(Path(temp).rglob('expected')))
            self.assertFalse((package/'history').exists())
            self.assertFalse((package/'tests').exists())
            self.assertFalse((package/'examples').exists())
            docs = {'dialect-support.md', 'agent-response-templates.md', 'operations.md'}
            self.assertEqual({p.name for p in (package/'docs').iterdir()}, docs)
            for name in ['VERSION', 'CHANGELOG.md', *('docs/' + name for name in docs)]:
                self.assertEqual((package/name).read_bytes(), (PACKAGE/name).read_bytes())
            self.assertEqual({p.name for p in project.iterdir()},{'01_no_target_ddl.sql','context.sql'})

    def test_two_processes_keep_both_index_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); wiki=root/'wiki'; source=EXAMPLES/'06_same_name_diff_schema.sql'
            runs=[]
            for n,subject in enumerate(('function+core+orders_summary+()','function+archive+orders_summary+()')):
                run=root/str(n)
                self.assertTrue(build(source,run,project_root=EXAMPLES,subject=subject,context=[EXAMPLES/'context.sql'])['publication_authorized'])
                prepare(run,wiki)
                self.assertTrue(finish(run,sql_files=[source],context=[EXAMPLES/'context.sql'],project_root=EXAMPLES,wiki_root=wiki)['publication_authorized'])
                runs.append(run)
            command=[sys.executable,'-B',str(PACKAGE/'scripts/publish.py'),'publish']
            processes=[subprocess.Popen(command+[str(run),'--wiki',str(wiki),'--project-root',str(EXAMPLES)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for run in runs]
            try:
                results=[process.communicate(timeout=45) for process in processes]
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            for process,(out,err) in zip(processes,results):
                self.assertEqual(process.returncode,0,(out,err))
            self.assertEqual((wiki/'index.md').read_text().count(']('),2)
            catalogue=read_json(wiki/'.wiki-doc/index.json')
            graph=read_json(wiki/'.wiki-doc/lineage.json')
            self.assertEqual(len(catalogue['pages']),2)
            self.assertEqual({p['canonical_key'] for p in catalogue['pages']},set(graph['nodes']))
            self.assertEqual(catalogue['generated_at'],graph['generated_at'])
            self.assertTrue(lint(wiki)['valid'],lint(wiki))

    def test_text_and_coherent_formula_mutations_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp); source=EXAMPLES/'09_view_and_readonly.sql'; subject='view+demo+order_totals'
            self.assertTrue(build(source,run,project_root=EXAMPLES,subject=subject,context=[EXAMPLES/'context.sql'])['publication_authorized'])
            page=(run/'page.draft.md').read_text(); facts=read_json(run/'facts.json')
            changed=page.replace('price * quantity','price + quantity')
            self.assertTrue(check_page_claims(facts,changed))
            atomic_bytes(run/'page.draft.md',changed.encode())
            result=finish(run,sql_files=[source],context=[EXAMPLES/'context.sql'],project_root=EXAMPLES)
            self.assertFalse(result['publication_authorized'])
            for f in facts['formulas']: f['expression']=f['expression'].replace('*','+')
            for c in facts['columns']:
                if c.get('expression'): c['expression']=c['expression'].replace('*','+')
            atomic_json(run/'facts.json',facts)
            page,coverage=render(facts,read_json(run/'validation_plan.json'))
            atomic_bytes(run/'page.draft.md',page.encode()); atomic_json(run/'coverage.json',coverage)
            result=finish(run,sql_files=[source],context=[EXAMPLES/'context.sql'],project_root=EXAMPLES)
            self.assertFalse(result['publication_authorized'])
            self.assertTrue(any('expression' in e for e in result['errors']),result)


if __name__=='__main__': unittest.main()
