"""Independent SQL/DDL extractor for inventory and validation plan generation.

This module extracts operations, dependencies, and structure from SQL/DDL
without reading facts.json, draft, or expected test results.

Recognized constructs (some require blocking coverage_notes; not a full AST parser):
- CREATE FUNCTION/PROCEDURE (with dollar-quoted bodies)
- CREATE VIEW / CREATE MATERIALIZED VIEW / CREATE TABLE AS (CTAS)
- DML: SELECT, INSERT, UPDATE, DELETE, MERGE
- PL/pgSQL: PERFORM, CALL, EXECUTE
- CTE (WITH ... AS)
- FROM / JOIN dependencies
- Basic signature extraction

Limitations (explicitly documented):
- No full SQL grammar; unsupported syntax produces coverage_notes
- Dynamic SQL names are not resolved
- Complex nested subqueries may be partially captured
- Dialect-specific constructs beyond basic PostgreSQL are not guaranteed
"""
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from identity import ObjectDescriptor, canonical_key, IdentityError, _parse_arg_types


SUPPORTED_CONSTRUCTS = frozenset({
    'CREATE_FUNCTION', 'CREATE_PROCEDURE', 'CREATE_VIEW',
    'CREATE_MATERIALIZED_VIEW', 'CREATE_TABLE_AS',
    'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE',
    'PERFORM', 'CALL', 'EXECUTE',
    'RETURN',
    'CTE', 'TEMP_TABLE',
    'CREATE_INDEX', 'CREATE_TRIGGER',
    'GRANT', 'REVOKE',
    'ALTER', 'DROP', 'TRUNCATE',
})

DML_KEYWORDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE'}
PLPGSQL_KEYWORDS = {'PERFORM', 'CALL', 'EXECUTE'}

# Patterns for dollar-quoted strings
DOLLAR_QUOTE_RE = re.compile(r'\$(?:[a-zA-Z_][a-zA-Z_0-9]*)?\$')

# Pattern for CREATE statements
REFERENCE_PATTERN = r'[a-zA-Z_][\w$]*(?:\s*\.\s*[a-zA-Z_][\w$]*)?'
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?'
    r'(FUNCTION|PROCEDURE|VIEW|MATERIALIZED\s+VIEW|TABLE)\s+'
    r'(' + REFERENCE_PATTERN + ')',
    re.IGNORECASE
)

# Pattern for signature extraction
SIGNATURE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+'
    r'([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)\s*\(([^)]*)\)',
    re.IGNORECASE | re.DOTALL
)

