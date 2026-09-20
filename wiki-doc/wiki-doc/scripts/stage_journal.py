"""Append-only stage journal for wiki-doc pipeline runs.

Records explicitly submitted stages with timestamps, run_id, page_id and
outcome. CLI decision issuance, publication and recovery are instrumented.
This diagnostic journal is separate from the transaction recovery journal.

Usage:
  from stage_journal import StageJournal
  journal = StageJournal(wiki_root)
  journal.record('inventory', run_id='...', page_id='...', outcome='ok')
  journal.record('publish', run_id='...', page_id='...', outcome='ready',
                 details={'page': 'my_page', 'index_updated': True})
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from artifact_schema import read_json
from wiki_store import inside


JOURNAL_DIR = '.wiki-doc'
JOURNAL_FILE = 'journal.jsonl'


class StageJournal:
    """Append-only JSONL journal for pipeline stages."""

    VALID_STAGES = frozenset({
        'inventory', 'plan', 'draft', 'validation', 'decision',
        'publish', 'lint', 'query', 'regression', 'error',
    })

    def __init__(self, wiki_root: Path | str):
        self._root = Path(wiki_root).resolve()
        self._dir = inside(self._root, JOURNAL_DIR)
        self._path = inside(self._root, JOURNAL_DIR + '/' + JOURNAL_FILE)

    def record(self, stage: str, *, run_id: str | None = None,
               page_id: str | None = None, outcome: str = 'ok',
               details: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append a journal entry and return it."""
        if stage not in self.VALID_STAGES:
            raise ValueError(f'Invalid stage {stage!r}; expected one of {sorted(self.VALID_STAGES)}')
        entry = {
            'schema_version': 1,
            'event_id': str(uuid.uuid4()),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'stage': stage,
            'run_id': run_id,
            'page_id': page_id,
            'outcome': outcome,
        }
        if details:
            entry['details'] = details
        payload = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
        # The same interprocess lock serializes writers. Import lazily to keep
        # logging optional for the publisher and gate.
        from publish import index_lock
        with index_lock(self._root):
            path = inside(self._root, JOURNAL_DIR + '/' + JOURNAL_FILE)
            with path.open('a+b') as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell():
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b'\n':
                        stream.write(b'\n')  # Preserve, but isolate a crash-truncated record.
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        return entry

    def read(self) -> list[dict[str, Any]]:
        """Read all journal entries (for metrics/reporting)."""
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_bytes().splitlines():
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line.decode('utf-8'))
                    if isinstance(entry, dict):
                        entries.append(entry)
                except (json.JSONDecodeError, UnicodeError):
                    continue
        return entries

    def clear(self) -> None:
        """Remove the journal file (for testing)."""
        if self._path.exists():
            self._path.unlink()


def record_stage(wiki_root, stage, *, run_dir=None, **fields):
    """Best-effort diagnostics; missing metadata never invents a pipeline run ID."""
    try:
        if run_dir is not None:
            try:
                manifest = read_json(Path(run_dir) / 'manifest.json')
                for key in ('run_id', 'page_id'):
                    if key not in fields and isinstance(manifest.get(key), str):
                        fields[key] = manifest[key]
            except (OSError, ValueError, AttributeError):
                pass
        StageJournal(wiki_root).record(stage, **fields)
    except Exception:
        pass  # Diagnostics cannot alter admission, publication or recovery results.
