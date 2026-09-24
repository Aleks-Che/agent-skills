"""Q-04: body, dependency and DDL analysis towards the control object.

Compact fixtures reproduce the control SQL constructs (see
examples/fixtures/acceptance-large.json for the pinned external input):
parameter -> assignment -> IF guard -> branch operations (retro pairs),
full PL/pgSQL traversal (GET DIAGNOSTICS, RAISE, EXCEPTION audit calls),
CTE vs derived-table aliases, and expression essentials (NULL polarity,
CASE order, IN sets, ORDER direction, date bounds).
"""
import hashlib
import json
import re
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))

from sql_extract import extract_inventory  # noqa: E402


def analyze(sql, subject=None, dialect='postgres'):
    subjects = [subject] if subject else None
    return extract_inventory(sql, 'q04.sql', 'b' * 64, dialect=dialect,
                             version='unknown', documented_subjects=subjects)


def items(inv, kind):
    return [i for i in inv['items'] if i['kind'] == kind]


RETRO_SQL = """
CREATE OR REPLACE FUNCTION q_hist.apply_retro(p_retro varchar DEFAULT '0')
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    v_retro varchar(32);
    n bigint;
BEGIN
    v_retro = CASE WHEN p_retro IS NULL THEN '0' ELSE p_retro END;
    IF v_retro = '1' THEN
        DELETE FROM q_hist.main AS m WHERE m.fact_start_date < to_date('01.07.2025', 'dd.mm.yyyy');
        GET DIAGNOSTICS n = ROW_COUNT;
        INSERT INTO q_hist.main (kpi_id, fact_start_date)
        SELECT k.kpi_id, k.fact_start_date FROM q_src.kpi_facts AS k
        WHERE k.fact_start_date < to_date('01.07.2025', 'dd.mm.yyyy');

        DELETE FROM q_hist.clients AS c WHERE c.open_date < to_date('01.07.2025', 'dd.mm.yyyy');
        INSERT INTO q_hist.clients (id) SELECT c.id FROM q_src.clients AS c
        WHERE c.open_date < to_date('01.07.2025', 'dd.mm.yyyy');
    END IF;
    RETURN n;
END;
$$;
"""

TRAVERSAL_SQL = """
CREATE OR REPLACE FUNCTION q_ops.audit_trail(p int)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    n int;
BEGIN
    DELETE FROM q_ops.t WHERE id = p;
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'deleted %', n;
    BEGIN
        UPDATE q_ops.t SET a = 1 WHERE id = p;
    EXCEPTION WHEN unique_violation THEN
        RAISE WARNING 'dup %', SQLERRM;
        PERFORM q_ops.audit_log('err');
    END;
    RETURN;
END;
$$;
"""

CTE_SQL = """
CREATE OR REPLACE FUNCTION q_out.two_inserts()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO q_out.out_a (id, total)
    WITH tm AS (
        SELECT o.id, o.amount FROM q_src.orders AS o WHERE o.amount IS NOT NULL
    )
    SELECT tm.id, tm.amount FROM tm;

    INSERT INTO q_out.out_b (id, total)
    WITH tm AS (
        SELECT l.id, l.qty * l.price AS amount FROM q_src.order_lines AS l
    )
    SELECT s.id, s.amount FROM (SELECT tm.id, tm.amount FROM tm) AS s;
END;
$$;
"""

ESSENTIALS_SQL = """
CREATE OR REPLACE FUNCTION q_out.essentials(p date)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    r bigint;
BEGIN
    SELECT count(*) INTO r
      FROM q_src.facts AS f
     WHERE f.phone IS NOT NULL
       AND f.status IN ('new', 'ready')
       AND f.day < DATE '2025-07-01'
     ORDER BY f.priority DESC, f.created_at
     LIMIT 5;
    INSERT INTO q_out.out_c (id, v)
    SELECT f.id, CASE WHEN f.k = 1 THEN 10 WHEN f.k = 2 THEN 20 ELSE 0 END
      FROM q_src.facts AS f
     WHERE f.day < to_date('01.07.2025', 'dd.mm.yyyy');
    RETURN r;
END;
$$;
"""


