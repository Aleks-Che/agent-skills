"""Reference subprocess adapter: request JSON -> runs.json, without expectations."""
import json
import sys
from pathlib import Path
from artifact_schema import read_json
from build_bundle import build
from wiki_store import atomic_json


def main():
    request=read_json(sys.argv[1]); root=Path(request['project_root']); out=Path(request['output_dir'])
    results=[]
    for i,subject in enumerate(request['subjects']):
        result=build(root/request['sql'],out/str(i),project_root=root,subject=subject,
                     context=[root/p for p in request['context']],version=request['version'],
                     migration_manifest=root/request['migration_manifest'] if request.get('migration_manifest') else None,
                     profile=request.get('profile'))
        results.append(dict(subject=subject,run_dir=str(i),profile=result['profile']))
    atomic_json(out/'runs.json',dict(schema_version=1,runs=results))
    return 0


if __name__=='__main__': raise SystemExit(main())
