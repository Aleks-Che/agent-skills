"""Read-only wiki lint with separate legacy metadata and source-code findings."""
import argparse
import json
import re
from pathlib import Path

from artifact_schema import read_json,ArtifactInputError
from coverage_gate import MarkdownDocument,CoverageResult,_local_links,validate_coverage
from profiles import load_profile,access_findings
from validation_gate import evaluate_bundle
from wiki_store import inside,load_metadata,hash_file,indexed_pages,WikiConflict


def lint(wiki_root,*,project_root=None):
    root=Path(wiki_root).resolve(); issues=[]; source_findings=[]; mappings=[]
    def add(code,file,message,fix,severity='error'):
        issues.append(dict(code=code,file=str(file),message=message,fix=fix,severity=severity))
    if not root.is_dir():
        add('wiki_missing',root,'Wiki directory does not exist','Select an existing wiki root')
        return dict(valid=False,issues=issues,source_findings=[],mappings=[],publication_authorized=False)
    try: records=load_metadata(root)
    except (ValueError,OSError) as exc:
        add('metadata_invalid',root/'.wiki-doc/pages',str(exc),'Restore metadata from its committed journal'); records=[]
    metadata={r['page_id']:r for r in records}
    pages=sorted(p for p in root.rglob('*.md') if '.wiki-doc' not in p.relative_to(root).parts)
    index=root/'index.md'; indexed=indexed_pages(index.read_text(encoding='utf-8-sig')) if index.is_file() else set()
    keys={}
    for path in pages:
        relative=path.relative_to(root).as_posix()
        if not path.resolve().is_relative_to(root):
            add('path_escape',path,'Page resolves outside the wiki','Replace the escaping symlink with a local page'); continue
        text=path.read_text(encoding='utf-8-sig'); document=MarkdownDocument(text)
        checked=CoverageResult()
        link_roots=[Path(project_root).resolve()] if project_root else []
        record=metadata.get(relative)
        if record and record.get('project_root'): link_roots.append(Path(record['project_root']))
        _local_links(document,checked,page_id=relative,wiki_root=root,link_roots=link_roots)
        for message in checked.errors: add('link_invalid',path,message,'Correct the relative path or target anchor')
        for message in document.errors: add('markdown_invalid',path,message,'Use supported Markdown with unambiguous anchors')
        if document.duplicates: add('anchor_duplicate',path,', '.join(document.duplicates),'Give each anchor a unique ID')
        if relative=='index.md': continue
        if record:
            key=record['canonical_key']
            mappings.append(dict(page_id=relative,canonical_key=key,status='managed',preserve_path=True))
            if relative not in indexed: add('index_missing',path,'Managed page is absent from index','Add its existing path to index.md')
            if hash_file(path)!=record['page_sha256']: add('page_changed',path,'Published bytes differ from checked provenance','Preserve manual edits; prepare and validate a merged draft')
            try:
                archive=inside(root,record['bundle'])
                verified=evaluate_bundle(archive,roots={'wiki':root,'link_project':project_root or record.get('project_root',archive)},
                                          profile_path=record['profile'],policy_path=record.get('policy'))
                for message in verified['errors']: add('provenance_invalid',path,message,'Regenerate and validate the complete bundle with current tools')
                facts=read_json(archive/'facts.json'); plan=read_json(archive/'validation_plan.json'); coverage=read_json(archive/'coverage.json')
                manifest=read_json(archive/'manifest.json')
                main=next(o for o in facts['objects'] if o['id'] in facts['documented_object_ids'])
                if (record['canonical_key']!=main['canonical_key'] or record['page_id']!=manifest['page_id'] or
                    record['run_id']!=manifest['run_id'] or record['page_sha256']!=manifest['artifacts']['draft']['sha256'] or
                    record['source_files']!=manifest['sql_files']+manifest.get('context_files',[])):
                    add('metadata_mismatch',path,'Metadata differs from the verified bundle identity/inputs','Restore metadata from the committed journal')
                actual=validate_coverage(coverage,text,facts,plan,wiki_root=root,link_roots=link_roots)
                for message in actual.errors: add('coverage_invalid',path,message,'Restore content and fact coverage; validate the actual merged page')
                if facts.get('page_contract')=='claims-v1':
                    from page_claims import check_page_claims
                    for message in check_page_claims(facts,text): add('page_claim_invalid',path,message,'Correct the rendered claim against SQL and facts')
                if facts.get('profile') and record['profile']:
                    source_findings.extend(dict(page_id=relative,**f) for f in access_findings(read_json(archive/'inventory.json'),load_profile(record['profile'])))
                source_root=Path(project_root or record.get('project_root',archive)).resolve()
                for ref in record['source_files']:
                    source=(source_root/ref['path']).resolve()
                    if not source.is_relative_to(source_root) or hash_file(source)!=ref['sha256']:
                        add('source_stale',path,f'Current source differs: {ref["path"]}','Regenerate this page from the current SQL/context snapshot')
            except (ArtifactInputError,WikiConflict,OSError,ValueError,KeyError) as exc:
                add('provenance_invalid',path,str(exc),'Restore or regenerate the complete publication bundle')
        else:
            # A standalone legacy page need not be a graph/index defect.
            key_match=re.search(r'(?:canonical_key|Canonical key|Канонический ключ|Ключ)\s*[:|]\**\s*`?([^`\n|]+)',text,re.I)
            key=key_match[1].strip() if key_match else None
            mappings.append(dict(page_id=relative,canonical_key=key,status='legacy_unverified',preserve_path=True))
            add('legacy_metadata_missing',path,'New provenance is absent; semantic correctness is unverified','Preserve this path; create explicit identity mapping and run a new validation cycle','warning')
        if key:
            if key in keys: add('identity_duplicate',path,f'Same canonical key as {keys[key]}','Resolve the duplicate with an explicit identity registry; preserve manual content')
            keys[key]=relative
    for relative in metadata.keys()-{p.relative_to(root).as_posix() for p in pages}:
        add('page_missing',inside(root,relative),'Metadata points to a missing page','Recover the committed page from its verified bundle')
    return dict(valid=not any(i['severity']=='error' for i in issues),issues=issues,source_findings=source_findings,
                mappings=mappings,publication_authorized=False,files_checked=len(pages))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('wiki'); p.add_argument('--project-root'); p.add_argument('--json',action='store_true')
    args=p.parse_args(argv)
    try: result=lint(args.wiki,project_root=args.project_root)
    except (OSError,ValueError) as exc: print(str(exc)); return 2
    if args.json: print(json.dumps(result,ensure_ascii=True,indent=2))
    else:
        print('PASS' if result['valid'] else 'FAIL')
        for i in result['issues']: print(f"{i['severity']} [{i['code']}] {i['file']}: {i['message']}\n  Fix: {i['fix']}")
        for f in result['source_findings']: print(f"SOURCE [{f['status']}] {f['page_id']}: {f['operation']} {f['target']}")
    return 0 if result['valid'] else 1


if __name__=='__main__': raise SystemExit(main())
