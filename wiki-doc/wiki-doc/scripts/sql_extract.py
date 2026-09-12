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
    'CTE', 'TEMP_TABLE',
})

DML_KEYWORDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE'}
PLPGSQL_KEYWORDS = {'PERFORM', 'CALL', 'EXECUTE'}

# Patterns for dollar-quoted strings
DOLLAR_QUOTE_RE = re.compile(r'\$([a-zA-Z_]*)\$')

# Pattern for CREATE statements
CREATE_RE = re.compile(
    r'CREATE\s+OR\s+REPLACE\s+'
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
    """Remove string literals and comments for keyword scanning."""
    result = []
    i = 0
    in_line_comment = False
    in_block_comment = 0
    in_string = False
    string_char = None

    while i < len(text):
        if in_line_comment:
            if text[i] == '\n':
                in_line_comment = False
                result.append('\n')
            i += 1
            continue
        if in_block_comment > 0:
            if text[i:i+2] == '*/':
                in_block_comment -= 1
                i += 2
                result.append('  ')
            else:
                if text[i] == '\n':
                    result.append('\n')
                i += 1
            continue
        if in_string:
            if text[i] == string_char:
                if i + 1 < len(text) and text[i+1] == string_char:
                    i += 2
                    continue
                in_string = False
                result.append(string_char)
            i += 1
            continue

        if text[i:i+2] == '--':
            in_line_comment = True
            i += 2
            continue
        if text[i:i+2] == '/*':
            in_block_comment += 1
            i += 2
            result.append('/*')
            continue
        if text[i] in ("'",):
            in_string = True
            string_char = text[i]
            result.append(text[i])
            i += 1
            continue

        result.append(text[i])
        i += 1

    return ''.join(result)


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


def _line_of(text: str, pos: int) -> int:
    return text[:pos].count('\n') + 1


def _find_dollar_quoted_ranges(text: str) -> list:
    """Find all dollar-quoted ranges in text."""
    ranges = []
    for m in DOLLAR_QUOTE_RE.finditer(text):
        tag = m.group(1)
        start = m.start()
        # Find closing
        closing_pattern = re.compile(r'\$' + re.escape(tag) + r'\$')
        close_match = closing_pattern.search(text, m.end())
        if close_match:
            ranges.append((start, close_match.end()))
    return ranges


def _is_in_dollar_quote(pos: int, ranges: list) -> bool:
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


def _counters():
    return {k: 0 for k in SUPPORTED_CONSTRUCTS}


def extract_objects(sql_text: str, file_path: str, file_sha256: str) -> list:
    """Extract CREATE objects from SQL text."""
    objects = []
    for m in CREATE_RE.finditer(sql_text):
        kind_raw = m.group(1).strip().upper()
        name = m.group(2)
        schema = None
        if '.' in name:
            schema, name = name.rsplit('.', 1)

        # Remove trailing parentheses from name
        name = name.rstrip('(').rstrip()

        kind_map = {
            'FUNCTION': 'function',
            'PROCEDURE': 'procedure',
            'VIEW': 'view',
            'MATERIALIZED VIEW': 'materialized_view',
            'TABLE': 'ctas',  # CREATE TABLE AS
        }
        kind = kind_map.get(kind_raw, 'unknown')

        start_line = _line_of(sql_text, m.start())

        # Try to find signature for functions/procedures
        signature = None
        if kind in ('function', 'procedure'):
            sig_match = SIGNATURE_RE.search(sql_text, m.start())
            if sig_match:
                sig_name = sig_match.group(1)
                if '.' in sig_name:
                    sig_name = sig_name.rsplit('.', 1)[1]
                sig_name = sig_name.rstrip('(').rstrip()
                signature = f"{sig_name}({sig_match.group(2).strip()})"

        # Find end of statement (simplified: look for $$; or next CREATE)
        end_line = start_line
        dollar_end = sql_text.find('$$;', m.end())
        if dollar_end != -1:
            end_line = _line_of(sql_text, dollar_end + 2)
        else:
            next_create = CREATE_RE.search(sql_text, m.end())
            if next_create:
                end_line = _line_of(sql_text, next_create.start()) - 1
            else:
                end_line = _line_of(sql_text, len(sql_text))

        objects.append(ObjectInfo(
            name=name,
            kind=kind,
            schema=schema,
            signature=signature,
            start_line=start_line,
            end_line=end_line,
        ))

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

    return items, notes


def _normalize_ref(ref: str) -> str:
    """Normalize a table/function reference."""
    ref = ref.strip()
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
    end_pos = min(start_pos + 2000, len(text))
    segment = text[start_pos:end_pos]

    for m in FROM_RE.finditer(segment):
        ref = _normalize_ref(m.group(1))
        if ref.upper() not in ('SELECT', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'UNION', 'VALUES'):
            refs.append(ref)

    for m in JOIN_RE.finditer(segment):
        refs.append(_normalize_ref(m.group(1)))

    return refs


def extract_inventory(sql_text: str, file_path: str, file_sha256: str,
                      dialect: str = 'postgres', version: str = 'unknown',
                      documented_subjects: list = None) -> dict:
    """Full inventory extraction from SQL text.

    Returns dict matching inventory.schema.json v2.
    """
    objects = extract_objects(sql_text, file_path, file_sha256)
    all_items = []
    all_notes = []
    counters = _counters()

    for obj in objects:
        # Find the body of this object by searching for the CREATE statement
        # Use a flexible pattern that matches the object name with optional schema
        if obj.schema:
            name_pattern = re.escape(obj.schema) + r'\.' + re.escape(obj.name)
        else:
            name_pattern = r'(?:\w+\.)?' + re.escape(obj.name)
        
        create_pattern = re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|VIEW|MATERIALIZED\s+VIEW|TABLE)\s+'
            + name_pattern + r'[\s\(]',
            re.IGNORECASE
        )
        create_match = create_pattern.search(sql_text)
        if create_match:
            dollar_tag = '$$'
            body_start = sql_text.find(dollar_tag, create_match.end())
            if body_start != -1:
                body_end = sql_text.find(dollar_tag + ';', body_start + 2)
                if body_end != -1:
                    body = sql_text[body_start + 2:body_end]
                else:
                    body = sql_text[body_start + 2:]
            else:
                # No dollar-quoted body (e.g., view) — use until next CREATE or end
                next_create = CREATE_RE.search(sql_text, create_match.end())
                if next_create:
                    body = sql_text[create_match.start():next_create.start()]
                else:
                    body = sql_text[create_match.start():]
        else:
            body = sql_text

        scope = f"{obj.kind}+{obj.schema or 'public'}+{obj.name}"
        if obj.signature:
            scope = f"{obj.kind}+{obj.schema or 'public'}+{obj.signature}"

        items, notes = extract_operations(body, file_path, file_sha256, scope, counters)
        all_items.extend(items)
        all_notes.extend(notes)

    # Detect unsupported constructs
    upper_text = sql_text.upper()
    for keyword in ('TRIGGER', 'INDEX', 'CONSTRAINT', 'GRANT', 'REVOKE'):
        if re.search(r'\b' + keyword + r'\b', upper_text):
            all_notes.append(CoverageNote(
                source_ref=SourceRef(file_path, 1, _line_of(sql_text, len(sql_text)), file_sha256),
                reason=f'{keyword} found but not fully supported by extractor',
            ))

    result = {
        'schema_version': 2,
        'run_id': '00000000-0000-0000-0000-000000000000',  # placeholder
        'dialect': {'name': dialect, 'version': version},
        'items': [_item_to_dict(i) for i in all_items],
        'coverage_notes': [_note_to_dict(n) for n in all_notes],
        'inputs': [{'path': file_path, 'sha256': file_sha256}],
        'documented_subjects': documented_subjects or [o.name for o in objects],
    }

    return result


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

    args = parser.parse_args()

    try:
        path = Path(args.sql_file)
        sql_text = path.read_text(encoding='utf-8')
        sha = sha256_file(path)

        result = extract_inventory(
            sql_text, str(path), sha,
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
