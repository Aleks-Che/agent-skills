"""Independent SQL/DDL extractor for inventory and validation plan generation.

This module extracts operations, dependencies, and structure from SQL/DDL
without reading facts.json, draft, or expected test results.

Supported subset (regex-based, not a full AST parser):
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


SUPPORTED_CONSTRUCTS = frozenset({
    'CREATE_FUNCTION', 'CREATE_PROCEDURE', 'CREATE_VIEW',
    'CREATE_MATERIALIZED_VIEW', 'CREATE_TABLE_AS',
    'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE',
    'PERFORM', 'CALL', 'EXECUTE',
    'RETURN',
    'CTE', 'TEMP_TABLE',
})

DML_KEYWORDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE'}
PLPGSQL_KEYWORDS = {'PERFORM', 'CALL', 'EXECUTE'}

# Patterns for dollar-quoted strings
DOLLAR_QUOTE_RE = re.compile(r'\$(?:[a-zA-Z_][a-zA-Z_0-9]*)?\$')

# Pattern for CREATE statements
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?'
    r'(FUNCTION|PROCEDURE|VIEW|MATERIALIZED\s+VIEW|TABLE)\s+'
    r'([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)',
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
FROM_RE = re.compile(r'\bFROM\s+(\S+(?:\.\S+)?)', re.IGNORECASE)
JOIN_RE = re.compile(r'\bJOIN\s+(\S+(?:\.\S+)?)', re.IGNORECASE)

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


def _mask_sql(text):
    masked, ranges, issues = list(text), [], []
    i = 0
    while i < len(text):
        start, kind = i, None
        if text.startswith('--', i):
            kind = 'comment'
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            kind, depth, i = 'comment', 1, i + 2
            while i < len(text) and depth:
                if text.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif text.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                issues.append((start, 'Unterminated block comment'))
        elif text[i] in "'\"":
            kind, quote, i = 'quoted', text[i], i + 1
            escaped = start > 0 and text[start - 1] in 'eE' and (start < 2 or not text[start - 2].isalnum())
            closed = False
            while i < len(text):
                if escaped and text[i] == '\\':
                    i += 2
                elif text[i] == quote:
                    if i + 1 < len(text) and text[i + 1] == quote:
                        i += 2
                    else:
                        i += 1
                        closed = True
                        break
                else:
                    i += 1
            i = min(i, len(text))
            if not closed:
                issues.append((start, 'Unterminated quoted token'))
            if quote == '"':
                issues.append((start, 'Quoted identifiers are outside the P0 subset'))
        else:
            tag = DOLLAR_QUOTE_RE.match(text, i)
            if tag:
                kind = 'dollar'
                end = text.find(tag.group(), tag.end())
                i = len(text) if end < 0 else end + len(tag.group())
                if end < 0:
                    issues.append((start, 'Unterminated dollar quote'))
                ranges.append((start, i, tag.end(), end if end >= 0 else len(text)))
            else:
                i += 1
        if kind:
            masked[start:i] = ['\n' if ch == '\n' else ' ' for ch in text[start:i]]
    return ''.join(masked), ranges, issues


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
    """Read top-level declarations with literal-aware statement boundaries."""
    cleaned, ranges, _ = _mask_sql(sql_text)
    objects = []
    for m in CREATE_RE.finditer(cleaned):
        semi = cleaned.find(';', m.end())
        end = semi + 1 if semi >= 0 else len(sql_text)
        full_name = m.group(2).lower()
        schema, name = full_name.rsplit('.', 1) if '.' in full_name else (None, full_name)
        kind = m.group(1).lower().replace(' ', '_')
        if kind == 'table' and re.search(r'\bAS\s+(?:SELECT|WITH)\b', cleaned[m.end():end], re.I):
            kind = 'ctas'
        body_start, body_end = m.start(), end
        signature = None
        if kind in ('function', 'procedure'):
            sig = SIGNATURE_RE.match(cleaned, m.start())
            if sig:
                signature = name + '(' + sql_text[sig.start(2):sig.end(2)].strip() + ')'
            bodies = [(bs, be) for ds, de, bs, be in ranges if m.end() <= ds < end
                      and re.search(r'\bAS\s*$', cleaned[m.end():ds], re.I)]
            if bodies:
                body_start, body_end = bodies[0]
        objects.append(ObjectInfo(name, kind, schema, signature,
                                  _line_of(sql_text, m.start()),
                                  _line_of(sql_text, max(m.start(), end - 1)),
                                  m.start(), end, body_start, body_end))
    return objects


def extract_operations(sql_text: str, file_path: str, file_sha256: str,
                       scope: str = '', counters: dict = None) -> tuple:
    """Extract DML/PLpgSQL operations from SQL body.

    Returns (items: list[InventoryItem], notes: list[CoverageNote])
    """
    if counters is None:
        counters = _counters()

    items = []
    notes = []

    dollar_ranges = _find_dollar_quoted_ranges(sql_text)
    cleaned = _strip_strings_and_comments(sql_text)

    # SELECT
    for m in SELECT_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        counters['SELECT'] += 1
        reads = _extract_from_joins(cleaned, m.start())
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'SELECT', 'ordinal': counters['SELECT']},
            kind='SELECT',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            reads=reads,
        ))

    # INSERT
    for m in INSERT_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['INSERT'] += 1
        reads = _extract_from_joins(cleaned, m.end())
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'INSERT', 'ordinal': counters['INSERT']},
            kind='INSERT',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            writes=[_normalize_ref(target)],
            reads=reads,
        ))

    # UPDATE
    for m in UPDATE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['UPDATE'] += 1
        reads = _extract_from_joins(cleaned, m.end())
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'UPDATE', 'ordinal': counters['UPDATE']},
            kind='UPDATE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            writes=[_normalize_ref(target)],
            reads=reads,
        ))

    # DELETE
    for m in DELETE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['DELETE'] += 1
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'DELETE', 'ordinal': counters['DELETE']},
            kind='DELETE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            writes=[_normalize_ref(target)],
        ))

    # MERGE
    for m in MERGE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['MERGE'] += 1
        reads = []
        using_m = MERGE_USING_RE.search(cleaned, m.start())
        if using_m:
            reads.append(_normalize_ref(using_m.group(1)))
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'MERGE', 'ordinal': counters['MERGE']},
            kind='MERGE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            writes=[_normalize_ref(target)],
            reads=reads,
        ))

    # PERFORM
    for m in PERFORM_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['PERFORM'] += 1
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'PERFORM', 'ordinal': counters['PERFORM']},
            kind='PERFORM',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            calls=[_normalize_ref(target)],
        ))

    # CALL
    for m in CALL_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        target = m.group(1)
        counters['CALL'] += 1
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'CALL', 'ordinal': counters['CALL']},
            kind='CALL',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            calls=[_normalize_ref(target)],
        ))

    # EXECUTE (dynamic)
    for m in EXECUTE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        line = _line_of(sql_text, m.start())
        counters['EXECUTE'] += 1
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'EXECUTE', 'ordinal': counters['EXECUTE']},
            kind='EXECUTE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            details={'dynamic': True, 'unresolved_parts': ['dynamic target']},
        ))

    # CTE
    for m in CTE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        cte_name = m.group(1)
        counters['CTE'] += 1
        line = _line_of(sql_text, m.start())
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'CTE', 'ordinal': counters['CTE']},
            kind='CTE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            details={'cte_name': cte_name},
        ))

    # TEMP TABLE
    for m in TEMP_TABLE_RE.finditer(cleaned):
        if _is_in_dollar_quote(m.start(), dollar_ranges):
            continue
        table_name = m.group(1)
        counters['TEMP_TABLE'] += 1
        line = _line_of(sql_text, m.start())
        items.append(InventoryItem(
            anchor={'object_or_scope': scope, 'construct': 'TEMP_TABLE', 'ordinal': counters['TEMP_TABLE']},
            kind='TEMP_TABLE',
            source_ref=SourceRef(file_path, line, line, file_sha256),
            details={'table_name': table_name},
        ))

    for m in re.finditer(r'\bRETURN\b', cleaned, re.I):
        counters['RETURN'] = counters.get('RETURN', 0) + 1
        line = _line_of(sql_text, m.start())
        items.append(InventoryItem({'object_or_scope': scope, 'construct': 'RETURN', 'ordinal': counters['RETURN']},
                                   'RETURN', SourceRef(file_path, line, line, file_sha256)))

    # Keep source spans and statement features independent of writer-created IDs.
    for item in items:
        # Locate the occurrence by its line and construct, then stop at this statement.
        matches = list(re.finditer(r'\b' + re.escape(item.kind.replace('_TABLE', '')) + r'\b', cleaned, re.I))
        match = next((m for m in matches if _line_of(sql_text, m.start()) == item.source_ref.start_line), None)
        if match:
            stop = cleaned.find(';', match.start())
            stop = len(cleaned) if stop < 0 else stop + 1
            segment = cleaned[match.start():stop]
            item.details['profile_features'] = sorted(set(re.findall(
                r'\b(?:ckr_uup_queue|ckr_uup_products_contract_val|ckr_uup_org_st|product_group|coef_up|consent|row_number|init_type_oper|start_oper|add_log|end_oper)\b', segment, re.I)))
            item.source_ref.end_line = _line_of(sql_text, max(match.start(), stop - 1))
            if item.kind in DML_KEYWORDS | {'RETURN', 'PERFORM', 'CALL'}:
                item.details['has_formula'] = bool(re.search(r'[+/-]|[\w.)]\s*\*\s*[\w.(]|\b(?:SUM|AVG|COUNT|MIN|MAX|CASE|OVER)\b', segment, re.I))
                item.details['has_condition'] = bool(re.search(r'\b(?:WHERE|ON|CASE|HAVING)\b', segment, re.I))
                item.calls = list(dict.fromkeys(item.calls + re.findall(r'\b([a-zA-Z_]\w*\.[a-zA-Z_]\w*)\s*\(', segment)))
                # INSERT target(column-list) is not a function invocation.
                item.calls = [name for name in item.calls if name not in item.writes]
                builtins = {'sum', 'avg', 'count', 'min', 'max', 'now', 'coalesce', 'nullif',
                            'greatest', 'least', 'round', 'abs', 'lower', 'upper', 'trim',
                            'substring', 'extract', 'date_trunc', 'to_char', 'to_date',
                            'row_number', 'cast', 'in', 'values', 'over', 'filter'}
                for call in re.findall(r'(?<![\w.])([a-zA-Z_]\w*)\s*\(', segment):
                    if call.lower() not in builtins and call not in item.writes:
                        notes.append(CoverageNote(item.source_ref, f'Unresolved unqualified call: {call}'))
                if re.search(r'(?<![\w.])(?:now|coalesce|nullif|greatest|least|round|abs|date_trunc|to_char|to_date)\s*\(', segment, re.I):
                    item.details['has_formula'] = True
            for field in ('reads', 'writes', 'calls'):
                for target in getattr(item, field):
                    if '.' not in target:
                        notes.append(CoverageNote(item.source_ref, f'Unresolved {field} target/search_path: {target}'))
            # Bare EXECUTE variables cannot establish the operation template.
            if item.kind == 'EXECUTE':
                raw = sql_text[match.start():stop]
                if not re.match(r"EXECUTE\s+(?:format\s*\(\s*)?'(?:SELECT|INSERT|UPDATE|DELETE|TRUNCATE)\b", raw, re.I):
                    notes.append(CoverageNote(item.source_ref, 'Unanalyzed dynamic SQL expression'))

    for pos, reason in _mask_sql(sql_text)[2]:
        notes.append(CoverageNote(SourceRef(file_path, _line_of(sql_text, pos),
                                           _line_of(sql_text, pos), file_sha256), reason))
    # This is a deliberately bounded scanner. Complex grammar remains a blocking gap.
    for match in re.finditer(r'\b(?:MERGE|WITH|TRIGGER|INDEX|CONSTRAINT|GRANT|REVOKE|ALTER|DROP|TRUNCATE|IF|LOOP|EXCEPTION|DECLARE|COPY|DO)\b', cleaned, re.I):
        line = _line_of(sql_text, match.start())
        notes.append(CoverageNote(SourceRef(file_path, line, line, file_sha256),
                                  f'{match.group().upper()} requires analysis beyond the P0 subset'))
    for match in re.finditer(r'\(\s*SELECT\b|\b(?:UNION|INTERSECT|EXCEPT)\b', cleaned, re.I):
        line = _line_of(sql_text, match.start())
        notes.append(CoverageNote(SourceRef(file_path, line, line, file_sha256), 'Nested/set query requires scoped analysis beyond P0'))
    for match in re.finditer(r'[^;]+(?:;|$)', cleaned):
        fragment = re.sub(r'^\s*(?:BEGIN\b\s*)?', '', match.group(), flags=re.I).strip()
        if not fragment or re.fullmatch(r'END\s*;?', fragment, re.I):
            continue
        if not re.match(r'(?:SELECT|INSERT|UPDATE|DELETE|MERGE|PERFORM|CALL|EXECUTE|CREATE|WITH|RETURN)\b', fragment, re.I):
            line = _line_of(sql_text, match.start())
            notes.append(CoverageNote(SourceRef(file_path, line, line, file_sha256),
                                      f'Unanalyzed executable fragment: {fragment[:60]}'))
    return items, notes


def _normalize_ref(ref: str) -> str:
    """Normalize a table/function reference."""
    ref = ref.strip().rstrip(';,')
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
    return f"{obj.kind}+{obj.schema or '?'}+{obj.signature or obj.name}"


def extract_inventory(sql_text: str, file_path: str, file_sha256: str,
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
                                      'schema': obj.schema, 'signature': obj.signature}))
        if obj.schema is None:
            note(obj.start, obj.end, 'Unresolved schema/search_path for declaration')
        if obj.kind == 'table':
            note(obj.start, obj.end, 'CREATE TABLE column/constraint analysis is outside the P0 subset')
            continue
        if obj.kind in ('function', 'procedure') and obj.body_start == obj.start:
            note(obj.start, obj.end, 'Routine requires an AS dollar-quoted body in the P0 subset')
            continue
        body = sql_text[obj.body_start:obj.body_end]
        items, notes = extract_operations(body, file_path, file_sha256, scope)
        offset = _line_of(sql_text, obj.body_start) - 1
        for item in items + notes:
            item.source_ref.start_line += offset
            item.source_ref.end_line += offset
        all_items.extend(items)
        all_notes.extend(notes)
        if not items:
            note(obj.body_start, obj.body_end, 'No supported executable operation found')
    if not objects:
        items, notes = extract_operations(sql_text, file_path, file_sha256, file_path)
        all_items.extend(items)
        all_notes.extend(notes)
        note(0, len(sql_text), 'No supported object declaration; migration/raw statement scope requires further analysis')
    elif not documented_subjects:
        remainder = list(cleaned)
        for obj in objects:
            remainder[obj.start:obj.end] = ' ' * (obj.end - obj.start)
        if ''.join(remainder).strip(' ;\r\n\t'):
            note(0, len(sql_text), 'Unanalyzed top-level statements outside declarations')
    return {
        'schema_version': 2,
        'run_id': '00000000-0000-0000-0000-000000000000',
        'dialect': {'name': dialect, 'version': version},
        'items': [_item_to_dict(i) for i in all_items],
        'coverage_notes': [_note_to_dict(n) for n in all_notes],
        'inputs': [{'path': file_path, 'sha256': file_sha256}],
        'documented_subjects': [object_scope(o) for o in selected] or [file_path],
    }


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


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sql_file', help='Path to SQL file')
    parser.add_argument('--dialect', default='postgres', help='SQL dialect')
    parser.add_argument('--version', default='unknown', help='DB version')
    parser.add_argument('--subjects', nargs='*', help='Documented subjects (object names)')
    parser.add_argument('--run-id', help='UUID for this run')
    parser.add_argument('--project-root', type=Path, help='Store paths relative to the SQL project root')

    args = parser.parse_args()

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

        print(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(main())
