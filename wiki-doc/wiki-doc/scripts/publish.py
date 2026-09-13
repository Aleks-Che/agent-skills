"""Prepare, publish and recover a local wiki with process locks and durable journals."""
import argparse
import contextlib
import difflib
import json
import os
import re
import time
from pathlib import Path

from artifact_schema import read_json, ArtifactInputError
from bundle import save_bundle
from validation_gate import evaluate_bundle
from wiki_store import (WikiConflict, inside, digest, read_bytes, hash_file, atomic_bytes,
                        atomic_json, json_bytes, metadata_path, merge_page, update_index, load_metadata)


@contextlib.contextmanager
def index_lock(root,timeout=15):
    path=inside(root,'.wiki-doc/index.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    stream.seek(0,2)
    if stream.tell()==0: stream.write(b'0'); stream.flush()
    deadline=time.monotonic()+timeout
    locked=False
    try:
        while not locked:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                locked=True
            except OSError:
                if time.monotonic()>=deadline: raise WikiConflict('Timed out waiting for the shared wiki index lock')
                time.sleep(0.05)
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()


def prepare(run_dir,wiki_root):
    run,root=Path(run_dir).resolve(),Path(wiki_root).resolve()
    facts=read_json(run/'facts.json')
    obj=next(o for o in facts['objects'] if o['id'] in facts['documented_object_ids'])
    page_id=obj['page_id']
    page,index,meta=inside(root,page_id),inside(root,'index.md'),metadata_path(root,page_id)
    incoming=(run/'page.draft.md').read_text(encoding='utf-8-sig')
    current=read_bytes(page)
    base=None
    if meta.exists():
        prior=read_json(meta)
        previous=inside(root,prior['bundle'])/'page.draft.md'
        data=previous.read_bytes()
        if digest(data)!=prior['page_sha256']: raise WikiConflict('Previous generated base differs from publication metadata')
        base=data.decode('utf-8-sig')
    merged=merge_page(base,current.decode('utf-8-sig') if current is not None else None,incoming)
    if merged!=incoming:
        atomic_bytes(run/'page.generated.md',incoming.encode('utf-8'))
        atomic_bytes(run/'page.draft.md',merged.encode('utf-8'))
    plan=dict(schema_version=1,run_id=facts['run_id'],page_id=page_id,canonical_key=obj['canonical_key'],
              wiki_root=str(root),draft_sha256=hash_file(run/'page.draft.md'),
              expected_page_sha256=digest(current),expected_index_sha256=hash_file(index),expected_metadata_sha256=hash_file(meta),
              index_base=(read_bytes(index) or b'').decode('utf-8'))
    atomic_json(run/'publication.json',plan)
    return dict(prepared=True,draft_changed=merged!=incoming,validation_required=True,plan=plan)


def _recover_locked(root):
    folder=inside(root,'.wiki-doc/journal')
    results=[]
    if not folder.exists(): return results
    for journal_path in sorted(folder.glob('*.json')):
        journal=read_json(journal_path)
        _validate_journal(root,journal_path,journal)
        if journal.get('state') in ('committed','rolled_back'): continue
        conflicts=[]
        for entry in journal.get('files',[]):
            target=inside(root,entry['path'])
            current=hash_file(target)
            if current not in (entry['old_sha256'],entry['new_sha256']): conflicts.append(entry['path'])
        if conflicts:
            journal.update(state='conflict',conflicts=conflicts)
            atomic_json(journal_path,journal)
            results.append(dict(run_id=journal['run_id'],state='conflict',files=conflicts))
            continue
        # Detect actual bytes, including process death before the next journal write.
        for entry in reversed(journal['files']):
            target=inside(root,entry['path'])
            if hash_file(target)==entry['old_sha256']: continue
            if entry['old_sha256'] is None:
                if target.exists(): target.unlink()
            else:
                backup=inside(root,entry['backup'])
                data=backup.read_bytes()
                if digest(data)!=entry['old_sha256']: raise WikiConflict('Recovery backup checksum mismatch')
                atomic_bytes(target,data)
        journal['state']='rolled_back'
        atomic_json(journal_path,journal)
        results.append(dict(run_id=journal['run_id'],state='rolled_back'))
    return results


def _validate_journal(root,path,journal):
    """Check the entire write set and backups before recovery changes any file."""
    if not isinstance(journal,dict) or journal.get('schema_version')!=1 or not re.fullmatch(r'[0-9a-f-]{36}',journal.get('run_id','')):
        raise WikiConflict('Invalid recovery journal')
    run_id=journal['run_id']
    if path.name!=run_id+'.json' or journal.get('state') not in ('prepared','page_replaced','index_replaced','metadata_replaced','committed','rolled_back','conflict'):
        raise WikiConflict('Invalid recovery state/identity')
    entries=journal.get('files')
    if not isinstance(entries,list) or len(entries)!=3: raise WikiConflict('Invalid recovery write set')
    page=entries[0].get('path','')
    if not page.endswith('.md') or page=='index.md' or page.startswith('.wiki-doc/'):
        raise WikiConflict('Invalid recovery page path')
    expected=(page,'index.md',metadata_path(root,page).relative_to(root).as_posix())
    for i,entry in enumerate(entries):
        if entry.get('path')!=expected[i]: raise WikiConflict('Recovery target differs from managed write set')
        inside(root,entry['path'])
        prefix=f'.wiki-doc/transactions/{run_id}/{i}'
        if entry.get('backup')!=prefix+'.old' or entry.get('staged')!=prefix+'.staged':
            raise WikiConflict('Recovery staging/backup path differs from this transaction')
        for field in ('old_sha256','new_sha256'):
            value=entry.get(field)
            if not (field=='old_sha256' and value is None) and not (isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)):
                raise WikiConflict('Invalid recovery checksum')
        if journal['state'] not in ('committed','rolled_back') and entry['old_sha256'] is not None:
            if hash_file(inside(root,entry['backup']))!=entry['old_sha256']:
                raise WikiConflict('Recovery backup checksum mismatch')


