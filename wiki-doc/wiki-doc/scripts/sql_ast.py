"""Independent PostgreSQL syntax inventory using libpg_query through pglast.

Parses SQL and PL/pgSQL without a database. Unhandled AST nodes remain explicit gaps.
Only raw SQL is accepted; facts, pages and expectations are never inputs.
"""
import hashlib
import re
from pathlib import PurePosixPath

from artifact_schema import ArtifactInputError
from identity import ObjectDescriptor, canonical_key, _parse_arg_types
from sql_syntax import mask_sql

try:
    from pglast import ast, parse_sql, parse_plpgsql
    from pglast.stream import RawStream
except ImportError:
    ast = None


def require_parser():
    if ast is None:
        raise ArtifactInputError('Install pglast==7.14 from requirements.txt for PostgreSQL analysis')


def children(node):
    if isinstance(node, ast.Node):
        for key in node:
            value = getattr(node, key)
            if isinstance(value, ast.Node):
                yield value
            elif isinstance(value, tuple):
                yield from (v for v in value if isinstance(v, ast.Node))


def walk(node):
    if isinstance(node, ast.Node):
        yield node
        for child in children(node):
            yield from walk(child)


def sql(node):
    return RawStream()(node) if node is not None else ''


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def names(values):
    return [v.sval for v in values or ()]


def relation(node):
    return (node.schemaname + '.' if node.schemaname else '') + node.relname


def type_name(node):
    return sql(node) if node else None


def source_statement(raw, text):
    data = text.encode('utf-8')
    start = raw.stmt_location
    end = start + raw.stmt_len if raw.stmt_len else len(data)
    snippet = data[start:end].decode('utf-8')
    masked = mask_sql(snippet)[0]
    leading = len(masked) - len(masked.lstrip())
    return snippet[leading:], data[:start].decode('utf-8').count('\n') + 1 + snippet[:leading].count('\n')


BUILTINS = frozenset('sum avg count min max now coalesce nullif greatest least round abs lower upper trim substring extract date_trunc to_char to_date row_number rank dense_rank lag lead format concat concat_ws length current_date timezone generate_series'.split())
QUERY_TYPES = ('SelectStmt', 'InsertStmt', 'UpdateStmt', 'DeleteStmt', 'MergeStmt')


def columns_of(node):
    result = []
    for column in node.tableElts or ():
        if isinstance(column, ast.ColumnDef):
            constraints = list(column.constraints or ())
            default = next((sql(c.raw_expr) for c in constraints if c.contype.name == 'CONSTR_DEFAULT'), None)
            result.append(dict(name=column.colname, type=type_name(column.typeName),
                               default=default, not_null=any(c.contype.name in ('CONSTR_NOTNULL', 'CONSTR_PRIMARY') for c in constraints),
                               primary_key=any(c.contype.name=='CONSTR_PRIMARY' for c in constraints)))
    for constraint in node.tableElts or ():
        if isinstance(constraint,ast.Constraint) and constraint.contype.name=='CONSTR_PRIMARY':
            keys={k.sval for k in constraint.keys or ()}
            for column in result:
                if column['name'] in keys: column.update(primary_key=True,not_null=True)
    return result


def constraint_details(c, column=None):
    """Versioned constraint facts, using parser enum names rather than numeric ordinals."""
    kind = c.contype.name.removeprefix('CONSTR_')
    kind = {'PRIMARY': 'PRIMARY_KEY', 'FOREIGN': 'FOREIGN_KEY',
            'NOTNULL': 'NOT_NULL'}.get(kind, kind)
    result = dict(name=c.conname, type=kind, ddl=sql(c))
    keys = c.fk_attrs or c.keys
    if keys or column:
        result['columns'] = [k.sval for k in keys] if keys else [column]
    if c.pktable:
        result['referenced_table'] = relation(c.pktable)
        result['referenced_columns'] = [k.sval for k in c.pk_attrs or ()]
    if c.raw_expr:
        result['expression'] = sql(c.raw_expr)
    return result


