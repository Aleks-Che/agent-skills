"""Set-operation semantics must survive projection expansion and the full gate."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
from build_bundle import build, finish, render
from pglast import parse_sql
from sql_extract import extract_inventory
from sql_types import column_catalog, expand_star_outputs, expand_wildcard_outputs


def inventory(query):
    return extract_inventory('CREATE VIEW demo.v AS ' + query, 'source.sql', 'a'*64)


class SetOperationTests(unittest.TestCase):
    TABLES = {'demo.a': {'columns': [{'name': 'id', 'type': 'integer'}]},
              'demo.b': {'columns': [{'name': 'other', 'type': 'numeric'}]}}

    def test_all_operators_and_all_flags_are_preserved(self):
        for operator in ('UNION', 'INTERSECT', 'EXCEPT'):
            for all_ in (False, True):
                with self.subTest(operator=operator, all_=all_):
                    inv = inventory(f'SELECT id FROM demo.a {operator} '
                                    + ('ALL ' if all_ else '') + 'SELECT other FROM demo.b')
                    parents = [i for i in inv['items'] if 'set_operation' in i['details']]
                    self.assertEqual(len(parents), 1)
                    self.assertEqual(parents[0]['details']['set_operation']['operator'], operator)
                    self.assertEqual(parents[0]['details']['set_operation']['all'], all_)

    def test_operand_source_order_and_parenthesized_tree(self):
        for query in ('SELECT 1 AS x UNION ALL SELECT 2 AS x EXCEPT SELECT 3 AS x',
                      'SELECT 1 AS x UNION ALL (SELECT 2 AS x EXCEPT SELECT 3 AS x)'):
            with self.subTest(query=query):
                inv = inventory(query)
                leaves = [i for i in inv['items'] if i['kind'] == 'SELECT'
                          and not i['details'].get('set_operation')]
                self.assertEqual([i['details']['columns'][0]['expression'] for i in leaves], ['1','2','3'])
                parents = [i for i in inv['items'] if i['details'].get('set_operation')]
                self.assertEqual(len(parents), 2)
                outer = parents[0]['details']['set_operation']
                inner = parents[1]['anchor']
                self.assertEqual(outer['right' if '(' in query else 'left'], inner)

    def test_root_clauses_and_calls_are_retained(self):
        inv = inventory('SELECT 1 AS x UNION ALL SELECT 2 AS x ORDER BY x DESC '
                        'LIMIT demo.row_limit() OFFSET 1')
        parent = next((i for i in inv['items'] if 'set_operation' in i['details']), None)
        self.assertIsNotNone(parent)
        self.assertEqual(parent['details']['order_by'], ['x DESC'])
        self.assertEqual(parent['details']['limit'], 'demo.row_limit()')
        self.assertEqual(parent['details']['offset'], '1')
        self.assertIn('demo.row_limit', parent.get('calls', []))

    def test_unknown_call_in_union_limit_is_blocking(self):
        inv = inventory('SELECT 1 AS x UNION ALL SELECT 2 AS x LIMIT mystery_limit()')
        self.assertTrue(any('mystery_limit' in n['reason'] for n in inv['coverage_notes']))

    def test_nested_with_and_limits_are_not_skipped_on_larg_chain(self):
        inv = inventory('(WITH c AS (SELECT id FROM demo.a) '
                        'SELECT id FROM c UNION ALL SELECT id FROM c LIMIT 1) '
                        'UNION ALL SELECT other FROM demo.b')
        self.assertEqual([i['details']['name'] for i in inv['items'] if i['kind'] == 'CTE'], ['c'])
        self.assertTrue(any(i['details'].get('limit') == '1' for i in inv['items']))
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])

    def test_width_mismatch_and_unknown_right_width_stay_blocked(self):
        queries = ['WITH c AS (SELECT id FROM demo.a UNION ALL SELECT 1,2) SELECT * FROM c',
                   'WITH c AS (SELECT id FROM demo.a UNION ALL SELECT * FROM demo.missing) SELECT * FROM c',
                   'WITH c AS (SELECT * FROM demo.a UNION ALL SELECT * FROM demo.b) SELECT * FROM c']
        for query in queries:
            with self.subTest(query=query):
                tables = copy.deepcopy(self.TABLES)
                tables['demo.b']['columns'].append({'name':'extra','type':'integer'})
                inv = inventory(query)
                expand_wildcard_outputs(inv, tables)
                self.assertTrue(inv['coverage_notes'])
                self.assertIsNone(expand_star_outputs(parse_sql(query)[0].stmt, tables))

    def test_cte_names_and_positional_mapping_through_set_operation(self):
        query = 'WITH c(renamed) AS (SELECT * FROM demo.a UNION ALL SELECT * FROM demo.b) SELECT * FROM c'
        inv = inventory(query)
        self.assertFalse(expand_wildcard_outputs(inv, copy.deepcopy(self.TABLES)))
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        self.assertEqual([(c['name'], c['expression']) for c in inv['items'][0]['details']['output_columns']],
                         [('renamed','c.renamed')])
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES),
                         [('renamed','c.renamed')])

    def test_direct_set_result_is_not_left_branch_expression_or_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source.sql'
            source.write_text('CREATE VIEW demo.v AS SELECT 1::integer AS x '
                              'UNION ALL SELECT 2.5::numeric AS y', encoding='utf8')
            inv = extract_inventory(source.read_text(), 'source.sql', 'a'*64)
            cat = column_catalog(inv, [source], [], root)
            self.assertEqual(cat['tables']['demo.v']['columns'][0]['name'], 'x')
            self.assertIsNone(cat['tables']['demo.v']['columns'][0]['type'])
            self.assertIsNone(cat['mappings'][0]['expression'])

    def test_direct_set_view_declared_names_apply_after_expansion(self):
        inv = extract_inventory('CREATE VIEW demo.v(renamed) AS '
                                'SELECT * FROM demo.a UNION ALL SELECT * FROM demo.b',
                                'source.sql', 'a'*64)
        self.assertFalse(expand_wildcard_outputs(inv, copy.deepcopy(self.TABLES)))
        self.assertEqual([c['name'] for c in inv['items'][0]['details']['output_columns']], ['renamed'])

    def test_insert_mapping_records_set_result_as_unknown_expression(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root/'source.sql', root/'context.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN '
                              'INSERT INTO demo.out SELECT 1::int AS x UNION ALL SELECT 2.5::numeric; '
                              'END $$;', encoding='utf8')
            context.write_text('CREATE TABLE demo.out(value numeric);', encoding='utf8')
            inv = extract_inventory(source.read_text(), 'source.sql', 'a'*64)
            cat = column_catalog(inv, [source], [context], root)
            self.assertEqual([(m['name'], m['expression'], m['type_expression']) for m in cat['mappings']],
                             [('value', None, None)])

    def test_perform_set_operation_and_all_operands_keep_branch_guards(self):
        inv = extract_inventory('CREATE FUNCTION demo.f(p boolean) RETURNS void LANGUAGE plpgsql AS $$ '
                                'BEGIN IF p THEN PERFORM 1 UNION ALL SELECT 2; END IF; END $$;',
                                'source.sql', 'a'*64)
        parent = next(i for i in inv['items'] if i['details'].get('set_operation'))
        self.assertEqual(parent['kind'], 'PERFORM')
        self.assertEqual(parent['details']['guards'], ['p'])
        self.assertTrue(all(i['details']['guards'] == ['p'] for i in inv['items'] if i['kind'] == 'SELECT'))

    def test_values_operand_gap_survives_repeated_enrichment(self):
        inv = inventory('SELECT 1 AS x UNION ALL VALUES (2)')
        for _ in range(2):
            expand_wildcard_outputs(inv, {})
            self.assertTrue(any('Set operation output widths' in n['reason'] for n in inv['coverage_notes']))


class SetOperationGateTests(unittest.TestCase):
    def test_unestablished_output_name_requires_alias_instead_of_invention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source.sql'
            source.write_text('CREATE VIEW demo.v AS SELECT count(*) UNION ALL SELECT count(*)', encoding='utf8')
            result = build(source, root/'run', project_root=root, subject='demo.v')
            self.assertFalse(result['publication_authorized'], result)
            inv = json.loads((root/'run/inventory.json').read_text(encoding='utf8'))
            self.assertTrue(any('output names require explicit aliases' in n['reason'] for n in inv['coverage_notes']))

    def test_full_gate_blocks_invalid_or_unanalysed_union(self):
        for query in ('SELECT 1 AS x UNION ALL SELECT 2,3',
                      'SELECT 1 AS x UNION ALL SELECT 2 AS x LIMIT mystery_limit()'):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root/'source.sql'
                source.write_text('CREATE VIEW demo.v AS ' + query, encoding='utf8')
                result = build(source, root/'run', project_root=root, subject='demo.v')
                self.assertFalse(result['publication_authorized'], result)

    def test_full_gate_checks_set_semantics_with_fresh_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, run = root/'source.sql', root/'run'
            source.write_text('CREATE VIEW demo.v AS SELECT 1::int AS x UNION ALL '
                              'SELECT 2.5::numeric AS y ORDER BY x DESC LIMIT 2', encoding='utf8')
            result = build(source, run, project_root=root, subject='demo.v')
            self.assertTrue(result['publication_authorized'], result)
            facts = json.loads((run/'facts.json').read_text(encoding='utf8'))
            op = next((o for o in facts['operations'] if 'set_operation' in o.get('structure',{})), None)
            self.assertIsNotNone(op)
            plan = json.loads((run/'validation_plan.json').read_text(encoding='utf8'))
            original = copy.deepcopy(facts)
            for field, value in [('operator','EXCEPT'), ('all', False), ('limit','1')]:
                with self.subTest(field=field):
                    facts = copy.deepcopy(original)
                    op = next(o for o in facts['operations'] if 'set_operation' in o.get('structure',{}))
                    if field == 'limit': op['structure'][field] = value
                    else: op['structure']['set_operation'][field] = value
                    (run/'facts.json').write_text(json.dumps(facts), encoding='utf8')
                    page, coverage = render(facts, plan)
                    (run/'page.draft.md').write_text(page, encoding='utf8')
                    (run/'coverage.json').write_text(json.dumps(coverage), encoding='utf8')
                    result = finish(run, sql_files=[source], context=[], project_root=root)
                    self.assertFalse(result['publication_authorized'], result)
                    self.assertTrue(any('structure differs' in e for e in result['errors']), result)
                    self.assertFalse(any('hash mismatch' in e.lower() for e in result['errors']), result)


if __name__ == '__main__':
    unittest.main()
