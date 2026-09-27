"""Q-04 DDL completion: catalog-guarded DROP/ADD, SELECT * expansion, function contracts.

Compact fixtures reproduce the control project's migration templates (see
examples/fixtures/acceptance-large.json for the pinned external input): the
provable pg_attribute-guarded DROP COLUMN DO template, view/truncate/update
companions, and SELECT * expansion against established DDL state.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from ddl import do_drop_columns, enrich_inventory, reconstruct
from sql_extract import extract_inventory
from sql_types import (column_catalog, expand_wildcard_outputs, infer_expression,
                       POSITIONAL_INSERT_NOTE)

DO_DROP = """
DO
$$
DECLARE
  t record;
BEGIN
  FOR t IN (SELECT n.nspname
                 , c.relname
                 , a.attname
              FROM pg_catalog.pg_attribute a
              JOIN pg_catalog.pg_class c
                ON c.oid = a.attrelid
              JOIN pg_catalog.pg_namespace n
                ON n.oid = c.relnamespace
             WHERE n.nspname = 'sch'
               AND c.relname = 'tbl'
               AND a.attname = '{column}'
               AND a.attnum > 0
               AND NOT a.attisdropped)
  LOOP
    EXECUTE 'ALTER TABLE '||t.nspname||'.'||t.relname||' DROP COLUMN '||t.attname;
  END LOOP;
