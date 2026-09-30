"""Agent adapter plumbing: substrate, LLM seats and sealing without expectations."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from bundle_fixture import PACKAGE, read_json

sys.path.insert(0, str(PACKAGE / 'scripts'))
import agent_adapter

SQL = ('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$\n'
       'BEGIN\n'
       '  INSERT INTO demo.t(id) SELECT id FROM demo.s;\n'
       'END;\n'
       '$$;\n')
CONTEXT = 'CREATE TABLE demo.t(id int);\nCREATE TABLE demo.s(id int);\n'
SUBJECT = 'function+demo+f+()'


class AgentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'input'
        self.project.mkdir()
        (self.project / 'source.sql').write_text(SQL, encoding='utf8')
        (self.project / 'context.sql').write_text(CONTEXT, encoding='utf8')
        self.output = self.root / 'output'
        self.output.mkdir()
        self.stub = self.root / 'stub_agent.py'

    def request(self):
        path = self.root / 'request.json'
        path.write_text(json.dumps({
            'schema_version': 1, 'project_root': str(self.project),
            'output_dir': str(self.output), 'skill_root': str(PACKAGE),
            'sql': 'source.sql', 'context': ['context.sql'], 'subjects': [SUBJECT],
            'version': '15', 'dialect': 'postgres',
        }), encoding='utf8')
        return path

    def argv(self, code=0):
        run_dir = self.output / '0'
        self.stub.write_text(
            'import json, sys\n'
            'from pathlib import Path\n'
            f'run = Path({str(run_dir)!r})\n'
            "if 'validator-агент' in sys.argv[-1]:\n"
            "    target = run / 'validation.json'\n"
            "    data = json.loads(target.read_text(encoding='utf-8'))\n"
            "    for item in data['checks']: item['status'] = 'ok'\n"
            "    data['checks'][0]['reason'] = 'Stub validator reviewed the draft.'\n"
            "    target.write_text(json.dumps(data), encoding='utf-8')\n"
            'else:\n'
            "    target = run / 'page.draft.md'\n"
            "    target.write_text(target.read_text(encoding='utf-8') + '\\nStub writer prose.\\n',\n"
            "                      encoding='utf-8')\n"
            f'sys.exit({code})\n',
            encoding='utf8')
        return ['--agent', json.dumps([sys.executable, '-B', str(self.stub), '{message}']),
                '--model', 'stub-model']

    def test_stub_agent_seals_a_complete_bundle(self):
        result = agent_adapter.main([str(self.request()), *self.argv()])
        self.assertEqual(result, 0)
        runs = json.loads((self.output / 'runs.json').read_text(encoding='utf-8'))
        self.assertEqual(runs['schema_version'], 1)
        self.assertEqual([(r['subject'], r['run_dir']) for r in runs['runs']], [(SUBJECT, '0')])
        run_dir = self.output / '0'
        for name in ('facts.json', 'inventory.json', 'validation_plan.json',
                     'page.draft.md', 'coverage.json', 'validation.json',
                     'manifest.json', 'decision.json'):
            self.assertTrue((run_dir / name).is_file(), name)
        record = read_json(self.root / 'agent-logs' / '0-adapter.json')
        self.assertEqual(record['subject'], SUBJECT)
        self.assertEqual(record['model'], 'stub-model')
        self.assertEqual(record['repair_rounds'], 0)
        # Run-owned evidence is re-bound to the sealed bytes, so the rewritten
        # page does not invalidate the mechanical validation draft.
        self.assertEqual(record['gate']['decision'], 'ready', record['gate'])
        self.assertTrue(record['gate']['publication_authorized'])
        self.assertEqual(record['writer']['continuations'], 0)
        self.assertEqual(len(record['writer']['page_sha256']), 64)
        self.assertEqual(len(record['validator']['validation_sha256']), 64)
        for seat in ('0-writer.log', '0-validator.log'):
            self.assertTrue((self.root / 'agent-logs' / seat).is_file(), seat)

    def test_windows_bom_request_and_validator_json_seal_with_original_identity(self):
        request = self.request()
        request.write_text(request.read_text(encoding='utf-8'), encoding='utf-8-sig')
        args = self.argv()
        script = self.stub.read_text(encoding='utf-8').replace(
            "target.write_text(json.dumps(data), encoding='utf-8')",
            "target.write_text(json.dumps(data), encoding='utf-8-sig')")
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(agent_adapter.main([str(request), *args]), 0)
        run = self.output / '0'
        binding = read_json(self.root / 'agent-logs/0-binding.json')
        manifest = read_json(run / 'manifest.json')
        self.assertEqual(manifest['run_id'], binding['manifest']['run_id'])
        self.assertEqual(read_json(run / 'decision.json')['decision'], 'ready')
        self.assertEqual(manifest['artifacts']['validation']['sha256'],
                         agent_adapter.sha256_bytes((run / 'validation.json').read_bytes()))

    def test_independent_validator_routes_initial_and_repair_seats(self):
        args = self.argv()
        trace = self.root/'seat-routing.jsonl'
        source = self.stub.read_text(encoding='utf-8')
        source = source.replace('from pathlib import Path\n',
            'from pathlib import Path\n'
            f'trace = Path({str(trace)!r})\n'
            'with trace.open("a", encoding="utf-8") as out:\n'
            '    out.write(json.dumps(sys.argv[1:3]) + "\\n")\n')
        source = source.replace("    target.write_text(json.dumps(data), encoding='utf-8')",
            "    if len(trace.read_text(encoding='utf-8').splitlines()) == 2:\n"
            "        data['checks'][0].update(status='defect', reason='First review requires correction.')\n"
            "    target.write_text(json.dumps(data), encoding='utf-8')")
        self.stub.write_text(source, encoding='utf-8')
        writer = [sys.executable, '-B', str(self.stub), 'writer', '{model}', '{message}']
        validator = [sys.executable, '-B', str(self.stub), 'validator', '{model}', '{message}']
        self.assertEqual(agent_adapter.main([str(self.request()), *args,
            '--writer-agent', json.dumps(writer), '--validator-agent', json.dumps(validator),
            '--validator-model', 'independent-test-model', '--repair-rounds', '1']), 0)
        calls = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(calls, [['writer', 'stub-model'], ['validator', 'independent-test-model']]*2)
        record = read_json(self.root/'agent-logs/0-adapter.json')
        self.assertEqual(record['models'], {'writer': 'stub-model', 'validator': 'independent-test-model'})
        self.assertEqual(record['initial_decision'], 'revise')
        self.assertEqual(record['repair_rounds'], 1)
        self.assertEqual(record['gate']['decision'], 'ready')

    def test_duplicate_validation_keys_are_not_normalized_away_during_rebind(self):
        args = self.argv()
        script = self.stub.read_text(encoding='utf-8').replace(
            "target.write_text(json.dumps(data), encoding='utf-8')",
            "target.write_text(json.dumps(data)[:-1] + ',\\\"checks\\\": []}', encoding='utf-8-sig')")
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(agent_adapter.main([str(self.request()), *args]), 1)
        self.assertFalse((self.output / '0/decision.json').exists())
        self.assertEqual(read_json(self.output / 'runs.json')['runs'], [])

    def test_review_note_without_explicit_check_results_cannot_inherit_ready(self):
        args = self.argv()
        script = self.stub.read_text(encoding='utf-8').replace(
            "    for item in data['checks']: item['status'] = 'ok'\n", '')
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(agent_adapter.main([str(self.request()), *args]), 0)
        record = read_json(self.root / 'agent-logs/0-adapter.json')
        self.assertEqual(record['gate']['decision'], 'blocked')
        self.assertFalse(record['gate']['publication_authorized'])
        self.assertTrue(all(item['status'] == 'inconclusive'
                            for item in read_json(self.output/'0/validation.json')['checks']))

    def test_partial_review_leaves_unreviewed_obligations_blocking(self):
        args = self.argv()
        script = self.stub.read_text(encoding='utf-8').replace(
            "    for item in data['checks']: item['status'] = 'ok'\n",
            "    data['checks'][0]['status'] = 'ok'\n")
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(agent_adapter.main([str(self.request()), *args]), 0)
        self.assertEqual(read_json(self.root/'agent-logs/0-adapter.json')['gate']['decision'], 'blocked')

    def test_extra_prose_defect_blocks_otherwise_complete_review(self):
        args = self.argv()
        script = self.stub.read_text(encoding='utf-8').replace(
            "    target.write_text(json.dumps(data), encoding='utf-8')",
            "    data['checks'].append(dict(id='review:1', category='technical', blocking=True, "
            "status='defect', defect_code='unsupported_claim', fact_ids=['obj_1'], "
            "reason='Prose recommends CALL for a function; SQL declares CREATE FUNCTION.', "
            "evidence=data['checks'][0]['evidence']))\n"
            "    target.write_text(json.dumps(data), encoding='utf-8')")
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(agent_adapter.main([str(self.request()), *args]), 0)
        gate = read_json(self.root/'agent-logs/0-adapter.json')['gate']
        self.assertEqual(gate['decision'], 'revise', gate)
        self.assertFalse(gate['publication_authorized'])

    def test_prose_projection_omits_only_matching_claims_and_preserves_line_numbers(self):
        request = read_json(self.request())
        run = self.output/'projection'
        agent_adapter.build_substrate(request, SUBJECT, run)
        draft = ('## Plain heading\n'
                 '<!-- wiki-doc:fragment obj_1 -->\n'
                 '| Fact | Property | SQL value |\n'
                 '| --- | --- | --- |\n'
                 '| obj_1 | kind | "function" |\n\n'
                 'A visible unsupported guarantee.\n'
                 '| Fact | Property | SQL value |\n'
                 '| --- | --- | --- |\n'
                 '| ordinary | assertion | "unverified" |\n\n'
                 '```sql\nCALL demo.f();\n```\n'
                 '<!-- wiki-doc:fragment fake --> visible trailing text\n')
        (run/'page.draft.md').write_text(draft, encoding='utf-8')
        agent_adapter.prepare_semantic_review(run)
        projection = (run/'page.prose.txt').read_text(encoding='utf-8')
        self.assertNotIn('| obj_1 | kind |', projection)
        for number in (1, 7, 8, 9, 10, 12, 13, 14, 15):
            self.assertIn(f'L{number:06d} | {draft.splitlines()[number-1]}', projection)
        self.assertEqual((run/'page.draft.md').read_text(encoding='utf-8'), draft)
        self.assertTrue(all(item['status'] == 'inconclusive'
                            for item in read_json(run/'validation.json')['checks']))
        report = read_json(run/'validation.json')
        for item in report['checks']:
            item['status'] = 'ok'
        (run/'validation.json').write_text(json.dumps(report), encoding='utf-8')
        (run/'page.draft.md').write_text(draft+'New assertion after repair.\n', encoding='utf-8')
        agent_adapter.prepare_semantic_review(run)
        self.assertTrue(all(item['status'] == 'inconclusive'
                            for item in read_json(run/'validation.json')['checks']))
        self.assertIn('New assertion after repair.', (run/'page.prose.txt').read_text(encoding='utf-8'))

    def test_prose_projection_preserves_semantics_inside_formatted_claim_cells(self):
        request = read_json(self.request())
        run = self.output/'formatted-projection'
        agent_adapter.build_substrate(request, SUBJECT, run)
        for value in ('"function" ![Unsupported guarantee](#entities)',
                      '["function"](#entities "Unsupported guarantee")',
                      '~~"function"~~',
                      '<span title="Unsupported guarantee">"function"</span>'):
            with self.subTest(value=value):
                draft = ('| Fact | Property | SQL value |\n| --- | --- | --- |\n'
                         f'| obj_1 | kind | {value} |\n')
                (run/'page.draft.md').write_text(draft, encoding='utf-8')
                agent_adapter.prepare_semantic_review(run)
                self.assertIn(value, (run/'page.prose.txt').read_text(encoding='utf-8'))

    def test_rebinding_preserves_current_precise_and_invalid_ranges_for_gate_review(self):
        run = self.output / 'ranges'
        run.mkdir()
        page = run / 'page.draft.md'
        page.write_text('first\nsecond\nthird\n', encoding='utf-8')
        digest = agent_adapter.sha256_bytes(page.read_bytes())
        entries = [dict(root='run', path='page.draft.md', start_line=2, end_line=end,
                        sha256=digest) for end in (2, 99999)]
        report = dict(checks=[dict(evidence=entries)])
        path = run / 'validation.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(agent_adapter.rebind_run_evidence(run), 0)
        self.assertEqual(read_json(path), report)
        page.write_text('replacement\n', encoding='utf-8')
        self.assertEqual(agent_adapter.rebind_run_evidence(run), 2)
        for ref in read_json(path)['checks'][0]['evidence']:
            self.assertEqual((ref['start_line'], ref['end_line']), (1, 1))
            self.assertEqual(ref['sha256'], agent_adapter.sha256_bytes(page.read_bytes()))

    def test_failing_agent_does_not_produce_runs(self):
        # A seat that fails without delivering an artifact must not create runs;
        # delivered artifacts count even when the CLI exits non-zero afterwards.
        self.stub.write_text('import sys\nsys.exit(3)\n', encoding='utf8')
        result = agent_adapter.main([str(self.request()),
                                     '--agent', json.dumps([sys.executable, '-B', str(self.stub), '{message}']),
                                     '--model', 'stub-model'])
        self.assertEqual(result, 1)
        runs = json.loads((self.output / 'runs.json').read_text(encoding='utf-8'))
        self.assertEqual(runs['runs'], [])
        self.assertFalse((self.output / '0' / 'decision.json').exists())

    def test_seat_continues_the_session_after_an_output_cap_stop(self):
        run_dir = self.output / '0'
        run_dir.mkdir(parents=True)
        (run_dir / 'page.draft.md').write_text('base\n', encoding='utf8')
        self.stub.write_text(
            'import json, sys\n'
            'from pathlib import Path\n'
            f'run = Path({str(run_dir)!r})\n'
            "if '--session' in sys.argv:\n"
            "    target = run / 'page.draft.md'\n"
            "    target.write_text(target.read_text(encoding='utf-8') + 'prose\\n', encoding='utf-8')\n"
            "    print(json.dumps({'type': 'step_finish', 'sessionID': 'ses_stub',\n"
            "                      'part': {'reason': 'stop'}}))\n"
            'else:\n'
            "    print(json.dumps({'type': 'step_finish', 'sessionID': 'ses_stub',\n"
            "                      'part': {'reason': 'length'}}))\n"
            'sys.exit(0)\n',
            encoding='utf8')
        agent = [sys.executable, '-B', str(self.stub), '{message}']
        ok, seconds, command, continuations = agent_adapter.seat(
            agent, message='write the page', resume_message='continue and write the page',
            workspace=self.root, model='stub-model', timeout=30,
            log_path=self.root / 'agent-logs' / 'seat.log',
            artifact=run_dir / 'page.draft.md', rounds=2)
        self.assertTrue(ok)
        self.assertEqual(continuations, 1)
        self.assertIn('--session', command)
        self.assertIn('ses_stub', command)
        self.assertGreater(seconds, 0)
        self.assertEqual(agent_adapter.last_session_and_reason(
            self.root / 'agent-logs' / 'seat.log')[1], 'length')
        self.assertEqual(agent_adapter.last_session_and_reason(
            self.root / 'agent-logs' / 'seat-continue-1.log')[1], 'stop')

    def test_delivered_artifact_counts_even_with_nonzero_exit(self):
        result = agent_adapter.main([str(self.request()), *self.argv(code=3)])
        self.assertEqual(result, 0)
        runs = json.loads((self.output / 'runs.json').read_text(encoding='utf-8'))
        self.assertEqual([(r['subject'], r['run_dir']) for r in runs['runs']], [(SUBJECT, '0')])

    def test_prompts_point_to_skill_instructions_and_prepared_context(self):
        request = json.loads(self.request().read_text(encoding='utf-8'))
        writer = agent_adapter.prompt_for(agent_adapter.WRITER_PROMPT, PACKAGE,
                                          request, self.output / '0', subject=SUBJECT)
        for needle in ('doc-writer.md', 'template.md', 'facts.json', 'validation_plan.json',
                       'source.sql', SUBJECT, 'coverage.json', 'page.draft.md'):
            self.assertIn(needle, writer)
        self.assertNotIn('examples/expected', writer)
        validator = agent_adapter.prompt_for(agent_adapter.VALIDATOR_PROMPT, PACKAGE,
                                             request, self.output / '0', subject=SUBJECT, total=7)
        self.assertIn('doc-validator.md', validator)
        self.assertIn('7', validator)
        self.assertIn('root/path/start_line/end_line/sha256', validator)
        self.assertNotIn('examples/expected', validator)


    def test_seal_leaves_no_stale_ready_decision_after_refusal(self):
        run_dir = self.output / '0'
        self.stub.write_text(
            'import json, sys\n'
            'from pathlib import Path\n'
            f'run = Path({str(run_dir)!r})\n'
            "if 'validation-review.md' in sys.argv[-1]:\n"
            "    target = run / 'validation.json'\n"
            "    data = json.loads(target.read_text(encoding='utf-8'))\n"
            "    for item in data['checks']:\n"
            "        item['status'] = 'ok'\n"
            "        item['evidence'] = ['source.sql:1 - stub string evidence']\n"
            "    target.write_text(json.dumps(data), encoding='utf-8')\n"
            'else:\n'
            "    target = run / 'page.draft.md'\n"
            "    target.write_text(target.read_text(encoding='utf-8') + '\\nStub writer prose.\\n',\n"
            "                      encoding='utf-8')\n",
            encoding='utf8')
        result = agent_adapter.main([str(self.request()),
                                     '--agent', json.dumps([sys.executable, '-B', str(self.stub), '{message}']),
                                     '--model', 'stub-model'])
        self.assertEqual(result, 0)
        record = read_json(self.root / 'agent-logs' / '0-adapter.json')
        self.assertEqual(record['gate']['decision'], 'blocked', record['gate'])
        self.assertTrue(any('structured file references' in e for e in record['gate']['errors']),
                        record['gate'])
        self.assertFalse((run_dir / 'decision.json').is_file())


if __name__ == '__main__':
    unittest.main()
