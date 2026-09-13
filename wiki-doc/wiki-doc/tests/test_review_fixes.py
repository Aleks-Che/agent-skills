"""Acceptance regressions from the P0/P1 implementation review (2026-09-13)."""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bundle_fixture import make_bundle, seal, write_json, PACKAGE, PAGE_ID
from artifact_schema import read_json, load_schemas, validate_schema, _linked_artifacts
from bundle import create_manifest, compute_tool_versions, write_manifest, save_bundle
from check_policy import load_policy, derive_inventory_checks, validate_check_against_policy
from evidence import sha256_file, sha256_bytes, check_evidence_against_inputs
from identity import (ObjectDescriptor, canonical_key, compute_identity, normalize_identifier,
                      normalize_type, resolve_page_id, page_id, IdentityError, main as identity_main)
from sql_extract import extract_inventory
from validation_plan import generate_plan
from validation_gate import evaluate_bundle, evaluate_checks


class InventoryAcceptanceTests(unittest.TestCase):
    def test_spaced_qualified_names_and_dollar_names_are_not_truncated(self):
        sql = 'CREATE VIEW demo . v$ AS SELECT a.x FROM ONLY demo . a JOIN demo . b ON a.id = b.id;'
        inv = extract_inventory(sql, 'source.sql', 'a' * 64)
        self.assertEqual(inv['documented_subjects'], ['view+demo+v%24'])
        self.assertFalse(inv['coverage_notes'], inv['coverage_notes'])
        select = next(i for i in inv['items'] if i['kind'] == 'SELECT')
        self.assertEqual(select['reads'], ['demo.a', 'demo.b'])

    def test_explicit_view_column_list_cannot_silently_use_select_alias(self):
        sql = 'CREATE VIEW demo.v(actual_name) AS SELECT amount AS different_name FROM demo.orders;'
        inv = extract_inventory(sql, 'source.sql', 'a' * 64)
        self.assertTrue(any('column lists' in n['reason'] for n in inv['coverage_notes']))
        plan = generate_plan(inv, load_policy())
        self.assertTrue(any(c['rule_id'] == 'analysis_gap' and c['blocking'] for c in plan['required_checks']))

    def inventory(self, sql, subjects=None):
        return extract_inventory(sql, 'test.sql', sha256_bytes(sql.encode()), documented_subjects=subjects)

    def test_plain_create_and_materialized_view(self):
        for sql, kind in [('CREATE VIEW demo.v AS SELECT 1;', 'view'),
                          ('CREATE MATERIALIZED VIEW demo.v AS SELECT 1;', 'materialized_view'),
                          ('CREATE TABLE demo.v AS SELECT 1;', 'ctas')]:
            with self.subTest(sql=sql):
                inv = self.inventory(sql)
                self.assertFalse(validate_schema(inv, load_schemas()['inventory'], 'inventory'))
                self.assertEqual(inv['items'][0]['details']['object_kind'], kind)
                self.assertFalse(inv['coverage_notes'])
                rules = {c['rule_id'] for c in generate_plan(inv, load_policy())['required_checks']}
                self.assertIn('columns', rules)
                self.assertNotIn('signature', rules)

    def test_comments_and_literals_cannot_create_declarations_or_operations(self):
        for sql in ["-- CREATE OR REPLACE VIEW demo.fake AS SELECT 1;",
                    "/* outer /* inner */ CREATE VIEW demo.fake AS SELECT 1; */",
                    "SELECT 'CREATE OR REPLACE VIEW demo.fake AS SELECT 1;';"]:
            with self.subTest(sql=sql):
                inv = self.inventory(sql)
                self.assertFalse(any(i['kind'] == 'DECLARATION' for i in inv['items']))
                self.assertLessEqual(sum(i['kind'] == 'SELECT' for i in inv['items']), 1)
                self.assertTrue(inv['coverage_notes'])

    def test_named_dollar_body_and_original_spans(self):
        inv = self.inventory('-- comment\nCREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $body1$\nBEGIN\nINSERT INTO demo.t(id)\nSELECT id FROM demo.s;\nEND;\n$body1$;')
        insert = next(i for i in inv['items'] if i['kind'] == 'INSERT')
        self.assertEqual((insert['source_ref']['start_line'], insert['source_ref']['end_line']), (4, 5))
        self.assertFalse(inv['coverage_notes'])

    def test_two_calls_on_one_line_keep_distinct_dependencies(self):
        inv = self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM demo.a(); PERFORM demo.b(); END; $$;')
        calls = [i for i in inv['items'] if i['kind'] == 'PERFORM']
        self.assertEqual([i['calls'] for i in calls], [['demo.a'], ['demo.b']])
        self.assertEqual([i['anchor']['ordinal'] for i in calls], [1, 2])

    def test_neighbouring_view_and_routine_do_not_share_bodies(self):
        sql = 'CREATE VIEW demo.v AS SELECT id FROM demo.view_source;\nCREATE FUNCTION demo.f() RETURNS int LANGUAGE sql AS $$SELECT id FROM demo.routine_source;$$;'
        inv = self.inventory(sql, ['demo.v'])
        self.assertEqual(next(i['reads'] for i in inv['items'] if i['kind'] == 'SELECT'), ['demo.view_source'])
        self.assertEqual(len(inv['documented_subjects']), 1)

    def test_overloads_and_argument_renaming_share_identity_rules(self):
        sql = 'CREATE FUNCTION demo.f(p int) RETURNS int LANGUAGE sql AS $$ SELECT 1; $$;\nCREATE FUNCTION demo.f(p bigint) RETURNS int LANGUAGE sql AS $$ SELECT 2; $$;'
        inv = self.inventory(sql)
        self.assertEqual(inv['documented_subjects'], ['function+demo+f+(integer)', 'function+demo+f+(bigint)'])
        selected = self.inventory(sql.replace('p int', 'renamed int'), ['function+demo+f+(integer)'])
        self.assertEqual(selected['documented_subjects'], ['function+demo+f+(integer)'])

    def test_unsupported_fragment_creates_a_blocking_plan_obligation(self):
        inv = self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN TRUNCATE demo.t; PERFORM demo.log(); END; $$;')
        checks = generate_plan(inv, load_policy())['required_checks']
        self.assertTrue(any(c['rule_id'] == 'analysis_gap' and c['blocking'] for c in checks))

    def test_each_expression_has_its_own_obligation(self):
        inv = self.inventory('CREATE VIEW demo.v AS SELECT a * 2 AS x, b / 3 AS y FROM demo.t WHERE a > 0;')
        checks = generate_plan(inv, load_policy())['required_checks']
        formulas = [c for c in checks if c['rule_id'] == 'formula']
        self.assertEqual(len(formulas), 2)
        self.assertEqual(len({c['id'] for c in formulas}), 2)
        self.assertEqual(len([c for c in checks if c['rule_id'] == 'condition']), 1)

    def test_dynamic_unknown_has_template_and_valid_anchor(self):
        sql = (PACKAGE / 'examples/07_dynamic_sql.sql').read_text(encoding='utf-8')
        inv = self.inventory(sql)
        dynamic = [i for i in inv['items'] if i['kind'] == 'EXECUTE']
        self.assertEqual([i['details']['command_kind'] for i in dynamic], ['TRUNCATE', 'INSERT'])
        self.assertTrue(all(i['details']['template'] for i in dynamic))
        plan = generate_plan(inv, load_policy())
        self.assertFalse(validate_schema(plan, load_schemas()['validation_plan'], 'plan'))
        self.assertFalse(_linked_artifacts({'inventory': inv, 'validation_plan': plan}))

    def test_multi_command_dynamic_string_is_an_analysis_gap(self):
        inv = self.inventory("CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$BEGIN EXECUTE 'SELECT 1; DROP TABLE demo.t'; END;$$;")
        self.assertTrue(any('dynamic' in n['reason'] for n in inv['coverage_notes']))

    def test_merge_does_not_invent_set_target_and_delete_keeps_using(self):
        inv = self.inventory('CREATE FUNCTION demo.f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN MERGE INTO demo.t AS t USING demo.s AS s ON t.id=s.id WHEN MATCHED THEN UPDATE SET v=s.v; DELETE FROM demo.t USING demo.s WHERE demo.t.id=demo.s.id; END;$$;')
        self.assertFalse(any('set' in i.get('writes', []) for i in inv['items']))
        self.assertEqual(next(i['reads'] for i in inv['items'] if i['kind'] == 'DELETE'), ['demo.s'])
        self.assertTrue(inv['coverage_notes'])  # MERGE remains explicitly partial in P0.

    def test_anchor_does_not_change_when_neighbour_is_added(self):
        items = []
        for scope, path in [('view+demo+a', 'a.sql'), ('view+demo+b', 'b.sql')]:
            items.append({'kind': 'SELECT', 'anchor': {'object_or_scope': scope, 'construct': 'SELECT', 'ordinal': 1},
                          'source_ref': {'path': path, 'start_line': 1, 'end_line': 1, 'sha256': 'a' * 64}})
        together = derive_inventory_checks(load_policy(), items)
        alone = derive_inventory_checks(load_policy(), items[1:])
        self.assertEqual(together[1], alone[0])
        self.assertEqual(together[1]['inventory_anchor']['path'], 'b.sql')
        self.assertEqual(together[1]['inventory_anchor']['ordinal'], 1)

    def test_plan_rejects_empty_inventory_and_incorrect_kind(self):
        inv = self.inventory('CREATE VIEW demo.v AS SELECT 1;')
        with self.assertRaises(ValueError):
            generate_plan(inv, load_policy(), object_kind='function')
        inv['items'] = []
        with self.assertRaises(ValueError):
            generate_plan(inv, load_policy())

    def test_policy_rejects_downgrade_and_unjustified_inapplicability(self):
        check = {'id': 'identity', 'rule_id': 'identity', 'source': 'object_type', 'subject': 'demo.v',
                 'category': 'technical', 'blocking': True, 'applicable': True}
        for field, value in [('category', 'editorial'), ('blocking', False), ('applicable', False)]:
            self.assertTrue(validate_check_against_policy(load_policy(), {**check, field: value}))