END;
$$;
"""

DO_DROP_IN = DO_DROP.replace("a.attname = '{column}'", "a.attname IN ('c1', 'c2')")


def do_node(text):
    from pglast import parse_sql
    statements = parse_sql(text)
    for raw in statements:
        if type(raw.stmt).__name__ == 'DoStmt':
            return raw.stmt
    raise AssertionError('no DO statement parsed')


class CatalogGuardedDoTests(unittest.TestCase):
    def test_literal_column_drop_template_is_recognized(self):
        schema, table, columns = do_drop_columns(do_node(DO_DROP.format(column='old_col')))
        self.assertEqual((schema, table, columns), ('sch', 'tbl', ['old_col']))

    def test_in_list_drop_template_is_recognized(self):
        schema, table, columns = do_drop_columns(do_node(DO_DROP_IN))
        self.assertEqual((schema, table, columns), ('sch', 'tbl', ['c1', 'c2']))

    def test_unrelated_do_is_rejected_with_a_local_reason(self):
        node = do_node("DO $$ BEGIN EXECUTE 'ALTER TABLE sch.tbl ADD COLUMN b int'; END $$;")
        with self.assertRaises(ValueError) as caught:
            do_drop_columns(node)
        self.assertIn('Unsupported DO migration node', str(caught.exception))

    def test_loop_with_extra_statement_is_rejected(self):
        text = DO_DROP_IN.replace("    EXECUTE 'ALTER TABLE '||t.nspname||'.'||t.relname||' DROP COLUMN '||t.attname;",
                                  "    EXECUTE 'ALTER TABLE '||t.nspname||'.'||t.relname||' DROP COLUMN '||t.attname;\n    EXECUTE 'DELETE FROM sch.tbl';")
        with self.assertRaises(ValueError) as caught:
            do_drop_columns(do_node(text))
        self.assertIn('exactly one dynamic statement', str(caught.exception))

    def test_missing_guards_are_rejected(self):
        text = DO_DROP_IN.replace('AND a.attnum > 0', '').replace('AND NOT a.attisdropped', '')
        with self.assertRaises(ValueError) as caught:
            do_drop_columns(do_node(text))
        self.assertIn('guards', str(caught.exception))

    def test_non_catalog_query_is_rejected(self):
        text = DO_DROP_IN.replace('pg_catalog.pg_attribute', 'pg_catalog.pg_class')
        with self.assertRaises(ValueError) as caught:
            do_drop_columns(do_node(text))
        self.assertIn('pg_attribute', str(caught.exception))

    def test_template_wrong_concat_order_is_rejected(self):
        text = DO_DROP_IN.replace("' DROP COLUMN '||t.attname", "' DROP COLUMN '||t.relname")
        with self.assertRaises(ValueError):
            do_drop_columns(do_node(text))


class DoMigrationTests(unittest.TestCase):
    def _reconstruct(self, files):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ordered = []
            for name, text in files.items():
                (root / name).write_text(text, encoding='utf8')
                ordered.append(name)
            (root / 'manifest.json').write_text(json.dumps(
                dict(dialect='postgres', version='15', ordered_files=ordered)), encoding='utf8')
            return reconstruct(root / 'manifest.json', project_root=root)

    def test_drop_add_cycle_reproduces_redefinition(self):
        result = self._reconstruct({
            'a.sql': 'CREATE TABLE sch.tbl (id int, c1 int, keep int);',
            'b.sql': (DO_DROP.format(column='c1') +
                      'ALTER TABLE sch.tbl ADD COLUMN c1 numeric;\n'
                      'COMMENT ON COLUMN sch.tbl.c1 IS \'amount\';'),
        })
        self.assertEqual(result['status'], 'resolved', result['errors'])
        columns = result['tables']['sch.tbl']['columns']
        self.assertEqual([(c['name'], c['type']) for c in columns],
                         [('id', 'integer'), ('keep', 'integer'), ('c1', 'numeric')])
        self.assertEqual(columns[-1]['comment'], 'amount')

    def test_in_list_drop_add_moves_columns_to_the_end(self):
        result = self._reconstruct({
            'a.sql': 'CREATE TABLE sch.tbl (c1 int, c2 int, keep int);',
            'b.sql': DO_DROP_IN + 'ALTER TABLE sch.tbl ADD COLUMN c2 text;\nALTER TABLE sch.tbl ADD COLUMN c1 int2;',
        })
        self.assertEqual(result['status'], 'resolved', result['errors'])
        # Both columns are re-appended in ADD order after the guarded drop.
        self.assertEqual([c['name'] for c in result['tables']['sch.tbl']['columns']],
                         ['keep', 'c2', 'c1'])

    def test_drop_without_baseline_stays_unsupported(self):
        result = self._reconstruct({'b.sql': DO_DROP.format(column='c1')})
        self.assertEqual(result['status'], 'unsupported')
        self.assertTrue(any('without established baseline' in e for e in result['errors']))

    def test_non_template_do_stays_unsupported(self):
        result = self._reconstruct({
            'a.sql': 'CREATE TABLE sch.tbl (id int);',
            'b.sql': "DO $$ BEGIN EXECUTE 'ALTER TABLE sch.tbl ADD COLUMN b int'; END $$;",
        })
        self.assertEqual(result['status'], 'unsupported')
        self.assertTrue(any('Unsupported DO' in e for e in result['errors']))

    def test_view_truncate_and_update_are_accepted_companions(self):
        result = self._reconstruct({
            'a.sql': 'CREATE TABLE sch.tbl (id int, flag int);',
            'b.sql': ('DROP VIEW IF EXISTS sch.v CASCADE;\n'
                      'TRUNCATE TABLE sch.tbl;\n' +
                      DO_DROP.format(column='flag') +
                      'ALTER TABLE sch.tbl ADD COLUMN flag int2;\n'
                      'UPDATE sch.tbl SET flag = 0;'),
        })
        self.assertEqual(result['status'], 'resolved', result['errors'])
        self.assertEqual([c['name'] for c in result['tables']['sch.tbl']['columns']],
                         ['id', 'flag'])


class WildcardExpansionTests(unittest.TestCase):
    def test_bare_star_expands_from_established_columns(self):
        inv = extract_inventory('CREATE VIEW demo.v AS SELECT * FROM demo.t;',
                                'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        outputs = inv['items'][0]['details']['output_columns']
        self.assertEqual([(o['name'], o['expression']) for o in outputs],
                         [('id', 'demo.t.id'), ('x', 'demo.t.x')])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_qualified_star_keeps_only_its_own_relation(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS SELECT u.* FROM demo.t JOIN demo.u AS u ON u.id = t.id;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}]},
            'demo.u': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'y', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        outputs = inv['items'][0]['details']['output_columns']
        self.assertEqual([(o['name'], o['expression']) for o in outputs],
                         [('id', 'u.id'), ('y', 'u.y')])

    def test_unknown_source_keeps_the_blocking_gap(self):
        inv = extract_inventory('CREATE VIEW demo.v AS SELECT * FROM demo.t;',
                                'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {})
        # The declaration output and the SELECT item both keep the wildcard.
        self.assertEqual(len(unresolved), 2)
        self.assertTrue(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_expansion_is_applied_when_inventory_is_enriched(self):
        inv = extract_inventory('CREATE VIEW demo.v AS SELECT * FROM demo.t;',
                                'source.sql', 'a' * 64)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ctx.sql').write_text('CREATE TABLE demo.t (id int, x text);', encoding='utf8')
            enrich_inventory(inv, context_files=[root / 'ctx.sql'], project_root=root)
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_star_inside_function_body_expands_on_insert_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$\n'
                              'BEGIN\n'
                              'INSERT INTO demo.out SELECT * FROM demo.src;\n'
                              'END $$;', encoding='utf8')
            (root / 'ctx.sql').write_text(
                'CREATE TABLE demo.src (id int, amount numeric);\n'
                'CREATE TABLE demo.out (id int, amount numeric);', encoding='utf8')
            inv = extract_inventory(source.read_text(encoding='utf8'), 'source.sql', 'a' * 64)
            # Mirrors the build pipeline: enrich establishes DDL state first.
            enrich_inventory(inv, context_files=[root / 'ctx.sql'], project_root=root)
            result = column_catalog(inv, [source], [root / 'ctx.sql'], root)
            self.assertEqual([(m['name'], m['expression']) for m in result['mappings']],
                             [('id', 'demo.src.id'), ('amount', 'demo.src.amount')])
            self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_unexpanded_view_wildcard_is_not_claimed_as_resolved(self):
        inv = extract_inventory('CREATE VIEW demo.v AS SELECT * FROM demo.t;',
                                'source.sql', 'a' * 64)
        self.assertTrue(any('Wildcard output columns require DDL expansion' in n['reason']
                            for n in inv['coverage_notes']))

    def test_star_through_cte_expands_from_cte_columns(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS WITH c AS (SELECT id, x FROM demo.t) SELECT * FROM c;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_star_through_derived_table_expands(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS SELECT * FROM (SELECT id, x FROM demo.t) AS d;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_cte_aliascolnames_override_select_names(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS WITH c(a, b) AS (SELECT id, x FROM demo.t) SELECT * FROM c;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))
        # The CTE aliascolnames `a, b` override the inner SELECT names `id, x`,
        # so the outer projection shows `a, b` (not `id, x`).
        projection = next(i for i in inv['items']
                          if i['kind'] == 'SELECT' and any(
                              isinstance(c, dict) and c.get('expanded_from') == '*'
                              for c in i['details'].get('columns') or ()))
        self.assertEqual([(c['name'], c['expression']) for c in projection['details']['columns']],
                         [('a', 'c.a'), ('b', 'c.b')])

    def test_nested_cte_wildcard_resolves_through_chain(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS WITH a AS (SELECT id, x FROM demo.t), '
            'b AS (SELECT * FROM a) SELECT * FROM b;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_union_all_cte_expands_from_left_operand(self):
        inv = extract_inventory(
            'CREATE VIEW demo.v AS WITH a AS (SELECT id, x FROM demo.t), '
            'b AS (SELECT * FROM a UNION ALL SELECT * FROM a) SELECT * FROM b;',
            'source.sql', 'a' * 64)
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}, {'name': 'x', 'type': 'text'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))

    def test_three_way_union_inventories_all_operands(self):
        # PostgreSQL parses `a UNION b UNION c` left-associatively as
        # `(a UNION b) UNION c`. The middle operand `b` must still be analysed
        # so its reads (and any wildcards) are not silently dropped.
        inv = extract_inventory(
            'CREATE VIEW demo.v AS WITH b AS '
            '(SELECT id FROM demo.t UNION ALL SELECT id FROM demo.t '
            'UNION ALL SELECT id FROM demo.t) SELECT * FROM b;',
            'source.sql', 'a' * 64)
        union_operands = [i for i in inv['items']
                          if i.get('kind') == 'SELECT'
                          and not i['details'].get('set_operation')
                          and 'demo.t' in (i.get('reads') or [])]
        self.assertEqual(len(union_operands), 3,
                         f'expected 3 UNION operands, got {len(union_operands)}')
        unresolved = expand_wildcard_outputs(inv, {
            'demo.t': {'columns': [{'name': 'id', 'type': 'integer'}]}})
        self.assertEqual(unresolved, [])
        self.assertFalse(any('Wildcard' in n['reason'] for n in inv['coverage_notes']))


class ExternalFunctionContractTests(unittest.TestCase):
    def test_last_day_and_add_months_are_identified_not_unresolved(self):
        inv = extract_inventory(
            "CREATE FUNCTION demo.f(d date) RETURNS date LANGUAGE sql AS $$"
            "SELECT last_day(d) + add_months(d, 1); $$;", 'source.sql', 'a' * 64)
        self.assertFalse(any('Unresolved unqualified call' in n['reason'] for n in inv['coverage_notes']))
        declaration = next(i for i in inv['items'] if i['kind'] == 'DECLARATION')
        self.assertEqual(declaration['details'].get('external_functions'), ['add_months', 'last_day'])

    def test_documented_contracts_drive_conservative_types(self):
        self.assertEqual(infer_expression('last_day(d)', {}), 'date')
        self.assertEqual(infer_expression('add_months(d, 1)', {}), 'date')

    def test_unknown_unqualified_calls_still_gaps(self):
        inv = extract_inventory(
            "CREATE FUNCTION demo.f() RETURNS void LANGUAGE sql AS $$"
            "SELECT mystery_helper(); $$;", 'source.sql', 'a' * 64)
        self.assertTrue(any('Unresolved unqualified call: mystery_helper' in n['reason']
                            for n in inv['coverage_notes']))


class GreenplumStorageValueTests(unittest.TestCase):
    def test_orientation_row_is_a_recorded_storage_value(self):
        inv = extract_inventory(
            'CREATE TABLE demo.t (id int) WITH (appendonly = TRUE, orientation = ROW, '
            'compresstype = zstd, compresslevel = 3) DISTRIBUTED BY (id);',
            'source.sql', 'a' * 64, dialect='greenplum', version='unknown')
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        create = next(i for i in inv['items'] if i['kind'] == 'CREATE')
        self.assertEqual(create['details']['storage_parameters'],
                         {'appendonly': 'true', 'orientation': 'row',
                          'compresstype': 'zstd', 'compresslevel': '3'})
        self.assertEqual(create['details']['distributed'], {'mode': 'BY', 'columns': ['id']})


class MultiBranchColumnExpressionTests(unittest.TestCase):
    """A null aggregate cannot substitute for evidence of each SQL mapping."""

    def test_null_expression_without_operation_queries_is_rejected(self):
        from sql_types import check_types
        facts = {
            'columns': [
                {'id': 'col_1', 'object_id': 'obj_1', 'name': 'val',
                 'type_target': 'numeric', 'type_expression': None,
                 'expression': None, 'expression_status': 'unknown',
                 'source_refs': []},
                {'id': 'col_2', 'object_id': 'obj_1', 'name': 'id',
                 'type_target': 'int', 'type_expression': 'int',
                 'expression': 's.id', 'expression_status': 'known',
                 'source_refs': []},
            ],
            'objects': [{'id': 'obj_1', 'kind': 'table', 'schema': 'demo', 'name': 'tgt'}],
            'documented_object_ids': set(),
            'definitions': [{'id': 'def_1', 'object_id': 'obj_1', 'status': 'resolved', 'source_refs': []}],
        }
        catalogue = {
            'tables': {'demo.tgt': {'columns': [
                {'name': 'id', 'type': 'int'}, {'name': 'val', 'type': 'numeric'}]}},
            'mappings': [
                {'table': 'demo.tgt', 'name': 'val', 'expression': 's.amount',
                 'type_expression': 'numeric', 'source_ref': {}},
                {'table': 'demo.tgt', 'name': 'val', 'expression': 's.amount * 2',
                 'type_expression': 'numeric', 'source_ref': {}},
                {'table': 'demo.tgt', 'name': 'id', 'expression': 's.id',
                 'type_expression': 'int', 'source_ref': {}},
                {'table': 'demo.tgt', 'name': 'id', 'expression': 's.id',
                 'type_expression': 'int', 'source_ref': {}},
            ],
            'functions': {},
        }
        errors = check_types(facts, catalogue)
        self.assertEqual(errors, [
            'facts column col_1: SQL mapping variant lacks its operation query',
            'facts column col_1: SQL mapping variant lacks its operation query'])

    def test_non_null_expression_still_checked_against_all_mappings(self):
        from sql_types import check_types
        facts = {
            'columns': [
                {'id': 'col_1', 'object_id': 'obj_1', 'name': 'val',
                 'type_target': 'numeric', 'type_expression': 'numeric',
                 'expression': 's.amount * 2', 'expression_status': 'known',
                 'source_refs': []},
            ],
            'objects': [{'id': 'obj_1', 'kind': 'table', 'schema': 'demo', 'name': 'tgt'}],
            'documented_object_ids': set(),
            'definitions': [{'id': 'def_1', 'object_id': 'obj_1', 'status': 'resolved', 'source_refs': []}],
        }
        catalogue = {
            'tables': {'demo.tgt': {'columns': [{'name': 'val', 'type': 'numeric'}]}},
            'mappings': [
                {'table': 'demo.tgt', 'name': 'val', 'expression': 's.amount',
                 'type_expression': 'numeric', 'source_ref': {}},
                {'table': 'demo.tgt', 'name': 'val', 'expression': 's.amount * 2',
                 'type_expression': 'numeric', 'source_ref': {}},
            ],
            'functions': {},
        }
        errors = check_types(facts, catalogue)
        self.assertIn('facts column col_1: multiple SQL mappings require a null expression/type summary with unknown status', errors)


class PositionalInsertWidthTests(unittest.TestCase):
    """A positional INSERT maps only at equal target/select widths.

    A known width mismatch is a source finding, not an analysis gap: the
    analysis is complete (the statement cannot map), the defect belongs to
    the SQL versus the established DDL state. It is listed on the page and
    never zip-truncated into a plausible-looking but invented column map.
    """

    def _catalogue(self, body, context_sql):
        import tempfile
        from sql_types import column_catalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 'source.sql', root / 'context.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$'
                              f'BEGIN {body} END $$;', encoding='utf8')
            context.write_text(context_sql, encoding='utf8')
            inv = extract_inventory(source.read_text(encoding='utf8'), 'source.sql', 'a' * 64)
            return inv, column_catalog(inv, [source], [context], root)

    @staticmethod
    def _positional(inv):
        return [f for f in inv.get('source_findings', [])
                if f.get('reason') == POSITIONAL_INSERT_NOTE]

    def test_star_width_mismatch_is_a_listed_source_finding(self):
        inv, cat = self._catalogue('INSERT INTO demo.out SELECT * FROM demo.src;',
                                   'CREATE TABLE demo.src(id int); '
                                   'CREATE TABLE demo.out(first int, second text);')
        self.assertEqual([m for m in cat['mappings'] if m['table'] == 'demo.out'], [])
        findings = self._positional(inv)
        self.assertEqual(len(findings), 1, inv.get('source_findings'))
        self.assertEqual(findings[0]['source_ref']['start_line'], 1)
        self.assertEqual((findings[0]['target_width'], findings[0]['select_width']), (2, 1))
        self.assertFalse([n for n in inv['coverage_notes']
                          if n['reason'] == POSITIONAL_INSERT_NOTE], inv['coverage_notes'])

    def test_star_width_match_maps_columns_positionally(self):
        inv, cat = self._catalogue('INSERT INTO demo.out SELECT * FROM demo.src;',
                                   'CREATE TABLE demo.src(id int); CREATE TABLE demo.out(first int);')
        self.assertEqual([(m['name'], m['expression']) for m in cat['mappings']],
                         [('first', 'demo.src.id')])
        self.assertFalse(self._positional(inv), inv.get('source_findings'))

    def test_explicit_projection_width_mismatch_is_not_truncated(self):
        inv, cat = self._catalogue('INSERT INTO demo.out SELECT id FROM demo.src;',
                                   'CREATE TABLE demo.src(id int); '
                                   'CREATE TABLE demo.out(first int, second text);')
        self.assertEqual([m for m in cat['mappings'] if m['table'] == 'demo.out'], [])
        self.assertEqual(len(self._positional(inv)), 1)

    def test_explicit_column_list_projection_maps(self):
        inv, cat = self._catalogue('INSERT INTO demo.out (second) SELECT id FROM demo.src;',
                                   'CREATE TABLE demo.src(id int); '
                                   'CREATE TABLE demo.out(first int, second text);')
        self.assertEqual([(m['name'], m['expression']) for m in cat['mappings']],
                         [('second', 'id')])
        self.assertFalse(self._positional(inv), inv.get('source_findings'))

    def test_values_insert_without_projection_is_not_a_width_gap(self):
        inv, cat = self._catalogue("INSERT INTO demo.out VALUES (1, 'x');",
                                   "CREATE TABLE demo.out(first int, second text);")
        self.assertFalse(self._positional(inv), inv.get('source_findings'))

    def test_unresolved_star_keeps_the_wildcard_gap_instead(self):
        inv, cat = self._catalogue('INSERT INTO demo.out SELECT * FROM demo.src;',
                                   'CREATE TABLE demo.out(first int, second text);')
        self.assertTrue(any('Wildcard' in n['reason'] for n in inv['coverage_notes']),
                        inv['coverage_notes'])
        self.assertFalse(self._positional(inv), inv.get('source_findings'))

    def test_source_finding_is_visible_but_does_not_block_the_gate(self):
        import tempfile
        from build_bundle import build
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 'source.sql', root / 'context.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$'
                              'BEGIN INSERT INTO demo.out SELECT * FROM demo.src; END $$;',
                              encoding='utf8')
            context.write_text('CREATE TABLE demo.src(id int); '
                               'CREATE TABLE demo.out(first int, second text);', encoding='utf8')
            result = build(source, root / 'run', project_root=root,
                           subject='demo.f', context=[context])
            inv = json.loads((root / 'run/inventory.json').read_text(encoding='utf-8'))
            self.assertEqual(len(self._positional(inv)), 1)
            page = (root / 'run/page.draft.md').read_text(encoding='utf-8')
            self.assertIn('Source analysis observations', page)
            self.assertIn(POSITIONAL_INSERT_NOTE, page)
            self.assertTrue(result['publication_authorized'], result)

    def test_page_that_drops_a_source_finding_is_not_publishable(self):
        import tempfile
        from build_bundle import build, finish
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, context = root / 'source.sql', root / 'context.sql'
            source.write_text('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$'
                              'BEGIN INSERT INTO demo.out SELECT * FROM demo.src; END $$;',
                              encoding='utf8')
            context.write_text('CREATE TABLE demo.src(id int); '
                               'CREATE TABLE demo.out(first int, second text);', encoding='utf8')
            result = build(source, root / 'run', project_root=root,
                           subject='demo.f', context=[context])
            self.assertTrue(result['publication_authorized'], result)
            # A writer that silently drops the visible finding loses the
            # honesty of the non-blocking classification: re-seal must refuse.
            page = (root / 'run/page.draft.md').read_text(encoding='utf-8')
            stripped = '\n'.join(line for line in page.splitlines()
                                 if POSITIONAL_INSERT_NOTE not in line)
            self.assertNotEqual(stripped, page)
            (root / 'run/page.draft.md').write_text(stripped, encoding='utf-8')
            resealed = finish(root / 'run', sql_files=[source], context=[context],
                              project_root=root)
            self.assertEqual(resealed['decision'], 'revise', resealed)
            self.assertFalse(resealed['publication_authorized'])
            self.assertTrue(any('source finding is not visible' in e
                                for e in resealed['errors']), resealed['errors'])


class DdlAcceptanceTests(unittest.TestCase):
    """Q-04 DDL acceptance on the pinned control project (when reachable)."""
    def _manifest_for(self, root):
        import os.path
        from sql_extract import sha256_file
        scenario = json.loads((PACKAGE / 'examples/fixtures/acceptance-large.json').read_text(encoding='utf8'))
        ordered_inputs = scenario['ddl_acceptance']['ordered_inputs']
        sources = [root / entry['path'] for entry in ordered_inputs]
        for source, entry in zip(sources, ordered_inputs):
            self.assertTrue(source.is_file(), f'pinned DDL input is missing: {source}')
            self.assertEqual(sha256_file(source), entry['sha256'],
                             f'{source}: {scenario["hash_policy"]["on_hash_mismatch"]}')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mdir = Path(temporary.name)
        # The acceptance sequence is pinned input, never a sorted glob of a
        # changing checkout. It does not establish production deployment order.
        ordered = [os.path.relpath(p, mdir).replace('\\', '/') for p in sources]
        manifest = dict(dialect='greenplum', version='unknown',
                        target_revision='q04-onboarding-ddl-acceptance',
                        ordered_files=ordered)
        path = mdir / 'manifest.json'
        path.write_text(json.dumps(manifest), encoding='utf8')
        return path

    @staticmethod
    def _project_root():
        raw = os.environ.get('WIKI_DOC_ACCEPTANCE_PROJECT', '')
        root = Path(raw) if raw else None
        return root if root is not None and root.is_dir() else None

    def _source_context_for(self, root):
        """Pinned read-only INI source DDL for the three retro SELECT * copies."""
        from sql_extract import sha256_file
        scenario = json.loads((PACKAGE / 'examples/fixtures/acceptance-large.json')
                              .read_text(encoding='utf8'))
        files = []
        for entry in scenario['source_context']['files']:
            source = root / entry['path']
            self.assertTrue(source.is_file(), f'pinned source context is missing: {source}')
            self.assertEqual(sha256_file(source), entry['sha256'],
                             f'{source}: {scenario["hash_policy"]["on_hash_mismatch"]}')
            files.append(source)
        return files

    def test_onboarding_ddl_reconstructs_to_the_reviewed_state(self):
        root = self._project_root()
        if root is None:
            self.skipTest('control project not configured (WIKI_DOC_ACCEPTANCE_PROJECT)')
        result = reconstruct(self._manifest_for(root), project_root=root)
        self.assertEqual(result['status'], 'resolved', result['errors'])
        core = 's_gp_p1024_dmr_svd_kb_ckr_uup_gp_core'
        tables = result['tables']
        main = tables[f'{core}.ckr_uup_db_onboarding_main']
        meet = tables[f'{core}.ckr_uup_db_onboarding_meet_tasks']
        clients = tables[f'{core}.ckr_uup_db_onboarding_new_clients']
        # D10: meet_tasks has exactly 31 columns with the reviewed types.
        self.assertEqual(len(meet['columns']), 31)
        self.assertEqual([(c['name'], c['type']) for c in meet['columns']][-6:],
                         [('fact_start_date', 'timestamp'), ('task_type', 'varchar(512)'),
                          ('product_id', 'varchar(512)'), ('product_name', 'varchar(512)'),
                          ('product_group', 'varchar(512)'), ('was_in_progress', 'int2')])
        by_name = {c['name']: c['type'] for c in meet['columns']}
        self.assertEqual(by_name['is_done'], 'int2')
        self.assertEqual(by_name['is_phoned'], 'int2')
        self.assertEqual(by_name['task_id'], 'varchar(32)')
        # main: the DROP+ADD of product moved it to the end as int2.
        self.assertEqual([c['name'] for c in main['columns']][-1], 'product')
        self.assertEqual(main['columns'][-1]['type'], 'int2')
        self.assertEqual(len(main['columns']), 25)
        # new_clients: ALTER COLUMN TYPE changed only the type, not the position.
        client_types = {c['name']: c['type'] for c in clients['columns']}
        self.assertEqual(client_types['active_products_90'], 'numeric(8, 2)')
        self.assertEqual(client_types['is_client_active_rko'], 'int2')
        self.assertEqual(client_types['ucp_id'], 'numeric')
        self.assertEqual(len(clients['columns']), 29)
        # Greenplum attributes survive the ordered reconstruction.
        self.assertEqual(main['distributed'], {'mode': 'RANDOMLY'})
        self.assertEqual(meet['distributed'], {'mode': 'BY', 'columns': ['task_id']})
        self.assertEqual(clients['distributed'], {'mode': 'BY', 'columns': ['ucp_id']})
        for table in (main, meet, clients):
            self.assertEqual(table.get('storage_parameters'),
                             {'appendonly': 'true', 'orientation': 'row',
                              'compresstype': 'zstd', 'compresslevel': '3'})
            self.assertEqual(table.get('gp_extension_version'), 1)

    def test_control_sql_targets_resolve_against_reconstructed_ddl(self):
        scenario = json.loads((PACKAGE / 'examples/fixtures/acceptance-large.json')
                              .read_text(encoding='utf-8'))
        root = self._project_root()
        sql = root / scenario['input']['relative_path'] if root is not None else None
        if not sql or not sql.is_file():
            self.skipTest('control project not configured (WIKI_DOC_ACCEPTANCE_PROJECT)')
        from sql_extract import sha256_file
        digest = sha256_file(sql)
        self.assertEqual(digest, scenario['input']['sha256'],
                         scenario['hash_policy']['on_hash_mismatch'])
        manifest = self._manifest_for(root)
        result = reconstruct(manifest, project_root=root)
        self.assertEqual(result['status'], 'resolved', result['errors'])
        context = self._source_context_for(root)
        inv = extract_inventory(sql.read_text(encoding='utf-8-sig'),
                                sql.relative_to(root).as_posix(), digest,
                                dialect='greenplum', version='unknown', documented_subjects=[
                                    's_gp_p1024_dmr_svd_kb_ckr_uup_gp_core.ckr_uup_db_onboarding'])
        enrich_inventory(inv, context_files=context, project_root=root, migration_manifest=manifest)
        table_root = (root / 'gp/gp/all/u_gp_p1024_dmr_svd_kb_ckr_uup_gp_loader'
                      / '04s_gp_p1024_dmr_svd_kb_ckr_uup_gp_core/02table')
        self.assertTrue(table_root.is_dir())
        # The ordered migration manifest alone establishes the reviewed target
        # state; drop-only scripts of other tables are unordered and out of
        # this acceptance scope, so they are not smuggled in as context. The
        # pinned source_context is read-only INI DDL, used only to expand the
        # three retro SELECT * copies.
        catalogue = column_catalog(inv, [sql], context, root, migration_manifest=manifest)
        # Every wildcard is expanded against established DDL now: the three
        # retro INSERT ... SELECT * copies resolve through the pinned INI
        # source tables. EXCEPTION is a complete conditional model; positional
        # mismatches below remain source findings, not analysis gaps.
        self.assertEqual(inv['coverage_notes'], [])
        self.assertEqual(sum(n['reason'] == 'Wildcard output columns require DDL expansion'
                             for n in inv['coverage_notes']), 0)
        self.assertEqual(sum(n['reason'].startswith('Exception handlers are inventoried')
                             for n in inv['coverage_notes']), 0)
        self.assertEqual(len([i for i in inv['items'] if i['kind'] == 'EXCEPTION_BLOCK']), 1)
        # The retro copies expand to exactly the INI source columns.
        ini = 's_gp_p1024_ora_svd_kb_ckr_uup_gp_ini'
        retro_widths = {19328: 'main', 19359: 'new_clients', 19390: 'meet_tasks'}
        expanded = {}
        for item in inv['items']:
            line = item['source_ref']['start_line']
            if item['kind'] == 'SELECT' and line in retro_widths:
                expanded[line] = [c['name'] for c in item['details'].get('columns') or []]
        self.assertEqual(sorted(expanded), sorted(retro_widths))
        for line, suffix in retro_widths.items():
            source_columns = [c['name'] for c in
                              catalogue['tables'][f'{ini}.ckr_uup_db_onboarding_{suffix}']['columns']]
            self.assertEqual(expanded[line], source_columns)
        # The INI sources are narrower than the migrated targets (24/25, 20/29,
        # 26/31) and 43 calculated main inserts build 24-column rows into the
        # 25-column target. Positional mapping is unproven for all of them and
        # is listed as a source finding, never zip-truncated into a plausible
        # column map. This is an independent source-level finding of this
        # acceptance run; it describes the SQL versus the established DDL state
        # and is not an analysis gap (Q-07/Q-09 decision, 2026-09-25).
        positional = [f for f in inv.get('source_findings', [])
                      if f.get('reason') == POSITIONAL_INSERT_NOTE]
        self.assertEqual(len(positional), 46,
                         sorted(f['source_ref']['start_line'] for f in positional))
        self.assertEqual(sorted(f['source_ref']['start_line'] for f in positional
                                if f['source_ref']['start_line'] in retro_widths),
                         sorted(retro_widths))
        self.assertFalse([n for n in inv['coverage_notes']
                          if n['reason'] == POSITIONAL_INSERT_NOTE], inv['coverage_notes'])
        self.assertFalse([m for m in catalogue['mappings']
                          if 'SELECT *' in (m.get('query') or '') and 'gp_ini' in m['query']],
                         'no positional mapping may be invented for the retro copies')
        core = 's_gp_p1024_dmr_svd_kb_ckr_uup_gp_core'
        reviewed = {f'{core}.ckr_uup_db_onboarding_main',
                    f'{core}.ckr_uup_db_onboarding_meet_tasks',
                    f'{core}.ckr_uup_db_onboarding_new_clients'}
        checked = 0
        for mapping in catalogue['mappings']:
            if mapping['table'] not in reviewed:
                continue
            known = {c['name'] for c in catalogue['tables'][mapping['table']]['columns']}
            self.assertIn(mapping['name'], known,
                          f"{mapping['table']}.{mapping['name']} is not in the reconstructed DDL")
            checked += 1
        self.assertGreaterEqual(checked, 30, 'expected the reviewed tables to appear in mappings')