def recover(wiki_root):
    root=Path(wiki_root).resolve()
    with index_lock(root): return _recover_locked(root)


def _index_rebase_safe(root,plan,current):
    base=plan['index_base'].lstrip('\ufeff')
    text=(current or b'').decode('utf-8-sig')
    if not base: base='# Wiki\n\n'
    if not text.startswith(base.rstrip()): return False
    suffix=text[len(base.rstrip()):].strip()
    if not suffix: return True
    from urllib.parse import unquote
    import re
    published={m['page_id'] for m in load_metadata(root)}
    return all((m:=re.fullmatch(r'- \[[^\n]+\]\(([^)]+)\)',line.strip())) is not None and unquote(m[1]) in published
               for line in suffix.splitlines() if line.strip())


def _check_plan(run,root):
    plan=read_json(run/'publication.json')
    required={'schema_version','run_id','page_id','canonical_key','wiki_root','draft_sha256',
              'expected_page_sha256','expected_index_sha256','expected_metadata_sha256','index_base'}
    if not isinstance(plan,dict) or set(plan)!=required or plan['schema_version']!=1:
        raise WikiConflict('Invalid publication preparation contract')
    if Path(plan['wiki_root']).resolve()!=root: raise WikiConflict('Prepared wiki root differs from destination')
    manifest=read_json(run/'manifest.json')
    if plan['run_id']!=manifest['run_id'] or plan['page_id']!=manifest['page_id']:
        raise WikiConflict('Publication plan belongs to another run/page')
    reference=manifest.get('publication_plan')
    if not reference or reference.get('sha256')!=hash_file(run/'publication.json'):
        raise WikiConflict('Publication preparation is not bound by the checked manifest')
    if plan['draft_sha256']!=hash_file(run/'page.draft.md'):
        raise WikiConflict('Draft changed after publication preparation')
    facts=read_json(run/'facts.json')
    obj=next(o for o in facts['objects'] if o['id'] in facts['documented_object_ids'])
    if plan['canonical_key']!=obj['canonical_key']: raise WikiConflict('Publication canonical key differs from checked facts')
    if digest(plan['index_base'].encode('utf-8'))!=plan['expected_index_sha256'] and not (plan['index_base']=='' and plan['expected_index_sha256'] is None):
        raise WikiConflict('Prepared index text does not match its checksum')
    return plan


