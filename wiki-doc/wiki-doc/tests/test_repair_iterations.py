"""Repair rounds are real attempts: executed work is counted, requests are not."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class RepairRoundTests(unittest.TestCase):
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

    def write_stub(self, repair_writer_body, initial_writer_body=None):
        if initial_writer_body is None:
            initial_writer_body = (
                "    target = run / 'page.draft.md'\n"
                "    marker = '<!-- wiki-doc:managed end -->'\n"
                "    text = target.read_text(encoding='utf-8')\n"
                "    target.write_text(text.replace(marker, 'Example: `wrong.f(1)`\\n' + marker), encoding='utf-8')\n")
        self.stub.write_text(
            'import json, sys\n'
            'from pathlib import Path\n'
            f'run = Path({str(self.output / "0")!r})\n'
            'message = sys.argv[-1]\n'
            "if 'validation-review.md' in message:\n"
            "    target = run / 'validation.json'\n"
            "    report = json.loads(target.read_text(encoding='utf-8'))\n"
            "    for item in report['checks']: item['status'] = 'ok'\n"
            "    target.write_text(json.dumps(report), encoding='utf-8')\n"
            "    review = run / 'validation-review.md'\n"
            "    review.write_text((review.read_text(encoding='utf-8') if review.is_file() else '') + 'checked\\n', encoding='utf-8')\n"
            "elif 'writer-repair' in message:\n"
            + repair_writer_body +
            'else:\n'
            + initial_writer_body,
            encoding='utf8')

    def run_adapter(self, rounds):
        return agent_adapter.main([str(self.request()),
                                   '--agent', json.dumps([sys.executable, '-B',
                                                          str(self.stub), '{message}']),
                                   '--model', 'stub-model',
                                   '--repair-rounds', str(rounds)])

    def record(self):
        return read_json(self.root / 'agent-logs' / '0-adapter.json')

    def test_repair_round_fixes_refused_page_and_is_counted(self):
        self.write_stub(
            "    target = run / 'page.draft.md'\n"
            "    text = target.read_text(encoding='utf-8')\n"
            "    text = text.replace('\\nExample: `wrong.f(1)`\\n', '\\n')\n"
            "    target.write_text(text, encoding='utf-8')\n")
        self.assertEqual(self.run_adapter(1), 0)
        record = self.record()
        self.assertEqual(record['initial_decision'], 'revise', record['gate'])
        self.assertEqual(record['repair_rounds'], 1)
        self.assertEqual(record['repair_history'], [
            {'round': 1, 'outcome': 'sealed', 'decision': 'ready', 'errors': []}],
            record['repair_history'])
        self.assertEqual(record['gate']['decision'], 'ready', record['gate'])
        self.assertTrue(record['gate']['publication_authorized'])
        self.assertEqual([(s['role'], s['round'], s['delivered']) for s in record['seat_attempts']],
                         [('writer', 0, True), ('validator', 0, True),
                          ('writer', 1, True), ('validator', 1, True)])
        self.assertEqual(record['writer']['command'], record['seat_attempts'][0]['command'])
        self.assertEqual(record['validator']['command'], record['seat_attempts'][1]['command'])
        runs = read_json(self.output / 'runs.json')['runs']
        self.assertEqual(runs[0]['initial_decision'], 'revise')
        self.assertEqual(runs[0]['repair_rounds'], 1)
        self.assertEqual(read_json(self.output / '0' / 'decision.json')['decision'], 'ready')

    def test_ready_first_attempt_claims_no_repair(self):
        self.write_stub(
            '    pass\n',
            initial_writer_body=(
                "    target = run / 'page.draft.md'\n"
                "    marker = '<!-- wiki-doc:managed end -->'\n"
                "    text = target.read_text(encoding='utf-8')\n"
                "    target.write_text(text.replace(marker, 'Stub writer prose.\\n' + marker), encoding='utf-8')\n"))
        self.assertEqual(self.run_adapter(2), 0)
        record = self.record()
        self.assertEqual(record['initial_decision'], 'ready', record['gate'])
        self.assertEqual(record['repair_rounds'], 0)
        self.assertEqual(record['repair_history'], [])
        runs = read_json(self.output / 'runs.json')['runs']
        self.assertEqual(runs[0]['repair_rounds'], 0)

    def test_failed_repair_round_restores_the_sealed_bundle(self):
        self.write_stub('    pass\n')
        self.assertEqual(self.run_adapter(1), 0)
        record = self.record()
        self.assertEqual(record['initial_decision'], 'revise', record['gate'])
        self.assertEqual(record['repair_rounds'], 1)
        self.assertEqual(record['repair_history'], [{'round': 1, 'outcome': 'writer_failed'}])
        self.assertEqual(record['gate']['decision'], 'revise', record['gate'])
        page = (self.output / '0' / 'page.draft.md').read_text(encoding='utf-8')
        self.assertIn('Example: `wrong.f(1)`', page)
        self.assertEqual(read_json(self.output / '0' / 'decision.json')['decision'], 'revise')

    def test_coverage_only_repair_is_delivered(self):
        self.write_stub(
            "    (run / 'coverage.json').write_bytes((run / 'coverage.backup').read_bytes())\n",
            initial_writer_body=(
                "    import json\n"
                "    target = run / 'coverage.json'\n"
                "    (run / 'coverage.backup').write_bytes(target.read_bytes())\n"
                "    data = json.loads(target.read_text(encoding='utf-8'))\n"
                "    next(iter(data['entries'].values()))[0]['section_id'] = 'dataflow_diagram'\n"
                "    target.write_text(json.dumps(data), encoding='utf-8')\n"))
        self.assertEqual(self.run_adapter(1), 0)
        record = self.record()
        self.assertEqual(record['initial_decision'], 'revise')
        self.assertEqual(record['gate']['decision'], 'ready', record)

    def test_failed_writer_restores_protected_bundle_files(self):
        self.write_stub(
            "    (run / 'facts.backup').write_bytes((run / 'facts.json').read_bytes())\n"
            "    (run / 'facts.json').write_text('{}', encoding='utf-8')\n"
            "    (run / 'decision.json').unlink()\n")
        self.assertEqual(self.run_adapter(1), 0)
        run = self.output / '0'
        self.assertEqual((run / 'facts.json').read_bytes(), (run / 'facts.backup').read_bytes())
        self.assertEqual(read_json(run / 'decision.json')['decision'], 'revise')

    def test_malformed_repair_is_rejected_at_binding_and_rolled_back(self):
        self.write_stub(
            "    page = run / 'page.draft.md'\n"
            "    page.write_text(page.read_text(encoding='utf-8') + '\\nchanged', encoding='utf-8')\n"
            "    (run / 'facts.backup').write_bytes((run / 'facts.json').read_bytes())\n"
            "    (run / 'facts.json').write_text('{', encoding='utf-8')\n")
        self.assertEqual(self.run_adapter(1), 0)
        run = self.output / '0'
        self.assertEqual((run / 'facts.json').read_bytes(), (run / 'facts.backup').read_bytes())
        self.assertEqual(self.record()['repair_history'][0]['outcome'], 'writer_failed')
        failure = read_json(self.root / 'agent-logs/0-binding-failure.json')
        self.assertIn('artifacts.facts: sha256 mismatch', failure['error'])
        self.assertFalse(failure['publication_authorized'])
        self.assertEqual(read_json(run / 'decision.json')['decision'], 'revise')

    def test_adapter_rejects_invalid_repair_limits_before_creating_a_run(self):
        for count in (-1, 4):
            with self.subTest(count=count):
                with self.assertRaises(SystemExit) as caught:
                    self.run_adapter(count)
                self.assertEqual(caught.exception.code, 2)
        self.assertFalse((self.output / '0').exists())

    def test_failed_validator_restores_page_and_keeps_stale_review_undelivered(self):
        self.write_stub(
            "    page = run / 'page.draft.md'\n"
            "    page.write_text(page.read_text(encoding='utf-8').replace('wrong.f(1)', 'demo.f()'), encoding='utf-8')\n")
        script = self.stub.read_text(encoding='utf-8')
        script = script.replace("if 'validation-review.md' in message:\n",
                                "if 'validator-repair' in message:\n"
                                "    sys.exit(3)\n"
                                "elif 'validation-review.md' in message:\n")
        self.stub.write_text(script, encoding='utf-8')
        self.assertEqual(self.run_adapter(1), 0)
        record = self.record()
        self.assertEqual(record['repair_history'][0]['outcome'], 'validator_failed')
        self.assertFalse(record['seat_attempts'][-1]['delivered'])
        self.assertIn('wrong.f(1)', (self.output / '0/page.draft.md').read_text(encoding='utf-8'))
        self.assertEqual(read_json(self.output / '0/decision.json')['decision'], 'revise')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows batch transport')
    def test_long_batch_prompt_is_delivered_verbatim_through_a_task_file(self):
        self.stub.write_text(
            "import sys\nfrom pathlib import Path\n"
            "task = Path(sys.argv[-1].split('TASK_FILE: ', 1)[1])\n"
            "print(task.read_text(encoding='utf-8'))\n", encoding='utf-8')
        wrapper = self.root / 'agent.cmd'
        wrapper.write_text(f'@"{sys.executable}" "{self.stub}" %*\n', encoding='utf-8')
        message = 'A long task\n' + 'repair feedback ' * 1000
        log = self.root / 'transport.log'
        code, _, command = agent_adapter.call_agent(
            [str(wrapper), '{message}'], message=message, cwd=self.root,
            model='stub', timeout=20, log_path=log)
        self.assertEqual(code, 0, log.read_bytes())
        self.assertLess(len(command[-1]), 1000)
        self.assertEqual(log.with_suffix('.task.md').read_text(encoding='utf-8'), message)
        self.assertIn(b'A long task\r\nrepair feedback', log.read_bytes())


class RepairAccountingTests(unittest.TestCase):
    def test_conflicting_adapter_limits_are_rejected_before_run_creation(self):
        from run_regression import run_suite
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'runs'
            for flags in (['--repair-rounds', '1'], ['--repair-rounds=1'],
                          ['--repair-rounds', '2', '--repair-rounds', '2']):
                with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'must match'):
                    run_suite(PACKAGE / 'examples/cases-q.json', output, mode='adapter',
                              iterations=2, model='stub', adapter=['stub', '{request}', *flags])
            self.assertFalse(output.exists())

    def test_invalid_saved_repair_accounting_is_a_diagnostic_not_a_crash(self):
        from run_regression import run_suite
        case = read_json(PACKAGE / 'examples/cases-q.json')['cases'][0]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            out = output / 'run-01' / case['id'] / 'output'
            (out / '0').mkdir(parents=True)
            for count, initial in [('2', 'revise'), (True, 'revise'), (-1, 'revise'),
                                   (4, 'revise'), (1, None), (1, 'ready'), (0, 'invented')]:
                with self.subTest(count=count, initial=initial):
                    (out / 'runs.json').write_text(json.dumps({'runs': [dict(
                        subject=case['subjects'][0], run_dir='0', repair_rounds=count,
                        initial_decision=initial)]}), encoding='utf-8')
                    with patch('run_regression.check_run') as checker:
                        report = run_suite(PACKAGE / 'examples/cases-q.json', output,
                                           repeats=1, cases_filter={case['id']})
                    checker.assert_not_called()
                    self.assertFalse(report['valid'])
                    self.assertEqual(report['repair_iterations'], 0)
                    self.assertTrue(report['results'][0]['errors'])

    def test_report_counts_executed_rounds_not_the_cli_parameter(self):
        from run_regression import run_suite
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub = root / 'stub_adapter.py'
            stub.write_text(
                'import json, subprocess, sys\n'
                'from pathlib import Path\n'
                'request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))\n'
                f'proc = subprocess.run([sys.executable, "-B", {str(PACKAGE / "scripts/regression_adapter.py")!r}, sys.argv[1]])\n'
                'if proc.returncode:\n'
                '    raise SystemExit(proc.returncode)\n'
                "out = Path(request['output_dir'])\n"
                "data = json.loads((out / 'runs.json').read_text(encoding='utf-8'))\n"
                "for row in data['runs']:\n"
                "    row['repair_rounds'] = 2\n"
                "    row['initial_decision'] = 'revise'\n"
                "(out / 'runs.json').write_text(json.dumps(data), encoding='utf-8')\n",
                encoding='utf8')
            report = run_suite(PACKAGE / 'examples/cases-q.json', root / 'out',
                               mode='adapter', repeats=1, iterations=3,
                               adapter=[sys.executable, '-B', str(stub), '{request}'],
                               model='stub-model', cases_filter={'q01'})
        self.assertEqual(report['repair_iterations_requested'], 3)
        self.assertEqual(report['repair_iterations'], 2)
        row = report['results'][0]
        self.assertEqual(row['repair_iterations'], 2)
        self.assertEqual(row['initial_decision'], 'revise')
        self.assertEqual(row['decision'], 'ready')
        self.assertTrue(report['valid'], report)

    def test_repair_iterations_must_be_valid_and_refer_to_adapter_runs(self):
        from run_regression import run_suite
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'runs'
            for count in (-1, 4):
                with self.subTest(count=count), self.assertRaisesRegex(
                        ValueError, 'between 0 and 3'):
                    run_suite(PACKAGE / 'examples/cases-q.json', output, repeats=1,
                              iterations=count)
            for mode in ('saved', 'reference'):
                with self.subTest(mode=mode), self.assertRaisesRegex(
                        ValueError, 'require adapter mode'):
                    run_suite(PACKAGE / 'examples/cases-q.json', output, mode=mode,
                              repeats=1, iterations=1)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
