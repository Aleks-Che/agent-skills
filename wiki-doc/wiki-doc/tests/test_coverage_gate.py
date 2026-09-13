"""P1-02 acceptance: real v2 bundles, Markdown targets, links and CLI failures."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from coverage_gate import MarkdownDocument, CoverageError, parse_markdown_sections, validate_coverage, validate_coverage_bundle
from artifact_schema import read_json
from check_policy import load_policy, validate_policy, PolicyError
from bundle_fixture import make_bundle, seal, write_json, PAGE_ID, PACKAGE
from validation_gate import evaluate_bundle


class MarkdownTests(unittest.TestCase):
    def test_explicit_ids(self):
        self.assertEqual(parse_markdown_sections('## Purpose {#purpose}\nText.'), {'purpose': ['Text.']})

    def test_implicit_ids(self):
        self.assertIn('some-title', parse_markdown_sections('## Some title\nText.'))

    def test_unicode_ids(self):
        self.assertIn('итог', parse_markdown_sections('## Итог\nРасчёт.'))

    def test_duplicate_explicit_ids_rejected(self):
        with self.assertRaises(CoverageError):
            parse_markdown_sections('## A {#same}\nOne.\n## B {#same}\nTwo.')

    def test_repeated_titles_are_distinct(self):
        sections = parse_markdown_sections('## A\nOne.\n## A\nTwo.')
        self.assertEqual(sections, {'a': ['One.'], 'a-1': ['Two.']})

    def test_repeated_titles_with_explicit_ids(self):
        self.assertEqual(len(parse_markdown_sections('## A {#one}\nOne.\n## A {#two}\nTwo.')), 2)

    def test_generated_anchor_collision_rejected(self):
        with self.assertRaises(CoverageError):
            parse_markdown_sections('## A\nOne.\n## A {#a}\nTwo.')

    def test_fenced_code_is_content_but_not_a_heading(self):
        sections = parse_markdown_sections('## A {#a}\n```sql\n## fake {#fake}\nSELECT 1;\n```')
        self.assertEqual(set(sections), {'a'})
        self.assertTrue(sections['a'])

    def test_tilde_fence_and_longer_closing_fence(self):
        self.assertEqual(set(parse_markdown_sections('## A\n~~~\n# fake\n~~~~\n## B\nText')), {'a', 'b'})

    def test_fence_with_text_is_not_closing_fence(self):
        self.assertEqual(set(parse_markdown_sections('## A\n```\n``` bad\n## fake\n```')), {'a'})

    def test_indented_code_not_heading(self):
        self.assertNotIn('fake', parse_markdown_sections('## A\n\n    ## fake\n'))

    def test_comments_are_not_content(self):
        self.assertEqual(parse_markdown_sections('## A\n<!-- text\n## fake\n-->'), {'a': []})

    def test_comments_inside_code_are_preserved(self):
        self.assertTrue(parse_markdown_sections('## A\n```\n<!-- visible code -->\n```')['a'])

    def test_heading_only_parent_is_empty(self):
        self.assertFalse(parse_markdown_sections('## A\n### B\n')['a'])

    def test_nested_content_belongs_to_parent(self):
        self.assertEqual(parse_markdown_sections('## A\n### B\nText')['a'], ['Text'])

    def test_setext_and_closing_atx(self):
        self.assertEqual(set(parse_markdown_sections('A {#one}\n===\nText\n\n## B {#two} ##\nOther')), {'one','two'})

    def test_indented_atx_heading(self):
        self.assertIn('one', parse_markdown_sections('  ## A {#one}\nText'))

    def test_layout_is_not_content(self):
        self.assertFalse(parse_markdown_sections('## A\n\n---\n\n[ref]: file.md\n')['a'])

    def test_hidden_section_marker(self):
        d = MarkdownDocument('<!-- wiki-doc:section stable -->\n## Title\nText')
        self.assertIn('stable', d.sections)
        self.assertFalse(d.errors)

    def test_html_anchor_before_heading(self):
        self.assertIn('stable', MarkdownDocument('<a id="stable"></a>\n\n## Title\nText').sections)

    def test_unattached_section_marker(self):
        self.assertTrue(MarkdownDocument('<!-- wiki-doc:section stable -->\nText').errors)

    def test_fragment_binds_one_paragraph(self):
        d = MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\nFirst.\n\nSecond.')
        self.assertEqual(d.fragments['f'], {'start': 2, 'body': 2, 'end': 3})

    def test_fragment_binds_table(self):
        d = MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\n| a | b |\n| - | - |\n| 1 | 2 |')
        self.assertEqual(d.fragments['f']['end'], 5)
        self.assertTrue(d.nonempty(d.fragments['f']))

    def test_fragment_binds_list(self):
        d = MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\n- one\n- two')
        self.assertEqual(d.fragments['f']['end'], 4)

    def test_fragment_binds_code(self):
        d = MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\n```sql\nSELECT 1;\n```')
        self.assertTrue(d.nonempty(d.fragments['f']))

    def test_fragment_cannot_jump_to_later_section(self):
        self.assertTrue(MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\n## B\nText').errors)

    def test_hidden_html_content_is_not_coverage(self):
        self.assertTrue(MarkdownDocument('## A\n<span hidden>Fake</span>').errors)

    def test_fragment_section_id_collision(self):
        self.assertIn('a', MarkdownDocument('## A\n<!-- wiki-doc:fragment a -->\nText').duplicates)

    def test_duplicate_fragment_markers(self):
        self.assertIn('f', MarkdownDocument('## A\n<!-- wiki-doc:fragment f -->\nFirst.\n\n<!-- wiki-doc:fragment f -->\nSecond.').duplicates)


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls.temp.name)
        make_bundle(cls.run_dir)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.facts, self.coverage, self.plan = [read_json(self.run_dir / (n + '.json'))
                                             for n in ('facts', 'coverage', 'validation_plan')]
        self.draft = (self.run_dir / 'page.draft.md').read_text(encoding='utf-8')

    def check(self, **kwargs):
        return validate_coverage(self.coverage, self.draft, self.facts, self.plan, **kwargs)

    def test_complete_bundle(self):
        result = self.check()
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.to_dict()['coverage_percent'], 100)
        self.assertFalse(result.to_dict()['publication_authorized'])

    def test_documented_object_is_required(self):
        del self.coverage['entries']['obj_1']
        self.assertIn('obj_1', self.check().uncovered_facts)

    def test_missing_formula(self):
        del self.coverage['entries']['formula_001']
        self.assertIn('formula_001', self.check().uncovered_facts)

    def test_linked_column_is_required(self):
        del self.coverage['entries']['col_001']
        self.assertIn('col_001', self.check().uncovered_facts)

    def test_unrelated_context_is_optional(self):
        self.facts['columns'].append({**self.facts['columns'][0], 'id': 'context_extra'})
        result = self.check()
        self.assertTrue(result.valid, result.errors)
        self.assertNotIn('context_extra', result.covered_facts | result.uncovered_facts)

    def test_optional_context_reference_still_checked(self):
        self.coverage['entries']['orders'] = [{'section_id': 'no-such-section'}]
        self.assertFalse(self.check().valid)

    def test_unknown_fact_id(self):
        self.coverage['entries']['missing'] = [{'section_id': 'misc'}]
        result = self.check()
        self.assertTrue(result.input_error)

    def test_empty_coverage_array_is_not_success(self):
        self.coverage['entries']['formula_001'] = []
        self.assertTrue(self.check().input_error)

    def test_missing_section_is_not_counted_as_covered(self):
        self.coverage['entries']['formula_001'] = [{'section_id': 'missing'}]
        result = self.check()
        self.assertIn('formula_001', result.uncovered_facts)
        self.assertLess(result.to_dict()['coverage_percent'], 100)

    def test_wrong_section_rejected_by_policy(self):
        self.coverage['entries']['formula_001'] = [{'section_id': 'header_purpose'}]
        self.assertTrue(any('wrong section' in e for e in self.check().errors))

    def test_policy_is_shared_not_hardcoded(self):
        self.coverage['entries']['formula_001'] = [{'section_id': 'header_purpose'}]
        policy = load_policy()
        policy['coverage']['allowed_sections']['formulas'].append('header_purpose')
        self.assertTrue(self.check(policy=policy).valid)

    def test_policy_rejects_unknown_section(self):
        policy = load_policy()
        policy['coverage']['allowed_sections']['formulas'] = ['nonexistent']
        with self.assertRaises(PolicyError):
            validate_policy(policy)

    def test_missing_required_section(self):
        self.draft = self.draft.replace('{#schema_signature}', '{#other}')
        self.assertIn('schema_signature', self.check().missing_sections)

    def test_empty_required_section(self):
        self.draft = self.draft.replace('core.calc() returns numeric.', '<!-- no visible content -->')
        self.assertIn('schema_signature', self.check().empty_sections)

    def test_duplicate_id_is_not_merged(self):
        self.draft += '\n## Again {#formulas_dependencies}\nContent.'
        result = self.check()
        self.assertFalse(result.valid)
        self.assertIn('formulas_dependencies', result.duplicate_sections)

    def test_missing_fragment_is_error(self):
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = '#missing'
        result = self.check()
        self.assertFalse(result.valid)
        self.assertIn('#missing', result.invalid_fragments)

    def test_valid_hidden_fragment(self):
        self.draft = self.draft.replace('Reads demo.orders.', '<!-- wiki-doc:fragment calculation -->\nReads demo.orders.')
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = '#calculation'
        result = self.check()
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fragments['formula_001'][0]['start_line'], 7)

    def test_empty_child_heading_is_not_fragment(self):
        self.draft = self.draft.replace('## Limitations', '### Empty {#empty}\n\n## Limitations')
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = '#empty'
        self.assertFalse(self.check().valid)

    def test_fragment_in_another_section(self):
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = '#misc'
        self.assertIn('#misc', self.check().invalid_fragments)

    def test_legacy_section_fragment(self):
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = 'formulas_dependencies'
        self.assertTrue(self.check().valid)

    def test_fragment_marker_in_code_does_not_exist(self):
        self.draft += '\n```\n<!-- wiki-doc:fragment fake -->\n```'
        self.coverage['entries']['unknown_001'] = [{'section_id': 'misc', 'fragment_ref': '#fake'}]
        self.assertFalse(self.check().valid)

    def test_mixed_run_is_input_error(self):
        self.coverage['run_id'] = '00000000-0000-0000-0000-000000000000'
        self.assertTrue(self.check().input_error)

    def test_malformed_shapes_do_not_crash(self):
        for value in ([], None, {'entries': []}):
            with self.subTest(value=value):
                result = validate_coverage(value, self.draft, self.facts)
                self.assertTrue(result.input_error)

    def test_empty_fragment_string_is_input_error_or_rejected(self):
        self.coverage['entries']['formula_001'][0]['fragment_ref'] = ''
        self.assertFalse(self.check().valid)

    def test_plan_shape_not_ignored(self):
        self.plan = []
        self.assertTrue(self.check().input_error)

    def test_no_plan_is_explicitly_partial(self):
        self.plan = None
        result = self.check()
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any('facts only' in w for w in result.warnings))

    def test_nested_final_path_and_parent_relative_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'related.md').write_text('## Target\nText', encoding='utf-8')
            self.coverage['page_id'] = self.plan['page_id'] = 'nested/page.md'
            self.facts['objects'][0]['page_id'] = 'nested/page.md'
            self.draft += '\n[Related](../related.md#target)'
            result = self.check(wiki_root=root)
            self.assertTrue(result.valid, result.errors)

    def test_page_path_escape(self):
        self.coverage['page_id'] = self.plan['page_id'] = '../outside.md'
        self.facts['objects'][0]['page_id'] = '../outside.md'
        self.assertFalse(self.check(wiki_root=self.run_dir).valid)

    def test_malformed_draft(self):
        self.draft = None
        self.assertTrue(self.check().input_error)


class BundleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / 'run'
        self.run_dir.mkdir()
        self.wiki = Path(self.temp.name) / 'wiki'
        self.wiki.mkdir()
        make_bundle(self.run_dir)

    def tearDown(self):
        self.temp.cleanup()

    def draft(self, addition):
        path = self.run_dir / 'page.draft.md'
        path.write_text(path.read_text(encoding='utf-8') + '\n' + addition, encoding='utf-8')
        # Rebind actual draft evidence after an intentional edit.
        from evidence import sha256_file
        report = read_json(self.run_dir / 'validation.json')
        for check in report['checks']:
            for ref in check['evidence']:
                if ref['path'] == 'page.draft.md':
                    ref['sha256'] = sha256_file(path)
        write_json(self.run_dir, 'validation', report)
        seal(self.run_dir, issue=False)

    def issue(self):
        return evaluate_bundle(self.run_dir, roots={'wiki': self.wiki}, write_decision=True)

    def test_fragment_failure_blocks_full_gate(self):
        coverage = read_json(self.run_dir / 'coverage.json')
        coverage['entries']['formula_001'][0]['fragment_ref'] = '#absent'
        write_json(self.run_dir, 'coverage', coverage)
        self.assertEqual(seal(self.run_dir)['decision'], 'revise')

    def test_correct_local_fragment(self):
        self.draft('[Details](#formulas_dependencies)')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_missing_local_fragment(self):
        self.draft('[Details](#absent)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_final_location_used_instead_of_run_directory(self):
        (self.wiki / 'related.md').write_text('## Target {#target}\nText', encoding='utf-8')
        self.draft('[Related](related.md#target)')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_file_only_in_run_directory_does_not_resolve(self):
        (self.run_dir / 'related.md').write_text('Text', encoding='utf-8')
        self.draft('[Related](related.md)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_relative_link_requires_wiki_root(self):
        self.draft('[Related](related.md)')
        self.assertEqual(evaluate_bundle(self.run_dir, write_decision=True)['decision'], 'revise')

    def test_dangling_link_in_target_markdown(self):
        (self.wiki / 'related.md').write_text('## Target\nText', encoding='utf-8')
        self.draft('[Related](related.md#absent)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_reference_links_and_encoded_filenames(self):
        (self.wiki / 'other page.md').write_text('## Target\nText', encoding='utf-8')
        self.draft('[Related][ref]\n\n[ref]: other%20page.md#target')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_self_link_uses_draft_before_publication(self):
        self.draft(f'[Self]({PAGE_ID}#schema_signature)')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_link_inside_code_is_not_checked(self):
        self.draft('```\n[Example](missing.md)\n```')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_missing_image_is_detected(self):
        self.draft('![Diagram](missing.png)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_external_url_is_not_fetched(self):
        self.draft('[Website](https://example.invalid/#absent)')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_path_escape_rejected(self):
        (Path(self.temp.name) / 'outside.md').write_text('Text', encoding='utf-8')
        self.draft('[Outside](../outside.md)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_declared_source_root_and_line_anchor(self):
        self.draft('[SQL](../run/source.sql#L2-L6)')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_invalid_line_anchor(self):
        self.draft('[SQL](../run/source.sql#L2-L600)')
        self.assertEqual(self.issue()['decision'], 'revise')

    def test_comment_links_are_ignored(self):
        self.draft('<!-- [Related](missing.md) -->')
        self.assertEqual(self.issue()['decision'], 'ready')

    def test_linked_file_changed_during_gate(self):
        target = self.wiki / 'related.md'
        target.write_text('## Target\nText', encoding='utf-8')
        self.draft('[Related](related.md#target)')
        from coverage_gate import validate_coverage as real_validate
        def mutate(*args, **kwargs):
            result = real_validate(*args, **kwargs)
            target.write_text('## Changed\nText', encoding='utf-8')
            return result
        with patch('coverage_gate.validate_coverage', side_effect=mutate):
            result = self.issue()
        self.assertEqual(result['decision'], 'blocked')
        self.assertTrue(any('linked file changed' in e for e in result['errors']))

    def test_archive_retains_final_wiki_link_context(self):
        from bundle import save_bundle
        (self.wiki / 'related.md').write_text('## Target\nText', encoding='utf-8')
        self.draft('[Related](related.md#target)\n\n[SQL](../run/source.sql#L2-L6)')
        self.assertEqual(self.issue()['decision'], 'ready')
        archive = Path(self.temp.name) / 'archive'
        save_bundle(self.run_dir, archive, roots={'wiki': self.wiki})
        self.assertEqual(evaluate_bundle(archive, roots={'wiki': self.wiki, 'link_project': self.run_dir})['decision'], 'ready')

    def test_repeated_gate_checks_current_links(self):
        target = self.wiki / 'related.md'
        target.write_text('## Target\nText', encoding='utf-8')
        self.draft('[Related](related.md#target)')
        self.assertEqual(self.issue()['decision'], 'ready')
        target.write_text('## Changed\nText', encoding='utf-8')
        self.assertFalse(evaluate_bundle(self.run_dir, roots={'wiki': self.wiki})['publication_authorized'])

    def test_semantic_defect_blocks_nonempty_but_wrong_text(self):
        path = self.run_dir / 'page.draft.md'
        path.write_text(path.read_text(encoding='utf-8').replace('1.1', '9.9'), encoding='utf-8')
        self.draft('')
        report = read_json(self.run_dir / 'validation.json')
        check = next(c for c in report['checks'] if c['fact_ids'] == ['formula_001'])
        check.update(status='defect', reason='Independent review: draft says 9.9; SQL and fact say 1.1.')
        write_json(self.run_dir, 'validation', report)
        result = seal(self.run_dir)
        self.assertEqual(result['decision'], 'revise')
        self.assertIn(check['id'], result['decision_record']['blocking_defects'])

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(PACKAGE / 'scripts/coverage_gate.py'), *map(str,args), '--json'],
                              capture_output=True, text=True, encoding='utf-8')

    def test_cli_valid(self):
        p = self.cli('--bundle', self.run_dir)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(json.loads(p.stdout)['publication_authorized'])

    def test_cli_missing_draft(self):
        (self.run_dir / 'page.draft.md').unlink()
        self.assertEqual(self.cli('--bundle', self.run_dir).returncode, 2)

    def test_cli_corrupt_plan_is_not_ignored(self):
        (self.run_dir / 'validation_plan.json').write_text('{bad')
        p = self.cli('--bundle', self.run_dir)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn('Traceback', p.stderr)

    def test_cli_missing_plan_is_error_in_bundle_mode(self):
        (self.run_dir / 'validation_plan.json').unlink()
        self.assertEqual(self.cli('--bundle', self.run_dir).returncode, 2)

    def test_cli_invalid_explicit_policy_not_ignored(self):
        self.assertEqual(self.cli('--bundle', self.run_dir, '--policy', self.run_dir / 'absent.json').returncode, 2)

    def test_cli_invalid_json_shape(self):
        (self.run_dir / 'coverage.json').write_text('[]')
        p = self.cli('--bundle', self.run_dir)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn('Traceback', p.stderr)

    def test_cli_duplicate_json_keys(self):
        (self.run_dir / 'coverage.json').write_text('{"entries":{},"entries":{}}')
        self.assertEqual(self.cli('--bundle', self.run_dir).returncode, 2)

    def test_cli_positional_plan_errors_are_visible(self):
        p = self.cli(self.run_dir / 'coverage.json', self.run_dir / 'page.draft.md', self.run_dir / 'facts.json', '--plan', self.run_dir / 'absent')
        self.assertEqual(p.returncode, 2)

    def test_cli_contract_error_vs_coverage_error(self):
        coverage = read_json(self.run_dir / 'coverage.json')
        del coverage['entries']['formula_001']
        write_json(self.run_dir, 'coverage', coverage)
        self.assertEqual(self.cli('--bundle', self.run_dir).returncode, 1)


if __name__ == '__main__':
    unittest.main()
