"""Q-04 review: branch semantics, diagnostics and end-to-end fact checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from build_bundle import build, finish, render
from check_policy import derive_inventory_checks, load_policy
from sql_extract import extract_inventory
from sql_types import column_catalog
from ddl import reconstruct


def inventory(body, declarations='', parameters='p boolean, q boolean, r boolean'):
    text = (f'CREATE FUNCTION demo.f({parameters}) RETURNS void LANGUAGE plpgsql AS $$\n'
            f'{declarations}\nBEGIN\n{body}\nEND $$;')
    return extract_inventory(text, 'source.sql', 'a' * 64)


def operations(inv, kind):
    return [i for i in inv['items'] if i['kind'] == kind]


class BranchReviewTests(unittest.TestCase):
    def test_elsif_and_else_exclude_previous_true_conditions_in_source_order(self):
        inv = inventory("""IF p THEN DELETE FROM demo.a;
ELSIF q THEN DELETE FROM demo.b;
ELSIF r THEN DELETE FROM demo.c;
ELSE DELETE FROM demo.d; END IF;""")
        deletes = operations(inv, 'DELETE')
        self.assertEqual([i['writes'][0] for i in deletes], ['demo.a', 'demo.b', 'demo.c', 'demo.d'])
        # IS NOT TRUE includes NULL, which also enters the next PL/pgSQL branch.
        self.assertEqual([i['details']['guards'] for i in deletes], [
            ['p'], ['(p) IS NOT TRUE', 'q'],
            ['(p) IS NOT TRUE', '(q) IS NOT TRUE', 'r'],
            ['(p) IS NOT TRUE', '(q) IS NOT TRUE', '(r) IS NOT TRUE']])
        self.assertEqual(operations(inv, 'IF')[1]['details']['guards'], ['(p) IS NOT TRUE'])

    def test_nested_elsif_keeps_outer_branch_and_guard(self):
        inv = inventory('IF p THEN IF q THEN RETURN; ELSIF r THEN DELETE FROM demo.a; '
                        'ELSE DELETE FROM demo.b; END IF; END IF;')
        first, second = operations(inv, 'DELETE')
        self.assertEqual(first['details']['branch'], 'then_body/elsif:0')
        self.assertEqual(first['details']['guards'], ['p', '(q) IS NOT TRUE', 'r'])
        self.assertEqual(second['details']['guards'], ['p', '(q) IS NOT TRUE', '(r) IS NOT TRUE'])

    def test_all_statement_kinds_inherit_guard(self):
        inv = inventory("""IF p THEN
