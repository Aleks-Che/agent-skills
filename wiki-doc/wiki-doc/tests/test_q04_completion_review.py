"""Independent counterexamples for the Q-04 completion claims."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from build_bundle import build
from ddl import apply_statement, do_drop_columns
from pglast import parse_sql
from sql_extract import extract_inventory
from sql_types import expand_star_outputs, expand_wildcard_outputs, infer_expression
from test_q04_ddl_star import DO_DROP, do_node


class CatalogProofTests(unittest.TestCase):
    def test_query_variations_that_change_the_drop_set_are_rejected(self):
        original = DO_DROP.format(column='c1')
        mutations = {
            'limit': ('AND NOT a.attisdropped)', 'AND NOT a.attisdropped LIMIT 0)'),
            'offset': ('AND NOT a.attisdropped)', 'AND NOT a.attisdropped OFFSET 1)'),
            'sort_expression': ('AND NOT a.attisdropped)', 'AND NOT a.attisdropped ORDER BY side_effect())'),
            'attnum': ('a.attnum > 0', 'a.attnum > 999'),
            'null_guard': ('a.attnum > 0', 'a.attnum > NULL'),
            'negative_membership': ("a.attname = 'c1'", "a.attname NOT IN ('c1')"),
            'wrong_join': ('c.oid = a.attrelid', 'n.oid = a.attrelid'),
            'or_join': ('c.oid = a.attrelid', 'c.oid = a.attrelid OR true'),
            'filtered_join': ('c.oid = a.attrelid', 'c.oid = a.attrelid AND false'),
            'outer_join': ('JOIN pg_catalog.pg_class', 'LEFT JOIN pg_catalog.pg_class'),
            'renamed_projection': ('SELECT n.nspname', 'SELECT n.nspname AS relname'),
            'wrong_projection_owner': ('SELECT n.nspname', 'SELECT c.nspname'),
            'duplicate_filter': ("c.relname = 'tbl'", "c.relname = 'other' AND c.relname = 'tbl'"),
            'union': ('AND NOT a.attisdropped)', "AND NOT a.attisdropped UNION SELECT 'sch','tbl','keep')"),
        }
        for label, (old, new) in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                do_drop_columns(do_node(original.replace(old, new)))

    def test_early_return_and_declaration_effects_are_rejected(self):
        original = DO_DROP.format(column='c1')
        for text in (original.replace('BEGIN', 'BEGIN RETURN;'),
                     original.replace('t record;', "t record; x int := side_effect();")):
            with self.subTest(sql=text), self.assertRaises(ValueError):
                do_drop_columns(do_node(text))

    def test_unquoted_dynamic_identifiers_and_missing_separators_are_rejected(self):
        for text in (DO_DROP.format(column='MixedCase'), DO_DROP.format(column='two words'),
                     DO_DROP.format(column='c1').replace("'ALTER TABLE '", "'ALTER TABLE'")):
            with self.subTest(sql=text), self.assertRaises(ValueError):
                do_drop_columns(do_node(text))

    def test_dynamic_drop_cannot_bypass_distribution_column_guard(self):
        state = {'sch.tbl': {'columns': [{'name': 'c1', 'type': 'integer'}],
                             'distributed': {'mode': 'BY', 'columns': ['c1']}}}
        with self.assertRaisesRegex(ValueError, 'distribution'):
            apply_statement(state, do_node(DO_DROP.format(column='c1')))


class WildcardProofTests(unittest.TestCase):
    TABLES = {'demo.t': {'columns': [{'name': 'id', 'type': 'integer'},
                                     {'name': 'a', 'type': 'text'}]},
              'demo.u': {'columns': [{'name': 'id', 'type': 'integer'},
                                     {'name': 'b', 'type': 'text'}]}}

    def test_join_projection_variants_are_not_flattened_as_plain_from(self):
        queries = ['SELECT * FROM demo.t JOIN demo.u USING (id)',
                   'SELECT * FROM demo.t NATURAL JOIN demo.u',
                   'SELECT * FROM demo.t AS x(renamed_id, renamed_a)',
                   'SELECT * FROM (demo.t JOIN demo.u ON t.id=u.id) AS j',
                   'SELECT t.* FROM demo.t AS x']
        for query in queries:
            with self.subTest(query=query):
                inv = extract_inventory('CREATE VIEW demo.v AS ' + query + ';', 's.sql', 'a' * 64)
                self.assertTrue(expand_wildcard_outputs(inv, self.TABLES))
                self.assertTrue(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))
                self.assertIsNone(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES))

    def test_cte_shadow_does_not_use_physical_table_columns(self):
        query = 'WITH t AS (SELECT 1 AS different) SELECT * FROM t'
        tables = {'t': self.TABLES['demo.t']}
        # CTE t shadows physical table t: expansion uses the CTE column, not id/a.
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, tables),
                         [('different', 't.different')])

    def test_quoted_names_survive_expansion_and_type_inference(self):
        query = 'SELECT "X".* FROM demo.t AS "X"'
        tables = {'demo.t': {'columns': [{'name': 'Case ID', 'type': 'integer'}]}}
        inv = extract_inventory('CREATE VIEW demo.v AS ' + query + ';', 's.sql', 'a' * 64)
        self.assertFalse(expand_wildcard_outputs(inv, tables))
        outputs = inv['items'][0]['details']['output_columns']
        self.assertEqual(outputs[0]['expression'], '"X"."Case ID"')
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, tables),
                         [('Case ID', '"X"."Case ID"')])
        self.assertEqual(infer_expression(outputs[0]['expression'], tables,
                                         aliases={'X': 'demo.t'}), 'integer')

    def test_plain_join_preserves_from_order(self):
        query = 'SELECT t.*, u.b FROM demo.t JOIN demo.u ON t.id=u.id'
        self.assertEqual(expand_star_outputs(parse_sql(query)[0].stmt, self.TABLES),
                         [('id', 'demo.t.id'), ('a', 'demo.t.a'), ('b', 'u.b')])
        # A proven temporary table uses a scope key internally, but SQL
        # expressions must retain its actual source name.
        inv = extract_inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$'
                                'BEGIN CREATE TEMP TABLE local_t(id int); PERFORM local_t.* FROM local_t; END $$;',
                                's.sql', 'a' * 64)
        created = next(i for i in inv['items'] if i['kind'] == 'CREATE')
        self.assertFalse(expand_wildcard_outputs(inv, {
            created['details']['reference']: {'columns': created['details']['columns']}}))
        projection = next(i for i in inv['items'] if i['kind'] == 'PERFORM')
        self.assertEqual(projection['details']['columns'][0]['expression'], 'local_t.id')

    def test_declared_output_names_are_applied_after_expansion(self):
        for statement in ('CREATE VIEW demo.v(x,y) AS SELECT * FROM demo.t;',
                          'CREATE TABLE demo.v(x,y) AS SELECT * FROM demo.t;',
                          'CREATE MATERIALIZED VIEW demo.v(x,y) AS SELECT * FROM demo.t;'):
            with self.subTest(sql=statement):
                inv = extract_inventory(statement, 's.sql', 'a' * 64)
                self.assertFalse(expand_wildcard_outputs(inv, self.TABLES))
                self.assertEqual([(o['name'], o['expression']) for o in inv['items'][0]['details']['output_columns']],
                                 [('x', 'demo.t.id'), ('y', 'demo.t.a')])

    def test_full_gate_refuses_join_using_wildcard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 's.sql', root / 'ctx.sql'
            source.write_text('CREATE VIEW demo.v AS SELECT * FROM demo.t JOIN demo.u USING (id);', encoding='utf8')
            context.write_text('CREATE TABLE demo.t(id int,a text); CREATE TABLE demo.u(id int,b text);', encoding='utf8')
            decision = build(source, root / 'run', project_root=root, subject='demo.v', context=[context])
            self.assertFalse(decision['publication_authorized'], decision)
            inv = json.loads((root / 'run/inventory.json').read_text(encoding='utf8'))
            self.assertTrue(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))


class FunctionContractProofTests(unittest.TestCase):
    def test_oracle_contract_does_not_copy_the_input_type(self):
        self.assertEqual(infer_expression("add_months(TIMESTAMP '2026-01-31 12:00:00', 1)", {}), 'date')

    def test_bad_arity_cannot_claim_a_known_function_contract(self):
        for expression in ('last_day()', 'last_day(d, 1)', 'add_months(d)', 'add_months(d, 1, 2)'):
            with self.subTest(expression=expression):
                self.assertIsNone(infer_expression(expression, {}))
                inv = extract_inventory('CREATE FUNCTION demo.f(d date) RETURNS date LANGUAGE sql AS $$SELECT '
                                        + expression + '$$;', 's.sql', 'a' * 64)
                self.assertTrue(inv['coverage_notes'])


if __name__ == '__main__':
    unittest.main()