# Patterns for DML
SELECT_RE = re.compile(r'\bSELECT\b', re.IGNORECASE)
INSERT_RE = re.compile(r'\bINSERT\s+INTO\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
UPDATE_RE = re.compile(r'\bUPDATE\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
DELETE_RE = re.compile(r'\bDELETE\s+FROM\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
MERGE_RE = re.compile(r'\bMERGE\s+INTO\s+(\S+(?:\.\S+)?)', re.IGNORECASE)

# Pattern for USING in MERGE
MERGE_USING_RE = re.compile(
    r'\bMERGE\s+INTO\s+\S+\s+AS\s+\w+\s+USING\s+(\S+(?:\.\S+)?)',
    re.IGNORECASE
)

# Patterns for PL/pgSQL
PERFORM_RE = re.compile(r'\bPERFORM\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
CALL_RE = re.compile(r'\bCALL\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
EXECUTE_RE = re.compile(r'\bEXECUTE\s+', re.IGNORECASE)

# Patterns for FROM/JOIN
FROM_RE = re.compile(r'\bFROM\s+(?:ONLY\s+)?(' + REFERENCE_PATTERN + ')', re.IGNORECASE)
JOIN_RE = re.compile(r'\bJOIN\s+(?:ONLY\s+)?(' + REFERENCE_PATTERN + ')', re.IGNORECASE)

# Pattern for CTE
CTE_RE = re.compile(r'\bWITH\s+(\w+)\s+AS\s*\(', re.IGNORECASE)

# Pattern for temp table
TEMP_TABLE_RE = re.compile(
    r'CREATE\s+(?:LOCAL\s+)?TEMP(?:ORARY)?\s+TABLE\s+(\w+)',
    re.IGNORECASE
)

# Pattern for RETURNING
RETURNING_RE = re.compile(r'\bRETURNING\b', re.IGNORECASE)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _strip_dollar_quoted(text: str, quote_tag: str) -> str:
    """Remove dollar-quoted body, keeping outer structure."""
    pattern = re.compile(
        r'\$' + re.escape(quote_tag) + r'\$.*?\$' + re.escape(quote_tag) + r'\$',
        re.DOTALL
    )
    return pattern.sub('/* DOLLAR_QUOTED_BODY */', text)


def _strip_strings_and_comments(text: str) -> str:
    """Mask literals/comments without changing any byte-independent text offsets."""
    return _mask_sql(text)[0]


from sql_syntax import mask_sql as _mask_sql, matching_paren, split_top_level


@dataclass
class SourceRef:
    path: str
    start_line: int
    end_line: int
    sha256: str


@dataclass
class InventoryItem:
    anchor: dict  # {object_or_scope, construct, ordinal}
    kind: str
    source_ref: SourceRef
    reads: list = field(default_factory=list)
    writes: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class CoverageNote:
    source_ref: SourceRef
    reason: str


@dataclass
class ObjectInfo:
    name: str
    kind: str  # function, procedure, view, materialized_view, table, ctas
    schema: Optional[str] = None
    signature: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    start: int = 0
    end: int = 0
    body_start: int = 0
    body_end: int = 0
    input_types: Optional[list] = None
    identity_error: Optional[str] = None


def _line_of(text: str, pos: int) -> int:
    return text[:pos].count('\n') + 1


def _find_dollar_quoted_ranges(text: str) -> list:
    """Find all dollar-quoted ranges in text."""
    return [(start, end) for start, end, _, _ in _mask_sql(text)[1]]


def _is_in_dollar_quote(pos: int, ranges: list) -> bool:
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


def _counters():
    return {k: 0 for k in SUPPORTED_CONSTRUCTS}


def extract_objects(sql_text: str, file_path: str, file_sha256: str) -> list:
    """Read declarations and their exact signature/body boundaries."""
    cleaned, ranges, _ = _mask_sql(sql_text)
    objects = []
    for m in CREATE_RE.finditer(cleaned):
        semi = cleaned.find(';', m.end())
        end = semi + 1 if semi >= 0 else len(sql_text)
        full_name = re.sub(r'\s+', '', m.group(2)).lower()
        schema, name = full_name.rsplit('.', 1) if '.' in full_name else (None, full_name)
        kind = '_'.join(m.group(1).lower().split())
        if kind == 'table' and re.search(r'\bAS\s+(?:SELECT|WITH)\b', cleaned[m.end():end], re.I):
            kind = 'ctas'
        body_start, body_end = m.start(), end
        signature, input_types, identity_error = None, None, None
        if kind in ('function', 'procedure'):
            args_start = m.end() + len(cleaned[m.end():end]) - len(cleaned[m.end():end].lstrip())
            try:
                if args_start >= end or cleaned[args_start] != '(':
                    raise IdentityError('Missing routine signature')
                args_end = matching_paren(cleaned, args_start)
                if args_end >= end:
                    raise IdentityError('Signature extends outside declaration')
                signature = name + sql_text[args_start:args_end + 1]
                args = sql_text[args_start + 1:args_end]
                input_types = list(_parse_arg_types(split_top_level(args) if args.strip() else []))
            except (ValueError, IdentityError) as exc:
                identity_error = str(exc)
            bodies = [(bs, be) for ds, de, bs, be in ranges if m.end() <= ds < end
                      and re.search(r'\bAS\s*$', cleaned[m.end():ds], re.I)]
            if bodies:
                body_start, body_end = bodies[0]
        objects.append(ObjectInfo(name, kind, schema, signature,
                                  _line_of(sql_text, m.start()),
                                  _line_of(sql_text, max(m.start(), end - 1)),
                                  m.start(), end, body_start, body_end, input_types, identity_error))
    return objects


def _clauses(text):
    """Top-level keyword spans in masked SQL (parenthesized expressions are opaque)."""
    depth, result = 0, []
    for m in re.finditer(r'\(|\)|[a-zA-Z_][\w$]*', text):
        if m.group() == '(':
            depth += 1
        elif m.group() == ')':
            depth -= 1
        elif depth == 0:
            result.append((m.group().upper(), m.start(), m.end()))
    return result


def _expressions(raw, kind):
    """Concrete output/assignment expressions and predicate clauses for bounded SQL."""
    cleaned = _strip_strings_and_comments(raw)
    words = _clauses(cleaned)
    boundary = {'FROM','WHERE','GROUP','HAVING','ORDER','LIMIT','OFFSET','RETURNING','INTO','USING',
                'JOIN','LEFT','RIGHT','FULL','INNER','CROSS','WHEN','UNION','WINDOW','FETCH'}
    formulas, conditions, columns = [], [], []
    formula_pattern = r'[+/%-]|[\w.)]\s*\*\s*[\w.(]|\b(?:SUM|AVG|COUNT|MIN|MAX|CASE|OVER)\b|[a-zA-Z_]\w*\s*\('
    for index, (word, start, end) in enumerate(words):
        next_clause = next((pos for token, pos, _ in words[index + 1:] if token in boundary or token == 'ON'), len(raw))
        expression = raw[end:next_clause].strip().rstrip(';').strip()
        if word in ('WHERE','ON','HAVING') and expression:
            conditions.append(expression)
        if word == 'SELECT' or (kind == 'UPDATE' and word == 'SET') or (kind == 'RETURN' and word == 'RETURN'):
            if word == 'SELECT':
                expression = re.sub(r'^ALL\s+', '', expression, flags=re.I)
            for term in split_top_level(expression) if expression else []:
                term_cleaned = _strip_strings_and_comments(term)
                alias = re.search(r'\s+AS\s+([a-zA-Z_]\w*)\s*$', term_cleaned, re.I)
                expr = term[:alias.start()].strip() if alias else term
                if word == 'SET':
                    assignment = re.match(r'[^=]+=\s*(.*)', expr, re.S)
                    expr = assignment.group(1) if assignment else expr
                if re.search(formula_pattern, _strip_strings_and_comments(expr), re.I):
                    formulas.append(expr)
                if word == 'SELECT':
                    bare = re.fullmatch(r'(?:[a-zA-Z_]\w*\.)?([a-zA-Z_]\w*)', expr)
                    columns.append({'name': alias.group(1).lower() if alias else (bare.group(1).lower() if bare else None),
                                    'expression': expr})
    return formulas, conditions, columns


def extract_operations(sql_text: str, file_path: str, file_sha256: str,
                       scope: str = '', counters: dict = None) -> tuple:
    counters = {} if counters is None else counters
    items, notes = [], []
    cleaned, _, lexical_issues = _mask_sql(sql_text)
    identifier = REFERENCE_PATTERN
    patterns = {
        'SELECT': r'\bSELECT\b', 'INSERT': r'\bINSERT\s+INTO\s+(?:ONLY\s+)?(' + identifier + ')',
        'UPDATE': r'\bUPDATE\s+(?:ONLY\s+)?(' + identifier + ')',
        'DELETE': r'\bDELETE\s+FROM\s+(?:ONLY\s+)?(' + identifier + ')',
        'MERGE': r'\bMERGE\s+INTO\s+(' + identifier + ')',
        'PERFORM': r'\bPERFORM\b', 'CALL': r'\bCALL\b', 'EXECUTE': r'\bEXECUTE\b',
        'RETURN': r'\bRETURN\b', 'CTE': r'\bWITH\s+(' + identifier + r')\s+AS\s*\(',
        'TEMP_TABLE': r'\bCREATE\s+(?:LOCAL\s+)?TEMP(?:ORARY)?\s+TABLE\s+(' + identifier + ')',
        'CREATE_INDEX': r'\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(' + identifier + ')',
        'CREATE_TRIGGER': r'\bCREATE\s+(?:OR\s+REPLACE\s+)?CONSTRAINT\s+TRIGGER\s+(\w+)\b|\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(\w+)\b',
        'GRANT': r'\bGRANT\b',
        'REVOKE': r'\bREVOKE\b',
    }
    matches = sorted((m.start(), kind, m) for kind, pattern in patterns.items()
                     for m in re.finditer(pattern, cleaned, re.I))
    def add_note(start, stop, reason):
        notes.append(CoverageNote(SourceRef(file_path, _line_of(sql_text, start),
                    _line_of(sql_text, max(start, stop - 1)), file_sha256), reason))
    for start, kind, m in matches:
        statement_start = cleaned.rfind(';', 0, start) + 1
        # MERGE/ON CONFLICT actions are part of the parent statement, not tables named SET.
        if kind == 'UPDATE' and (m.group(1).upper() == 'SET' or re.search(r'\bMERGE\b|\bON\s+CONFLICT\b', cleaned[statement_start:start], re.I)):
            continue
        stop = cleaned.find(';', start)
        stop = len(cleaned) if stop < 0 else stop + 1
        segment, raw = cleaned[start:stop], sql_text[start:stop]
        counters[kind] = counters.get(kind, 0) + 1
        item = InventoryItem({'object_or_scope': scope, 'construct': kind, 'ordinal': counters[kind]},
                             kind, SourceRef(file_path, _line_of(sql_text, start),
                                             _line_of(sql_text, max(start, stop - 1)), file_sha256))
        if kind in ('INSERT','UPDATE','DELETE','MERGE'):
            item.writes = [_normalize_ref(m.group(1))]
        if kind in DML_KEYWORDS | {'PERFORM','RETURN'}:
            item.reads = _extract_from_joins(cleaned, start)
            if kind == 'DELETE':
                item.reads = [ref for ref in item.reads if ref not in item.writes]
            if kind in ('DELETE','MERGE'):
                using = re.search(r'\bUSING\s+(' + identifier + ')', segment, re.I)
                if using:
                    item.reads.append(_normalize_ref(using.group(1)))
        if kind in DML_KEYWORDS | {'RETURN','PERFORM','CALL'}:
            try:
                formulas, conditions, columns = _expressions(raw, kind)
            except ValueError as exc:
                formulas, conditions, columns = [], [], []
                add_note(start, stop, str(exc))
            item.details.update(formulas=formulas, conditions=conditions,
                                has_formula=bool(formulas), has_condition=bool(conditions))
            if columns:
                item.details['columns'] = columns
            item.calls = [_normalize_ref(name) for name in re.findall(r'\b([a-zA-Z_][\w$]*\s*\.\s*[a-zA-Z_][\w$]*)\s*\(', segment)]
            item.calls = [name for name in item.calls if name not in item.writes]
            builtins = {'sum','avg','count','min','max','now','coalesce','nullif','greatest','least','round','abs',
                        'lower','upper','trim','substring','extract','date_trunc','to_char','to_date','row_number',
                        'cast','in','values','over','filter','numeric','decimal','varchar','character','timestamp','time'}
            for call in re.findall(r'(?<![\w.])([a-zA-Z_]\w*)\s*\(', segment):
                if call.lower() not in builtins and call.lower() not in item.writes:
                    add_note(start, stop, f'Unresolved unqualified call: {call}')
            # Comma joins and clauses not covered by this scanner must not silently lose sources.
            for word, begin, end in _clauses(segment):
                if word in ('FROM','USING'):
                    tail = segment[end:]
                    tail = re.split(r'\b(?:WHERE|GROUP|ORDER|HAVING|RETURNING|WHEN)\b', tail, maxsplit=1, flags=re.I)[0]
                    if len(split_top_level(tail.rstrip(';'))) > 1:
                        add_note(start, stop, 'Comma-separated sources require scoped analysis')
            if re.search(r'\bSELECT\b.*?\bINTO\b|\bON\s+CONFLICT\b|\bDISTINCT\s+ON\b|\bWINDOW\b', segment, re.I | re.S):
                add_note(start, stop, 'SELECT INTO / ON CONFLICT / named window requires scoped analysis')
        if kind == 'CTE':
            item.details['cte_name'] = m.group(1)
        if kind == 'TEMP_TABLE':
            item.details['table_name'] = m.group(1)
            add_note(start, stop, 'Temporary table columns/lifetime require scoped analysis')
        if kind == 'CREATE_INDEX':
            item.details['index_name'] = m.group(1)
            item.details['table_name'] = _normalize_ref(m.group(2))
            item.writes = [item.details['table_name']]
        if kind == 'CREATE_TRIGGER':
            trig_name = m.group(1) or m.group(2)
            item.details['trigger_name'] = trig_name
            table_match = re.search(r'\bON\s+(' + identifier + r')', segment, re.I)
            if table_match:
                item.details['table_name'] = _normalize_ref(table_match.group(1))
                item.writes = [item.details['table_name']]
            func_match = re.search(r'\bEXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(' + identifier + r')', segment, re.I)
            if func_match:
                item.calls = [_normalize_ref(func_match.group(1))]
        if kind in ('GRANT', 'REVOKE'):
            on_match = re.search(r'\bON\s+(?:TABLE\s+)?(' + identifier + r')', segment, re.I)
            if on_match:
                item.reads = [_normalize_ref(on_match.group(1))]
        if kind == 'EXECUTE':
            item.details.update(dynamic=True, unresolved_parts=['runtime target or parameter values'])
            template = re.match(r"EXECUTE\s+(?:format\s*\(\s*)?'((?:''|[^'])*)'", raw, re.I | re.S)
            if template:
                command = template.group(1).replace("''", "'")
                item.details['template'] = command
                first = re.match(r'\s*(SELECT|INSERT|UPDATE|DELETE|TRUNCATE)\b', command, re.I)
                item.details['command_kind'] = first.group(1).upper() if first else 'UNKNOWN'
                # Only a known single command template supports an honest runtime unknown.
                template_code = _strip_strings_and_comments(command).strip().rstrip(';')
                if not first or ';' in template_code or re.search(r'%(?!%|(?:[1-9]\d*\$)?[IL])', command):
                    add_note(start, stop, 'Unanalyzed dynamic SQL template')
            else:
                add_note(start, stop, 'Unanalyzed dynamic SQL expression')
        item.details['has_date_boundary'] = bool(re.search(r'\b(?:DATE|TIMESTAMP|INTERVAL)\b', segment, re.I))
        for field in ('reads','writes','calls'):
            setattr(item, field, list(dict.fromkeys(getattr(item, field))))
            for target in getattr(item, field):
                if '.' not in target:
                    add_note(start, stop, f'Unresolved {field} target/search_path: {target}')
        items.append(item)
    for pos, reason in lexical_issues:
        add_note(pos, pos + 1, reason)
    for m in re.finditer(r'\b(?:MERGE|WITH|TRIGGER|INDEX|CONSTRAINT|GRANT|REVOKE|ALTER|DROP|TRUNCATE|IF|LOOP|EXCEPTION|DECLARE|COPY|DO|CASE)\b', cleaned, re.I):
        add_note(m.start(), m.end(), f'{m.group().upper()} requires PostgreSQL AST analysis')
    for m in re.finditer(r'\(\s*SELECT\b|\b(?:UNION|INTERSECT|EXCEPT)\b', cleaned, re.I):
        add_note(m.start(), m.end(), 'Nested/set query requires scoped analysis beyond P0')
    for m in re.finditer(r'[^;]+(?:;|$)', cleaned):
        fragment = re.sub(r'^\s*(?:BEGIN\b\s*)?', '', m.group(), flags=re.I).strip()
        if not fragment or re.fullmatch(r'END\s*;?', fragment, re.I):
            continue
        recognized = any(m.start() <= start < m.end() for start, _, _ in matches)
        if not recognized or not re.match(r'(?:SELECT|INSERT|UPDATE|DELETE|MERGE|PERFORM|CALL|EXECUTE|CREATE|WITH|RETURN)\b', fragment, re.I):
            add_note(m.start(), m.end(), f'Unanalyzed executable fragment: {fragment[:60]}')
    return items, notes


def _normalize_ref(ref: str) -> str:
    """Normalize a table/function reference."""
    ref = re.sub(r'\s+', '', ref.strip().rstrip(';,')).lower()
    # Remove trailing parenthesis and everything after it
    if '(' in ref:
        ref = ref[:ref.index('(')]
    # Remove aliases
    parts = ref.split()
    if len(parts) > 1 and parts[0].upper() not in ('AS', 'ON', 'WHERE', 'SET', 'VALUES'):
        return parts[0]
    return ref


def _extract_from_joins(text: str, start_pos: int) -> list:
    """Extract FROM and JOIN references after a given position (within same statement)."""
    refs = []
    # Look ahead within a reasonable range
    semi = text.find(';', start_pos)
    end_pos = len(text) if semi < 0 else semi
    segment = text[start_pos:end_pos]

    for m in FROM_RE.finditer(segment):
        ref = _normalize_ref(m.group(1))
        if ref.upper() not in ('SELECT', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'UNION', 'VALUES'):
            refs.append(ref)

    for m in JOIN_RE.finditer(segment):
        refs.append(_normalize_ref(m.group(1)))

    return refs


def object_scope(obj):
    try:
        if obj.identity_error:
            raise IdentityError(obj.identity_error)
        if obj.kind in ('function', 'procedure') and obj.input_types is None:
            raise IdentityError('Unresolved signature')
        return canonical_key(ObjectDescriptor(obj.kind, obj.schema, obj.name, obj.input_types))
    except IdentityError:
        return f"unresolved+{obj.kind}+{obj.schema or '?'}+{obj.name}@{obj.start_line}"


def _extract_inventory_legacy(sql_text: str, file_path: str, file_sha256: str,
                      dialect: str = 'postgres', version: str = 'unknown',
                      documented_subjects: list = None) -> dict:
    objects = extract_objects(sql_text, file_path, file_sha256)
    all_items, all_notes = [], []
    selected = objects
    if documented_subjects:
        selected = []
        for subject in documented_subjects:
            matches = [o for o in objects if subject in
                       (o.name, f'{o.schema}.{o.name}', object_scope(o))]
            if len(matches) != 1:
                raise ValueError(f'Subject {subject!r}: expected one declaration, found {len(matches)}')
            if matches[0] not in selected:
                selected.append(matches[0])

    def note(start, end, reason):
        all_notes.append(CoverageNote(SourceRef(file_path, _line_of(sql_text, start),
                         _line_of(sql_text, max(start, end - 1)), file_sha256), reason))

    cleaned, _, lexical_issues = _mask_sql(sql_text)
    for pos, reason in lexical_issues:
        if not documented_subjects or any(o.start <= pos < o.end for o in selected):
            note(pos, pos + 1, reason)
    if dialect.lower() not in ('postgres', 'postgresql'):
        note(0, len(sql_text), f'Unsupported dialect: {dialect}')
    for obj in selected:
        scope = object_scope(obj)
        ref = SourceRef(file_path, obj.start_line, obj.end_line, file_sha256)
        all_items.append(InventoryItem(
            {'object_or_scope': scope, 'construct': 'DECLARATION', 'ordinal': 1},
            'DECLARATION', ref, details={'object_kind': obj.kind, 'name': obj.name,
                                      'schema': obj.schema, 'signature': obj.signature,
                                      'input_types': obj.input_types, 'canonical_key': scope}))
        if scope.startswith('unresolved+'):
            note(obj.start, obj.end, obj.identity_error or 'Unresolved object identity')
        if obj.schema is None:
            note(obj.start, obj.end, 'Unresolved schema/search_path for declaration')
        if obj.kind == 'table':
            note(obj.start, obj.end, 'CREATE TABLE column/constraint analysis is outside the P0 subset')
            continue
        if obj.kind in ('function', 'procedure') and obj.body_start == obj.start:
            note(obj.start, obj.end, 'Routine requires an AS dollar-quoted body in the P0 subset')
            continue
        if obj.kind in ('function', 'procedure'):
            header = cleaned[obj.start:obj.body_start] + cleaned[obj.body_end:obj.end]
            language = re.search(r'\bLANGUAGE\s+(\w+)', header, re.I)
            if not language or language.group(1).lower() not in ('sql', 'plpgsql'):
                note(obj.start, obj.end, 'Unsupported or unresolved routine language')
            if re.search(r'\bRETURNS\s+TABLE\b', header, re.I):
                all_items[-1].details['returns_table'] = True
                note(obj.start, obj.end, 'RETURNS TABLE columns require structural analysis')
        body = sql_text[obj.body_start:obj.body_end]
        items, notes = extract_operations(body, file_path, file_sha256, scope)
        offset = _line_of(sql_text, obj.body_start) - 1
        for item in items + notes:
            item.source_ref.start_line += offset
            item.source_ref.end_line += offset
        if obj.kind in ('view', 'materialized_view', 'ctas'):
            header_match = CREATE_RE.match(cleaned, obj.start)
            if header_match and cleaned[header_match.end():obj.end].lstrip().startswith('('):
                note(obj.start, obj.end, 'Explicit relation output column lists require structural analysis')
            outputs = next((i.details.get('columns', []) for i in items if i.kind == 'SELECT'), [])
            all_items[-1].details['output_columns'] = outputs
            if any(o['expression'] == '*' or o['expression'].endswith('.*') for o in outputs):
                note(obj.start, obj.end, 'Wildcard output columns require DDL context analysis')
        all_items.extend(items)
        all_notes.extend(notes)
        if not items:
            note(obj.body_start, obj.body_end, 'No supported executable operation found')
    if not objects:
        items, notes = extract_operations(sql_text, file_path, file_sha256, file_path)
        all_items.extend(items)
        all_notes.extend(notes)
        note(0, len(sql_text), 'No supported object declaration; migration/raw statement scope requires further analysis')
    else:
        remainder = list(cleaned)
        for obj in objects:
            remainder[obj.start:obj.end] = ' ' * (obj.end - obj.start)
        if ''.join(remainder).strip(' ;\r\n\t'):
            note(0, len(sql_text), 'Unanalyzed top-level statements outside declarations')
    if not all_items:
        all_items.append(InventoryItem({'object_or_scope': file_path, 'construct': 'ANALYSIS_GAP', 'ordinal': 1},
                                      'ANALYSIS_GAP', SourceRef(file_path, 1, 1, file_sha256)))
    return {
        'schema_version': 2,
        'run_id': '00000000-0000-0000-0000-000000000000',
        'dialect': {'name': dialect, 'version': version},
        'items': [_item_to_dict(i) for i in all_items],
        'coverage_notes': [_note_to_dict(n) for n in all_notes],
        'inputs': [{'path': file_path, 'sha256': file_sha256}],
        'documented_subjects': [object_scope(o) for o in selected] or [file_path],
    }


def extract_inventory(sql_text, file_path, file_sha256, dialect='postgres', version='unknown', documented_subjects=None):
    """Native syntax analysis with stable P0 anchors for the previously supported subset."""
    from sql_ast import analyze, require_parser
    require_parser()
    try:
        native = analyze(sql_text, file_path, file_sha256, dialect, version, documented_subjects)
    except Exception as exc:
        # A parse failure must never turn into a successful regex-only validation.
        fallback = _extract_inventory_legacy(sql_text, file_path, file_sha256, dialect, version, documented_subjects)
        fallback['coverage_notes'].append({'source_ref': {'path': file_path, 'sha256': file_sha256,
            'start_line': 1, 'end_line': max(1, len(sql_text.splitlines()))}, 'reason': f'PostgreSQL AST analysis failed: {exc}'})
        return fallback
    try:
        previous = _extract_inventory_legacy(sql_text, file_path, file_sha256, dialect, version, documented_subjects)
    except ValueError:
        return native
    if not native['coverage_notes'] and not previous['coverage_notes'] and not any(i['kind'] in ('EXECUTE','CTAS','CTE') for i in native['items']):
        declarations = {i['details']['canonical_key']: i['details'] for i in native['items'] if i['kind'] == 'DECLARATION'}
        for item in previous['items']:
            if item['kind'] == 'DECLARATION':
                info = declarations.get(item['details']['canonical_key'], {})
                for key in ('parameters', 'returns', 'returns_table', 'analysis', 'volatility'):
                    if key in info: item['details'][key] = info[key]
        return previous
    return native


def _item_to_dict(item: InventoryItem) -> dict:
    d = {
        'anchor': item.anchor,
        'kind': item.kind,
        'source_ref': asdict(item.source_ref),
    }
    if item.reads:
        d['reads'] = item.reads
    if item.writes:
        d['writes'] = item.writes
    if item.calls:
        d['calls'] = item.calls
    if item.details:
        d['details'] = item.details
    return d


def _note_to_dict(note: CoverageNote) -> dict:
    return {
        'source_ref': asdict(note.source_ref),
        'reason': note.reason,
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sql_file', help='Path to SQL file')
    parser.add_argument('--dialect', default='postgres', help='SQL dialect')
    parser.add_argument('--version', default='unknown', help='DB version')
    parser.add_argument('--subjects', nargs='*', help='Documented subjects (object names)')
    parser.add_argument('--run-id', help='UUID for this run')
    parser.add_argument('--project-root', type=Path, help='Store paths relative to the SQL project root')
    parser.add_argument('--context', type=Path, nargs='*', default=[])
    parser.add_argument('--migration-manifest', type=Path)
    parser.add_argument('-o', '--output', type=Path, help='Write UTF-8 inventory JSON instead of stdout')

    args = parser.parse_args(argv)

    try:
        path = Path(args.sql_file)
        sql_text = path.read_text(encoding='utf-8-sig')
        sha = sha256_file(path)

        source_path = path.resolve().relative_to(args.project_root.resolve()).as_posix() if args.project_root else str(path)
        result = extract_inventory(
            sql_text, source_path, sha,
            dialect=args.dialect,
            version=args.version,
            documented_subjects=args.subjects,
        )

        if args.run_id:
            result['run_id'] = args.run_id

        from ddl import enrich_inventory
        result = enrich_inventory(result, context_files=args.context, project_root=args.project_root,
                                  migration_manifest=args.migration_manifest)

        payload = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(payload + '\n', encoding='utf-8')
        else:
            print(payload)

    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
