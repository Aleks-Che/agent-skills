"""Independent PostgreSQL syntax inventory using libpg_query through pglast.

Parses SQL and PL/pgSQL without a database. Unhandled AST nodes remain explicit gaps.
Only raw SQL is accepted; facts, pages and expectations are never inputs.
"""
import hashlib
import re
from pathlib import PurePosixPath

from artifact_schema import ArtifactInputError
from identity import ObjectDescriptor, canonical_key, _parse_arg_types
from sql_gp import (distributed_in_bytes, execute_on_in_bytes, is_greenplum, prepare as gp_prepare,
storage_parameters_of, storage_options_in_bytes, statement_byte_span)
from sql_syntax import mask_sql, SubjectSelectionError

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


def from_relations(node, *, env=None, temps=None, derived_refs=None):
    """Return (alias, relation) star-expansion sources of a query, or None if any
    FROM item is not a plain relation, CTE reference, or derived table.
    Only plain ON/CROSS joins preserve a simple concatenation of columns;
    USING/NATURAL joins and aliases that rename columns need their own
    projection model and remain unresolved.  CTE references are returned as
    (alias, cte_name) for env resolution; derived tables as (alias, alias_name)."""
    result = []
    def visit(item):
        if isinstance(item, ast.RangeVar):
            if item.alias and item.alias.colnames:
                result.append(None)
            else:
                ref = relation(item)
                if not item.schemaname:
                    ref = (env or {}).get(ref, (temps or {}).get(ref, ref))
                result.append([item.alias.aliasname if item.alias else None, ref])
        elif isinstance(item, ast.RangeSubselect):
            if item.alias and item.alias.aliasname and not item.alias.colnames:
                result.append([item.alias.aliasname,
                               (derived_refs or {}).get(id(item.subquery), item.alias.aliasname)])
            else:
                result.append(None)
        elif isinstance(item, ast.JoinExpr):
            if item.isNatural or item.usingClause or item.alias:
                result.append(None)
            else:
                visit(item.larg)
                visit(item.rarg)
        else:
            result.append(None)
    for item in getattr(node, 'fromClause', None) or ():
        visit(item)
    if not result or any(entry is None for entry in result):
        return None
    return result


def star_targets(target_list):
    """Return indexes of SELECT-list entries that are * or alias.* wildcards."""
    indexes = []
    for index, target in enumerate(target_list or ()):
        value = getattr(target, 'val', None)
        if isinstance(value, ast.ColumnRef) and isinstance(value.fields[-1], ast.A_Star):
            indexes.append(index)
    return indexes


def type_name(node):
    return sql(node) if node else None


def source_statement(raw, text):
    data = text.encode('utf-8')
    start, end = statement_byte_span(raw, text)
    snippet = data[start:end].decode('utf-8')
    masked = mask_sql(snippet)[0]
    leading = len(masked) - len(masked.lstrip())
    return snippet[leading:], data[:start].decode('utf-8').count('\n') + 1 + snippet[:leading].count('\n')


BUILTINS = frozenset('sum avg count min max now coalesce nullif greatest least round abs lower upper trim substring extract date_trunc to_char to_date row_number rank dense_rank lag lead format concat concat_ws length current_date timezone generate_series trunc to_number xmlagg make_interval'.split())
QUERY_TYPES = ('SelectStmt', 'InsertStmt', 'UpdateStmt', 'DeleteStmt', 'MergeStmt')
WILDCARD_NOTE = 'Wildcard output columns require DDL expansion'
# Oracle-compatibility date functions used by the control projects (orafce on
# Greenplum, Oracle SQL semantics). Contracts are cited, not invented:
# LAST_DAY(d) -> DATE, last calendar day of the month containing d;
# ADD_MONTHS(d, n) -> DATE, d plus n months with month-end
# adjustment. Availability of the extension on the target server is NOT
# established here; callers record that as an explicit unknown.
EXTERNAL_FUNCTION_CONTRACTS = {
    'last_day': dict(return_type='date', arity=1,
                     summary='Last calendar day of the month containing the argument',
                     semantics_source='oracle_sql_reference'),
    'add_months': dict(return_type='date', arity=2,
                       summary='Argument date plus n months with month-end adjustment; Oracle contract returns DATE',
                       semantics_source='oracle_sql_reference'),
}
EXTERNAL_FUNCTION_NOTE = ('Oracle-compatibility function {name}: semantics per {source}; '
                          'availability on the target server is not verified')
