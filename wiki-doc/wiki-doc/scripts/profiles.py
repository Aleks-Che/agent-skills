"""Explicit profile loading and source-code access findings, separate from doc defects."""
import hashlib
import json
import re
from pathlib import Path

from artifact_schema import read_json, ArtifactInputError

PACKAGE = Path(__file__).resolve().parents[1]
CKR = PACKAGE / 'profiles/ckr_gp/profile.json'


def load_profile(path=None):
    if path is None or path in ('CKR_GP','ckr_gp'):
        path = CKR
    else:
        path = Path(path).resolve()
        if path == PACKAGE / 'project-profile.md': path = CKR
        elif path.name == 'profile.md': path = path.with_suffix('.json')
    path = Path(path)
    data = read_json(path)
    required = {'schema_version','id','activation_prefixes','schema_pattern','families','audit_loader_suffix','audit_call_layer','features','matrix'}
    if not isinstance(data,dict) or set(data)!=required or data.get('schema_version')!=1:
        raise ArtifactInputError('Invalid profile contract')
    if not all(isinstance(data[k],list) and all(isinstance(x,str) for x in data[k]) for k in ('features','activation_prefixes')):
        raise ArtifactInputError('Profile features/prefixes must be string arrays')
    if not isinstance(data['matrix'],dict) or not isinstance(data['families'],dict):
        raise ArtifactInputError('Profile matrix/families must be mappings')
    if not all(isinstance(data[k],str) and data[k] for k in ('id','schema_pattern','audit_loader_suffix','audit_call_layer')):
        raise ArtifactInputError('Profile names/pattern/layer must be nonempty strings')
    if not all(isinstance(k,str) and isinstance(v,str) for k,v in data['families'].items()):
        raise ArtifactInputError('Profile family names must be strings')
    try: re.compile(data['schema_pattern'])
    except (TypeError,re.error) as exc: raise ArtifactInputError(str(exc)) from exc
    for row in data['matrix'].values():
        if not isinstance(row,dict) or any(v not in ('allowed','forbidden','unconfirmed') for v in row.values()):
            raise ArtifactInputError('Invalid profile access decision')
    return data


def profile_hash(path):
    if str(path) in ('CKR_GP','ckr_gp') or Path(path).resolve()==PACKAGE/'project-profile.md': path=CKR
    path=Path(path).resolve()
    if path.name=='profile.md': path=path.with_suffix('.json')
    load_profile(path)
    records=[]
    for file in (path,path.with_name('profile.md'),path.with_name('access.md')):
        if not file.is_file(): raise ArtifactInputError(f'Missing profile resource: {file}')
        records.append((file.name,hashlib.sha256(file.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(records,separators=(',',':')).encode()).hexdigest()


def detect_profile(inventory, explicit=None):
    if explicit is not None: return load_profile(explicit)
    config=load_profile()
    identifiers=[i['details'].get('schema','') or '' for i in inventory['items']]
    identifiers += [r for i in inventory['items'] for f in ('reads','writes','calls') for r in i.get(f,[])]
    return config if any(s.startswith(tuple(config['activation_prefixes'])) for s in identifiers) else None


def classify_access(profile, source_schema, target, operation):
    target_schema=target.rsplit('.',1)[0] if '.' in target else ''
    source=re.fullmatch(profile['schema_pattern'],source_schema or '')
    dest=re.fullmatch(profile['schema_pattern'],target_schema)
    if not source or not dest: return dict(status='unconfirmed',reason='Project identity/layer is not established')
    sf,df=profile['families'][source[1]],profile['families'][dest[1]]
    same=sf==df and source[2]==dest[2]
    if sf=='SVD' and df=='SVD' and dest[2].endswith(profile['audit_loader_suffix']) and dest[3]==profile['audit_call_layer'] and operation=='calls':
        return dict(status='allowed',reason='Direct audit routine call exception')
    key=f'{sf}:{df}:{"same" if same else "other"}'
    status=profile['matrix'].get(key,{}).get(dest[3],'unconfirmed')
    return dict(status=status,reason=f'Profile access matrix {key}, target layer {dest[3]}')


def access_findings(inventory, profile):
    declarations={i['anchor']['object_or_scope']:i['details'] for i in inventory['items'] if i['kind']=='DECLARATION'}
    findings=[]
    for item in inventory['items']:
        source=declarations.get(item['anchor']['object_or_scope'],{}).get('schema')
        for field in ('reads','writes','calls'):
            for target in item.get(field,[]):
                if target.startswith('@'): continue
                findings.append(dict(source_schema=source,target=target,operation=field,source_ref=item['source_ref'],
                                     **classify_access(profile,source,target,field)))
    return findings
