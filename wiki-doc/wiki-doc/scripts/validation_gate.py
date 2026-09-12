"""Validate a wiki-doc run bundle and compute its publication decision.

Extended from the original report-only evaluator. Now supports:
- Full bundle validation (manifest, inventory, plan, facts, evidence)
- Independent inventory rebuilding from SQL
- Policy-based obligation derivation
- Cross-artifact consistency checks

Legacy mode: python scripts/validation_gate.py <validation.json>
Bundle mode:  python scripts/validation_gate.py --bundle <run_dir>
"""
import argparse
import json
import sys
from pathlib import Path

# Sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence import sha256_file, validate_evidence, EvidenceError
from bundle import verify_manifest_hashes, verify_decision_against_manifest, reverify_bundle
from check_policy import load_policy, is_defect_blocking, get_rule, PolicyError, POLICY_PATH
from sql_extract import extract_inventory as sql_extract_inventory
from validation_plan import generate_plan
from artifact_schema import read_artifact_set, read_json, validate_artifacts, ArtifactInputError, FACT_ARRAYS
from bundle import compute_tool_versions
from evidence import resolve_reference
import re

STATUSES = {"ok", "defect", "inconclusive", "not_applicable"}
REQUIRED = {"identity", "sql_registry", "registry_document"}
REQUIRED_LEGACY = REQUIRED  # alias for clarity in bundle mode


# ---- Legacy report-only evaluation (kept for backward compat) ----

def evaluate(report):
    """Evaluate a standalone validation.json (legacy mode)."""
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
        if check_id in REQUIRED_LEGACY and (status == "not_applicable" or not check["blocking"]):
            raise ValueError(f"{check_id}: mandatory check must be applicable and blocking")
        counts[status] += 1
        if check["blocking"]:
            if status == "defect":
                blocking_defects.append(check_id)
            elif status == "inconclusive":
                blocking_unknowns.append(check_id)
    if REQUIRED_LEGACY - seen:
        raise ValueError("missing mandatory checks: " + ", ".join(sorted(REQUIRED_LEGACY - seen)))
    resolved = counts["ok"] + counts["defect"]
    applicable = resolved + counts["inconclusive"]
    coverage = 100 * resolved / applicable if applicable else None
    accuracy = 100 * counts["ok"] / resolved if resolved else None
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
        "decision": "blocked",
        "legacy_decision": decision,
        "publication_authorized": False,
        "coverage_percent": coverage,
        "accuracy_percent": accuracy,
        "counts": counts,
        "blocking_defects": blocking_defects,
        "blocking_inconclusive": blocking_unknowns,
    }


def _gate_result(decision, *, errors=None, metrics=None, input_error=False, record=None):
    return {'decision': decision, 'valid': decision == 'ready',
            'publication_authorized': decision == 'ready', 'input_error': input_error,
            'errors': errors or [], 'warnings': [], 'metrics': metrics or {},
            'decision_record': record}


def evaluate_checks(checks, required, policy):
    """Count only independently required obligations; enforce policy severity."""
    by_plan, errors, defects, unknowns = {}, [], [], []
    counts = dict.fromkeys(sorted(STATUSES), 0)
    required_by_id = {c['id']: c for c in required}
    for check in checks:
        plan_id = check.get('plan_check_id')
        if plan_id in required_by_id:
            by_plan.setdefault(plan_id, []).append(check)
        else:
            # Extra successes never improve a score. Unclassified defects are technical.
            blocking = is_defect_blocking(policy, check.get('defect_code', check['id']),
                                         check.get('category') != 'editorial' or check['blocking'])
            if blocking and check['status'] in ('defect', 'inconclusive'):
                (defects if check['status'] == 'defect' else unknowns).append(check['id'])
    for expected in required:
        matches = by_plan.get(expected['id'], [])
        if len(matches) != 1:
            errors.append(f"{expected['id']}: expected exactly one result, got {len(matches)}")
            counts['inconclusive'] += 1
            continue
        check = matches[0]
        status = check['status']
        if expected['applicable'] and status == 'not_applicable':
            errors.append(f"{check['id']}: found obligation cannot be not_applicable")
            counts['inconclusive'] += 1
            continue
        if not expected['applicable'] and status != 'not_applicable':
            errors.append(f"{check['id']}: inconsistent applicability")
        counts[status] += 1
        rule = get_rule(policy, expected['rule_id'])
        blocking = is_defect_blocking(policy, check.get('defect_code', check['id']),
                                     expected['blocking'] or rule['blocking_default'] or rule['category'] == 'technical')
        if blocking and status == 'defect':
            defects.append(check['id'])
        elif blocking and status == 'inconclusive':
            unknowns.append(check['id'])
    resolved = counts['ok'] + counts['defect']
    applicable = resolved + counts['inconclusive']
    ready = (not errors and not defects and not unknowns and applicable > 0
             and resolved == applicable and counts['ok'] * 100 >= resolved * 85)
    return {
        'decision': 'ready' if ready else ('blocked' if unknowns else 'revise'),
        'errors': errors, 'blocking_defects': defects, 'blocking_inconclusive': unknowns,
        'metrics': {'coverage_percent': 100 * resolved / applicable if applicable else None,
                    'accuracy_percent': 100 * counts['ok'] / resolved if resolved else None,
                    'total_checks': len(required),
                    **{f'{s}_count': n for s, n in counts.items()}},
    }


