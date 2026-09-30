"""Conservative column type evidence from declarations, casts and ordered DDL."""
from pathlib import Path
from ddl import catalog, reconstruct
from sql_ast import (sql, type_name, relation, walk, require_parser, quote,
                     from_relations, star_targets, WILDCARD_NOTE,
                     SET_OUTPUT_NOTE, set_output_columns, EXTERNAL_FUNCTION_CONTRACTS)
from sql_gp import prepare as gp_prepare

POSITIONAL_INSERT_NOTE = ('Positional INSERT mapping is unresolved: select output width '
                          'differs from the established target width')


def infer_expression(expression,tables,variables=None,aliases=None):
    require_parser()
    from pglast import ast, parse_sql
    try: node=parse_sql('SELECT '+expression)[0].stmt.targetList[0].val
    except Exception: return None
    if isinstance(node,ast.TypeCast): return type_name(node.typeName)
    if isinstance(node,ast.FuncCall):
        name='.'.join(p.sval for p in node.funcname)
        if name in EXTERNAL_FUNCTION_CONTRACTS:
            contract=EXTERNAL_FUNCTION_CONTRACTS[name]
            return contract['return_type'] if len(node.args or ()) == contract['arity'] else None
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


def _identifier(name):
    from pglast import ast
    return sql(ast.ColumnRef(fields=(ast.String(sval=name),)))


def _column_ref(owner, name):
    return f'{owner}.{_identifier(name)}' if owner else _identifier(name)


def _source_owner(alias, relation_name):
    if relation_name.startswith('@'):
        relation_name = relation_name.rsplit(':', 1)[-1]
    return _identifier(alias) if alias else '.'.join(_identifier(part) for part in relation_name.split('.'))


def _star_prefix(expression):
    """Return the qualifier of `qual.*`, or '' for bare `*`."""
    return expression[:-2] if expression.endswith('.*') else ''


def _match_star_sources(prefix, sources):
    """Sources covered by a wildcard: all of them for `*`, one for `qual.*`."""
    if not prefix:
        return list(sources)
    matched = []
    for alias, rel in sources:
        # An alias hides the physical name. More than one matching relation
        # is ambiguous, so never choose the first match by iteration order.
        visible = rel.rsplit(':', 1)[-1] if rel.startswith('@') else rel
        candidates = ({_identifier(alias)} if alias else
                      {_source_owner(None, rel), _identifier(visible.rsplit('.', 1)[-1])})
        if prefix in candidates:
            matched.append((alias, rel))
    return matched if len(matched) == 1 else []


def _renamed_columns(columns, aliases=()):
    """Apply a positional alias prefix only to an established output width."""
    if not columns or len(aliases) > len(columns):
        return None
    result = []
    for index, column in enumerate(columns):
        expression = column.get('expression') or ''
        if expression == '*' or expression.endswith('.*'):
            return None
        name = aliases[index] if index < len(aliases) else column.get('name')
        if not name:
            return None
        result.append({'name': name, 'type': column.get('type')})
    return result


