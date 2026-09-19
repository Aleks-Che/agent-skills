"""Deterministic reference adapter for saved regression runs (no LLM quality claim).

Reads raw source/context only; creates a reviewable technical fact page and a full
bundle. Independent expectations are consumed exclusively by run_regression.py.
"""
import argparse
import json
import uuid
from pathlib import Path

from artifact_schema import FACT_ARRAYS
from bundle import create_manifest,compute_tool_versions,write_manifest
from check_policy import load_policy
from ddl import enrich_inventory,reconstruct
from evidence import sha256_file
from identity import page_id
from page_claims import expected_claims,check_page_claims
from profiles import detect_profile,access_findings,CKR
from sql_extract import extract_inventory
from sql_types import column_catalog
from validation_plan import generate_plan
from validation_gate import evaluate_bundle
from wiki_store import atomic_json,atomic_bytes

PACKAGE=Path(__file__).resolve().parents[1]


def build(sql_path,run_dir,*,project_root,subject,context=(),migration_manifest=None,profile=None,version='15',wiki_root=None):
    root,run=Path(project_root).resolve(),Path(run_dir).resolve()
    source=Path(sql_path).resolve(); context=[Path(p).resolve() for p in context]
    migration_manifest=Path(migration_manifest).resolve() if migration_manifest else None
    if migration_manifest:
        ordered=reconstruct(migration_manifest,project_root=root)
        context=list(dict.fromkeys(context+[root/r['path'] for r in ordered['inputs'] if root/r['path']!=source]))
    inv=extract_inventory(source.read_text(encoding='utf-8-sig'),source.relative_to(root).as_posix(),sha256_file(source),
                          version=version,documented_subjects=[subject])
    inv['run_id']=str(uuid.uuid4())
    enrich_inventory(inv,context_files=context,project_root=root,migration_manifest=migration_manifest)
    chosen=detect_profile(inv,profile)
    profile_path=(profile or CKR) if chosen else None
    declaration=next(i for i in inv['items'] if i['kind']=='DECLARATION'); d=declaration['details']
    pid=page_id(d['canonical_key'])
    facts=dict(schema_version=2,run_id=inv['run_id'],dialect=inv['dialect'],profile=chosen['id'] if chosen else None,
               documented_object_ids=['obj_1'],inputs=inv['inputs'],page_contract='claims-v1',**{g:[] for g in FACT_ARRAYS})
    main=dict(id='obj_1',kind=d['object_kind'],schema=d['schema'],name=d['name'],signature=d.get('signature'),
              canonical_key=d['canonical_key'],page_id=pid,source_refs=[declaration['source_ref']])
    for field in ('parameters','returns','volatility'):
        if field in d: main[field]=d[field]
    facts['objects'].append(main)
    objects={f"{d['schema']}.{d['name']}":'obj_1'}
    kinds={r:('function' if field=='calls' else 'table') for i in inv['items'] for field in ('reads','writes','calls') for r in i.get(field,[])}
    def obj(name,ref,kind=None):
        if name in objects: return objects[name]
        oid=f'obj_{len(objects)+1}'; objects[name]=oid
        kind=kind or kinds.get(name,'table')
        entry=dict(id=oid,kind=kind,schema=name.rsplit('.',1)[0] if '.' in name else None,
                   name=name.rsplit('.',1)[-1],page_id=None,source_refs=[ref])
        if name.startswith('@'):
            entry.update(kind='cte' if name.startswith('@cte:') else 'temp_table',schema=None,name=name.rsplit(':',1)[-1],
                         canonical_key=name,scope='obj_1',physical=False)
        facts['objects'].append(entry)
        return oid
    operations={}
    for item in inv['items']:
        if item['kind']=='DECLARATION': continue
        details=item.get('details',{}); ref=item['source_ref']; oid=f'op_{len(facts["operations"])+1}'
        entry=dict(id=oid,kind=item['kind'],scope='obj_1',order=len(facts['operations'])+1,source_refs=[ref],condition_ids=[],dynamic=False)
        for field in ('reads','writes','calls'): entry[field]=[obj(name,ref) for name in item.get(field,[])]
        structural={k:v for k,v in details.items() if k in ('branches','branch','ddl','temporary','lifetime','reference','confirmed_call_effects','group_by','arguments','command_kind','query','assignments','target_columns','into','assignment_target','return_expression','result_for')}
        # Flatten nested index/trigger/grant structure and ALTER constraints into the operation.
        if isinstance(details.get('structure'), dict): structural.update(details['structure'])
        if details.get('constraints'): structural['constraints']=details['constraints']
        if structural: entry['structure']=structural
        if item['kind']=='EXECUTE':
            entry['dynamic']=dict(template=details.get('template') or 'unresolved',unresolved_parts=details.get('unresolved_parts',['unknown runtime target']))
            facts['unknowns'].append(dict(id=f'unknown_{len(facts["unknowns"])+1}',what='Concrete dynamic target',
                reason='Only the SQL template and visible static dependencies are established; runtime identifiers are unknown.',related_facts=[oid]))
        for group,prefix in (('formulas','formula'),('conditions','cond')):
            for expression in details.get(group,[]):
                fid=f'{prefix}_{len(facts[group])+1}'
                facts[group].append(dict(id=fid,expression=expression,operation_ids=[oid],source_refs=[ref]))
                if group=='conditions': entry['condition_ids'].append(fid)
        facts['operations'].append(entry)
        operations[tuple(item['anchor'][k] for k in ('object_or_scope','construct','ordinal'))]=oid
    catalogue=column_catalog(inv,[source],context,root,migration_manifest)
    used=set(objects)
    if d['object_kind']=='migration': used.update(catalogue['tables'])
    mappings={(m['table'],m['name']):m for m in catalogue['mappings'] if m['table'] in used}
    # Include targets with unknown DDL so absence remains a fact, never an external label.
    for name,column in mappings:
        if name not in catalogue['tables']:
            catalogue['tables'][name]=dict(columns=[dict(name=c,type=None) for t,c in mappings if t==name],source_ref=mappings[(name,column)]['source_ref'],missing=True)
    for name in sorted(used):
        table=catalogue['tables'].get(name)
        routine=catalogue['functions'].get(name,[])
        local=next((i for i in inv['items'] if i.get('details',{}).get('reference')==name),None)
        ref=table['source_ref'] if table else (routine[0]['source_ref'] if len(routine)==1 else declaration['source_ref'])
        oid=obj(name,ref)
        definition=dict(id=f'def_{len(facts["definitions"])+1}',object_id=oid,
                        status='resolved' if table and not table.get('missing') or len(routine)==1 or local or oid=='obj_1' else 'not_found',source_refs=[ref])
        if local:
            definition['source_refs']=[local['source_ref']]
            definition['structure']=local['details'].get('query',local['details'].get('ddl'))
        if d['object_kind']=='migration' and table:
            state=d['reconstruction']; definition.update(revision=state.get('target_revision'),migration_order=[r['path'] for r in state['inputs']])
        facts['definitions'].append(definition)
        for column in table['columns'] if table else []:
            cid=f'col_{len(facts["columns"])+1}'; mapping=mappings.get((name,column['name']))
            entry=dict(id=cid,object_id=oid,name=column['name'],type_target=column['type'],
                       type_expression=mapping['type_expression'] if mapping else None,source_refs=[ref],
                       default=column.get('default'),nullable=not column['not_null'] if 'not_null' in column else None)
            entry['expression_status']=('known' if entry['type_expression'] is not None else 'unknown') if mapping else 'not_applicable'
            if 'primary_key' in column: entry['primary_key']=column['primary_key']
            if mapping: entry['expression']=mapping['expression']
            if column.get('comment'): entry['description']=column['comment']
            entry['type_evidence']={field:dict(kind='expression' if field=='type_expression' or table.get('derived') else 'declaration',
                                  source_refs=[mapping['source_ref'] if field=='type_expression' else ref])
                                    for field in ('type_target','type_expression') if entry[field] is not None}
            facts['columns'].append(entry)
            if entry['type_target'] is None or entry['expression_status']=='unknown':
                facts['unknowns'].append(dict(id=f'unknown_{len(facts["unknowns"])+1}',what=f'Type evidence for {name}.{column["name"]}',
                    reason='SQL/DDL does not establish the target or expression type in the supported static subset; see the separate expression_status.',related_facts=[cid]))
    findings=access_findings(inv,chosen) if chosen else []
    if chosen: main['access_observations']=findings
    plan=generate_plan(inv,load_policy(),page_id=pid,profile_active=bool(chosen),profile_path=profile_path)
    run.mkdir(parents=True,exist_ok=True)
    for name,value in (('facts',facts),('inventory',inv),('validation_plan',plan)): atomic_json(run/(name+'.json'),value)
    page,coverage=render(facts,plan,findings)
    atomic_bytes(run/'page.draft.md',page.encode('utf-8')); atomic_json(run/'coverage.json',coverage)
    atomic_json(run/'source_findings.json',findings)
    return finish(run,sql_files=[source],context=context,project_root=root,profile_path=profile_path,migration_manifest=migration_manifest,wiki_root=wiki_root)