def _section_bodies(draft):
    """P0 Markdown subset: explicit heading IDs, ignoring fenced code/comments."""
    sections, current, fence = {}, None, None
    for line in re.sub(r'<!--.*?-->', '', draft, flags=re.S).splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        heading = None if fence else re.match(r'^#{1,6}\s+(.+?)\s*$', line)
        if heading:
            text = heading.group(1)
            explicit = re.search(r'\{#([\w-]+)\}\s*$', text)
            current = explicit.group(1) if explicit else text.strip()
            if current in sections:
                raise ValueError(f'Duplicate section ID {current!r}')
            sections[current] = []
        elif current and line.strip() and not re.match(r'^\s*<!--.*-->\s*$', line):
            sections[current].append(line)
    return sections


def _fact_checks(artifacts, rebuilt, required, draft):
    """Match operations in both directions by source occurrence, never writer IDs."""
    errors, facts = [], artifacts['facts']
    objects = {o['id']: o for o in facts['objects']}
    documented = set(facts['documented_object_ids'])
    declarations = [i for i in rebuilt['items'] if i['kind'] == 'DECLARATION']
    if len(declarations) != 1 or len(documented) != 1:
        return ['P0 bundle requires exactly one independently selected declaration per page']
    declaration = declarations[0]
    obj = objects[next(iter(documented))]
    for key in ('kind', 'schema', 'name'):
        expected = declaration['details'].get('object_kind' if key == 'kind' else key)
        if obj.get(key) != expected:
            errors.append(f'facts identity {key} differs from SQL declaration')
    signature = declaration['details'].get('signature')
    if signature and obj.get('signature') not in (signature, signature[signature.index('('):]):
        errors.append('facts signature differs from SQL declaration')
    operations = [o for o in facts['operations'] if o.get('scope') in documented]
    items = [i for i in rebuilt['items'] if i['kind'] in
             ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CALL', 'PERFORM', 'EXECUTE', 'RETURN')]
    matched, item_facts = set(), {}
    for item in items:
        ref = item['source_ref']
        matches = [op for op in operations if op['id'] not in matched and op['kind'] == item['kind']
                   and any(r['path'] == ref['path'] and r['start_line'] <= ref['start_line'] <= r['end_line']
                           for r in op['source_refs'])]
        # Order distinguishes repeated operations on one source line.
        matches.sort(key=lambda op: op.get('order', 0))
        if not matches:
            errors.append(f"SQL operation {item['kind']} at {ref['path']}:{ref['start_line']} missing from facts")
            continue
        op = matches[0]
        matched.add(op['id'])
        anchor = item['anchor']
        key = (ref['path'], anchor['object_or_scope'], anchor['construct'], anchor['ordinal'])
        item_facts[key] = op['id']
        for field in ('reads', 'writes', 'calls'):
            names = {f"{objects[oid].get('schema')}.{objects[oid]['name']}" for oid in op.get(field, [])}
            if names != set(item.get(field, [])):
                errors.append(f"facts {op['id']}.{field} differs from independent SQL inventory")
    for op in operations:
        if op['id'] not in matched:
            errors.append(f"facts operation {op['id']} has no independent SQL occurrence")

    # Context entities are needed only when reached by a documented fact.
    relevant = set(documented) | matched
    all_facts = {f['id']: f for group in FACT_ARRAYS for f in facts[group]}
    changed = True
    while changed:
        previous = relevant.copy()
        for fid, fact in all_facts.items():
            owners = {fact.get('scope'), fact.get('object_id')}
            links = set(fact.get('operation_ids', [])) | set(fact.get('related_facts', []))
            if owners & documented or links & relevant:
                relevant.add(fid)
            if fid in relevant:
                for field in ('condition_ids', 'column_ids', 'source_columns'):
                    relevant.update(fact.get(field, []))
        changed = previous != relevant
    coverage = artifacts['coverage']['entries']
    try:
        sections = _section_bodies(draft)
    except ValueError as exc:
        return errors + [str(exc)]
    for fid in sorted(relevant):
        if fid not in coverage:
            errors.append(f'coverage: fact {fid} is not covered')
    for fid, refs in coverage.items():
        for ref in refs:
            section = ref['section_id']
            if not sections.get(section):
                errors.append(f'coverage: {fid}: missing or empty section {section!r}')
            if ref.get('fragment_ref') and ref['fragment_ref'] not in ('#' + section, section):
                errors.append(f'coverage: {fid}: fragment_ref requires the P1 fragment parser')
    for required_check in required:
        if required_check['source'] == 'section':
            section = required_check['id'].removeprefix('section:')
            if not sections.get(section):
                errors.append(f'missing or empty required section {section!r}')
        if required_check['source'] != 'inventory':
            continue
        anchor = required_check['inventory_anchor']
        key = tuple(anchor[k] for k in ('path', 'object_or_scope', 'construct', 'ordinal'))
        op_id = item_facts.get(key)
        results = [c for c in artifacts['validation']['checks'] if c.get('plan_check_id') == required_check['id']]
        rule = required_check['rule_id']
        group = {'formula': 'formulas', 'condition': 'conditions', 'unknown': 'unknowns'}.get(rule)
        ids = {op_id} if rule == 'operation' else set()
        if group:
            ids = {f['id'] for f in facts[group] if op_id in f.get('operation_ids', f.get('related_facts', []))}
        if op_id and (not ids or not results or not any(ids & set(c.get('fact_ids', [])) for c in results)):
            errors.append(f'{required_check["id"]}: result lacks a matching {rule} fact')
    return errors


def _verify_evidence(artifacts, roots):
    errors = []
    manifest = artifacts['manifest']
    allowed = {}
    for ref in manifest['sql_files'] + manifest.get('context_files', []):
        allowed[resolve_reference(ref, roots)] = ref['sha256']
    for name, ref in manifest['artifacts'].items():
        if name != 'validation':
            allowed[resolve_reference(ref, roots, 'run')] = ref['sha256']
    # Package evidence is bound by the verified aggregate tool hashes.
    package = roots['package']
    for relative in ('SKILL.md', 'doc-writer.md', 'doc-validator.md', 'ddl-finder.md',
                     'rules.md', 'template.md', 'references/facts.md', 'references/artifacts.md',
                     'references/check-policy.json'):
        path = package / relative
        allowed[path.resolve()] = sha256_file(path)
    for path in (package / 'template').glob('*.md'):
        allowed[path.resolve()] = sha256_file(path)

    def visit(value, location):
        if isinstance(value, dict):
            if {'path', 'start_line', 'end_line', 'sha256'} <= value.keys():
                ref = {'root': 'project', **value}
                try:
                    validate_evidence(ref, roots)
                    path = resolve_reference(ref, roots)
                    if allowed.get(path) != ref['sha256']:
                        errors.append(f'{location}: evidence is outside the checked input bundle')
                except EvidenceError as exc:
                    errors.append(f'{location}: {exc}')
            for key, child in value.items():
                visit(child, f'{location}.{key}')
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, f'{location}.{i}')
    for name in ('facts', 'inventory', 'validation'):
        visit(artifacts[name], name)
    for check in artifacts['validation']['checks']:
        if any(not isinstance(ref, dict) for ref in check['evidence']):
            errors.append(f"{check['id']}: bundle evidence must contain structured file references")
    for name in ('facts', 'inventory'):
        for ref in artifacts[name]['inputs']:
            try:
                if allowed.get(resolve_reference(ref, roots)) != ref['sha256']:
                    errors.append(f'{name}.inputs: undeclared or changed input {ref["path"]}')
            except EvidenceError as exc:
                errors.append(f'{name}.inputs: {exc}')
    return errors


