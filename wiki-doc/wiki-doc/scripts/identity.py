"""Reproducible object identity: canonical key, page slug, page_id with SHA-256 suffix.

Implements deterministic identification rules from SKILL.md and REVIEW §7.1:

- relation (table/view/materialized_view/ctas): kind + schema + name
- function/procedure: kind + schema + name + ordered input argument types
  (INOUT/VARIADIC included, OUT excluded, parameter names/DEFAULT excluded)
- migration: migration + relative_path + migration_id

Quoted identifiers preserve case; unquoted identifiers are lowercased (PostgreSQL rule).
Type normalization uses only confirmed dialect aliases (int4→integer, etc.).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote
from sql_syntax import mask_sql, split_top_level
from dataclasses import dataclass
from typing import Optional


VALID_KINDS = frozenset({
    'table', 'view', 'materialized_view', 'ctas',
    'function', 'procedure', 'migration',
    'cte', 'temp_table',
})

RELATION_KINDS = frozenset({'table', 'view', 'materialized_view', 'ctas'})
ROUTINE_KINDS = frozenset({'function', 'procedure'})

# Confirmed PostgreSQL type aliases → canonical names.
# Only mappings verified by dialect rules; no guessing of user-defined types.
PG_TYPE_ALIASES: dict[str, str] = {
    'int2': 'smallint',
    'int4': 'integer',
    'int': 'integer',
    'varbit': 'bit varying',
    'bit varying': 'bit varying',
    'bit': 'bit',
    'int8': 'bigint',
    'float4': 'real',
    'float8': 'double precision',
    'bool': 'boolean',
    'varchar': 'character varying',
    'bpchar': 'character',
    'char': 'character',
    'character varying': 'character varying',
    'character': 'character',
    'timestamp': 'timestamp without time zone',
    'timestamptz': 'timestamp with time zone',
    'timetz': 'time with time zone',
    'time': 'time without time zone',
    'decimal': 'numeric',
    'float': 'double precision',
    'bytea': 'bytea',
    'text': 'text',
    'uuid': 'uuid',
    'json': 'json',
    'jsonb': 'jsonb',
    'xml': 'xml',
    'interval': 'interval',
    'date': 'date',
    'time without time zone': 'time without time zone',
    'time with time zone': 'time with time zone',
    'timestamp without time zone': 'timestamp without time zone',
    'timestamp with time zone': 'timestamp with time zone',
    'numeric': 'numeric',
    'real': 'real',
    'double precision': 'double precision',
    'smallint': 'smallint',
    'integer': 'integer',
    'bigint': 'bigint',
    'boolean': 'boolean',
    'character varying': 'character varying',
    'character': 'character',
}


class IdentityError(ValueError):
    """Identity computation failure."""


@dataclass(frozen=True)
class ObjectDescriptor:
    """Input for identity computation."""
    kind: str
    schema: Optional[str] = None
    name: Optional[str] = None
    arg_types: Optional[list[str]] = None
    migration_path: Optional[str] = None
    migration_id: Optional[str] = None


# ---- Identifier handling ----

def _is_quoted(identifier: str) -> bool:
    """Check if identifier is double-quoted."""
    return identifier.startswith('"') and identifier.endswith('"') and len(identifier) >= 2


IDENT = r'(?:"(?:""|[^"])+"|[^\W\d][\w$]*)'


def normalize_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise IdentityError('Identifier must be a string')
    identifier = identifier.strip()
    if not identifier:
        return ''
    if not re.fullmatch(IDENT, identifier) or '\x00' in identifier:
        raise IdentityError(f'Invalid SQL identifier: {identifier!r}')
    value = identifier[1:-1].replace('""', '"') if _is_quoted(identifier) else identifier.lower()
    # PostgreSQL's default NAMEDATALEN truncates identifiers; do not guess that identity.
    if len(value.encode('utf-8')) > 63:
        raise IdentityError('Identifier exceeds the supported PostgreSQL 63-byte limit')
    return value


def normalize_type(type_name: str, *, for_identity=False) -> str:
    """Normalize PG aliases; preserve quoted/qualified user types without search_path guesses.

    Routine identity discards typmods and array bounds (CREATE FUNCTION semantics).
    """
    if not isinstance(type_name, str):
        raise IdentityError('Type must be a string')
    if not type_name.strip():
        return ''
    t = type_name.strip()
    array = re.search(r'(?:\s*\[\s*\d*\s*\])+\s*$|\s+ARRAY(?:\s*\[\s*\d*\s*\])?\s*$', t, re.I)
    suffix = '[]' if array else ''
    if array:
        t = t[:array.start()].strip()
    type_mask = mask_sql(t)[0]
    modifiers = re.search(r'\(\s*[+-]?\d+\s*(?:,\s*[+-]?\d+\s*)?\)', type_mask)
    mod = ''
    if modifiers:
        mod = re.sub(r'\s+', '', modifiers.group())
        t = t[:modifiers.start()] + t[modifiers.end():]
    syntax = mask_sql(t)[0]
    if any(ch in syntax for ch in '()[]'):
        raise IdentityError(f'Unsupported type syntax: {type_name!r}')
    # Keep quoted components distinct; never lowercase their contents.
    parts = split_top_level(t, '.')
    if len(parts) > 2 or not all(parts):
        raise IdentityError(f'Invalid qualified type: {type_name!r}')
    qualified = len(parts) == 2
    base = parts[-1].strip()
    quoted = _is_quoted(base)
    schema = normalize_identifier(parts[0]) if qualified else None
    if qualified or quoted:
        name = normalize_identifier(base)
        # Qualified or quoted names refer to catalog typname, not SQL aliases.
        # In particular pg_catalog.char / "char" is NOT SQL char (= bpchar).
        catalog_names = {
            'int2','int4','int8','float4','float8','bool','text','bytea','numeric',
            'varchar','bpchar','char','json','jsonb','uuid','date','time','timetz',
            'timestamp','timestamptz','interval','bit','varbit','money','inet','cidr',
            'macaddr','macaddr8','point','line','lseg','box','path','polygon','circle',
            'tsvector','tsquery','oid','record','anyelement','anyarray','anycompatible',
            'anycompatiblearray','regclass','regtype','regprocedure','regproc','void',
            'trigger','event_trigger','cstring','internal',
        }
        if (schema == 'pg_catalog' or not qualified) and name in catalog_names:
            normalized = 'pg_catalog.char' if name == 'char' else PG_TYPE_ALIASES.get(name, name)
            return normalized + ('' if for_identity else mod) + suffix
        def rendered(value):
            return '"' + value.replace('"', '""') + '"' if not re.fullmatch(r'[a-z_][a-z0-9_$]*', value) else value
        normalized = (rendered(schema) + '.' if qualified else '') + rendered(name)
        if for_identity and not qualified:
            raise IdentityError(f'Unresolved user type/search_path: {type_name}')
        return normalized + ('' if for_identity else mod) + suffix
    base = ' '.join(base.lower().split())
    if not re.fullmatch(r'[a-z_][a-z0-9_]*(?: [a-z_]+)*', base):
        raise IdentityError(f'Invalid type: {type_name!r}')
    normalized = PG_TYPE_ALIASES.get(base, base)
    if base == 'float' and mod:
        precision = int(mod[1:-1])
        if not 1 <= precision <= 53:
            raise IdentityError('float precision must be 1..53')
        normalized, mod = ('real' if precision <= 24 else 'double precision'), ''
    if for_identity and base not in PG_TYPE_ALIASES:
        # These builtins and pseudo-types have no alias rewriting.
        builtins = {'money','inet','cidr','macaddr','macaddr8','point','line','lseg','box','path','polygon','circle',
                    'tsvector','tsquery','oid','record','anyelement','anyarray','anycompatible','anycompatiblearray',
                    'regclass','regtype','regprocedure','regproc','void','trigger','event_trigger','cstring','internal'}
        if base not in builtins:
            raise IdentityError(f'Unresolved user type/search_path: {type_name}')
    return normalized + ('' if for_identity else mod) + suffix


def _parse_arg_types(arg_types: list[str]) -> tuple[str, ...]:
    if not isinstance(arg_types, (list, tuple)):
        raise IdentityError('Argument types must be an explicit list')
    result = []
    for arg in arg_types:
        if not isinstance(arg, str) or not arg.strip():
            raise IdentityError('Empty argument declaration')
        cleaned, _, issues = mask_sql(arg, mask_identifiers=False)
        if issues:
            raise IdentityError(issues[0][1])
        masked_identifiers = re.sub(r'"(?:""|[^"])*"', lambda m: ' ' * len(m.group()), cleaned)
        default = re.search(r'\bDEFAULT\b|=', masked_identifiers, re.I)
        declaration = cleaned[:default.start() if default else len(cleaned)].strip()
        mode = re.match(r'^(INOUT|VARIADIC|OUT|IN)\b\s*', declaration, re.I)
        if mode:
            declaration = declaration[mode.end():].strip()
        if not declaration:
            raise IdentityError('Missing argument type')
        if mode and mode.group(1).upper() == 'OUT':
            continue
        # Try a type-only declaration first; otherwise remove exactly one parameter name.
        try:
            normalized = normalize_type(declaration, for_identity=True)
        except IdentityError as original:
            name = re.match(IDENT + r'\s+', declaration)
            if not name:
                raise original
            normalized = normalize_type(declaration[name.end():], for_identity=True)
        if mode and mode.group(1).upper() == 'VARIADIC' and not normalized.endswith('[]') and normalized != 'any':
            raise IdentityError('VARIADIC requires an array type')
        result.append(normalized)
    return tuple(result)


# ---- Migration ID extraction ----

_MIGRATION_ID_RE = re.compile(r'^(\d+)[_.-]')


def _extract_migration_id(path: str) -> str:
    """Extract migration ID from filename.

    Patterns: 001_create.sql, 001-create.sql, 001.create.sql → '001'
    Falls back to stem if no numeric prefix found.
    """
    from pathlib import PurePosixPath
    stem = PurePosixPath(path).stem
    m = _MIGRATION_ID_RE.match(stem)
    if m:
        return m.group(1)
    return stem


# ---- Core identity functions ----

def _component(value, *, path=False, type_name=False):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise IdentityError('Empty or invalid key component')
    # Preserve readable ordinary keys; escape every serialization delimiter.
    return quote(value, safe='._-' + ('/' if path else '') + (' []' if type_name else ''))


def canonical_key(desc: ObjectDescriptor) -> str:
    """Compute canonical key for an object descriptor.

    Rules from SKILL.md:
    - relation: kind + schema + name
    - function/procedure: kind + schema + name + (type1,type2,...)
    - migration: migration + path + id

    Returns canonical key string like 'function+core+orders_summary+(bigint)'.
    Raises IdentityError for invalid/ambiguous input.
    """
    if not isinstance(desc, ObjectDescriptor):
        raise IdentityError('Expected ObjectDescriptor instance')
    if desc.kind not in VALID_KINDS:
        raise IdentityError(f'Unknown kind: {desc.kind!r}')

    # Migration
    if desc.kind == 'migration':
        path = desc.migration_path
        if not isinstance(path, str) or not path.strip():
            raise IdentityError('Migration requires migration_path')
        if PureWindowsPath(path).drive or PureWindowsPath(path).root or PurePosixPath(path).is_absolute():
            raise IdentityError('Migration path must be project-relative')
        parts = []
        for part in path.replace('\\', '/').split('/'):
            if part in ('', '.'):
                continue
            if part == '..':
                if not parts:
                    raise IdentityError('Migration path escapes the project')
                parts.pop()
            else:
                parts.append(part)
        path = '/'.join(parts)
        if not path:
            raise IdentityError('Migration path is empty')
        mid = desc.migration_id or _extract_migration_id(path)
        return f'migration+{_component(path, path=True)}+{_component(mid)}'

    # Routine (function/procedure)
    if desc.kind in ROUTINE_KINDS:
        if not desc.name:
            raise IdentityError(f'{desc.kind} requires name')
        schema = normalize_identifier(desc.schema) if desc.schema else ''
        if not schema:
            raise IdentityError('Unresolved schema/search_path')
        schema = _component(schema)
        name = _component(normalize_identifier(desc.name))
        arg_types = _parse_arg_types(desc.arg_types or [])
        if arg_types:
            args_str = ','.join(_component(t, type_name=True) for t in arg_types)
            return f'{desc.kind}+{schema}+{name}+({args_str})'
        return f'{desc.kind}+{schema}+{name}+()'

    # Relation (table/view/materialized_view/ctas)
    if desc.kind in RELATION_KINDS | {'cte', 'temp_table'}:
        if not desc.name:
            raise IdentityError(f'{desc.kind} requires name')
        schema = normalize_identifier(desc.schema) if desc.schema else ''
        if not schema:
            raise IdentityError('Unresolved schema/search_path')
        schema = _component(schema)
        name = _component(normalize_identifier(desc.name))
        return f'{desc.kind}+{schema}+{name}'

    raise IdentityError(f'Unhandled kind: {desc.kind!r}')


def page_slug(canonical_key: str) -> str:
    """Convert canonical key to safe filename slug.

    Rules:
    - Replace non-alphanumeric (except +) with -
    - Replace + with -
    - Collapse consecutive -
    - Strip leading/trailing -
    - Lowercase

    Example: 'function+core+orders_summary+(bigint)' → 'function-core-orders-summary-bigint'
    """
    slug = canonical_key.lower()
    # Remove parentheses content markers but keep content
    slug = slug.replace('(', '-').replace(')', '')
    # Replace + with -
    slug = slug.replace('+', '-')
    # Replace non-alphanumeric- with -
    slug = re.sub(r'[^a-z0-9-]', '-', slug)
    # Collapse consecutive -
    slug = re.sub(r'-+', '-', slug)
    # Strip leading/trailing -
    slug = slug.strip('-')
    return slug[:80].rstrip('-') or 'object'


def page_id(canonical_key: str, existing_ids: Optional[set[str]] = None, initial_hash_len: int = 12) -> str:
    """Generate page_id with SHA-256 hash suffix.

    Format: <slug>--<hash>.md
    Hash length starts at initial_hash_len (default 12); extends on collision.
    Canonical key is always hashed, even for zero-argument routines.

    Returns page_id string like 'function-core-calc--a1b2c3d4e5f6.md'.
    """
    if not isinstance(canonical_key, str) or not canonical_key.strip():
        raise IdentityError('Canonical key must be nonempty')
    if type(initial_hash_len) is not int or not 1 <= initial_hash_len <= 64:
        raise IdentityError('Hash length must be 1..64')
    existing_ids = existing_ids or set()
    slug = page_slug(canonical_key)
    key_bytes = canonical_key.encode('utf-8')
    full_hash = hashlib.sha256(key_bytes).hexdigest()

    hash_len = initial_hash_len
    while hash_len <= len(full_hash):
        candidate = f'{slug}--{full_hash[:hash_len]}.md'
        if candidate not in existing_ids:
            return candidate
        hash_len += 1

    raise IdentityError('SHA-256 collision cannot be resolved')


def detect_collision(existing_ids: set[str], new_id: str) -> bool:
    """Check if a page_id already exists in the set of existing pages."""
    return new_id in existing_ids


# ---- Convenience functions ----

def compute_identity(
    kind: str,
    schema: Optional[str] = None,
    name: Optional[str] = None,
    arg_types: Optional[list[str]] = None,
    migration_path: Optional[str] = None,
    migration_id: Optional[str] = None,
    existing_ids: Optional[set[str]] = None,
    existing_pages: Optional[dict[str, str]] = None,
    legacy_keys: Optional[dict[str, str]] = None,
) -> dict:
    """Compute full identity for an object. Returns dict with all identity fields."""
    desc = ObjectDescriptor(
        kind=kind,
        schema=schema,
        name=name,
        arg_types=arg_types,
        migration_path=migration_path,
        migration_id=migration_id,
    )
    ckey = canonical_key(desc)
    slug = page_slug(ckey)
    pid = resolve_page_id(ckey, existing_pages=existing_pages, occupied_ids=existing_ids, legacy_keys=legacy_keys)
    collision = False

    return {
        'canonical_key': ckey,
        'slug': slug,
        'page_id': pid,
        'collision': collision,
    }


def resolve_page_id(key, *, existing_pages=None, occupied_ids=None, legacy_keys=None):
    """Reuse an existing path only with an explicit path-to-key registry.

    legacy_keys is an explicit old-key -> current-key mapping, never a guess.
    """
    existing_pages = {} if existing_pages is None else existing_pages
    legacy_keys = {} if legacy_keys is None else legacy_keys
    if not isinstance(existing_pages, dict) or not isinstance(legacy_keys, dict):
        raise IdentityError('Existing pages and legacy keys must be mappings')
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in legacy_keys.items()):
        raise IdentityError('Legacy mappings must contain nonempty keys and values')
    matches = []
    normalized_pages = {}
    for path, old_key in existing_pages.items():
        if not isinstance(path, str) or not isinstance(old_key, str) or not path or not old_key:
            raise IdentityError('Page registry must map nonempty paths to nonempty keys')
        posix = PurePosixPath(path.replace('\\', '/'))
        if (PureWindowsPath(path).drive or PureWindowsPath(path).root or posix.is_absolute()
                or '..' in posix.parts or ':' in path or any(ord(c) < 32 for c in path)
                or posix.suffix != '.md'):
            raise IdentityError('Existing page path must be a relative Markdown path inside the wiki')
        normalized = posix.as_posix()
        if normalized in normalized_pages:
            raise IdentityError('Duplicate normalized path in page registry')
        normalized_pages[normalized] = old_key
        if legacy_keys.get(old_key, old_key) == key:
            matches.append(normalized)
    if len(matches) > 1:
        raise IdentityError('Ambiguous existing pages for the canonical key')
    if matches:
        return matches[0]
    return page_id(key, set(normalized_pages) | set(occupied_ids or []))


# ---- CLI ----

def main(argv=None):
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description='Compute and verify PostgreSQL object identity.')
    sub = parser.add_subparsers(dest='command')

    # compute: compute identity for an object
    compute_p = sub.add_parser('compute', help='Compute identity for an object')
    compute_p.add_argument('--kind', required=True, choices=sorted(VALID_KINDS),
                           help='Object kind')
    compute_p.add_argument('--schema', help='Schema name (unquoted)')
    compute_p.add_argument('--name', help='Object name (unquoted)')
    compute_p.add_argument('--arg-types', nargs='*', default=[],
                           help='Argument type declarations')
    compute_p.add_argument('--migration-path', help='Migration file path (relative)')
    compute_p.add_argument('--migration-id', help='Migration identifier')
    compute_p.add_argument('--registry', help='JSON path-to-key map for existing pages')
    compute_p.add_argument('--legacy-keys', help='JSON old-key-to-current-key map')
    compute_p.add_argument('--existing-ids', nargs='*', default=[],
                           help='Existing page_ids for collision check')

    # check: verify identity against existing pages
    check_p = sub.add_parser('check', help='Check identity against existing pages')
    check_p.add_argument('canonical_key', help='Canonical key to check')
    check_p.add_argument('--existing-ids', nargs='+', required=True,
                         help='Existing page_ids')

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    from artifact_schema import read_json
    try:
        if args.command == 'compute':
            existing = set(args.existing_ids)
            result = compute_identity(
                kind=args.kind,
                schema=args.schema,
                name=args.name,
                arg_types=args.arg_types,
                migration_path=args.migration_path,
                migration_id=args.migration_id,
                existing_ids=existing,
                existing_pages=read_json(args.registry) if args.registry else None,
                legacy_keys=read_json(args.legacy_keys) if args.legacy_keys else None,
            )
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0

        elif args.command == 'check':
            existing = set(args.existing_ids)
            pid = page_id(args.canonical_key)
            collision = detect_collision(existing, pid)
            print(json.dumps({
                'canonical_key': args.canonical_key,
                'page_id': pid,
                'collision': collision,
            }, indent=2, ensure_ascii=True))
            return 1 if collision else 0

    except (IdentityError, ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1

    parser.print_help()
    return 2


if __name__ == '__main__':
    import sys
    sys.exit(main())