class RetroPairTests(unittest.TestCase):
    """Q-04: parameter -> assignment -> IF guard -> branch operations."""

    @classmethod
    def setUpClass(cls):
        cls.inv = analyze(RETRO_SQL, 'function+q_hist+apply_retro+(character varying)')
        cls.notes = [n['reason'] for n in cls.inv['coverage_notes']]

    def test_no_unsupported_plpgsql_gaps(self):
        self.assertFalse([r for r in self.notes if 'Unsupported PL/pgSQL' in r], self.notes)

    def test_parameter_reaches_assignment_and_guard(self):
        assign = [i for i in items(self.inv, 'ASSIGN')
                  if i['details'].get('assignment_target') == 'v_retro']
        self.assertEqual(len(assign), 1)
        self.assertTrue(any('p_retro' in f for f in assign[0]['details']['formulas']),
                        assign[0]['details'])
        gate = items(self.inv, 'IF')[0]
        self.assertEqual(gate['details']['conditions'], ["v_retro = '1'"])

    def test_retro_pairs_carry_guard_and_history_boundary(self):
        guarded = [i for i in self.inv['items']
                   if i['kind'] in ('DELETE', 'INSERT') and i['details'].get('guards')]
        self.assertEqual(len(guarded), 4)
        for item in guarded:
            self.assertEqual(item['details']['guards'], ["v_retro = '1'"], item)
            self.assertEqual(item['details']['branch'], 'then_body', item)
        targets = {}
        for item in guarded:
            targets.setdefault(item['kind'], set()).update(item.get('writes', []))
        self.assertEqual(targets['DELETE'], targets['INSERT'])
        self.assertEqual(len(targets['DELETE']), 2)
        deletes = [i for i in guarded if i['kind'] == 'DELETE']
        for item in deletes:
            self.assertTrue(any('01.07.2025' in c for c in item['details']['conditions']), item)
            self.assertFalse(any('v_date_start' in c or 'v_date_end' in c
                                for c in item['details']['conditions']), item)

    def test_guard_scopes_are_not_invented_on_unguarded_ops(self):
        unguarded = [i for i in self.inv['items'] if i['kind'] in ('DELETE', 'INSERT')
                     and not i['details'].get('guards')]
        self.assertEqual(unguarded, [])


class TraversalTests(unittest.TestCase):
    """Q-04: GET DIAGNOSTICS / RAISE / EXCEPTION bodies are covered."""

    @classmethod
    def setUpClass(cls):
        cls.inv = analyze(TRAVERSAL_SQL, 'function+q_ops+audit_trail+(integer)')
        cls.notes = [n['reason'] for n in cls.inv['coverage_notes']]

    def test_getdiag_and_raise_are_not_gaps(self):
        self.assertFalse([r for r in self.notes if 'PLpgSQL_stmt_getdiag' in r], self.notes)
        self.assertFalse([r for r in self.notes if 'PLpgSQL_stmt_raise' in r], self.notes)

    def test_raise_records_level_and_message(self):
        levels = {i['details']['raise_level'] for i in items(self.inv, 'RAISE')}
        self.assertEqual(levels, {'NOTICE', 'WARNING'})
        self.assertTrue(all(i['details']['message'] for i in items(self.inv, 'RAISE')))

    def test_exception_handler_body_is_traversed(self):
        handler = [i for i in self.inv['items']
                   if str(i['details'].get('branch', '')).startswith('exception:')]
        kinds = sorted(i['kind'] for i in handler)
        self.assertEqual(kinds, ['PERFORM', 'RAISE'])
        self.assertIn('unique_violation', handler[0]['details']['branch'])
        perform = [i for i in handler if i['kind'] == 'PERFORM'][0]
        self.assertEqual(perform['calls'], ['q_ops.audit_log'])

    def test_trailing_statements_after_getdiag_are_kept(self):
        kinds = [i['kind'] for i in self.inv['items']]
        self.assertIn('DELETE', kinds)
        self.assertIn('UPDATE', kinds)
        self.assertIn('RETURN', kinds)


class CteScopeTests(unittest.TestCase):
    """Q-04: CTE scopes per INSERT and derived aliases are distinct."""

    @classmethod
    def setUpClass(cls):
        cls.inv = analyze(CTE_SQL, 'function+q_out+two_inserts+()')

    def test_same_cte_name_has_two_scopes(self):
        ctes = items(self.inv, 'CTE')
        self.assertEqual([c['details']['name'] for c in ctes], ['tm', 'tm'])
        self.assertEqual(len({c['details']['reference'] for c in ctes}), 2)

    def test_derived_alias_is_not_a_cte(self):
        derived = [d for i in self.inv['items']
                   for d in i['details'].get('derived_aliases', [])]
        self.assertEqual(derived, ['s'])
        self.assertNotIn('s', [c['details']['name'] for c in items(self.inv, 'CTE')])

    def test_physical_reads_go_through_cte_and_subquery(self):
        reads = {r for i in self.inv['items'] for r in i.get('reads', []) if not r.startswith('@')}
        self.assertEqual(reads, {'q_src.orders', 'q_src.order_lines'})