def expand_wildcard_outputs(inventory, tables):
    """Expand SELECT * / qual.* outputs against established table columns.

    `tables` maps relation names to {'columns': [{'name':..., 'type':...}, ...]}.
    Entries whose sources are not fully established stay wildcards. Coverage
    notes for the wildcard gap are recomputed: only unexpanded items keep them.
    CTE and derived-table output columns are added to `tables` as they are
    resolved so later items can expand through them.  Iterates until fixpoint
    so nested CTE/derived chains resolve in dependency order.
    """
    def anchor_key(anchor):
        return tuple(anchor.get(k) for k in ('object_or_scope', 'construct', 'ordinal'))

    by_anchor = {anchor_key(i['anchor']): i for i in inventory.get('items', [])}

    def _register_cte_columns():
        # Build CTE aliascolnames map from CTE items. When a CTE declares
        # `WITH c(a, b) AS (...)`, the visible output columns are `a, b`,
        # not the inner SELECT names — match PostgreSQL semantics.
        cte_aliases = {}
        for item in inventory.get('items', []):
            if item.get('kind') != 'CTE':
                continue
            ref = item.get('details', {}).get('reference')
            cols = item.get('details', {}).get('columns') or []
            if ref and cols and all(isinstance(c, str) for c in cols):
                cte_aliases[ref] = cols
        for item in inventory.get('items', []):
            details = item.get('details', {})
            result_for = details.get('result_for')
            if result_for and result_for.startswith(('@cte:', '@derived:')):
                cols = _renamed_columns(details.get('columns') or details.get('output_columns'),
                                        cte_aliases.get(result_for, ()))
                if cols:
                    tables[result_for] = {'columns': cols, 'source_ref': item['source_ref']}

    def _expand_pass():
        unresolved = []
        changed = False
        for item in inventory.get('items', []):
            details = item.get('details', {})
            still_wildcard = False
            for key in ('columns', 'output_columns'):
                outputs = details.get(key)
                if not outputs or not any(isinstance(o, dict) and
                                         (o.get('expression') == '*' or str(o.get('expression', '')).endswith('.*'))
                                         for o in outputs):
                    continue
                rebuilt = []
                for entry in outputs:
                    expression = entry.get('expression') if isinstance(entry, dict) else None
                    if not (expression == '*' or str(expression).endswith('.*')):
                        rebuilt.append(entry)
                        continue
                    if entry.get('set_operands'):
                        operands = [by_anchor.get(anchor_key(a), {}).get('details', {}).get('columns')
                                    for a in entry['set_operands']]
                        expanded = set_output_columns(*operands)
                        if expanded is None:
                            rebuilt.append(entry)
                            still_wildcard = True
                        else:
                            rebuilt.extend(expanded)
                            changed = True
                        continue
                    sources = entry.get('star_sources')
                    if not sources:
                        rebuilt.append(entry)
                        still_wildcard = True
                        continue
                    covered = _match_star_sources(_star_prefix(expression), [tuple(s) for s in sources])
                    expanded = []
                    ok = bool(covered)
                    for alias, rel in covered:
                        columns = (tables.get(rel) or {}).get('columns')
                        if not columns:
                            ok = False
                            break
                        owner = _source_owner(alias, rel)
                        for column in columns:
                            expanded.append(dict(name=column['name'],
                                                 expression=_column_ref(owner, column['name']),
                                                 expanded_from=expression))
                    if not ok:
                        rebuilt.append(entry)
                        still_wildcard = True
                    else:
                        rebuilt.extend(expanded)
                        changed = True
                details[key] = rebuilt
                if key == 'output_columns' and not still_wildcard:
                    for output, alias in zip(rebuilt, details.get('output_column_aliases', [])):
                        output['name'] = alias
            if still_wildcard:
                unresolved.append(item)
        return unresolved, changed

    # Fixpoint: expand, register CTE columns, repeat until stable.
    while True:
        _register_cte_columns()
        unresolved_items, changed = _expand_pass()
        # Each successful pass removes at least one wildcard. Counting items
        # misses progress when just one of several stars in an item resolves.
        if not changed:
            break
    if any(note.get('reason') in (WILDCARD_NOTE, SET_OUTPUT_NOTE) for note in inventory.get('coverage_notes', [])) \
            or unresolved_items:
        # Keep unsupported set nodes (e.g. VALUES operands) which have no
        # projection placeholder and therefore cannot be resolved by this pass.
        kept = [n for n in inventory.get('coverage_notes', []) if n.get('reason') != WILDCARD_NOTE
                and not (n.get('reason') == SET_OUTPUT_NOTE and any(
                    i['source_ref'] == n['source_ref'] and i['details'].get('set_operation')
                    for i in inventory.get('items', [])))]
        for item in unresolved_items:
            pending_set = any(isinstance(c, dict) and c.get('set_operands') for key in ('columns','output_columns')
                              for c in item['details'].get(key, []))
            note = dict(source_ref=item['source_ref'], reason=SET_OUTPUT_NOTE if pending_set else WILDCARD_NOTE)
            if note not in kept:
                kept.append(note)
        inventory['coverage_notes'] = kept
    return unresolved_items