def table_constraints(node):
    result = []
    for entry in node.tableElts or ():
        if isinstance(entry, ast.Constraint):
            result.append(constraint_details(entry))
        elif isinstance(entry, ast.ColumnDef):
            result.extend(constraint_details(c, entry.colname) for c in entry.constraints or ())
    return result


class Analyzer:
    def __init__(self, text, path, sha, version):
        require_parser()
        self.text, self.path, self.sha, self.version = text, path, sha, version
        self.items, self.notes, self.counts, self.temps = [], [], {}, {}
        self.scope = path
        self.query_number = 0
        self.cte_nodes = {}

    def cte_reference(self, node):
        if id(node) not in self.cte_nodes:
            self.cte_nodes[id(node)] = (node, f'@cte:{self.scope}:{len(self.cte_nodes) + 1}:{node.ctename}')
        return self.cte_nodes[id(node)][1]

    def ref(self, line, end=None):
        last=max(1,len(self.text.splitlines()))
        return dict(path=self.path, sha256=self.sha, start_line=min(last,max(1, line)), end_line=min(last,max(1, end or line)))

    def note(self, line, reason):
        value = dict(source_ref=self.ref(line), reason=reason)
        if value not in self.notes:
            self.notes.append(value)

    def item(self, kind, line, end=None, **details):
        key = (self.scope, kind)
        self.counts[key] = self.counts.get(key, 0) + 1
        value = dict(anchor=dict(object_or_scope=self.scope, construct=kind, ordinal=self.counts[key]),
                     kind=kind, source_ref=self.ref(line, end), details=details)
        self.items.append(value)
        return value

    def dependencies(self, node, env, line, exclude=()):
        reads, calls = [], []
        def visit(n, local):
            if not isinstance(n, ast.Node) or id(n) in exclude:
                return
            with_ = getattr(n, 'withClause', None)
            if with_:
                local = {**local, **{c.ctename: self.cte_reference(c) for c in with_.ctes}}
            if isinstance(n, ast.RangeVar):
                if '.' in (n.schemaname or '') or '.' in n.relname:
                    self.note(line,'Dependency identifiers containing dots require component-aware lineage')
                name = relation(n)
                if not n.schemaname:
                    name = local.get(name, self.temps.get(name, name))
                reads.append(name)
                if '.' not in name and not name.startswith('@'):
                    self.note(line, f'Unresolved reads target/search_path: {name}')
            if isinstance(n, ast.FuncCall):
                parts = names(n.funcname)
                if len(parts) > 1:
                    calls.append('.'.join(parts))
                elif parts and parts[0] not in BUILTINS:
                    self.note(line, f'Unresolved unqualified call: {parts[0]}')
            for child in children(n):
                if child is not with_:
                    visit(child, local)
        visit(node, env)
        return list(dict.fromkeys(reads)), list(dict.fromkeys(calls))

    def analyze_query(self, node, raw, line, forced_kind=None, env=None, branch=None):
        env = dict(env or {})
        self.query_number += 1
        statement_no = self.query_number
        with_ = getattr(node, 'withClause', None)
        if with_:
            if with_.recursive:
                self.note(line, 'Recursive CTE requires recursive lineage analysis')
            for cte in with_.ctes:
                local = self.cte_reference(cte)
                self.item('CTE', line, name=cte.ctename, reference=local, physical=False,
                          lifetime='statement', columns=[n.sval for n in cte.aliascolnames or ()],query=sql(cte.ctequery))
                result=self.analyze_query(cte.ctequery, sql(cte.ctequery), line, env=env, branch=branch)
                if result: result['details']['result_for']=local
                env[cte.ctename] = local
        cls = type(node).__name__
        kind = forced_kind or cls.removesuffix('Stmt').upper()
        if kind not in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'PERFORM', 'CALL', 'RETURN', 'ASSIGN', 'IF'):
            self.note(line, f'Unsupported query AST: {cls}')
            return
        if kind == 'MERGE' and (not self.version.split('.')[0].isdigit() or int(self.version.split('.')[0]) < 15):
            self.note(line, 'MERGE requires a confirmed PostgreSQL version >= 15')
        target = getattr(node, 'relation', None)
        reads, calls = self.dependencies(node, env, line, exclude=(id(target),) if target else ())
        formulas, conditions, outputs = [], [], []
        def direct_walk(n):
            yield n
            for child in children(n):
                if child is with_ or type(child).__name__ in QUERY_TYPES:
                    continue
                yield from direct_walk(child)
        for n in direct_walk(node):
            if isinstance(n, ast.ResTarget) and n.val is not None:
                expression = sql(n.val)
                if cls == 'SelectStmt' and n in (node.targetList or ()):
                    name = n.name
                    if not name and isinstance(n.val, ast.ColumnRef) and isinstance(n.val.fields[-1], ast.String):
                        name = n.val.fields[-1].sval
                    outputs.append(dict(name=name, expression=expression))
                if not isinstance(n.val, (ast.ColumnRef, ast.A_Const)) or kind in ('UPDATE', 'ASSIGN'):
                    formulas.append(expression)
            for field in ('whereClause', 'havingClause', 'quals', 'joinCondition', 'condition'):
                value = getattr(n, field, None)
                if isinstance(value, ast.Node):
                    conditions.append(sql(value))
        details = dict(formulas=list(dict.fromkeys(formulas)), conditions=list(dict.fromkeys(conditions)),
                       has_formula=bool(formulas), has_condition=bool(conditions), analysis='postgres_ast',
                       has_date_boundary=bool(re.search(r'\b(?:DATE|TIMESTAMP|INTERVAL)\b', raw, re.I)))
        if outputs:
            details['columns'] = outputs
        if branch:
            details['branch'] = branch
        if getattr(node,'groupClause',None): details['group_by']=[sql(n) for n in node.groupClause]
        if isinstance(node,ast.UpdateStmt): details['assignments']=[dict(target=t.name,expression=sql(t.val)) for t in node.targetList or ()]
        if isinstance(node,ast.InsertStmt) and node.cols: details['target_columns']=[t.name for t in node.cols]
        if kind=='RETURN': details['return_expression']=raw
        if isinstance(node, ast.MergeStmt):
            details['branches'] = [dict(match=c.matchKind.name, action=c.commandType.name,
                                        condition=sql(c.condition), assignments=[(quote(t.name)+' = '+sql(t.val)) if t.val is not None else quote(t.name) for t in c.targetList or ()],
                                        values=[sql(v) for v in c.values or ()]) for c in node.mergeWhenClauses or ()]
        item = self.item(kind, line, line + raw.count('\n'), **details)
        if reads: item['reads'] = reads
        if calls: item['calls'] = calls
        if target:
            if '.' in (target.schemaname or '') or '.' in target.relname:
                self.note(line,'Target identifiers containing dots require component-aware lineage')
            name = relation(target)
            name = self.temps.get(name, name) if not target.schemaname else name
            item['writes'] = [name]
            if '.' not in name and not name.startswith('@'):
                self.note(line, f'Unresolved writes target/search_path: {name}')
        def subqueries(n):
            for child in children(n):
                if child is with_:
                    continue
                if type(child).__name__ in QUERY_TYPES:
                    yield child
                else:
                    yield from subqueries(child)
        for child in subqueries(node):
            if isinstance(child, ast.SelectStmt) and child.valuesLists and not child.targetList:
                continue  # VALUES is not a SELECT occurrence in source SQL.
            self.analyze_query(child, sql(child), line, env=env, branch=branch)
        return item

    def statement(self, node, raw, line, forced_kind=None, branch=None):
        cls = type(node).__name__
        if cls in QUERY_TYPES:
            return self.analyze_query(node, raw, line, forced_kind, branch=branch)
        elif isinstance(node, ast.CreateStmt):
            name = relation(node.relation)
            temporary = node.relation.relpersistence == 't'
            if temporary:
                ref = f'@temp:{self.scope}:{name}'
                self.temps[name] = ref
            else:
                ref = name
            constraints = table_constraints(node)
            extension = dict(extension_version=1, constraints=constraints) if constraints else {}
            self.item('CREATE', line, line + raw.count('\n'), table_name=name, reference=ref,
                      physical=True, temporary=temporary, lifetime=node.oncommit.name,
                      columns=columns_of(node), ddl=sql(node), **extension)['writes'] = [ref]
        elif isinstance(node, ast.CreateTableAsStmt):
            name = relation(node.into.rel)
            temporary=node.into.rel.relpersistence=='t'
            ref=f'@temp:{self.scope}:{name}' if temporary else name
            if temporary: self.temps[name]=ref
            self.item('CTAS', line, line + raw.count('\n'), ddl=sql(node),temporary=temporary,reference=ref,lifetime=node.into.onCommit.name)['writes'] = [ref]
            result=self.analyze_query(node.query, raw, line, branch=branch)
            if result: result['details']['result_for']=ref
            return result
        elif isinstance(node, (ast.AlterTableStmt, ast.RenameStmt, ast.DropStmt, ast.CommentStmt, ast.TruncateStmt)):
            kind = {ast.AlterTableStmt:'ALTER',ast.RenameStmt:'ALTER',ast.DropStmt:'DROP',ast.CommentStmt:'COMMENT',ast.TruncateStmt:'TRUNCATE'}[type(node)]
            details = dict(ddl=sql(node), analysis='postgres_ast')
            # Extract constraint details from ALTER TABLE ADD CONSTRAINT
            if isinstance(node, ast.AlterTableStmt):
                constraints = []
                for cmd in node.cmds or ():
                    if isinstance(cmd, ast.AlterTableCmd) and cmd.def_ and isinstance(cmd.def_, ast.Constraint):
                        constraints.append(constraint_details(cmd.def_))
                        if cmd.def_.indexname:
                            self.note(line, 'Constraint USING INDEX requires index-column resolution')
                if constraints:
                    details['constraints'] = constraints
                    details['extension_version'] = 1
            item = self.item(kind, line, line + raw.count('\n'), **details)
            if getattr(node, 'relation', None):
                item['writes'] = [relation(node.relation)]
            elif isinstance(node, ast.TruncateStmt):
                item['writes'] = [relation(r) for r in node.relations]
        elif isinstance(node, ast.CallStmt):
            expression = node.funccall
            item = self.item('CALL', line, line + raw.count('\n'), formulas=[], conditions=[], analysis='postgres_ast')
            item['calls'] = ['.'.join(names(expression.funcname))]
        elif isinstance(node, ast.CreateTrigStmt):
            timing_map = {2: 'BEFORE', 0: 'AFTER', 64: 'INSTEAD OF'}
            event_mask = {4: 'INSERT', 16: 'UPDATE', 8: 'DELETE', 32: 'TRUNCATE'}
            timing = timing_map.get(node.timing, f'UNKNOWN({node.timing})')
            events = [name for mask, name in event_mask.items() if node.events & mask]
            target = relation(node.relation)
            func = '.'.join(names(node.funcname))
            item = self.item('TRIGGER', line, line + raw.count('\n'),
                           extension_version=1, trigger_name=node.trigname, table=target,
                           timing=timing, events=events,
                           for_each_row=node.row, function=func,
                           is_constraint=node.isconstraint,
                           when=sql(node.whenClause) if node.whenClause else None,
                           update_columns=[c.sval for c in node.columns or ()],
                           arguments=[a.sval for a in node.args or ()],
                           ddl=sql(node), analysis='postgres_ast')
            item['calls'] = [func] if func else []
            item['writes'] = [target]
        elif isinstance(node, ast.IndexStmt):
            target = relation(node.relation)
            params = []
            for p in node.indexParams or ():
                if p.name:
                    params.append(p.name)
                elif p.expr:
                    params.append(sql(p.expr))
            item = self.item('INDEX', line, line + raw.count('\n'),
                           extension_version=1, index_name=node.idxname, table=target,
                           unique=node.unique, primary=node.primary,
                           access_method=node.accessMethod or 'btree',
                           columns=params,
                           where=sql(node.whereClause) if node.whereClause else None,
                           ddl=sql(node), analysis='postgres_ast')
            item['writes'] = [target]
        elif isinstance(node, ast.GrantStmt):
            action = 'GRANT' if node.is_grant else 'REVOKE'
            privs = [p.priv_name or 'ALL' for p in node.privileges or ()] or ['ALL']
            objtype = node.objtype.name.removeprefix('OBJECT_')
            grantees = [g.rolename or sql(g) for g in node.grantees or ()]
            targets = [relation(o) for o in node.objects or () if isinstance(o, ast.RangeVar)]
            if (node.targtype.name != 'ACL_TARGET_OBJECT' or objtype not in ('TABLE', 'SEQUENCE')
                    or len(targets) != len(node.objects or ())):
                self.note(line, 'Unsupported GRANT/REVOKE target; only explicit relations are supported')
            item = self.item(action, line, line + raw.count('\n'),
                           extension_version=1, privileges=privs, object_type=objtype,
                           privilege_columns=[dict(privilege=p.priv_name or 'ALL', columns=[c.sval for c in p.cols or ()])
                                              for p in node.privileges or ()],
                           grantees=grantees, targets=targets,
                           grant_option=node.grant_option,
                           ddl=sql(node), analysis='postgres_ast')
            if targets:
                item['writes'] = targets
        elif isinstance(node, ast.CreateSchemaStmt):
            # Namespace setup is context, with no data operation to invent.
            return
        else:
            self.note(line, f'Unsupported executable AST node: {cls}')

    def plpgsql(self, data, body_line, branch=None):
        kind, value = next(iter(data.items()))
        line = body_line + value.get('lineno', 1) - 1
        expr = lambda field: value.get(field, {}).get('PLpgSQL_expr', {}).get('query', '')
        if kind == 'PLpgSQL_stmt_block':
            if value.get('exceptions'):
                self.note(line, 'PL/pgSQL exception handlers require control-flow analysis')
            for entry in value.get('body', []):
                self.plpgsql(entry, body_line, branch)
        elif kind in ('PLpgSQL_stmt_execsql', 'PLpgSQL_stmt_perform', 'PLpgSQL_stmt_call'):
            query = expr('sqlstmt' if kind.endswith('execsql') else 'expr')
            for raw in parse_sql(query):
                item=self.statement(raw.stmt, query, line, 'PERFORM' if kind.endswith('perform') else None, branch)
                if item and value.get('into'):
                    target=value.get('target',{})
                    row=target.get('PLpgSQL_row',{})
                    destinations=[f['name'] for f in row.get('fields',[])] or [target.get('PLpgSQL_var',{}).get('refname')]
                    if not all(destinations): self.note(line,'Unresolved PL/pgSQL INTO target')
                    else:
                        item['details']['into']=destinations
                        item['details']['assignments']=[dict(target=name,expression=sql(t.val)) for name,t in zip(destinations,getattr(raw.stmt,'targetList',()) or ())]
        elif kind == 'PLpgSQL_stmt_if':
            condition = expr('cond')
            self.item('IF', line, conditions=[condition], formulas=[], has_condition=True,
                      branches=['then', 'else'], analysis='postgres_ast')
            for label in ('then_body', 'else_body'):
                for entry in value.get(label, []):
                    self.plpgsql(entry, body_line, (branch + '/' if branch else '') + label)
            for index, entry in enumerate(value.get('elsif_list', [])):
                other = entry.get('PLpgSQL_if_elsif', entry)
                self.item('IF', body_line + other.get('lineno', 1) - 1,
                          conditions=[other['cond']['PLpgSQL_expr']['query']], has_condition=True)
                for nested in other.get('stmts', []):
                    self.plpgsql(nested, body_line, f'elsif:{index}')
        elif kind in ('PLpgSQL_stmt_assign', 'PLpgSQL_stmt_return'):
            query = expr('expr')
            if not query and not value.get('lineno'):
                return  # Implicit end-of-function return, not a source occurrence.
            expression = query.split(':=', 1)[-1].strip()
            if expression:
                parsed = parse_sql('SELECT ' + expression)[0].stmt
                item=self.analyze_query(parsed, query, line, 'ASSIGN' if kind.endswith('assign') else 'RETURN', branch=branch)
                if kind.endswith('assign'):
                    item['details']['assignment_target']=query.split(':=',1)[0].strip()
                    item['details']['assignments']=[dict(target=item['details']['assignment_target'],expression=expression)]
            else:
                self.item('RETURN', line, formulas=[], conditions=[])
        elif kind == 'PLpgSQL_stmt_dynexecute':
            query = expr('query')
            parsed = parse_sql('SELECT ' + query)[0].stmt.targetList[0].val
            template, args = None, []
            if isinstance(parsed, ast.A_Const) and isinstance(parsed.val, ast.String):
                template = parsed.val.sval
            elif isinstance(parsed, ast.FuncCall) and names(parsed.funcname) == ['format'] and parsed.args:
                first = parsed.args[0]
                if isinstance(first, ast.A_Const) and isinstance(first.val, ast.String):
                    template, args = first.val.sval, [sql(a) for a in parsed.args[1:]]
            details = dict(dynamic=True, unresolved_parts=['runtime identifiers or parameter values'],
                           template=template, arguments=args, analysis='postgres_ast')
            item = self.item('EXECUTE', line, line + query.count('\n'), **details)
            if not template:
                self.note(line, 'Unanalyzed dynamic SQL expression')
                return
            counter = [0]
            def placeholder(match):
                counter[0] += 1
                return f'__dynamic_{counter[0]}' if match[0].endswith('I') else "'__dynamic_value'"
            if re.search(r'%(?!%|(?:[1-9]\d*\$)?[IL])', template):
                self.note(line, 'Unsupported dynamic format placeholder')
                return
            command = re.sub(r'%(?:[1-9]\d*\$)?[IL]', placeholder, template).replace('%%', '%')
            statements = parse_sql(command)
            if len(statements) != 1 or type(statements[0].stmt).__name__ not in QUERY_TYPES + ('TruncateStmt',):
                self.note(line, 'Unanalyzed dynamic SQL template')
                return
            node = statements[0].stmt
            item['details']['command_kind'] = type(node).__name__.removesuffix('Stmt').upper()
            reads, calls = self.dependencies(node, {}, line, exclude=(id(getattr(node,'relation',None)),))
            item['reads'] = [r for r in reads if '__dynamic_' not in r]
            item['calls'] = calls
        elif kind == 'PLpgSQL_stmt_null':
            return
        else:
            self.note(line, f'Unsupported PL/pgSQL AST node: {kind}')