CREATE TABLE demo.t(a int);
CREATE TABLE demo.u AS SELECT 1 AS a;
ALTER TABLE demo.t ADD COLUMN b int;
TRUNCATE demo.t;
CALL demo.audit();
EXECUTE 'DELETE FROM demo.t';
WITH x AS (SELECT 1 AS a) SELECT a FROM x;
RETURN;
END IF;""")
        self.assertFalse(inv['coverage_notes'])
        body = [i for i in inv['items'] if i['kind'] not in ('DECLARATION', 'IF')]
        self.assertTrue({'CREATE','CTAS','ALTER','TRUNCATE','CALL','EXECUTE','CTE','SELECT','RETURN'}
                        <= {i['kind'] for i in body})
        for item in body:
            with self.subTest(kind=item['kind']):
                self.assertEqual(item['details'].get('guards'), ['p'])
                self.assertEqual(item['details'].get('branch'), 'then_body')

    def test_exception_context_reaches_ddl_and_calls(self):
        inv = inventory('IF p THEN BEGIN RETURN; EXCEPTION WHEN division_by_zero THEN '
                        'TRUNCATE demo.t; CALL demo.audit(); END; END IF;')
        for kind in ('TRUNCATE', 'CALL'):
            details = operations(inv, kind)[0]['details']
            self.assertEqual(details.get('branch'), 'then_body/exception:division_by_zero')
            self.assertEqual(details.get('guards'), ['p'])
        self.assertEqual(inv['coverage_notes'], [])
        flow = operations(inv, 'EXCEPTION_BLOCK')[0]['details']['exception_flow']
        self.assertEqual(flow['failure_point'], 'runtime_dependent_any_expression_in_protected_body')
        self.assertEqual(flow['handlers'][0]['conditions'], ['division_by_zero'])
        self.assertEqual([a['construct'] for a in flow['handlers'][0]['body']], ['TRUNCATE', 'CALL'])


class RaiseReviewTests(unittest.TestCase):
    def test_all_six_sql_raise_levels(self):
        levels = ['DEBUG', 'LOG', 'INFO', 'NOTICE', 'WARNING', 'EXCEPTION']
        inv = inventory('\n'.join(f"RAISE {level} 'msg';" for level in levels))
        self.assertEqual([i['details']['raise_level'] for i in operations(inv, 'RAISE')], levels)

    def test_using_options_and_named_condition_survive(self):
        inv = inventory("RAISE unique_violation USING DETAIL='duplicate', HINT='retry';")
        details = operations(inv, 'RAISE')[0]['details']
        self.assertEqual(details.get('raise_condition'), 'unique_violation')
        self.assertEqual(details.get('raise_options'), [
            {'name': 'DETAIL', 'expression': "'duplicate'"},
            {'name': 'HINT', 'expression': "'retry'"}])

    def test_sqlstate_is_distinct_from_rethrow(self):
        inv = inventory("RAISE SQLSTATE '22012'; RAISE;")
        named, rethrow = operations(inv, 'RAISE')
        self.assertEqual(named['details'].get('raise_condition'), '22012')
        self.assertFalse(named['details'].get('rethrow'))
        self.assertTrue(rethrow['details'].get('rethrow'))

    def test_expression_calls_and_reads_are_not_lost(self):
        inv = inventory("RAISE NOTICE '%', demo.format_value((SELECT max(a) FROM demo.t)) "
                        "USING DETAIL=demo.detail();")
        op = operations(inv, 'RAISE')[0]
        self.assertEqual(set(op.get('calls', [])), {'demo.format_value', 'demo.detail'})
        self.assertEqual(op.get('reads'), ['demo.t'])
        self.assertEqual(len(operations(inv, 'SELECT')), 1)

    def test_unresolved_raise_expression_call_keeps_gap(self):
        inv = inventory("RAISE NOTICE '%', unknown_call();")
        self.assertTrue(any('Unresolved unqualified call: unknown_call' in n['reason']
                            for n in inv['coverage_notes']))

    def test_raise_expressions_have_individual_plan_obligations(self):
        inv = inventory("RAISE NOTICE '%', p::text USING DETAIL=q::text;")
        op = operations(inv, 'RAISE')[0]
        self.assertEqual(op['details'].get('formulas'), ['p::text', 'q::text'])
        checks = derive_inventory_checks(load_policy(), [op])
        self.assertEqual([c['rule_id'] for c in checks].count('formula'), 2)
        self.assertEqual([c['rule_id'] for c in checks].count('operation'), 1)


class DiagnosticsReviewTests(unittest.TestCase):
    def test_get_diagnostics_preserves_assignment_and_guard(self):
        inv = inventory('IF p THEN GET DIAGNOSTICS n = ROW_COUNT; END IF;', 'DECLARE n bigint;')
        self.assertFalse(inv['coverage_notes'])
        ops = operations(inv, 'ASSIGN')
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]['details'].get('assignment_target'), 'n')
        self.assertEqual(ops[0]['details'].get('diagnostic'), {'kind':'ROW_COUNT', 'stacked':False})
        self.assertEqual(ops[0]['details'].get('guards'), ['p'])
        self.assertEqual(derive_inventory_checks(load_policy(), ops)[0]['rule_id'], 'operation')

    def test_stacked_diagnostics_keeps_each_target_and_kind(self):
        inv = inventory('BEGIN RETURN; EXCEPTION WHEN OTHERS THEN '
                        'GET STACKED DIAGNOSTICS msg = MESSAGE_TEXT, ctx = PG_EXCEPTION_CONTEXT; END;',
                        'DECLARE msg text; ctx text;')
        ops = operations(inv, 'ASSIGN')
        self.assertEqual([i['details']['assignment_target'] for i in ops], ['msg', 'ctx'])
        self.assertEqual([i['details']['diagnostic'] for i in ops], [
            {'kind':'MESSAGE_TEXT', 'stacked':True}, {'kind':'PG_EXCEPTION_CONTEXT', 'stacked':True}])

    def test_quoted_assignment_target_with_equals(self):
        inv = inventory('"a=b" := 1; "a=b" = 2;', 'DECLARE "a=b" int;')
        self.assertFalse(inv['coverage_notes'])
        self.assertEqual([i['details']['assignment_target'] for i in operations(inv, 'ASSIGN')], ['a=b', 'a=b'])
        self.assertEqual([i['details']['assignments'][0]['expression'] for i in operations(inv, 'ASSIGN')], ['1','2'])


class DdlReviewTests(unittest.TestCase):
    def test_positional_insert_uses_post_migration_column_order(self):
        root = PACKAGE / 'examples'
        source = next((root / 'fixtures').glob('q08*.sql'))
        inv = extract_inventory(source.read_text(encoding='utf8'), source.relative_to(root).as_posix(), 'a' * 64)
        result = column_catalog(inv, [source], [], root, migration_manifest=root/'fixtures/migrations/manifest.json')
        self.assertEqual([(m['name'], m['expression']) for m in result['mappings']],
                         [('id','s.id'), ('amount','s.amount'), ('legacy','s.legacy'), ('total','s.total')])

    def test_unexpanded_wildcard_is_not_claimed_as_resolved(self):
        inv = extract_inventory('CREATE VIEW demo.v AS SELECT * FROM demo.t;', 'source.sql', 'a' * 64)
        self.assertTrue(any('Wildcard output columns require DDL expansion' in n['reason']
                            for n in inv['coverage_notes']))

    def test_dynamic_migration_remains_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'step.sql').write_text("CREATE TABLE demo.t(a int); DO $$ BEGIN "
                                        "EXECUTE 'ALTER TABLE demo.t ADD COLUMN b int'; END $$;", encoding='utf8')
            (root/'manifest.json').write_text(json.dumps(dict(dialect='postgres', version='15', ordered_files=['step.sql'])), encoding='utf8')
            result = reconstruct(root/'manifest.json', project_root=root)
            self.assertEqual(result['status'], 'unsupported')
            # Only the provable catalog-guarded DROP COLUMN template is applied;
            # this free-form dynamic EXECUTE stays an explicit unsupported node.
            self.assertTrue(any('Unsupported DO' in error for error in result['errors']))


class GateReviewTests(unittest.TestCase):
    SQL = """CREATE FUNCTION demo.f(p boolean) RETURNS void LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
