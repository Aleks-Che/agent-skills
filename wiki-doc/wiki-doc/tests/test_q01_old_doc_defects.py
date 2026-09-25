"""Q-01 reproducible kit: old documentation defects against the pinned control SQL.

The old page and the old audit stay external control inputs of the test project.
Each checked assertion is a pair: the false claim signature present in those
inputs and independent SQL/DDL evidence that contradicts it. The specification —
the list of checked assertions — is examples/fixtures/old-doc-defects.json;
numbers only apply to its pinned bytes.
"""
import json
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from ddl import reconstruct
from sql_extract import extract_inventory, sha256_file

SPEC_PATH = PACKAGE / 'examples' / 'fixtures' / 'old-doc-defects.json'


class OldDocumentationDefectKit(unittest.TestCase):
    """Execute the D01-D12 assertion list and its positive controls."""

    @classmethod
    def setUpClass(cls):
        raw = os.environ.get('WIKI_DOC_ACCEPTANCE_PROJECT', '')
        root = Path(raw) if raw else None
        if root is None or not root.is_dir():
            raise unittest.SkipTest('control project not configured (WIKI_DOC_ACCEPTANCE_PROJECT)')
        cls.root = root
        cls.spec = json.loads(SPEC_PATH.read_text(encoding='utf-8'))
        for name, entry in cls.spec['inputs'].items():
            path = root / entry['path']
            if not path.is_file():
                raise AssertionError(f'control input is missing: {name}: {path}')
            digest = sha256_file(path)
            if digest != entry['sha256']:
                raise AssertionError(f'{name}: {cls.spec["hash_policy"]["on_hash_mismatch"]}: '
                                     f'{digest} != {entry["sha256"]}')
        sql_entry = cls.spec['inputs']['control_sql']
        cls.sql_text = (root / sql_entry['path']).read_text(encoding='utf-8-sig')
        cls.sql_lines = cls.sql_text.splitlines()
        cls.page_text = (root / cls.spec['inputs']['old_page']['path']).read_text(encoding='utf-8')
        cls.audit_text = (root / cls.spec['inputs']['old_audit']['path']).read_text(encoding='utf-8')
        cls.inventory = extract_inventory(
            cls.sql_text, sql_entry['path'], sql_entry['sha256'],
            dialect='greenplum', version='unknown', documented_subjects=[
                's_gp_p1024_dmr_svd_kb_ckr_uup_gp_core.ckr_uup_db_onboarding'])
        cls.tables = cls._reconstructed_tables()

    @classmethod
    def _reconstructed_tables(cls):
        import os.path
        import tempfile
        scenario = json.loads((PACKAGE / 'examples' / 'fixtures' / 'acceptance-large.json')
                              .read_text(encoding='utf-8'))
        sources = [cls.root / e['path'] for e in scenario['ddl_acceptance']['ordered_inputs']]
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        mdir = Path(temporary.name)
        ordered = [os.path.relpath(p, mdir).replace('\\', '/') for p in sources]
        (mdir / 'manifest.json').write_text(json.dumps(dict(
            dialect='greenplum', version='unknown',
            target_revision='q01-old-doc-defect-kit', ordered_files=ordered)), encoding='utf-8')
        result = reconstruct(mdir / 'manifest.json', project_root=cls.root)
        if result['status'] != 'resolved':
            raise AssertionError(f'DDL reconstruction failed: {result["errors"]}')
        return result['tables']

    def _check_evidence(self, evidence):
        for item in evidence:
            kind = item['kind']
            with self.subTest(evidence=kind):
                if kind == 'sql_contains':
                    for text in item['strings']:
                        self.assertIn(text, self.sql_text)
                elif kind == 'sql_line_contains':
                    self.assertIn(item['contains'], self.sql_lines[item['line'] - 1])
                elif kind == 'sql_lines_lack':
                    block = '\n'.join(self.sql_lines[item['from'] - 1:item['to']])
                    for text in item['strings']:
                        self.assertNotIn(text, block)
                elif kind == 'ddl_table_columns':
                    columns = self.tables[item['table']]['columns']
                    self.assertEqual(len(columns), item['count'],
                                     [c['name'] for c in columns])
                elif kind == 'inventory_dml_counts':
                    counts = Counter(i['kind'] for i in self.inventory['items'])
                    for name, expected in item['counts'].items():
                        self.assertEqual(counts[name], expected)
                elif kind == 'inventory_call_counts':
                    total = sum(any(call.endswith(item['suffix']) for call in i.get('calls', []))
                                for i in self.inventory['items'])
                    self.assertEqual(total, item['count'])
                else:
                    self.fail(f'unknown evidence kind: {kind}')

    def test_spec_covers_every_review_defect(self):
        ids = [d['id'] for d in self.spec['defects']]
        self.assertEqual(ids, [f'D{i:02d}' for i in range(1, 13)])
        self.assertTrue(self.spec['positive_controls'])

    def test_every_documented_defect_is_present_and_contradicted(self):
        for defect in self.spec['defects']:
            with self.subTest(defect=defect['id']):
                self.assertTrue(defect['checked_claim'])
                for text in defect.get('page_contains', []):
                    self.assertIn(text, self.page_text)
                for text in defect.get('page_lacks', []):
                    self.assertNotIn(text, self.page_text)
                for text in defect.get('audit_contains', []):
                    self.assertIn(text, self.audit_text)
                self._check_evidence(defect['evidence'])

    def test_correct_statements_of_the_old_page_are_not_flagged(self):
        for control in self.spec['positive_controls']:
            with self.subTest(control=control['id']):
                self.assertTrue(control['checked_claim'])
                for text in control.get('page_contains', []):
                    self.assertIn(text, self.page_text)
                self._check_evidence(control['evidence'])


if __name__ == '__main__':
    unittest.main()
