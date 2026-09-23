"""Bounded Greenplum dialect adapter for the declared GP subset.

Lexical, position-preserving handling of the Greenplum extensions that
PostgreSQL's libpg_query cannot parse:

- ``EXECUTE ON {MASTER|ANY|ALL SEGMENTS}`` — function/procedure declaration attribute
  (never a body-level dynamic EXECUTE);
- ``DISTRIBUTED BY (columns)`` / ``DISTRIBUTED {RANDOMLY|REPLICATED}`` — table
  distribution clause;
- ``WITH (...)`` storage parameters before the distribution clause stay in the
  parse view and surface as PostgreSQL reloptions on ``CreateStmt``.

Invariants:
- Patterns are matched only at lexical code boundaries: strings, dollar-quoted
  bodies, comments and quoted identifiers are excluded via ``mask_sql``.
- The parse view replaces recognized ranges with spaces (newlines kept) so
  UTF-8 byte length and line mapping of the original input stay valid.
  Evidence and manifests always hash the original bytes; snippets and
  source_ref always come from the original text.
- Unrecognized text is never deleted or masked: leftover Greenplum-looking
  tokens stay in the parse view and additionally become explicit residue
  diagnostics with a localized source range.
- GRANT/REVOKE ``EXECUTE ON FUNCTION/...`` is not a GP attribute and is never
  masked or reported.
"""
import re

from sql_syntax import mask_sql, matching_paren, split_top_level

GP_DIALECTS = ('greenplum',)
EXECUTE_TARGETS = ('MASTER', 'ANY', 'ALL SEGMENTS')

_DISTRIBUTED_RE = re.compile(r'(?<![\w$])DISTRIBUTED(?![\w$])', re.I)
_EXECUTE_ON_RE = re.compile(r'(?<![\w$])EXECUTE\s+ON\s+([^\W\d][\w$]*)(?![\w$])', re.I)


def is_greenplum(dialect):
    return (dialect or '').lower() in GP_DIALECTS


def _line_of(text, offset):
    return text[:offset].count('\n') + 1


def _byte_offset(text, offset):
    return len(text[:offset].encode('utf-8'))


def _mask_bytes(data, start, end):
    """Replace [start, end) with spaces, keeping LF/CR for line mapping."""
    for i in range(start, end):
        if data[i] not in (0x0a, 0x0d):
            data[i] = 0x20


def _statement_bounds(code, position):
    start = code.rfind(';', 0, position) + 1
    end = code.find(';', position)
    return start, len(code) if end < 0 else end


def _identifier(part):
    # Preserve SQL spelling (including quoted case); reject expressions/opclasses.
    part = mask_sql(part, mask_identifiers=False)[0].strip()
    return part if re.fullmatch(r'"(?:[^"\x00]|"")+"|[^\W\d][\w$]*', part) else None


def identifier_name(part):
    return part[1:-1].replace('""', '"') if part.startswith('"') else part.lower()


