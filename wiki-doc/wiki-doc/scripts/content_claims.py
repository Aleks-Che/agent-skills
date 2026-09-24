"""Bounded visible-assertion checks; not semantic acceptance of D12.

Target dialect/version does not prove compatibility. Date comparisons do not
prove a window's business rationale. Marked examples are parsed, never executed.
See references/content-claims.md for supported scope and remaining review.
"""
import datetime
import re

from markdown_it import MarkdownIt
from pglast import ast, parse_sql
from pglast.error import Error as ParseError
from identity import normalize_type
from sql_ast import sql, type_name

_VERSION = re.compile(r'\b(PostgreSQL|Greenplum|GP)\s*(?:version\s*|версии?\s*)?'
                      r'(>=|<=|>|<|=)?\s*(\d+(?:\.\d+)*)\b', re.I)
_COMPAT = re.compile(r'compatib\w*|совместим\w*', re.I)
_TARGET = re.compile(r'\b(?:target|server|dialect|целев\w*|сервер\w*|диалект\w*)\b', re.I)
_CAVEAT = re.compile(r'\b(?:not (?:established|verified|confirmed|supported|compatible|limited|guaranteed)|'
                     r'unverified|unconfirmed|unestablished|'
                     r'no (?:evidence|proof) of compatibility|'
                     r'не (?:подтвержден\w*|установлен\w*|проверен\w*|ограничен\w*|гарантир\w*)|'
                     r'совместимость неизвестна)\b', re.I)
_EXAMPLE = re.compile(r'\b(?:example|usage|invocation|call(?: example)?|пример(?: вызова)?|вызов)\s*:', re.I)
_EXAMPLE_HEADING = re.compile(r'\b(?:examples?|usage|invocations?|примеры?|вызов)\b', re.I)
_FACT_ID = re.compile(r'^(?:obj|def|op|col|formula|cond|unknown)_\w+$')
_WINDOW = re.compile(r'\b(?:history (?:covers|is limited|before|after)|records (?:before|after|since)|'
                     r'(?:processing |data )?window|окн\w*|истори\w*|запис\w*)\b', re.I)
_BOUNDARY = re.compile(r'\b(before|after|until|since|from|prior to|limited to|up to|'
                       r'no later than|no earlier than|до|после|начиная с|не ранее|не позднее)\s+'
                       r'(?:\w+\s+){0,3}?(\d{4}(?:-\d{2}(?:-\d{2})?)?)\b', re.I)
_OPS = {'before': '<', 'prior to': '<', 'до': '<', 'after': '>', 'после': '>',
        'since': '>=', 'from': '>=', 'no earlier than': '>=', 'начиная с': '>=', 'не ранее': '>=',
        'up to': '<=', 'no later than': '<=', 'не позднее': '<='}


def _inline_text(token):
    return ''.join(' ' if t.type in ('softbreak', 'hardbreak') else t.content
                   for t in token.children or () if t.type in ('text', 'code_inline', 'softbreak', 'hardbreak'))


def _page_parts(page):
    """Parse rendered CommonMark, preserving ordinary tables but not claims/comments."""
    prose, examples = [], []
    tokens = MarkdownIt('commonmark').enable('table').parse(page)
    example_heading = pending = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == 'heading_open':
            example_heading = bool(_EXAMPLE_HEADING.search(_inline_text(tokens[i + 1])))
            pending = False
        if token.type == 'tr_open':
            end = next(j for j in range(i + 1, len(tokens)) if tokens[j].type == 'tr_close')
            cells = [t for t in tokens[i:end] if t.type == 'inline']
            if len(cells) == 3 and _FACT_ID.fullmatch(_inline_text(cells[0])):
                i = end + 1
                continue
        if token.type == 'inline':
            text = _inline_text(token)
            prose.append(text)
            label = _EXAMPLE.search(text)
            in_example = example_heading or bool(label)
            if in_example:
                codes = [t.content for t in token.children or () if t.type == 'code_inline']
                if codes:
                    examples.extend(codes)
                elif label and text[label.end():].strip():
                    examples.append(text[label.end():].strip())
            pending = in_example
        elif token.type in ('fence', 'code_block'):
            if (pending or example_heading) and token.info.strip().lower() in ('', 'sql', 'postgresql', 'postgres', 'plpgsql'):
                examples.append(token.content)
            pending = False
        i += 1
    return prose, examples


