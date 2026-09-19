"""Independent oracle checking of saved bundles, or isolated adapter generation."""
import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from artifact_schema import read_json,FACT_ARRAYS
from evidence import sha256_file
from page_claims import read_claims,check_page_claims
from validation_gate import evaluate_bundle,_expression_key
from wiki_store import atomic_json,inside

PACKAGE=Path(__file__).resolve().parents[1]


def check_expected(facts,expected):
    """Check selected facts against a separately authored oracle, not generator IDs."""
    errors=[]; objects={o['id']:o for o in facts['objects']}
    def name(oid):
        obj=objects[oid]
        return obj.get('canonical_key') if obj['kind'] in ('cte','temp_table') else f"{obj.get('schema')}.{obj['name']}"
    main=objects[facts['documented_object_ids'][0]]
    for field,value in expected.get('declaration',{}).items():
        if main.get(field)!=value: errors.append('declaration.'+field)
    if 'operations' in expected and dict(Counter(o['kind'] for o in facts['operations']))!=expected['operations']:
        errors.append('operation occurrence counts')
    for field in ('reads','writes','calls'):
        actual={name(oid) for op in facts['operations'] for oid in op[field] if not name(oid).startswith('@')}
        if field in expected and actual!=set(expected[field]): errors.append(field+' physical dependencies')
    for column in expected.get('columns',[]):
        found=[c for c in facts['columns'] if name(c['object_id'])==column['object'] and c['name']==column['name']]
        if len(found)!=1 or any(found[0].get(k)!=v for k,v in column.items() if k not in ('object','name')):
            errors.append('column '+column['object']+'.'+column['name'])
    for table,names in expected.get('exact_columns',{}).items():
        if {c['name'] for c in facts['columns'] if name(c['object_id'])==table}!=set(names): errors.append('exact columns '+table)
    for definition in expected.get('definitions',[]):
        found=[d for d in facts['definitions'] if name(d['object_id'])==definition['object']]
        if len(found)!=1 or any(found[0].get(k)!=v for k,v in definition.items() if k!='object'): errors.append('definition '+definition['object'])
    for group in ('formulas','conditions'):
        values={_expression_key(f['expression']) for f in facts[group]}
        for expression in expected.get(group,[]):
            if _expression_key(expression) not in values: errors.append(group+': '+expression)
    for kind,count in expected.get('local_objects',{}).items():
        if len([o for o in objects.values() if o['kind']==kind and o.get('physical') is False])!=count: errors.append('local objects '+kind)
    for operation,status in expected.get('access',{}).items():
        if not any(f['operation']==operation and f['status']==status for f in main.get('access_observations',[])):
            errors.append('source access '+operation)
    for constraint in expected.get('structures',[]):
        found=[o for o in facts['operations'] if o['kind']==constraint['kind']]
        if not any(all(o.get('structure',{}).get(k)==v for k,v in constraint.items() if k!='kind') for o in found): errors.append('operation structure '+constraint['kind'])
    if expected.get('unknown_required') and not facts['unknowns']: errors.append('honest unknown is missing')
    return errors


def check_run(run,project,expected_dir,subject,profile=None):
    facts=read_json(run/'facts.json'); page=(run/'page.draft.md').read_text(encoding='utf-8-sig')
    expectation=read_json(expected_dir/'facts.json')['subjects'][subject]
    errors=check_expected(facts,expectation)
    checks=read_json(expected_dir/'checks.json'); plan=read_json(run/'validation_plan.json')
    rules={c['rule_id'] for c in plan['required_checks']}
    for rule in checks['required_rules']:
        if rule not in rules: errors.append('missing required rule '+rule)
    required_decision=read_json(expected_dir/'decision.json')['decision']
    gate=evaluate_bundle(run,roots={'project':project},profile_path=profile)
    if gate['decision']!=required_decision or not gate['publication_authorized']: errors+=gate['errors'] or ['gate did not authorize expected ready bundle']
    assertion=read_json(expected_dir/'page_assertions.json')
    if assertion['contract']=='claims-v1':
        errors+=check_page_claims(facts,page)
        actual,parse_errors=read_claims(page); errors+=parse_errors
        # Rebuild values from rendered Markdown, then apply the independent SQL oracle.
        visible=copy.deepcopy(facts)
        for group in FACT_ARRAYS:
            for fact in visible[group]:
                for (fid,field),value in actual.items():
                    if fid==fact['id']: fact[field]=value
        errors+=['page '+e for e in check_expected(visible,expectation)]
    fingerprint=copy.deepcopy(facts)
    fingerprint.pop('run_id',None)
    return dict(valid=not errors,errors=errors,decision=gate['decision'],
                facts_sha256=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest())


