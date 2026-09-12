"""Validate a wiki review report and compute its publication decision (no writes)."""
import argparse
import json
import sys

STATUSES = {"ok", "defect", "inconclusive", "not_applicable"}
REQUIRED = {"identity", "sql_registry", "registry_document"}


def evaluate(report):
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        raise ValueError("report.checks must be a list")
    counts = dict.fromkeys(STATUSES, 0)
    seen = set()
    blocking_defects = []
    blocking_unknowns = []
    for check in report["checks"]:
        if not isinstance(check, dict):
            raise ValueError("each check must be an object")
        check_id = check.get("id")
        status = check.get("status")
        if not isinstance(check_id, str) or not check_id.strip() or check_id in seen:
            raise ValueError("check IDs must be nonempty and unique")
        seen.add(check_id)
        if status not in STATUSES:
            raise ValueError(f"{check_id}: invalid status")
        if type(check.get("blocking")) is not bool:
            raise ValueError(f"{check_id}: blocking must be a boolean")
        if not isinstance(check.get("reason"), str) or not check["reason"].strip():
            raise ValueError(f"{check_id}: reason is required")
        evidence = check.get("evidence")
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise ValueError(f"{check_id}: evidence must be a list of references")
        if status in {"ok", "defect"} and not evidence:
            raise ValueError(f"{check_id}: a resolved check needs evidence")
        if check_id in REQUIRED and (status == "not_applicable" or not check["blocking"]):
            raise ValueError(f"{check_id}: mandatory check must be applicable and blocking")
        counts[status] += 1
        if check["blocking"]:
            if status == "defect":
                blocking_defects.append(check_id)
            elif status == "inconclusive":
                blocking_unknowns.append(check_id)
    if REQUIRED - seen:
        raise ValueError("missing mandatory checks: " + ", ".join(sorted(REQUIRED - seen)))
    resolved = counts["ok"] + counts["defect"]
    applicable = resolved + counts["inconclusive"]
    coverage = 100 * resolved / applicable if applicable else None
    accuracy = 100 * counts["ok"] / resolved if resolved else None
    # Compare integer counts: rounding the displayed percentage must not admit a report.
    ready = (
        applicable > 0
        and resolved == applicable
        and resolved > 0
        and 100 * counts["ok"] >= 85 * resolved
        and not blocking_defects
        and not blocking_unknowns
    )
    decision = "ready" if ready else ("blocked" if blocking_unknowns else "revise")
    return {
        "decision": decision,
        "coverage_percent": coverage,
        "accuracy_percent": accuracy,
        "counts": counts,
        "blocking_defects": blocking_defects,
        "blocking_inconclusive": blocking_unknowns,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="Path to validation.json")
    args = parser.parse_args()
    try:
        with open(args.report, encoding="utf-8") as stream:
            result = evaluate(json.load(stream))
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())