# Values emitted by the pinned pglast/libpg_query parser (not server severity codes).
RAISE_LEVELS = {14: 'DEBUG', 15: 'LOG', 17: 'INFO', 18: 'NOTICE', 19: 'WARNING', 21: 'EXCEPTION'}
RAISE_OPTIONS = ('ERRCODE', 'MESSAGE', 'DETAIL', 'HINT', 'COLUMN', 'CONSTRAINT', 'DATATYPE', 'TABLE', 'SCHEMA')


def _assignment_parts(query):
    masked = mask_sql(query)[0]
    depth = 0
    for index, char in enumerate(masked):
        if char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif char == '=' and depth == 0:
            end = index - 1 if index and masked[index - 1] == ':' else index
            return query[:end].strip(), query[index + 1:].strip()
    raise ValueError('Unresolved PL/pgSQL assignment boundary')


def _datum_name(datums, index):
    if not datums or index is None or not isinstance(index, int):
        return None
    if index < 0 or index >= len(datums):
        return None
    for value in datums[index].values():
        return value.get('refname')
    return None


def _guard_details(guards):
    return {'guards': list(guards)} if guards else {}


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
    def __init__(self, text, path, sha, version, gp_constructs=(), gp_enabled=False):
        require_parser()
        self.text, self.path, self.sha, self.version = text, path, sha, version
        self.gp_constructs = list(gp_constructs)
        self.gp_enabled = gp_enabled
        self.items, self.notes, self.counts, self.temps = [], [], {}, {}
        self.scope = path
        self.query_number = 0
        self.cte_nodes = {}
        self.external_calls = set()

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

    def check_merge_version(self, line):
        if self.gp_enabled:
            self.note(line, 'MERGE compatibility with Greenplum is not established; PostgreSQL version thresholds do not apply')
            return
        major = self.version.split('.')[0]
        if not major.isdigit() or int(major) < 15:
            self.note(line, 'MERGE requires a confirmed PostgreSQL version >= 15')

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
                elif parts and parts[0] in EXTERNAL_FUNCTION_CONTRACTS:
                    if len(n.args or ()) == EXTERNAL_FUNCTION_CONTRACTS[parts[0]]['arity']:
                        self.external_calls.add(parts[0])
                    else:
                        self.note(line, f'Unsupported arity for Oracle-compatibility function: {parts[0]}')
                elif parts and parts[0] not in BUILTINS:
                    self.note(line, f'Unresolved unqualified call: {parts[0]}')
            for child in children(n):
                if child is not with_:
                    visit(child, local)
        visit(node, env)
        return list(dict.fromkeys(reads)), list(dict.fromkeys(calls))

    def analyze_query(self, node, raw, line, forced_kind=None, env=None, branch=None, guard=()):
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
                          lifetime='statement', columns=[n.sval for n in cte.aliascolnames or ()],query=sql(cte.ctequery),
                          **_guard_details(guard))
                result=self.analyze_query(cte.ctequery, sql(cte.ctequery), line, env=env, branch=branch, guard=guard)
                if result: result['details']['result_for']=local
                env[cte.ctename] = local
        cls = type(node).__name__
        kind = forced_kind or cls.removesuffix('Stmt').upper()
        if kind not in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'PERFORM', 'CALL', 'RETURN', 'ASSIGN', 'IF', 'RAISE'):
            self.note(line, f'Unsupported query AST: {cls}')
            return
        if kind == 'MERGE':
            self.check_merge_version(line)
        target = getattr(node, 'relation', None)
        reads, calls = self.dependencies(node, env, line, exclude=(id(target),) if target else ())
        formulas, conditions, outputs, derived = [], [], [], []
        def direct_walk(n):
            yield n
            for child in children(n):
                if child is with_ or type(child).__name__ in QUERY_TYPES:
                    continue
                yield from direct_walk(child)
        # Derived aliases belong to this FROM, not the CTE namespace inherited
        # by subqueries. Link their actual query result for fixpoint expansion.
        derived_refs = {}
        for n in direct_walk(node):
            if isinstance(n, ast.RangeSubselect) and n.alias and n.alias.aliasname:
                derived.append(n.alias.aliasname)
                if not n.alias.colnames:
                    derived_refs[id(n.subquery)] = f'@derived:{self.scope}:{statement_no}:{n.alias.aliasname}'
        for n in direct_walk(node):
            if isinstance(n, ast.ResTarget) and n.val is not None:
                expression = sql(n.val)
                if cls == 'SelectStmt' and n in (node.targetList or ()):
                    name = n.name
                    if not name and isinstance(n.val, ast.ColumnRef) and isinstance(n.val.fields[-1], ast.String):
                        name = n.val.fields[-1].sval
                    entry = dict(name=name, expression=expression)
                    if isinstance(n.val, ast.ColumnRef) and isinstance(n.val.fields[-1], ast.A_Star):
                        sources = from_relations(node, env=env, temps=self.temps,
                                                 derived_refs=derived_refs)
                        if sources is not None:
                            entry['star_sources'] = sources
                    outputs.append(entry)
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
        if derived:
            details['derived_aliases'] = list(dict.fromkeys(derived))
        if branch:
            details['branch'] = branch
        if guard:
            details['guards'] = list(guard)
        if getattr(node,'groupClause',None): details['group_by']=[sql(n) for n in node.groupClause]
        if getattr(node,'sortClause',None): details['order_by']=[sql(s) for s in node.sortClause]
        if getattr(node,'limitCount',None) is not None: details['limit']=sql(node.limitCount)
        if getattr(node,'limitOffset',None) is not None: details['offset']=sql(node.limitOffset)
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
            result = self.analyze_query(child, sql(child), line, env=env, branch=branch, guard=guard)
            if result and id(child) in derived_refs:
                result['details']['result_for'] = derived_refs[id(child)]
        return item

    def statement(self, node, raw, line, forced_kind=None, branch=None, gp_span=None, guard=()):
        cls = type(node).__name__
        if cls in QUERY_TYPES:
            return self.analyze_query(node, raw, line, forced_kind, branch=branch, guard=guard)
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
            if self.gp_enabled:
                distributed = distributed_in_bytes(self.gp_constructs, *gp_span) if gp_span else None
                if distributed:
                    extension['distributed'] = distributed
                try:
                    storage = storage_parameters_of(node)
                    lexical = storage_options_in_bytes(self.gp_constructs, *gp_span) if gp_span else None
                    if lexical:
                        storage = {**(storage or {}), **lexical}
                    if storage:
                        extension['storage_parameters'] = storage
                except ValueError as exc:
                    self.note(line, str(exc))
            if any(k in extension for k in ('distributed', 'storage_parameters')):
                extension['gp_extension_version'] = 1
            self.item('CREATE', line, line + raw.count('\n'), table_name=name, reference=ref,
                      physical=True, temporary=temporary, lifetime=node.oncommit.name,
                      columns=columns_of(node), ddl=sql(node), **extension)['writes'] = [ref]
        elif isinstance(node, ast.CreateTableAsStmt):
            name = relation(node.into.rel)
            temporary=node.into.rel.relpersistence=='t'
            ref=f'@temp:{self.scope}:{name}' if temporary else name
            if temporary: self.temps[name]=ref
            extension = {}
            if self.gp_enabled:
                distributed = distributed_in_bytes(self.gp_constructs, *gp_span) if gp_span else None
                if distributed:
                    extension['distributed'] = distributed
                try:
                    storage = storage_parameters_of(node.into)
                    lexical = storage_options_in_bytes(self.gp_constructs, *gp_span) if gp_span else None
                    if lexical:
                        storage = {**(storage or {}), **lexical}
                    if storage:
                        extension['storage_parameters'] = storage
                except ValueError as exc:
                    self.note(line, str(exc))
            if extension:
                extension['gp_extension_version'] = 1
            self.item('CTAS', line, line + raw.count('\n'), ddl=sql(node),temporary=temporary,reference=ref,lifetime=node.into.onCommit.name, **extension)['writes'] = [ref]
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

    def plpgsql(self, data, body_line, branch=None, guard=(), datums=None):
        # Apply the enclosing control flow to every emitted operation, including
        # utility statements, CTE records and dynamic commands. Nested branches
        # already carry a more specific context and must keep it.
        start = len(self.items)
        self._plpgsql(data, body_line, branch, guard, datums)
        for item in self.items[start:]:
            if branch:
                item['details'].setdefault('branch', branch)
            if guard:
                item['details'].setdefault('guards', list(guard))

    def _plpgsql(self, data, body_line, branch=None, guard=(), datums=None):
        kind, value = next(iter(data.items()))
        line = body_line + value.get('lineno', 1) - 1
        expr = lambda field: value.get(field, {}).get('PLpgSQL_expr', {}).get('query', '')
        if kind == 'PLpgSQL_stmt_block':
            exceptions = value.get('exceptions') or {}
            block = exceptions.get('PLpgSQL_exception_block', exceptions)
            if block.get('exc_list'):
                self.note(line, 'Exception handlers are inventoried as branch ops; runtime failure point is not analysed')
            for entry in value.get('body', []):
                self.plpgsql(entry, body_line, branch, guard, datums)
            for handler in (block.get('exc_list') or []):
                entry = handler.get('PLpgSQL_exception', handler)
                conditions = []
                for condition in entry.get('conditions') or []:
                    condition = condition.get('PLpgSQL_condition', condition)
                    conditions.append(condition.get('condname') or str(condition.get('sqlerrcode', 'unknown')))
                label = ('exception:' + ','.join(conditions)) if conditions else 'exception:others'
                handler_branch = (branch + '/' if branch else '') + label
                for action in entry.get('action') or []:
                    self.plpgsql(action, body_line, handler_branch, guard, datums)
        elif kind in ('PLpgSQL_stmt_execsql', 'PLpgSQL_stmt_perform', 'PLpgSQL_stmt_call'):
            query = expr('sqlstmt' if kind.endswith('execsql') else 'expr')
            for raw in parse_sql(query):
                item=self.statement(raw.stmt, query, line, 'PERFORM' if kind.endswith('perform') else None, branch, guard=guard)
                if item and value.get('into'):
                    target=value.get('target',{})
                    row=target.get('PLpgSQL_row',{})
                    destinations=[f['name'] for f in row.get('fields',[])] or [target.get('PLpgSQL_var',{}).get('refname')]
                    if not all(destinations): self.note(line,'Unresolved PL/pgSQL INTO target')
                    else:
                        item['details']['into']=destinations
                        item['details']['assignments']=[dict(target=name,expression=sql(t.val)) for name,t in zip(destinations,getattr(raw.stmt,'targetList',()) or ())]
        elif kind == 'PLpgSQL_stmt_getdiag':
            for diagnostic in value.get('diag_items') or []:
                diagnostic = diagnostic.get('PLpgSQL_diag_item', diagnostic)
                target = _datum_name(datums, diagnostic.get('target'))
                if not target:
                    self.note(line, 'Unresolved PL/pgSQL GET DIAGNOSTICS target')
                    continue
                self.item('ASSIGN', line, assignment_target=target,
                          assignments=[dict(target=target, expression=diagnostic['kind'])],
                          diagnostic=dict(kind=diagnostic['kind'], stacked=bool(value.get('is_stacked'))),
                          analysis='postgres_ast')
        elif kind == 'PLpgSQL_stmt_raise':
            params = [p.get('PLpgSQL_expr', {}).get('query', '')
                      for p in value.get('params') or []]
            options = []
            for option in value.get('options') or []:
                option = option.get('PLpgSQL_raise_option', option)
                index = option.get('opt_type', -1)
                if not 0 <= index < len(RAISE_OPTIONS):
                    self.note(line, 'Unsupported PL/pgSQL RAISE option')
                    continue
                options.append(dict(name=RAISE_OPTIONS[index],
                                    expression=option.get('expr', {}).get('PLpgSQL_expr', {}).get('query', '')))
            expressions = [p for p in params if p] + [o['expression'] for o in options if o['expression']]
            if expressions:
                query = 'SELECT ' + ', '.join(expressions)
                item = self.analyze_query(parse_sql(query)[0].stmt, query, line, 'RAISE', branch=branch, guard=guard)
                item['details'].pop('columns', None)
            else:
                item = self.item('RAISE', line)
            details = dict(raise_level=RAISE_LEVELS.get(value.get('elog_level'), 'UNKNOWN'),
                           message=value.get('message'), arguments=[p for p in params if p],
                           raise_condition=value.get('condname'), raise_options=options,
                           rethrow=not any(k in value for k in ('message', 'condname', 'params', 'options')),
                           formulas=list(dict.fromkeys(expressions)), has_formula=bool(expressions),
                           analysis='postgres_ast', **_guard_details(guard))
            if details['raise_level'] == 'UNKNOWN':
                self.note(line, 'Unsupported PL/pgSQL RAISE level')
            if branch:
                details['branch'] = branch
            item['details'].update(details)
        elif kind == 'PLpgSQL_stmt_if':
            condition = expr('cond')
            self.item('IF', line, conditions=[condition], formulas=[], has_condition=True,
                      branches=['then', 'else'], analysis='postgres_ast', **_guard_details(guard))
            prefix = branch + '/' if branch else ''
            for entry in value.get('then_body', []):
                self.plpgsql(entry, body_line, prefix + 'then_body', tuple(guard) + (condition,), datums)
            # FALSE and NULL both skip a PL/pgSQL condition; NOT alone loses NULL.
            rejected = tuple(guard) + (f'({condition}) IS NOT TRUE',)
            for index, entry in enumerate(value.get('elsif_list', [])):
                other = entry.get('PLpgSQL_stmt_elsif', entry.get('PLpgSQL_if_elsif', entry))
                condition = other['cond']['PLpgSQL_expr']['query']
                self.item('IF', body_line + other.get('lineno', 1) - 1,
                          conditions=[condition], has_condition=True, analysis='postgres_ast', **_guard_details(rejected))
                for nested in other.get('stmts', []) or other.get('then_body', []):
                    self.plpgsql(nested, body_line, prefix + f'elsif:{index}', rejected + (condition,), datums)
                rejected += (f'({condition}) IS NOT TRUE',)
            for entry in value.get('else_body', []):
                self.plpgsql(entry, body_line, prefix + 'else_body', rejected, datums)
        elif kind in ('PLpgSQL_stmt_assign', 'PLpgSQL_stmt_return'):
            query = expr('expr')
            if not query and not value.get('lineno'):
                return  # Implicit end-of-function return, not a source occurrence.
            if kind.endswith('assign'):
                assignment_target, expression = _assignment_parts(query)
            else:
                expression = query.strip()
            if expression:
                parsed = parse_sql('SELECT ' + expression)[0].stmt
                item=self.analyze_query(parsed, query, line, 'ASSIGN' if kind.endswith('assign') else 'RETURN', branch=branch, guard=guard)
                if kind.endswith('assign'):
                    target = _datum_name(datums, value.get('varno'))
                    item['details']['assignment_target']=target or assignment_target
                    item['details']['assignments']=[dict(target=item['details']['assignment_target'],expression=expression)]
            else:
                self.item('RETURN', line, formulas=[], conditions=[], **_guard_details(guard))
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
            if isinstance(node, ast.MergeStmt):
                self.check_merge_version(line)
            reads, calls = self.dependencies(node, {}, line, exclude=(id(getattr(node,'relation',None)),))
            item['reads'] = [r for r in reads if '__dynamic_' not in r]
            item['calls'] = calls
        elif kind == 'PLpgSQL_stmt_null':
            return
        else:
            self.note(line, f'Unsupported PL/pgSQL AST node: {kind}')