class IdentityAcceptanceTests(unittest.TestCase):
    def key(self, arg):
        return canonical_key(ObjectDescriptor('function', 'demo', 'f', [arg]))

    def test_names_defaults_modes_and_multicomponent_types(self):
        for a, b in [('p bigint DEFAULT 1', 'renamed int8 = 2'),
                     ('INOUT p double precision', 'IN q float8'),
                     ('VARIADIC p int4[]', 'IN xs integer ARRAY'),
                     ('p numeric(12,2)', 'x decimal(18,4)'),
                     ('"arg name" timestamp(3) with time zone DEFAULT now()', 't timestamptz')]:
            with self.subTest(a=a, b=b):
                self.assertEqual(self.key(a), self.key(b))

    def test_quoted_types_preserve_case_and_quote_escapes(self):
        self.assertNotEqual(self.key('p "Types"."Money"'), self.key('p types.money'))
        self.assertEqual(normalize_identifier('"A""B"'), 'A"B')
        self.assertEqual(normalize_identifier('FOO'), normalize_identifier('"foo"'))
        self.assertNotEqual(self.key('p types."Money(12)"'), self.key('p types."Money"'))
        self.assertEqual(self.key('p "pg_catalog"."int4"'), self.key('p integer'))

    def test_unresolved_schema_and_user_type_are_errors(self):
        with self.assertRaises(IdentityError):
            compute_identity('table', name='t')
        for arg in ['p blob', 'p custom_type', 'IN', '']:
            with self.subTest(arg=arg), self.assertRaises(IdentityError):
                self.key(arg)

    def test_pg_aliases_arrays_and_float_precision(self):
        self.assertEqual(self.key('p int4[10][20]'), self.key('p integer[]'))
        self.assertEqual(self.key('p float(24)'), self.key('p real'))
        self.assertEqual(self.key('p float(53)'), self.key('p double precision'))
        self.assertNotEqual(self.key('p real'), self.key('p double precision'))

    def test_key_separators_cannot_merge_distinct_identifiers(self):
        a = canonical_key(ObjectDescriptor('table', '"a+b"', 'c'))
        b = canonical_key(ObjectDescriptor('table', 'a', '"b+c"'))
        self.assertNotEqual(a, b)

    def test_migration_paths_normalize_and_cannot_escape(self):
        a = compute_identity('migration', migration_path='db\\sub\\..\\001_create.sql')
        b = compute_identity('migration', migration_path='./db/001_create.sql')
        self.assertEqual(a, b)
        for path in ['../001.sql', 'C:\\001.sql', '/001.sql']:
            with self.assertRaises(IdentityError):
                compute_identity('migration', migration_path=path)

    def test_existing_path_and_explicit_legacy_mapping_are_preserved(self):
        key = self.key('p int')
        self.assertEqual(resolve_page_id(key, existing_pages={'manual/old-name.md': key}), 'manual/old-name.md')
        self.assertEqual(resolve_page_id(key, existing_pages={'old.md': 'legacy-key'}, legacy_keys={'legacy-key': key}), 'old.md')
        with self.assertRaises(IdentityError):
            resolve_page_id(key, existing_pages={'a.md': key, 'b.md': key})

    def test_registry_paths_are_validated_and_normalized(self):
        key = self.key('p int')
        self.assertEqual(resolve_page_id(key, existing_pages={r'manual\old.md': key}), 'manual/old.md')
        for path in ['', '../old.md', '/old.md', 'C:/old.md', 'old.txt', 'old.md:stream', 'a\x00.md']:
            with self.subTest(path=path), self.assertRaises(IdentityError):
                resolve_page_id(key, existing_pages={path: key})
        with self.assertRaises(IdentityError):
            resolve_page_id(key, existing_pages={'old.md': key, './old.md': 'other'})

    def test_hash_collision_preserves_existing_name_and_exhaustion_fails(self):
        with patch('identity.page_slug', return_value='same'), patch('identity.hashlib.sha256') as sha:
            sha.return_value.hexdigest.return_value = 'a' * 64
            original = 'same--' + 'a' * 12 + '.md'
            self.assertEqual(page_id('different-key', {original}), 'same--' + 'a' * 13 + '.md')
            with self.assertRaises(IdentityError):
                page_id('different-key', {'same--' + 'a' * n + '.md' for n in range(12, 65)})

    def test_check_cli_reports_existing_id_without_silently_renaming(self):
        key = self.key('p int')
        with redirect_stdout(io.StringIO()) as output:
            result = identity_main(['check', key, '--existing-ids', page_id(key)])
        self.assertEqual(result, 1)
        self.assertTrue(json.loads(output.getvalue())['collision'])

    def test_catalog_character_and_quoted_aliases_remain_distinct(self):
        self.assertEqual(self.key('char'), self.key('pg_catalog.bpchar'))
        self.assertEqual(self.key('"char"'), self.key('pg_catalog.char'))
        self.assertNotEqual(self.key('char'), self.key('"char"'))
        self.assertNotEqual(self.key('integer'), self.key('pg_catalog.integer'))
        self.assertEqual(self.key('timestamptz'), self.key('pg_catalog."timestamptz"'))
        self.assertEqual(self.key('timestamp'), self.key('"timestamp"'))

    def test_slug_and_hash_length_are_bounded(self):
        self.assertLess(len(page_id('table+' + 'very-long-name-' * 100)), 150)
        for value in [0, -1, 65, True]:
            with self.assertRaises(IdentityError):
                page_id('table+demo+t', initial_hash_len=value)


class GateAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / 'run'
        self.run.mkdir()
        make_bundle(self.run)

    def test_wrong_formula_cannot_pass_with_resealed_hashes(self):
        facts = read_json(self.run / 'facts.json')
        facts['formulas'][0]['expression'] = 'sum(amount * 9.9)'
        write_json(self.run, 'facts', facts)
        result = seal(self.run)
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue(any('formula' in e for e in result['errors']))

    def test_invented_formula_with_valid_links_is_rejected(self):
        facts = read_json(self.run / 'facts.json')
        facts['formulas'].append({**facts['formulas'][0], 'id': 'formula_invented', 'expression': 'amount / 99'})
        write_json(self.run, 'facts', facts)
        coverage = read_json(self.run / 'coverage.json')
        coverage['entries']['formula_invented'] = [{'section_id': 'formulas_dependencies'}]
        write_json(self.run, 'coverage', coverage)
        result = seal(self.run)
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue(any('formula_invented' in e for e in result['errors']))

    def test_parsed_artifact_bytes_must_match_the_manifest(self):
        from artifact_schema import read_artifact_set
        path = self.run / 'facts.json'
        original = path.read_bytes()
        transient = json.loads(original)
        transient['unknowns'][0]['reason'] = 'A different transient claim'

        def during_read(directory, **kwargs):
            path.write_text(json.dumps(transient), encoding='utf-8')
            try:
                return read_artifact_set(directory, **kwargs)
            finally:
                path.write_bytes(original)

        with patch('validation_gate.read_artifact_set', side_effect=during_read):
            result = evaluate_bundle(self.run, write_decision=True)
        self.assertEqual(result['decision'], 'blocked', result)
        self.assertTrue(any('parsed bytes' in e for e in result['errors']))

    def test_parsed_manifest_bytes_must_match_its_recorded_hash(self):
        from artifact_schema import read_artifact_set
        path = self.run / 'manifest.json'
        original = path.read_bytes()
        transient = json.loads(original)
        transient['revision'] = 'A different transient revision'

        def during_read(directory, **kwargs):
            path.write_text(json.dumps(transient), encoding='utf-8')
            try:
                return read_artifact_set(directory, **kwargs)
            finally:
                path.write_bytes(original)

        with patch('validation_gate.read_artifact_set', side_effect=during_read):
            result = evaluate_bundle(self.run, write_decision=True)
        self.assertEqual(result['decision'], 'blocked', result)
        self.assertTrue(any('parsed bytes' in e for e in result['errors']))

    def test_honest_runtime_unknown_can_receive_ready(self):
        sql = "CREATE FUNCTION demo.f(suffix text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN EXECUTE format('TRUNCATE TABLE demo.%I', suffix); END; $$;\n"
        (self.run / 'source.sql').write_text(sql, encoding='utf-8')
        sha = sha256_file(self.run / 'source.sql')
        ref = {'path': 'source.sql', 'start_line': 1, 'end_line': 1, 'sha256': sha}
        rid = read_json(self.run / 'facts.json')['run_id']
        key, pid = 'function+demo+f+(text)', page_id('function+demo+f+(text)')
        facts = {'schema_version': 2, 'run_id': rid, 'dialect': {'name': 'postgres', 'version': '15'},
                 'profile': None, 'documented_object_ids': ['f'], 'inputs': [{'path': 'source.sql', 'sha256': sha}],
                 'objects': [{'id': 'f', 'kind': 'function', 'schema': 'demo', 'name': 'f', 'signature': '(suffix text)',
                              'canonical_key': key, 'page_id': pid, 'source_refs': [ref]}],
                 'definitions': [], 'columns': [], 'formulas': [], 'conditions': [],
                 'operations': [{'id': 'exec', 'kind': 'EXECUTE', 'scope': 'f', 'order': 1,
                                 'dynamic': {'template': 'TRUNCATE TABLE demo.%I', 'unresolved_parts': ['suffix']},
                                 'reads': [], 'writes': [], 'calls': [], 'condition_ids': [], 'source_refs': [ref]}],
                 'unknowns': [{'id': 'runtime', 'what': 'Concrete TRUNCATE target', 'reason': 'suffix is a runtime argument',
                               'related_facts': ['exec']}]}
        write_json(self.run, 'facts', facts)
        inv = extract_inventory(sql, 'source.sql', sha, version='15')
        inv['run_id'] = rid
        write_json(self.run, 'inventory', inv)
        plan = generate_plan(inv, load_policy(), page_id=pid)
        write_json(self.run, 'validation_plan', plan)
        draft = '# Dynamic truncation {#header_purpose}\nTruncates a table named by suffix.\n## Signature {#schema_signature}\nf(suffix text) returns void.\n## Dependencies {#formulas_dependencies}\nTRUNCATE target: demo plus a quoted suffix identifier.\n## Limits {#misc}\nThe concrete table name depends on the runtime argument.\n'
        (self.run / 'page.draft.md').write_text(draft, encoding='utf-8')
        write_json(self.run, 'coverage', {'schema_version': 2, 'run_id': rid, 'page_id': pid,
                   'entries': {fid: [{'section_id': 'misc'}] for fid in ('f', 'exec', 'runtime')}})
        checks = []
        for check in plan['required_checks']:
            checks.append({'id': 'result:' + check['id'], 'plan_check_id': check['id'], 'status': 'ok',
                           'blocking': check['blocking'], 'category': check['category'],
                           'reason': 'The command template and runtime limitation are accurately documented.',
                           'evidence': [{'root': 'project', **ref}],
                           'fact_ids': [{'operation': 'exec', 'unknown': 'runtime'}.get(check['rule_id'], 'f')]})
        write_json(self.run, 'validation', {'schema_version': 2, 'run_id': rid, 'page_id': pid, 'checks': checks})
        manifest = create_manifest(page_id=pid, sql_files=[self.run / 'source.sql'], artifacts_dir=self.run,
                                   tool_versions=compute_tool_versions(PACKAGE))
        write_manifest(manifest, self.run / 'manifest.json')
        result = evaluate_bundle(self.run, write_decision=True)
        self.assertEqual(result['decision'], 'ready', result)
        self.assertEqual(evaluate_bundle(self.run)['decision'], 'ready')
        facts['operations'][0]['dynamic']['template'] = 'DELETE FROM demo.%I'
        write_json(self.run, 'facts', facts)
        manifest = create_manifest(page_id=pid, sql_files=[self.run / 'source.sql'], artifacts_dir=self.run,
                                   tool_versions=compute_tool_versions(PACKAGE))
        write_manifest(manifest, self.run / 'manifest.json')
        self.assertEqual(evaluate_bundle(self.run, write_decision=True)['decision'], 'revise')

    def test_view_output_column_is_required_and_its_expression_is_checked(self):
        sql = 'CREATE VIEW core.v AS\nSELECT sum(amount * 1.1) AS total\nFROM demo.orders\nWHERE amount > 0;\n'
        (self.run / 'source.sql').write_text(sql, encoding='utf-8')
        sha = sha256_file(self.run / 'source.sql')
        ref = {'path': 'source.sql', 'start_line': 1, 'end_line': 4, 'sha256': sha}
        facts = read_json(self.run / 'facts.json')
        def rebind(value):
            if isinstance(value, dict):
                if value.get('path') == 'source.sql':
                    value.update(ref if 'start_line' in value else {'sha256': sha})
                for child in value.values():
                    rebind(child)
            elif isinstance(value, list):
                for child in value:
                    rebind(child)
        rebind(facts)
        pid = page_id('view+core+v')
        facts['objects'][0].update(kind='view', name='v', canonical_key='view+core+v', page_id=pid)
        facts['objects'][0].pop('signature')
        facts['columns'][0]['type_target'] = None
        facts['columns'][0].pop('type_evidence')
        facts['columns'].append({'id': 'output', 'object_id': 'obj_1', 'name': 'total',
                                 'expression': 'sum(amount * 1.1)', 'type_target': None,
                                 'type_expression': None, 'source_refs': [ref]})
        facts['unknowns'].append({'id': 'types', 'what': 'Column types', 'reason': 'Source DDL not provided',
                                  'related_facts': ['col_001', 'output']})
        write_json(self.run, 'facts', facts)
        inv = extract_inventory(sql, 'source.sql', sha, version='15')
        inv['run_id'] = facts['run_id']
        write_json(self.run, 'inventory', inv)
        plan = generate_plan(inv, load_policy(), page_id=pid)
        write_json(self.run, 'validation_plan', plan)
        draft = '# View {#header_purpose}\ncore.v sums positive order amounts.\n## Definition {#schema_signature}\nCREATE VIEW core.v AS a SELECT aggregate.\n## Dependencies {#formulas_dependencies}\nReads demo.orders, computes sum(amount * 1.1) WHERE amount > 0.\n## Output {#entities}\ntotal = sum(amount * 1.1). Its type is unknown because source DDL is absent.\n## Limits {#misc}\nSource column types and business rounding requirements are unknown.\n'
        (self.run / 'page.draft.md').write_text(draft, encoding='utf-8')
        coverage = read_json(self.run / 'coverage.json')
        coverage['page_id'] = pid
        coverage['entries'].update({key: [{'section_id': 'entities'}] for key in ('output', 'types')})
        write_json(self.run, 'coverage', coverage)
        checks = []
        for c in plan['required_checks']:
            checks.append({'id': 'result:' + c['id'], 'plan_check_id': c['id'], 'status': 'ok',
                           'blocking': c['blocking'], 'category': c['category'],
                           'reason': 'The view and output expression agree with SQL; types remain unknown.',
                           'evidence': [{'root': 'project', **ref}],
                           'fact_ids': [{'operation': 'op_001', 'formula': 'formula_001', 'condition': 'cond_001'}.get(c['rule_id'], 'obj_1')]})
        write_json(self.run, 'validation', {'schema_version': 2, 'run_id': facts['run_id'], 'page_id': pid, 'checks': checks})
        def issue():
            manifest = create_manifest(page_id=pid, sql_files=[self.run / 'source.sql'], artifacts_dir=self.run,
                                       tool_versions=compute_tool_versions(PACKAGE))
            write_manifest(manifest, self.run / 'manifest.json')
            return evaluate_bundle(self.run, write_decision=True)
        result = issue()
        self.assertEqual(result['decision'], 'ready', result)
        facts['columns'][-1]['expression'] = 'sum(amount * 9.9)'
        write_json(self.run, 'facts', facts)
        self.assertEqual(issue()['decision'], 'revise')
        facts['columns'].pop()
        facts['unknowns'][-1]['related_facts'] = ['col_001']
        coverage['entries'].pop('output')
        write_json(self.run, 'facts', facts)
        write_json(self.run, 'coverage', coverage)
        result = issue()
        self.assertEqual(result['decision'], 'revise', result)
        self.assertTrue(any('missing output' in e for e in result['errors']))

    def test_unclassified_extra_defect_cannot_become_editorial(self):
        report = read_json(self.run / 'validation.json')
        extra = {**report['checks'][0], 'id': 'new-unclassified-claim', 'status': 'defect', 'category': 'editorial', 'blocking': False}
        extra.pop('plan_check_id')
        report['checks'].append(extra)
        write_json(self.run, 'validation', report)
        self.assertEqual(seal(self.run)['decision'], 'revise')

    def test_forged_canonical_key_blocks_publication(self):
        facts = read_json(self.run / 'facts.json')
        facts['objects'][0]['canonical_key'] = 'function+demo+other+()'
        write_json(self.run, 'facts', facts)
        self.assertEqual(seal(self.run)['decision'], 'revise')

    def remap_page(self, target, registry=None):
        for name in ['facts', 'coverage', 'validation', 'validation_plan']:
            data = read_json(self.run / (name + '.json'))
            if name == 'facts':
                data['objects'][0]['page_id'] = target
            else:
                data['page_id'] = target
            write_json(self.run, name, data)
        registry_path = None
        if registry is not None:
            registry_path = self.run / 'pages.json'
            registry_path.write_text(json.dumps(registry), encoding='utf-8')
        manifest = create_manifest(page_id=target, sql_files=[self.run / 'source.sql'], artifacts_dir=self.run,
                                   tool_versions=compute_tool_versions(PACKAGE), identity_registry=registry_path)
        write_manifest(manifest, self.run / 'manifest.json')

    def test_arbitrary_page_id_requires_explicit_existing_mapping(self):
        self.remap_page('legacy/calc.md')
        self.assertEqual(evaluate_bundle(self.run, write_decision=True)['decision'], 'revise')
        self.remap_page('legacy/calc.md', {'pages': {'legacy/calc.md': 'old-calc-key'},
                                        'legacy_keys': {'old-calc-key': 'function+core+calc+()'}})
        result = evaluate_bundle(self.run, write_decision=True)
        self.assertEqual(result['decision'], 'ready', result)

    def test_invalid_registry_returns_diagnostics(self):
        self.remap_page('legacy/calc.md', [])
        result = evaluate_bundle(self.run, write_decision=True)
        self.assertFalse(result['publication_authorized'])

    def test_archive_copies_only_verified_run_files_and_sources(self):
        (self.run / 'unrelated-secret.txt').write_text('Not a bundle artifact', encoding='utf-8')
        dest = Path(self.temp.name) / 'archive'
        save_bundle(self.run, dest)
        self.assertFalse((dest / 'unrelated-secret.txt').exists())
        self.assertTrue((dest / 'source.sql').is_file())
        self.assertEqual(evaluate_bundle(dest)['decision'], 'ready')

    def test_evidence_must_match_the_resolved_root_and_declared_input(self):
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        (other / 'source.sql').write_text('SELECT 1;\n', encoding='utf-8')
        roots = {'project': self.run, 'wiki': other}
        declared = [{'path': 'source.sql', 'sha256': sha256_file(self.run / 'source.sql')}]
        evidence = [{'root': 'wiki', 'path': 'source.sql', 'start_line': 1, 'end_line': 1,
                     'sha256': sha256_file(other / 'source.sql')}]
        self.assertTrue(check_evidence_against_inputs(evidence, declared, roots))

    def test_evidence_cli_bad_json_shape_is_input_error_without_traceback(self):
        path = self.run / 'bad-evidence.json'
        path.write_text('42', encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(PACKAGE / 'scripts/evidence.py'), 'validate', str(path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
