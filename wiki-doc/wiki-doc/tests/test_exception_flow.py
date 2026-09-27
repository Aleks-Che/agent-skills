"""Conditional EXCEPTION flow, nested dispatch, and independent gate checks."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
from sql_extract import extract_inventory
from build_bundle import build, finish, render
from artifact_schema import read_json
from wiki_store import atomic_json

SQL = """CREATE FUNCTION demo.f(p integer) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 UPDATE demo.t SET id = p;
 BEGIN
  INSERT INTO demo.t(id) VALUES(p);
 EXCEPTION WHEN unique_violation OR SQLSTATE '22012' THEN
  BEGIN
   UPDATE demo.t SET id = 2;
  EXCEPTION WHEN OTHERS THEN RAISE;
  END;
 WHEN OTHERS THEN NULL;
 END;
 DELETE FROM demo.t WHERE id = p;
EXCEPTION WHEN division_by_zero THEN RAISE NOTICE 'division';
WHEN OTHERS THEN RAISE;
END;
$$;
"""
SUBJECT = 'function+demo+f+(integer)'


def inventory(sql=SQL):
    return extract_inventory(sql, 'source.sql', hashlib.sha256(sql.encode()).hexdigest(),
                             dialect='postgres', version='15', documented_subjects=[SUBJECT])


class ExceptionFlowTests(unittest.TestCase):
    def test_nested_regions_route_handler_errors_past_the_current_list(self):
        inv = inventory()
        self.assertEqual(inv['coverage_notes'], [])
        regions = [i for i in inv['items'] if i['kind'] == 'EXCEPTION_BLOCK']
        self.assertEqual(len(regions), 3)
        outer, inner, inside_handler = regions
        self.assertIsNone(outer['details']['exception_flow']['parent'])
        self.assertEqual(inner['details']['exception_flow']['parent'], outer['anchor'])
        self.assertEqual(inside_handler['details']['exception_flow']['parent'], outer['anchor'])
        self.assertNotEqual(inside_handler['details']['exception_flow']['parent'], inner['anchor'])

    def test_handler_order_alternatives_empty_handler_and_body_are_preserved(self):
        inv = inventory()
        regions = [i for i in inv['items'] if i['kind'] == 'EXCEPTION_BLOCK']
        flow = regions[1]['details']['exception_flow']
        self.assertEqual([h['conditions'] for h in flow['handlers']],
                         [['unique_violation', '22012'], ['others']])
        self.assertEqual([h['ordinal'] for h in flow['handlers']], [1, 2])
        self.assertEqual(flow['handlers'][1]['body'], [])
        self.assertEqual([a['construct'] for a in flow['body']], ['INSERT'])
        self.assertEqual(flow['others_excludes'], ['query_canceled', 'assert_failure'])
        self.assertEqual(flow['rollback'], 'transactional_database_changes_within_protected_block')
        self.assertEqual(flow['variables'], 'values_at_failure_are_retained')
        self.assertEqual(flow['external_effects'], 'not_assumed_transactional')
        self.assertIn('before_protected_body', flow['initialization_error'])
        self.assertIn('runtime_dependent', flow['failure_point'])

    def test_unknown_handler_body_still_blocks(self):
        sql = SQL.replace("RAISE NOTICE 'division';", 'LOOP EXIT; END LOOP;')
        self.assertTrue(any('Unsupported PL/pgSQL AST node' in n['reason']
                            for n in inventory(sql)['coverage_notes']))

    def test_initializers_are_executable_and_outside_their_own_protection(self):
        source = SQL.replace('BEGIN\n UPDATE', 'DECLARE x integer := demo.init(p);\nBEGIN\n UPDATE', 1)
        source = source.replace(' BEGIN\n  INSERT', ' DECLARE y integer := demo.nested(x);\n BEGIN\n  INSERT', 1)
        inv = inventory(source)
        self.assertEqual(inv['coverage_notes'], [])
        initializers = [i for i in inv['items'] if i['details'].get('initializer')]
        self.assertEqual([i['details']['assignment_target'] for i in initializers], ['x', 'y'])
        self.assertEqual([i['calls'] for i in initializers], [['demo.init'], ['demo.nested']])
        outer, inner, _ = [i['details']['exception_flow'] for i in inv['items']
                            if i['kind'] == 'EXCEPTION_BLOCK']
        self.assertEqual(outer['initializers'], [initializers[0]['anchor']])
        self.assertEqual(inner['initializers'], [initializers[1]['anchor']])
        self.assertNotIn(initializers[0]['anchor'], outer['body'])
        self.assertIn(initializers[1]['anchor'], outer['body'])
        self.assertNotIn(initializers[1]['anchor'], inner['body'])

    def test_ambiguous_same_line_initializer_ownership_stays_blocking(self):
        source = "CREATE FUNCTION demo.f(p integer) RETURNS void LANGUAGE plpgsql AS $$ DECLARE x integer := demo.init(); BEGIN DECLARE y integer := demo.nested(); BEGIN NULL; END; END; $$;"
        self.assertTrue(any('initializer block ownership' in n['reason']
                            for n in inventory(source)['coverage_notes']))

    def test_initializer_after_parent_begin_on_same_line_is_not_bound_to_parent(self):
        source = SQL.replace('BEGIN\n UPDATE demo.t SET id = p;\n BEGIN',
                             'BEGIN DECLARE x integer := demo.init(p);\n BEGIN', 1)
        inv = inventory(source)
        self.assertTrue(any('initializer block ownership' in n['reason']
                            for n in inv['coverage_notes']))
        outer = next(i for i in inv['items'] if i['kind'] == 'EXCEPTION_BLOCK')
        self.assertEqual(outer['details']['exception_flow']['initializers'], [])

    def test_full_gate_rejects_reordered_visible_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, ddl, run = root / 'source.sql', root / 'context.sql', root / 'run'
            source.write_text(SQL, encoding='utf-8')
            ddl.write_text('CREATE TABLE demo.t(id integer);', encoding='utf-8')
            self.assertEqual(build(source, run, project_root=root, subject=SUBJECT,
                                   context=[ddl], version='15')['decision'], 'ready')
            facts = read_json(run / 'facts.json')
            updates = [o for o in facts['operations'] if o['kind'] == 'UPDATE']
            updates[0]['order'], updates[1]['order'] = updates[1]['order'], updates[0]['order']
            atomic_json(run / 'facts.json', facts)
            page, coverage = render(facts, read_json(run / 'validation_plan.json'))
            (run / 'page.draft.md').write_text(page, encoding='utf-8')
            atomic_json(run / 'coverage.json', coverage)
            checked = finish(run, sql_files=[source], context=[ddl], project_root=root)
            self.assertFalse(checked['publication_authorized'], checked)
            self.assertTrue(any('order differs' in str(e) for e in checked['errors']), checked)

    def test_full_gate_accepts_model_and_rejects_coherent_flow_forgery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, ddl, run = root / 'source.sql', root / 'context.sql', root / 'run'
            source.write_text(SQL, encoding='utf-8')
            ddl.write_text('CREATE TABLE demo.t(id integer);', encoding='utf-8')
            result = build(source, run, project_root=root, subject=SUBJECT, context=[ddl], version='15')
            self.assertEqual(result['decision'], 'ready', result)
            facts = read_json(run / 'facts.json')
            plan = read_json(run / 'validation_plan.json')
            self.assertTrue(any(c.get('inventory_anchor', {}).get('construct') == 'EXCEPTION_BLOCK'
                                and c['blocking'] for c in plan['required_checks']))
            base_inventory = read_json(run / 'inventory.json')
            for mutation in ('drop_body', 'reverse_handlers', 'wrong_parent'):
                with self.subTest(mutation=mutation):
                    changed = copy.deepcopy(facts)
                    inv = copy.deepcopy(base_inventory)
                    op = next(o for o in changed['operations'] if o['kind'] == 'EXCEPTION_BLOCK')
                    flow = op['structure']['exception_flow']
                    if mutation == 'drop_body':
                        flow['body'] = []
                    elif mutation == 'reverse_handlers':
                        flow['handlers'].reverse()
                    else:
                        flow['parent'] = copy.deepcopy(flow['body'][0])
                    next(i for i in inv['items'] if i['kind'] == 'EXCEPTION_BLOCK')['details']['exception_flow'] = copy.deepcopy(flow)
                    atomic_json(run / 'facts.json', changed)
                    atomic_json(run / 'inventory.json', inv)
                    page, coverage = render(changed, plan)
                    (run / 'page.draft.md').write_text(page, encoding='utf-8')
                    atomic_json(run / 'coverage.json', coverage)
                    checked = finish(run, sql_files=[source], context=[ddl], project_root=root)
                    self.assertFalse(checked['publication_authorized'], checked)
                    self.assertNotEqual(checked['decision'], 'ready', checked)


if __name__ == '__main__':
    unittest.main()
