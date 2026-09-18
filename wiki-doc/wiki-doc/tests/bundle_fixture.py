"""A complete, independently specified small SQL page for gate regressions."""
import json
import shutil
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
from bundle import create_manifest, compute_tool_versions, write_manifest
from artifact_schema import read_json
from sql_extract import extract_inventory
from validation_plan import generate_plan
from check_policy import load_policy
from validation_gate import evaluate_bundle
from evidence import sha256_file
from identity import page_id
PAGE_ID = page_id("function+core+calc+()")


def write_json(run, name, data):
    (run / f'{name}.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def seal(run, issue=True, *, page_id=PAGE_ID):
    facts = read_json(run / 'facts.json')
    manifest = create_manifest(run_id=facts['run_id'], page_id=page_id,
                               sql_files=[run / 'source.sql'], artifacts_dir=run,
                               tool_versions=compute_tool_versions(PACKAGE))
    write_manifest(manifest, run / 'manifest.json')
    if issue:
        return evaluate_bundle(run, write_decision=True)


def make_bundle(run):
    fixture = PACKAGE / 'tests' / 'fixtures' / 'artifacts' / 'valid'
    shutil.copy2(fixture / 'source.sql', run / 'source.sql')
    facts = read_json(fixture / 'facts.json')
    # Byte hashes follow the checkout's actual LF/CRLF representation.
    sha = sha256_file(run / 'source.sql')
    def hashes(value):
        if isinstance(value, dict):
            if value.get('path') == 'source.sql' and 'sha256' in value:
                value['sha256'] = sha
            for child in value.values(): hashes(child)
        elif isinstance(value, list):
            for child in value: hashes(child)
    hashes(facts)
    facts['objects'][0]['page_id'] = PAGE_ID
    facts['objects'][0]['canonical_key'] = 'function+core+calc+()'
    write_json(run, 'facts', facts)
    inv = extract_inventory((run / 'source.sql').read_text(encoding='utf-8-sig'), 'source.sql', sha,
                            version='15', documented_subjects=['core.calc'])
    inv['run_id'] = facts['run_id']
    write_json(run, 'inventory', inv)
    plan = generate_plan(inv, load_policy(), page_id=PAGE_ID)
    write_json(run, 'validation_plan', plan)
    draft = '''# Calculation {#header_purpose}
core.calc sums positive order amounts with a multiplier of 1.1.
## Signature {#schema_signature}
core.calc() returns numeric.
## Formulas and dependencies {#formulas_dependencies}
Reads demo.orders. Computes sum(amount * 1.1) WHERE amount > 0.
## Limitations {#misc}
Business rounding requirements are not specified by SQL.
'''
    (run / 'page.draft.md').write_text(draft, encoding='utf-8')
    entries = {f['id']: [{'section_id': 'formulas_dependencies'}]
               for group in ('objects', 'definitions', 'operations', 'columns', 'formulas', 'conditions', 'unknowns')
               for f in facts[group]}
    write_json(run, 'coverage', {'schema_version': 2, 'run_id': facts['run_id'],
                                'page_id': PAGE_ID, 'entries': entries})
    checks = []
    fact_by_rule = {'operation': 'op_001', 'formula': 'formula_001', 'condition': 'cond_001'}
    for c in plan['required_checks']:
        check = {'id': 'result:' + c['id'], 'plan_check_id': c['id'], 'status': 'ok',
                 'blocking': c['blocking'], 'category': c['category'],
                 'reason': 'SQL, registry and page agree for this obligation.',
                 'evidence': [{'root': 'project', 'path': 'source.sql', 'start_line': 1, 'end_line': 6, 'sha256': sha},
                              {'root': 'run', 'path': 'page.draft.md', 'start_line': 1, 'end_line': 8,
                               'sha256': sha256_file(run / 'page.draft.md')}],
                 'fact_ids': [fact_by_rule.get(c['rule_id'], 'obj_1')]}
        checks.append(check)
    write_json(run, 'validation', {'schema_version': 2, 'run_id': facts['run_id'],
                                  'page_id': PAGE_ID, 'checks': checks})
    result = seal(run)
    if result['decision'] != 'ready':
        raise AssertionError(f'Positive bundle must be ready: {result}')
    return result