def _clauses(prose):
    for block in prose:
        yield from re.split(r'(?<=[.!?;])\s+|\bbut\b|\bоднако\b', block, flags=re.I)


def _compatibility(facts, prose):
    errors = []
    dialect = facts.get('dialect', {})
    for clause in _clauses(prose):
        if _CAVEAT.search(clause):
            continue
        for match in _VERSION.finditer(clause):
            product, comparator, version = match.groups()
            if _COMPAT.search(clause):
                errors.append(f'Unverified compatibility claim: "{match[0]}"; target dialect/version is not compatibility evidence')
            elif _TARGET.search(clause):
                name = 'postgres' if product.lower() == 'postgresql' else 'greenplum'
                actual = str(dialect.get('version', 'unknown'))
                if (name != dialect.get('name') or comparator not in (None, '=')
                        or actual == 'unknown' or actual != version):
                    errors.append(f'Unverified target version claim: "{match[0]}"; facts establish {dialect.get("name")} {actual}')
    return errors


def _date_literal(node):
    if isinstance(node, ast.TypeCast) and normalize_type(type_name(node.typeName)) == 'date':
        node = node.arg
    if isinstance(node, ast.A_Const) and isinstance(node.val, ast.String):
        try:
            return datetime.date.fromisoformat(node.val.sval).isoformat()
        except ValueError:
            pass
    return None


def _bounds(node):
    """Only direct comparisons; OR/NOT do not imply an individual child bound."""
    if isinstance(node, ast.BoolExpr) and node.boolop.name == 'AND_EXPR':
        return set().union(*(_bounds(n) for n in node.args))
    if isinstance(node, ast.A_Expr) and node.kind.name == 'AEXPR_OP':
        operator = ''.join(p.sval for p in node.name)
        date, column = _date_literal(node.rexpr), node.lexpr
        if date is None:
            date, column = _date_literal(node.lexpr), node.rexpr
            operator = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '=': '='}.get(operator)
        if date and operator in ('<', '<=', '>', '>=', '=') and isinstance(column, ast.ColumnRef):
            return {(sql(column), operator, date)}
    return set()


def _windows(facts, prose):
    errors, conditions = [], {}
    for cond in facts.get('conditions', []):
        try:
            node = parse_sql('SELECT ' + cond['expression'])[0].stmt.targetList[0].val
            conditions[cond['id']] = _bounds(node)
        except (ParseError, ValueError, KeyError, IndexError):
            conditions[cond.get('id')] = set()
    for clause in _clauses(prose):
        if not _WINDOW.search(clause) or _CAVEAT.search(clause):
            continue
        refs = re.findall(r'\bcond_\w+\b', clause)
        selected = [conditions.get(cid, set()) for cid in refs] if refs else list(conditions.values())
        known = set().union(*selected)
        for match in _BOUNDARY.finditer(clause):
            operator = _OPS.get(match[1].lower())
            # A year/month is not a fixed date; "until"/"limited to" is ambiguous.
            if not any(op == operator and date == match[2] for _, op, date in known):
                errors.append(f'Window boundary claim "{match[0]}" is not established by a matching SQL comparison')
    return errors


def _argument_type(node):
    if isinstance(node, ast.A_Const):
        if node.isnull:
            return '?null'
        return {ast.String: '?string', ast.Integer: 'integer', ast.Float: 'numeric', ast.Boolean: 'boolean'}.get(type(node.val))
    if isinstance(node, ast.TypeCast):
        target = normalize_type(type_name(node.typeName))
        # Type identity alone cannot establish arbitrary cast validity.
        if not isinstance(node.arg, ast.A_Const):
            return None
        if node.arg.isnull:
            return target
        value = node.arg.val
        raw = getattr(value, 'sval', getattr(value, 'ival', getattr(value, 'fval', None)))
        if target in ('text', 'character varying', 'character'):
            return target
        if target in ('smallint', 'integer', 'bigint') and re.fullmatch(r'[+-]?\d+', str(raw)):
            bits = {'smallint': 16, 'integer': 32, 'bigint': 64}[target]
            if -(2 ** (bits - 1)) <= int(raw) < 2 ** (bits - 1):
                return target
        if target in ('numeric', 'real', 'double precision') and re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', str(raw)):
            return target
        if target == 'boolean' and (isinstance(value, ast.Boolean) or str(raw).lower() in ('true', 'false', 't', 'f', 'yes', 'no', 'on', 'off', '1', '0')):
            return target
        if target == 'date' and _date_literal(node):
            return target
    return None