class ExpressionDetailTests(unittest.TestCase):
    """Q-04: NULL polarity, CASE order, IN sets, ORDER direction, date bounds."""

    @classmethod
    def setUpClass(cls):
        cls.inv = analyze(ESSENTIALS_SQL, 'function+q_out+essentials+(date)')
        cls.conditions = [c for i in cls.inv['items'] for c in i['details'].get('conditions', [])]
        cls.formulas = [f for i in cls.inv['items'] for f in i['details'].get('formulas', [])]

    def test_null_polarity_is_preserved(self):
        self.assertTrue(any('f.phone IS NOT NULL' in c for c in self.conditions), self.conditions)
        self.assertFalse(any('f.phone IS NULL' in c and 'NOT' not in c for c in self.conditions))

    def test_in_set_is_exact(self):
        text = self.formulas + self.conditions
        self.assertTrue(any("f.status IN ('new', 'ready')" in x for x in text), text)

    def test_case_branch_order_is_preserved(self):
        case = [f for f in self.formulas if 'CASE' in f]
        self.assertTrue(case, self.formulas)
        self.assertLess(case[0].index('f.k = 1'), case[0].index('f.k = 2'))

    def test_order_direction_and_limit(self):
        select = [i for i in items(self.inv, 'SELECT') if i['details'].get('order_by')][0]
        self.assertEqual(select['details']['order_by'], ['f.priority DESC', 'f.created_at'])
        self.assertEqual(select['details']['limit'], '5')

    def test_date_boundaries_are_flagged(self):
        dated = [i for i in self.inv['items'] if i['details'].get('has_date_boundary')]
        self.assertTrue(dated)
        self.assertTrue(any('2025-07-01' in c for c in self.conditions))