def render(facts,plan,findings=()):
    main=facts['objects'][0]
    headings={'header_purpose':'Object and purpose','schema_signature':'Signature and parameters','entities':'Columns and definitions',
              'formulas_dependencies':'Operations, dependencies and expressions','dataflow_diagram':'Data flow','misc':'Limitations and source observations'}
    sections={k:[] for k in headings}
    claims=expected_claims(facts); entries={}
    for group in FACT_ARRAYS:
        section='formulas_dependencies' if group in ('operations','formulas','conditions') else ('entities' if group=='columns' else ('misc' if group=='unknowns' else 'schema_signature'))
        for fact in facts[group]:
            rows=[]
            for (fid,field),value in claims.items():
                if fid==fact['id']:
                    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True).replace('|','\\|')
                    delimiter='`'*(max([len(x) for x in __import__('re').findall(r'`+',encoded)]+[0])+1)
                    rows.append(f'| {fid} | {field} | {delimiter} {encoded} {delimiter} |')
            sections[section].append('<!-- wiki-doc:fragment '+fact['id']+' -->\n| Fact | Property | SQL value |\n| --- | --- | --- |\n'+'\n'.join(rows))
            entries[fact['id']]=[dict(section_id=section,fragment_ref=fact['id'])]
    sections['header_purpose']=[f"`{main['canonical_key']}`. Static technical reference for the selected SQL declaration. Statements and evidence below describe the visible behavior."]
    by_id={o['id']:o for o in facts['objects']}
    diagram=['flowchart LR']
    def label(oid):
        obj=by_id[oid]
        return ((obj.get('schema')+'.') if obj.get('schema') else '')+obj['name']
    def safe(value): return value.replace('"','&quot;').replace('[','&#91;').replace(']','&#93;')
    for oid in by_id: diagram.append(f'  {oid}["{safe(label(oid))}"]')
    for op in facts['operations']:
        opid=op['id']; diagram.append(f'  {opid}["{op["order"]}: {op["kind"]}"]')
        for oid in op['reads']: diagram.append(f'  {oid} -->|reads| {opid}')
        for oid in op['writes']: diagram.append(f'  {opid} -->|writes| {oid}')
        for oid in op['calls']: diagram.append(f'  {opid} -->|calls| {oid}')
        result_for=op.get('structure',{}).get('result_for')
        if result_for:
            for oid,obj in by_id.items():
                if obj.get('canonical_key')==result_for or label(oid)==result_for: diagram.append(f'  {opid} -->|result| {oid}')
        for effect in op.get('structure',{}).get('confirmed_call_effects',[]):
            callers=[oid for oid in by_id if label(oid)==effect['call']]
            for table in effect['writes']:
                for oid in by_id:
                    if label(oid)==table:
                        for caller in callers: diagram.append(f'  {caller} -.->|confirmed call write| {oid}')
        if op['dynamic']:
            diagram.extend([f'  runtime_{opid}["Runtime target: unresolved"]',f'  {opid} -->|dynamic command| runtime_{opid}'])
    sections['dataflow_diagram']=['```mermaid\n'+'\n'.join(diagram)+'\n```\n\nDiagram source is provided; a Mermaid renderer was not run.']
    sections['misc'].append('Runtime execution, undocumented business requirements and unsupported type inference are not established by static analysis.')
    if findings:
        sections['misc'].append('Source access observations (these describe SQL, separately from documentation defects):\n\n'+
            '\n'.join(f"- {f['operation']} `{f['target']}`: **{f['status']}** — {f['reason']}." for f in findings))
    text='<!-- wiki-doc:managed begin -->\n'+'\n\n'.join('## '+headings[s]+' {#'+s+'}\n\n'+'\n\n'.join(body) for s,body in sections.items() if body)+'\n<!-- wiki-doc:managed end -->\n'
    return text,dict(schema_version=2,run_id=facts['run_id'],page_id=main['page_id'],entries=entries)


