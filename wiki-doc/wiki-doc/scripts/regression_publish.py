"""Publish every first-repeat regression bundle to a new isolated wiki and lint it."""
import argparse
import json
from pathlib import Path
from artifact_schema import read_json
from bundle import write_manifest
from evidence import sha256_file
from lint import lint
from publish import prepare,publish
from validation_gate import evaluate_bundle
from wiki_store import atomic_json


def publish_regression(report_path,wiki):
    report_path=Path(report_path); report=read_json(report_path); wiki=Path(wiki).resolve()
    if not report['valid']: raise ValueError('Regression must pass before publication acceptance')
    if wiki.exists(): raise ValueError('Use a new isolated wiki directory')
    results=[]
    for record in [r for r in report['results'] if r['repeat']==1]:
        run=Path(record['run_dir']); project=Path(record['project_root']); profile=record.get('profile')
        prepared=prepare(run,wiki)
        if prepared['draft_changed']: raise ValueError('Merged content requires another content review')
        manifest=read_json(run/'manifest.json')
        manifest['publication_plan']=dict(root='run',path='publication.json',sha256=sha256_file(run/'publication.json'))
        write_manifest(manifest,run/'manifest.json')
        checked=evaluate_bundle(run,roots={'project':project,'wiki':wiki},profile_path=profile,write_decision=True)
        if not checked['publication_authorized']: raise ValueError('; '.join(checked['errors']))
        dry=publish(run,wiki,project_root=project,profile_path=profile,dry_run=True)
        actual=publish(run,wiki,project_root=project,profile_path=profile)
        repeated=publish(run,wiki,project_root=project,profile_path=profile)
        results.append(dict(case=record['case'],subject=record['subject'],page_id=actual['page_id'],
                            dry_run_paths=sorted(dry['changes']),idempotent=repeated['idempotent'],archive=actual['archive']))
    inspected=lint(wiki)
    result=dict(schema_version=1,valid=inspected['valid'] and all(r['idempotent'] for r in results),
                wiki_root=str(wiki),published=len(results),results=results,lint=inspected)
    atomic_json(report_path.parent/'publication-report.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('report'); p.add_argument('--wiki',required=True); a=p.parse_args()
    try:
        result=publish_regression(a.report,a.wiki)
        print(json.dumps({k:v for k,v in result.items() if k not in ('results','lint')},indent=2)); return 0 if result['valid'] else 1
    except (ValueError,OSError) as exc: print(json.dumps({'error':str(exc)})); return 2


if __name__=='__main__': raise SystemExit(main())