class AcceptanceNumbersTests(unittest.TestCase):
    """Q-04 acceptance numbers on the pinned control SQL (when reachable)."""

    def test_control_sql_numbers(self):
        scenario = json.loads((PACKAGE / 'examples/fixtures/acceptance-large.json')
                              .read_text(encoding='utf-8'))
        root = Path(__import__('os').environ.get('WIKI_DOC_ACCEPTANCE_PROJECT', ''))
        sql = root / scenario['input']['relative_path'] if str(root) != '.' else None
        if not root or not sql or not sql.is_file():
            self.skipTest('control project not configured (WIKI_DOC_ACCEPTANCE_PROJECT)')
        from sql_extract import sha256_file
        digest = sha256_file(sql)
        self.assertEqual(digest, scenario['input']['sha256'],
                         scenario['hash_policy']['on_hash_mismatch'])
        text = sql.read_text(encoding='utf-8-sig')
        inv = extract_inventory(text, 'control.sql', digest, dialect='greenplum',
                                version='unknown', documented_subjects=[
                                    's_gp_p1024_dmr_svd_kb_ckr_uup_gp_core.ckr_uup_db_onboarding'])
        expected = scenario['acceptance_numbers']
        counts = {}
        for item in inv['items']:
            if item['kind'] in ('INSERT', 'DELETE', 'UPDATE'):
                counts[item['kind']] = counts.get(item['kind'], 0) + 1
        self.assertEqual(counts, expected['operations'])
        def identity_hash(rows):
            return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=True,
                                             separators=(',', ':')).encode('utf8')).hexdigest()
        identities = scenario['acceptance_identities']
        dml = [i for i in inv['items'] if i['kind'] in expected['operations']]
        self.assertEqual({i['anchor']['object_or_scope'] for i in dml},
                         {'function+s_gp_p1024_dmr_svd_kb_ckr_uup_gp_core+ckr_uup_db_onboarding+'
                          '(character varying,character varying,character varying,integer)'})
        self.assertEqual(identity_hash([[i['kind'], target, i['source_ref']['start_line']]
                                       for i in dml for target in i.get('writes', [])]),
                         identities['operations_sha256'])
        main = [i for i in items(inv, 'INSERT')
                if any(w.endswith('.ckr_uup_db_onboarding_main') for w in i.get('writes', []))]
        retro = [i for i in main if i['details'].get('guards')]
        self.assertEqual(len(retro), 1)
        self.assertEqual(len(main) - len(retro), expected['main_body']['calculated_insert'])
        reads = {r for i in inv['items'] for r in i.get('reads', []) if not r.startswith('@')}
        self.assertEqual(len(reads), expected['physical_sources']['unique_from_join'])
        self.assertEqual(identity_hash(list(reads)), identities['physical_sources_sha256'])
        guarded = [i for i in inv['items'] if i['details'].get('guards')]
        self.assertTrue(all("v_retro = '1'" in i['details']['guards'] for i in guarded))
        retro_ops = [i for i in guarded if any('v_retro' in x for x in i['details']['guards'])]
        retro_deletes = [i for i in retro_ops if i['kind'] == 'DELETE']
        retro_inserts = [i for i in retro_ops if i['kind'] == 'INSERT']
        self.assertEqual(len(retro_deletes), expected['retro_pairs']['expected'])
        self.assertEqual(len(retro_inserts), expected['retro_pairs']['expected'])
        del_targets = {tuple(sorted(i.get('writes', []))) for i in retro_deletes}
        ins_targets = {tuple(sorted(i.get('writes', []))) for i in retro_inserts}
        self.assertEqual(del_targets, ins_targets)
        self.assertEqual({target.rsplit('.', 1)[-1] for targets in del_targets for target in targets},
                         {'ckr_uup_db_onboarding_main', 'ckr_uup_db_onboarding_new_clients',
                          'ckr_uup_db_onboarding_meet_tasks'})
        for d in retro_deletes:
            conds = d['details'].get('conditions', [])
            self.assertTrue(any('01.07.2025' in c for c in conds), (d['source_ref'], conds))
            self.assertFalse(any('v_date_start' in c or 'v_date_end' in c
                                 for c in conds), (d['source_ref'], conds))
        for ins in retro_inserts:
            same_line = [i for i in inv['items']
                         if i['source_ref']['start_line'] == ins['source_ref']['start_line']
                         and i['kind'] == 'SELECT']
            source_conds = [c for i in same_line for c in i['details'].get('conditions', [])]
            boundary_conds = [c for c in source_conds if '01.07.2025' in c]
            self.assertTrue(boundary_conds, (ins['source_ref'], [i['details'].get('conditions', [])
                                                                  for i in same_line]))
            self.assertFalse(any('v_date_start' in c or 'v_date_end' in c for c in source_conds))
        pairs = set()
        str_lit = re.compile(r"'([^']*)'")
        int_lit = re.compile(r'^\s*(?:CAST\()?\s*(\d+)\s*\)?\s*$')

        def literal(expr, kind):
            expr = (expr or '').strip()
            if kind == 'value_name' and re.match(r"^(?:CAST\()?\s*'", expr):
                m = str_lit.search(expr)
                return m.group(1) if m else None
            if kind == 'structure':
                m = int_lit.match(expr)
                return m.group(1) if m else None
            return None
        for ins in main:
            if ins['details'].get('guards'):
                continue
            line = ins['source_ref']['start_line']
            literals = {}
            for j in inv['items']:
                if j is ins or j['source_ref']['start_line'] != line:
                    continue
                for c in (j['details'].get('columns') or []):
                    if c.get('name') not in ('value_name', 'structure'):
                        continue
                    value = literal(c.get('expression'), c['name'])
                    if value is not None:
                        literals.setdefault(c['name'], set()).add(value)
            names = literals.get('value_name', set())
            structs = literals.get('structure', set())
            self.assertEqual(len(names), 1, (line, names))
            self.assertEqual(len(structs), 1, (line, structs))
            pairs.add((names.pop(), structs.pop()))
        self.assertEqual(len(pairs), expected['kpi']['kpi_structure_pairs'])
        self.assertEqual(len({p[0] for p in pairs}), expected['kpi']['unique_kpi'])
        self.assertEqual(identity_hash([list(p) for p in pairs]), identities['kpi_pairs_sha256'])
        # The handler is after all KPI inserts; counting DML cannot detect its loss.
        handlers = [i for i in inv['items'] if 'exception:' in i['details'].get('branch', '')]
        handler_calls = {call.rsplit('.', 1)[-1] for i in handlers for call in i.get('calls', [])}
        self.assertEqual(handler_calls, {'init_type_oper', 'start_oper', 'add_log_add', 'add_log', 'end_oper'})
        self.assertEqual(sum(any(c.endswith('.add_log_add') for c in i.get('calls', []))
                             for i in inv['items']), 164)
        self.assertEqual(len([i for i in handlers if i['details'].get('diagnostic', {}).get('stacked')]), 4)
        from check_policy import load_policy
        from validation_plan import generate_plan
        plan = generate_plan(inv, load_policy())
        gaps = [c for c in plan['required_checks'] if c['rule_id'] == 'analysis_gap']
        self.assertEqual(len(gaps), len(inv['coverage_notes']))
        # Q-04 is not complete: its later wildcard pass added 72 unresolved
        # CTE/derived projections. Named date contracts removed nine call gaps,
        # but did not resolve those projections. This is a blocking baseline,
        # not acceptance of complete analysis of the control object.
        from collections import Counter
        self.assertEqual(Counter(n['reason'] for n in inv['coverage_notes']), {
            'Wildcard output columns require DDL expansion': 72,
            'Exception handlers are inventoried as branch ops; runtime failure point is not analysed': 1,
        })
        self.assertEqual(len(gaps), 73)
        self.assertTrue(all(c['blocking'] for c in gaps))


if __name__ == '__main__':
    unittest.main()
