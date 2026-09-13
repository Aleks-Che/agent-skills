"""Position-preserving lexical helpers for the supported PostgreSQL subset."""
import re

DOLLAR_QUOTE_RE = re.compile(r'\$(?:[a-zA-Z_][a-zA-Z_0-9]*)?\$')

def mask_sql(text, mask_identifiers=True):
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
            if quote == '"' and mask_identifiers:
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
        if kind and (mask_identifiers or text[start] != '"'):
            masked[start:i] = ['\n' if ch == '\n' else ' ' for ch in text[start:i]]
    return ''.join(masked), ranges, issues



def matching_paren(text, start):
    """Find a closing parenthesis in already masked SQL."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    raise ValueError('Unclosed parenthesis')


def split_top_level(text, delimiter=','):
    """Split SQL lists without splitting typmods, defaults, or quoted tokens."""
    masked, _, issues = mask_sql(text, mask_identifiers=False)
    if issues:
        raise ValueError(issues[0][1])
    # Quoted identifiers can contain punctuation, too.
    masked = re.sub(r'"(?:""|[^"])*"', lambda m: ' ' * len(m.group()), masked)
    depth, start, parts = 0, 0, []
    for i, ch in enumerate(masked):
        if ch in '([': depth += 1
        elif ch in ')]': depth -= 1
        elif ch == delimiter and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        if depth < 0:
            raise ValueError('Unbalanced SQL list')
    if depth:
        raise ValueError('Unbalanced SQL list')
    parts.append(text[start:].strip())
    return parts
