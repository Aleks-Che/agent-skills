"""Conservative column type evidence from declarations, casts and ordered DDL."""
from pathlib import Path
from ddl import catalog, reconstruct
from sql_ast import sql, type_name, relation, walk, require_parser


def infer_expression(expression,tables,variables=None,aliases=None):
    require_parser()
    from pglast import ast, parse_sql
    try: node=parse_sql('SELECT '+expression)[0].stmt.targetList[0].val
    except Exception: return None
    if isinstance(node,ast.TypeCast): return type_name(node.typeName)
    if isinstance(node,ast.FuncCall):
        name='.'.join(p.sval for p in node.funcname)
        return {'now':'timestamp with time zone','count':'bigint','to_date':'date'}.get(name)
    if isinstance(node,ast.ColumnRef) and isinstance(node.fields[-1],ast.String):
        name=node.fields[-1].sval
        if len(node.fields)==1 and name in (variables or {}): return variables[name]
        visible=tables
        if len(node.fields)>1 and all(isinstance(p,ast.String) for p in node.fields):
            owner='.'.join(p.sval for p in node.fields[:-1]); key=(aliases or {}).get(owner,owner)
            if key in tables: visible={key:tables[key]}
            else: return None
        elif aliases:
            visible={k:v for k,v in tables.items() if k in aliases.values()}
        candidates={c['type'] for t in visible.values() for c in t['columns'] if c['name']==name}
        return next(iter(candidates)) if len(candidates)==1 else None
    return None


def column_catalog(inventory,sql_files,context_files,root,migration_manifest=None):
    """Returns known target declarations and positional INSERT/CTAS outputs.

    This pass reads SQL bytes independently of facts and Markdown. It deliberately
    leaves arithmetic/operator/function type resolution to a DB-aware reviewer.
    """
    root=Path(root).resolve()
    files=list(dict.fromkeys(Path(p).resolve() for p in [*sql_files,*context_files]))
    migration=reconstruct(migration_manifest,project_root=root) if migration_manifest else None
    ordered={(root/r['path']).resolve() for r in migration['inputs']} if migration else set()
    context=catalog([p for p in files if p not in ordered],root)
    if context['errors']: raise ValueError('; '.join(context['errors']))
    tables=dict(context['tables'])
    if migration and migration['status']=='resolved':
        for name,table in migration['tables'].items():
            last=migration['inputs'][-1]
            tables[name]={**table,'source_ref':{**last,'start_line':1,'end_line':len((root/last['path']).read_text(encoding='utf-8-sig').splitlines())}}
    declaration=next(i for i in inventory['items'] if i['kind']=='DECLARATION')
    scope=declaration['anchor']['object_or_scope']
    for item in inventory['items']:
        details=item.get('details',{})
        if item['kind']=='CREATE' and details.get('columns'):
            # Ordered physical DDL is already in the catalogue. Only synthesize
            # scoped locals; replacing a table here would undo its ALTERs/DROP.
            reference=details.get('reference',item.get('writes',[''])[0])
            if reference.startswith('@'):
                tables[reference]=dict(columns=details['columns'],source_ref=item['source_ref'])
    mappings=[]
    from pglast import ast, parse_sql, parse_plpgsql
    def inspect(node,ref):
        before=len(mappings)
        if isinstance(node,ast.MergeStmt):
            for branch in node.mergeWhenClauses or ():
                if branch.commandType.name=='CMD_UPDATE':
                    for target in branch.targetList or ():
                        mappings.append(dict(table=relation(node.relation),name=target.name,expression=sql(target.val),source_ref=ref))
                elif branch.commandType.name=='CMD_INSERT':
                    for target,value in zip(branch.targetList or (),branch.values or ()):
                        mappings.append(dict(table=relation(node.relation),name=target.name,expression=sql(value),source_ref=ref))
        if isinstance(node,ast.UpdateStmt):
            for target in node.targetList or ():
                mappings.append(dict(table=relation(node.relation),name=target.name,expression=sql(target.val),source_ref=ref))
        if isinstance(node,ast.InsertStmt) and node.cols and isinstance(node.selectStmt,ast.SelectStmt):
            target=relation(node.relation)
            if not node.relation.schemaname:
                target=next((k for k in tables if k.startswith('@temp:'+scope+':') and k.endswith(':'+target)),target)
            for col,value in zip(node.cols,node.selectStmt.targetList or ()):
                mappings.append(dict(table=target,name=col.name,expression=sql(value.val),source_ref=ref))
        for child in walk(node):
            if child is not node and isinstance(child,ast.InsertStmt): inspect(child,ref)
        aliases={r.alias.aliasname if r.alias else r.relname:relation(r) for r in walk(node) if isinstance(r,ast.RangeVar)}
        for mapping in mappings[before:]: mapping.setdefault('aliases',aliases)
    def pl(value,ref):
        if isinstance(value,dict):
            expr=value.get('PLpgSQL_expr',{}).get('query','')
            if expr:
                try:
                    for raw in parse_sql(expr): inspect(raw.stmt,ref)
                except Exception: pass
            for child in value.values(): pl(child,ref)
        elif isinstance(value,list):
            for child in value: pl(child,ref)
    for path in sql_files:
        text=Path(path).read_text(encoding='utf-8-sig')
        ref=next(r for r in inventory['inputs'] if r['path']==Path(path).resolve().relative_to(root).as_posix())
        ref={**ref,'start_line':1,'end_line':len(text.splitlines())}
        for raw in parse_sql(text):
            node=raw.stmt
            if isinstance(node,ast.CreateFunctionStmt):
                parts=[p.sval for p in node.funcname]
                d=declaration['details']
                if parts[-1]!=d['name'] or (len(parts)>1 and parts[-2]!=d['schema']): continue
                options={o.defname:o.arg for o in node.options or ()}
                if getattr(options.get('language'),'sval','')=='plpgsql':
                    pl(parse_plpgsql(sql(node)),ref)
            else: inspect(node,ref)
    d=declaration['details']
    if d['object_kind'] in ('view','materialized_view','ctas'):
        target=f"{d['schema']}.{d['name']}"
        aliases={}
        for path in sql_files:
            for raw in parse_sql(Path(path).read_text(encoding='utf-8-sig')):
                n=raw.stmt
                selected=(isinstance(n,ast.ViewStmt) and relation(n.view)==target) or (isinstance(n,ast.CreateTableAsStmt) and relation(n.into.rel)==target)
                if selected: aliases={r.alias.aliasname if r.alias else r.relname:relation(r) for r in walk(n.query) if isinstance(r,ast.RangeVar)}
        for out in d.get('output_columns',[]):
            name=out['name'] or out['expression']
            mappings.append(dict(table=target,name=name,expression=out['expression'],source_ref=declaration['source_ref'],aliases=aliases))
        tables[target]=dict(columns=[dict(name=m['name'],type=infer_expression(m['expression'],tables,aliases=aliases),default=None,not_null=False)
                                    for m in mappings if m['table']==target],source_ref=declaration['source_ref'],derived=True)
    variables={p['name']:p['type'] for p in d.get('parameters',[]) if p.get('name')}
    for mapping in mappings:
        mapping['type_expression']=infer_expression(mapping['expression'],tables,variables,mapping.get('aliases'))
    return dict(tables=tables,mappings=mappings,functions=context['functions'])


