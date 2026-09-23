"""P2-03: source -> facts -> rendered claims -> gate, including coherent omissions."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from artifact_schema import read_json, load_schemas, validate_schema
from build_bundle import build, finish, render
from ddl import catalog
from regression_mutations import run_mutations
from run_regression import check_run
from sql_extract import extract_inventory
from validation_gate import _fact_checks
from validation_plan import generate_plan
from check_policy import load_policy
from wiki_store import atomic_json, atomic_bytes

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES = PACKAGE / 'examples'
SUBJECT = 'table+demo+accounts'
SOURCE = EXAMPLES / '12_triggers_indexes.sql'


class ExtensionInventoryTests(unittest.TestCase):
    def inventory(self, sql, subject=None):
        return extract_inventory(sql, 'input.sql', hashlib.sha256(sql.encode()).hexdigest(),
                                 version='15', documented_subjects=[subject] if subject else None)

    def test_inline_constraints_each_have_an_obligation(self):
        inv = self.inventory('CREATE TABLE demo.t (id int PRIMARY KEY, code text UNIQUE, '
                             'owner int REFERENCES demo.owner(id), x int CHECK (x > 0));')
        create = next(i for i in inv['items'] if i['kind'] == 'CREATE')
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual({c['type'] for c in create['details']['constraints']},
                         {'PRIMARY_KEY', 'UNIQUE', 'FOREIGN_KEY', 'CHECK'})
        checks = generate_plan(inv, load_policy())['required_checks']
        self.assertEqual(sum(c['rule_id'] == 'constraint' for c in checks), 4)

    def test_partial_expression_index_keeps_ast_details_inside_routine(self):
        inv = self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE SQL AS $$ '
                             'CREATE UNIQUE INDEX idx ON demo.t (lower(email)) WHERE active; $$;')
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        index = next(i for i in inv['items'] if i['kind'] == 'INDEX')['details']
        self.assertEqual(index['columns'], ['lower(email)'])
        self.assertEqual(index['where'], 'active')
        self.assertEqual(index['extension_version'], 1)

    def test_public_and_grant_option_are_not_roles(self):
        inv = self.inventory('CREATE TABLE demo.t (id int); '
                             'GRANT SELECT (id) ON demo.t TO PUBLIC WITH GRANT OPTION;')
        grant = next(i for i in inv['items'] if i['kind'] == 'GRANT')['details']
        self.assertEqual(grant['grantees'], ['PUBLIC'])
        self.assertTrue(grant['grant_option'])
        self.assertEqual(grant['object_type'], 'TABLE')
        self.assertEqual(grant['privilege_columns'], [{'privilege': 'select', 'columns': ['id']}])

    def test_trigger_when_and_update_columns_survive(self):
        inv = self.inventory('CREATE TABLE demo.t (id int); CREATE TRIGGER audit BEFORE UPDATE OF id '
                             'ON demo.t FOR EACH ROW WHEN (NEW.id > 0) EXECUTE FUNCTION demo.audit();')
        trigger = next(i for i in inv['items'] if i['kind'] == 'TRIGGER')['details']
        self.assertEqual(trigger['update_columns'], ['id'])
        self.assertEqual(trigger['when'], 'new.id > 0')

    def test_grant_all_on_columns_keeps_a_named_privilege(self):
        inv = self.inventory('CREATE TABLE demo.t (id int); GRANT ALL (id) ON demo.t TO reader;')
        grant = next(i for i in inv['items'] if i['kind'] == 'GRANT')['details']
        self.assertEqual(grant['privileges'], ['ALL'])
        self.assertEqual(grant['privilege_columns'], [{'privilege': 'ALL', 'columns': ['id']}])

    def test_using_index_does_not_invent_primary_key_column_properties(self):
        sql = ('CREATE TABLE demo.t (id int); CREATE UNIQUE INDEX idx ON demo.t (id); '
               'ALTER TABLE demo.t ADD CONSTRAINT pk PRIMARY KEY USING INDEX idx;')
        inv = self.inventory(sql)
        self.assertTrue(any('USING INDEX' in n['reason'] for n in inv['coverage_notes']))
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'input.sql'
            source.write_text(sql, encoding='utf-8')
            self.assertTrue(any('USING INDEX' in e for e in catalog([source], source.parent)['errors']))

    def test_selected_function_does_not_inherit_other_objects_extensions(self):
        inv = self.inventory(SOURCE.read_text(encoding='utf-8'), 'function+demo+audit_account+()')
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        self.assertFalse({'TRIGGER', 'INDEX', 'ALTER', 'GRANT', 'REVOKE'} & {i['kind'] for i in inv['items']})

    def test_view_owns_trigger_and_grant(self):
        inv = self.inventory('CREATE VIEW demo.v AS SELECT 1 AS id; '
                             'CREATE TRIGGER audit INSTEAD OF INSERT ON demo.v FOR EACH ROW '
                             'EXECUTE FUNCTION demo.audit(); GRANT SELECT ON demo.v TO reader;', 'view+demo+v')
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual({i['anchor']['object_or_scope'] for i in inv['items']}, {'view+demo+v'})
        self.assertTrue({'TRIGGER', 'GRANT'} <= {i['kind'] for i in inv['items']})

    def test_unsupported_access_targets_remain_analysis_gaps(self):
        for clause in ('GRANT USAGE ON SCHEMA demo TO reader;',
                       'GRANT SELECT ON ALL TABLES IN SCHEMA demo TO reader;',
                       'GRANT EXECUTE ON FUNCTION demo.f() TO reader;'):
            with self.subTest(sql=clause):
                inv = self.inventory('CREATE TABLE demo.t (id int); ' + clause)
                self.assertTrue(inv['coverage_notes'])

    def test_constraint_order_does_not_cross_files_without_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create, alter = root / 'create.sql', root / 'alter.sql'
            create.write_text('CREATE TABLE demo.t (id int);', encoding='utf-8')
            alter.write_text('ALTER TABLE demo.t ADD CONSTRAINT pk PRIMARY KEY (id);', encoding='utf-8')
            self.assertTrue(catalog([create, alter], root)['errors'])
            create.write_text(create.read_text() + alter.read_text(), encoding='utf-8')
            result = catalog([create], root)
            self.assertFalse(result['errors'])
            self.assertTrue(result['tables']['demo.t']['columns'][0]['primary_key'])
            self.assertTrue(result['tables']['demo.t']['columns'][0]['not_null'])


class ExtensionGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / 'run'
        result = build(SOURCE, self.run, project_root=EXAMPLES, subject=SUBJECT)
        self.assertTrue(result['publication_authorized'], result)
        self.facts = read_json(self.run / 'facts.json')
        self.plan = read_json(self.run / 'validation_plan.json')

    def reissue(self, facts):
        atomic_json(self.run / 'facts.json', facts)
        page, coverage = render(facts, self.plan)
        atomic_bytes(self.run / 'page.draft.md', page.encode('utf-8'))
        atomic_json(self.run / 'coverage.json', coverage)
        return finish(self.run, sql_files=[SOURCE], context=[], project_root=EXAMPLES)

    def test_example_passes_independent_oracle_and_gate(self):
        result = check_run(self.run, EXAMPLES, EXAMPLES / 'expected/12', SUBJECT)
        self.assertTrue(result['valid'], result)

    def test_annotated_text_and_coherent_mutations_are_refused(self):
        report = self.root / 'regression-report.json'
        atomic_json(report, {'results': [dict(repeat=1, case='12', subject=SUBJECT,
                    run_dir=str(self.run), project_root=str(EXAMPLES), profile=None)]})
        result = run_mutations(report, EXAMPLES / 'expected', self.root / 'mutations')
        self.assertEqual(result['mutations'], 16)
        self.assertEqual(result['false_ready'], 0, result)
        for row in result['results']:
            self.assertTrue(row['oracle_errors'], row)
            self.assertTrue(any('structure' in e or 'Page claim' in e for e in row['errors']), row)

    def test_coherent_removal_of_each_new_operation_is_refused(self):
        for kind in ('TRIGGER', 'INDEX', 'GRANT', 'REVOKE'):
            with self.subTest(kind=kind):
                facts = copy.deepcopy(self.facts)
                facts['operations'] = [o for o in facts['operations'] if o['kind'] != kind]
                result = self.reissue(facts)
                self.assertFalse(result['publication_authorized'], result)
                self.assertTrue(any('missing from facts' in e for e in result['errors']), result)

    def test_coherent_constraint_removal_is_refused(self):
        for kind in ('CREATE', 'ALTER'):
            with self.subTest(kind=kind):
                facts = copy.deepcopy(self.facts)
                op = next(o for o in facts['operations'] if o['kind'] == kind)
                op['structure'].pop('constraints')
                result = self.reissue(facts)
                self.assertFalse(result['publication_authorized'], result)
                self.assertTrue(any('structure differs' in e for e in result['errors']), result)

    def test_extension_results_must_link_the_matching_operation(self):
        inventory = read_json(self.run / 'inventory.json')
        original = read_json(self.run / 'validation.json')
        for rule in ('trigger', 'index', 'constraint', 'access_rule'):
            with self.subTest(rule=rule):
                report = copy.deepcopy(original)
                check_id = next(c['id'] for c in self.plan['required_checks'] if c['rule_id'] == rule)
                next(c for c in report['checks'] if c['plan_check_id'] == check_id)['fact_ids'] = ['obj_1']
                errors = _fact_checks({'facts': self.facts, 'validation': report}, inventory,
                                      self.plan['required_checks'], '')
                self.assertTrue(any('matching ' + rule + ' fact' in e for e in errors), errors)

    def test_extension_structure_requires_supported_version(self):
        schema = load_schemas()['facts']
        for kind in ('TRIGGER', 'INDEX', 'GRANT', 'REVOKE'):
            for version in (None, 99):
                with self.subTest(kind=kind, version=version):
                    facts = copy.deepcopy(self.facts)
                    structure = next(o for o in facts['operations'] if o['kind'] == kind)['structure']
                    if version is None:
                        structure.pop('extension_version')
                    else:
                        structure['extension_version'] = version
                    self.assertTrue(validate_schema(facts, schema, 'facts'))


if __name__ == '__main__':
    unittest.main()