def expand_star_outputs(select_node, tables):
    """Expand wildcards in a SELECT target list using established columns.

    Returns a flat [(name, expression)] list, or None when any wildcard source
    is not established. Target lists without wildcards are returned unchanged.
    CTE references resolve to the CTE output columns (not shadowed physical
    tables); derived tables resolve to their subquery output columns.
    """
    from pglast import ast

    def resolve(node, inherited_tables, inherited_ctes):
        if not isinstance(node, ast.SelectStmt):
            return None
        local_tables, env = dict(inherited_tables), dict(inherited_ctes)

        def register(ref, query, aliases=()):
            outputs = resolve(query, local_tables, env)
            columns = _renamed_columns(
                [dict(name=name, expression=expr) for name, expr in outputs] if outputs else None,
                aliases)
            # An unresolved local source must still hide a physical namesake.
            local_tables[ref] = {'columns': columns or []}

        if star_targets(node.targetList) or node.op:
            with_ = node.withClause
            if with_:
                if with_.recursive:
                    return None
                for cte in with_.ctes:
                    ref = f'@cte:{id(cte)}:{cte.ctename}'
                    register(ref, cte.ctequery, [n.sval for n in cte.aliascolnames or ()])
                    env[cte.ctename] = ref
            if node.op:
                operands = [resolve(child, local_tables, env) for child in (node.larg, node.rarg)]
                columns = [[dict(name=name, expression=expr) for name, expr in values]
                           if values is not None else None for values in operands]
                projected = set_output_columns(*columns)
                return [(c['name'], c['expression']) for c in projected] if projected is not None else None
            derived_refs = {}

            def derived(item):
                if isinstance(item, ast.JoinExpr):
                    derived(item.larg)
                    derived(item.rarg)
                elif isinstance(item, ast.RangeSubselect) and item.alias:
                    ref = f'@derived:{id(item)}:{item.alias.aliasname}'
                    register(ref, item.subquery, [n.sval for n in item.alias.colnames or ()])
                    derived_refs[id(item.subquery)] = ref

            for item in node.fromClause or ():
                derived(item)
            sources = from_relations(node, env=env, derived_refs=derived_refs)
            if sources is None:
                return None
        result = []
        for target in node.targetList or ():
            value = target.val
            if isinstance(value, ast.ColumnRef) and isinstance(value.fields[-1], ast.A_Star):
                prefix = '.'.join(_identifier(p.sval) for p in value.fields[:-1]
                                  if isinstance(p, ast.String))
                covered = _match_star_sources(prefix, sources)
                if not covered:
                    return None
                for alias, ref in covered:
                    columns = (local_tables.get(ref) or {}).get('columns')
                    if not columns:
                        return None
                    owner = _source_owner(alias, ref)
                    result.extend((column['name'], _column_ref(owner, column['name']))
                                  for column in columns)
            else:
                name = target.name
                if not name and isinstance(value, ast.ColumnRef) and isinstance(value.fields[-1], ast.String):
                    name = value.fields[-1].sval
                result.append((name, sql(value)))
        return result

    return resolve(select_node, tables, {})


def group_mappings(mappings):
    groups = {}
    for mapping in mappings:
        groups.setdefault((mapping['table'], mapping['name']), []).append(mapping)
    return groups


def mapping_variants(mappings):
    """Distinct SQL expressions/types; repeated occurrences are retained separately."""
    from validation_gate import _expression_key
    from identity import normalize_type
    return {(_expression_key(m['expression']) if m['expression'] is not None else None,
             normalize_type(m['type_expression']) if m['type_expression'] is not None else None)
            for m in mappings}


