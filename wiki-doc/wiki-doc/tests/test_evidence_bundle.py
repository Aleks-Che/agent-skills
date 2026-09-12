"""Tests for evidence.py and bundle.py."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

from evidence import (
    sha256_bytes, sha256_file, register_root, get_root, clear_roots,
    validate_evidence, validate_source_ref, validate_source_refs,
    check_evidence_against_inputs, check_evidence_existence,
    EvidenceError, EvidenceRef, _line_count, extract_lines_text,
)
from bundle import (
    create_manifest, write_manifest, verify_manifest_hashes,
    verify_decision_against_manifest, reverify_bundle, save_bundle,
    compute_tool_versions, BundleError,
)


class HashTests(unittest.TestCase):
    """SHA-256 hashing of bytes and files."""

    def test_sha256_bytes_empty(self):
        # SHA-256 of empty bytes
        result = sha256_bytes(b'')
        self.assertEqual(len(result), 64)
        self.assertEqual(result, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_sha256_bytes_hello(self):
        result = sha256_bytes(b'hello')
        self.assertEqual(len(result), 64)
        self.assertNotEqual(result, sha256_bytes(b''))

    def test_sha256_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as f:
            f.write(b'test content')
            path = Path(f.name)
        try:
            result = sha256_file(path)
            self.assertEqual(result, sha256_bytes(b'test content'))
        finally:
            path.unlink()


class LineExtractionTests(unittest.TestCase):
    """Line counting and extraction from raw bytes."""

    def test_line_count_single_line(self):
        self.assertEqual(_line_count(b'hello'), 1)

    def test_line_count_with_newline(self):
        self.assertEqual(_line_count(b'hello\n'), 1)

    def test_line_count_multiline(self):
        self.assertEqual(_line_count(b'a\nb\nc'), 3)

    def test_line_count_empty(self):
        self.assertEqual(_line_count(b''), 0)

    def test_extract_lines_basic(self):
        data = b'line1\nline2\nline3'
        self.assertEqual(extract_lines_text(data, 1, 1), 'line1')
        self.assertEqual(extract_lines_text(data, 2, 2), 'line2')
        self.assertEqual(extract_lines_text(data, 1, 3), 'line1\nline2\nline3')

    def test_extract_lines_out_of_bounds(self):
        data = b'a\nb'
        with self.assertRaises(ValueError):
            extract_lines_text(data, 1, 5)

    def test_extract_lines_invalid_range(self):
        data = b'a\nb'
        with self.assertRaises(ValueError):
            extract_lines_text(data, 2, 1)


class RootManagementTests(unittest.TestCase):
    """Root registration and lookup."""

    def setUp(self):
        clear_roots()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        clear_roots()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_get_root(self):
        register_root('project', self.tmp)
        root = get_root('project')
        self.assertEqual(root.name, 'project')
        self.assertEqual(root.path, self.tmp.resolve())

    def test_get_unknown_root_raises(self):
        with self.assertRaises(ValueError):
            get_root('nonexistent')

    def test_register_non_dir_raises(self):
        f = self.tmp / 'file.txt'
        f.write_text('x')
        with self.assertRaises(ValueError):
            register_root('bad', f)

    def test_root_contains(self):
        register_root('project', self.tmp)
        root = get_root('project')
        sub = self.tmp / 'sub' / 'file.sql'
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text('SELECT 1;')
        self.assertTrue(root.contains(sub.resolve()))

    def test_root_does_not_contain_outside(self):
        register_root('project', self.tmp)
        root = get_root('project')
        outside = Path(tempfile.mkdtemp()) / 'file.txt'
        outside.write_text('x')
        try:
            self.assertFalse(root.contains(outside.resolve()))
        finally:
            outside.unlink()
            outside.parent.rmdir()


class EvidenceValidationTests(unittest.TestCase):
    """Validate evidence refs against file system."""

    def setUp(self):
        clear_roots()
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / 'project'
        self.project.mkdir()
        self.sql_file = self.project / 'test.sql'
        self.sql_file.write_text('SELECT 1;\nSELECT 2;\nSELECT 3;\n')
        self.sha = sha256_file(self.sql_file)
        register_root('project', self.project)

    def tearDown(self):
        clear_roots()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_evidence(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 2,
            'sha256': self.sha,
        }
        result = validate_evidence(ref, {})
        self.assertIsInstance(result, EvidenceRef)
        self.assertEqual(result.path, 'test.sql')

    def test_missing_field_raises(self):
        ref = {'root': 'project', 'path': 'test.sql', 'start_line': 1}
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_unknown_root_raises(self):
        ref = {
            'root': 'unknown',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_file_not_found_raises(self):
        ref = {
            'root': 'project',
            'path': 'nonexistent.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_hash_mismatch_raises(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': '0' * 64,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_line_range_exceeds_file_raises(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 100,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_end_line_before_start_raises(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 3,
            'end_line': 1,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_start_line_zero_raises(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 0,
            'end_line': 1,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_skip_line_check(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 100,
            'sha256': self.sha,
        }
        result = validate_evidence(ref, {}, check_lines=False)
        self.assertIsInstance(result, EvidenceRef)

    def test_skip_hash_check(self):
        ref = {
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': '0' * 64,
        }
        result = validate_evidence(ref, {}, check_hash=False)
        self.assertIsInstance(result, EvidenceRef)

    def test_path_escape_raises(self):
        ref = {
            'root': 'project',
            'path': '../escape.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': self.sha,
        }
        with self.assertRaises(EvidenceError):
            validate_evidence(ref, {})

    def test_validate_source_refs(self):
        refs = [
            {
                'root': 'project',
                'path': 'test.sql',
                'start_line': 1,
                'end_line': 1,
                'sha256': self.sha,
            },
            {
                'root': 'project',
                'path': 'test.sql',
                'start_line': 2,
                'end_line': 3,
                'sha256': self.sha,
            },
        ]
        results = validate_source_refs(refs, {})
        self.assertEqual(len(results), 2)

    def test_root_override(self):
        """Roots passed to validate_evidence override registered roots."""
        alt = Path(tempfile.mkdtemp())
        alt_file = alt / 'alt.sql'
        alt_file.write_text('SELECT 1;')
        try:
            ref = {
                'root': 'alt',
                'path': 'alt.sql',
                'start_line': 1,
                'end_line': 1,
                'sha256': sha256_file(alt_file),
            }
            result = validate_evidence(ref, {'alt': alt})
            self.assertEqual(result.path, 'alt.sql')
        finally:
            shutil.rmtree(alt, ignore_errors=True)


class EvidenceChecksTests(unittest.TestCase):
    """Higher-level evidence checks against inputs."""

    def setUp(self):
        clear_roots()
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / 'project'
        self.project.mkdir()
        self.file = self.project / 'test.sql'
        self.file.write_text('SELECT 1;\n')
        self.sha = sha256_file(self.file)
        register_root('project', self.project)

    def tearDown(self):
        clear_roots()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_evidence_against_inputs_match(self):
        evidence = [{'path': 'test.sql', 'sha256': self.sha}]
        inputs = [{'path': 'test.sql', 'sha256': self.sha}]
        errors = check_evidence_against_inputs(evidence, inputs, {})
        self.assertEqual(errors, [])

    def test_check_evidence_against_inputs_mismatch(self):
        evidence = [{'path': 'test.sql', 'sha256': '0' * 64}]
        inputs = [{'path': 'test.sql', 'sha256': self.sha}]
        errors = check_evidence_against_inputs(evidence, inputs, {})
        self.assertEqual(len(errors), 1)
        self.assertIn('mismatch', errors[0])

    def test_check_evidence_existence(self):
        evidence = [{
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': self.sha,
        }]
        errors = check_evidence_existence(evidence, {})
        self.assertEqual(errors, [])

    def test_check_evidence_existence_missing(self):
        evidence = [{
            'root': 'project',
            'path': 'missing.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': self.sha,
        }]
        errors = check_evidence_existence(evidence, {})
        self.assertEqual(len(errors), 1)
        self.assertIn('not found', errors[0])


class BundleCreationTests(unittest.TestCase):
    """Create manifest from run directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        self.package_dir = Path(__file__).resolve().parent.parent

        # Create minimal run files
        self.sql = self.run_dir / 'test.sql'
        self.sql.write_text('SELECT 1;\n')

        # Create minimal artifact files
        self._create_artifact('facts', {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'dialect': {'name': 'postgres', 'version': '15'},
            'documented_object_ids': ['obj_1'],
            'inputs': [{'path': 'test.sql', 'sha256': sha256_file(self.sql)}],
            'objects': [], 'definitions': [], 'operations': [],
            'columns': [], 'formulas': [], 'conditions': [], 'unknowns': [],
        })
        self._create_artifact('inventory', {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'dialect': {'name': 'postgres', 'version': '15'},
            'items': [], 'coverage_notes': [],
            'inputs': [{'path': 'test.sql', 'sha256': sha256_file(self.sql)}],
            'documented_subjects': ['obj_1'],
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_artifact(self, name, data):
        (self.run_dir / f'{name}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_create_manifest_basic(self):
        tool_versions = {
            'scripts_sha256': 'a' * 64,
            'skill_md_sha256': 'b' * 64,
            'template_sha256': 'c' * 64,
            'policy_sha256': 'd' * 64,
        }
        manifest = create_manifest(
            run_id='11111111-1111-1111-1111-111111111111',
            page_id='test_page',
            sql_files=[self.sql],
            artifacts_dir=self.run_dir,
            tool_versions=tool_versions,
        )
        self.assertEqual(manifest['schema_version'], 2)
        self.assertEqual(manifest['run_id'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(manifest['page_id'], 'test_page')
        self.assertEqual(len(manifest['sql_files']), 1)
        self.assertIn('facts', manifest['artifacts'])
        self.assertIn('inventory', manifest['artifacts'])

    def test_create_manifest_generates_run_id(self):
        manifest = create_manifest(
            page_id='test_page',
            sql_files=[self.sql],
            artifacts_dir=self.run_dir,
            tool_versions={'scripts_sha256': 'a' * 64, 'skill_md_sha256': 'b' * 64,
                          'template_sha256': 'c' * 64, 'policy_sha256': 'd' * 64},
        )
        self.assertIn('run_id', manifest)
        self.assertEqual(len(manifest['run_id']), 36)  # UUID format

    def test_write_and_read_manifest(self):
        manifest = create_manifest(
            page_id='test_page',
            sql_files=[self.sql],
            artifacts_dir=self.run_dir,
            tool_versions={'scripts_sha256': 'a' * 64, 'skill_md_sha256': 'b' * 64,
                          'template_sha256': 'c' * 64, 'policy_sha256': 'd' * 64},
        )
        out = self.run_dir / 'manifest.json'
        write_manifest(manifest, out)
        loaded = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(loaded['run_id'], manifest['run_id'])


class BundleVerificationTests(unittest.TestCase):
    """Verify manifest hashes and decision consistency."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()

        self.sql = self.run_dir / 'test.sql'
        self.sql.write_text('SELECT 1;\n')
        (self.run_dir / 'validation.json').write_text('{"run_id": "00000000-0000-0000-0000-000000000000"}')
        self.sha = sha256_file(self.sql)

        self._create_artifact('facts', {
            'schema_version': 2, 'run_id': '00000000-0000-0000-0000-000000000000',
            'dialect': {'name': 'postgres', 'version': '15'},
            'documented_object_ids': ['obj_1'],
            'inputs': [{'path': 'test.sql', 'sha256': self.sha}],
            'objects': [], 'definitions': [], 'operations': [],
            'columns': [], 'formulas': [], 'conditions': [], 'unknowns': [],
        })

        self.manifest = create_manifest(
            run_id='00000000-0000-0000-0000-000000000000',
            page_id='test_page',
            sql_files=[self.sql],
            artifacts_dir=self.run_dir,
            tool_versions={'scripts_sha256': 'a' * 64, 'skill_md_sha256': 'b' * 64,
                          'template_sha256': 'c' * 64, 'policy_sha256': 'd' * 64},
        )
        write_manifest(self.manifest, self.run_dir / 'manifest.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_artifact(self, name, data):
        (self.run_dir / f'{name}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_verify_manifest_hashes_passes(self):
        errors = verify_manifest_hashes(self.manifest, self.run_dir)
        self.assertEqual(errors, [])

    def test_verify_manifest_hashes_detects_corruption(self):
        # Corrupt the SQL file
        self.sql.write_text('CORRUPTED')
        errors = verify_manifest_hashes(self.manifest, self.run_dir)
        self.assertTrue(any('mismatch' in e for e in errors))

    def test_verify_manifest_hashes_detects_missing_file(self):
        self.sql.unlink()
        errors = verify_manifest_hashes(self.manifest, self.run_dir)
        self.assertTrue(any('not found' in e for e in errors))

    def test_verify_decision_against_manifest_passes(self):
        manifest_hash = sha256_file(self.run_dir / 'manifest.json')
        decision = {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'page_id': 'test_page',
            'decision': 'ready',
            'manifest_sha256': manifest_hash,
            'validation_sha256': sha256_file(self.run_dir / 'validation.json'),
        }
        errors = verify_decision_against_manifest(decision, self.manifest, self.run_dir)
        self.assertEqual(errors, [])

    def test_verify_decision_detects_manifest_hash_mismatch(self):
        decision = {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'page_id': 'test_page',
            'decision': 'ready',
            'manifest_sha256': '0' * 64,
            'validation_sha256': sha256_file(self.run_dir / 'validation.json'),
        }
        errors = verify_decision_against_manifest(decision, self.manifest, self.run_dir)
        self.assertTrue(any('mismatch' in e for e in errors))

    def test_verify_decision_detects_run_id_mismatch(self):
        manifest_hash = sha256_file(self.run_dir / 'manifest.json')
        decision = {
            'schema_version': 2,
            'run_id': '11111111-1111-1111-1111-111111111111',
            'page_id': 'test_page',
            'decision': 'ready',
            'manifest_sha256': manifest_hash,
            'validation_sha256': sha256_file(self.run_dir / 'validation.json'),
        }
        errors = verify_decision_against_manifest(decision, self.manifest, self.run_dir)
        self.assertTrue(any('run_id' in e for e in errors))


class ReverifyBundleTests(unittest.TestCase):
    """Full bundle re-verification."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        from bundle_fixture import make_bundle
        make_bundle(self.run_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_artifact(self, name, data):
        (self.run_dir / f'{name}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_reverify_without_decision(self):
        (self.run_dir / 'decision.json').unlink()
        result = reverify_bundle(self.run_dir, require_decision=False)
        self.assertTrue(result['valid'])
        self.assertIsNone(result['decision'])

    def test_reverify_missing_manifest(self):
        (self.run_dir / 'manifest.json').unlink()
        result = reverify_bundle(self.run_dir)
        self.assertFalse(result['valid'])
        self.assertIn('not found', result['errors'][0])

    def test_reverify_with_valid_decision(self):
        result = reverify_bundle(self.run_dir)
        self.assertTrue(result['valid'], result)
        self.assertFalse(result['publication_authorized'])
        self.assertIsNotNone(result['decision'])

    def test_reverify_with_corrupted_decision(self):
        decision = {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'page_id': 'test_page',
            'decision': 'ready',
            'manifest_sha256': '0' * 64,
            'validation_sha256': sha256_file(self.run_dir / 'validation.json'),
        }
        (self.run_dir / 'decision.json').write_text(
            json.dumps(decision, indent=2), encoding='utf-8')
        result = reverify_bundle(self.run_dir)
        self.assertFalse(result['valid'])


class BundleStorageTests(unittest.TestCase):
    """Long-term bundle storage."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        from bundle_fixture import make_bundle
        make_bundle(self.run_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_bundle(self):
        dest = self.tmp / 'storage' / 'runs' / 'test_run'
        result = save_bundle(self.run_dir, dest)
        self.assertEqual(result, dest)
        self.assertTrue((dest / 'manifest.json').exists())
        self.assertTrue((dest / 'facts.json').exists())


class CLITests(unittest.TestCase):
    """CLI commands for evidence and bundle."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        self.package_dir = Path(__file__).resolve().parent.parent

        self.sql = self.run_dir / 'test.sql'
        self.sql.write_text('SELECT 1;\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundle_create_and_verify(self):
        from bundle import main as bundle_main

        # Create
        rc = bundle_main([
            'create', str(self.run_dir),
            '--page-id', 'test_page',
            '--package-dir', str(self.package_dir),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((self.run_dir / 'manifest.json').exists())

        # Verify
        rc = bundle_main(['verify', str(self.run_dir), '--no-decision'])
        self.assertEqual(rc, 0)

    def test_evidence_validate(self):
        from evidence import main as evidence_main
        clear_roots()

        # Create evidence file
        ev_file = self.run_dir / 'evidence.json'
        sha = sha256_file(self.sql)
        ev_file.write_text(json.dumps([{
            'root': 'project',
            'path': 'test.sql',
            'start_line': 1,
            'end_line': 1,
            'sha256': sha,
        }]))

        rc = evidence_main([
            'validate', str(ev_file),
            '--root', 'project', str(self.run_dir),
        ])
        self.assertEqual(rc, 0)


class BundleGateTests(unittest.TestCase):
    """Bundle-level gate evaluation (P0-05)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        self.package_dir = Path(__file__).resolve().parent.parent

        self.sql = self.run_dir / 'test.sql'
        self.sql.write_text('CREATE FUNCTION test_func() RETURNS void AS $$\nBEGIN\n  SELECT 1;\nEND;\n$$ LANGUAGE plpgsql;\n')
        self.sha = sha256_file(self.sql)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_artifact(self, name, data):
        (self.run_dir / f'{name}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def _create_full_bundle(self):
        from bundle_fixture import make_bundle
        make_bundle(self.run_dir)
        self.sql = self.run_dir / 'source.sql'

    def test_gate_blocks_without_manifest(self):
        from validation_gate import evaluate_bundle
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'blocked')
        self.assertIn('manifest.json not found', result['errors'][0])

    def test_gate_blocks_with_corrupted_manifest(self):
        from validation_gate import evaluate_bundle
        (self.run_dir / 'manifest.json').write_text('CORRUPTED')
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'blocked')

    def test_gate_blocks_when_sql_hash_changed(self):
        from validation_gate import evaluate_bundle
        self._create_full_bundle()
        # Corrupt SQL after manifest creation
        self.sql.write_text('SELECT 999;\n')
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'blocked')
        self.assertTrue(any('mismatch' in e for e in result['errors']))

    def test_gate_blocks_when_validation_missing(self):
        from validation_gate import evaluate_bundle
        # Create manifest but no validation
        from bundle import create_manifest, write_manifest
        manifest = create_manifest(
            page_id='test_page',
            sql_files=[self.sql],
            artifacts_dir=self.run_dir,
            tool_versions={'scripts_sha256': 'a' * 64, 'skill_md_sha256': 'b' * 64,
                          'template_sha256': 'c' * 64, 'policy_sha256': 'd' * 64},
        )
        write_manifest(manifest, self.run_dir / 'manifest.json')
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'blocked')

    def test_gate_blocks_when_decision_hash_mismatch(self):
        from validation_gate import evaluate_bundle
        self._create_full_bundle()
        # Create decision with wrong manifest hash
        from bundle import sha256_file as bundle_sha
        manifest_hash = bundle_sha(self.run_dir / 'manifest.json')
        decision = {
            'schema_version': 2,
            'run_id': '00000000-0000-0000-0000-000000000000',
            'page_id': 'test_page',
            'decision': 'ready',
            'manifest_sha256': '0' * 64,  # Wrong hash
            'validation_sha256': 'b' * 64,
        }
        (self.run_dir / 'decision.json').write_text(
            json.dumps(decision, indent=2), encoding='utf-8')
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'blocked')
        self.assertTrue(any('mismatch' in e.lower() for e in result['errors']),
                       f'Expected mismatch error, got: {result["errors"]}')

    def test_gate_blocks_when_run_id_mismatch(self):
        from validation_gate import evaluate_bundle
        self._create_full_bundle()
        # Corrupt facts run_id
        facts = json.loads((self.run_dir / 'facts.json').read_text(encoding='utf-8'))
        facts['run_id'] = '11111111-1111-1111-1111-111111111111'
        (self.run_dir / 'facts.json').write_text(
            json.dumps(facts, indent=2), encoding='utf-8')
        result = evaluate_bundle(self.run_dir)
        # Should detect run_id mismatch in schema validation
        self.assertIn(result['decision'], ('blocked', 'revise'))

    def test_gate_ready_with_valid_bundle(self):
        from validation_gate import evaluate_bundle
        self._create_full_bundle()
        result = evaluate_bundle(self.run_dir)
        self.assertEqual(result['decision'], 'ready', result)
        self.assertTrue(result['publication_authorized'])


if __name__ == '__main__':
    unittest.main()