def analyze(text, path, sha, dialect='postgres', version='unknown', documented_subjects=None):
    require_parser()
    engine = Analyzer(text, path, sha, version)
    raw_stmts = parse_sql(text)
    encoded = text.encode('utf-8')
    objects = []
    for raw in raw_stmts:
        node = raw.stmt
        start = raw.stmt_location
        stop = start + raw.stmt_len if raw.stmt_len else len(encoded)
        snippet = encoded[start:stop].decode('utf-8')
        line = encoded[:start].decode('utf-8').count('\n') + 1
        # libpg_query includes whitespace/comments before the statement in RawStmt.
        masked = mask_sql(snippet)[0]
        leading = len(masked) - len(masked.lstrip())
        line += snippet[:leading].count('\n')
        snippet = snippet[leading:]
        kind, schema, name, args, signature = None, None, None, None, None
        details = {}
        if isinstance(node, ast.CreateFunctionStmt):
            kind = 'procedure' if node.is_procedure else 'function'
            parts = names(node.funcname)
            schema, name = (parts[-2] if len(parts) > 1 else None), parts[-1]
            parameters = [dict(name=p.name, type=type_name(p.argType), mode=p.mode.value,
                               default=sql(p.defexpr) if p.defexpr else None) for p in node.parameters or ()]
            args = [p['type'] for p in parameters if p['mode'] not in ('o','t')]
            signature = name + '(' + ', '.join(args) + ')'
            details.update(parameters=parameters, returns=type_name(node.returnType), returns_table=any(p['mode']=='t' for p in parameters))
            options={o.defname:o.arg for o in node.options or ()}
            details['volatility']=getattr(options.get('volatility'),'sval','volatile')
        elif isinstance(node, (ast.ViewStmt, ast.CreateStmt, ast.CreateTableAsStmt)):
            target = node.view if isinstance(node, ast.ViewStmt) else (node.into.rel if isinstance(node,ast.CreateTableAsStmt) else node.relation)
            schema, name = target.schemaname, target.relname
            kind = 'view' if isinstance(node,ast.ViewStmt) else ('materialized_view' if isinstance(node,ast.CreateTableAsStmt) and node.objtype.name=='OBJECT_MATVIEW' else ('ctas' if isinstance(node,ast.CreateTableAsStmt) else 'table'))
            if isinstance(node, ast.CreateStmt): details['columns'] = columns_of(node)
        if kind:
            descriptor = ObjectDescriptor(kind, quote(schema) if schema else None, quote(name), args)
            try:
                key = canonical_key(descriptor)
            except ValueError:
                key = f'unresolved+{kind}+{schema or "?"}+{name}@{line}'
            details.update(object_kind=kind, name=name, schema=schema, input_types=list(_parse_arg_types(args)) if args is not None else None,
                           signature=signature, canonical_key=key, analysis='postgres_ast')
            objects.append((raw, snippet, line, details))
    if not objects and any(isinstance(r.stmt, (ast.AlterTableStmt,ast.RenameStmt,ast.DropStmt)) for r in raw_stmts):
        key = canonical_key(ObjectDescriptor('migration', migration_path=path))
        details = dict(object_kind='migration', name=PurePosixPath(path).name, schema=None,
                       input_types=None, signature=None, canonical_key=key, analysis='postgres_ast')
        objects = [(None, text, 1, details)]
    selected = objects
    if documented_subjects:
        selected = []
        for subject in documented_subjects:
            matches = [o for o in objects if subject in (o[3]['name'], f"{o[3]['schema']}.{o[3]['name']}", o[3]['canonical_key'])]
            if len(matches) != 1:
                raise ValueError(f'Subject {subject!r}: expected one declaration, found {len(matches)}')
            if matches[0] not in selected: selected.append(matches[0])
    if not selected:
        raise ValueError('No supported object declaration')
    analyzed = set()
    for raw, snippet, line, details in selected:
        engine.scope, engine.temps, engine.cte_nodes = details['canonical_key'], {}, {}
        declaration = engine.item('DECLARATION', line, line + snippet.count('\n'), **details)
        if engine.scope.startswith('unresolved+'):
            engine.note(line, 'Unresolved schema/search_path or input type in declaration')
        if raw is None:
            for stmt in raw_stmts:
                statement_text, statement_line = source_statement(stmt, text)
                engine.statement(stmt.stmt, statement_text, statement_line)
            continue
        node = raw.stmt
        if isinstance(node, ast.CreateFunctionStmt):
            options = {o.defname:o.arg for o in node.options or ()}
            language = getattr(options.get('language'), 'sval', None)
            bodies = options.get('as')
            if not bodies or not isinstance(bodies, tuple) or len(bodies) != 1:
                engine.note(line, 'Routine requires one SQL/PLpgSQL body')
                continue
            body = bodies[0].sval
            body_pos = snippet.find(body)
            body_line = line + snippet[:max(0,body_pos)].count('\n')
            try:
                if language == 'plpgsql':
                    function = parse_plpgsql(snippet)[0]['PLpgSQL_function']
                    engine.plpgsql(function['action'], body_line)
                elif language == 'sql':
                    for statement in parse_sql(body):
                        statement_text, statement_line = source_statement(statement, body)
                        engine.statement(statement.stmt, statement_text, body_line + statement_line - 1)
                else:
                    engine.note(line, 'Unsupported or unresolved routine language')
            except Exception as exc:
                engine.note(line, f'Routine body analysis failed: {exc}')
        elif isinstance(node, ast.ViewStmt):
            query=engine.analyze_query(node.query, snippet, line)
            outputs = query['details'].get('columns',[]) if query else []
            for index, alias in enumerate(node.aliases or ()):
                if index < len(outputs): outputs[index]['name'] = alias.sval
            declaration['details']['output_columns'] = outputs
        else:
            query=engine.statement(node, snippet, line)
            if isinstance(node, ast.CreateTableAsStmt):
                declaration['details']['output_columns'] = query['details'].get('columns',[]) if query else []
                for index, alias in enumerate(node.into.colNames or ()):
                    if index < len(declaration['details']['output_columns']): declaration['details']['output_columns'][index]['name'] = alias.sval
        if details['object_kind'] in ('table', 'view', 'materialized_view', 'ctas'):
            qualified = f"{details['schema']}.{details['name']}"
            for other in raw_stmts:
                if other is raw or any(other is o[0] for o in objects): continue
                target = getattr(other.stmt, 'relation', None)
                matches = bool(target) and relation(target) == qualified
                if not matches and isinstance(other.stmt, ast.GrantStmt):
                    matches = any(isinstance(o, ast.RangeVar) and relation(o) == qualified
                                  for o in other.stmt.objects or ())
                if matches:
                    statement_text, statement_line = source_statement(other, text)
                    engine.statement(other.stmt, statement_text, statement_line)
                    analyzed.add(id(other))
    recognized = {id(o[0]) for o in objects if o[0]}
    # Statements that are valid standalone or attached to a declaration
    standalone_types = (ast.CreateTrigStmt, ast.IndexStmt, ast.GrantStmt)
    if not any(o[0] is None for o in objects):
        for raw in raw_stmts:
            if id(raw) in recognized or id(raw) in analyzed or isinstance(raw.stmt, ast.CreateSchemaStmt): continue
            if isinstance(raw.stmt, (ast.InsertStmt,ast.UpdateStmt,ast.DeleteStmt,ast.AlterTableStmt,ast.CommentStmt)) and any(o[3]['object_kind']=='table' for o in objects): continue
            if isinstance(raw.stmt, standalone_types):
                # Never attach a top-level DDL/access statement to the last routine.
                target = getattr(raw.stmt, 'relation', None)
                targets = [relation(target)] if target else [relation(o) for o in getattr(raw.stmt, 'objects', ()) or ()
                                                              if isinstance(o, ast.RangeVar)]
                owners = {f"{o[3]['schema']}.{o[3]['name']}" for o in objects
                          if o[3]['object_kind'] in ('table', 'view', 'materialized_view', 'ctas')}
                if targets and set(targets) <= owners:
                    continue  # Belongs to an explicitly unselected relation.
                engine.scope = path
                statement_text, statement_line = source_statement(raw, text)
                engine.statement(raw.stmt, statement_text, statement_line)
                engine.note(statement_line, 'Standalone extension requires a declared target relation in the selected SQL')
                continue
            engine.note(1, f'Unanalyzed top-level statement: {type(raw.stmt).__name__}')
    if dialect.lower() not in ('postgres','postgresql'):
        engine.note(1, f'Unsupported dialect: {dialect}')
    for item in engine.items:
        if any(o.get('expression') == '*' or o.get('expression','').endswith('.*') for o in item['details'].get('output_columns',[])):
            engine.note(item['source_ref']['start_line'], 'Wildcard output columns require DDL expansion')
    return dict(schema_version=2, run_id='00000000-0000-0000-0000-000000000000',
                dialect=dict(name=dialect,version=version), items=engine.items, coverage_notes=engine.notes,
                inputs=[dict(path=path,sha256=sha)], documented_subjects=[o[3]['canonical_key'] for o in selected])