def column_catalog(inventory,sql_files,context_files,root,migration_manifest=None):
    """Returns known target declarations and positional INSERT/CTAS outputs.

    This pass reads SQL bytes independently of facts and Markdown. It deliberately
    leaves arithmetic/operator/function type resolution to a DB-aware reviewer.
    """
    root=Path(root).resolve()
    dialect=inventory.get('dialect',{}).get('name','postgres')
    files=list(dict.fromkeys(Path(p).resolve() for p in [*sql_files,*context_files]))
    migration=reconstruct(migration_manifest,project_root=root) if migration_manifest else None
    ordered={(root/r['path']).resolve() for r in migration['inputs']} if migration else set()
    context=catalog([p for p in files if p not in ordered],root,dialect=dialect)
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
            tables[details.get('reference',item.get('writes',[''])[0])]=dict(columns=details['columns'],source_ref=item['source_ref'], **{k:details[k] for k in ('distributed','storage_parameters','gp_extension_version') if k in details})
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
        if isinstance(node,ast.InsertStmt) and isinstance(node.selectStmt,ast.SelectStmt):
            target=relation(node.relation)
            if not node.relation.schemaname:
                target=next((k for k in tables if k.startswith('@temp:'+scope+':') and k.endswith(':'+target)),target)
            # An omitted target list uses the established DDL order, including
            # DROP/ADD changes in the supplied migration manifest.
            column_names = ([col.name for col in node.cols] if node.cols else
                            [col['name'] for col in tables.get(target, {}).get('columns', [])])
            # VALUES is a sequence of explicit row expressions, not an empty
            # SELECT target list. Keep every row's variant for the column gate.
            outputs = ([[(None, sql(value)) for value in row]
                        for row in node.selectStmt.valuesLists]
                       if node.selectStmt.valuesLists else
                       [expand_star_outputs(node.selectStmt, tables)])
            # Positional mapping is proven only when the select output width
            # equals the target width (explicit column list or established DDL
            # order). A known mismatch is a source finding, not an analysis
            # gap: the analysis is complete (the statement cannot map), the
            # defect belongs to the SQL versus the established DDL state. It
            # is listed on the page and never zip-truncated into a plausible
            # mapping.
            matching = all(expanded is not None and len(expanded) == len(column_names)
                           for expanded in outputs)
            if column_names and matching:
                for expanded in outputs:
                    for name,(_source_name,value) in zip(column_names,expanded):
                        mappings.append(dict(table=target,name=name,expression=value,source_ref=ref))
            mismatched = next((expanded for expanded in outputs
                               if expanded and column_names and len(expanded) != len(column_names)), None)
            if mismatched is not None:
                query = sql(node)
                owner = next((item for item in inventory.get('items', [])
                              if item.get('kind') == 'INSERT'
                              and item.get('details', {}).get('query') == query), None)
                finding = dict(kind='positional_insert',
                               source_ref=owner['source_ref'] if owner else ref,
                               source_schema=target.rsplit('.', 1)[0] if '.' in target else None,
                               target=target, operation='INSERT', status='unmapped',
                               reason=(POSITIONAL_INSERT_NOTE if not node.selectStmt.valuesLists else
                                       'Positional INSERT mapping is unresolved: VALUES output width differs from target width'),
                               target_width=len(column_names), select_width=len(mismatched))
                findings = inventory.setdefault('source_findings', [])
                if finding not in findings:
                    findings.append(finding)
        for mapping in mappings[before:]:
            mapping.update(query=sql(node), kind=type(node).__name__.removesuffix('Stmt').upper())
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
        parse_text,_,_=gp_prepare(text, dialect)
        ref=next(r for r in inventory['inputs'] if r['path']==Path(path).resolve().relative_to(root).as_posix())
        ref={**ref,'start_line':1,'end_line':len(text.splitlines())}
        for raw in parse_sql(parse_text):
            node=raw.stmt
            if isinstance(node,ast.CreateFunctionStmt):
                parts=[p.sval for p in node.funcname]
                d=declaration['details']
                if parts[-1]!=d['name'] or (len(parts)>1 and parts[-2]!=d['schema']): continue
                options={o.defname:o.arg for o in node.options or ()}
                if getattr(options.get('language'),'sval','')=='plpgsql':
                    pl(parse_plpgsql(sql(node)),ref)
            else: inspect(node,ref)
    # Resolve recorded SELECT * / qual.* outputs against the established DDL
    # state before derived targets (view/CTAS columns) consume them.
    expand_wildcard_outputs(inventory, tables)
    d=declaration['details']
    if d['object_kind'] in ('view','materialized_view','ctas'):
        target=f"{d['schema']}.{d['name']}"
        aliases={}
        for path in sql_files:
            parse_text,_,_=gp_prepare(Path(path).read_text(encoding='utf-8-sig'), dialect)
            for raw in parse_sql(parse_text):
                n=raw.stmt
                selected=(isinstance(n,ast.ViewStmt) and relation(n.view)==target) or (isinstance(n,ast.CreateTableAsStmt) and relation(n.into.rel)==target)
                if selected: aliases={r.alias.aliasname if r.alias else r.relname:relation(r) for r in walk(n.query) if isinstance(r,ast.RangeVar)}
        for out in d.get('output_columns',[]):
            if out['name'] is None and out['expression'] is None:
                note = dict(source_ref=declaration['source_ref'],
                            reason='Set operation output names require explicit aliases')
                if note not in inventory['coverage_notes']:
                    inventory['coverage_notes'].append(note)
                continue
            name=out['name'] or out['expression']
            mappings.append(dict(table=target,name=name,expression=out['expression'],source_ref=declaration['source_ref'],aliases=aliases))
        attributes = {k:i['details'][k] for i in inventory['items'] if i['kind']=='CTAS' and i.get('details',{}).get('reference')==target for k in ('distributed','storage_parameters','gp_extension_version') if k in i['details']}
        tables[target]=dict(**attributes, columns=[dict(name=m['name'],type=infer_expression(m['expression'],tables,aliases=aliases),default=None,not_null=False)
                                    for m in mappings if m['table']==target],source_ref=declaration['source_ref'],derived=True)
    variables={p['name']:p['type'] for p in d.get('parameters',[]) if p.get('name')}
    for mapping in mappings:
        mapping['type_expression']=infer_expression(mapping['expression'],tables,variables,mapping.get('aliases'))
    # Independent type limitations are plan inputs, not inferred from writer facts.
    used = {r for item in inventory['items'] for field in ('reads', 'writes', 'calls')
            for r in item.get(field, [])}
    used.add(f"{d['schema']}.{d['name']}")
    if d['object_kind'] == 'migration':
        used.update(tables)
    unknowns = {(m['table'], m['name']) for m in mappings
                if m['table'] in used and m['type_expression'] is None}
    # A column-wide null summary is an independently derived limitation. It
    # must not remove an obligation just because all individual types are known.
    unknowns.update(key for key, group in group_mappings(mappings).items()
                    if key[0] in used and len(mapping_variants(group)) > 1)
    unknowns.update((table, column['name']) for table in used
                    for column in tables.get(table, {}).get('columns', [])
                    if column.get('type') is None)
    unknowns.update((m['table'], m['name']) for m in mappings
                    if m['table'] in used and m['table'] not in tables)
    if unknowns:
        d['type_unknowns'] = [dict(table=table, name=name) for table, name in sorted(unknowns)]
    else:
        d.pop('type_unknowns', None)
    return dict(tables=tables,mappings=mappings,functions=context['functions'])


