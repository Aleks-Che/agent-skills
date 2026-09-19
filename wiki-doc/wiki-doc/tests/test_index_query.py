"""P2-01: real publication, graph traversal, archive integrity and read-only CLI."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from bundle_fixture import make_bundle, seal, PAGE_ID, PACKAGE
from artifact_schema import read_json
from build_bundle import build, finish
from index import build_catalogue, build_index, build_lineage, rebuild_after_publish, _graph, IndexError
from publish import prepare, publish, recover
from query import (query_dependencies, query_consumers, query_affected, query_page,
                   query_outdated, main, QueryError)
from wiki_store import hash_file, load_metadata, metadata_path, WikiConflict


SUBPROCESS_ENV = {**os.environ,
                  'PYTHONPATH': os.pathsep.join(
                      [str(PACKAGE / 'scripts')] + ([os.environ['PYTHONPATH']] if os.environ.get('PYTHONPATH') else []))}


class PublishedQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'; self.run.mkdir()
        self.wiki = self.root / 'wiki'
        make_bundle(self.run)
        prepare(self.run, self.wiki)
        self.assertEqual(seal(self.run)['decision'], 'ready')
        publish(self.run, self.wiki)

    def snapshot(self):
        return {p.relative_to(self.wiki).as_posix(): p.read_bytes() for p in self.wiki.rglob('*') if p.is_file()}

    def test_catalogue_and_evidence_are_bound_to_publication(self):
        index, graph = build_catalogue(self.wiki)
        self.assertEqual(index['generated_at'], graph['generated_at'])
        page = index['pages'][0]
        self.assertEqual(page['page_sha256'], hash_file(self.wiki / PAGE_ID))
        self.assertEqual(page['page_state'], 'current')
        self.assertEqual(page['canonical_key'], 'function+core+calc+()')
        edge = graph['edges'][0]
        self.assertEqual(edge['to'], 'table+demo+orders')
        self.assertEqual(edge['page_id'], PAGE_ID)
        for ref in edge['evidence']:
            self.assertEqual(ref['root'], 'wiki')
            self.assertEqual(hash_file(self.wiki / ref['path']), ref['sha256'])
            self.assertLessEqual(ref['end_line'], len((self.wiki / ref['path']).read_text().splitlines()))

    def test_consumers_dependencies_and_affected_keep_evidence(self):
        graph = build_lineage(self.wiki)
        dependencies = query_dependencies(graph, 'function+core+calc+()')
        self.assertEqual(dependencies['reads'], ['table+demo+orders'])
        consumers = query_consumers(graph, 'table+demo+orders')
        self.assertEqual(consumers['read_by'], ['function+core+calc+()'])
        self.assertTrue(consumers['edges'][0]['evidence'])
        affected = query_affected(graph, 'table+demo+orders')
        self.assertEqual(affected[0]['via'], 'table+demo+orders')
        self.assertEqual(affected[0]['page_id'], PAGE_ID)
        self.assertTrue(affected[0]['edge']['evidence'])

    def test_read_only_cli_all_modes_with_stale_and_missing_caches(self):
        (self.wiki / '.wiki-doc/index.json').write_text('{bad json')
        (self.wiki / '.wiki-doc/lineage.json').unlink()
        before = self.snapshot()
        for args in (['--page', PAGE_ID], ['--dependencies', 'function+core+calc+()'],
                     ['--consumers', 'table+demo+orders'], ['--affected', 'table+demo+orders'], ['--outdated']):
            for json_flag in ([], ['--json']):
                with self.subTest(args=args, json=json_flag), contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main([str(self.wiki), *args, *json_flag]), 0)
                    json.loads(out.getvalue())
        self.assertEqual(before, self.snapshot())

    def test_empty_legacy_page_is_queryable_and_tmp_drafts_are_excluded(self):
        (self.wiki / 'legacy.md').write_text('# old')
        temp = self.wiki / '.tmp' / 'unpublished'; temp.mkdir(parents=True)
        (temp / 'page.draft.md').write_text('# draft')
        result = query_page(self.wiki, 'legacy.md')
        self.assertEqual(result['status'], 'legacy')
        self.assertFalse(result['lineage_verified'])
        self.assertEqual(result['dependencies'], {})
        self.assertEqual(len(build_index(self.wiki)['pages']), 2)
        self.assertEqual(len(build_lineage(self.wiki)['nodes']), 2)

    def test_bold_legacy_identity_is_preserved(self):
        (self.wiki / 'legacy.md').write_text('**Канонический ключ:** `table+x+y`', encoding='utf-8')
        entry = next(p for p in build_index(self.wiki)['pages'] if p['status'] == 'legacy')
        self.assertEqual(entry['canonical_key'], 'table+x+y')

    def test_changed_and_missing_live_pages_do_not_claim_current(self):
        (self.wiki / PAGE_ID).write_text('manual edit')
        self.assertEqual(query_page(self.wiki, PAGE_ID)['page']['page_state'], 'changed')
        (self.wiki / PAGE_ID).unlink()
        self.assertEqual(query_page(self.wiki, PAGE_ID)['page']['page_state'], 'missing')

    def test_changed_and_missing_sources_are_outdated(self):
        self.assertEqual(query_outdated(self.wiki), [])
        (self.run / 'source.sql').write_text('-- changed')
        self.assertEqual(query_outdated(self.wiki)[0]['reason'], 'source_changed')
        (self.run / 'source.sql').unlink()
        self.assertTrue(query_outdated(self.wiki)[0]['source_missing'])

    def test_missing_project_is_reported_instead_of_silent_success(self):
        self.run.rename(self.root / 'moved')
        self.assertEqual(query_outdated(self.wiki)[0]['reason'], 'project_root_unavailable')

    def test_missing_project_metadata_is_unknown(self):
        path = metadata_path(self.wiki, PAGE_ID)
        data = read_json(path); data.pop('project_root'); path.write_text(json.dumps(data))
        self.assertEqual(query_outdated(self.wiki)[0]['reason'], 'project_root_unavailable')

    def test_tampered_archive_and_metadata_refused_with_existing_cache(self):
        record = load_metadata(self.wiki)[0]
        for path in (self.wiki / record['bundle'] / 'facts.json', metadata_path(self.wiki, PAGE_ID)):
            old = path.read_bytes()
            data = read_json(path)
            if 'objects' in data: data['operations'][0]['reads'] = []
            else: data['canonical_key'] = 'function+other+fake+()'
            path.write_text(json.dumps(data))
            with self.assertRaises(IndexError): query_page(self.wiki, PAGE_ID)
            path.write_bytes(old)

    def test_invalid_archive_cli_has_structured_error(self):
        record = load_metadata(self.wiki)[0]
        (self.wiki / record['bundle'] / 'facts.json').write_text('[]')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(self.wiki), '--outdated']), 1)
            self.assertIn('error', json.loads(err.getvalue()))

    def test_pending_transaction_refuses_read_without_recovery_side_effect(self):
        journal = next((self.wiki / '.wiki-doc/journal').glob('*.json'))
        data = read_json(journal); data['state'] = 'metadata_replaced'; journal.write_text(json.dumps(data))
        before = self.snapshot()
        with self.assertRaises(IndexError): build_catalogue(self.wiki)
        self.assertEqual(before, self.snapshot())

    def test_optimistic_snapshot_rejects_changed_metadata(self):
        import index as module
        original = module._state
        calls = []
        def changed(root):
            value = original(root)
            calls.append(True)
            if len(calls) == 2: value['concurrent'] = 'change'
            return value
        with patch.object(module, '_state', side_effect=changed):
            with self.assertRaisesRegex(IndexError, 'changed during Query'): build_catalogue(self.wiki)

    def test_idempotent_publish_repairs_missing_catalogues(self):
        (self.wiki / '.wiki-doc/index.json').unlink()
        (self.wiki / '.wiki-doc/lineage.json').write_text('{}')
        self.assertTrue(publish(self.run, self.wiki)['idempotent'])
        self.assertEqual(len(read_json(self.wiki / '.wiki-doc/index.json')['pages']), 1)
        self.assertEqual(len(read_json(self.wiki / '.wiki-doc/lineage.json')['edges']), 1)

    def test_explicit_rebuild_uses_lock_and_recoverable_pair(self):
        summary = rebuild_after_publish(self.wiki)
        self.assertEqual(summary['pages'], 1)
        journals = [read_json(p) for p in (self.wiki / '.wiki-doc/journal').glob('*.json')]
        self.assertTrue(any(j.get('kind') == 'catalogue' and len(j['files']) == 2 for j in journals))

    def update_run(self):
        run = self.root / 'update'; run.mkdir()
        make_bundle(run)
        old_id = read_json(run / 'facts.json')['run_id']
        new_id = str(uuid.uuid4())
        for path in run.glob('*.json'):
            path.write_text(path.read_text(encoding='utf-8').replace(old_id, new_id), encoding='utf-8')
        prepare(run, self.wiki)
        self.assertEqual(seal(run)['decision'], 'ready')
        return run

    def committed_bytes(self):
        names = [PAGE_ID, 'index.md', metadata_path(self.wiki, PAGE_ID).relative_to(self.wiki).as_posix(),
                 '.wiki-doc/index.json', '.wiki-doc/lineage.json']
        return {n:(self.wiki / n).read_bytes() for n in names}

    def test_new_catalogue_faults_restore_all_five_previous_files(self):
        run = self.update_run()
        before = self.committed_bytes()
        for stage in ('before_machine_index','after_machine_index','before_lineage','after_lineage'):
            def fault(label):
                if label == stage: raise RuntimeError(stage)
            with self.subTest(stage=stage), self.assertRaises(RuntimeError):
                publish(run,self.wiki,fault=fault)
            self.assertEqual(before, self.committed_bytes())

    def test_catalogue_process_death_refuses_query_then_restores_old_generation(self):
        run = self.update_run()
        before = self.committed_bytes()
        code = 'from publish import publish; import os,sys; publish(sys.argv[1],sys.argv[2],fault=lambda s: os._exit(71) if s==sys.argv[3] else None)'
        for stage in ('after_machine_index','after_lineage'):
            result = subprocess.run([sys.executable,'-B','-c',code,str(run),str(self.wiki),stage],capture_output=True,timeout=45,env=SUBPROCESS_ENV)
            self.assertEqual(result.returncode,71,result.stderr)
            with self.assertRaises(IndexError): query_page(self.wiki,PAGE_ID)
            self.assertEqual(recover(self.wiki)[0]['state'],'rolled_back')
            self.assertEqual(before,self.committed_bytes())

    def test_foreign_json_edit_is_preserved_by_recovery(self):
        run = self.update_run()
        path = self.wiki / '.wiki-doc/index.json'
        def fault(label):
            if label == 'after_machine_index':
                path.write_text('foreign edit')
                raise RuntimeError('foreign edit')
        with self.assertRaises(RuntimeError): publish(run,self.wiki,fault=fault)
        self.assertEqual(path.read_text(),'foreign edit')
        self.assertEqual(recover(self.wiki)[0]['state'],'conflict')

    def test_explicit_rebuild_failure_restores_the_pair(self):
        before=self.committed_bytes()
        import publish as module
        original=module.os.replace
        failed=[]
        def replace(src,dst):
            if Path(dst)==self.wiki/'.wiki-doc/lineage.json' and not failed:
                failed.append(True)
                raise OSError('simulated disk failure')
            return original(src,dst)
        with patch.object(module.os,'replace',side_effect=replace):
            with self.assertRaises(OSError): rebuild_after_publish(self.wiki)
        self.assertEqual(before,self.committed_bytes())

    def test_query_unknown_page_and_negative_depth(self):
        with self.assertRaises(QueryError): query_page(self.wiki, 'absent.md')
        with self.assertRaises(QueryError): query_page(self.wiki, PAGE_ID, max_depth=-1)


class GraphSemanticsTests(unittest.TestCase):
    def graph(self, objects, operations, key='function+demo+caller+()', published=()):
        main = dict(id='owner', kind='function', schema='demo', name='caller', canonical_key=key)
        record = dict(canonical_key=key, page_id='caller.md', run_id='run', bundle='.wiki-doc/runs/run')
        ref = dict(path='source.sql', sha256='a'*64, start_line=1, end_line=2)
        operations = [dict(id=f'op_{i}', source_refs=[ref], **op) for i, op in enumerate(operations)]
        facts = dict(objects=[main, *objects], operations=operations)
        bundles=[(record, facts, main, dict(sql_files=[ref]))]
        for i,obj in enumerate(published):
            doc=dict(id=f'doc_{i}',**obj)
            metadata=dict(canonical_key=doc['canonical_key'],page_id=f'callee_{i}.md',run_id='run',bundle='.wiki-doc/runs/run')
            bundles.append((metadata,dict(objects=[doc],operations=[]),doc,dict(sql_files=[ref])))
        return _graph(bundles, [], 'timestamp')

    def test_local_entities_have_distinct_names_kinds_and_page_scopes(self):
        objects = [dict(id=i, kind=k, name=i, scope='owner', physical=False)
                   for i, k in [('first','cte'), ('second','cte'), ('temp','temp_table')]]
        graph = self.graph(objects, [dict(reads=['first','second','temp'])])
        targets = {e['to'] for e in graph['edges']}
        self.assertEqual(len(targets), 3)
        self.assertEqual(len(graph['nodes']), 1)
        self.assertEqual(sum(t.startswith('@temp:') for t in targets), 1)
        other = self.graph(objects, [dict(reads=['first'])], key='function+other+caller+()')
        self.assertNotIn(other['edges'][0]['to'], targets)

    def test_missing_signature_is_external_and_repeated_operations_keep_evidence(self):
        graph = self.graph([dict(id='callee', kind='function', schema='demo', name='helper')],
                           [dict(calls=['callee']), dict(calls=['callee'])])
        result = query_dependencies(graph, 'function+demo+caller+()')
        self.assertEqual(len(result['calls']), 1)
        self.assertTrue(result['calls'][0].startswith('@external:'))
        self.assertEqual(len(result['edges']), 2)
        self.assertEqual({e['operation_id'] for e in result['edges']}, {'op_0','op_1'})

    def test_dynamic_gap_is_visible(self):
        graph = self.graph([], [dict(dynamic={'template':'TRUNCATE %I','unresolved_parts':['target']})])
        result = query_dependencies(graph, 'function+demo+caller+()')
        self.assertEqual(result['writes'], [])
        self.assertEqual(len(result['gaps']), 1)

    def test_unknown_signature_preserves_possible_overloads_without_choosing(self):
        published=[dict(kind='function',schema='demo',name='helper',signature=f'({t})',
                        canonical_key=f'function+demo+helper+({t})') for t in ('integer','text')]
        graph=self.graph([dict(id='callee',kind='function',schema='demo',name='helper')],
                         [dict(calls=['callee'])],published=published)
        edge=graph['edges'][0]
        self.assertEqual(edge['resolution'],'unresolved')
        self.assertEqual(len(edge['candidates']),2)
        for key in edge['candidates']:
            consumers=query_consumers(graph,key)
            self.assertEqual(consumers['called_by'],[])
            self.assertEqual(len(consumers['possible_consumers']),1)
            self.assertTrue(query_affected(graph,key)[0]['possible'])

    def test_generic_table_reference_resolves_to_published_view(self):
        graph=self.graph([dict(id='view',kind='table',schema='demo',name='v')],[dict(reads=['view'])],
            published=[dict(kind='view',schema='demo',name='v',canonical_key='view+demo+v')])
        self.assertEqual(graph['edges'][0]['to'],'view+demo+v')
        self.assertEqual(graph['edges'][0]['resolution'],'published')

    def test_duplicate_published_identity_is_rejected(self):
        with self.assertRaises(IndexError):
            self.graph([],[],published=[dict(kind='function',schema='demo',name='caller',
                signature='()',canonical_key='function+demo+caller+()')])

    def test_identifier_encoding_and_routine_type_normalization(self):
        graph = self.graph([dict(id='table',kind='table',schema='MiX',name='a+b'),
                            dict(id='fn',kind='function',schema='demo',name='f',signature='(int4)')],
                           [dict(reads=['table'],calls=['fn'])])
        result = query_dependencies(graph, 'function+demo+caller+()')
        self.assertEqual(result['reads'], ['table+MiX+a%2Bb'])
        self.assertEqual(result['calls'], ['function+demo+f+(integer)'])

    def test_data_cascade_cycles_depth_and_possible_overload(self):
        def edge(a,b,kind,**kw): return dict(**{'from':a,'to':b,'kind':kind}, **kw)
        graph = dict(nodes={k:dict(page_id=k+'.md') for k in ('producer','reader','caller')}, edges=[
            edge('producer','input','read'), edge('producer','output','write'),
            edge('reader','output','read'), edge('caller','reader','call'), edge('reader','caller','call'),
            edge('possible','@external:overload','call',candidates=['caller'])])
        affected = query_affected(graph, 'input')
        by_node = {e['node']:e for e in affected}
        self.assertEqual(by_node['reader']['via'], 'output')
        self.assertEqual(by_node['caller']['depth'], 4)
        self.assertTrue(by_node['possible']['possible'])
        self.assertEqual(len(query_affected(graph, 'input', max_depth=1)), 1)
        self.assertEqual(query_affected(graph, 'input', max_depth=0), [])


class RealGraphTests(unittest.TestCase):
    def test_sql_producer_output_consumer_crosses_published_pages(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); wiki=root/'wiki'; source=root/'source.sql'
            source.write_text('CREATE TABLE demo.src (value numeric);\nCREATE TABLE demo.dst (value numeric);\n'
                'CREATE FUNCTION demo.produce() RETURNS void LANGUAGE sql AS $$\n'
                'INSERT INTO demo.dst(value) SELECT value FROM demo.src;\n$$;\n'
                'CREATE VIEW demo.consumer AS SELECT value FROM demo.dst;\n')
            for i,subject in enumerate(('demo.produce','demo.consumer')):
                run=root/f'run{i}'
                result=build(source,run,project_root=root,subject=subject)
                self.assertEqual(result['decision'],'ready',result)
                prepare(run,wiki)
                result=finish(run,sql_files=[source],context=[],project_root=root,wiki_root=wiki)
                self.assertEqual(result['decision'],'ready',result)
                publish(run,wiki,project_root=root)
            graph=build_lineage(wiki)
            impacted=query_affected(graph,'table+demo+src')
            self.assertIn('view+demo+consumer',{r['node'] for r in impacted})
            self.assertEqual(len(build_index(wiki)['pages']),2)


if __name__ == '__main__': unittest.main()
