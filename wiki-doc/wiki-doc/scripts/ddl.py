"""Static DDL catalog and explicitly ordered migration reconstruction."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from artifact_schema import read_json, validate_schema, load_schemas, ArtifactInputError
from sql_ast import require_parser, columns_of, relation, type_name, sql


def parse(text):
    require_parser()
    from pglast import parse_sql
    return parse_sql(text)


def apply_statement(state, node):
    from pglast import ast
    if isinstance(node, ast.CreateStmt):
        key = relation(node.relation)
        if key in state and not node.if_not_exists:
            raise ValueError(f'Duplicate CREATE TABLE without established replacement: {key}')
        state.setdefault(key, dict(columns=columns_of(node)))
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
                if found is None and not command.missing_ok:
                    raise ValueError(f'Unknown column {key}.{command.name}')
                if found: columns.remove(found)
            elif subtype in ('AT_AlterColumnType','AT_ColumnDefault','AT_SetNotNull','AT_DropNotNull'):
                if found is None:
                    raise ValueError(f'Unknown column {key}.{command.name}')
                if subtype == 'AT_AlterColumnType': found['type'] = type_name(command.def_.typeName)
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
        elif node.renameType.name == 'OBJECT_TABLE':
            new_key = (node.relation.schemaname + '.' if node.relation.schemaname else '') + node.newname
            if new_key in state: raise ValueError(f'Rename collision: {new_key}')
            state[new_key] = state.pop(key)
        else:
            raise ValueError(f'Unsupported RENAME: {node.renameType.name}')
    elif isinstance(node, ast.DropStmt):
        if node.removeType.name != 'OBJECT_TABLE':
            raise ValueError(f'Unsupported DROP: {node.removeType.name}')
        for parts in node.objects:
            key = '.'.join(p.sval for p in parts)
            if key not in state and not node.missing_ok: raise ValueError(f'Unknown DROP target: {key}')
            state.pop(key, None)
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


def reconstruct(manifest_path=None, *, project_root=None):
    if manifest_path is None:
        return dict(status='ambiguous', tables={}, inputs=[], errors=['Migration order is not established; provide a manifest'])
    path = Path(manifest_path).resolve()
    root = Path(project_root).resolve() if project_root else path.parent
    manifest = read_json(path)
    errors = validate_schema(manifest, load_schemas()['migration_manifest'], 'migration_manifest')
    if errors:
        raise ArtifactInputError('; '.join(errors))
    if manifest['dialect'].lower() not in ('postgres','postgresql'):
        return dict(status='unsupported', tables={}, inputs=[], errors=['Unsupported migration dialect'])
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
            for raw in parse(data.decode('utf-8-sig')):
                apply_statement(candidate, raw.stmt)
            state = candidate
        except Exception as exc:
            return dict(status='unsupported', tables=state, inputs=inputs, errors=[f'{relative}: {exc}'])
    return dict(status='resolved', tables=state, inputs=inputs, errors=[], target_revision=manifest.get('target_revision'))


def catalog(files, root):
    """CREATE is type evidence; constraint additions after the same-file CREATE are ordered."""
    from pglast import ast
    result, functions, inputs, errors = {}, {}, [], []
    root = Path(root).resolve()
    for path in files:
        path = Path(path).resolve()
        if not path.is_relative_to(root): raise ArtifactInputError(f'Context file outside project: {path}')
        data = path.read_bytes()
        ref = dict(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(data).hexdigest())
        inputs.append(ref)
        local_tables = set()
        for raw in parse(data.decode('utf-8-sig')):
            node = raw.stmt
            if isinstance(node, ast.CreateStmt):
                key = relation(node.relation)
                value = dict(columns=columns_of(node), source_ref={**ref, 'start_line':1,'end_line':len(data.decode('utf-8-sig').splitlines())})
                if key in result and result[key]['columns'] != value['columns']:
                    errors.append(f'Conflicting unordered definitions for {key}')
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
                    errors.append(str(exc))
            elif isinstance(node, (ast.AlterTableStmt,ast.RenameStmt,ast.DropStmt)):
                errors.append(f'Unordered migration DDL in {ref["path"]}; provide migration manifest')
    return dict(tables=result, functions=functions, inputs=inputs, errors=errors)


def enrich_inventory(inventory, *, context_files=(), project_root=None, migration_manifest=None):
    root = Path(project_root).resolve() if project_root else Path.cwd()
    migrations = reconstruct(migration_manifest, project_root=root) if migration_manifest else None
    ordered = {(root / ref['path']).resolve() for ref in migrations['inputs']} if migrations else set()
    static_context = [p for p in context_files if Path(p).resolve() not in ordered]
    context = catalog(static_context, root) if static_context else dict(tables={}, functions={}, inputs=[], errors=[])
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
        for message in context['errors'] + (migrations['errors'] if migrations else []):
            inventory['coverage_notes'].append(dict(source_ref=declaration['source_ref'], reason=message))
    for item in inventory['items']:
        effects = []
        for call in item.get('calls', []):
            candidates = context['functions'].get(call, [])
            # No overload guessing. Effects remain separate from direct operation writes.
            if len(candidates) == 1:
                from sql_ast import analyze
                source = candidates[0]['source_ref']
                try:
                    callee = analyze(candidates[0]['signature'], source['path'], source['sha256'], version=inventory['dialect']['version'])
                    if not callee['coverage_notes']:
                        effects.append(dict(call=call, reads=sorted({r for op in callee['items'] for r in op.get('reads',[])}),
                                            writes=sorted({r for op in callee['items'] for r in op.get('writes',[])}), source_ref=source))
                except Exception:
                    pass  # No confirmed effect is better than an invented one.
        if effects: item['details']['confirmed_call_effects'] = effects
    for ref in context['inputs'] + (migrations['inputs'] if migrations else []):
        if ref not in inventory['inputs']: inventory['inputs'].append(ref)
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
