"""Run annotated claim mutations with independently checked positive controls."""
import copy
import json
from pathlib import Path
import re
import shutil

from artifact_schema import FACT_ARRAYS, read_json
from build_bundle import render, finish
from page_claims import check_page_claims
from run_regression import check_expected, check_run
from wiki_store import atomic_json, atomic_bytes


def mutate(facts, annotation):
    """Replace one existing field of exactly one fact; reject invalid/no-op probes."""
    try:
        group, selector = annotation['group'], annotation.get('selector', {})
        if group not in FACT_ARRAYS or not isinstance(selector, dict):
            raise ValueError('Invalid mutation group or selector')
        found = [f for f in facts[group] if all(k in f and f[k] == v for k, v in selector.items())]
        if len(found) != 1:
            raise ValueError(f'Mutation selector must match exactly one fact; matched {len(found)}')
        target = found[0]
        field = annotation['field']
        if not isinstance(field, str) or not field or any(not p for p in field.split('.')):
            raise ValueError('Invalid mutation field path')
        path = field.split('.')
        for index, component in enumerate(path):
            if isinstance(target, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', component):
                    raise ValueError('Mutation list index must be a nonnegative integer')
                key = int(component)
            elif isinstance(target, dict):
                key = component
            else:
                raise ValueError('Mutation path traverses a scalar')
            previous = target[key]  # Require the final field to exist too.
            if index == len(path) - 1:
                if previous == annotation['value']:
                    raise ValueError('Mutation must change the selected value')
                target[key] = copy.deepcopy(annotation['value'])
            else:
                target = previous
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f'Invalid mutation target: {exc}') from exc
    return facts


def _detection_layer(errors):
    if any('Page claim' in e or 'Unsupported page claim' in e for e in errors):
        return 'visible_claims'
    if any(e.startswith('facts: ') for e in errors):
        return 'schema'
    if any(e.startswith('Source access observations differ from verified profile') for e in errors):
        return 'profile'
    if any(e.startswith('facts ') or 'result lacks a matching' in e for e in errors):
        return 'sql'
    return None


def run_mutations(report_path, expected_root, output):
    report = read_json(report_path)
    output = Path(output)
    results, controls, input_errors = [], [], []
    records = [r for r in report['results'] if r.get('repeat') == 1]
    if not records:
        input_errors.append('No first-repeat records to test')
    for record in records:
        case = record.get('case', '<missing>')
        if not all(k in record for k in ('case', 'subject', 'run_dir', 'project_root')):
            input_errors.append(f'{case}: generation did not produce a complete run record')
            continue
        run, project = Path(record['run_dir']), Path(record['project_root'])
        try:
            expected = Path(expected_root) / case
            assertions = read_json(expected / 'page_assertions.json')
            annotations = assertions['mutations']
            if not annotations or len({a['id'] for a in annotations}) != len(annotations):
                raise ValueError('Mutation annotations must be nonempty with unique IDs')
            # Recheck the real bundle, SQL oracle and visible claims. A cached decision
            # or an already broken baseline cannot establish mutation detection.
            control = check_run(run, project, expected, record['subject'], record.get('profile'))
            controls.append(dict(case=case, subject=record['subject'], **control))
            if not control['valid']:
                for annotation in annotations:
                    for mode in ('text-only', 'coherent'):
                        results.append(dict(case=case, subject=record['subject'],
                            mutation=annotation['id'], mode=mode, status='baseline_invalid',
                            decision=None, false_ready=False, detection_layer=None,
                            errors=control['errors'], oracle_errors=[], run_dir=None))
                continue
            original = read_json(run / 'facts.json')
            plan = read_json(run / 'validation_plan.json')
            manifest = read_json(run / 'manifest.json')
            oracle = read_json(expected / 'facts.json')['subjects'][record['subject']]
        except (ValueError, OSError, KeyError, TypeError) as exc:
            input_errors.append(f'{case}: {exc}')
            continue
        for annotation in annotations:
            try:
                changed = mutate(copy.deepcopy(original), annotation)
                page, coverage = render(changed, plan, changed['objects'][0].get('access_observations', []))
                claims_errors = check_page_claims(original, page)
                if not claims_errors:
                    raise ValueError('Mutation does not change visible claims')
            except (ValueError, OSError, KeyError, TypeError) as exc:
                for mode in ('text-only', 'coherent'):
                    results.append(dict(case=case, subject=record['subject'],
                        mutation=annotation['id'], mode=mode, status='invalid_mutation',
                        decision=None, false_ready=False, detection_layer=None,
                        errors=[str(exc)], oracle_errors=[], run_dir=None))
                continue
            for mode in ('text-only', 'coherent'):
                dest = output / case / (run.name + '-' + annotation['id'] + '-' + mode)
                # Reject traversal from annotation/report names before any filesystem mutation.
                if not dest.resolve().is_relative_to(output.resolve()):
                    raise ValueError('Mutation destination escapes output')
                if dest.exists():
                    raise ValueError('Mutation destination already exists')
                shutil.copytree(run, dest)
                atomic_bytes(dest / 'page.draft.md', page.encode('utf-8'))
                if mode == 'coherent':
                    atomic_json(dest / 'facts.json', changed)
                    atomic_json(dest / 'coverage.json', coverage)
                # Reissue validation and hashes via actual claims checking + full SQL gate.
                # Do not inject a pre-authored defect report or count stale-hash refusals.
                result = finish(dest, sql_files=[project / r['path'] for r in manifest['sql_files']],
                    context=[project / r['path'] for r in manifest.get('context_files', [])],
                    project_root=project, profile_path=record.get('profile'),
                    migration_manifest=project / manifest['migration_manifest']['path']
                    if manifest.get('migration_manifest') else None)
                layer = _detection_layer(result['errors'])
                false_ready = result['publication_authorized'] or result['decision'] == 'ready'
                status = 'false_ready' if false_ready else 'detected' if layer else 'unattributed_rejection'
                results.append(dict(case=case, subject=record['subject'], mutation=annotation['id'],
                    mode=mode, status=status, decision=result['decision'], false_ready=false_ready,
                    detection_layer=layer, errors=result['errors'],
                    oracle_errors=check_expected(changed, oracle), run_dir=str(dest)))
    tested = sum(r['decision'] is not None for r in results)
    false_ready = sum(r['false_ready'] for r in results)
    summary = dict(schema_version=1,
        valid=bool(results) and not input_errors and all(r['status'] == 'detected' for r in results),
        mutations=len(results), tested_mutations=tested, untested=len(results) - tested,
        detected=sum(r['status'] == 'detected' for r in results), false_ready=false_ready,
        false_ready_rate=false_ready / tested if tested else None,
        controls=controls, input_errors=input_errors, results=results,
        limitation='Controlled claims/schema/SQL/profile checks only; annotation IDs do not establish full D01-D12 prose or graph coverage.')
    atomic_json(output / 'mutation-report.json', summary)
    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--expected-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run_mutations(args.report, args.expected_root, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in ('results', 'controls')}, indent=2))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
