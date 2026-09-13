"""Check Markdown coverage and local links; never authorize publication alone.

Usage: coverage_gate.py --bundle RUN --wiki-root WIKI --json
Or: coverage_gate.py coverage.json draft.md facts.json --plan validation_plan.json
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from artifact_schema import (ArtifactInputError, FACT_ARRAYS, read_json, validate_artifacts,
                             validate_schema, load_schemas, check_run_consistency, check_unique_ids)
from check_policy import load_policy, derive_section_checks, PolicyError, validate_policy

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None


class CoverageError(ValueError):
    pass


class CoverageResult:
    def __init__(self):
        self.errors, self.warnings = [], []
        self.covered_facts, self.uncovered_facts = set(), set()
        self.missing_sections, self.empty_sections = [], []
        self.duplicate_sections, self.invalid_fragments = [], []
        self.fragments = {}
        self.link_snapshots = {}
        self.input_error = False

    @property
    def valid(self):
        return not self.errors

    def to_dict(self):
        total = len(self.covered_facts | self.uncovered_facts)
        return dict(valid=self.valid, errors=self.errors, warnings=self.warnings,
                    covered_facts=sorted(self.covered_facts), uncovered_facts=sorted(self.uncovered_facts),
                    missing_sections=sorted(set(self.missing_sections)),
                    empty_sections=sorted(set(self.empty_sections)),
                    duplicate_sections=sorted(set(self.duplicate_sections)),
                    invalid_fragments=sorted(set(self.invalid_fragments)), fragments=self.fragments,
                    coverage_percent=100 * len(self.covered_facts) / total if total else None,
                    input_error=self.input_error, publication_authorized=False,
                    semantic_validation_required=True)


ID = r'[\w][\w.-]*'
MARKER = re.compile(r'\s*<!--\s*wiki-doc:(section|fragment)\s+(' + ID + r')\s*-->\s*\Z')
ANCHOR = re.compile(r'\s*<a\s+id=[\"\x27](' + ID + r')[\"\x27]\s*>\s*</a>\s*\Z', re.I)


def _text(children):
    return ''.join(t.content for t in children or []
                   if t.type in ('text', 'code_inline', 'softbreak', 'hardbreak', 'image'))


def _substance(text):
    # Punctuation, comments, link definitions and empty layout are not coverage.
    return any(c.isalnum() for c in text)


class MarkdownDocument:
    """CommonMark + tables, explicit IDs and bounded hidden fragment markers."""
    def __init__(self, text):
        if MarkdownIt is None:
            raise ArtifactInputError('Install dependencies: python -m pip install -r requirements.txt')
        self.text = text
        self.sections, self.fragments, self.duplicates, self.errors = {}, {}, [], []
        self.content, self.links = [], []
        tokens = MarkdownIt('commonmark').enable(['table', 'strikethrough']).parse(text)
        headings, markers, blocks = [], [], []
        implicit_counts = {}
        for i, token in enumerate(tokens):
            if token.map and token.nesting == 1 and token.type in (
                    'paragraph_open', 'table_open', 'bullet_list_open', 'ordered_list_open', 'blockquote_open'):
                blocks.append(tuple(token.map))
            if token.type == 'heading_open' and token.level == 0:
                title = _text(tokens[i + 1].children).strip()
                explicit = re.search(r'\s*\{#(' + ID + r')\}\s*$', title)
                if explicit:
                    sid = explicit.group(1)
                else:
                    slug = re.sub(r'[^\w\s-]', '', title.lower()).replace(' ', '-') or 'section'
                    n = implicit_counts.get(slug, 0)
                    implicit_counts[slug] = n + 1
                    sid = slug + (f'-{n}' if n else '')
                headings.append(dict(id=sid, start=token.map[0], body=token.map[1],
                                     end=len(text.splitlines()), level=int(token.tag[1:])))
            elif token.type == 'inline':
                anchor = ANCHOR.fullmatch(token.content)
                if anchor and token.map:
                    markers.append(('anchor', anchor[1], *token.map))
                    continue
                children = token.children or []
                for child in children:
                    if child.type in ('link_open', 'image'):
                        self.links.append(child.attrGet('href' if child.type == 'link_open' else 'src'))
                    elif child.type == 'html_inline' and not (
                            re.fullmatch(r'<!--.*?-->', child.content, re.S) or
                            re.fullmatch(r'<br\s*/?>', child.content, re.I)):
                        self.errors.append('raw inline HTML requires conversion to Markdown')
                if i == 0 or tokens[i - 1].type != 'heading_open':
                    if token.map and _substance(_text(children)):
                        self.content.append((*token.map, _text(children)))
            elif token.type in ('fence', 'code_block'):
                blocks.append(tuple(token.map))
                if _substance(token.content):
                    self.content.append((*token.map, token.content))
            elif token.type == 'html_block':
                marker, anchor = MARKER.fullmatch(token.content), ANCHOR.fullmatch(token.content)
                if marker:
                    markers.append((marker[1], marker[2], *token.map))
                elif anchor:
                    markers.append(('anchor', anchor[1], *token.map))
                elif re.sub(r'<!--.*?-->', '', token.content, flags=re.S).strip():
                    self.errors.append(f'raw HTML block at line {token.map[0] + 1} requires conversion to Markdown')
        # A section marker is immediately followed by a heading; an anchor before a
        # heading is equivalent. Fragment markers attach to one following block.
        for kind, sid, start, end in markers:
            following = [h for h in headings if h['start'] >= end and
                         not ''.join(text.splitlines(True)[end:h['start']]).strip()]
            if kind == 'section' or (kind == 'anchor' and following):
                if not following:
                    self.errors.append(f'section marker {sid!r} has no adjacent heading')
                else:
                    h = following[0]
                    if h.get('marked'):
                        self.errors.append(f'multiple section markers before line {h["start"] + 1}')
                    h['id'], h['marked'] = sid, True
            else:
                candidates = [(a, b) for a, b in blocks if a >= end and
                              not ''.join(text.splitlines(True)[end:a]).strip()]
                if not candidates:
                    self.errors.append(f'fragment {sid!r} has no adjacent content block')
                else:
                    a = min(x[0] for x in candidates)
                    b = max(x[1] for x in candidates if x[0] == a)
                    self._add(self.fragments, sid, dict(start=a, body=a, end=b))
        for i, h in enumerate(headings):
            h['end'] = next((n['start'] for n in headings[i + 1:] if n['level'] <= h['level']), h['end'])
            self._add(self.sections, h['id'], h)
        for sid in self.sections.keys() & self.fragments.keys():
            self.duplicates.append(sid)
        self.errors.extend(f'Duplicate section/fragment ID {sid!r}' for sid in sorted(set(self.duplicates)))

    def _add(self, target, sid, value):
        if sid in target:
            self.duplicates.append(sid)
        else:
            target[sid] = value

    def nonempty(self, span):
        return any(a >= span['body'] and b <= span['end'] for a, b, _ in self.content)

    def target(self, sid):
        return self.sections.get(sid) or self.fragments.get(sid)


def parse_markdown_sections(draft):
    """Compatibility helper; duplicates are errors, code and nested bodies count."""
    document = MarkdownDocument(draft)
    if document.errors:
        raise CoverageError('; '.join(document.errors))
    return {sid: [text for a, b, text in document.content if a >= h['body'] and b <= h['end']]
            for sid, h in document.sections.items()}


def collect_required_facts(facts, groups):
    documented = set(facts['documented_object_ids'])
    all_facts = {f['id']: (group, f) for group in FACT_ARRAYS for f in facts[group]}
    relevant = set(documented)
    while True:
        previous = relevant.copy()
        for fid, (_, fact) in all_facts.items():
            if {fact.get('scope'), fact.get('object_id')} & documented or (
                    set(fact.get('operation_ids', [])) | set(fact.get('related_facts', []))) & relevant:
                relevant.add(fid)
            if fid in relevant:
                for field in ('operation_ids', 'condition_ids', 'column_ids', 'source_columns', 'related_facts'):
                    relevant.update(fact.get(field, []))
        if relevant == previous:
            break
    return {fid for fid in relevant if all_facts[fid][0] in groups}, all_facts


def _local_links(document, result, *, page_id, wiki_root, link_roots):
    root = Path(wiki_root).resolve() if wiki_root else None
    page = (root / page_id).resolve() if root else None
    if page and (not page.is_relative_to(root) or Path(page_id).is_absolute()):
        result.errors.append('page_id is outside wiki root')
        return
    allowed = ([root] if root else []) + [Path(p).resolve() for p in link_roots]
    cache = {}
    for href in document.links:
        try:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                # External URLs are not fetched by a static, local coverage check.
                if url.scheme and url.scheme.lower() not in ('http', 'https', 'mailto'):
                    raise CoverageError(f'unsupported link scheme: {href!r}')
                continue
            path, fragment = unquote(url.path), unquote(url.fragment)
            if '\\' in path or '\x00' in path or path.startswith('/'):
                raise CoverageError(f'local link must use a relative URL: {href!r}')
            if not path:
                if fragment and not document.target(fragment):
                    raise CoverageError(f'local fragment not found: {href!r}')
                continue
            if page is None:
                raise CoverageError(f'local link {href!r} requires --wiki-root (the final page location)')
            target = (page.parent / path).resolve()
            if not any(target.is_relative_to(r) for r in allowed):
                raise CoverageError(f'local link leaves declared roots: {href!r}')
            if target == page:
                if fragment and not document.target(fragment):
                    raise CoverageError(f'local fragment not found: {href!r}')
                continue
            if target.is_dir():
                if fragment:
                    raise CoverageError(f'directory link cannot contain a file anchor: {href!r}')
                result.link_snapshots[target] = None
                continue
            if target not in cache:
                data = target.read_bytes()
                result.link_snapshots[target] = hashlib.sha256(data).hexdigest()
                cache[target] = data
            if fragment:
                data = cache[target]
                if target.suffix.lower() in ('.md', '.markdown'):
                    linked = MarkdownDocument(data.decode('utf-8-sig'))
                    if linked.errors or not linked.target(fragment):
                        raise CoverageError(f'linked Markdown fragment missing or ambiguous: {href!r}')
                else:
                    lines = re.fullmatch(r'L([1-9]\d*)(?:-L([1-9]\d*))?', fragment)
                    if not lines or not 1 <= int(lines[1]) <= int(lines[2] or lines[1]) <= len(data.decode('utf-8-sig').splitlines()):
                        raise CoverageError(f'unsupported or invalid file fragment: {href!r}')
        except (CoverageError, OSError, UnicodeError, ValueError) as exc:
            result.errors.append(f'link: {exc}')


def validate_coverage(coverage, draft, facts, validation_plan=None, policy=None, *, wiki_root=None, link_roots=()):
    result = CoverageResult()
    if not isinstance(draft, str):
        result.errors, result.input_error = ['draft must be Markdown text'], True
        return result
    artifacts = dict(facts=facts, coverage=coverage)
    try:
        result.errors.extend(validate_artifacts(artifacts, required=set(artifacts)))
        if validation_plan is not None:
            plan_errors = validate_schema(validation_plan, load_schemas()['validation_plan'], 'validation_plan')
            result.errors.extend(plan_errors)
            if not plan_errors:
                result.errors.extend(check_run_consistency({**artifacts, 'validation_plan': validation_plan}))
                result.errors.extend(check_unique_ids(validation_plan, 'validation_plan', [('required_checks', 'id')]))
        if result.errors:
            result.input_error = True
            return result
        policy = load_policy() if policy is None else validate_policy(policy)
        document = MarkdownDocument(draft)
        result.errors.extend(document.errors)
        result.duplicate_sections.extend(document.duplicates)
        required, all_facts = collect_required_facts(facts, policy['coverage']['required_groups'])
        result.uncovered_facts = required.copy()
        for fid in sorted(required - coverage['entries'].keys()):
            result.errors.append(f'coverage: fact {fid} is not covered')
        for fid, refs in coverage['entries'].items():
            before = len(result.errors)
            for ref in refs:
                sid = ref['section_id']
                section = document.sections.get(sid)
                if section is None:
                    result.missing_sections.append(sid)
                    result.errors.append(f'coverage {fid}: section {sid!r} not found')
                    continue
                if not document.nonempty(section):
                    result.empty_sections.append(sid)
                    result.errors.append(f'coverage {fid}: section {sid!r} is empty')
                group = all_facts[fid][0]
                if sid not in policy['coverage']['allowed_sections'][group]:
                    result.errors.append(f'coverage {fid}: wrong section {sid!r} for {group}')
                target = section
                fragment = ref.get('fragment_ref')
                if fragment is not None:
                    target = document.target(fragment.removeprefix('#'))
                    if not target or not (target['start'] >= section['start'] and target['end'] <= section['end']) or not document.nonempty(target):
                        result.invalid_fragments.append(fragment)
                        result.errors.append(f'coverage {fid}: fragment_ref {fragment!r} missing, empty or outside section {sid!r}')
                        continue
                result.fragments.setdefault(fid, []).append(dict(section_id=sid, fragment_ref=fragment,
                                                                 start_line=target['body'] + 1, end_line=target['end']))
            if len(result.errors) == before and not document.errors and fid in required:
                result.covered_facts.add(fid)
                result.uncovered_facts.remove(fid)
            if fid not in required:
                result.warnings.append(f'coverage {fid}: optional context fact outside documented objects')
        if validation_plan is None:
            documented = set(facts['documented_object_ids'])
            objects = [o for o in facts['objects'] if o['id'] in documented]
            operations = [o for o in facts['operations'] if o['scope'] in documented]
            context = dict(object_kind=objects[0]['kind'], source_count=len({r for o in operations for r in o['reads']}))
            section_checks = derive_section_checks(policy, context)
            result.warnings.append('No independent plan supplied; section applicability is inferred from facts only')
        else:
            section_checks = validation_plan['required_checks']
        for check in section_checks:
            if check['source'] == 'section':
                sid = check['id'].removeprefix('section:')
                section = document.sections.get(sid)
                if not section or not document.nonempty(section):
                    (result.missing_sections if not section else result.empty_sections).append(sid)
                    result.errors.append(f'missing or empty required section {sid!r}')
        _local_links(document, result, page_id=coverage['page_id'], wiki_root=wiki_root, link_roots=link_roots)
    except (ArtifactInputError, PolicyError) as exc:
        result.errors.append(str(exc))
        result.input_error = True
    return result


def validate_coverage_bundle(run_dir, policy_path=None, *, wiki_root=None, link_roots=()):
    run = Path(run_dir)
    try:
        return validate_coverage(read_json(run / 'coverage.json'),
                                 (run / 'page.draft.md').read_text(encoding='utf-8-sig'),
                                 read_json(run / 'facts.json'), read_json(run / 'validation_plan.json'),
                                 load_policy(policy_path), wiki_root=wiki_root, link_roots=link_roots)
    except (ArtifactInputError, PolicyError, OSError, UnicodeError) as exc:
        result = CoverageResult()
        result.errors.append(str(exc))
        result.input_error = True
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('coverage', 'draft', 'facts'):
        parser.add_argument(name, nargs='?')
    for name in ('plan', 'policy', 'bundle', 'wiki-root'):
        parser.add_argument('--' + name)
    parser.add_argument('--link-root', action='append', default=[], help='Additional allowed root for source links')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.bundle:
        if args.coverage or args.draft or args.facts or args.plan:
            parser.error('--bundle cannot be combined with positional inputs or --plan')
        result = validate_coverage_bundle(args.bundle, args.policy, wiki_root=args.wiki_root, link_roots=args.link_root)
    else:
        if not all((args.coverage, args.draft, args.facts)):
            parser.error('Provide coverage, draft and facts, or --bundle')
        try:
            result = validate_coverage(read_json(args.coverage), Path(args.draft).read_text(encoding='utf-8-sig'),
                                       read_json(args.facts), read_json(args.plan) if args.plan else None,
                                       load_policy(args.policy), wiki_root=args.wiki_root, link_roots=args.link_root)
        except (ArtifactInputError, PolicyError, OSError, UnicodeError) as exc:
            result = CoverageResult()
            result.errors, result.input_error = [str(exc)], True
    # ASCII JSON is valid UTF-8 and safe on Windows legacy console encodings.
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))
    else:
        print('Coverage validation: ' + ('PASS' if result.valid else 'FAIL'))
        for error in result.errors:
            print(error.encode('ascii', 'backslashreplace').decode('ascii'))
    return 2 if result.input_error else (0 if result.valid else 1)


if __name__ == '__main__':
    raise SystemExit(main())
