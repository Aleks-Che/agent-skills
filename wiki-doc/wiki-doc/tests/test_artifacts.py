"""Tests for artifact schema validation and v2 contracts."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"

# Load artifact_schema module
_spec = importlib.util.spec_from_file_location(
    "artifact_schema", SCRIPTS_DIR / "artifact_schema.py"
)
_schema_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_schema_mod)

# Load validation_gate module
_gate_spec = importlib.util.spec_from_file_location(
    "validation_gate", SCRIPTS_DIR / "validation_gate.py"
)
_gate = importlib.util.module_from_spec(_gate_spec)
_gate_spec.loader.exec_module(_gate)


VALID_RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
OTHER_RUN_ID = "00000000-1111-2222-3333-444444444444"


FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures' / 'artifacts'

def _fixture(name, run_id=VALID_RUN_ID):
    data = json.loads((FIXTURES_DIR / 'valid' / f'{name}.json').read_text(encoding='utf-8'))
    data['run_id'] = run_id
    return data

def valid_facts(run_id=VALID_RUN_ID):
    return _fixture("facts", run_id)


def valid_coverage(run_id=VALID_RUN_ID):
    return _fixture("coverage", run_id)


def valid_validation(run_id=VALID_RUN_ID):
    return _fixture("validation", run_id)


def valid_validation_plan(run_id=VALID_RUN_ID):
    return _fixture("validation_plan", run_id)


def valid_manifest(run_id=VALID_RUN_ID):
    return _fixture("manifest", run_id)


def valid_inventory(run_id=VALID_RUN_ID):
    return _fixture("inventory", run_id)


def valid_decision(run_id=VALID_RUN_ID):
    return _fixture("decision", run_id)


def write_artifacts(tmpdir, artifacts):
    """Write artifact dicts to JSON files in tmpdir."""
    for name, data in artifacts.items():
        with open(tmpdir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(data, f)


class SchemaValidationTests(unittest.TestCase):
    """Validate that valid artifacts pass schema checks."""

    def test_valid_facts_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_facts(), schemas["facts"], "facts")
        self.assertEqual(errors, [])

    def test_valid_coverage_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_coverage(), schemas["coverage"], "coverage")
        self.assertEqual(errors, [])

    def test_valid_validation_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_validation(), schemas["validation"], "validation")
        self.assertEqual(errors, [])

    def test_valid_plan_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_validation_plan(), schemas["validation_plan"], "plan")
        self.assertEqual(errors, [])

    def test_valid_manifest_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_manifest(), schemas["manifest"], "manifest")
        self.assertEqual(errors, [])

    def test_valid_inventory_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_inventory(), schemas["inventory"], "inventory")
        self.assertEqual(errors, [])

    def test_valid_decision_schema(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(valid_decision(), schemas["decision"], "decision")
        self.assertEqual(errors, [])


class SchemaVersionTests(unittest.TestCase):
    """Verify schema_version=2 is required."""

    def test_missing_schema_version_rejected(self):
        data = valid_facts()
        del data["schema_version"]
        errors = _schema_mod.check_schema_version(data, "facts")
        self.assertTrue(any("missing schema_version" in e for e in errors))

    def test_wrong_schema_version_rejected(self):
        data = valid_facts()
        data["schema_version"] = 1
        errors = _schema_mod.check_schema_version(data, "facts")
        self.assertTrue(any("expected 2" in e for e in errors))


class UniqueIdTests(unittest.TestCase):
    """Verify duplicate IDs are rejected."""

    def test_duplicate_operation_id_rejected(self):
        data = valid_facts()
        data["operations"].append(dict(data["operations"][0]))
        errors = _schema_mod.check_unique_ids(data, "facts", [("operations", "id")])
        self.assertTrue(any("duplicate" in e and "op_001" in e for e in errors))

    def test_duplicate_column_id_rejected(self):
        data = valid_facts()
        data["columns"].append(dict(data["columns"][0]))
        errors = _schema_mod.check_unique_ids(data, "facts", [("columns", "id")])
        self.assertTrue(any("duplicate" in e and "col_001" in e for e in errors))

    def test_unique_ids_pass(self):
        data = valid_facts()
        id_paths = [
            ("objects", "id"), ("definitions", "id"), ("operations", "id"),
            ("columns", "id"), ("formulas", "id"), ("conditions", "id"),
            ("unknowns", "id"),
        ]
        errors = _schema_mod.check_unique_ids(data, "facts", id_paths)
        self.assertEqual(errors, [])


class RunConsistencyTests(unittest.TestCase):
    """Verify run_id consistency across artifacts."""

    def test_matching_run_ids_pass(self):
        artifacts = {"facts": valid_facts(), "coverage": valid_coverage()}
        errors = _schema_mod.check_run_consistency(artifacts)
        self.assertEqual(errors, [])

    def test_mismatched_run_ids_rejected(self):
        artifacts = {"facts": valid_facts(), "coverage": valid_coverage(run_id=OTHER_RUN_ID)}
        errors = _schema_mod.check_run_consistency(artifacts)
        self.assertTrue(any("run_id mismatch" in e for e in errors))


class CrossReferenceTests(unittest.TestCase):
    """Verify coverage entries reference existing fact IDs."""

    def test_coverage_references_valid_facts(self):
        errors = _schema_mod.check_fact_references(
            valid_coverage(), valid_facts(), "coverage"
        )
        self.assertEqual(errors, [])

    def test_coverage_references_unknown_fact_rejected(self):
        cov = valid_coverage()
        cov["entries"]["nonexistent_999"] = [{"section_id": "test"}]
        errors = _schema_mod.check_fact_references(cov, valid_facts(), "coverage")
        self.assertTrue(any("nonexistent_999" in e for e in errors))


class InvalidSchemaRejectionTests(unittest.TestCase):
    """Verify invalid data is rejected by schema validation."""

    def test_facts_missing_required_fields(self):
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema({}, schemas["facts"], "facts")
        self.assertTrue(len(errors) > 0)

    def test_facts_invalid_object_kind(self):
        data = valid_facts()
        data["objects"][0]["kind"] = "invalid_kind"
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(data, schemas["facts"], "facts")
        self.assertTrue(len(errors) > 0)

    def test_facts_invalid_source_ref_missing_sha256(self):
        data = valid_facts()
        del data["objects"][0]["source_refs"][0]["sha256"]
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(data, schemas["facts"], "facts")
        self.assertTrue(len(errors) > 0)

    def test_facts_invalid_sha256_format(self):
        data = valid_facts()
        data["objects"][0]["source_refs"][0]["sha256"] = "tooshort"
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(data, schemas["facts"], "facts")
        self.assertTrue(len(errors) > 0)

    def test_validation_check_missing_required_fields(self):
        data = valid_validation()
        data["checks"][0] = {"id": "test"}
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(data, schemas["validation"], "validation")
        self.assertTrue(len(errors) > 0)

    def test_coverage_not_versioned_dict_rejected(self):
        """Old unversioned coverage format should be rejected by v2 schema."""
        schemas = _schema_mod.load_schemas()
        old_format = {"op_001": ["Section A"]}
        errors = _schema_mod.validate_schema(old_format, schemas["coverage"], "coverage")
        self.assertTrue(len(errors) > 0)

    def test_run_id_format_rejected(self):
        data = valid_facts()
        data["run_id"] = "not-a-uuid"
        schemas = _schema_mod.load_schemas()
        errors = _schema_mod.validate_schema(data, schemas["facts"], "facts")
        self.assertTrue(any("run_id" in e or "pattern" in e.lower() for e in errors))


class FullArtifactSetTests(unittest.TestCase):
    """Test validate_artifact_set with real files."""

    def test_valid_complete_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            write_artifacts(tmpdir, {
                "facts": valid_facts(),
                "coverage": valid_coverage(),
                "validation": valid_validation(),
                "validation_plan": valid_validation_plan(),
                "manifest": valid_manifest(),
                "inventory": valid_inventory(),
                "decision": valid_decision(),
            })
            errors = _schema_mod.validate_artifact_set(tmpdir)
            self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_explicit_partial_stage_can_validate_facts(self):
        """An unfinished stage is accepted only as an explicit partial check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            write_artifacts(tmpdir, {"facts": valid_facts()})
            errors = _schema_mod.validate_artifact_set(tmpdir, required=["facts"])
            self.assertEqual(errors, [])

    def test_invalid_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            with open(tmpdir / "facts.json", "w") as f:
                f.write("{invalid json")
            with self.assertRaisesRegex(_schema_mod.ArtifactInputError, "invalid JSON"):
                _schema_mod.validate_artifact_set(tmpdir)


class ValidationGateV2Tests(unittest.TestCase):
    """Legacy report-only arithmetic remains usable; this is not bundle approval."""

    def test_v2_validation_report_accepted(self):
        report = valid_validation()
        result = _gate.evaluate(report)
        self.assertEqual(result["decision"], "ready")

    def test_gate_requires_mandatory_checks(self):
        report = valid_validation()
        report["checks"] = report["checks"][:2]  # Remove one mandatory
        with self.assertRaises(ValueError):
            _gate.evaluate(report)

    def test_gate_blocks_on_defect(self):
        report = valid_validation()
        report["checks"][0]["status"] = "defect"
        result = _gate.evaluate(report)
        self.assertEqual(result["decision"], "revise")

    def test_gate_blocks_on_inconclusive(self):
        report = valid_validation()
        report["checks"][0]["status"] = "inconclusive"
        result = _gate.evaluate(report)
        self.assertEqual(result["decision"], "blocked")


if __name__ == "__main__":
    unittest.main()
