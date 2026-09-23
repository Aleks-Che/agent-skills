"""Q-01: independent expectations for the Q-fixture set.

Verifies, without consulting any writer output, that
  1. the hand-authored oracles in examples/expected/qNN agree with the source
     SQL on the mechanically checkable facts (operations, reads, writes, calls),
  2. every structured assertion in examples/expected/qNN/assertions.json
     actually holds in the fixture SQL / its DDL context,
  3. the known extractor crash is reproduced deterministically (q05) and, after
     Q-02, is replaced by diagnostics instead of exit code 2.

The expectations are authored in examples/expected/_authoring.py from reading
the SQL by hand. This module only checks them; it never regenerates them.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / 'scripts'
EXAMPLES = PACKAGE / 'examples'
FIXTURES = EXAMPLES / 'fixtures'
EXPECTED = EXAMPLES / 'expected'
sys.path.insert(0, str(SCRIPTS))

from sql_extract import extract_inventory, sha256_file  # noqa: E402
from run_regression import isolate
from ddl import reconstruct

Q_IDS = [f'q{i:02d}' for i in range(1, 12)]


def comment_free(text):
    """Blank -- and /* */ comments, keeping offsets so span checks stay valid."""
    out, i = list(text), 0
    while i < len(text):
        if text.startswith('--', i):
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            out[i:end] = ' ' * (end - i)
            i = end
        elif text.startswith('/*', i):
            depth, j = 1, i + 2
            while j < len(text) and depth:
                if text.startswith('/*', j):
                    depth, j = depth + 1, j + 2
                elif text.startswith('*/', j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out[i:j] = ' ' * (j - i)
            i = j
        else:
            i += 1
    return ''.join(out)


def load_case(case_id):
    manifest = json.loads((EXAMPLES / 'cases-q.json').read_text(encoding='utf-8'))
    return next(c for c in manifest['cases'] if c['id'] == case_id)


def load_expected(case_id, name):
    return json.loads((EXPECTED / case_id / name).read_text(encoding='utf-8'))


def fixture_text(case_id):
    case = load_case(case_id)
    return (EXAMPLES / case['sql']).read_text(encoding='utf-8-sig')


def context_text(case_id):
    case = load_case(case_id)
    return '\n'.join((EXAMPLES / path).read_text(encoding='utf-8-sig')
                     for path in case['context'])


def inventory_of(case_id):
    case = load_case(case_id)
    path = EXAMPLES / case['sql']
    text = path.read_text(encoding='utf-8-sig')
    return extract_inventory(text, case['sql'], sha256_file(path),
                             dialect=case.get('dialect', 'postgres'),
                             version=case.get('version', 'unknown'),
                             documented_subjects=case['subjects'])


def flattened(inventory, field):
    values = set()
    for item in inventory['items']:
        values.update(item.get(field, []))
    return {v for v in values if not v.startswith('@')}


class ManifestTests(unittest.TestCase):
    def test_every_q_case_has_sql_context_and_expectations(self):
        manifest = json.loads((EXAMPLES / 'cases-q.json').read_text(encoding='utf-8'))
        self.assertEqual([c['id'] for c in manifest['cases']], Q_IDS)
        for case in manifest['cases']:
            self.assertTrue((EXAMPLES / case['sql']).is_file(), case['id'])
            for path in case['context']:
                self.assertTrue((EXAMPLES / path).is_file(), f"{case['id']} {path}")
            for name in ('facts.json', 'checks.json', 'decision.json',
                         'page_assertions.json', 'assertions.json'):
                self.assertTrue((EXPECTED / case['id'] / name).is_file(),
                                f"{case['id']} {name}")

    def test_expectations_are_not_reachable_from_adapter_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            for case_id in Q_IDS:
                with self.subTest(case=case_id):
                    case = load_case(case_id)
                    workspace = Path(directory) / case_id
                    package, project, output = isolate(case, workspace, EXAMPLES)
                    expected_inputs = {case['sql'], *case['context']}
                    if case['migration_manifest']:
                        manifest = EXAMPLES / case['migration_manifest']
                        expected_inputs.add(case['migration_manifest'])
                        for relative in json.loads(manifest.read_text())['ordered_files']:
                            expected_inputs.add((manifest.parent / relative).resolve()
                                                .relative_to(EXAMPLES).as_posix())
                    self.assertEqual({p.relative_to(project).as_posix()
                                      for p in project.rglob('*') if p.is_file()}, expected_inputs)
                    self.assertFalse((package / 'examples').exists())
                    self.assertEqual(list(output.iterdir()), [])
                    self.assertFalse(any(p.name in {'expected', '_authoring.py', 'assertions.json',
                                                   'page_assertions.json', 'acceptance-large.json'}
                                         for p in workspace.rglob('*')))
                    request = json.loads((workspace / 'request.json').read_text())
                    self.assertNotIn('review_ids', request)
                    self.assertNotIn('expectation_mode', request)

    def test_acceptance_scenario_pins_input_hash(self):
        scenario = json.loads((FIXTURES / 'acceptance-large.json').read_text(encoding='utf-8'))
        self.assertEqual(scenario['input']['sha256'],
                         '168dc68069a244fa957931d092b93c44a8f01a0b0b3bd3538a6de507cb4941c3')
        self.assertEqual(scenario['hash_policy']['on_hash_mismatch'], 'refuse_to_apply_numbers')
        self.assertEqual(scenario['acceptance_numbers']['operations'],
                         {'INSERT': 77, 'DELETE': 77, 'UPDATE': 1})
        self.assertEqual(scenario['acceptance_numbers']['kpi']['unique_kpi'], 36)
        self.assertEqual(scenario['acceptance_numbers']['kpi']['kpi_structure_pairs'], 68)
        self.assertEqual(scenario['acceptance_numbers']['physical_sources']['unique_from_join'], 30)
        self.assertEqual(scenario['acceptance_numbers']['retro_pairs']['expected'], 3)
        self.assertEqual(len(scenario['known_defects_in_reviewed_page']), 12)
        self.assertFalse(scenario['expected_gate']['publication_authorized'])


class OracleConsistencyTests(unittest.TestCase):
    """Hand-authored oracle must agree with the SQL on mechanical facts."""

    def check_case(self, case_id):
        expectation = load_expected(case_id, 'facts.json')['subjects']
        self.assertEqual(len(expectation), 1)
        subject, oracle = next(iter(expectation.items()))
        self.assertEqual(load_case(case_id)['subjects'], [subject])
        inventory = inventory_of(case_id)
        operations = Counter(i['kind'] for i in inventory['items']
                             if i['kind'] != 'DECLARATION')
        if 'operations' in oracle:
            self.assertEqual(dict(operations), oracle['operations'],
                             f'{case_id} operation occurrence counts')
        for field in ('reads', 'writes', 'calls'):
            if field in oracle:
                self.assertEqual(flattened(inventory, field), set(oracle[field]),
                                 f'{case_id} {field} physical dependencies')

    def test_q01(self):
        self.check_case('q01')

    def test_q02(self):
        self.check_case('q02')

    def test_q03(self):
        self.check_case('q03')

    def test_q04(self):
        self.check_case('q04')

    def test_q06(self):
        self.check_case('q06')

    def test_q07(self):
        self.check_case('q07')

    def test_q08(self):
        self.check_case('q08')

    def test_q09(self):
        self.check_case('q09')

    def test_q10(self):
        self.check_case('q10')

    def test_q11(self):
        self.check_case('q11')

    def test_positive_controls_are_not_all_blocked(self):
        """'Always blocked' must not pass as an improvement (plan section 5)."""
        ready = [c for c in Q_IDS if load_expected(c, 'decision.json')['decision'] == 'ready']
        self.assertGreaterEqual(len(ready), 10, ready)

    def test_negative_control_q05_stays_blocked(self):
        self.assertEqual(load_expected('q05', 'decision.json')['decision'], 'blocked')


class AssertionChecks(unittest.TestCase):
    """Each structured assertion holds in the fixture SQL / DDL context."""

    def iter_assertions(self, case_id):
        payload = load_expected(case_id, 'assertions.json')
        self.assertEqual(payload['authoring'],
                         'hand-derived from source SQL, independent of extractor and writer')
        for assertion in payload['assertions']:
            self.assertEqual(assertion['severity'], 'blocking', assertion['id'])
            self.assertTrue(assertion['claim'], assertion['id'])
            yield assertion

    def verify(self, case_id, handler):
        checked = []
        text = comment_free(fixture_text(case_id))
        for assertion in self.iter_assertions(case_id):
            handler(text, assertion)
            checked.append(assertion['id'])
        self.assertTrue(checked, case_id)
        return checked

    @staticmethod
    def _require(text, fragment, assertion):
        if fragment not in text:
            raise AssertionError(f"{assertion['id']}: missing {fragment!r}")

    @staticmethod
    def _forbid(text, fragment, assertion):
        if fragment in text:
            raise AssertionError(f"{assertion['id']}: forbidden {fragment!r}")

    def test_q01_retro(self):
        def handle(text, a):
            if a['kind'] == 'parameter_reaches_condition':
                self._require(text, f"{a['local']} boolean := {a['parameter']}", a)
                self._require(text, f"IF {a['condition']} THEN", a)
                self._forbid(text, 'p_retro unused', a)
            elif a['kind'] == 'retro_operation_pair':
                for kind, target in a['pair']:
                    self._require(text, f'{kind} INTO {target}' if kind == 'INSERT'
                                  else f'{kind} FROM {target}', a)
                self._require(text, 'IF v_retro THEN', a)
            elif a['kind'] == 'history_boundary':
                for bound in a['bound_to']:
                    self._require(text, f"{bound} < DATE '{a['literal']}'", a)
                for unbound in a['not_bound_to']:
                    self._forbid(text, f'{unbound} < DATE', a)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q01', handle)), 3)

    def test_q02_null_polarity(self):
        def handle(text, a):
            self.assertEqual(a['kind'], 'null_check_polarity')
            self._require(text, a['expression'], a)
            self.assertIn(a['polarity'], a['expression'])
            for wrong in a['rejected']:
                self._forbid(text, wrong, a)
        self.assertEqual(len(self.verify('q02', handle)), 2)

    def test_q03_status_sets_and_order(self):
        def handle(text, a):
            if a['kind'] == 'in_list_set':
                self._require(text, a['expression_contains'] + ' AS ' + a['flag'], a)
                for member in a['members']:
                    self.assertIn(f"'{member}'", a['expression_contains'])
                self.assertEqual(len(a['members']), a['member_count'])
                for wrong in a['rejected']:
                    self.assertNotIn(f"'{wrong}'", a['expression_contains'])
            elif a['kind'] == 'row_choice_order':
                self._require(text, 'ORDER BY t.priority DESC, t.created_at', a)
                self._require(text, f"LIMIT {a['limit']}", a)
            elif a['kind'] == 'counterexample':
                self._require(text, 'ORDER BY t.priority DESC, t.created_at', a)
                winner = sorted(a['rows'], key=lambda row: (-row['priority'], row['created_at']))[0]
                earliest = min(a['rows'], key=lambda row: row['created_at'])
                self.assertEqual(winner['task_id'], a['selected_task_id'])
                self.assertEqual(earliest['task_id'], a['earliest_task_id'])
                self.assertNotEqual(winner['task_id'], earliest['task_id'])
                sets = {item['flag']: set(item['members'])
                        for item in self.iter_assertions('q03') if item['kind'] == 'in_list_set'}
                self.assertEqual({flag: winner['status'] in members for flag, members in sets.items()},
                                 a['selected_flags'])
                self._require(text, 'is_done = v_is_done, is_phoned = v_is_phoned', a)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q03', handle)), 4)

    def test_q04_date_branch(self):
        def handle(text, a):
            if a['kind'] == 'date_branch_boundary':
                self._require(text, f"IF {a['condition']} THEN", a)
                for bound in a['bound_to']:
                    self.assertIn(bound, text)
                for unbound in a['not_bound_to']:
                    self._forbid(text, unbound, a)
            elif a['kind'] == 'branch_formula_pair':
                self._require(text, "IF p_date >= DATE '2026-01-01' THEN", a)
                self._require(text, 'ELSE', a)
                for branch in a['branches']:
                    self._require(text, branch['formula'], a)
                divisors = {branch['formula'] for branch in a['branches']}
                self.assertEqual(len(divisors), 2, 'the two branches must differ')
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q04', handle)), 2)

    def test_q05_master_attribute_and_nesting(self):
        def handle(text, a):
            if a['kind'] == 'declaration_attribute':
                self._require(text, a['attribute'], a)
                self.assertIn('$$ LANGUAGE plpgsql EXECUTE ON MASTER;', text)
                self.assertTrue(text.index('$$') < text.index('EXECUTE ON MASTER'))
            elif a['kind'] == 'attribute_not_dynamic_execute':
                body = text.split('AS $$', 1)[1].rsplit('$$', 1)[0]
                self._forbid(body, 'EXECUTE ', a)
            elif a['kind'] == 'nested_source_preserved':
                for ref in a['physical_reads']:
                    self._require(text, ref, a)
                self.assertEqual(len(re.findall(r'FROM\s*\(', text)), a['derived_tables_min'])
            elif a['kind'] == 'no_crash':
                self.assertTrue(a['forbid_exit_codes'])
                self.assertIn('Unbalanced SQL list', a['forbid_error_text'])
            elif a['kind'] == 'unverified_compatibility':
                self.assertEqual(load_case('q05')['version'], a['version'])
                self.assertEqual(a['version'], 'unknown')
                self.assertEqual(load_case('q05')['dialect'], a['dialect'])
                self.assertEqual(a['confirmed_minimum_versions'], {})
                self.assertEqual(a['rejected'], ['PostgreSQL >=9.4', 'Greenplum >=5'])
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q05', handle)), 5)

    def test_q06_cte_scopes(self):
        def handle(text, a):
            if a['kind'] == 'cte_vs_derived':
                self._require(text, 'WITH tm AS (', a)
                self._require(text, ') AS s;', a)
                self.assertEqual(a['derived_alias_not_cte'], ['s'])
                self.assertEqual(a['cte_names'], ['tm'])
            elif a['kind'] == 'cte_scopes':
                self.assertEqual(text.count('WITH tm AS ('), a['scope_count'])
                for scope_reads in a['per_scope_reads']:
                    self.assertTrue(scope_reads)
                    self._require(text, scope_reads[0], a)
                self.assertNotEqual(a['per_scope_reads'][0], a['per_scope_reads'][1])
            elif a['kind'] == 'graph_edges':
                self._require(text, 'INSERT INTO q_out.out_a', a)
                self._require(text, 'INSERT INTO q_out.out_b', a)
                self._require(text, 'FROM q_src.orders', a)
                self._require(text, 'FROM q_src.order_lines', a)
            elif a['kind'] == 'level_formulas':
                for formula in a['formulas_by_scope'].values():
                    self._require(text, formula, a)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q06', handle)), 4)

    def test_q07_xml_filters(self):
        def handle(text, a):
            if a['kind'] == 'xml_aggregate_filters':
                self.assertEqual(text.lower().count('xmlagg('), a['aggregate_count'])
                self._require(text, f'name "{a["element_name"]}"', a)
                self._require(text, f'name "{a["child_name"]}"', a)
            elif a['kind'] == 'metadata_calls_not_filters':
                selects = re.findall(r'd\.field_name = \'(\w+)\'', text)
                self.assertEqual(len(selects), a['metadata_select_count'])
                self.assertEqual(selects, a['metadata_fields'])
                self.assertEqual(a['must_not_count_as'], 'filters')
            elif a['kind'] == 'reference_source_present':
                self._require(text, f"FROM {a['source']}", a)
                self.assertEqual(a['on_missing'], 'plan_gap_blocks')
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q07', handle)), 3)

    def test_q08_positional_insert(self):
        state = reconstruct(FIXTURES / 'migrations/manifest.json', project_root=EXAMPLES)
        self.assertEqual(state['status'], 'resolved', state)
        columns = state['tables']['q_out.orders']['columns']
        by_name = {column['name']: column for column in columns}
        def handle(text, a):
            if a['kind'] == 'positional_insert_columns':
                self._require(text, f"INSERT INTO {a['target']}\n", a)
                self._forbid(text, f"INSERT INTO {a['target']} (", a)
                self.assertEqual(len(a['post_migration_columns']), a['column_count'])
                self.assertEqual(len(a['pre_migration_columns']), a['column_count'] - 1)
                self.assertEqual([column['name'] for column in columns], a['post_migration_columns'])
                self._require(text, 'SELECT ' + ', '.join('s.' + name for name in a['post_migration_columns']), a)
            elif a['kind'] == 'column_types':
                for column in a['columns']:
                    self.assertEqual(by_name[column['name']]['type'], column['type_target'])
                self.assertEqual(by_name['amount']['default'], '0')
                self.assertEqual(by_name['amount']['comment'], 'Order amount in account currency')
            elif a['kind'] == 'migration_state':
                manifest = json.loads((FIXTURES / 'migrations' / 'manifest.json')
                                      .read_text(encoding='utf-8'))
                self.assertEqual(manifest['target_revision'], a['target_revision'])
                self.assertEqual(manifest['ordered_files'], a['ordered_files'])
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q08', handle)), 3)

    def test_q09_kpi_pairs_and_levels(self):
        text = comment_free(fixture_text('q09'))
        branches = re.findall(
            r'SELECT f.kpi_id, f.struct_id, (\d+), f.value_num, ([^\n]+)\s+'
            r'FROM q_src.kpi_facts AS f\s+'
            r'WHERE f.kpi_id = (\d+) AND f.struct_id = (\d+) AND f.level_no = (\d+);', text)
        self.assertEqual(len(branches), len(re.findall(r'\bINSERT INTO\b', text)))
        actual_levels, actual_formulas = {}, {}
        for output_level, formula, kpi, struct, filter_level in branches:
            self.assertEqual(output_level, filter_level)
            actual_levels.setdefault(f'{kpi}/{struct}', []).append(int(filter_level))
            actual_formulas[f'{kpi}/{struct}/{filter_level}'] = formula.strip()
        def handle(text, a):
            if a['kind'] == 'kpi_structure_pairs':
                self.assertEqual(len(a['pairs']), a['pair_count'])
                self.assertEqual(set(actual_levels), {f"{p['kpi_id']}/{p['struct_id']}" for p in a['pairs']})
            elif a['kind'] == 'levels_per_pair':
                self.assertEqual(len(a['levels_by_pair']), a['pair_count'])
                self.assertEqual(actual_levels, a['levels_by_pair'])
                self.assertEqual(sum(len(v) for v in a['levels_by_pair'].values()),
                                 a['calculation_branches'])
                self.assertGreater(len({len(levels) for levels in actual_levels.values()}), 1)
                self.assertIn('every KPI', a['rejected_claim'])
            elif a['kind'] == 'level_formulas':
                self.assertEqual(actual_formulas, a['formulas_by_pair_level'])
                self.assertEqual(len(set(a['formulas_by_pair_level'].values())), 3)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q09', handle)), 3)

    def test_q10_hours_and_plan(self):
        def handle(text, a):
            if a['kind'] == 'hour_normalization':
                self._require(text, a['formula'], a)
                for wrong in a['rejected']:
                    self._forbid(text, f'{wrong}\n', a)
            elif a['kind'] == 'plan_selection':
                self._require(text, 'ORDER BY p.priority DESC, p.weight DESC', a)
                self._require(text, f"LIMIT {a['limit']}", a)
            elif a['kind'] == 'essential_sources':
                self.assertEqual(a['on_missing'], 'plan_gap_blocks')
                for source in a['sources']:
                    self._require(text, f'FROM {source}', a)
                for condition in a['essential_conditions']:
                    self._require(text, condition, a)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q10', handle)), 3)

    def test_q11_rr_sl_and_unknown(self):
        def handle(text, a):
            if a['kind'] == 'rr_formula':
                self._require(text, 'FILTER (WHERE', a)
                self._require(text, '/ count(*)::numeric', a)
                self._require(text, 'ELSE 0 END', a)
                for wrong in a['rejected']:
                    self._forbid(text, wrong, a)
            elif a['kind'] == 'sl_formula':
                self._require(text, '1 - CASE WHEN', a)
                self._require(text, '/ count(*)::numeric', a)
            elif a['kind'] == 'honest_unknown':
                self.assertEqual(a['decoded'], False)
                self._require(text, 'rr_value', a)
                for invented in a['rejected']:
                    self._forbid(text, invented, a)
            else:
                raise AssertionError(f"unhandled assertion kind {a['kind']}")
        self.assertEqual(len(self.verify('q11', handle)), 3)

    def test_all_declared_assertion_ids_are_unique_and_cover_d_matrix(self):
        seen, reviews = set(), set()
        for case_id in Q_IDS:
            for assertion in self.iter_assertions(case_id):
                self.assertNotIn(assertion['id'], seen, assertion['id'])
                seen.add(assertion['id'])
                reviews.add(assertion['review'])
        self.assertGreaterEqual(len(seen), 30)
        # D01..D12 must each appear at least once across the Q-set.
        for index in range(1, 13):
            self.assertIn(f'D{index:02d}', reviews)
        self.assertIn('Q-02', reviews)

    def test_each_case_declares_at_least_one_mutation(self):
        for case_id in Q_IDS:
            mutations = load_expected(case_id, 'page_assertions.json')['mutations']
            self.assertTrue(mutations, case_id)
            for mutation in mutations:
                self.assertIn(mutation['group'],
                              ('objects', 'definitions', 'operations', 'columns',
                               'formulas', 'conditions', 'unknowns'))
                self.assertTrue(mutation['id'])


class CrashReproductionTests(unittest.TestCase):
    """Q-01 acceptance / Q-02 boundary: the historical crash is on record."""

    def run_cli(self, case_id, extra=()):
        case = load_case(case_id)
        command = [sys.executable, '-B', str(SCRIPTS / 'sql_extract.py'),
                   str(EXAMPLES / case['sql']),
                   '--dialect', case.get('dialect', 'postgres'),
                   '--version', case.get('version', 'unknown'), *extra]
        return subprocess.run(command, cwd=str(PACKAGE), capture_output=True,
                              text=True, timeout=120)

    def test_q05_must_not_end_in_undiagnosed_crash(self):
        """Q-02 acceptance: diagnostics replace exit code 2 'Unbalanced SQL list'."""
        completed = self.run_cli('q05')
        self.assertNotEqual(completed.stderr.strip(), '{"error": "Unbalanced SQL list"}')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        reasons = ' | '.join(n['reason'] for n in payload['coverage_notes'])
        self.assertTrue(payload['coverage_notes'], 'blocked reason required')
        self.assertRegex(reasons, r'(?i)(unsupported dialect|analysis failed|greenplum|master)')
        self.assertFalse(payload.get('documented_subjects') == [])

    def test_postive_control_cli_stays_clean(self):
        completed = self.run_cli('q02')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['dialect']['name'], 'postgres')
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['coverage_notes'], [])


if __name__ == '__main__':
    unittest.main()