def analyze(text, path, sha, dialect='postgres', version='unknown', documented_subjects=None):
    require_parser()
    parse_text, gp_constructs, gp_notes = gp_prepare(text, dialect)
    gp_enabled = is_greenplum(dialect)
    engine = Analyzer(text, path, sha, version, gp_constructs=gp_constructs, gp_enabled=gp_enabled)
    for note_line, reason in gp_notes:
        engine.note(note_line, reason)
    raw_stmts = parse_sql(parse_text)
    encoded = text.encode('utf-8')
    objects = []
    for raw in raw_stmts:
        node = raw.stmt
        start, stop = statement_byte_span(raw, parse_text)
        snippet = encoded[start:stop].decode('utf-8')
        masked_snippet = parse_text.encode('utf-8')[start:stop].decode('utf-8')
        line = encoded[:start].decode('utf-8').count('\n') + 1
        # libpg_query includes whitespace/comments before the statement in RawStmt.
        masked = mask_sql(snippet)[0]
        leading = len(masked) - len(masked.lstrip())
        line += snippet[:leading].count('\n')
        snippet = snippet[leading:]
        masked_snippet = masked_snippet[leading:]
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
            execute_on = execute_on_in_bytes(gp_constructs, start, stop)
            if execute_on:
                details.update(execute_on=execute_on, gp_extension_version=1)
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
            objects.append((raw, snippet, masked_snippet, line, details))
    if not objects and any(isinstance(r.stmt, (ast.AlterTableStmt,ast.RenameStmt,ast.DropStmt)) for r in raw_stmts):
        key = canonical_key(ObjectDescriptor('migration', migration_path=path))
        details = dict(object_kind='migration', name=PurePosixPath(path).name, schema=None,
                       input_types=None, signature=None, canonical_key=key, analysis='postgres_ast')
        objects = [(None, text, text, 1, details)]
    selected = objects
    if documented_subjects:
        selected = []
        for subject in documented_subjects:
            matches = [o for o in objects if subject in (o[4]['name'], f"{o[4]['schema']}.{o[4]['name']}", o[4]['canonical_key'])]
            if len(matches) != 1:
                raise SubjectSelectionError(f'Subject {subject!r}: expected one declaration, found {len(matches)}')
            if matches[0] not in selected: selected.append(matches[0])
    if not selected:
        raise ValueError('No supported object declaration')
    analyzed = set()
    for raw, snippet, masked_snippet, line, details in selected:
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
                    function = parse_plpgsql(masked_snippet)[0]['PLpgSQL_function']
                    engine.plpgsql(function['action'], body_line, datums=function.get('datums'))
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
            if node.aliases and star_targets(node.query.targetList):
                declaration['details']['output_column_aliases'] = [a.sval for a in node.aliases]
            for index, alias in enumerate(node.aliases or ()):
                if index < len(outputs): outputs[index]['name'] = alias.sval
            declaration['details']['output_columns'] = outputs
        else:
            query=engine.statement(node, snippet, line, gp_span=statement_byte_span(raw, parse_text))
            if isinstance(node, ast.CreateTableAsStmt):
                declaration['details']['output_columns'] = query['details'].get('columns',[]) if query else []
                if node.into.colNames and star_targets(node.query.targetList):
                    declaration['details']['output_column_aliases'] = [a.sval for a in node.into.colNames]
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
            if isinstance(raw.stmt, (ast.InsertStmt,ast.UpdateStmt,ast.DeleteStmt,ast.AlterTableStmt,ast.CommentStmt)) and any(o[4]['object_kind']=='table' for o in objects): continue
            if isinstance(raw.stmt, standalone_types):
                # Never attach a top-level DDL/access statement to the last routine.
                target = getattr(raw.stmt, 'relation', None)
                targets = [relation(target)] if target else [relation(o) for o in getattr(raw.stmt, 'objects', ()) or ()
                                                              if isinstance(o, ast.RangeVar)]
                owners = {f"{o[4]['schema']}.{o[4]['name']}" for o in objects
                          if o[4]['object_kind'] in ('table', 'view', 'materialized_view', 'ctas')}
                if targets and set(targets) <= owners:
                    continue  # Belongs to an explicitly unselected relation.
                engine.scope = path
                statement_text, statement_line = source_statement(raw, text)
                engine.statement(raw.stmt, statement_text, statement_line)
                engine.note(statement_line, 'Standalone extension requires a declared target relation in the selected SQL')
                continue
            engine.note(1, f'Unanalyzed top-level statement: {type(raw.stmt).__name__}')
    if dialect.lower() not in ('postgres', 'postgresql', 'greenplum'):
        engine.note(1, f'Unsupported dialect: {dialect}')
    for item in engine.items:
        if item['kind'] == 'DECLARATION' and engine.external_calls:
            item['details']['external_functions'] = sorted(engine.external_calls)
        if any(o.get('expression') == '*' or o.get('expression','').endswith('.*') for o in item['details'].get('output_columns',[])):
            engine.note(item['source_ref']['start_line'], WILDCARD_NOTE)
        if any(c.get('expression') == '*' or c.get('expression','').endswith('.*')
               for c in item['details'].get('columns',[]) if isinstance(c, dict)):
            engine.note(item['source_ref']['start_line'], WILDCARD_NOTE)
    return dict(schema_version=2, run_id='00000000-0000-0000-0000-000000000000',
                dialect=dict(name=dialect,version=version), items=engine.items, coverage_notes=engine.notes,
                inputs=[dict(path=path,sha256=sha)], documented_subjects=[o[4]['canonical_key'] for o in selected])
