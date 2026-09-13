"""Annotated text-only and coherent mutations of all eleven regression cases."""
import copy
import json
from pathlib import Path
import shutil
from artifact_schema import read_json
from build_bundle import render,finish
from page_claims import check_page_claims
from run_regression import check_expected
from wiki_store import atomic_json,atomic_bytes


def mutate(facts,annotation):
    group=annotation['group']; selector=annotation.get('selector',{})
    found=[f for f in facts[group] if all(f.get(k)==v for k,v in selector.items())]
    if not found: raise ValueError('Mutation selector matched no fact')
    target=found[0]; path=annotation['field'].split('.')
    for component in path[:-1]: target=target[int(component)] if isinstance(target,list) else target[component]
    last=path[-1]; target[int(last) if isinstance(target,list) else last]=annotation['value']
    return facts


def run_mutations(report_path,expected_root,output):
    report=read_json(report_path); output=Path(output); results=[]
    for record in [r for r in report['results'] if r.get('repeat')==1 and 'subject' in r]:
        run=Path(record['run_dir']); project=Path(record['project_root']); case=record['case']
        assertions=read_json(Path(expected_root)/case/'page_assertions.json')
        original=read_json(run/'facts.json'); plan=read_json(run/'validation_plan.json'); manifest=read_json(run/'manifest.json')
        oracle=read_json(Path(expected_root)/case/'facts.json')['subjects'][record['subject']]
        for annotation in assertions['mutations']:
            changed=mutate(copy.deepcopy(original),annotation)
            page,coverage=render(changed,plan,changed['objects'][0].get('access_observations',[]))
            # Both arms retain real source paths and hashes. Reissue report/manifest so
            # rejection cannot be explained just by a stale draft checksum.
            for mode in ('text-only','coherent'):
                dest=output/case/(run.name+'-'+annotation['id']+'-'+mode)
                if dest.exists(): raise ValueError('Mutation destination already exists')
                shutil.copytree(run,dest)
                atomic_bytes(dest/'page.draft.md',page.encode('utf-8'))
                if mode=='coherent': atomic_json(dest/'facts.json',changed); atomic_json(dest/'coverage.json',coverage)
                result=finish(dest,sql_files=[project/r['path'] for r in manifest['sql_files']],context=[project/r['path'] for r in manifest.get('context_files',[])],
                              project_root=project,profile_path=record.get('profile'),migration_manifest=project/manifest['migration_manifest']['path'] if manifest.get('migration_manifest') else None)
                oracle_errors=check_expected(changed,oracle)
                results.append(dict(case=case,subject=record['subject'],mutation=annotation['id'],mode=mode,decision=result['decision'],
                                    false_ready=result['publication_authorized'],errors=result['errors'],oracle_errors=oracle_errors,run_dir=str(dest)))
    false_ready=sum(r['false_ready'] for r in results)
    summary=dict(schema_version=1,valid=bool(results) and false_ready==0,mutations=len(results),false_ready=false_ready,
                 false_ready_rate=false_ready/len(results) if results else None,results=results)
    atomic_json(output/'mutation-report.json',summary)
    return summary


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('report'); p.add_argument('--expected-root',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(); result=run_mutations(a.report,a.expected_root,a.output)
    print(json.dumps({k:v for k,v in result.items() if k!='results'},indent=2)); return 0 if result['valid'] else 1


if __name__=='__main__': raise SystemExit(main())
