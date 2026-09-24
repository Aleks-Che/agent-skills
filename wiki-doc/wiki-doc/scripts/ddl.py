"""Static DDL catalog and explicitly ordered migration reconstruction."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from artifact_schema import read_json, validate_schema, load_schemas, ArtifactInputError
from sql_ast import quote, require_parser, columns_of, relation, type_name, sql
from sql_gp import (identifier_name, statement_byte_span, distributed_in_bytes,
                    is_greenplum, prepare as gp_prepare, storage_parameters_of,
                    storage_options_in_bytes)


def parse(text, dialect='postgres'):
    require_parser()
    from pglast import parse_sql
    parse_text, _, notes = gp_prepare(text, dialect)
    if notes:
        raise ValueError('; '.join(f'line {line}: {reason}' for line, reason in notes))
    return parse_sql(parse_text)


def _and_terms(node):
    from pglast import ast
    if isinstance(node, ast.BoolExpr) and node.boolop.name == 'AND_EXPR':
        terms = []
        for arg in node.args:
            terms.extend(_and_terms(arg))
        return terms
    return [node]


def _string_const(node):
    from pglast import ast
    if isinstance(node, ast.A_Const) and isinstance(node.val, ast.String):
        return node.val.sval
    return None


def _catalog_drop_spec(query_text):
    """Parse the guarded pg_attribute lookup of the DROP COLUMN template.

    Returns (schema, table, columns) only when the query provably selects
    exactly the named columns of one literal schema.table with the standard
    system-catalog guards.
    """
    from pglast import ast, parse_sql
    text = query_text.strip()
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    try:
        statements = parse_sql(text)
        if len(statements) != 1:
            raise ValueError('expected one SELECT')
        node = statements[0].stmt
    except Exception as exc:
        raise ValueError(f'Catalog-guarded DO query does not parse: {exc}')
    if not isinstance(node, ast.SelectStmt) or node.valuesLists:
        raise ValueError('Catalog-guarded DO query must be a SELECT over pg_attribute')
    if node.op.name != 'SETOP_NONE' or any(getattr(node, field, None) for field in (
            'groupClause', 'havingClause', 'withClause', 'distinctClause',
            'limitCount', 'limitOffset', 'windowClause', 'lockingClause', 'intoClause', 'sortClause')):
        raise ValueError('Catalog-guarded DO query must not filter, combine or aggregate the selected rows')
    aliases, predicates = {}, []

    def visit(item):
        if isinstance(item, ast.RangeVar):
            name = relation(item)
            alias = item.alias.aliasname if item.alias else item.relname
            if alias in aliases or name in aliases.values() or (item.alias and item.alias.colnames):
                raise ValueError('Catalog-guarded DO query requires distinct pg_attribute, pg_class and pg_namespace aliases')
            aliases[alias] = name
        elif isinstance(item, ast.JoinExpr):
            if item.jointype.name != 'JOIN_INNER' or item.isNatural or item.usingClause or item.alias:
                raise ValueError('Catalog-guarded DO query requires plain INNER joins')
            visit(item.larg)
            visit(item.rarg)
            if item.quals is not None:
                predicates.extend(_and_terms(item.quals))
        else:
            raise ValueError('Catalog-guarded DO query must not use subqueries or function sources')
    for item in node.fromClause or ():
        visit(item)
    if set(aliases.values()) != {'pg_catalog.pg_attribute', 'pg_catalog.pg_class', 'pg_catalog.pg_namespace'}:
        raise ValueError('Catalog-guarded DO query must join pg_attribute, pg_class and pg_namespace')

    def field(node):
        if isinstance(node, ast.ColumnRef) and len(node.fields) == 2 and all(
                isinstance(p, ast.String) for p in node.fields):
            owner, column = (p.sval for p in node.fields)
            if owner in aliases:
                return aliases[owner].removeprefix('pg_catalog.') + '.' + column
        return None

    projected = {}
    for target in node.targetList or ():
        source = field(target.val)
        label = target.name or (source.rsplit('.', 1)[-1] if source else None)
        if not label or label in projected:
            raise ValueError('Catalog-guarded DO query has ambiguous projected names')
        projected[label] = source
    if projected != {'nspname': 'pg_namespace.nspname', 'relname': 'pg_class.relname',
                     'attname': 'pg_attribute.attname'}:
        raise ValueError('Catalog-guarded DO query must select the original nspname, relname and attname')
    required_pairs = {frozenset(('pg_class.oid', 'pg_attribute.attrelid')),
                      frozenset(('pg_namespace.oid', 'pg_class.relnamespace'))}
    pairs = set()
    schema = table = None
    columns = None
    guards = set()
    predicates.extend(_and_terms(node.whereClause))
    for term in predicates:
        if isinstance(term, ast.BoolExpr) and term.boolop.name == 'NOT_EXPR':
            if len(term.args) == 1 and field(term.args[0]) == 'pg_attribute.attisdropped':
                guards.add('attisdropped')
                continue
        if isinstance(term, ast.A_Expr):
            operator = [p.sval for p in term.name or ()]
            left = field(term.lexpr)
            if term.kind.name == 'AEXPR_OP' and operator == ['=']:
                pair = frozenset((left, field(term.rexpr)))
                if pair in required_pairs:
                    pairs.add(pair)
                    continue
            if term.kind.name == 'AEXPR_OP' and operator == ['='] and left == 'pg_namespace.nspname' and schema is None:
                schema = _string_const(term.rexpr)
                if schema is not None:
                    continue
            if term.kind.name == 'AEXPR_OP' and operator == ['='] and left == 'pg_class.relname' and table is None:
                table = _string_const(term.rexpr)
                if table is not None:
                    continue
            if left == 'pg_attribute.attname' and term.kind.name == 'AEXPR_IN' and operator == ['='] and columns is None:
                columns = [_string_const(v) for v in term.rexpr or ()]
                continue
            if term.kind.name == 'AEXPR_OP' and operator == ['='] and left == 'pg_attribute.attname' and columns is None:
                columns = [_string_const(term.rexpr)]
                continue
            if (term.kind.name == 'AEXPR_OP' and operator == ['>'] and left == 'pg_attribute.attnum'
                    and isinstance(term.rexpr, ast.A_Const) and isinstance(term.rexpr.val, ast.Integer)
                    and term.rexpr.val.ival == 0):
                guards.add('attnum')
                continue
        raise ValueError('Catalog-guarded DO query has an unrecognized constraint')
    if pairs != required_pairs:
        raise ValueError('Catalog-guarded DO query must join class/namespace by qualified oid')
    if not schema or not table or not columns or any(c is None for c in columns):
        raise ValueError('Catalog-guarded DO query must pin literal schema, table and column names')
    if guards != {'attnum', 'attisdropped'}:
        raise ValueError('Catalog-guarded DO query must keep the attnum>0 and NOT attisdropped guards')
    # This template concatenates identifiers without quote_ident. Prove that
    # PostgreSQL reparses every literal as the very same identifier.
    for name in [schema, table, *columns]:
        try:
            parsed = parse_sql('SELECT ' + name)
            target = parsed[0].stmt.targetList[0]
            safe = (len(parsed) == 1 and len(parsed[0].stmt.targetList) == 1
                    and isinstance(target.val, ast.ColumnRef) and len(target.val.fields) == 1
                    and isinstance(target.val.fields[0], ast.String)
                    and target.val.fields[0].sval == name and target.name is None
                    and not parsed[0].stmt.fromClause)
        except Exception:
            safe = False
        if not safe:
            raise ValueError('Catalog-guarded DO template requires safe unquoted identifiers')
    return schema, table, columns


def _drop_column_template(expression_text, varname):
    from pglast import ast, parse_sql
    try:
        node = parse_sql('SELECT ' + expression_text.strip())[0].stmt.targetList[0].val
    except Exception:
        return False
    leaves = []

    def flatten(part):
        if isinstance(part, ast.A_Expr) and [s.sval for s in part.name or ()] == ['||']:
            flatten(part.lexpr)
            flatten(part.rexpr)
        else:
            leaves.append(part)
    flatten(node)
    if len(leaves) != 6:
        return False

    def const(part):
        return _string_const(part)

    def field(part):
        from pglast import ast as ast_mod
        if isinstance(part, ast_mod.ColumnRef) and len(part.fields) == 2 and \
                all(isinstance(p, ast_mod.String) for p in part.fields) and \
                part.fields[0].sval == varname:
            return part.fields[1].sval
        return None
    prefix, sep1, sep2 = const(leaves[0]), const(leaves[2]), const(leaves[4])
    return (prefix is not None and prefix.upper() == 'ALTER TABLE '
            and sep1 is not None and sep1.strip() == '.'
            and sep2 is not None and sep2.upper() == ' DROP COLUMN '
            and field(leaves[1]) == 'nspname'
            and field(leaves[3]) == 'relname'
            and field(leaves[5]) == 'attname')


def do_drop_columns(node):
    """Recognize the provable catalog-guarded DROP COLUMN migration template.

    The template conditionally drops a literal column list of one literal
    table, querying pg_attribute with the standard guards and executing only
    `ALTER TABLE <nspname>.<relname> DROP COLUMN <attname>` per found column.
    Returns (schema, table, columns). Any other DO program raises ValueError.
    """
    from pglast import ast
    require_parser()
    body = None
    for arg in node.args or ():
        if arg.defname == 'as':
            body = getattr(arg.arg, 'sval', None)
        elif arg.defname == 'language':
            language = getattr(arg.arg, 'sval', None)
            if language and language.lower() != 'plpgsql':
                raise ValueError(f'Unsupported DO language: {language}')
    if not body:
        raise ValueError('Unsupported DO migration node: missing body')
    tag = 'wiki_doc_do_probe'
    if f'${tag}$' in body:
        raise ValueError('Unsupported DO migration node: reserved dollar tag in body')
    require_parser()
    from pglast import parse_plpgsql
    wrapped = f'CREATE FUNCTION wiki_doc.do_probe() RETURNS void LANGUAGE plpgsql AS ${tag}${body}${tag}$;'
    try:
        program = parse_plpgsql(wrapped)[0]['PLpgSQL_function']
    except Exception as exc:
        raise ValueError(f'Unsupported DO migration node: body does not parse ({exc})')
    action = program.get('action') if isinstance(program, dict) else None
    if any(d.get('PLpgSQL_var', {}).get('default_val') or
           d.get('PLpgSQL_var', {}).get('defaultval') for d in program.get('datums', [])):
        raise ValueError('Unsupported DO migration node: declaration initializer')
    if isinstance(action, dict) and 'PLpgSQL_stmt_block' in action:
        block = action['PLpgSQL_stmt_block']
        if block.get('exceptions'):
            raise ValueError('Unsupported DO migration node: exception handler')
        statements = list(block.get('body') or [])
    elif isinstance(action, dict) and 'PLpgSQL_stmt_fors' in action:
        statements = [action]
    else:
        statements = []
    # Only the parser's empty trailing RETURN is implicit; a source RETURN
    # can stop the migration before the loop.
    if statements and statements[-1] == {'PLpgSQL_stmt_return': {}}:
        statements.pop()
    if len(statements) != 1 or 'PLpgSQL_stmt_fors' not in statements[0]:
        raise ValueError('Unsupported DO migration node: expected one catalog FOR loop')
    loop = statements[0]['PLpgSQL_stmt_fors']
    var = (loop.get('var') or {}).get('PLpgSQL_rec', {}).get('refname')
    if not var:
        raise ValueError('Unsupported DO migration node: FOR loop must iterate a record')
    body_stmts = loop.get('body') or []
    if len(body_stmts) != 1 or 'PLpgSQL_stmt_dynexecute' not in body_stmts[0]:
        raise ValueError('Unsupported DO migration node: loop must execute exactly one dynamic statement')
    dynamic = body_stmts[0]['PLpgSQL_stmt_dynexecute']
    if dynamic.get('into') or dynamic.get('params'):
        raise ValueError('Unsupported DO migration node: EXECUTE INTO/USING')
    execute = dynamic.get('query', {}).get('PLpgSQL_expr', {}).get('query', '')
    if not _drop_column_template(execute, var):
        raise ValueError('Unsupported DO migration node: dynamic statement is not the DROP COLUMN template')
    catalog_query = loop.get('query', {}).get('PLpgSQL_expr', {}).get('query', '')
    return _catalog_drop_spec(catalog_query)


def apply_statement(state, node, gp_attributes=None):
    from pglast import ast
    if isinstance(node, ast.CreateStmt):
        key = relation(node.relation)
        if key in state and not node.if_not_exists:
            raise ValueError(f'Duplicate CREATE TABLE without established replacement: {key}')
        state.setdefault(key, dict(columns=columns_of(node), **(gp_attributes or {})))
    elif isinstance(node, ast.AlterTableStmt):
        key = relation(node.relation)
        if key not in state:
            raise ValueError(f'Missing baseline for {key}')
        columns = state[key]['columns']
        for command in node.cmds:
            subtype = command.subtype.name
            found = next((c for c in columns if c['name'] == command.name), None)
            if subtype == 'AT_AddColumn':
                column = command.def_
                if any(c['name'] == column.colname for c in columns):
                    raise ValueError(f'Duplicate column {key}.{column.colname}')
                # Reuse the CREATE column contract, including DEFAULT/NULL constraints.
                fake = ast.CreateStmt(tableElts=(column,))
                columns.extend(columns_of(fake))
            elif subtype == 'AT_DropColumn':
                if command.name in [identifier_name(c) for c in state[key].get('distributed', {}).get('columns', [])]:
                    raise ValueError('Dropping a Greenplum distribution column requires unsupported redistribution analysis')
                if found is None and not command.missing_ok:
                    raise ValueError(f'Unknown column {key}.{command.name}')
                if found: columns.remove(found)
            elif subtype in ('AT_AlterColumnType','AT_ColumnDefault','AT_SetNotNull','AT_DropNotNull'):
                if found is None:
                    raise ValueError(f'Unknown column {key}.{command.name}')
                if subtype == 'AT_AlterColumnType':
                    if command.name in [identifier_name(c) for c in state[key].get('distributed', {}).get('columns', [])]:
                        raise ValueError('Changing a Greenplum distribution column type requires unsupported redistribution analysis')
                    found['type'] = type_name(command.def_.typeName)
                elif subtype == 'AT_ColumnDefault': found['default'] = sql(command.def_) or None
                else: found['not_null'] = subtype == 'AT_SetNotNull'
            elif subtype == 'AT_AddConstraint' and isinstance(command.def_, ast.Constraint):
                constraint = command.def_
                if constraint.indexname:
                    raise ValueError('Constraint USING INDEX requires index-column resolution')
                keys = [k.sval for k in constraint.keys or constraint.fk_attrs or ()]
                if set(keys) - {c['name'] for c in columns}:
                    raise ValueError(f'Unknown constraint columns in {key}: {keys}')
                if constraint.contype.name == 'CONSTR_PRIMARY':
                    for col in columns:
                        if col['name'] in keys:
                            col.update(primary_key=True, not_null=True)
            else:
                raise ValueError(f'Unsupported ALTER action: {subtype}')
    elif isinstance(node, ast.RenameStmt):
        key = relation(node.relation)
        if key not in state:
            raise ValueError(f'Missing baseline for {key}')
        if node.renameType.name == 'OBJECT_COLUMN':
            col = next((c for c in state[key]['columns'] if c['name'] == node.subname), None)
            if col is None or any(c['name'] == node.newname for c in state[key]['columns']):
                raise ValueError(f'Ambiguous column rename in {key}')
            col['name'] = node.newname
            distribution = state[key].get('distributed', {})
            if 'columns' in distribution:
                distribution['columns'] = [quote(node.newname) if identifier_name(c) == node.subname else c for c in distribution['columns']]
        elif node.renameType.name == 'OBJECT_TABLE':
            new_key = (node.relation.schemaname + '.' if node.relation.schemaname else '') + node.newname
            if new_key in state: raise ValueError(f'Rename collision: {new_key}')
            state[new_key] = state.pop(key)
        else:
            raise ValueError(f'Unsupported RENAME: {node.renameType.name}')
    elif isinstance(node, ast.DropStmt):
        if node.removeType.name == 'OBJECT_VIEW':
            return  # View lifecycle is outside the reconstructed table state.
        if node.removeType.name != 'OBJECT_TABLE':
            raise ValueError(f'Unsupported DROP: {node.removeType.name}')
        for parts in node.objects:
            key = '.'.join(p.sval for p in parts)
            if key not in state and not node.missing_ok: raise ValueError(f'Unknown DROP target: {key}')
            state.pop(key, None)
    elif isinstance(node, ast.DoStmt):
        schema, table, dropped = do_drop_columns(node)
        key = f'{schema}.{table}'
        if key not in state:
            raise ValueError(f'Catalog-guarded DROP COLUMN without established baseline: {key}')
        for name in dropped:
            if name in [identifier_name(c) for c in state[key].get('distributed', {}).get('columns', [])]:
                raise ValueError('Dropping a Greenplum distribution column requires unsupported redistribution analysis')
            # The runtime DO drops the column only when it exists; absent names
            # are an idempotent no-op, not a reconstruction error.
            for column in [c for c in state[key]['columns'] if c['name'] == name]:
                state[key]['columns'].remove(column)
    elif isinstance(node, (ast.TruncateStmt, ast.UpdateStmt)):
        pass  # Data-only statement: no effect on the reconstructed column state.
    elif isinstance(node, ast.CommentStmt):
        if node.objtype.name=='OBJECT_COLUMN':
            parts=[p.sval for p in node.object]
            key='.'.join(parts[:-1])
            found=next((c for c in state.get(key,{}).get('columns',[]) if c['name']==parts[-1]),None)
            if found is None: raise ValueError(f'Unknown COMMENT column: {key}.{parts[-1]}')
            found['comment']=node.comment
    elif isinstance(node, ast.CreateSchemaStmt):
        pass
    else:
        raise ValueError(f'Unsupported migration node: {type(node).__name__}')



def gp_table_attributes(raw, constructs, parse_text):
    start, stop = statement_byte_span(raw, parse_text)
    value = {}
    distributed = distributed_in_bytes(constructs, start, stop)
    if distributed:
        value['distributed'] = distributed
    storage = storage_parameters_of(raw.stmt)
    lexical = storage_options_in_bytes(constructs, start, stop)
    if lexical:
        # GP-only bare values (masked in the parse view) win over the flag/None
        # left for the PostgreSQL option list.
        storage = {**(storage or {}), **lexical}
    if storage:
        value['storage_parameters'] = storage
    if value:
        value['gp_extension_version'] = 1
    return value


def reconstruct(manifest_path=None, *, project_root=None):
    if manifest_path is None:
        return dict(status='ambiguous', tables={}, inputs=[], errors=['Migration order is not established; provide a manifest'])
    path = Path(manifest_path).resolve()
    root = Path(project_root).resolve() if project_root else path.parent
    manifest = read_json(path)
    errors = validate_schema(manifest, load_schemas()['migration_manifest'], 'migration_manifest')
    if errors:
        raise ArtifactInputError('; '.join(errors))
    if manifest['dialect'].lower() not in ('postgres', 'postgresql', 'greenplum'):
        return dict(status='unsupported', tables={}, inputs=[], errors=['Unsupported migration dialect'])
    migration_dialect = manifest['dialect']
    state, inputs, seen = {}, [], set()
    for relative in manifest['ordered_files']:
        source = (path.parent / relative).resolve()
        if not source.is_relative_to(root) or source in seen:
            raise ArtifactInputError(f'Migration path escapes root or repeats: {relative}')
        seen.add(source)
        data = source.read_bytes()
        reference = dict(path=source.relative_to(root).as_posix(), sha256=hashlib.sha256(data).hexdigest())
        inputs.append(reference)
        try:
            candidate = copy.deepcopy(state)
            text = data.decode('utf-8-sig')
            parse_text, constructs, _ = gp_prepare(text, migration_dialect)
            from pglast import ast
            for raw in parse(text, migration_dialect):
                attributes = gp_table_attributes(raw, constructs, parse_text) if is_greenplum(migration_dialect) and isinstance(raw.stmt, ast.CreateStmt) else None
                apply_statement(candidate, raw.stmt, attributes)
            state = candidate
        except Exception as exc:
            return dict(status='unsupported', tables=state, inputs=inputs, errors=[f'{relative}: {exc}'])
    return dict(status='resolved', tables=state, inputs=inputs, errors=[], target_revision=manifest.get('target_revision'))


def catalog(files, root, dialect='postgres'):
    """CREATE is type evidence; constraint additions after the same-file CREATE are ordered."""
    from pglast import ast
    result, functions, inputs, errors, notes = {}, {}, [], [], []
    root = Path(root).resolve()
    gp_enabled = is_greenplum(dialect)
    for path in files:
        path = Path(path).resolve()
        if not path.is_relative_to(root): raise ArtifactInputError(f'Context file outside project: {path}')
        data = path.read_bytes()
        text = data.decode('utf-8-sig')
        ref = dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(data).hexdigest())
        inputs.append(ref)
        def note(message):
            errors.append(message)
            notes.append(dict(source_ref={**ref, 'start_line': 1,
                                          'end_line': max(1, len(text.splitlines()))},
                              reason=message))
        local_tables = set()
        parse_text, gp_constructs, gp_notes = gp_prepare(text, dialect)
        for line_no, reason in gp_notes:
            errors.append(f'{ref["path"]}:{line_no}: {reason}')
            notes.append(dict(source_ref={**ref, 'start_line': line_no, 'end_line': line_no},
                              reason=reason))
        try:
            statements = list(parse(text, dialect))
        except Exception as exc:
            # A dialect-specific context file must yield a blocking diagnostic
            # instead of an uncaught crash of the whole extraction run.
            note(f'{ref["path"]}: DDL parse failed: {exc}')
            continue
        def _created_key(statement):
            node = statement.stmt
            if isinstance(node, ast.CreateStmt):
                return relation(node.relation)
            if isinstance(node, ast.ViewStmt):
                return relation(node.view)
            if isinstance(node, ast.CreateTableAsStmt):
                return relation(node.into.rel)
            return None
        recreated = {}
        for index, statement in enumerate(statements):
            key = _created_key(statement)
            if key:
                recreated.setdefault(key, []).append(index)
        for statement_index, raw in enumerate(statements):
            node = raw.stmt
            if isinstance(node, ast.CreateStmt):
                key = relation(node.relation)
                value = dict(columns=columns_of(node), source_ref={**ref, 'start_line':1,'end_line':len(data.decode('utf-8-sig').splitlines())})
                if gp_enabled:
                    try:
                        value.update(gp_table_attributes(raw, gp_constructs, parse_text))
                    except ValueError as exc:
                        note(str(exc))
                if key in result:
                    previous = result[key]
                    # A plain column-only context is weaker evidence; it cannot
                    # erase explicit GP attributes from another matching CREATE.
                    attributes = ('distributed', 'storage_parameters')
                    if previous['columns'] != value['columns'] or any(k in previous and k in value and previous[k] != value[k] for k in attributes):
                        note(f'Conflicting unordered definitions for {key}')
                    elif any(k in previous for k in attributes) and not any(k in value for k in attributes):
                        value = previous
                    elif any(k in previous and k not in value for k in attributes) and any(k in value for k in attributes):
                        note(f'Incomplete overlapping Greenplum definitions for {key}')
                result[key] = value
                local_tables.add(key)
            elif isinstance(node, ast.CreateFunctionStmt):
                key = '.'.join(p.sval for p in node.funcname)
                functions.setdefault(key, []).append(dict(signature=sql(node), source_ref={**ref,'start_line':1,'end_line':len(data.decode('utf-8-sig').splitlines())}))
            elif (isinstance(node, ast.AlterTableStmt) and relation(node.relation) in local_tables
                  and all(c.subtype.name == 'AT_AddConstraint' for c in node.cmds or ())):
                try:
                    apply_statement(result, node)
                except ValueError as exc:
                    note(str(exc))
            elif isinstance(node, ast.DropStmt):
                # Idempotent recreate template: DROP IF EXISTS is accepted only
                # when the same file re-creates every dropped relation later.
                targets = ['.'.join(p.sval for p in parts) for parts in node.objects or ()]
                if node.missing_ok and targets and all(
                        any(i > statement_index for i in recreated.get(t, ())) for t in targets):
                    continue
                note(f'Unordered migration DDL in {ref["path"]}; provide migration manifest')
            elif isinstance(node, (ast.AlterTableStmt,ast.RenameStmt,ast.DoStmt)):
                note(f'Unordered migration DDL in {ref["path"]}; provide migration manifest')
    return dict(tables=result, functions=functions, inputs=inputs, errors=errors, coverage_notes=notes)


def enrich_inventory(inventory, *, context_files=(), project_root=None, migration_manifest=None):
    root = Path(project_root).resolve() if project_root else Path.cwd()
    dialect = inventory.get('dialect', {}).get('name', 'postgres')
    migrations = reconstruct(migration_manifest, project_root=root) if migration_manifest else None
    ordered = {(root / ref['path']).resolve() for ref in migrations['inputs']} if migrations else set()
    static_context = [p for p in context_files if Path(p).resolve() not in ordered]
    context = catalog(static_context, root, dialect=dialect) if static_context else dict(tables={}, functions={}, inputs=[], errors=[])
    inventory['coverage_notes'].extend(context.get('coverage_notes', []))
    declarations = [i for i in inventory['items'] if i['kind'] == 'DECLARATION']
    for declaration in declarations:
        details = declaration['details']
        if context_files:
            details['context_tables'] = context['tables']
        if details['object_kind'] == 'migration':
            details['reconstruction'] = migrations or reconstruct()
            if details['reconstruction']['status'] != 'resolved':
                inventory['coverage_notes'].append(dict(source_ref=declaration['source_ref'], reason='Migration reconstruction is not resolved'))
        if migrations and migrations['status'] == 'resolved':
            details['reconstructed_tables'] = migrations['tables']
        for message in migrations['errors'] if migrations else []:
            inventory['coverage_notes'].append(dict(source_ref=declaration['source_ref'], reason=message))
    for item in inventory['items']:
        effects = []
        for call in item.get('calls', []):
            candidates = context['functions'].get(call, [])
            # No overload guessing. Effects remain separate from direct operation writes.
            if len(candidates) == 1:
                from sql_ast import quote, analyze
                source = candidates[0]['source_ref']
                try:
                    callee = analyze(candidates[0]['signature'], source['path'], source['sha256'],
                                     dialect=inventory.get('dialect', {}).get('name', 'postgres'),
                                     version=inventory['dialect']['version'])
                    if not callee['coverage_notes']:
                        effects.append(dict(call=call, reads=sorted({r for op in callee['items'] for r in op.get('reads',[])}),
                                            writes=sorted({r for op in callee['items'] for r in op.get('writes',[])}), source_ref=source))
                except Exception:
                    pass  # No confirmed effect is better than an invented one.
        if effects: item['details']['confirmed_call_effects'] = effects
    for ref in context['inputs'] + (migrations['inputs'] if migrations else []):
        if ref not in inventory['inputs']: inventory['inputs'].append(ref)
    known = dict(context['tables'])
    if migrations and migrations['status'] == 'resolved':
        known.update(migrations.get('tables') or {})
    for item in inventory['items']:
        details = item.get('details', {})
        if item['kind'] == 'CREATE' and details.get('columns'):
            key = details.get('reference') or details.get('table_name')
            if key:
                known[key] = dict(columns=details['columns'])
    from sql_types import expand_wildcard_outputs
    expand_wildcard_outputs(inventory, known)
    return inventory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?')
    parser.add_argument('--project-root')
    args = parser.parse_args(argv)
    try:
        result = reconstruct(args.manifest, project_root=args.project_root)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result['status']=='resolved' else 1
    except (ArtifactInputError,OSError,ValueError) as exc:
        print(json.dumps(dict(error=str(exc)),ensure_ascii=True))
        return 2


if __name__ == '__main__': raise SystemExit(main())
