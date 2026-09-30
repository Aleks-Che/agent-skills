"""Cross-seat identity admission, using private packages and real local stub processes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
import json, sys
from pathlib import Path
settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
sys.path.insert(0, str(Path(settings['launcher']) / 'scripts'))
import agent_adapter
work = Path(settings['work'])
request = work / 'request.json'
skill = Path(settings['skill'])
run = work / 'output/0'

def mutate():
    target = {'sql': work / 'input/source.sql', 'ddl': work / 'input/context.sql',
              'runtime': skill / 'doc-validator.md', 'facts': run / 'facts.json'}[settings['mutation']]
    with target.open('a', encoding='utf-8') as stream:
        stream.write('\n' if settings['mutation'] == 'facts' else '\n-- binding probe changed bytes\n')

if settings['boundary'] in ('during_build', 'after_build'):
    original = agent_adapter.build_substrate
    def build(*args):
        if settings['boundary'] == 'during_build':
            mutate()
        result = original(*args)
        if settings['boundary'] == 'after_build':
            mutate()
        return result
    agent_adapter.build_substrate = build
elif settings['boundary'] == 'before_writer':
    original = agent_adapter.prompt_for
    def prompt(*args, **kwargs):
        result = original(*args, **kwargs)
        mutate()
        return result
    agent_adapter.prompt_for = prompt
elif settings['boundary'] == 'before_seal':
    original = agent_adapter.reseal
    def reseal(*args):
        mutate()
        return original(*args)
    agent_adapter.reseal = reseal
elif settings['boundary'] == 'after_gate':
    original = agent_adapter.build_substrate
    def build(*args):
        result = original(*args)
        import validation_gate
        real_gate = validation_gate.evaluate_bundle
        def evaluate(*args, **kwargs):
            checked = real_gate(*args, **kwargs)
            mutate()
            return checked
        validation_gate.evaluate_bundle = evaluate
        return result
    agent_adapter.build_substrate = build

argv = [str(request), '--model', 'local-stub', '--llm-timeout', '30', '--repair-rounds',
        str(settings['repairs']), '--agent', json.dumps([sys.executable, '-B',
        str(work / 'stub.py'), str(work / 'settings.json'), '{message}'])]
code = agent_adapter.main(argv)
summary = dict(code=code, modules={name: str(Path(sys.modules[name].__file__).resolve())
               for name in ('agent_adapter', 'agent_process', 'build_bundle', 'bundle', 'validation_gate')
               if name in sys.modules})
(work / 'harness-result.json').write_text(json.dumps(summary), encoding='utf-8')
'''

STUB = r'''
import hashlib, json, sys, uuid
from pathlib import Path
settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
work = Path(settings['work'])
skill = Path(settings['skill'])
run = work / 'output/0'
role = 'validator' if 'validator-агент' in sys.argv[-1] else 'writer'
events_path = work / 'events.json'
events = json.loads(events_path.read_text(encoding='utf-8')) if events_path.exists() else []
attempt = sum(event['role'] == role for event in events)
facts = json.loads((run / 'facts.json').read_text(encoding='utf-8'))
events.append(dict(role=role, attempt=attempt, run_id=facts['run_id'],
                   selected_skill_in_prompt=str(skill) in sys.argv[-1]))
events_path.write_text(json.dumps(events), encoding='utf-8')
if role == 'writer' and attempt == 1:
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    names = {reference['path'] for reference in manifest['artifacts'].values()}
    names.update(('manifest.json', 'decision.json', 'validation-review.md'))
    snapshot = {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                for name in names if (run / name).is_file()}
    (work / 'before-repair.json').write_text(json.dumps(snapshot), encoding='utf-8')
if role == 'writer':
    page = run / 'page.draft.md'
    original = work / 'original-page.md'
    if not original.exists():
        original.write_bytes(page.read_bytes())
    text = original.read_text(encoding='utf-8') + '\nLocal writer documentation.\n'
    if settings['repairs'] and attempt == 0:
        text = text.replace('<!-- wiki-doc:fragment', '<!-- missing-fragment', 1)
    page.write_text(text, encoding='utf-8')
else:
    report_path = run / 'validation.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    for item in report['checks']:
        item['status'] = 'ok'
    report_path.write_text(json.dumps(report), encoding='utf-8')
    (run / 'validation-review.md').write_text('Local validator completed.\n' + str(attempt), encoding='utf-8')

if settings['boundary'] == role and attempt == settings['attempt']:
    mutation = settings['mutation']
    if mutation == 'uuid':
        replacement = str(uuid.uuid4())
        for path in run.glob('*.json'):
            path.write_text(path.read_text(encoding='utf-8').replace(facts['run_id'], replacement), encoding='utf-8')
    elif mutation != 'none':
        target = {'sql': work / 'input/source.sql', 'ddl': work / 'input/context.sql',
                  'runtime': skill / 'doc-validator.md', 'facts': run / 'facts.json',
                  'inventory': run / 'inventory.json', 'plan': run / 'validation_plan.json'}[mutation]
        with target.open('a', encoding='utf-8') as stream:
            stream.write('\n' if mutation in ('facts', 'inventory', 'plan') else '\n-- binding probe changed bytes\n')
'''


def copy_runtime(destination, source=PACKAGE):
    for directory in ('scripts', 'schemas', 'references', 'template', 'profiles', 'docs'):
        shutil.copytree(source / directory, destination / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in source.iterdir():
        if path.is_file() and path.suffix in ('.md', '.txt'):
            shutil.copy2(path, destination / path.name)


class AdapterBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # All cases inspect the same snapshot even while another local task edits code.
        cls.package_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.package_temp.cleanup)
        cls.package = Path(cls.package_temp.name) / 'package'
        copy_runtime(cls.package)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def case(self, mutation='none', boundary='writer', *, attempt=0, repairs=0, foreign_launcher=False,
             different_version=False):
        self.counter += 1
        work = self.root / str(self.counter)
        skill = work / 'skill'
        copy_runtime(skill, self.package)
        (work / 'input').mkdir()
        (work / 'output').mkdir()
        (work / 'input/source.sql').write_text('CREATE VIEW demo.v AS SELECT id FROM demo.t;\n', encoding='utf-8')
        (work / 'input/context.sql').write_text('CREATE TABLE demo.t(id bigint);\n', encoding='utf-8')
        if different_version:
            with (skill / 'doc-validator.md').open('a', encoding='utf-8') as stream:
                stream.write('\nSelected private runtime.\n')
        settings = dict(work=str(work), skill=str(skill), launcher=str(self.package if foreign_launcher else skill),
                        mutation=mutation, boundary=boundary, attempt=attempt, repairs=repairs)
        (work / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
        (work / 'stub.py').write_text(STUB, encoding='utf-8')
        request = dict(schema_version=1, project_root=str(work / 'input'), output_dir=str(work / 'output'),
                       skill_root=str(skill), sql='source.sql', context=['context.sql'],
                       subjects=['view+demo+v'], version='15', dialect='postgres')
        (work / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        process = subprocess.run([sys.executable, '-B', '-c', HARNESS, str(work / 'settings.json')],
                                 capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        def read(relative, default=None):
            path = work / relative
            return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default
        return dict(work=work, result=read('harness-result.json'), events=read('events.json', []),
                    log=read('agent-logs/0-adapter.json'), failure=read('agent-logs/0-binding-failure.json'),
                    binding=read('agent-logs/0-binding.json'), decision=read('output/0/decision.json'),
                    runs=read('output/runs.json'), stderr=process.stderr)

    def assert_refused(self, case):
        self.assertNotEqual((case['decision'] or {}).get('decision'), 'ready', case)
        self.assertFalse((case['log'] or {}).get('gate', {}).get('publication_authorized'), case)
        self.assertTrue(case['result']['code'] != 0 or case['failure'] or
                        (case['log'] or {}).get('gate', {}).get('decision') == 'blocked', case)

    def test_real_stub_preserves_one_uuid_and_selected_runtime(self):
        case = self.case()
        self.assertEqual(case['result']['code'], 0, case['stderr'])
        self.assertEqual(case['decision']['decision'], 'ready')
        self.assertEqual({event['run_id'] for event in case['events']}, {case['binding']['manifest']['run_id']})
        self.assertTrue(all(event['selected_skill_in_prompt'] for event in case['events']))

    def test_explicit_other_copy_supplies_build_and_gate(self):
        case = self.case(foreign_launcher=True)
        self.assertEqual(case['result']['code'], 0, case['stderr'])
        self.assertEqual(case['decision']['decision'], 'ready')
        for name in ('build_bundle', 'bundle', 'validation_gate'):
            self.assertTrue(Path(case['result']['modules'][name]).is_relative_to(case['work'] / 'skill'))

    def test_different_launcher_version_is_refused_before_writer(self):
        case = self.case(foreign_launcher=True, different_version=True)
        self.assert_refused(case)
        self.assertEqual(case['events'], [])
        self.assertIn('runtime differs', case['stderr'])

    def test_runtime_and_coherent_uuid_changes_after_writer_are_refused(self):
        for mutation in ('runtime', 'uuid'):
            with self.subTest(mutation=mutation):
                case = self.case(mutation)
                self.assert_refused(case)
                self.assertEqual([event['role'] for event in case['events']], ['writer'])

    def test_sql_ddl_and_frozen_artifact_changes_after_writer_are_refused(self):
        for mutation in ('sql', 'ddl', 'facts', 'inventory', 'plan'):
            with self.subTest(mutation=mutation):
                self.assert_refused(self.case(mutation))

    def test_changes_after_validator_are_refused(self):
        for mutation in ('runtime', 'uuid', 'sql', 'ddl', 'facts'):
            with self.subTest(mutation=mutation):
                self.assert_refused(self.case(mutation, 'validator'))

    def test_changed_inputs_between_preparation_and_writer_never_reach_writer(self):
        for boundary in ('during_build', 'after_build', 'before_writer'):
            for mutation in ('runtime', 'sql', 'ddl', 'facts'):
                if boundary == 'during_build' and mutation == 'facts':
                    continue  # Frozen facts do not exist before analysis.
                with self.subTest(boundary=boundary, mutation=mutation):
                    case = self.case(mutation, boundary)
                    self.assert_refused(case)
                    self.assertEqual(case['events'], [])

    def test_reseal_checks_inputs_before_and_after_gate(self):
        for boundary in ('before_seal', 'after_gate'):
            for mutation in ('runtime', 'sql', 'ddl', 'facts'):
                with self.subTest(boundary=boundary, mutation=mutation):
                    self.assert_refused(self.case(mutation, boundary))

    def test_repair_keeps_original_binding_and_can_become_ready(self):
        case = self.case(repairs=1)
        self.assertEqual(case['result']['code'], 0, case['stderr'])
        self.assertEqual(case['log']['repair_rounds'], 1)
        self.assertNotEqual(case['log']['initial_decision'], 'ready')
        self.assertEqual(case['decision']['decision'], 'ready')
        self.assertEqual(len(case['events']), 4)
        self.assertEqual({event['run_id'] for event in case['events']}, {case['binding']['manifest']['run_id']})

    def test_repair_seats_cannot_replace_pinned_inputs(self):
        for boundary in ('writer', 'validator'):
            for mutation in ('runtime', 'uuid', 'sql', 'ddl', 'facts'):
                with self.subTest(boundary=boundary, mutation=mutation):
                    case = self.case(mutation, boundary, attempt=1, repairs=1)
                    self.assert_refused(case)
                    self.assertTrue(case['failure'])
                    if mutation in ('uuid', 'facts'):
                        # A failed repair can roll run-owned files back to the
                        # previously refused bundle, never turn that into ready.
                        self.assertEqual(case['log']['gate']['decision'], case['log']['initial_decision'])
                        for reference in case['binding']['manifest']['artifacts'].values():
                            path = case['work'] / 'output/0' / reference['path']
                            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), reference['sha256'])
                        snapshot = json.loads((case['work'] / 'before-repair.json').read_text(encoding='utf-8'))
                        for name, digest in snapshot.items():
                            path = case['work'] / 'output/0' / name
                            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, name)
                    else:
                        self.assertEqual(case['log']['gate']['decision'], 'blocked')
                        self.assertIsNone(case['decision'])


if __name__ == '__main__':
    unittest.main()