def publish(run_dir,wiki_root,*,project_root=None,profile_path=None,policy_path=None,dry_run=False,fault=None):
    run,root=Path(run_dir).resolve(),Path(wiki_root).resolve()
    plan=_check_plan(run,root)
    page,index,meta=inside(root,plan['page_id']),inside(root,'index.md'),metadata_path(root,plan['page_id'])
    roots={'project':Path(project_root).resolve() if project_root else run,'wiki':root}
    def checked():
        result=evaluate_bundle(run,roots=roots,profile_path=profile_path,policy_path=policy_path)
        if not result['publication_authorized']: raise WikiConflict('Full gate refused publication: '+'; '.join(result['errors']))
    def snapshots():
        if hash_file(page)!=plan['expected_page_sha256']: raise WikiConflict('Page changed after preparation; preserve manual edits and prepare again')
        if hash_file(meta)!=plan['expected_metadata_sha256']: raise WikiConflict('Publication metadata changed after preparation')
        current=read_bytes(index)
        if digest(current)!=plan['expected_index_sha256'] and not _index_rebase_safe(root,plan,current):
            raise WikiConflict('Index changed outside a verified concurrent append')
        return current
    def hit(name):
        if fault: fault(name)
    if dry_run:
        checked()
        current=snapshots()
        diff={}
        for name,old,new in ((plan['page_id'],read_bytes(page) or b'',(run/'page.draft.md').read_bytes()),
                             ('index.md',current or b'',update_index(current,plan['page_id']))):
            diff[name]=''.join(difflib.unified_diff(old.decode('utf-8-sig').splitlines(True),new.decode('utf-8-sig').splitlines(True),fromfile=name,tofile=name))
        new_metadata=_metadata(run,root,plan,roots,profile_path,policy_path)
        name=meta.relative_to(root).as_posix()
        diff[name]=''.join(difflib.unified_diff((read_bytes(meta) or b'').decode('utf-8-sig').splitlines(True),json_bytes(new_metadata).decode().splitlines(True),fromfile=name,tofile=name))
        return dict(dry_run=True,changes=diff)
    with index_lock(root):
        recovered=_recover_locked(root)
        if any(r['state']=='conflict' for r in recovered): raise WikiConflict('Unresolved recovery conflict; backups retained')
        # Same committed run is idempotent only if its actual files still match.
        if meta.is_file():
            prior=read_json(meta)
            if prior['run_id']==plan['run_id'] and hash_file(page)==prior['page_sha256']:
                checked()
                if hash_file(inside(root,prior['bundle'])/'manifest.json')!=hash_file(run/'manifest.json'):
                    raise WikiConflict('Committed run_id cannot be reused for a different bundle')
                from wiki_store import indexed_pages
                if plan['page_id'] not in indexed_pages((read_bytes(index) or b'').decode('utf-8-sig')):
                    raise WikiConflict('Previously committed index entry is missing')
                return dict(published=True,idempotent=True,page_id=plan['page_id'])
        checked()
        current_index=snapshots()
        for record in load_metadata(root):
            if record['canonical_key']==plan['canonical_key'] and record['page_id']!=plan['page_id']:
                raise WikiConflict('Canonical object already published at a different path')
        archive=inside(root,'.wiki-doc/runs/'+plan['run_id'])
        if not archive.exists(): save_bundle(run,archive,roots=roots,profile_path=profile_path,policy_path=policy_path)
        else:
            archived=evaluate_bundle(archive,roots={'wiki':root,'link_project':roots['project']},profile_path=profile_path,policy_path=policy_path)
            if not archived['publication_authorized'] or hash_file(archive/'manifest.json')!=hash_file(run/'manifest.json'):
                raise WikiConflict('Existing archive does not match this run')
        manifest=read_json(run/'manifest.json')
        metadata=_metadata(run,root,plan,roots,profile_path,policy_path)
        transaction=inside(root,'.wiki-doc/transactions/'+plan['run_id'])
        transaction.mkdir(parents=True,exist_ok=True)
        journal=dict(schema_version=1,run_id=plan['run_id'],state='prepared',files=[])
        entries=((page,(run/'page.draft.md').read_bytes()),(index,update_index(current_index,plan['page_id'])),(meta,json_bytes(metadata)))
        for i,(target,data) in enumerate(entries):
            old=read_bytes(target)
            backup=transaction/f'{i}.old'
            staged=transaction/f'{i}.staged'
            if old is not None: atomic_bytes(backup,old)
            atomic_bytes(staged,data)
            journal['files'].append(dict(path=target.relative_to(root).as_posix(),old_sha256=digest(old),new_sha256=digest(data),
                                         backup=backup.relative_to(root).as_posix(),staged=staged.relative_to(root).as_posix()))
        journal_path=inside(root,'.wiki-doc/journal/'+plan['run_id']+'.json')
        atomic_json(journal_path,journal)
        try:
            hit('prepared')
            for i,entry in enumerate(journal['files']):
                label=('page','index','metadata')[i]
                hit('before_'+label)
                target=inside(root,entry['path'])
                if hash_file(target)!=entry['old_sha256']: raise WikiConflict(f'{label} changed immediately before replacement')
                target.parent.mkdir(parents=True,exist_ok=True)
                os.replace(inside(root,entry['staged']),target)
                hit('after_'+label)
                journal['state']=label+'_replaced'
                atomic_json(journal_path,journal)
            for entry in journal['files']:
                if hash_file(inside(root,entry['path']))!=entry['new_sha256']: raise WikiConflict('Written publication bytes changed')
            journal['state']='committed'
            atomic_json(journal_path,journal)
        except Exception:
            _recover_locked(root)
            raise
        return dict(published=True,page_id=plan['page_id'],archive=str(archive),journal=str(journal_path))