def _match_arguments(call, obj):
    if any(getattr(call, field, None) for field in
           ('agg_star', 'agg_distinct', 'agg_within_group', 'agg_order', 'agg_filter', 'over')):
        return None  # Aggregate/window modifiers are not proved by a routine signature.
    parameters = obj.get('parameters')
    if parameters is None:
        return None
    params = [p for p in parameters if p.get('mode') not in ('o', 't')]
    assigned, named, score = {}, False, 0
    for index, argument in enumerate(call.args or ()):
        if isinstance(argument, ast.NamedArgExpr):
            named = True
            position = next((i for i, p in enumerate(params) if p.get('name') == argument.name), None)
            argument = argument.arg
        else:
            if named:
                return None
            position = min(index, len(params) - 1) if params and params[-1].get('mode') == 'v' else index
        if position is None or position >= len(params) or position < 0:
            return None
        param = params[position]
        variadic = param.get('mode') == 'v'
        if position in assigned and (not variadic or named or call.func_variadic):
            return None
        assigned[position] = True
        target = normalize_type(param['type'])
        if variadic and not call.func_variadic:
            if named or not target.endswith('[]'):
                return None
            target = target[:-2]
        actual = _argument_type(argument)
        if actual == target:
            continue
        if actual == '?null' or (actual == '?string' and target in ('text', 'character varying', 'character')):
            score += 2
        elif actual == 'integer' and target in ('bigint', 'numeric', 'real', 'double precision'):
            score += 1
        else:
            return None
    if any(i not in assigned and p.get('default') is None and p.get('mode') != 'v' for i, p in enumerate(params)):
        return None
    if call.func_variadic and (not params or params[-1].get('mode') != 'v'):
        return None
    return score


def _examples(facts, examples):
    errors = []
    for code in examples:
        if not re.search(r'\b(?:SELECT|CALL)\b|[\w"]\s*\(', code, re.I):
            continue
        try:
            statements = parse_sql(code if re.match(r'\s*(?:SELECT|CALL)\b', code, re.I) else 'SELECT ' + code)
        except ParseError:
            errors.append('Wrong call example: SQL syntax cannot be parsed')
            continue
        for raw in statements:
            node = raw.stmt
            if isinstance(node, ast.CallStmt):
                calls, kind = [node.funccall], 'procedure'
            elif isinstance(node, ast.SelectStmt) and not node.op and not any(getattr(node, field, None) for field in
                    ('fromClause', 'whereClause', 'groupClause', 'havingClause', 'windowClause', 'sortClause',
                     'limitCount', 'limitOffset', 'withClause', 'valuesLists', 'intoClause', 'lockingClause')):
                calls = [t.val for t in node.targetList or ()]
                kind = 'function'
            else:
                errors.append('Wrong call example: only standalone SELECT/CALL routine expressions are checked')
                continue
            for call in calls:
                if not isinstance(call, ast.FuncCall):
                    errors.append('Wrong call example: expected a direct routine call')
                    continue
                parts = [p.sval for p in call.funcname]
                candidates = [o for o in facts.get('objects', []) if o.get('kind') == kind and o.get('name') == parts[-1]
                              and (len(parts) == 1 or len(parts) == 2 and o.get('schema') == parts[0])]
                if len(parts) == 1 and len({o.get('schema') for o in candidates}) > 1:
                    errors.append('Wrong call example: unqualified routine requires an established schema')
                    continue
                scores = [s for o in candidates if (s := _match_arguments(call, o)) is not None]
                if not scores or scores.count(min(scores)) != 1:
                    errors.append(f'Wrong call example: {".".join(parts)} schema/signature/argument types are not uniquely established; use a declared routine and explicit supported casts')
    return errors


def check_compatibility_claims(facts, page):
    return _compatibility(facts, _page_parts(page)[0])


def check_window_claims(facts, page):
    return _windows(facts, _page_parts(page)[0])


def check_example_claims(facts, page):
    return _examples(facts, _page_parts(page)[1])


def check_content_claims(facts, page):
    prose, examples = _page_parts(page)
    return _compatibility(facts, prose) + _windows(facts, prose) + _examples(facts, examples)
