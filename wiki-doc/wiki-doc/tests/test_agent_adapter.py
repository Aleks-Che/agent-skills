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
