"""P2-03 review: page boundaries, fail-closed inventory and final DDL state."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from artifact_schema import load_schemas, validate_schema
from build_bundle import build, finish, render
from ddl import catalog
from sql_extract import extract_inventory, _extract_inventory_legacy
from wiki_store import atomic_json, atomic_bytes


class ModelReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'input.sql'

    def inventory(self, text, subject=None):
        return extract_inventory(text, 'input.sql', hashlib.sha256(text.encode()).hexdigest(),
                                 documented_subjects=[subject] if subject else None)

    def build_sql(self, text, subject='table+demo+t'):
        self.source.write_text(text, encoding='utf-8')
        return build(self.source, self.root / 'run', project_root=self.root, subject=subject)

    def facts(self):
        return json.loads((self.root / 'run/facts.json').read_text(encoding='utf-8'))

    def mutate_and_finish(self, facts):
        run = self.root / 'run'
        plan = json.loads((run / 'validation_plan.json').read_text(encoding='utf-8'))
        page, coverage = render(facts, plan)
        atomic_json(run / 'facts.json', facts)
        atomic_json(run / 'coverage.json', coverage)
        atomic_bytes(run / 'page.draft.md', page.encode())
        return finish(run, sql_files=[self.source], context=[], project_root=self.root)

    def test_unhandled_top_level_statements_block_full_build(self):
        for tail in ('DO $$ BEGIN NULL; END $$;', 'SET search_path = demo;', 'SELECT 42;'):
            with self.subTest(tail=tail):
                result = self.build_sql('CREATE TABLE demo.t(id int);\n' + tail)
                self.assertFalse(result['publication_authorized'])
                inv = json.loads((self.root / 'run/inventory.json').read_text())
                self.assertTrue(inv['coverage_notes'])
                self.assertEqual(inv['coverage_notes'][0]['source_ref']['start_line'], 2)

    def test_ddl_for_sibling_does_not_leak_into_selected_page(self):
        text = ('CREATE TABLE demo.a(id int); CREATE TABLE demo.b(id int); '
                'CREATE INDEX bi ON demo.b(id); GRANT SELECT ON demo.b TO PUBLIC; '
                'CREATE TRIGGER bt AFTER INSERT ON demo.b EXECUTE FUNCTION demo.f();')
        inv = self.inventory(text, 'table+demo+a')
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual([i['kind'] for i in inv['items']], ['DECLARATION', 'CREATE'])
        inv = self.inventory(text, 'table+demo+b')
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual([i['kind'] for i in inv['items']], ['DECLARATION', 'CREATE', 'CREATE', 'GRANT', 'CREATE'])
        self.assertEqual({i['anchor']['object_or_scope'] for i in inv['items']}, {'table+demo+b'})

    def test_orphan_ddl_is_a_gap_and_never_inherits_last_scope(self):
        inv = self.inventory('CREATE TABLE demo.t(id int); CREATE INDEX xi ON demo.external(id);')
        self.assertTrue(inv['coverage_notes'])
        self.assertFalse(any(i.get('details', {}).get('structure', {}).get('index_name') for i in inv['items']))

    def test_shared_grant_is_present_in_each_target_scope(self):
        text = 'CREATE TABLE demo.a(id int); CREATE TABLE demo.b(id int); GRANT SELECT ON demo.a, demo.b TO PUBLIC;'
        inv = self.inventory(text)
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual({i['anchor']['object_or_scope'] for i in inv['items'] if i['kind'] == 'GRANT'},
                         {'table+demo+a', 'table+demo+b'})

    def test_regex_approximation_does_not_claim_structural_coverage(self):
        inv = _extract_inventory_legacy('CREATE TABLE demo.t(id int); GRANT SELECT ON demo.t TO PUBLIC;', 'input.sql', 'a'*64)
        self.assertTrue(inv['coverage_notes'])

    def test_column_grant_preserves_column_scope(self):
        result = self.build_sql('CREATE TABLE demo.t(id int, secret text); GRANT SELECT(id) ON demo.t TO PUBLIC;')
        self.assertEqual(result['decision'], 'ready', result.get('errors'))
        facts = self.facts()
        grant = next(o for o in facts['operations'] if o['kind'] == 'GRANT')
        self.assertEqual(grant['structure']['privilege_columns'], [{'privilege': 'select', 'columns': ['id']}])
        grant['structure']['privilege_columns'][0]['columns'] = ['secret']
        self.assertFalse(self.mutate_and_finish(facts)['publication_authorized'])

    def test_unmodeled_grant_targets_are_explicit_gaps(self):
        for text in ('GRANT USAGE ON SCHEMA demo TO PUBLIC;', 'GRANT SELECT ON ALL TABLES IN SCHEMA demo TO PUBLIC;'):
            with self.subTest(text=text):
                self.assertTrue(self.inventory(text)['coverage_notes'])

    def test_primary_unique_foreign_constraints_keep_keys(self):
        for clause, kind in (('PRIMARY KEY (id)', 'primary_key'), ('UNIQUE (id)', 'unique'),
                             ('FOREIGN KEY (id) REFERENCES demo.parent(id)', 'foreign_key')):
            with self.subTest(clause=clause):
                inv = self.inventory('ALTER TABLE demo.t ADD CONSTRAINT c ' + clause + ';')
                self.assertFalse(inv['coverage_notes'])
                constraint = next(i for i in inv['items'] if i['kind'] == 'ALTER')['details']['constraints'][0]
                self.assertEqual(constraint['type'], kind)
                self.assertEqual(constraint['columns'], ['id'])

    def test_alter_final_column_state_survives_build_and_gate(self):
        result = self.build_sql('CREATE TABLE demo.t(id int, payload text); '
                                'ALTER TABLE demo.t ADD CONSTRAINT pk PRIMARY KEY(id); '
                                'ALTER TABLE demo.t ALTER COLUMN payload SET NOT NULL; '
                                'ALTER TABLE demo.t ADD COLUMN extra bigint;')
        self.assertEqual(result['decision'], 'ready', result.get('errors'))
        facts = self.facts()
        columns = {c['name']: c for c in facts['columns']}
        self.assertEqual(set(columns), {'id', 'payload', 'extra'})
        self.assertTrue(columns['id']['primary_key'])
        self.assertFalse(columns['id']['nullable'])
        self.assertFalse(columns['payload']['nullable'])
        columns['payload']['nullable'] = True
        self.assertFalse(self.mutate_and_finish(facts)['publication_authorized'])

    def test_drop_uses_object_list_and_removes_final_table(self):
        self.source.write_text('CREATE TABLE demo.a(id int); CREATE TABLE demo.b(id int); DROP TABLE demo.a, demo.b;')
        result = catalog([self.source], self.root)
        self.assertFalse(result['errors'])
        self.assertEqual(result['tables'], {})
        inv = self.inventory(self.source.read_text(), 'table+demo+a')
        drop = next(i for i in inv['items'] if i['kind'] == 'DROP')
        self.assertEqual(drop['writes'], ['demo.a', 'demo.b'])

    def test_cross_file_final_conflict_requires_order(self):
        self.source.write_text('CREATE TABLE demo.t(id int);')
        other = self.root / 'other.sql'
        other.write_text('CREATE TABLE demo.t(id int); ALTER TABLE demo.t ADD COLUMN payload text;')
        for paths in ([self.source, other], [other, self.source]):
            with self.subTest(paths=paths):
                self.assertTrue(catalog(paths, self.root)['errors'])

    def test_duplicate_create_in_same_file_is_not_silently_overwritten(self):
        self.source.write_text('CREATE TABLE demo.t(id int); CREATE TABLE demo.t(payload text);')
        self.assertTrue(catalog([self.source], self.root)['errors'])

    def test_cross_file_drop_and_rename_cannot_hide_conflicting_state(self):
        self.source.write_text('CREATE TABLE demo.t(id int);')
        other = self.root / 'other.sql'
        for tail in ('DROP TABLE demo.t;', 'ALTER TABLE demo.t RENAME TO renamed;'):
            other.write_text('CREATE TABLE demo.t(id int); ' + tail)
            for paths in ([self.source, other], [other, self.source]):
                with self.subTest(tail=tail, paths=paths):
                    self.assertTrue(catalog(paths, self.root)['errors'])

    def test_constraint_schema_rejects_invalid_types(self):
        result = self.build_sql('CREATE TABLE demo.t(id int); CREATE INDEX ti ON demo.t(id);')
        self.assertEqual(result['decision'], 'ready', result.get('errors'))
        facts = self.facts()
        index = next(o for o in facts['operations'] if o.get('structure', {}).get('index_name'))
        for value in ('yes', 1, None):
            with self.subTest(value=value):
                index['structure']['unique'] = value
                self.assertTrue(validate_schema(facts, load_schemas()['facts'], 'facts'))

    def test_instead_of_trigger_on_view_has_correct_scope(self):
        inv = self.inventory('CREATE VIEW demo.v AS SELECT 1 AS id; '
                             'CREATE TRIGGER vt INSTEAD OF INSERT ON demo.v FOR EACH ROW EXECUTE FUNCTION demo.f();')
        self.assertFalse(inv['coverage_notes'])
        trigger = next(i for i in inv['items'] if i.get('details', {}).get('structure', {}).get('trigger_name'))
        self.assertEqual(trigger['anchor']['object_or_scope'], 'view+demo+v')
        self.assertEqual(trigger['details']['structure']['timing'], 'INSTEAD OF')


if __name__ == '__main__':
    unittest.main()
