"""Tests for Q-05 finalize and provenance: full pipeline chain and page provenance."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from run_prepare import cmd_provenance, cmd_finalize, RUN_CONTEXT_FILE


class ProvenanceTests(unittest.TestCase):
    def test_no_metadata_directory_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = type('Args', (), {'wiki_root': tmp, 'page': ['demo.md']})
            result = cmd_provenance(args)
            self.assertEqual(result, 1)

    def test_empty_metadata_directory_cannot_verify_a_selected_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            (wiki / '.wiki-doc' / 'pages').mkdir(parents=True)
            args = type('Args', (), {'wiki_root': str(wiki), 'page': ['demo.md']})
            result = cmd_provenance(args)
            self.assertEqual(result, 1)

    def test_required_field_names_alone_cannot_prove_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            meta_dir = wiki / '.wiki-doc' / 'pages'
            meta_dir.mkdir(parents=True)
            meta = {
                'schema_version': 1, 'page_id': 'test-page', 'canonical_key': 'function+demo+f+()',
                'page_sha256': 'a' * 64, 'bundle': '.wiki-doc/runs/test-run',
                'run_id': 'test-run-id', 'profile': None, 'source_files': [],
            }
            (meta_dir / 'test-page.json').write_text(json.dumps(meta), encoding='utf8')
            args = type('Args', (), {'wiki_root': str(wiki), 'page': ['test-page.md']})
            result = cmd_provenance(args)
            self.assertEqual(result, 1)

    def test_metadata_missing_fields_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp)
            meta_dir = wiki / '.wiki-doc' / 'pages'
            meta_dir.mkdir(parents=True)
            meta = {'schema_version': 1, 'page_id': 'test-page'}
            (meta_dir / 'test-page.json').write_text(json.dumps(meta), encoding='utf8')
            args = type('Args', (), {'wiki_root': str(wiki), 'page': ['test-page.md']})
            result = cmd_provenance(args)
            self.assertEqual(result, 1)


class FinalizeTests(unittest.TestCase):
    def test_missing_run_context_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = type('Args', (), {'run_dir': tmp})
            result = cmd_finalize(args)
            self.assertEqual(result, 1)

    def test_limitation_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'limitation.json').write_text(json.dumps({'status': 'limitation'}), encoding='utf8')
            args = type('Args', (), {'run_dir': str(run)})
            result = cmd_finalize(args)
            self.assertEqual(result, 1)


if __name__ == '__main__':
    unittest.main()