def isolate(case,workspace,examples):
    if workspace.exists(): raise ValueError('Adapter workspace must be new; use a new --output path')
    package=workspace/'skill'; project=workspace/'input'; output=workspace/'output'
    for folder in ('scripts','schemas','references','template','profiles'):
        shutil.copytree(PACKAGE/folder,package/folder,ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('SKILL.md','template.md','doc-writer.md','doc-validator.md','ddl-finder.md','rules.md','requirements.txt','project-profile.md','VERSION','CHANGELOG.md'):
        shutil.copy2(PACKAGE/name,package/name)
    # Keep the skill's runtime documentation available to isolated agents
    # without copying historical reviews, test code or expected artifacts.
    (package/'docs').mkdir()
    for name in ('dialect-support.md', 'agent-response-templates.md', 'operations.md'):
        shutil.copy2(PACKAGE/'docs'/name,package/'docs'/name)
    paths={case['sql'],*case['context']}
    if case.get('migration_manifest'):
        mm=inside(examples,case['migration_manifest']); migration=read_json(mm)
        paths.add(case['migration_manifest'])
        for relative in migration['ordered_files']:
            source=(mm.parent/relative).resolve()
            if not source.is_relative_to(examples.resolve()): raise ValueError('Migration source escapes example root')
            paths.add(source.relative_to(examples.resolve()).as_posix())
    for relative in paths:
        source=inside(examples,relative); target=inside(project,relative)
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
    output.mkdir()
    request={k:case[k] for k in ('sql','context','subjects','version','migration_manifest','profile') if k in case}
    if request.get('profile'): request['profile']=str(package/request['profile'])
    request.update(schema_version=1,project_root=str(project),output_dir=str(output),skill_root=str(package))
    atomic_json(workspace/'request.json',request)
    return package,project,output


def run_suite(manifest_path,output,*,mode='saved',repeats=3,adapter=None,model=None,settings=None,timeout=120,iterations=0):
    manifest_path=Path(manifest_path).resolve(); cases=read_json(manifest_path)['cases']; examples=manifest_path.parent
    output=Path(output).resolve(); results=[]; started=time.monotonic()
    if mode=='adapter' and (not adapter or not model): raise ValueError('Agent adapter requires command argv and model/version')
    for repeat in range(1,repeats+1):
        for case in cases:
            workspace=output/f'run-{repeat:02d}'/case['id']
            try:
                if mode!='saved':
                    package,project,out=isolate(case,workspace,examples)
                    command=adapter if mode=='adapter' else [sys.executable,'-B',str(package/'scripts/regression_adapter.py'),'{request}']
                    command=[part.replace('{request}',str(workspace/'request.json')) for part in command]
                    env=dict(os.environ)
                    # Expose installed dependencies, never the original source/test tree.
                    env['PYTHONPATH']=os.pathsep.join(p for p in env.get('PYTHONPATH','').split(os.pathsep) if p and Path(p).resolve()!=PACKAGE/'scripts')
                    completed=subprocess.run(command,cwd=workspace,env=env,capture_output=True,timeout=timeout,shell=False)
                    (workspace/'stdout.log').write_bytes(completed.stdout); (workspace/'stderr.log').write_bytes(completed.stderr)
                    atomic_json(workspace/'adapter.json',dict(mode=mode,model=model or 'deterministic-reference-v1',settings=settings or {},timeout_seconds=timeout,returncode=completed.returncode))
                    if completed.returncode: raise ValueError(f'Adapter failed with exit {completed.returncode}; see stderr.log')
                else: project,out=workspace/'input',workspace/'output'
                generated=read_json(out/'runs.json')['runs']
                if Counter(r['subject'] for r in generated)!=Counter(case['subjects']): raise ValueError('Adapter omitted, duplicated or invented a documented subject')
                for row in generated:
                    run=inside(out,row['run_dir'])
                    checked=check_run(run,project,examples/'expected'/case['id'],row['subject'],row.get('profile'))
                    results.append(dict(case=case['id'],repeat=repeat,subject=row['subject'],run_dir=str(run),project_root=str(project),profile=row.get('profile'),**checked))
            except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as exc:
                results.append(dict(case=case['id'],repeat=repeat,valid=False,errors=[str(exc)]))
    spread={}
    for row in results:
        if 'subject' in row: spread.setdefault(row['case']+'/'+row['subject'],set()).add((row.get('facts_sha256'),row.get('decision'),tuple(row['errors'])))
    report=dict(schema_version=1,mode=mode,cycle='full-agent' if mode=='adapter' else 'semi-automatic-saved',
                full_agent_cycle_completed=mode=='adapter' and all(r['valid'] for r in results),
                valid=all(r['valid'] for r in results) and len(results)==repeats*sum(len(c['subjects']) for c in cases),
                cases=len(cases),subjects=sum(len(c['subjects']) for c in cases),repeats=repeats,repair_iterations=iterations,
                model=model or 'deterministic-reference-v1',settings=settings or {},elapsed_seconds=round(time.monotonic()-started,3),
                outcome_variants={k:len(v) for k,v in spread.items()},results=results,
                limitation='Arbitrary prose requires an independent content review; controlled rendered claims are checked against separately authored expectations.')
    atomic_json(output/'regression-report.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--manifest',default=str(PACKAGE/'examples/cases.json'))
    p.add_argument('--output',required=True); p.add_argument('--mode',choices=['saved','reference','adapter'],default='saved')
    p.add_argument('--repeats',type=int,default=3); p.add_argument('--adapter',help='JSON array argv; {request} is replaced, shell is never used')
    p.add_argument('--model'); p.add_argument('--settings',default='{}'); p.add_argument('--timeout',type=float,default=120); p.add_argument('--repair-iterations',type=int,default=0)
    a=p.parse_args(argv)
    if a.repeats<1 or a.timeout<=0: p.error('Positive repeats/timeout required')
    try:
        command=json.loads(a.adapter) if a.adapter else None
        if command is not None and (not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command)): raise ValueError('Adapter must be a nonempty JSON string array')
        report=run_suite(a.manifest,a.output,mode=a.mode,repeats=a.repeats,adapter=command,model=a.model,settings=json.loads(a.settings),timeout=a.timeout,iterations=a.repair_iterations)
        print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=True,indent=2)); return 0 if report['valid'] else 1
    except (ValueError,OSError) as exc: print(json.dumps({'error':str(exc)})); return 2


if __name__=='__main__': raise SystemExit(main())