def evaluate_bundle(run_dir, *, policy_path=None, roots=None, profile_path=None, write_decision=False):
    run = Path(run_dir).resolve()
    package = Path(__file__).resolve().parent.parent
    roots = {'project': run, **{k: Path(v).resolve() for k, v in (roots or {}).items()},
             'run': run, 'package': package}
    try:
        artifacts = read_artifact_set(run)
        if 'manifest' not in artifacts:
            return _gate_result('blocked', errors=['manifest.json not found'], input_error=True)
        required_artifacts = {'manifest', 'facts', 'inventory', 'validation_plan', 'coverage', 'validation'}
        if not write_decision:
            required_artifacts.add('decision')
        # When issuing a new decision, validate the inputs without trusting the old decision.
        old_decision = artifacts.pop('decision', None) if write_decision else artifacts.get('decision')
        schema_errors = validate_artifacts(artifacts, required=required_artifacts)
        if schema_errors:
            return _gate_result('blocked', errors=schema_errors, input_error=True)
        manifest = artifacts['manifest']
        errors = verify_manifest_hashes(manifest, run, roots=roots)
        if errors:
            return _gate_result('blocked', errors=errors)
        policy_path = Path(policy_path) if policy_path else POLICY_PATH
        policy = load_policy(policy_path)
        versions = compute_tool_versions(package, profile_path=profile_path, policy_path=policy_path)
        if versions != manifest['tool_versions']:
            return _gate_result('blocked', errors=['tool_versions mismatch: code, instructions, template, policy or profile changed'])
        facts, inventory = artifacts['facts'], artifacts['inventory']
        if bool(facts.get('profile')) != bool(profile_path):
            return _gate_result('blocked', errors=['Active profile and verified profile file do not match'])
        if profile_path and sha256_file(profile_path) != sha256_file(package / 'project-profile.md'):
            return _gate_result('blocked', errors=['Only the bundled CKR_GP profile has P0 obligation derivation'])
        errors = _verify_evidence(artifacts, roots)
        if errors:
            return _gate_result('blocked', errors=errors)
        rebuilt = {'schema_version': 2, 'run_id': manifest['run_id'], 'dialect': inventory['dialect'],
                   'items': [], 'coverage_notes': [], 'inputs': [], 'documented_subjects': []}
        # Select by independent declaration scope from the saved inventory, then rebuild it.
        for sql_ref in manifest['sql_files']:
            sql_path = resolve_reference(sql_ref, roots)
            local_subjects = {i['anchor']['object_or_scope'] for i in inventory['items']
                              if i['source_ref']['path'] == sql_ref['path']}
            result = sql_extract_inventory(sql_path.read_text(encoding='utf-8-sig'), sql_ref['path'], sql_ref['sha256'],
                                           dialect=inventory['dialect']['name'], version=inventory['dialect']['version'],
                                           documented_subjects=sorted(local_subjects) or inventory['documented_subjects'])
            for key in ('items', 'coverage_notes', 'inputs', 'documented_subjects'):
                rebuilt[key].extend(result[key])
        if rebuilt['coverage_notes']:
            return _gate_result('blocked', errors=['analysis gap: ' + n['reason'] for n in rebuilt['coverage_notes']])
        for key in ('items', 'coverage_notes', 'inputs', 'documented_subjects'):
            if inventory.get(key, []) != rebuilt[key]:
                errors.append(f'inventory.{key} differs from independently rebuilt SQL inventory')
        if errors:
            return _gate_result('revise', errors=errors)
        if facts['dialect'] != rebuilt['dialect']:
            return _gate_result('revise', errors=['facts dialect differs from inventory'])
        expected_plan = generate_plan(rebuilt, policy, page_id=manifest['page_id'], profile_active=bool(profile_path))
        if expected_plan != artifacts['validation_plan']:
            return _gate_result('revise', errors=['validation_plan differs from independently derived obligations'])
        report = artifacts['validation']
        evaluation = evaluate_checks(report['checks'], expected_plan['required_checks'], policy)
        errors.extend(evaluation['errors'])
        errors.extend(_fact_checks(artifacts, rebuilt, expected_plan['required_checks'],
                                   (run / 'page.draft.md').read_text(encoding='utf-8-sig')))
        verdict = evaluation['decision']
        if errors and verdict == 'ready':
            verdict = 'revise'
        record = {'schema_version': 2, 'run_id': manifest['run_id'], 'page_id': manifest['page_id'],
                  'decision': verdict, 'manifest_sha256': sha256_file(run / 'manifest.json'),
                  'validation_sha256': sha256_file(run / 'validation.json'),
                  'metrics': evaluation['metrics'], 'blocking_defects': evaluation['blocking_defects'],
                  'blocking_inconclusive': evaluation['blocking_inconclusive']}
        if not write_decision:
            decision_errors = verify_decision_against_manifest(old_decision, manifest, run)
            for field in ('decision', 'metrics', 'blocking_defects', 'blocking_inconclusive'):
                if field in old_decision and old_decision[field] != record[field]:
                    decision_errors.append(f'decision.{field} mismatch with gate computation')
            if decision_errors:
                return _gate_result('blocked', errors=decision_errors, metrics=evaluation['metrics'])
        # Close the read/check interval before issuing or accepting a decision.
        final_errors = verify_manifest_hashes(manifest, run, roots=roots)
        if sha256_file(run / 'manifest.json') != record['manifest_sha256']:
            final_errors.append('manifest changed during evaluation')
        if compute_tool_versions(package, profile_path=profile_path, policy_path=policy_path) != versions:
            final_errors.append('tool versions changed during evaluation')
        if final_errors:
            return _gate_result('blocked', errors=final_errors)
        if write_decision:
            output = run / 'decision.json'
            temporary = run / 'decision.json.tmp'
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(output)
        return _gate_result(verdict, errors=errors, metrics=evaluation['metrics'], record=record)
    except (ArtifactInputError, PolicyError, OSError, UnicodeError) as exc:
        return _gate_result('blocked', errors=[str(exc)], input_error=True)
    except (EvidenceError, ValueError) as exc:
        return _gate_result('blocked', errors=[str(exc)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', nargs='?', help='Standalone report for diagnostics only')
    parser.add_argument('--bundle', help='Complete run directory')
    parser.add_argument('--policy', help='Policy file (defaults to the package policy)')
    parser.add_argument('--profile', type=Path, help='Active profile file')
    parser.add_argument('--root', nargs=2, action='append', metavar=('NAME', 'PATH'))
    parser.add_argument('--write-decision', action='store_true', help='Issue a new decision after evaluating all inputs')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args(argv)
    if bool(args.report) == bool(args.bundle):
        parser.error('Choose either --bundle or a standalone report')
    if not args.bundle and (args.policy or args.profile or args.root or args.write_decision):
        parser.error('Bundle options require --bundle')
    if args.bundle:
        root_map = dict(args.root or [])
        if set(root_map) - {'project', 'wiki'}:
            parser.error('Configurable roots are project and wiki; run/package are fixed')
        result = evaluate_bundle(args.bundle, policy_path=args.policy, roots=root_map,
                                 profile_path=args.profile, write_decision=args.write_decision)
    else:
        try:
            result = evaluate(read_json(args.report))
        except (ValueError, OSError) as exc:
            result = _gate_result('blocked', errors=[str(exc)], input_error=True)
    if args.json or args.report:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Decision: {result['decision']}")
        for error in result.get('errors', []):
            print(f'  - {error}')
    return 2 if result.get('input_error') else (0 if result['decision'] == 'ready' else 1)


if __name__ == '__main__':
    sys.exit(main())


