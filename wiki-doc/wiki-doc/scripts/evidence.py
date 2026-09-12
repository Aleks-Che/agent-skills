"""Verifiable evidence: validate source references, hashes, and file bounds.

Evidence format (shared by source_refs and evidence entries):
    - root: one of 'project', 'run', 'wiki', 'package'
    - path: relative path from root
    - start_line: 1-based start line
    - end_line: 1-based end line (>= start_line)
    - sha256: hex-encoded SHA-256 of raw file bytes

Roots:
    project  — SQL project directory (examples, migrations, context)
    run      — current run directory (inventory, plan, facts, etc.)
    wiki     — wiki documentation root (pages, index)
    package  — wiki-doc skill package (scripts, schemas, references, templates)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path, PureWindowsPath
import re
from typing import Optional


@dataclass(frozen=True)
class EvidenceRoot:
    """Resolved root directory."""
    name: str
    path: Path

    def contains(self, resolved: Path) -> bool:
        try:
            resolved.relative_to(self.path)
            return True
        except ValueError:
            return False


@dataclass(frozen=True)
class EvidenceRef:
    """A validated reference to a span of bytes in a file."""
    root: str
    path: str
    start_line: int
    end_line: int
    sha256: str


# ---- Root management ----

_ROOTS: dict[str, EvidenceRoot] = {}


def register_root(name: str, path: Path) -> None:
    """Register a named root directory."""
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f'Root {name!r} is not a directory: {resolved}')
    _ROOTS[name] = EvidenceRoot(name=name, path=resolved)


def get_root(name: str) -> EvidenceRoot:
    """Get a registered root by name."""
    if name not in _ROOTS:
        raise ValueError(f'Unknown root {name!r}; registered: {list(_ROOTS)}')
    return _ROOTS[name]


def clear_roots() -> None:
    """Clear all registered roots (for testing)."""
    _ROOTS.clear()


# ---- Hashing ----

def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 of file bytes (not normalized)."""
    return sha256_bytes(path.read_bytes())


# ---- Line extraction ----

def _line_offsets(data: bytes) -> list[int]:
    """Return byte offsets of each line start (0-indexed lines)."""
    offsets = [0]
    for i, b in enumerate(data):
        if b == 0x0A:  # LF
            offsets.append(i + 1)
    return offsets


def _line_count(data: bytes) -> int:
    """Count lines in raw bytes (LF-delimited)."""
    if not data:
        return 0
    return data.count(b'\n') + (1 if not data.endswith(b'\n') else 0)


def extract_lines_text(data: bytes, start: int, end: int) -> str:
    """Extract lines [start, end] (1-indexed) as UTF-8 text from raw bytes.

    Handles LF and CRLF uniformly by splitting on LF.
    """
    text = data.decode('utf-8-sig')
    lines = text.split('\n')
    if lines[-1] == '':
        lines.pop()
    if start < 1 or end < start or end > len(lines):
        raise ValueError(f'Line range [{start}, {end}] out of bounds (total {len(lines)} lines)')
    return '\n'.join(line.removesuffix('\r') for line in lines[start - 1:end])


# ---- Validation ----

class EvidenceError(ValueError):
    """Evidence validation failure."""


def resolve_reference(ref: dict, roots: dict[str, Path], default_root='project') -> Path:
    """Resolve a relative reference, including symlinks, within an explicit root."""
    if not isinstance(ref, dict):
        raise EvidenceError('Reference must be an object')
    name, path = ref.get('root', default_root), ref.get('path')
    if not isinstance(name, str) or name not in roots:
        raise EvidenceError(f'Unknown root {name!r}')
    if (not isinstance(path, str) or not path.strip() or Path(path).is_absolute()
            or PureWindowsPath(path).drive or PureWindowsPath(path).root):
        raise EvidenceError('path must be relative to its root')
    try:
        root = Path(roots[name]).resolve(strict=True)
        resolved = (root / path.replace('\\', '/')).resolve()
        if not resolved.is_relative_to(root):
            raise EvidenceError(f'Path {path!r} escapes root {name!r}')
        if not resolved.is_file():
            raise EvidenceError(f'File not found: {resolved}')
        return resolved
    except (OSError, RuntimeError) as exc:
        raise EvidenceError(f'Cannot resolve {path!r}: {exc}') from exc