def _metadata(run,root,plan,roots,profile_path,policy_path):
    manifest=read_json(run/'manifest.json')
    return dict(schema_version=1,page_id=plan['page_id'],canonical_key=plan['canonical_key'],page_sha256=plan['draft_sha256'],
                bundle='.wiki-doc/runs/'+plan['run_id'],run_id=plan['run_id'],profile=str(Path(profile_path).resolve()) if profile_path else None,
                source_files=manifest['sql_files']+manifest.get('context_files',[]),project_root=str(roots['project']),
                policy=str(Path(policy_path).resolve()) if policy_path else None)


def cleanup_run(run_dir,wiki_root):
    """Remove only this committed UUID run immediately under the wiki's .tmp."""
    import shutil
    run,root=Path(run_dir).resolve(),Path(wiki_root).resolve()
    with index_lock(root):
        manifest=read_json(run/'manifest.json'); run_id=manifest['run_id']
        expected=inside(root,'.tmp/'+run_id)
        if run!=expected or not run.is_relative_to(root): raise WikiConflict('Cleanup only accepts this wiki/.tmp/<run_id>')
        journal=read_json(inside(root,'.wiki-doc/journal/'+run_id+'.json'))
        if journal['state']!='committed': raise WikiConflict('Uncommitted run cannot be cleaned')
        archive=inside(root,'.wiki-doc/runs/'+run_id)
        for name in ('manifest.json','decision.json','publication.json','page.draft.md','facts.json','coverage.json','inventory.json','validation_plan.json','validation.json'):
            if hash_file(run/name)!=hash_file(archive/name): raise WikiConflict('Cleanup source changed after archival')
        shutil.rmtree(run)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','publish','recover'])
    parser.add_argument('run_dir',nargs='?')
    parser.add_argument('--wiki',required=True)
    parser.add_argument('--project-root')
    parser.add_argument('--profile')
    parser.add_argument('--policy')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--cleanup-run',action='store_true',help='After commit, remove only this wiki/.tmp/<run_id>')
    args=parser.parse_args(argv)
    if args.mode!='recover' and not args.run_dir: parser.error('run_dir is required')
    try:
        if args.mode=='prepare': result=prepare(args.run_dir,args.wiki)
        elif args.mode=='recover': result=recover(args.wiki)
        else: result=publish(args.run_dir,args.wiki,project_root=args.project_root,profile_path=args.profile,policy_path=args.policy,dry_run=args.dry_run)
        if args.cleanup_run and args.mode=='publish' and not args.dry_run: cleanup_run(args.run_dir,args.wiki)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    except (WikiConflict,ArtifactInputError,OSError,ValueError) as exc:
        print(json.dumps(dict(published=False,error=str(exc)),ensure_ascii=True))
        return 1


if __name__=='__main__': raise SystemExit(main())