def finish(run,*,sql_files,context,project_root,profile_path=None,migration_manifest=None,wiki_root=None):
    from artifact_schema import read_json
    run=Path(run); facts=read_json(run/'facts.json'); plan=read_json(run/'validation_plan.json'); inv=read_json(run/'inventory.json')
    page=(run/'page.draft.md').read_text(encoding='utf-8-sig'); errors=check_page_claims(facts,page)
    refs=[{**r,'root':'project','start_line':1,'end_line':len((Path(project_root)/r['path']).read_text(encoding='utf-8-sig').splitlines())} for r in inv['inputs']]
    refs.append(dict(root='run',path='page.draft.md',sha256=sha256_file(run/'page.draft.md'),start_line=1,end_line=len(page.splitlines())))
    all_ids=[f['id'] for g in FACT_ARRAYS for f in facts[g]]
    checks=[dict(id='result:'+c['id'],plan_check_id=c['id'],status='defect' if errors else 'ok',category=c['category'],blocking=c['blocking'],
                 reason='Rendered claims, SQL inventory and evidence checked; this deterministic adapter does not certify arbitrary prose.',
                 evidence=refs,fact_ids=all_ids,**({'defect_code':'unsupported_claim'} if errors else {})) for c in plan['required_checks']]
    atomic_json(run/'validation.json',dict(schema_version=2,run_id=facts['run_id'],page_id=plan['page_id'],checks=checks))
    manifest=create_manifest(run_id=facts['run_id'],page_id=plan['page_id'],sql_files=sql_files,context_files=context,
        migration_manifest=migration_manifest,artifacts_dir=run,project_dir=project_root,tool_versions=compute_tool_versions(PACKAGE,profile_path=profile_path))
    write_manifest(manifest,run/'manifest.json')
    result=evaluate_bundle(run,roots={'project':project_root,**({'wiki':wiki_root} if wiki_root else {})},profile_path=profile_path,write_decision=True)
    return dict(run_dir=str(run),profile=str(profile_path) if profile_path else None,**result)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sql',required=True); p.add_argument('--run-dir',required=True); p.add_argument('--project-root',required=True)
    p.add_argument('--subject',required=True); p.add_argument('--context',nargs='*',default=[]); p.add_argument('--migration-manifest')
    p.add_argument('--profile'); p.add_argument('--version',default='15')
    a=p.parse_args(argv)
    try:
        result=build(a.sql,a.run_dir,project_root=a.project_root,subject=a.subject,context=a.context,migration_manifest=a.migration_manifest,profile=a.profile,version=a.version)
        print(json.dumps(result,ensure_ascii=True,indent=2)); return 0 if result['publication_authorized'] else 1
    except (ValueError,OSError) as exc: print(json.dumps({'error':str(exc)})); return 2


if __name__=='__main__': raise SystemExit(main())