def check_types(facts,catalogue):
    """Reject invented target types, swapped expression types and dropped DDL columns."""
    from functools import lru_cache
    from validation_gate import _expression_key
    expression_key = lru_cache(maxsize=None)(_expression_key)
    errors=[]
    objects={o['id']:o for o in facts['objects']}
    groups=group_mappings(catalogue['mappings'])
    # A wide INSERT uses the same full SQL for every target column. Normalize
    # each distinct query once, retaining kind and target identity in the key.
    operation_queries = {(op['kind'], oid, expression_key(op.get('structure',{}).get('query','')))
                         for op in facts.get('operations',[]) for oid in op.get('writes',[])}
    for col in facts['columns']:
        obj=objects[col['object_id']]
        key=obj.get('canonical_key') if obj['kind'] in ('cte','temp_table') else f"{obj.get('schema')}.{obj['name']}"
        table=catalogue['tables'].get(key)
        expected=next((c for c in table['columns'] if c['name']==col['name']),None) if table else None
        from identity import normalize_type
        same=lambda a,b: a==b if a is None or b is None else normalize_type(a)==normalize_type(b)
        if not same(col['type_target'],expected['type'] if expected else None):
            errors.append(f"facts column {col['id']}: target type is not established by SQL/ordered DDL")
        mappings=groups.get((key,col['name']),[])
        if len(mapping_variants(mappings)) > 1:
            if ('expression' not in col or col['expression'] is not None
                    or col['type_expression'] is not None or col.get('expression_status')!='unknown'):
                errors.append(f"facts column {col['id']}: multiple SQL mappings require a null expression/type summary with unknown status")
            # Null describes only the aggregate. The exact statements must remain
            # visible as operation claims, independently checked by the SQL gate.
            for mapping in mappings:
                if not mapping.get('query') or (
                        mapping.get('kind'), col['object_id'], expression_key(mapping['query'])) not in operation_queries:
                    errors.append(f"facts column {col['id']}: SQL mapping variant lacks its operation query")
        else:
            for mapping in mappings:
                actual,expected_expression=col.get('expression'),mapping['expression']
                matches=(actual==expected_expression if actual is None or expected_expression is None
                         else expression_key(actual)==expression_key(expected_expression))
                if not matches:
                    errors.append(f"facts column {col['id']}: expression differs from SQL mapping")
                if not same(col['type_expression'],mapping['type_expression']):
                    errors.append(f"facts column {col['id']}: expression type differs from independently supported inference")
        if expected:
            for field,target in (('default','default'),('description','comment'),('primary_key','primary_key')):
                if field in col and col[field]!=expected.get(target): errors.append(f"facts column {col['id']}: {field} differs from DDL")
        if 'expression_status' in col:
            mapped=bool(mappings)
            status=('known' if col['type_expression'] is not None else 'unknown') if mapped else 'not_applicable'
            if col['expression_status']!=status: errors.append(f"facts column {col['id']}: incorrect expression applicability")
    documented=set(facts['documented_object_ids'])
    for oid,obj in objects.items():
        key=obj.get('canonical_key') if obj['kind'] in ('cte','temp_table') else f"{obj.get('schema')}.{obj['name']}"
        table=catalogue['tables'].get(key)
        if table and (oid in documented or facts.get('page_contract')=='claims-v1'):
            if {c['name'] for c in facts['columns'] if c['object_id']==oid}!={c['name'] for c in table['columns']}:
                errors.append(f'facts columns differ from complete declared structure: {key}')
        definitions=[d for d in facts['definitions'] if d['object_id']==oid]
        for attribute in ('distributed', 'storage_parameters', 'gp_extension_version'):
            expected = (table or {}).get(attribute)
            if expected is not None or any(attribute in d for d in definitions):
                if len(definitions) != 1 or definitions[0].get(attribute) != expected:
                    errors.append(f'facts definition {attribute} differs from available SQL: {key}')
        if facts.get('page_contract')=='claims-v1':
            definitions=[d for d in facts['definitions'] if d['object_id']==oid]
            resolved=bool(table or oid in documented or obj['kind'] in ('cte','temp_table') or len(catalogue['functions'].get(key,[]))==1)
            if len(definitions)!=1 or definitions[0]['status']!=('resolved' if resolved else 'not_found'):
                errors.append(f'facts definition status differs from available SQL: {key}')
    return errors
