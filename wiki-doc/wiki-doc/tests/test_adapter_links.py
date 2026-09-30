"""Final-location source links through authoring, independent gate and publication."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
import agent_adapter
from artifact_schema import read_json
from lint import lint
from page_provenance import check_provenance
from publish import prepare, publish
from run_regression import check_run
from validation_gate import evaluate_bundle
from wiki_store import WikiConflict

SUBJECT = 'view+demo+v'
SQL_PATH = 'sources/contract v1#final.sql'
DDL_PATH = 'ddl/table schema.sql'

STUB = r'''
import json, sys
from pathlib import Path
settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
run = Path(settings['run'])
role = 'validator' if 'validator-агент' in sys.argv[-1] else 'writer'
if role == 'writer':
    path = run / 'page.draft.md'
    text = path.read_text(encoding='utf-8-sig')
    links = '\n\nИсходный код: [SQL](<{}>) и [DDL](<{}>).\n\n'.format(settings['sql_url'], settings['ddl_url'])
    marker = '<!-- wiki-doc:managed end -->'
    assert marker in text
    path.write_text(text.replace(marker, links + marker), encoding='utf-8')
else:
    path = run / 'validation.json'
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    for item in report['checks']:
        item['status'] = 'ok'
    report['checks'][0]['reason'] = 'Local validator checked the declared source links.'
    path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    (run / 'validation-review.md').write_text('Reviewed SQL and DDL links from the final page location.', encoding='utf-8')
'''


class AdapterLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.project = self.workspace / 'input'
        self.output = self.workspace / 'output'
        self.project.mkdir(parents=True)
        self.output.mkdir()
        self.sql = self.project / SQL_PATH
        self.ddl = self.project / DDL_PATH
        self.sql.parent.mkdir()
        self.ddl.parent.mkdir()
        self.sql.write_text('CREATE VIEW demo.v AS\nSELECT id\nFROM demo.t;\n', encoding='utf-8')
        self.ddl.write_text('CREATE TABLE demo.t(id bigint);\n', encoding='utf-8')
        # Existing files outside declared roots must still be rejected.
        (self.workspace / 'secret.sql').write_text('workspace private\n', encoding='utf-8')
        (self.root / 'outside.sql').write_text('outside declared roots\n', encoding='utf-8')
        self.run = self.output / '0'
        self.wiki = self.workspace / 'wiki' / '0'
        self.sql_url = quote(Path(os.path.relpath(self.sql, self.wiki)).as_posix(), safe='/')
        self.ddl_url = quote(Path(os.path.relpath(self.ddl, self.wiki)).as_posix(), safe='/')
        self.expected = self.root / 'expected'
        self.expected.mkdir()
        oracle = {
            'facts': {'subjects': {SUBJECT: {'declaration': {'kind': 'view', 'canonical_key': SUBJECT},
                                             'reads': ['demo.t']}}},
            'checks': {'required_rules': ['identity']},
            'decision': {'schema_version': 1, 'decision': 'ready'},
            'page_assertions': {'contract': 'claims-v1'},
        }
        for name, value in oracle.items():
            (self.expected / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def author(self, sql_url=None, ddl_url=None):
        request = dict(schema_version=1, project_root=str(self.project), output_dir=str(self.output),
                       skill_root=str(PACKAGE), sql=SQL_PATH, context=[DDL_PATH], subjects=[SUBJECT],
                       dialect='postgres', version='15')
        self.request = request
        request_path = self.workspace / 'request.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        settings = dict(run=str(self.run), sql_url=sql_url or self.sql_url + '#L1-L3',
                        ddl_url=ddl_url or self.ddl_url + '#L1')
        settings_path = self.workspace / 'stub-settings.json'
        settings_path.write_text(json.dumps(settings), encoding='utf-8')
        stub = self.workspace / 'stub.py'
        stub.write_text(STUB, encoding='utf-8')
        code = agent_adapter.main([str(request_path), '--model', 'local-stub', '--llm-timeout', '30',
            '--agent', json.dumps([sys.executable, '-B', str(stub), str(settings_path), '{message}'])])
        self.assertEqual(code, 0)
        return read_json(self.workspace / 'agent-logs/0-adapter.json')['gate']

    def independent(self):
        return check_run(self.run, self.project, self.expected, SUBJECT)

    def publication_seal(self):
        self.assertFalse(prepare(self.run, self.wiki)['draft_changed'])
        binding = read_json(self.workspace / 'agent-logs/0-binding.json')
        return agent_adapter.reseal(PACKAGE, self.request, self.run, {'profile': None, 'binding': binding})

    def test_source_links_survive_stub_check_publish_lint_and_provenance(self):
        gate = self.author()
        self.assertEqual(gate['decision'], 'ready', gate)
        self.assertTrue(gate['publication_authorized'])
        plan = read_json(self.run / 'validation_plan.json')
        page_id = plan['page_id']
        run_id = plan['run_id']
        for role in ('writer', 'validator'):
            prompt = (self.workspace / f'agent-logs/0-{role}-prompt.md').read_text(encoding='utf-8')
            self.assertIn(str(self.wiki).replace('\\', '/'), prompt.replace('\\', '/'))
            self.assertIn(self.sql_url, prompt)
        checked = self.independent()
        self.assertTrue(checked['valid'], checked)
        self.assertEqual(checked['decision'], 'ready')
        sealed = self.publication_seal()
        self.assertTrue(sealed['publication_authorized'], sealed)
        result = publish(self.run, self.wiki, project_root=self.project)
        self.assertTrue(result['published'], result)
        self.assertEqual(result['page_id'], page_id)
        content = (self.wiki / page_id).read_text(encoding='utf-8-sig')
        self.assertIn(self.sql_url + '#L1-L3', content)
        self.assertIn(self.ddl_url + '#L1', content)
        self.assertEqual((self.run / 'page.draft.md').read_bytes(), (self.wiki / page_id).read_bytes())
        inspected = lint(self.wiki, project_root=self.project)
        self.assertTrue(inspected['valid'], inspected)
        provenance = check_provenance(self.wiki, [page_id], project_root=self.project, expected_run_id=run_id)
        self.assertTrue(provenance['valid'], provenance)
        self.assertTrue(publish(self.run, self.wiki, project_root=self.project)['idempotent'])

    def assert_bad_link(self, href, message, *, ddl=False):
        gate = self.author(**({'ddl_url': href} if ddl else {'sql_url': href}))
        self.assertEqual(gate['decision'], 'revise', gate)
        self.assertFalse(gate['publication_authorized'])
        self.assertTrue(any('link:' in error and message in error for error in gate['errors']), gate)
        self.assertFalse(any('requires --wiki-root' in error for error in gate['errors']), gate)
        # All manifest hashes have been freshly sealed; this must be a link refusal.
        direct = evaluate_bundle(self.run, roots={'project': self.project, 'wiki': self.wiki})
        self.assertEqual(direct['decision'], 'revise', direct)
        self.assertFalse(direct.get('input_error'), direct)
        checked = self.independent()
        self.assertFalse(checked['valid'], checked)
        self.assertEqual(checked['decision'], 'revise', checked)
        self.assertTrue(any(message in error for error in checked['gate_errors']), checked)
        sealed = self.publication_seal()
        self.assertEqual(sealed['decision'], 'revise', sealed)
        with self.assertRaises(WikiConflict):
            publish(self.run, self.wiki, project_root=self.project)
        self.assertFalse((self.wiki / read_json(self.run / 'validation_plan.json')['page_id']).exists())

    def test_missing_sql_file_is_revise(self):
        self.assert_bad_link('../../input/sources/missing.sql', 'missing.sql')

    def test_workspace_sibling_is_not_an_implicitly_allowed_root(self):
        self.assert_bad_link('../../secret.sql', 'leaves declared roots')

    def test_traversal_outside_declared_roots_is_revise(self):
        self.assert_bad_link('../../../outside.sql', 'leaves declared roots')

    def test_encoded_traversal_is_not_allowed(self):
        self.assert_bad_link('%2E%2E/%2E%2E/%2E%2E/outside.sql', 'leaves declared roots')

    def test_invalid_source_line_anchor_is_revise(self):
        self.assert_bad_link(self.sql_url + '#L9999', 'invalid file fragment')

    def test_invalid_ddl_line_anchor_is_revise(self):
        self.assert_bad_link(self.ddl_url + '#L2', 'invalid file fragment', ddl=True)

    def test_unsupported_scheme_is_revise(self):
        # Unlike file:// (which CommonMark rejects as a link before this layer),
        # ftp is a parsed href and must reach the unchanged local link policy.
        self.assert_bad_link('ftp://example.invalid/source.sql', 'unsupported link scheme')

    def test_backslash_in_local_url_is_revise(self):
        self.assert_bad_link('../../input/sources%5Ccontract.sql', 'relative URL')


if __name__ == '__main__':
    unittest.main()