def check_types(facts,catalogue):
    """Reject invented target types, swapped expression types and dropped DDL columns."""
    errors=[]
    objects={o['id']:o for o in facts['objects']}
    for col in facts['columns']:
        obj=objects[col['object_id']]
        key=obj.get('canonical_key') if obj['kind'] in ('cte','temp_table') else f"{obj.get('schema')}.{obj['name']}"
        table=catalogue['tables'].get(key)
        expected=next((c for c in table['columns'] if c['name']==col['name']),None) if table else None
        from identity import normalize_type
        same=lambda a,b: a==b if a is None or b is None else normalize_type(a)==normalize_type(b)
        if not same(col['type_target'],expected['type'] if expected else None):
            errors.append(f"facts column {col['id']}: target type is not established by SQL/ordered DDL")
        for mapping in catalogue['mappings']:
            if mapping['table']==key and mapping['name']==col['name']:
                from validation_gate import _expression_key
                if col.get('expression') and _expression_key(col['expression'])!=_expression_key(mapping['expression']):
                    errors.append(f"facts column {col['id']}: expression differs from SQL mapping")
                if not same(col['type_expression'],mapping['type_expression']):
                    errors.append(f"facts column {col['id']}: expression type differs from independently supported inference")
        if expected:
            for field,target in (('default','default'),('description','comment'),('primary_key','primary_key')):
                if field in col and col[field]!=expected.get(target): errors.append(f"facts column {col['id']}: {field} differs from DDL")
            if 'nullable' in col and 'not_null' in expected and col['nullable'] != (not expected['not_null']):
                errors.append(f"facts column {col['id']}: nullable differs from DDL")
        if 'expression_status' in col:
            mapped=any(m['table']==key and m['name']==col['name'] for m in catalogue['mappings'])
            status=('known' if col['type_expression'] is not None else 'unknown') if mapped else 'not_applicable'
            if col['expression_status']!=status: errors.append(f"facts column {col['id']}: incorrect expression applicability")
    documented=set(facts['documented_object_ids'])
    for oid,obj in objects.items():
        key=obj.get('canonical_key') if obj['kind'] in ('cte','temp_table') else f"{obj.get('schema')}.{obj['name']}"
        table=catalogue['tables'].get(key)
        if table and (oid in documented or facts.get('page_contract')=='claims-v1'):
            if {c['name'] for c in facts['columns'] if c['object_id']==oid}!={c['name'] for c in table['columns']}:
                errors.append(f'facts columns differ from complete declared structure: {key}')
        if facts.get('page_contract')=='claims-v1':
            definitions=[d for d in facts['definitions'] if d['object_id']==oid]
            resolved=bool(table or oid in documented or obj['kind'] in ('cte','temp_table') or len(catalogue['functions'].get(key,[]))==1)
            if len(definitions)!=1 or definitions[0]['status']!=('resolved' if resolved else 'not_found'):
                errors.append(f'facts definition status differs from available SQL: {key}')
    return errors
