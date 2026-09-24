"""Independent regression cases for CTE/derived wildcard projection evidence."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from build_bundle import build
from pglast import parse_sql
from sql_extract import extract_inventory
from sql_types import column_catalog, expand_star_outputs, expand_wildcard_outputs


class ScopedWildcardTests(unittest.TestCase):
    TABLES = {'demo.t': {'columns': [{'name': 'id', 'type': 'integer'},
                                     {'name': 'x', 'type': 'text'}]}}

    def projection(self, query, tables=None):
        inventory = extract_inventory('CREATE VIEW demo.v AS ' + query,
                                      'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inventory, copy.deepcopy(
            self.TABLES if tables is None else tables))
        return inventory, unresolved, inventory['items'][0]['details']['output_columns']

    def assert_projection(self, query, expected):
        inventory, unresolved, outputs = self.projection(query)
        self.assertEqual(unresolved, [])
        self.assertFalse(inventory['coverage_notes'], inventory['coverage_notes'])
        self.assertEqual([(c['name'], c['expression']) for c in outputs], expected)
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES), expected)

    def test_partial_cte_alias_list_preserves_remaining_columns(self):
        for body in ('SELECT id, x FROM demo.t', 'SELECT * FROM demo.t'):
            with self.subTest(body=body):
                self.assert_projection(f'WITH c(a) AS ({body}) SELECT * FROM c',
                                       [('a', 'c.a'), ('x', 'c.x')])

    def test_alias_names_cannot_establish_unknown_cte_width(self):
        query = 'WITH c(a) AS (SELECT * FROM demo.missing) SELECT * FROM c'
        inventory, unresolved, outputs = self.projection(query)
        self.assertTrue(unresolved)
        self.assertEqual(outputs[0]['expression'], '*')
        self.assertTrue(inventory['coverage_notes'])
        self.assertIsNone(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES))

    def test_excess_cte_aliases_do_not_invent_columns(self):
        query = 'WITH c(a,b,c) AS (SELECT id,x FROM demo.t) SELECT * FROM c'
        inventory, unresolved, outputs = self.projection(query)
        self.assertTrue(unresolved)
        self.assertEqual(outputs[0]['expression'], '*')
        self.assertTrue(inventory['coverage_notes'])
        self.assertIsNone(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES))

    def test_aliases_name_otherwise_unnamed_expressions(self):
        self.assert_projection('WITH c(a,b) AS (SELECT 1,2) SELECT * FROM c',
                               [('a', 'c.a'), ('b', 'c.b')])

    def test_derived_alias_shadows_physical_catalog_entry(self):
        tables = {**self.TABLES, 'd': {'columns': [{'name': 'wrong', 'type': 'text'}]}}
        query = 'SELECT * FROM (SELECT id,x FROM demo.t) d'
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, tables),
                         [('id', 'd.id'), ('x', 'd.x')])
        query = 'SELECT * FROM (SELECT * FROM demo.missing) d'
        self.assertIsNone(expand_star_outputs(parse_sql(query)[0].stmt, tables))

    def test_nested_derived_wildcards_propagate_exact_columns(self):
        self.assert_projection('SELECT * FROM (SELECT * FROM (SELECT * FROM demo.t) a) b',
                               [('id', 'b.id'), ('x', 'b.x')])

    def test_derived_alias_does_not_shadow_cte_inside_subquery(self):
        self.assert_projection('WITH d AS (SELECT id,x FROM demo.t) '
                               'SELECT * FROM (SELECT * FROM d) d',
                               [('id', 'd.id'), ('x', 'd.x')])

    def test_qualified_relation_does_not_resolve_as_quoted_cte_name(self):
        self.assert_projection('WITH "demo.t" AS (SELECT 1 AS wrong) SELECT * FROM demo.t',
                               [('id', 'demo.t.id'), ('x', 'demo.t.x')])

    def test_deep_cte_chain_has_no_arbitrary_ten_pass_limit(self):
        ctes = ['c0 AS (SELECT * FROM demo.t)']
        ctes.extend(f'c{i} AS (SELECT * FROM c{i-1})' for i in range(1, 15))
        self.assert_projection('WITH ' + ', '.join(ctes) + ' SELECT * FROM c14',
                               [('id', 'c14.id'), ('x', 'c14.x')])

    def test_chained_shadowing_uses_previous_cte_output(self):
        self.assert_projection('WITH a AS (SELECT id,x FROM demo.t), '
                               'b(renamed) AS (SELECT * FROM a) SELECT b.* FROM b',
                               [('renamed', 'b.renamed'), ('x', 'b.x')])

    def test_cte_and_derived_alias_in_same_from_keep_distinct_sources(self):
        self.assert_projection('WITH d AS (SELECT id FROM demo.t) '
                               'SELECT c.*, d.* FROM d c CROSS JOIN (SELECT x FROM demo.t) d',
                               [('id', 'c.id'), ('x', 'd.x')])

    def test_positional_insert_mapping_through_derived_and_cte(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 'source.sql', root / 'context.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$'
                              'BEGIN INSERT INTO demo.out WITH c(a) AS (SELECT * FROM demo.t) '
                              'SELECT * FROM (SELECT * FROM c) d; END $$;', encoding='utf8')
            context.write_text('CREATE TABLE demo.t(id int,x text); '
                               'CREATE TABLE demo.out(first int,second text);', encoding='utf8')
            inventory = extract_inventory(source.read_text(encoding='utf8'), 'source.sql', 'a'*64)
            result = column_catalog(inventory, [source], [context], root)
            self.assertEqual([(m['name'], m['expression']) for m in result['mappings']],
                             [('first', 'd.a'), ('second', 'd.x')])
            self.assertFalse(inventory['coverage_notes'], inventory['coverage_notes'])

    def test_full_gate_preserves_partial_cte_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 'source.sql', root / 'context.sql'
            source.write_text('CREATE VIEW demo.v AS WITH c(a) AS '
                              '(SELECT id,x FROM demo.t) SELECT * FROM c;', encoding='utf8')
            context.write_text('CREATE TABLE demo.t(id int,x text);', encoding='utf8')
            decision = build(source, root / 'run', project_root=root,
                             subject='demo.v', context=[context])
            self.assertTrue(decision['publication_authorized'], decision)
            inventory = json.loads((root / 'run/inventory.json').read_text(encoding='utf8'))
            outputs = inventory['items'][0]['details']['output_columns']
            self.assertEqual([c['name'] for c in outputs], ['a', 'x'])

    def test_full_gate_keeps_unproven_cte_shapes_blocked(self):
        queries = ['WITH c(a) AS (SELECT * FROM demo.missing) SELECT * FROM c',
                   'WITH c(a,b,c) AS (SELECT id,x FROM demo.t) SELECT * FROM c',
                   'SELECT * FROM (SELECT * FROM demo.t JOIN demo.u USING (id)) d']
        for query in queries:
            with self.subTest(query=query), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, context = root / 'source.sql', root / 'context.sql'
                source.write_text('CREATE VIEW demo.v AS ' + query, encoding='utf8')
                context.write_text('CREATE TABLE demo.t(id int,x text); '
                                   'CREATE TABLE demo.u(id int,y text);', encoding='utf8')
                decision = build(source, root / 'run', project_root=root,
                                 subject='demo.v', context=[context])
                self.assertFalse(decision['publication_authorized'], decision)
                inventory = json.loads((root / 'run/inventory.json').read_text(encoding='utf8'))
                self.assertTrue(any('Wildcard' in n['reason'] for n in inventory['coverage_notes']))

    def test_reused_names_in_separate_statements_do_not_leak(self):
        inventory = extract_inventory(
            'CREATE FUNCTION demo.f() RETURNS void LANGUAGE sql AS $$ '
            'WITH c AS (SELECT id FROM demo.t) SELECT * FROM (SELECT * FROM c) d; '
            'WITH c AS (SELECT x FROM demo.t) SELECT * FROM (SELECT * FROM c) d; $$;',
            'source.sql', 'a' * 64)
        self.assertFalse(expand_wildcard_outputs(inventory, copy.deepcopy(self.TABLES)))
        outputs = [i['details']['columns'] for i in inventory['items']
                   if i['details'].get('derived_aliases') == ['d']]
        self.assertEqual([[(c['name'], c['expression']) for c in cols] for cols in outputs],
                         [[('id', 'd.id')], [('x', 'd.x')]])


if __name__ == '__main__':
    unittest.main()
