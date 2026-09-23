"""Author and emit independent Q-01 expectations for examples/expected/qNN.

Every literal below is derived by reading the fixture SQL in examples/fixtures
by hand, NOT by running the extractor under test or any writer. This file only
serialises those hand-authored constants so the JSON files stay deterministic
and the derivation stays reviewable.

It is AUTHORING TOOLING, never a generator input: scripts/run_regression.py
isolate() copies only case SQL/context and the skill runtime into an adapter
workspace, never examples/expected/**. Verified by
tests/test_q01_expectations.py::ManifestTests.

Run:  python -B examples/expected/_authoring.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

# Hand-authored, source-faithful condition/formula strings. Where the pglast
# deparser rewrites DATE 'x' to CAST('x' AS date), the SOURCE form is kept as
# truth; the delta is recorded in `deparse_deltas` and must be reconciled by
# Q-07 rather than silently normalised here.
CASES = {
    'q01': {
        'subject': 'function+q_hist+apply_retro+(boolean)',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'formula', 'condition',
                           'date_boundary', 'section', 'analysis_gap'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [{'name': 'p_retro', 'type': 'boolean', 'mode': 'd', 'default': None}],
                'volatility': 'volatile',
            },
            'operations': {'IF': 1, 'DELETE': 1, 'INSERT': 1, 'SELECT': 1, 'ASSIGN': 1, 'RETURN': 1},
            'reads': ['q_src.kpi_facts'],
            'writes': ['q_hist.retro_pairs'],
            'calls': [],
            'conditions': [
                'v_retro',
                "r.fact_start_date < DATE '2026-01-01'",
                "k.fact_start_date < DATE '2026-01-01'",
            ],
            'definitions': [{'object': 'q_hist.retro_pairs', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [
            "pipeline deparse emits CAST('2026-01-01' AS date) for the DELETE/SELECT bounds; source form kept as oracle truth",
        ],
        'assertions': [
            {'id': 'q01-A1', 'review': 'D01', 'kind': 'parameter_reaches_condition',
             'parameter': 'p_retro', 'local': 'v_retro', 'condition': 'v_retro',
             'severity': 'blocking',
             'claim': 'p_retro is assigned to v_retro and v_retro is the IF gate; p_retro is NOT unused'},
            {'id': 'q01-A2', 'review': 'D01', 'kind': 'retro_operation_pair',
             'branch': 'then', 'pair': [['DELETE', 'q_hist.retro_pairs'], ['INSERT', 'q_hist.retro_pairs']],
             'severity': 'blocking',
             'claim': 'the gated branch contains a historical DELETE/INSERT pair on the same target'},
            {'id': 'q01-A3', 'review': 'D01', 'kind': 'history_boundary',
             'column': 'fact_start_date', 'op': '<', 'literal': '2026-01-01',
             'bound_to': ['r.fact_start_date', 'k.fact_start_date'], 'not_bound_to': ['create_date'],
             'severity': 'blocking',
             'claim': 'the history window is fact_start_date < 2026-01-01 on both sides of the pair and is not limited to the call period'},
        ],
        'mutations': [
            {'group': 'conditions', 'selector': {'expression': 'v_retro'},
             'field': 'expression', 'value': 'true', 'id': 'D01-drop-retro-gate'},
        ],
    },
    'q02': {
        'subject': 'function+q_out+count_phoned_clients+()',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'formula', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [],
                'volatility': 'volatile',
            },
            'operations': {'SELECT': 1, 'RETURN': 1},
            'reads': ['q_src.clients'],
            'writes': [],
            'calls': [],
            'conditions': ['c.phone IS NOT NULL AND c.is_phoned IS NOT TRUE'],
            'formulas': ['count(*)'],
            'definitions': [{'object': 'q_src.clients', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q02-A1', 'review': 'D02', 'kind': 'null_check_polarity',
             'expression': 'c.phone IS NOT NULL', 'polarity': 'IS NOT NULL',
             'rejected': ['c.phone IS NULL', 'phone IS NULL'],
             'severity': 'blocking',
             'claim': 'the NULL check on phone is IS NOT NULL; flipping it in text or in facts+text is rejected'},
            {'id': 'q02-A2', 'review': 'D02', 'kind': 'null_check_polarity',
             'expression': 'c.is_phoned IS NOT TRUE', 'polarity': 'IS NOT TRUE',
             'rejected': ['c.is_phoned IS TRUE', 'c.is_phoned IS NULL'],
             'severity': 'blocking',
             'claim': 'is_phoned is filtered with IS NOT TRUE, a third polarity distinct from IS NULL'},
        ],
        'mutations': [
            {'group': 'conditions', 'selector': {'expression': 'c.phone IS NOT NULL AND c.is_phoned IS NOT TRUE'},
             'field': 'expression', 'value': 'c.phone IS NULL AND c.is_phoned IS NOT TRUE',
             'id': 'D02-flip-phone-null'},
            {'group': 'conditions', 'selector': {'expression': 'c.phone IS NOT NULL AND c.is_phoned IS NOT TRUE'},
             'field': 'expression', 'value': 'c.phone IS NOT NULL AND c.is_phoned IS TRUE',
             'id': 'D02-flip-is-phoned'},
        ],
    },
    'q03': {
        'subject': 'function+q_out+pick_next_task+(bigint)',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [{'name': 'p_uid', 'type': 'bigint', 'mode': 'd', 'default': None}],
                'volatility': 'volatile',
            },
            'operations': {'SELECT': 1, 'UPDATE': 1, 'RETURN': 1},
            'reads': ['q_src.task_queue'],
            'writes': ['q_src.task_queue'],
            'calls': [],
            'conditions': [
                't.user_id = p_uid AND t.is_done IS NOT TRUE',
                'u.task_id = v_task',
            ],
            'formulas': [
                "t.status IN ('new', 'ready', 'assigned', 'in_progress', 'waiting')",
                "t.status IN ('ready', 'in_progress', 'waiting')",
            ],
            'definitions': [{'object': 'q_src.task_queue', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q03-A1', 'review': 'D03', 'kind': 'in_list_set',
             'expression_contains': "t.status IN ('new', 'ready', 'assigned', 'in_progress', 'waiting')",
             'flag': 'is_done',
             'members': ['new', 'ready', 'assigned', 'in_progress', 'waiting'], 'member_count': 5,
             'rejected': ['done', 'cancelled', 'archived'],
             'severity': 'blocking',
             'claim': 'is_done uses five codes, independently from the three-code is_phoned calculation'},
            {'id': 'q03-A2', 'review': 'D03', 'kind': 'in_list_set',
             'expression_contains': "t.status IN ('ready', 'in_progress', 'waiting')",
             'flag': 'is_phoned',
             'members': ['ready', 'in_progress', 'waiting'], 'member_count': 3,
             'rejected': ['new', 'assigned'],
             'severity': 'blocking',
             'claim': 'is_phoned uses three codes; status assigned makes is_done true and is_phoned false'},
            {'id': 'q03-A3', 'review': 'D03', 'kind': 'row_choice_order',
             'order_by': ['t.priority DESC', 't.created_at'], 'limit': 1,
             'severity': 'blocking',
             'claim': 'row choice is priority DESC with created_at tie-break and LIMIT 1'},
            {'id': 'q03-A4', 'review': 'D03', 'kind': 'counterexample',
             'rows': [
                 {'task_id': 1, 'priority': 1, 'created_at': '2026-01-01', 'status': 'ready'},
                 {'task_id': 2, 'priority': 2, 'created_at': '2026-01-02', 'status': 'assigned'},
             ],
             'selected_task_id': 2, 'earliest_task_id': 1,
             'selected_flags': {'is_done': True, 'is_phoned': False},
             'severity': 'blocking',
             'claim': 'the set/order claims are checked against a two-task counterexample, not only the happy path'},
        ],
        'mutations': [
            {'group': 'formulas',
             'selector': {'expression': "t.status IN ('new', 'ready', 'assigned', 'in_progress', 'waiting')"},
             'field': 'expression',
             'value': "t.status IN ('ready', 'in_progress', 'waiting')",
             'id': 'D03-swap-status-sets'},
        ],
    },
    'q04': {
        'subject': 'function+q_out+calc_norm+(bigint,date)',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'formula', 'condition', 'date_boundary', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'numeric',
                'parameters': [
                    {'name': 'p_uid', 'type': 'bigint', 'mode': 'd', 'default': None},
                    {'name': 'p_date', 'type': 'date', 'mode': 'd', 'default': None},
                ],
                'volatility': 'volatile',
            },
            'operations': {'IF': 1, 'SELECT': 2, 'RETURN': 1},
            'reads': ['q_src.hours'],
            'writes': [],
            'calls': [],
            'conditions': [
                "p_date >= DATE '2026-01-01'",
                'h.user_id = p_uid AND h.on_date = p_date',
            ],
            'formulas': ['h.hours / 8.0', 'h.hours / 7.2', 'COALESCE(v_norm, 0)'],
            'definitions': [{'object': 'q_src.hours', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q04-A1', 'review': 'D04', 'kind': 'date_branch_boundary',
             'condition': "p_date >= DATE '2026-01-01'", 'literal': '2026-01-01',
             'bound_to': ['p_date', 'h.on_date'], 'not_bound_to': ['create_date', 'h.create_date'],
             'severity': 'blocking',
             'claim': 'the formula transition is keyed on p_date/on_date against 2026-01-01, not on create_date'},
            {'id': 'q04-A2', 'review': 'D04', 'kind': 'branch_formula_pair',
             'branches': [
                 {'when': "p_date >= DATE '2026-01-01'", 'formula': 'h.hours / 8.0'},
                 {'when': 'else', 'formula': 'h.hours / 7.2'},
             ],
             'severity': 'blocking',
             'claim': 'both date branches carry their own divisor; dropping >=2026-01-01 or unifying divisors is a defect tied to both KPI structures'},
        ],
        'mutations': [
            {'group': 'conditions', 'selector': {'expression': "p_date >= DATE '2026-01-01'"},
             'field': 'expression', 'value': 'h.create_date >= DATE \'2026-01-01\'',
             'id': 'D04-rebind-to-create-date'},
            {'group': 'formulas', 'selector': {'expression': 'h.hours / 7.2'},
             'field': 'expression', 'value': 'h.hours / 8.0',
             'id': 'D04-collapse-legacy-divisor'},
        ],
    },
    'q05': {
        'subject': 'function+q_out+gp_master_probe+(text)',
        'decision': 'blocked',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'calls', 'condition', 'section', 'analysis_gap'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [{'name': 'p_msg', 'type': 'text', 'mode': 'd', 'default': None}],
                'volatility': 'volatile',
            },
            'reads': ['q_src.events'],
            'writes': ['q_out.gp_events'],
            'calls': ['q_meta.log_event'],
            'unknown_required': True,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q05-A1', 'review': 'D05', 'kind': 'declaration_attribute',
             'attribute': 'EXECUTE ON MASTER', 'position': 'after_body',
             'severity': 'blocking',
             'claim': 'EXECUTE ON MASTER is a declaration attribute after the body, not a dynamic EXECUTE of the body'},
            {'id': 'q05-A2', 'review': 'D05', 'kind': 'attribute_not_dynamic_execute',
             'rejected': [{'kind': 'EXECUTE', 'details.dynamic': True}],
             'severity': 'blocking',
             'claim': 'the attribute must not be recorded as a body-level dynamic EXECUTE'},
            {'id': 'q05-A3', 'review': 'D05', 'kind': 'nested_source_preserved',
             'physical_reads': ['q_src.events'], 'derived_tables_min': 2,
             'severity': 'blocking',
             'claim': 'reads of the nested derived tables are preserved instead of lost to a fallback crash'},
            {'id': 'q05-A4', 'review': 'Q-02', 'kind': 'no_crash',
             'forbid_exit_codes': [2], 'forbid_error_text': ['Unbalanced SQL list'],
             'severity': 'blocking',
             'claim': 'analysis of this file must not end in an undiagnosed crash; before Q-03 it stays blocked with an explicit reason'},
            {'id': 'q05-A5', 'review': 'D12', 'kind': 'unverified_compatibility',
             'dialect': 'greenplum', 'version': 'unknown', 'confirmed_minimum_versions': {},
             'rejected': ['PostgreSQL >=9.4', 'Greenplum >=5'],
             'severity': 'blocking',
             'claim': 'the input does not establish a server version; PostgreSQL >=9.4 / Greenplum >=5 compatibility must not be promised'},
        ],
        'mutations': [
            {'group': 'objects', 'selector': {'canonical_key': 'function+q_out+gp_master_probe+(text)'},
             'field': 'volatility', 'value': 'stable',
             'id': 'D05-invent-volatility-claim'},
        ],
    },
    'q06': {
        'subject': 'function+q_out+two_inserts+()',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'formula', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'void',
                'parameters': [],
                'volatility': 'volatile',
            },
            'operations': {'INSERT': 2, 'CTE': 2, 'SELECT': 5},
            'reads': ['q_src.orders', 'q_src.order_lines'],
            'writes': ['q_out.out_a', 'q_out.out_b'],
            'calls': [],
            'conditions': ['o.amount IS NOT NULL'],
            'local_objects': {'cte': 2},
            'definitions': [{'object': 'q_out.out_a', 'status': 'resolved'},
                            {'object': 'q_out.out_b', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q06-A1', 'review': 'D11', 'kind': 'cte_vs_derived',
             'cte_names': ['tm'], 'derived_aliases': ['s'], 'derived_alias_not_cte': ['s'],
             'severity': 'blocking',
             'claim': 'tm is a CTE and s is a derived-table alias; calling s a CTE or tm a physical table is a subject defect'},
            {'id': 'q06-A2', 'review': 'D11', 'kind': 'cte_scopes',
             'name': 'tm', 'scope_count': 2, 'per_scope_reads': [['q_src.orders'], ['q_src.order_lines']],
             'severity': 'blocking',
             'claim': 'the two `tm` names live in different per-INSERT scopes and read different physical sources'},
            {'id': 'q06-A3', 'review': 'D11', 'kind': 'graph_edges',
             'reads_to_operations': [['q_src.orders', 'INSERT:1'], ['q_src.order_lines', 'INSERT:2']],
             'operations_to_writes': [['INSERT:1', 'q_out.out_a'], ['INSERT:2', 'q_out.out_b']],
             'severity': 'blocking',
             'claim': 'reads -> operation -> writes edges resolve to physical objects and the two INSERTs stay distinct'},
            {'id': 'q06-A4', 'review': 'D08', 'kind': 'level_formulas',
             'formulas_by_scope': {
                 'INSERT:2/tm': 'l.qty * l.price',
             },
             'severity': 'blocking',
             'claim': 'the second CTE computes amount as qty * price; losing the product while keeping the CTE name is incomplete coverage'},
        ],
        'mutations': [
            {'group': 'objects', 'selector': {'kind': 'cte', 'name': 'tm'},
             'field': 'physical', 'value': True,
             'id': 'D11-mark-cte-physical'},
            {'group': 'conditions', 'selector': {'expression': 'o.amount IS NOT NULL'},
             'field': 'expression', 'value': 'o.amount IS NULL',
             'id': 'D11-lose-cte-filter'},
        ],
    },
    'q07': {
        'subject': 'function+q_out+xml_filter_blob+(bigint)',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'formula', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'xml',
                'parameters': [{'name': 'p_id', 'type': 'bigint', 'mode': 'd', 'default': None}],
                'volatility': 'volatile',
            },
            'operations': {'SELECT': 6, 'RETURN': 1},
            'reads': ['q_meta.field_defs', 'q_src.row_values'],
            'writes': [],
            'calls': [],
            'conditions': [
                "d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'title'",
                "d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'kind'",
                "d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'owner'",
                "d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'state'",
                "d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'extra'",
                'f.row_id = p_id',
            ],
            'formulas': ['xmlelement(name filters, xmlagg(xmlelement(name filter, f.value) ORDER BY f.ord))'],
            'definitions': [{'object': 'q_meta.field_defs', 'status': 'resolved'},
                            {'object': 'q_src.row_values', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q07-A1', 'review': 'D06', 'kind': 'xml_aggregate_filters',
             'aggregate_calls': ['xmlagg'], 'aggregate_count': 1,
             'element_name': 'filters', 'child_name': 'filter',
             'severity': 'blocking',
             'claim': 'exactly ONE xmlagg-shaped aggregation produces the filters list'},
            {'id': 'q07-A2', 'review': 'D06', 'kind': 'metadata_calls_not_filters',
             'metadata_select_count': 5, 'metadata_source': 'q_meta.field_defs',
             'metadata_fields': ['title', 'kind', 'owner', 'state', 'extra'],
             'must_not_count_as': 'filters',
             'severity': 'blocking',
             'claim': 'the five q_meta.field_defs lookups are metadata calls and are not five filter rows'},
            {'id': 'q07-A3', 'review': 'D09', 'kind': 'reference_source_present',
             'source': 'q_meta.field_defs', 'role': 'reference/metadata dictionary',
             'on_missing': 'plan_gap_blocks',
             'severity': 'blocking',
             'claim': 'the metadata dictionary source is essential; deleting it leaves a source gap that blocks'},
        ],
        'mutations': [
            {'group': 'formulas',
             'selector': {'expression': 'xmlelement(name filters, xmlagg(xmlelement(name filter, f.value) ORDER BY f.ord))'},
             'field': 'expression',
             'value': 'xmlelement(name filters, xmlelement(name filter, f.value))',
             'id': 'D06-drop-xmlagg'},
        ],
    },
    'q08': {
        'subject': 'function+q_out+load_positional+()',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'condition', 'migration_order', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [],
                'volatility': 'volatile',
            },
            'operations': {'INSERT': 1, 'SELECT': 1, 'RETURN': 1},
            'reads': ['q_src.order_feed'],
            'writes': ['q_out.orders'],
            'calls': [],
            'conditions': ['s.id IS NOT NULL'],
            'formulas': [],
            'exact_columns': {'q_out.orders': ['id', 'amount', 'legacy', 'total']},
            'definitions': [{'object': 'q_out.orders', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q08-A1', 'review': 'D10', 'kind': 'positional_insert_columns',
             'target': 'q_out.orders', 'column_list_present': False,
             'post_migration_columns': ['id', 'amount', 'legacy', 'total'], 'column_count': 4,
             'pre_migration_columns': ['id', 'legacy', 'total'],
             'severity': 'blocking',
             'claim': 'the INSERT is positional, so its meaning is the post-migration column order id, amount, legacy, total'},
            {'id': 'q08-A2', 'review': 'D10', 'kind': 'column_types',
             'columns': [
                 {'name': 'id', 'type_target': 'bigint'},
                 {'name': 'amount', 'type_target': 'numeric', 'type_expression': 'numeric'},
                 {'name': 'legacy', 'type_target': 'text'},
                 {'name': 'total', 'type_target': 'integer'},
             ],
             'severity': 'blocking',
             'claim': 'each target column keeps its exact type and migration evidence; no tb_* mask and no empty type'},
            {'id': 'q08-A3', 'review': 'D10', 'kind': 'migration_state',
             'manifest': 'fixtures/migrations/manifest.json', 'target_revision': '014',
             'ordered_files': ['baseline_orders.sql', '014_amount_reorder.sql'],
             'severity': 'blocking',
             'claim': 'the chosen schema state is the declared migration order, not file mtime or file name guessing'},
        ],
        'mutations': [
            {'group': 'columns', 'selector': {'name': 'amount'},
             'field': 'type_target', 'value': 'text',
             'id': 'D10-wrong-amount-type'},
        ],
    },
    'q09': {
        'subject': 'function+q_out+load_kpi_levels+()',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'formula', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [],
                'volatility': 'volatile',
            },
            'operations': {'INSERT': 3, 'SELECT': 3, 'RETURN': 1},
            'reads': ['q_src.kpi_facts'],
            'writes': ['q_out.kpi_result'],
            'calls': [],
            'conditions': [
                'f.kpi_id = 10 AND f.struct_id = 100 AND f.level_no = 1',
                'f.kpi_id = 10 AND f.struct_id = 100 AND f.level_no = 2',
                'f.kpi_id = 20 AND f.struct_id = 200 AND f.level_no = 1',
            ],
            'formulas': ['f.value_num * 1.5', 'f.value_num + 10', 'f.value_num'],
            'definitions': [{'object': 'q_out.kpi_result', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q09-A1', 'review': 'D07', 'kind': 'kpi_structure_pairs',
             'pairs': [
                 {'kpi_id': 10, 'struct_id': 100},
                 {'kpi_id': 20, 'struct_id': 200},
             ], 'pair_count': 2,
             'rejected_pair_count': [3, 4],
             'severity': 'blocking',
             'claim': 'KPI/structure pairs are counted per pair; claiming 3 pairs is rejected'},
            {'id': 'q09-A2', 'review': 'D07', 'kind': 'levels_per_pair',
             'pair_count': 2,
             'levels_by_pair': {
                 '10/100': [1, 2],
                 '20/200': [1],
             },
             'calculation_branches': 3,
             'rejected_claim': 'two levels for every KPI',
             'severity': 'blocking',
             'claim': '10/100 has levels 1,2; 20/200 has only level 1; the three branches do not establish a runtime row count'},
            {'id': 'q09-A3', 'review': 'D08', 'kind': 'level_formulas',
             'formulas_by_pair_level': {
                 '10/100/2': 'f.value_num * 1.5',
                 '20/200/1': 'f.value_num + 10',
                 '10/100/1': 'f.value_num',
             },
             'severity': 'blocking',
             'claim': 'formulas differ per KPI/structure/level; keeping names while losing one formula is incomplete coverage'},
        ],
        'mutations': [
            {'group': 'conditions', 'selector': {'expression': 'f.kpi_id = 20 AND f.struct_id = 200 AND f.level_no = 1'},
             'field': 'expression', 'value': 'f.kpi_id = 10 AND f.struct_id = 100 AND f.level_no = 1',
             'id': 'D07-collapse-pair'},
            {'group': 'formulas', 'selector': {'expression': 'f.value_num * 1.5'},
             'field': 'expression', 'value': 'f.value_num',
             'id': 'D08-lose-struct-100-level2-formula'},
        ],
    },
    'q10': {
        'subject': 'function+q_out+load_norm_plan+(bigint,date)',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'formula', 'condition', 'section'],
        'expectation': {
            'declaration': {
                'returns': 'numeric',
                'parameters': [
                    {'name': 'p_uid', 'type': 'bigint', 'mode': 'd', 'default': None},
                    {'name': 'p_date', 'type': 'date', 'mode': 'd', 'default': None},
                ],
                'volatility': 'volatile',
            },
            'operations': {'SELECT': 2, 'INSERT': 1, 'RETURN': 1},
            'reads': ['q_src.hours', 'q_src.plan_variants'],
            'writes': ['q_out.kpi_result'],
            'calls': [],
            'conditions': ['p.user_id = p_uid', 'h.user_id = p_uid AND h.on_date = p_date'],
            'formulas': ['h.hours / 8.0', 'COALESCE(v_norm, 0)'],
            'definitions': [{'object': 'q_src.plan_variants', 'status': 'resolved'},
                            {'object': 'q_out.kpi_result', 'status': 'resolved'}],
            'unknown_required': False,
        },
        'deparse_deltas': [],
        'assertions': [
            {'id': 'q10-A1', 'review': 'D08', 'kind': 'hour_normalization',
             'formula': 'h.hours / 8.0', 'divisor': 8.0,
             'rejected': ['h.hours', 'h.hours / 7.2'],
             'severity': 'blocking',
             'claim': 'hour normalization divides by 8.0; losing the divisor while keeping the KPI name is incomplete coverage'},
            {'id': 'q10-A2', 'review': 'D08', 'kind': 'plan_selection',
             'order_by': ['p.priority DESC', 'p.weight DESC'], 'limit': 1,
             'severity': 'blocking',
             'claim': 'plan choice is highest priority, then largest weight, then LIMIT 1'},
            {'id': 'q10-A3', 'review': 'D09', 'kind': 'essential_sources',
             'sources': ['q_src.hours', 'q_src.plan_variants'],
             'essential_conditions': ['p.user_id = p_uid', 'h.user_id = p_uid AND h.on_date = p_date'],
             'on_missing': 'plan_gap_blocks',
             'severity': 'blocking',
             'claim': 'both sources and both WHERE conditions are essential; deleting either leaves a plan gap that blocks'},
        ],
        'mutations': [
            {'group': 'formulas', 'selector': {'expression': 'h.hours / 8.0'},
             'field': 'expression', 'value': 'h.hours',
             'id': 'D08-lose-hour-normalization'},
        ],
    },
    'q11': {
        'subject': 'function+q_out+load_rr_sl+()',
        'decision': 'ready',
        'required_rules': ['identity', 'signature', 'sql_registry', 'registry_document',
                           'operation', 'reads', 'writes', 'formula', 'section', 'unknown'],
        'expectation': {
            'declaration': {
                'returns': 'bigint',
                'parameters': [],
                'volatility': 'volatile',
            },
            'operations': {'INSERT': 1, 'SELECT': 1, 'RETURN': 1},
            'reads': ['q_src.sla_raw'],
            'writes': ['q_out.rr_sl'],
            'calls': [],
            'conditions': [],
            'formulas': [
                "CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0 "
                "THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) / count(*)::numeric ELSE 0 END",
                "1 - CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0 "
                "THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) / count(*)::numeric ELSE 0 END",
            ],
            'definitions': [{'object': 'q_src.sla_raw', 'status': 'resolved'},
                            {'object': 'q_out.rr_sl', 'status': 'resolved'}],
            'unknown_required': True,
        },
        'deparse_deltas': [
            "pipeline deparse emits CAST(count(*) AS numeric) and tightens WHERE( spacing; source form kept as oracle truth",
        ],
        'assertions': [
            {'id': 'q11-A1', 'review': 'D08', 'kind': 'rr_formula',
             'name': 'rr_value',
             'shape': 'in_target_count / total_count',
             'rejected': ['average of minutes', 'sum of minutes'],
             'severity': 'blocking',
             'claim': 'rr_value is the share of tickets closed within target_min, not an average of durations'},
            {'id': 'q11-A2', 'review': 'D08', 'kind': 'sl_formula',
             'name': 'sl_value',
             'shape': '1 - rr_value',
             'severity': 'blocking',
             'claim': 'sl_value is the complement of the SAME two counts; losing either formula while keeping both column names is incomplete coverage'},
            {'id': 'q11-A3', 'review': 'Q-07', 'kind': 'honest_unknown',
             'token': 'rr', 'decoded': False,
             'rejected': ['first-response rate', 'resolution rate', 'return rate'],
             'severity': 'blocking',
             'claim': 'the business expansion of `rr` is unknown; any expanded name is an unverified claim and must be rejected or explicitly limited'},
        ],
        'mutations': [
            {'group': 'formulas',
             'selector': {'expression': "1 - CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0 THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) / count(*)::numeric ELSE 0 END"},
             'field': 'expression', 'value': '1',
             'id': 'D08-lose-sl-formula'},
            # Additional unknown control: an invented decoding dressed up as a formula.
            # The real claim check is q11-A3; this mutation only proves that a
            # fabricated expansion cannot ride along as an equivalent formula.
            {'group': 'formulas',
             'selector': {'expression': "CASE WHEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) > 0 THEN count(*) FILTER (WHERE (s.closed_at - s.opened_at) <= make_interval(mins => s.target_min)) / count(*)::numeric ELSE 0 END"},
             'field': 'expression', 'value': 'avg(first_response_minutes) / 60',
             'id': 'Q07-invent-rr-decoding'},
        ],
    },
}

def write_case(case_id, spec):
    directory = OUT / case_id
    directory.mkdir(parents=True, exist_ok=True)
    subject = spec['subject']
    facts = {'schema_version': 1, 'subjects': {subject: dict(spec['expectation'])}}
    _dump(directory / 'facts.json', facts)
    _dump(directory / 'checks.json',
          {'schema_version': 1, 'required_rules': spec['required_rules']})
    _dump(directory / 'decision.json',
          {'schema_version': 1, 'decision': spec['decision']})
    _dump(directory / 'page_assertions.json', {
        'schema_version': 1,
        'contract': 'claims-v1',
        'oracle': 'facts.json',
        'require_actual_markdown_values': True,
        'mutations': spec['mutations'],
    })
    _dump(directory / 'assertions.json', {
        'schema_version': 1,
        'derived_from': f"fixtures/{_fixture_for(case_id)}",
        'authoring': 'hand-derived from source SQL, independent of extractor and writer',
        'deparse_deltas': spec['deparse_deltas'],
        'assertions': spec['assertions'],
    })


def _fixture_for(case_id):
    return {
        'q01': 'q01_retro_param_local.sql', 'q02': 'q02_null_check_sign.sql',
        'q03': 'q03_dedup_priority.sql', 'q04': 'q04_date_branch.sql',
        'q05': 'q05_gp_master_nested.sql', 'q06': 'q06_cte_scopes.sql',
        'q07': 'q07_xml_filters.sql', 'q08': 'q08_positional_insert.sql',
        'q09': 'q09_kpi_levels.sql', 'q10': 'q10_hours_plan.sql',
        'q11': 'q11_rr_sl.sql',
    }[case_id]


def _dump(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                    encoding='utf-8')


def main():
    for case_id, spec in CASES.items():
        write_case(case_id, spec)
        print('wrote', OUT / case_id)
    print('total cases:', len(CASES))


if __name__ == '__main__':
    main()