GET DIAGNOSTICS n = ROW_COUNT;
IF p THEN
    RAISE INFO 'count %', n USING DETAIL='row count';
ELSE
    RAISE NOTICE 'skipped';
END IF;
END $$;"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / 'run'
        self.source = self.root / 'source.sql'
        self.source.write_text(self.SQL, encoding='utf8')
        decision = build(self.source, self.run, project_root=self.root, subject='demo.f')
        self.assertEqual(decision['decision'], 'ready', decision)
        self.facts = json.loads((self.run / 'facts.json').read_text(encoding='utf8'))

    def check_mutation(self):
        # Regenerate claims, validation and hashes: a refusal must come from SQL.
        (self.run / 'facts.json').write_text(json.dumps(self.facts), encoding='utf8')
        plan = json.loads((self.run / 'validation_plan.json').read_text(encoding='utf8'))
        page, coverage = render(self.facts, plan)
        (self.run / 'page.draft.md').write_text(page, encoding='utf8')
        (self.run / 'coverage.json').write_text(json.dumps(coverage), encoding='utf8')
        result = finish(self.run, sql_files=[self.source], context=[], project_root=self.root)
        self.assertFalse(result['publication_authorized'], result)
        self.assertTrue(any('structure differs from independent SQL inventory' in e for e in result['errors']), result)
        self.assertFalse(any('sha256' in e.lower() or 'hash mismatch' in e.lower() for e in result['errors']), result)

    def test_else_guard_mutation_is_rejected(self):
        op = next(o for o in self.facts['operations'] if o.get('structure', {}).get('branch') == 'else_body')
        self.assertEqual(op['structure']['guards'], ['(p) IS NOT TRUE'])
        op['structure']['guards'] = ['p']
        self.check_mutation()

    def test_raise_using_mutation_is_rejected(self):
        op = next(o for o in self.facts['operations'] if o['kind'] == 'RAISE')
        self.assertIn('raise_options', op['structure'])
        op['structure']['raise_options'] = []
        self.check_mutation()

    def test_diagnostic_mutation_is_rejected(self):
        op = next(o for o in self.facts['operations'] if o['kind'] == 'ASSIGN')
        op['structure']['diagnostic']['kind'] = 'PG_CONTEXT'
        self.check_mutation()


if __name__ == '__main__':
    unittest.main()
