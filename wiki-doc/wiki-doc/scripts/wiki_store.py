"""Shared local wiki paths, metadata, managed regions and durable file writes."""
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

from artifact_schema import read_json
from coverage_gate import MarkdownDocument


class WikiConflict(ValueError): pass


def _namespace_key(path):
    """Compare resolved Windows DOS/UNC paths independently of the device prefix.

    ntpath.realpath can retain the extended prefix when a replacement makes its
    second OS probe fail. Resolution still happens before this comparison;
    symlinks pointing outside the wiki are never accepted by spelling alone.
    """
    value = str(path)
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'):
            return PureWindowsPath('\\\\' + value[8:])
        if re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
            return PureWindowsPath(value[4:])
    return path


def inside(root, relative):
    root=Path(root).resolve()
    rel=PurePosixPath(str(relative).replace('\\','/'))
    if rel.is_absolute() or '..' in rel.parts or ':' in str(rel):
        raise WikiConflict(f'Unsafe wiki-relative path: {relative}')
    path=(root / str(rel)).resolve()
    try:
        suffix = _namespace_key(path).relative_to(_namespace_key(root))
    except ValueError:
        raise WikiConflict(f'Path escapes wiki: {relative}') from None
    if not suffix.parts:
        raise WikiConflict(f'Path escapes wiki: {relative}')
    return root.joinpath(*suffix.parts)


def digest(data): return hashlib.sha256(data).hexdigest() if data is not None else None
def read_bytes(path): return Path(path).read_bytes() if Path(path).exists() else None
def hash_file(path): return digest(read_bytes(path))


def atomic_bytes(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.new')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,path)


def json_bytes(data): return (json.dumps(data,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode()
def atomic_json(path,data): atomic_bytes(path,json_bytes(data))
def metadata_path(root,page_id): return inside(root,'.wiki-doc/pages/'+hashlib.sha256(page_id.encode()).hexdigest()+'.json')


MANAGED=re.compile(r'\A(.*?)(<!-- wiki-doc:managed begin -->\n)(.*?)(\n<!-- wiki-doc:managed end -->)(.*)\Z',re.S)


def merge_page(base,current,incoming):
    """Preserve explicit manual exterior; overlapping managed edits are conflicts."""
    for text in (base,current,incoming):
        if text is not None and 'wiki-doc:managed' in text:
            if text.count('<!-- wiki-doc:managed begin -->')!=1 or text.count('<!-- wiki-doc:managed end -->')!=1 or not MANAGED.fullmatch(text):
                raise WikiConflict('Managed region must contain exactly one correctly ordered marker pair')
    if current is None: return incoming
    if base is not None:
        if current==base: return incoming
        if incoming==base: return current
    old=MANAGED.fullmatch(base or '')
    actual=MANAGED.fullmatch(current)
    new=MANAGED.fullmatch(incoming)
    if actual and new:
        if old and actual[3]!=old[3] and new[3]!=old[3]:
            raise WikiConflict('Both SQL generation and the current page changed the managed region')
        body=actual[3] if old and new[3]==old[3] else new[3]
        return actual[1]+new[2]+body+new[4]+actual[5]
    if base is not None and current.startswith(base):
        return incoming+current[len(base):]
    raise WikiConflict('Existing page has no unambiguous generated base/managed region; preserve a conflict draft')


def indexed_pages(index_text):
    document=MarkdownDocument(index_text)
    return {unquote(urlsplit(link).path) for link in document.links
            if not urlsplit(link).scheme and not urlsplit(link).netloc and urlsplit(link).path}


def update_index(current,page_id):
    text=current.decode('utf-8-sig') if current is not None else '# Wiki\n\n'
    if page_id in indexed_pages(text): return text.encode('utf-8')
    from urllib.parse import quote
    label=Path(page_id).stem.replace('[','\\[').replace(']','\\]')
    return (text.rstrip()+'\n\n- ['+label+']('+quote(page_id,safe='/')+')\n').encode('utf-8')


def load_metadata(root):
    folder=inside(root,'.wiki-doc/pages')
    result=[]
    if folder.is_dir():
        for path in sorted(folder.glob('*.json')):
            data=read_json(path)
            required={'schema_version','page_id','canonical_key','page_sha256','bundle','run_id','profile','source_files'}
            if not isinstance(data,dict) or not required<=data.keys() or data['schema_version']!=1:
                raise WikiConflict(f'Invalid publication metadata: {path.name}')
            inside(root,data['page_id'])
            inside(root,data['bundle'])
            if path.resolve()!=metadata_path(root,data['page_id']): raise WikiConflict('Metadata filename differs from page identity')
            if data['bundle']!='.wiki-doc/runs/'+data['run_id']: raise WikiConflict('Metadata bundle differs from its run identity')
            result.append(data)
    return result