def mask_greenplum(text):
    """Normalize only complete, owned GP clauses; keep every rejected byte.

    PostgreSQL parsing of a candidate proves its declaration/option boundary.
    This is syntax analysis, not a claim of GP server/version compatibility.
    """
    code = mask_sql(text, mask_identifiers=True)[0]
    constructs, notes, spans = [], [], []

    def reject(position, reason):
        notes.append((_line_of(text, position), reason))

    def accept(kind, position, end, **attributes):
        spans.append((position, end))
        constructs.append(dict(kind=kind, clause=text[position:end],
                               char_start=position, char_end=end,
                               byte_start=_byte_offset(text, position),
                               byte_end=_byte_offset(text, end),
                               start_line=_line_of(text, position),
                               end_line=_line_of(text, end - 1), **attributes))

    for match in _EXECUTE_ON_RE.finditer(code):
        position = match.start()
        start, stop = _statement_bounds(code, position)
        verb = re.match(r'\s*(\w+)', code[start:stop])
        if verb and verb.group(1).upper() in ('GRANT', 'REVOKE'):
            continue
        target = match.group(1).upper()
        end = match.end()
        if target == 'ALL':
            segments = re.match(r'\s+SEGMENTS(?![\w$])', code[end:stop], re.I)
            if segments:
                target, end = 'ALL SEGMENTS', end + segments.end()
        if target not in EXECUTE_TARGETS:
            reject(position, f'Unrecognized Greenplum EXECUTE ON target: {target}')
            continue
        if len(list(_EXECUTE_ON_RE.finditer(code, start, stop))) != 1:
            reject(position, 'Duplicate Greenplum EXECUTE ON clauses')
            continue
        try:
            from pglast import ast, parse_sql
            # COST is a PostgreSQL function option: substitution proves that
            # EXECUTE ON sits at an option boundary, not in a type/expression.
            candidate = text[start:position] + ' COST 1 ' + text[end:stop]
            parsed = parse_sql(candidate)
            if len(parsed) != 1 or not isinstance(parsed[0].stmt, ast.CreateFunctionStmt):
                raise ValueError('requires CREATE FUNCTION/PROCEDURE option')
        except Exception as exc:
            reject(position, f'Unsupported Greenplum EXECUTE ON position: {exc}')
            continue
        accept('EXECUTE_ON', position, end, target=target)

    for match in _DISTRIBUTED_RE.finditer(code):
        position = match.start()
        start, stop = _statement_bounds(code, position)
        if len(list(_DISTRIBUTED_RE.finditer(code, start, stop))) != 1:
            reject(position, 'Duplicate or ambiguous Greenplum DISTRIBUTED clauses')
            continue
        tail = re.match(r'\s+(BY|RANDOMLY|REPLICATED)(?![\w$])', code[match.end():stop], re.I)
        if not tail:
            reject(position, 'Unrecognized Greenplum DISTRIBUTED clause')
            continue
        mode, columns = tail.group(1).upper(), None
        end = match.end() + tail.end()
        try:
            if mode == 'BY':
                while end < stop and code[end].isspace():
                    end += 1
                close = matching_paren(code, end) if end < stop and code[end] == '(' else None
                if close is None or close >= stop:
                    raise ValueError('expected a parenthesized column list')
                columns = [_identifier(part) for part in split_top_level(text[end + 1:close])]
                if not columns or not all(columns) or len({identifier_name(c) for c in columns}) != len(columns):
                    raise ValueError('only distinct column identifiers are supported')
                end = close + 1
            if code[end:stop].strip():
                raise ValueError('distribution must follow storage/options and end the declaration')
            from pglast import ast, parse_sql
            parsed = parse_sql(text[start:position])
            if len(parsed) != 1 or not isinstance(parsed[0].stmt, (ast.CreateStmt, ast.CreateTableAsStmt)):
                raise ValueError('requires CREATE TABLE or CTAS')
            node = parsed[0].stmt
            if isinstance(node, ast.CreateTableAsStmt) and node.objtype.name != 'OBJECT_TABLE':
                raise ValueError('requires CREATE TABLE or CTAS')
            if columns and isinstance(node, ast.CreateStmt) and not node.inhRelations:
                declared = {c.colname for c in node.tableElts or () if isinstance(c, ast.ColumnDef)}
                if any(identifier_name(c) not in declared for c in columns):
                    raise ValueError('distribution references an undeclared column')
        except Exception as exc:
            reject(position, f'Unrecognized Greenplum DISTRIBUTED clause: {exc}')
            continue
        accept('DISTRIBUTED', position, end, mode=mode, columns=columns)

    data = bytearray(text.encode('utf-8'))
    for start, end in spans:
        _mask_bytes(data, _byte_offset(text, start), _byte_offset(text, end))
    return dict(masked_text=bytes(data).decode('utf-8'), constructs=constructs,
                notes=list(dict.fromkeys(notes)))


def prepare(text, dialect):
    """Return (parse_text, constructs, notes) with the GP adapter on demand."""
    if not is_greenplum(dialect):
        return text, [], []
    result = mask_greenplum(text)
    return result['masked_text'], result['constructs'], result['notes']


def statement_byte_span(raw, parse_text):
    """pglast 7 RawStmt locations are Unicode character offsets, not bytes."""
    start = raw.stmt_location
    stop = start + raw.stmt_len if raw.stmt_len else len(parse_text)
    return _byte_offset(parse_text, start), _byte_offset(parse_text, stop)


def attributes_in_bytes(constructs, byte_start, byte_end):
    """GP constructs whose source range lies inside the given byte window."""
    return [c for c in constructs if byte_start <= c['byte_start'] < c['byte_end'] <= byte_end]


def _distributed_record(clause):
    result = dict(mode=clause['mode'])
    if clause['columns']:
        result['columns'] = clause['columns']
    return result


def execute_on_in_bytes(constructs, byte_start, byte_end):
    values = [c['target'] for c in attributes_in_bytes(constructs, byte_start, byte_end)
              if c['kind'] == 'EXECUTE_ON']
    return values[0] if values else None


def distributed_in_bytes(constructs, byte_start, byte_end):
    values = [c for c in attributes_in_bytes(constructs, byte_start, byte_end)
              if c['kind'] == 'DISTRIBUTED']
    return _distributed_record(values[0]) if values else None


def _option_value(arg):
    if arg is None:
        return None
    from pglast import ast
    if isinstance(arg, ast.A_Const):
        value = arg.val
        if isinstance(value, ast.String):
            return value.sval
        if isinstance(value, ast.Integer):
            return str(value.ival)
        if isinstance(value, ast.Float):
            return str(value.fval)
        if isinstance(value, ast.Boolean):
            return 'true' if value.boolval else 'false'
        return None
    from sql_ast import sql
    return sql(arg).strip().strip("'") or None


def storage_parameters_of(node):
    """Table WITH storage/relop options (including GP appendonly/compress*)."""
    params = {}
    for option in getattr(node, 'options', None) or ():
        name = getattr(option, 'defname', None)
        if not name:
            continue
        namespace = getattr(option, 'defnamespace', None)
        name = f'{namespace}.{name}' if namespace else name
        if name in params:
            raise ValueError(f'Duplicate Greenplum storage parameter: {name}')
        params[name] = _option_value(getattr(option, 'arg', None))
    return params or None
