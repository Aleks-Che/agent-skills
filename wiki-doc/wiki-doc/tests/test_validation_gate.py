import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "validation_gate.py"
SPEC = importlib.util.spec_from_file_location("validation_gate", PATH)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def check(name, status="ok", blocking=False):
    return {
        "id": name, "status": status, "blocking": blocking,
        "reason": "Fixture check", "evidence": ["fixture.sql:1"],
    }


def required():
    return [check(name, blocking=True) for name in sorted(GATE.REQUIRED)]


class PublicationDecisionTests(unittest.TestCase):
    def test_three_general_checks_are_diagnostic_only(self):
        result = GATE.evaluate({"checks": required()})
        self.assertEqual(result["decision"], "blocked")
        self.assertFalse(result["publication_authorized"])
        self.assertEqual(GATE.evaluate({"checks": required()})["legacy_decision"], "ready")

    def test_unknowns_do_not_disappear_from_coverage(self):
        checks = required() + [check(f"unknown_{n}", "inconclusive") for n in range(99)]
        result = GATE.evaluate({"checks": checks})
        self.assertEqual(result["accuracy_percent"], 100)
        self.assertLess(result["coverage_percent"], 3)
        self.assertEqual(result["legacy_decision"], "revise")

    def test_blocking_error_cannot_be_offset_by_many_passes(self):
        checks = required() + [check(f"ok_{n}") for n in range(100)]
        checks += [check("wrong_type", "defect", True)]
        self.assertEqual(GATE.evaluate({"checks": checks})["legacy_decision"], "revise")

    def test_missing_source_evidence_blocks(self):
        checks = required()
        checks[0]["status"] = "inconclusive"
        self.assertEqual(GATE.evaluate({"checks": checks})["legacy_decision"], "blocked")

    def test_editorial_defects_allowed_at_threshold(self):
        checks = required() + [check(f"ok_{n}") for n in range(14)]
        checks += [check(f"editorial_{n}", "defect") for n in range(3)]
        result = GATE.evaluate({"checks": checks})
        self.assertEqual(result["accuracy_percent"], 85)
        self.assertEqual(result["legacy_decision"], "ready")

    def test_below_threshold_needs_revision(self):
        checks = required() + [check(f"ok_{n}") for n in range(13)]
        checks += [check(f"editorial_{n}", "defect") for n in range(4)]
        self.assertEqual(GATE.evaluate({"checks": checks})["legacy_decision"], "revise")

    def test_na_does_not_dilute_counts(self):
        result = GATE.evaluate({"checks": required() + [check("no_parameters", "not_applicable")]})
        self.assertEqual(result["coverage_percent"], 100)
        self.assertEqual(result["accuracy_percent"], 100)

    def test_duplicate_missing_or_bypassed_mandatory_checks_rejected(self):
        invalid = [
            required() + [required()[0]],
            required()[1:],
            [dict(item, blocking=False) for item in required()],
            [dict(item, status="not_applicable") for item in required()],
            [],
        ]
        for checks in invalid:
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                GATE.evaluate({"checks": checks})

    def test_check_without_evidence_rejected(self):
        checks = required()
        checks[0]["evidence"] = []
        with self.assertRaises(ValueError):
            GATE.evaluate({"checks": checks})


if __name__ == "__main__":
    unittest.main()