def validate_evidence(
    ref: dict,
    roots: dict[str, Path],
    *,
    check_lines: bool = True,
    check_hash: bool = True,
) -> EvidenceRef:
    """Validate an evidence/source_ref dict against registered roots.

    Args:
        ref: dict with root, path, start_line, end_line, sha256
        roots: mapping root name -> directory path (overrides registered roots)
        check_lines: verify line range against file
        check_hash: verify sha256 against file bytes

    Returns:
        EvidenceRef if valid

    Raises:
        EvidenceError on any validation failure
    """
    # Merge roots
    all_roots = {**{n: r.path for n, r in _ROOTS.items()}, **roots}

    if not isinstance(ref, dict):
        raise EvidenceError('Evidence must be an object')
    # Required fields
    for field in ('root', 'path', 'start_line', 'end_line', 'sha256'):
        if field not in ref:
            raise EvidenceError(f'Missing required field {field!r}')

    root_name = ref['root']
    rel_path = ref['path']
    start_line = ref['start_line']
    end_line = ref['end_line']
    sha256 = ref['sha256']

    resolved = resolve_reference(ref, all_roots)

    # Line range
    if type(start_line) is not int or type(end_line) is not int:
        raise EvidenceError('start_line and end_line must be integers')
    if start_line < 1:
        raise EvidenceError(f'start_line must be >= 1, got {start_line}')
    if end_line < start_line:
        raise EvidenceError(f'end_line ({end_line}) < start_line ({start_line})')

    # Hash
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise EvidenceError(f'sha256 must be 64-char hex string, got {sha256!r}')

    if check_hash or check_lines:
        try:
            data = resolved.read_bytes()
            data.decode('utf-8-sig')
        except (OSError, UnicodeError) as exc:
            raise EvidenceError(f'Cannot read UTF-8 evidence {rel_path!r}: {exc}') from exc
        actual_sha = sha256_bytes(data)

        if check_hash and sha256 != actual_sha:
            raise EvidenceError(
                f'sha256 mismatch for {rel_path}: expected {sha256}, got {actual_sha}')

        if check_lines:
            total = _line_count(data)
            if end_line > total:
                raise EvidenceError(
                    f'end_line ({end_line}) exceeds file length ({total} lines)')

    return EvidenceRef(
        root=root_name,
        path=rel_path,
        start_line=start_line,
        end_line=end_line,
        sha256=sha256,
    )


def validate_source_ref(ref: dict, roots: dict[str, Path]) -> EvidenceRef:
    """Validate a source_ref (same structure as evidence)."""
    return validate_evidence(ref, roots)


def validate_source_refs(refs: list[dict], roots: dict[str, Path]) -> list[EvidenceRef]:
    """Validate a list of source_refs."""
    return [validate_source_ref(r, roots) for r in refs]


# ---- Bundle-level evidence checks ----

def check_evidence_against_inputs(
    evidence: list[dict],
    inputs: list[dict],
    roots: dict[str, Path],
) -> list[str]:
    """Check that evidence sha256 values match declared inputs.

    inputs: list of {path, sha256} from facts.json or manifest
    Returns list of error strings.
    """
    input_map = {inp['path']: inp['sha256'] for inp in inputs}
    errors = []
    for i, ev in enumerate(evidence):
        path = ev.get('path', '')
        sha = ev.get('sha256', '')
        if path in input_map and sha != input_map[path]:
            errors.append(f'evidence.{i}: sha256 mismatch with declared input {path!r}')
    return errors


def check_evidence_existence(
    evidence: list[dict],
    roots: dict[str, Path],
) -> list[str]:
    """Quick check: does each evidence file exist and stay within root?

    Returns list of error strings.
    """
    errors = []
    for i, ev in enumerate(evidence):
        try:
            validate_evidence(ev, roots, check_lines=False, check_hash=False)
        except EvidenceError as exc:
            errors.append(f'evidence.{i}: {exc}')
    return errors


# ---- CLI ----

def _cli_validate(args):
    """CLI: validate evidence refs from JSON."""
    import json
    data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    roots = {name: Path(p) for name, p in args.root} if args.root else {}
    errors = []
    refs = data if isinstance(data, list) else data.get('evidence', [data])
    for i, ref in enumerate(refs):
        try:
            validate_evidence(ref, roots, check_lines=not args.no_lines, check_hash=not args.no_hash)
        except EvidenceError as exc:
            errors.append(f'{i}: {exc}')
    if errors:
        for e in errors:
            print(f'ERROR: {e}')
        return 1
    print(f'All {len(refs)} evidence refs valid.')
    return 0


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')

    val = sub.add_parser('validate', help='Validate evidence refs from JSON')
    val.add_argument('input', help='JSON file with evidence array or {evidence: [...]}')
    val.add_argument('--root', nargs=2, action='append', metavar=('NAME', 'PATH'),
                     help='Root directory (repeatable)')
    val.add_argument('--no-lines', action='store_true', help='Skip line range check')
    val.add_argument('--no-hash', action='store_true', help='Skip hash check')

    args = parser.parse_args(argv)
    if args.command == 'validate':
        return _cli_validate(args)
    parser.print_help()
    return 2


if __name__ == '__main__':
    import sys
    sys.exit(main())
